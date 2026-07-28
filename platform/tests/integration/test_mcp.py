import asyncio
import json
import socket
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone

import httpx
import uvicorn
from cryptography.fernet import Fernet
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pydantic import AnyUrl
from sqlalchemy import func, select

from tradingng_platform.api.app import create_app
from tradingng_platform.api.auth import current_principal
from tradingng_platform.auth.principal import Principal
from tradingng_platform.config import Settings
from tradingng_platform.db import Database
from tradingng_platform.gateway.client import GatewaySnapshot
from tradingng_platform.integrity.contracts import IntegrityStatus
from tradingng_platform.integrity.policy import PointInTimeRecorder
from tradingng_platform.integrity.repository import IntegrityRepository
from tradingng_platform.mcp.inspection import inspect_inventory
from tradingng_platform.models import AssessmentRun
from tradingng_platform.scheduler.policy import SystemSnapshot
from tradingng_platform.scheduler.repository import (
    ACTIVE_RUN_STATUSES,
    ExecutionMetadata,
    SchedulerPolicyRepository,
    SchedulerRepository,
)
from tradingng_platform.scheduler.service import AdmissionService

MCP_PRINCIPAL = Principal(
    issuer="test-issuer",
    subject="mcp-analyst",
    actor_type="service",
    scopes=frozenset(
        {
            "assessments:read",
            "assessments:submit",
            "assessments:cancel",
            "assessments:admin",
            "artifacts:read",
            "system:read",
            "validations:read",
        }
    ),
    display_name="MCP Analyst",
    roles=frozenset({"Admin"}),
)


class _OidcVerifier:
    async def verify(self, token: str) -> Principal:
        if token != "valid-mcp-token":
            raise ValueError("invalid token or audience")
        return MCP_PRINCIPAL


class _Gateway:
    async def status(self):
        return GatewaySnapshot(
            status="ok",
            active_completions=0,
            model="gpt-5.6-sol",
            reasoning_effort="xhigh",
            snapshot_id="f" * 64,
            latency_ms=1,
        )


class _Probe:
    def sample(self):
        return SystemSnapshot(20, 32, 100, 50, False)


@asynccontextmanager
async def _serve(app):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(app, log_level="warning", access_log=False, lifespan="on")
    )
    task = asyncio.create_task(server.serve(sockets=[listener]))
    for _ in range(200):
        if server.started:
            break
        if task.done():
            await task
        await asyncio.sleep(0.01)
    if not server.started:
        raise RuntimeError("test MCP server did not start")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=10)
        listener.close()


def _tool_payload(result) -> dict:
    assert result.isError is False
    assert result.structuredContent is not None
    return result.structuredContent


async def test_mcp_protocol_rest_parity_and_concurrency_gate(
    test_database_url,
    session_factory,
    instrument_classifier,
    tmp_path,
):
    settings = Settings(
        database_url=test_database_url,
        data_dir=tmp_path / "runtime",
        token_pepper="integration-token-pepper-value",
        webhook_encryption_key=Fernet.generate_key().decode(),
    )
    database = Database(settings)
    app = create_app(
        settings=settings,
        database=database,
        mcp_oidc=_OidcVerifier(),
        instrument_classifier=instrument_classifier,
    )
    app.dependency_overrides[current_principal] = lambda: MCP_PRINCIPAL
    try:
        async with _serve(app) as origin:
            app.state.system.gateway = _Gateway()
            app.state.system.system_probe = _Probe()
            mcp_url = f"{origin}/mcp"
            async with httpx.AsyncClient(  # noqa: SIM117
                headers={"Authorization": "Bearer valid-mcp-token"},
                timeout=10,
            ) as authenticated_http:
                async with streamable_http_client(
                    mcp_url,
                    http_client=authenticated_http,
                ) as streams:
                    read_stream, write_stream, _ = streams
                    async with ClientSession(read_stream, write_stream) as session:
                        initialized = await session.initialize()
                        assert initialized.protocolVersion == "2025-11-25"
                        tools = await session.list_tools()
                        assert {tool.name for tool in tools.tools} == {
                            "submit_assessment",
                            "submit_assessment_batch",
                            "get_assessment_status",
                            "list_assessments",
                            "cancel_assessment",
                            "retry_assessment",
                            "delete_assessment",
                            "compare_assessments",
                            "get_instrument_summary",
                            "list_instrument_overviews",
                            "get_system_capacity",
                            "schedule_validation",
                            "retry_validation",
                            "clean_reassess_assessment",
                        }
                        templates = await session.list_resource_templates()
                        assert len(templates.resourceTemplates) == 6
                        resources = await session.list_resources()
                        assert [str(item.uri) for item in resources.resources] == [
                            "tradingng://system/capacity"
                        ]
                        prompts = await session.list_prompts()
                        assert len(prompts.prompts) == 4

                        submission = {
                            "ticker": "NVDA",
                            "analysis_date": str(date(2026, 7, 25)),
                            "depth": "deep",
                            "analysts": ["market", "social", "news", "fundamentals"],
                            "language": "Chinese",
                            "asset_type": "stock",
                            "idempotency_key": "mcp-integration-nvda-20260725",  # gitleaks:allow
                        }
                        accepted = _tool_payload(
                            await session.call_tool("submit_assessment", submission)
                        )
                        duplicate = _tool_payload(
                            await session.call_tool("submit_assessment", submission)
                        )
                        run_id = accepted["run_id"]
                        assert duplicate["run_id"] == run_id
                        status = _tool_payload(
                            await session.call_tool("get_assessment_status", {"run_id": run_id})
                        )
                        summary = await session.read_resource(
                            AnyUrl(f"tradingng://assessments/{run_id}/summary")
                        )
                        evidence = await session.read_resource(
                            AnyUrl(f"tradingng://assessments/{run_id}/evidence")
                        )
                        assert json.loads(summary.contents[0].text)["id"] == run_id
                        assert json.loads(evidence.contents[0].text) == []

                        cancelled = _tool_payload(
                            await session.call_tool("cancel_assessment", {"run_id": run_id})
                        )
                        assert cancelled["status"] == "cancelled"
                        retried = _tool_payload(
                            await session.call_tool("retry_assessment", {"run_id": run_id})
                        )
                        retry_id = retried["run_id"]
                        async with session_factory() as db_session, db_session.begin():
                            retry_run = await db_session.get(
                                AssessmentRun,
                                uuid.UUID(retry_id),
                            )
                            retry_run.status = "succeeded"
                            recorder = PointInTimeRecorder(
                                date(2026, 7, 25),
                                now=datetime(2026, 7, 27, tzinfo=timezone.utc),
                            )
                            recorder.record(
                                "get_fundamentals",
                                IntegrityStatus.AT_RISK,
                                "current_snapshot_exposed",
                            )
                            await IntegrityRepository(db_session).persist_document(
                                retry_run.id,
                                recorder.finalize(),
                                artifact_id=None,
                                audit_mode="retrospective",
                            )
                        integrity = await session.read_resource(
                            AnyUrl(f"tradingng://assessments/{retry_id}/integrity")
                        )
                        assert json.loads(integrity.contents[0].text)["status"] == "at_risk"
                        cleaned = _tool_payload(
                            await session.call_tool(
                                "clean_reassess_assessment",
                                {"run_id": retry_id},
                            )
                        )
                        assert cleaned["status"] == "queued"
                        compared = _tool_payload(
                            await session.call_tool(
                                "compare_assessments",
                                {"run_ids": [run_id, retry_id]},
                            )
                        )
                        assert {item["id"] for item in compared["runs"]} == {
                            run_id,
                            retry_id,
                        }
                        capacity = _tool_payload(await session.call_tool("get_system_capacity"))
                        assert capacity["gateway_model"] == "gpt-5.6-sol"
                        overviews = _tool_payload(
                            await session.call_tool(
                                "list_instrument_overviews",
                                {"query": "NVDA", "limit": 25},
                            )
                        )
                        assert overviews["items"][0]["instrument"]["ticker"] == "NVDA"

                        deletable = _tool_payload(
                            await session.call_tool(
                                "submit_assessment",
                                {
                                    **submission,
                                    "ticker": "TSLA",
                                    "idempotency_key": "mcp-delete-tsla-20260725",
                                },
                            )
                        )
                        deletable_run_id = deletable["run_id"]
                        await session.call_tool(
                            "cancel_assessment",
                            {"run_id": deletable_run_id},
                        )
                        deleted = _tool_payload(
                            await session.call_tool(
                                "delete_assessment",
                                {"run_id": deletable_run_id},
                            )
                        )
                        assert deleted == {
                            "run_id": deletable_run_id,
                            "deleted": True,
                            "message": "Assessment was permanently deleted",
                        }

                        concurrent_results = await asyncio.gather(
                            *(
                                session.call_tool(
                                    "submit_assessment",
                                    {
                                        **submission,
                                        "ticker": f"TNG{index:02}",
                                        "idempotency_key": f"mcp-concurrent-{index:02}-20260725",
                                    },
                                )
                                for index in range(20)
                            )
                        )
                        concurrent_errors = [
                            [content.text for content in result.content if hasattr(content, "text")]
                            for result in concurrent_results
                            if result.isError
                        ]
                        assert not concurrent_errors, concurrent_errors

            inventory = await inspect_inventory(mcp_url, "valid-mcp-token")
            assert inventory["tools"] == sorted(tool.name for tool in tools.tools)
            assert inventory["resources"] == ["tradingng://system/capacity"]
            assert len(inventory["resource_templates"]) == 6
            assert len(inventory["prompts"]) == 4

            async with httpx.AsyncClient(base_url=origin, timeout=10) as rest:
                rest_run = await rest.get(f"/api/v1/assessments/{run_id}")
                assert rest_run.status_code == 200
                assert rest_run.json()["id"] == status["id"]
                assert rest_run.json()["config_snapshot_sha256"] == status["config_snapshot_sha256"]
                deleted_run = await rest.get(f"/api/v1/assessments/{deletable_run_id}")
                assert deleted_run.status_code == 404

                missing = await rest.post("/mcp", json={})
                invalid = await rest.post(
                    "/mcp",
                    headers={"Authorization": "Bearer wrong-audience"},
                    json={},
                )
                forbidden_origin = await rest.post(
                    "/mcp",
                    headers={
                        "Authorization": "Bearer valid-mcp-token",
                        "Origin": "https://evil.example",
                    },
                    json={},
                )
                wrong_content_type = await rest.post(
                    "/mcp",
                    headers={
                        "Authorization": "Bearer valid-mcp-token",
                        "Content-Type": "text/plain",
                    },
                    content="{}",
                )
                oversized = await rest.post(
                    "/mcp",
                    headers={
                        "Authorization": "Bearer valid-mcp-token",
                        "Content-Type": "application/json",
                    },
                    content=b"x" * (1024 * 1024 + 1),
                )
                unsupported_protocol = await rest.post(
                    "/mcp",
                    headers={
                        "Authorization": "Bearer valid-mcp-token",
                        "Content-Type": "application/json",
                        "Accept": "application/json, text/event-stream",
                        "MCP-Protocol-Version": "1900-01-01",
                    },
                    json={"jsonrpc": "2.0", "id": 7, "method": "tools/list"},
                )

            assert missing.status_code == 401
            assert "resource_metadata=" in missing.headers["WWW-Authenticate"]
            assert invalid.status_code == 401
            assert forbidden_origin.status_code == 403
            assert wrong_content_type.status_code == 415
            assert oversized.status_code == 413
            assert unsupported_protocol.status_code >= 400

        metadata = ExecutionMetadata("root", "tradingagents", "v1", {}, {})
        for _ in range(20):
            async with session_factory() as db_session, db_session.begin():
                decision = await AdmissionService(
                    SchedulerRepository(db_session),
                    SchedulerPolicyRepository(db_session),
                    _Gateway(),
                    _Probe(),
                    metadata,
                ).admit_one()
            if not decision.allowed:
                break
        async with session_factory() as db_session:
            active = int(
                await db_session.scalar(
                    select(func.count())
                    .select_from(AssessmentRun)
                    .where(AssessmentRun.status.in_(ACTIVE_RUN_STATUSES))
                )
                or 0
            )
        assert active == 2
    finally:
        await database.close()

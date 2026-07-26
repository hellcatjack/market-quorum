import json
import uuid
from datetime import date, datetime, timezone

import pytest

from tradingng_platform.assessments.contracts import RunDetailView
from tradingng_platform.auth.principal import Principal
from tradingng_platform.domain.runs import RunStatus
from tradingng_platform.mcp.context import reset_principal, set_principal
from tradingng_platform.mcp.server import create_mcp_server
from tradingng_platform.mcp.services import McpServices
from tradingng_platform.records.contracts import ArtifactView, EvidenceView

RUN_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
ARTIFACT_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")
NOW = datetime(2026, 7, 25, tzinfo=timezone.utc)


class _Assessments:
    async def get(self, principal, run_id):
        principal.require("assessments:read")
        return RunDetailView(
            id=run_id,
            request_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
            ticker="NVDA",
            asset_type="stock",
            analysis_date=date(2026, 7, 25),
            status=RunStatus.SUCCEEDED,
            attempt=1,
            created_at=NOW,
            gateway_model="gpt-5.6-sol",
            gateway_reasoning_effort="xhigh",
        )


class _Records:
    async def evidence(self, principal, run_id):
        principal.require("assessments:read")
        return [
            EvidenceView(
                id=uuid.UUID(int=4),
                source="yfinance",
                tool_name="get_stock_data",
                arguments={"ticker": "NVDA"},
                collected_at=NOW,
                effective_at=None,
                freshness="current",
                content_hash="a" * 64,
            )
        ]

    async def list_artifacts(self, principal, run_id):
        principal.require("artifacts:read")
        return [
            ArtifactView(
                id=ARTIFACT_ID,
                run_id=run_id,
                kind="report_1_complete",
                media_type="text/markdown",
                size=21,
                sha256="b" * 64,
                created_at=NOW,
            )
        ]

    async def read_report(self, principal, artifact_id):
        principal.require("artifacts:read")
        assert artifact_id == ARTIFACT_ID
        return "# Complete\nStored report"

    async def instrument_history(self, principal, ticker, limit):
        principal.require("assessments:read")
        return []


class _System:
    async def capacity(self, principal):
        principal.require("system:read")
        return _Dump(admission_allowed=True, queued=2)


class _Dump:
    def __init__(self, **values):
        self.values = values

    def model_dump(self, mode="python"):
        del mode
        return self.values


def _principal(*scopes):
    return Principal("issuer", "viewer", "user", frozenset(scopes))


async def _read(server, principal, uri):
    token = set_principal(principal)
    try:
        contents = await server.read_resource(uri)
        return list(contents)[0].content
    finally:
        reset_principal(token)


@pytest.mark.asyncio
async def test_resource_templates_are_authorized_deterministic_and_redacted():
    server = create_mcp_server(McpServices(_Assessments(), _Records(), _System()))
    templates = {str(item.uri_template) for item in server._resource_manager.list_templates()}
    assert templates == {
        "tradingng://assessments/{run_id}/summary",
        "tradingng://assessments/{run_id}/report/{section}",
        "tradingng://assessments/{run_id}/evidence",
        "tradingng://assessments/{run_id}/validations",
        "tradingng://instruments/{ticker}/history",
    }
    resources = {str(item.uri) for item in server._resource_manager.list_resources()}
    assert resources == {"tradingng://system/capacity"}

    summary = await _read(
        server,
        _principal("assessments:read"),
        f"tradingng://assessments/{RUN_ID}/summary",
    )
    evidence = await _read(
        server,
        _principal("assessments:read"),
        f"tradingng://assessments/{RUN_ID}/evidence",
    )

    assert json.loads(summary)["ticker"] == "NVDA"
    assert summary == json.dumps(json.loads(summary), sort_keys=True, separators=(",", ":"))
    assert "storage_key" not in evidence
    assert "/tmp/" not in evidence


@pytest.mark.asyncio
async def test_resource_scope_denial_and_report_section_boundary():
    server = create_mcp_server(McpServices(_Assessments(), _Records(), _System()))

    with pytest.raises(ValueError, match="PermissionDenied"):
        await _read(
            server,
            _principal("system:read"),
            f"tradingng://assessments/{RUN_ID}/summary",
        )

    report = await _read(
        server,
        _principal("artifacts:read"),
        f"tradingng://assessments/{RUN_ID}/report/complete",
    )
    assert report.startswith("# Complete")

    with pytest.raises(ValueError, match="InvalidParams"):
        await _read(
            server,
            _principal("artifacts:read"),
            f"tradingng://assessments/{RUN_ID}/report/private",
        )

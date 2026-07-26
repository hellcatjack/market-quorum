from __future__ import annotations

import asyncio
import os
import subprocess
import time
from datetime import datetime, timezone

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

pytestmark = pytest.mark.real
TERMINAL = {"succeeded", "failed", "cancelled", "needs_attention"}


def _is_playwright_mcp_process(command: str) -> bool:
    lowered = command.lower()
    return "playwright-mcp" in lowered or "@playwright/mcp" in lowered


async def _service_token() -> str:
    secret = os.environ.get("TRADINGNG_MCP_CLIENT_SECRET")
    if not secret:
        raise RuntimeError("TRADINGNG_MCP_CLIENT_SECRET is required")
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            os.environ.get(
                "TRADINGNG_REAL_TOKEN_URL",
                "http://127.0.0.1:18081/realms/tradingng/protocol/openid-connect/token",
            ),
            data={
                "grant_type": "client_credentials",
                "client_id": "tradingng-mcp",
                "client_secret": secret,
                "scope": (
                    "assessments:read assessments:submit assessments:cancel "
                    "validations:read validations:write system:read artifacts:read"
                ),
            },
        )
        response.raise_for_status()
        return response.json()["access_token"]


def _gateway_playwright_descendants() -> list[str]:
    rows = subprocess.run(
        ["ps", "-eo", "pid=,ppid=,args="],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.splitlines()
    processes = {}
    for row in rows:
        fields = row.strip().split(maxsplit=2)
        if len(fields) == 3:
            processes[int(fields[0])] = (int(fields[1]), fields[2])
    gateway_roots = {
        pid
        for pid, (_, command) in processes.items()
        if "python -m codex_gateway" in command
    }
    descendants = set(gateway_roots)
    changed = True
    while changed:
        changed = False
        for pid, (parent, _) in processes.items():
            if parent in descendants and pid not in descendants:
                descendants.add(pid)
                changed = True
    return [
        command
        for pid, (_, command) in processes.items()
        if pid in descendants and _is_playwright_mcp_process(command)
    ]


def test_playwright_process_detection_ignores_the_gateway_disable_flag():
    assert not _is_playwright_mcp_process(
        "codex app-server --config mcp_servers.playwright.enabled=false"
    )
    assert _is_playwright_mcp_process("npm exec @playwright/mcp@latest")
    assert _is_playwright_mcp_process("node /opt/node_modules/.bin/playwright-mcp")


@pytest.mark.skipif(
    os.environ.get("TRADINGNG_RUN_REAL_DEEP") != "1",
    reason="explicit real Codex acceptance is disabled",
)
async def test_two_real_deep_runs_preserve_gateway_and_archive_contract():
    token = await _service_token()
    base_url = os.environ.get(
        "TRADINGNG_REAL_BASE_URL", "https://ushome.amycat.com"
    ).rstrip("/")
    verify_tls = os.environ.get("TRADINGNG_TLS_VERIFY", "1") == "1"
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=10) as gateway_client:
        before = (
            (await gateway_client.get("http://127.0.0.1:8000/internal/status"))
            .raise_for_status()
            .json()
        )
    async with httpx.AsyncClient(
        base_url=base_url,
        headers=headers,
        verify=verify_tls,
        timeout=60,
    ) as client:
        tickers = [
            item.strip().upper()
            for item in os.environ.get("TRADINGNG_REAL_TICKERS", "NVDA,TSLA").split(",")
            if item.strip()
        ]
        assert len(tickers) == 2 and len(set(tickers)) == 2
        configured_run_ids = [
            item.strip()
            for item in os.environ.get("TRADINGNG_REAL_RUN_IDS", "").split(",")
            if item.strip()
        ]
        if configured_run_ids:
            assert len(configured_run_ids) == 2 and len(set(configured_run_ids)) == 2
            run_ids = configured_run_ids
        else:
            submission = await client.post(
                "/api/v1/assessment-batches",
                json={
                    "items": [
                        {
                            "ticker": ticker,
                            "asset_type": "stock",
                            "analysis_date": datetime.now(timezone.utc)
                            .date()
                            .isoformat(),
                        }
                        for ticker in tickers
                    ],
                    "analysts": ["market", "social", "news", "fundamentals"],
                    "depth": "deep",
                    "language": "Chinese",
                    "idempotency_key": f"real-deep-{int(time.time())}",
                },
            )
            submission.raise_for_status()
            run_ids = [item["id"] for item in submission.json()["items"]]

        loop = asyncio.get_running_loop()
        deadline = loop.time() + 90 * 60
        refresh_at = loop.time() + 4 * 60
        details = {}
        while loop.time() < deadline:
            if loop.time() >= refresh_at:
                token = await _service_token()
                headers["Authorization"] = f"Bearer {token}"
                client.headers["Authorization"] = headers["Authorization"]
                refresh_at = loop.time() + 4 * 60
            details = {}
            for run_id in run_ids:
                response = await client.get(f"/api/v1/assessments/{run_id}")
                response.raise_for_status()
                details[run_id] = response.json()
            if all(item["status"] in TERMINAL for item in details.values()):
                break
            await asyncio.sleep(5)
        assert {item["status"] for item in details.values()} == {"succeeded"}

        for run_id in run_ids:
            detail = details[run_id]
            assert detail["gateway_model"]
            assert detail["gateway_reasoning_effort"]
            assert detail["config_snapshot_sha256"]
            events = (await client.get(f"/api/v1/assessments/{run_id}/events")).json()[
                "items"
            ]
            assert [item["sequence"] for item in events] == list(
                range(1, len(events) + 1)
            )
            assert (
                await client.get(f"/api/v1/assessments/{run_id}/decision")
            ).is_success
            evidence = await client.get(f"/api/v1/assessments/{run_id}/evidence")
            artifacts = await client.get(f"/api/v1/assessments/{run_id}/artifacts")
            validations = await client.get(f"/api/v1/assessments/{run_id}/validations")
            assert evidence.is_success and evidence.json()
            assert artifacts.is_success and artifacts.json()
            assert validations.is_success
            assert sorted(item["horizon"] for item in validations.json()) == [1, 5, 20]

    async with httpx.AsyncClient(timeout=10) as gateway_client:
        after = (
            (await gateway_client.get("http://127.0.0.1:8000/internal/status"))
            .raise_for_status()
            .json()
        )
    assert before["model"] == after["model"]
    assert before["reasoning_effort"] == after["reasoning_effort"]

    async with (
        httpx.AsyncClient(
            headers=headers,
            verify=verify_tls,
            timeout=60,
        ) as mcp_http,
        streamable_http_client(f"{base_url}/mcp", http_client=mcp_http) as streams,
    ):
        read_stream, write_stream, _ = streams
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            mcp_status = await session.call_tool(
                "get_assessment_status", {"run_id": run_ids[0]}
            )
            assert mcp_status.structuredContent["status"] == "succeeded"
            assert (
                mcp_status.structuredContent["config_snapshot_sha256"]
                == details[run_ids[0]]["config_snapshot_sha256"]
            )

    assert await asyncio.to_thread(_gateway_playwright_descendants) == []
    mcp_result = await asyncio.to_thread(
        subprocess.run,
        ["codex", "mcp", "list"],
        text=True,
        capture_output=True,
        check=True,
    )
    mcp_list = mcp_result.stdout
    assert "playwright" in mcp_list and "enabled" in mcp_list

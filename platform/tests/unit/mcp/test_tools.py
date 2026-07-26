import uuid
from datetime import date, datetime, timezone

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from tradingng_platform.assessments.contracts import RunView
from tradingng_platform.auth.principal import Principal
from tradingng_platform.domain.runs import RunStatus
from tradingng_platform.mcp.context import reset_principal, set_principal
from tradingng_platform.mcp.server import create_mcp_server
from tradingng_platform.mcp.services import McpServices


class _View:
    def __init__(self, **values):
        self.values = values

    def model_dump(self, mode="python"):
        del mode
        return self.values


class _Assessments:
    def __init__(self):
        self.submit_calls = []
        self.wait_calls = 0

    async def submit(self, principal, command, request_id):
        principal.require("assessments:submit")
        self.submit_calls.append((principal, command, request_id))
        return [
            RunView(
                id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
                request_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
                ticker=command.items[0].ticker,
                asset_type=command.items[0].asset_type or "stock",
                analysis_date=command.items[0].analysis_date,
                status=RunStatus.QUEUED,
                attempt=1,
                created_at=datetime(2026, 7, 25, tzinfo=timezone.utc),
            )
        ]


class _Records:
    async def instrument_summary(self, principal, ticker):
        principal.require("assessments:read")
        return _View(ticker=ticker.upper())


class _System:
    async def capacity(self, principal):
        principal.require("system:read")
        return _View(admission_allowed=True)


def _principal(*scopes):
    return Principal(
        issuer="https://issuer.example",
        subject="client",
        actor_type="service",
        scopes=frozenset(scopes),
    )


async def _call(server, principal, name, arguments):
    token = set_principal(principal)
    try:
        return await server._tool_manager.call_tool(name, arguments)
    finally:
        reset_principal(token)


@pytest.mark.asyncio
async def test_tool_inventory_and_submit_use_existing_application_command():
    assessments = _Assessments()
    server = create_mcp_server(McpServices(assessments, _Records(), _System()))

    assert {tool.name for tool in server._tool_manager.list_tools()} == {
        "submit_assessment",
        "submit_assessment_batch",
        "get_assessment_status",
        "list_assessments",
        "cancel_assessment",
        "retry_assessment",
        "compare_assessments",
        "get_instrument_summary",
        "get_system_capacity",
        "schedule_validation",
    }

    result = await _call(
        server,
        _principal("assessments:submit"),
        "submit_assessment",
        {
            "ticker": "nvda",
            "analysis_date": "2026-07-25",
            "depth": "deep",
            "analysts": ["market", "social", "news", "fundamentals"],
            "language": "Chinese",
            "memory_mode": "historical",
            "idempotency_key": "mcp-call-123456",
        },
    )
    result = result.model_dump(mode="json")

    assert result["ticker"] == "NVDA"
    assert result["status"] == "queued"
    assert assessments.submit_calls[0][1].items[0].analysis_date == date(2026, 7, 25)
    assert assessments.submit_calls[0][1].items[0].asset_type is None
    assert assessments.submit_calls[0][1].memory_mode.value == "historical"
    await _call(
        server,
        _principal("assessments:submit"),
        "submit_assessment",
        {
            "ticker": "NVDA",
            "analysis_date": "2026-07-25",
            "asset_type": "stock",
            "idempotency_key": "mcp-legacy-123456",  # gitleaks:allow
        },
    )
    assert assessments.submit_calls[1][1].items[0].asset_type.value == "stock"
    assert assessments.wait_calls == 0


@pytest.mark.asyncio
async def test_tool_scope_and_unexpected_errors_are_sanitized():
    server = create_mcp_server(McpServices(_Assessments(), _Records(), _System()))

    with pytest.raises(ToolError, match="PermissionDenied"):
        await _call(
            server,
            _principal("assessments:read"),
            "submit_assessment",
            {
                "ticker": "NVDA",
                "analysis_date": "2026-07-25",
                "idempotency_key": "mcp-call-123456",
            },
        )

    class _BrokenAssessments(_Assessments):
        async def submit(self, principal, command, request_id):
            raise RuntimeError("/tmp/private.sql bearer-secret")

    broken = create_mcp_server(McpServices(_BrokenAssessments(), _Records(), _System()))
    with pytest.raises(ToolError) as captured:
        await _call(
            broken,
            _principal("assessments:submit"),
            "submit_assessment",
            {
                "ticker": "NVDA",
                "analysis_date": "2026-07-25",
                "idempotency_key": "mcp-call-123456",
            },
        )
    assert "InternalError" in str(captured.value)
    assert "/tmp/private.sql" not in str(captured.value)
    assert "bearer-secret" not in str(captured.value)

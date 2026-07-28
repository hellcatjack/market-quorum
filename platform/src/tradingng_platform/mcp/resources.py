from __future__ import annotations

import json
import uuid

from mcp.server.fastmcp import FastMCP

from tradingng_platform.mcp.context import current_principal
from tradingng_platform.mcp.errors import safe_resource
from tradingng_platform.mcp.services import McpServices
from tradingng_platform.records.service import RecordNotFound

_REPORT_SECTIONS = {
    "complete",
    "market",
    "sentiment",
    "news",
    "fundamentals",
    "research",
    "trader",
    "risk",
    "portfolio",
}


def canonical_json(payload) -> str:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def register_resources(server: FastMCP, services: McpServices) -> None:
    @server.resource(
        "tradingng://assessments/{run_id}/summary",
        mime_type="application/json",
    )
    @safe_resource
    async def assessment_summary(run_id: str) -> str:
        parsed_run_id = uuid.UUID(run_id)
        view = await services.assessments.get(current_principal(), parsed_run_id)
        if view is None:
            raise RecordNotFound("assessment was not found")
        return canonical_json(view.model_dump(mode="json"))

    @server.resource(
        "tradingng://assessments/{run_id}/report/{section}",
        mime_type="text/markdown",
    )
    @safe_resource
    async def assessment_report(run_id: str, section: str) -> str:
        if section not in _REPORT_SECTIONS:
            raise ValueError("unsupported report section")
        parsed_run_id = uuid.UUID(run_id)
        artifacts = await services.records.list_artifacts(current_principal(), parsed_run_id)
        matches = [
            artifact
            for artifact in artifacts
            if artifact.media_type in {"text/markdown", "text/plain"}
            and (
                artifact.kind == section
                or artifact.kind.endswith(f"_{section}")
                or artifact.kind == f"{section}_report"
            )
        ]
        if not matches:
            raise RecordNotFound("assessment report section was not found")
        return await services.records.read_report(current_principal(), matches[-1].id)

    @server.resource(
        "tradingng://assessments/{run_id}/evidence",
        mime_type="application/json",
    )
    @safe_resource
    async def assessment_evidence(run_id: str) -> str:
        evidence = await services.records.evidence(current_principal(), uuid.UUID(run_id))
        return canonical_json([item.model_dump(mode="json") for item in evidence])

    @server.resource(
        "tradingng://assessments/{run_id}/integrity",
        mime_type="application/json",
    )
    @safe_resource
    async def assessment_integrity(run_id: str) -> str:
        if services.integrity is None:
            raise RuntimeError("integrity service is unavailable")
        integrity = await services.integrity.get(
            current_principal(),
            uuid.UUID(run_id),
        )
        return canonical_json(integrity.model_dump(mode="json"))

    @server.resource(
        "tradingng://assessments/{run_id}/validations",
        mime_type="application/json",
    )
    @safe_resource
    async def assessment_validations(run_id: str) -> str:
        if services.validation is None:
            raise RuntimeError("validation service is unavailable")
        items = await services.validation.list_for_run(
            current_principal(),
            uuid.UUID(run_id),
        )
        return canonical_json([item.model_dump(mode="json") for item in items])

    @server.resource(
        "tradingng://instruments/{ticker}/history",
        mime_type="application/json",
    )
    @safe_resource
    async def instrument_history(ticker: str) -> str:
        history = await services.records.instrument_history(current_principal(), ticker, 50)
        return canonical_json([item.model_dump(mode="json") for item in history])

    @server.resource("tradingng://system/capacity", mime_type="application/json")
    @safe_resource
    async def system_capacity() -> str:
        capacity = await services.system.capacity(current_principal())
        return canonical_json(capacity.model_dump(mode="json"))

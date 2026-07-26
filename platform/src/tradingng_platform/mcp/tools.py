from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from tradingng_platform.assessments.contracts import (
    AssessmentItem,
    ComparisonRequest,
    ComparisonView,
    Depth,
    MemoryMode,
    RunDetailView,
    RunListFilters,
    RunPage,
    SubmitAssessments,
)
from tradingng_platform.assessments.service import AssessmentNotFound
from tradingng_platform.domain.instruments import AssetType
from tradingng_platform.domain.runs import RunStatus
from tradingng_platform.mcp.context import current_principal
from tradingng_platform.mcp.errors import safe_tool
from tradingng_platform.mcp.services import McpServices
from tradingng_platform.records.contracts import InstrumentSummaryView
from tradingng_platform.system.contracts import CapacityView
from tradingng_platform.validation.contracts import ValidationScheduleResult, ValidationView


class JobAccepted(BaseModel):
    run_id: uuid.UUID
    request_id: uuid.UUID
    ticker: str
    status: RunStatus
    status_uri: str


class BatchAccepted(BaseModel):
    batch_size: int
    runs: list[JobAccepted]


class OperationResult(BaseModel):
    run_id: uuid.UUID
    status: RunStatus
    message: str


def _accepted(run) -> JobAccepted:
    return JobAccepted(
        run_id=run.id,
        request_id=run.request_id,
        ticker=run.ticker,
        status=run.status,
        status_uri=f"/api/v1/assessments/{run.id}",
    )


def _request_id() -> str:
    return f"mcp-{uuid.uuid4().hex}"


def register_tools(server: FastMCP, services: McpServices) -> None:
    @server.tool(structured_output=True)
    @safe_tool
    async def submit_assessment(
        ticker: str,
        analysis_date: date,
        idempotency_key: str,
        depth: Depth = Depth.DEEP,
        memory_mode: MemoryMode = MemoryMode.INDEPENDENT,
        analysts: list[str] | None = None,
        language: str = "Chinese",
        asset_type: AssetType | None = None,
    ) -> JobAccepted:
        """Queue one assessment and return immediately with its run identifier."""
        command = SubmitAssessments(
            items=[
                AssessmentItem(
                    ticker=ticker,
                    asset_type=asset_type,
                    analysis_date=analysis_date,
                )
            ],
            analysts=tuple(analysts or ("market", "social", "news", "fundamentals")),
            depth=depth,
            memory_mode=memory_mode,
            language=language,
            idempotency_key=idempotency_key,
        )
        runs = await services.assessments.submit(current_principal(), command, _request_id())
        if len(runs) != 1:
            raise RuntimeError("single assessment submission did not return exactly one run")
        return _accepted(runs[0])

    @server.tool(structured_output=True)
    @safe_tool
    async def submit_assessment_batch(
        items: Annotated[list[AssessmentItem], Field(min_length=1, max_length=100)],
        idempotency_key: Annotated[str, Field(min_length=8, max_length=128)],
        depth: Depth = Depth.DEEP,
        memory_mode: MemoryMode = MemoryMode.INDEPENDENT,
        analysts: list[str] | None = None,
        language: str = "Chinese",
    ) -> BatchAccepted:
        """Queue up to 100 assessments as one idempotent batch."""
        command = SubmitAssessments(
            items=items,
            analysts=tuple(analysts or ("market", "social", "news", "fundamentals")),
            depth=depth,
            memory_mode=memory_mode,
            language=language,
            idempotency_key=idempotency_key,
        )
        runs = await services.assessments.submit(current_principal(), command, _request_id())
        return BatchAccepted(batch_size=len(runs), runs=[_accepted(run) for run in runs])

    @server.tool(structured_output=True)
    @safe_tool
    async def get_assessment_status(run_id: uuid.UUID) -> RunDetailView:
        """Return the current state and immutable execution metadata for one run."""
        run = await services.assessments.get(current_principal(), run_id)
        if run is None:
            raise AssessmentNotFound(run_id)
        return run

    @server.tool(structured_output=True)
    @safe_tool
    async def list_assessments(
        ticker: str | None = None,
        status: list[RunStatus] | None = None,
        submitted_by: uuid.UUID | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        cursor: str | None = None,
        limit: Annotated[int, Field(ge=1, le=200)] = 50,
    ) -> RunPage:
        """List visible assessments using the same filters and cursor as REST."""
        page = await services.assessments.list(
            current_principal(),
            RunListFilters(
                ticker=ticker,
                status=tuple(status or ()),
                submitted_by=submitted_by,
                created_from=created_from,
                created_to=created_to,
                cursor=cursor,
                limit=limit,
            ),
        )
        return page

    @server.tool(structured_output=True)
    @safe_tool
    async def cancel_assessment(run_id: uuid.UUID) -> OperationResult:
        """Request cancellation of a visible assessment without waiting for completion."""
        run = await services.assessments.cancel(current_principal(), run_id, _request_id())
        return OperationResult(
            run_id=run.id,
            status=run.status,
            message="Cancellation state was recorded",
        )

    @server.tool(structured_output=True)
    @safe_tool
    async def retry_assessment(run_id: uuid.UUID) -> OperationResult:
        """Queue a retry for an eligible assessment and return the new run identifier."""
        run = await services.assessments.retry(current_principal(), run_id, _request_id())
        return OperationResult(
            run_id=run.id,
            status=run.status,
            message="Retry was queued",
        )

    @server.tool(structured_output=True)
    @safe_tool
    async def compare_assessments(
        run_ids: Annotated[list[uuid.UUID], Field(min_length=2, max_length=10)],
    ) -> ComparisonView:
        """Compare two to ten stored runs without starting new work."""
        command = ComparisonRequest(run_ids=run_ids)
        view = await services.assessments.compare(current_principal(), command.run_ids)
        return view

    @server.tool(structured_output=True)
    @safe_tool
    async def get_instrument_summary(ticker: str) -> InstrumentSummaryView:
        """Return the latest stored assessment summary for an instrument."""
        view = await services.records.instrument_summary(current_principal(), ticker)
        return view

    @server.tool(structured_output=True)
    @safe_tool
    async def get_system_capacity() -> CapacityView:
        """Return scheduler and Gateway capacity without changing system state."""
        view = await services.system.capacity(current_principal())
        return view

    @server.tool(structured_output=True)
    @safe_tool
    async def schedule_validation(
        run_id: uuid.UUID,
        horizons: list[int] | None = None,
    ) -> ValidationScheduleResult:
        """Schedule background outcome validation without fetching prices inline."""
        if services.validation is None:
            raise RuntimeError("validation service is unavailable")
        items = await services.validation.schedule(
            current_principal(),
            run_id,
            horizons,
            _request_id(),
        )
        return ValidationScheduleResult(items=items)

    @server.tool(structured_output=True)
    @safe_tool
    async def retry_validation(validation_id: uuid.UUID) -> ValidationView:
        """Retry a failed, unavailable, or expired outcome-validation job."""
        if services.validation is None:
            raise RuntimeError("validation service is unavailable")
        return await services.validation.retry(
            current_principal(),
            validation_id,
            _request_id(),
        )

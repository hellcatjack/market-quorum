import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status

from tradingng_platform.api.auth import require_scopes
from tradingng_platform.api.errors import ApiError, request_id_for
from tradingng_platform.assessments.contracts import (
    ComparisonRequest,
    ComparisonView,
    RunDetailView,
    RunListFilters,
    RunPage,
    RunStepView,
    RunView,
    SubmitAssessments,
)
from tradingng_platform.assessments.service import (
    AssessmentAccessDenied,
    AssessmentAnalystsIncompatible,
    AssessmentAssetTypeConflict,
    AssessmentIdempotencyConflict,
    AssessmentInstrumentIdentityConflict,
    AssessmentNotFound,
    AssessmentRetryNotAllowed,
)
from tradingng_platform.auth.principal import Principal
from tradingng_platform.domain.runs import RunStatus
from tradingng_platform.instruments.classification import (
    InstrumentClassificationNotFound,
    InstrumentClassificationUnavailable,
    InstrumentTypeUnsupported,
)

router = APIRouter(tags=["assessments"])


def _translate(error: Exception) -> None:
    if isinstance(error, AssessmentNotFound):
        raise ApiError(404, "assessment_not_found", "Assessment was not found") from None
    if isinstance(error, AssessmentAccessDenied):
        raise ApiError(403, "assessment_forbidden", "Assessment belongs to another principal")
    if isinstance(error, AssessmentRetryNotAllowed):
        raise ApiError(
            409,
            "retry_not_allowed",
            "Assessment state does not allow retry",
            {"status": error.status.value},
        ) from None
    if isinstance(error, AssessmentIdempotencyConflict):
        raise ApiError(
            409,
            "idempotency_conflict",
            "Idempotency key was already used for another payload",
        ) from None
    if isinstance(error, AssessmentAssetTypeConflict):
        raise ApiError(
            422,
            "asset_type_conflict",
            "Supplied asset type conflicts with the resolved instrument type",
            {
                "ticker": error.ticker,
                "requested": error.requested.value,
                "resolved": error.resolved.value,
            },
        ) from None
    if isinstance(error, AssessmentInstrumentIdentityConflict):
        raise ApiError(
            409,
            "instrument_identity_conflict",
            "Stored instrument identity conflicts with the latest resolved type",
            {
                "ticker": error.ticker,
                "existing": error.existing,
                "resolved": error.resolved.value,
            },
        ) from None
    if isinstance(error, AssessmentAnalystsIncompatible):
        raise ApiError(
            422,
            "incompatible_analysts",
            "No selected analysts are compatible with the resolved instrument type",
            {"ticker": error.ticker, "asset_type": error.asset_type.value},
        ) from None
    if isinstance(error, InstrumentClassificationNotFound):
        raise ApiError(
            422,
            "instrument_not_found",
            "Instrument could not be identified by an exact symbol match",
            {"ticker": error.ticker},
        ) from None
    if isinstance(error, InstrumentTypeUnsupported):
        raise ApiError(
            422,
            "instrument_type_unsupported",
            "Resolved instrument type is not supported",
            {"ticker": error.ticker, "quote_type": error.quote_type},
        ) from None
    if isinstance(error, InstrumentClassificationUnavailable):
        raise ApiError(
            503,
            "instrument_classification_unavailable",
            "Instrument classification is temporarily unavailable",
            {"ticker": error.ticker},
        ) from None
    if isinstance(error, ValueError):
        raise ApiError(422, "invalid_request", str(error)) from None
    raise error


async def _submit(
    command: SubmitAssessments,
    request: Request,
    response: Response,
    principal: Principal,
) -> RunPage:
    try:
        runs = await request.app.state.assessments.submit(
            principal,
            command,
            request_id_for(request),
        )
    except (
        AssessmentAnalystsIncompatible,
        AssessmentAssetTypeConflict,
        AssessmentIdempotencyConflict,
        AssessmentInstrumentIdentityConflict,
        InstrumentClassificationNotFound,
        InstrumentClassificationUnavailable,
        InstrumentTypeUnsupported,
    ) as error:
        _translate(error)
    if not runs:
        raise ApiError(500, "internal_error", "Assessment submission returned no runs")
    response.headers["Location"] = f"/api/v1/assessments/{runs[0].id}"
    return RunPage(items=runs)


@router.post(
    "/assessments",
    response_model=RunPage,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="submit_assessment",
)
async def submit_assessment(
    command: SubmitAssessments,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(require_scopes("assessments:submit"))],
) -> RunPage:
    if len(command.items) != 1:
        raise ApiError(
            422,
            "invalid_request",
            "Single assessment endpoint requires exactly one item",
        )
    return await _submit(command, request, response, principal)


@router.post(
    "/assessment-batches",
    response_model=RunPage,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="submit_assessment_batch",
)
async def submit_assessment_batch(
    command: SubmitAssessments,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(require_scopes("assessments:submit"))],
) -> RunPage:
    return await _submit(command, request, response, principal)


@router.get(
    "/assessments",
    response_model=RunPage,
    operation_id="list_assessments",
)
async def list_assessments(
    request: Request,
    principal: Annotated[Principal, Depends(require_scopes("assessments:read"))],
    ticker: str | None = None,
    run_status: Annotated[list[RunStatus] | None, Query(alias="status")] = None,
    submitted_by: uuid.UUID | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> RunPage:
    try:
        return await request.app.state.assessments.list(
            principal,
            RunListFilters(
                ticker=ticker,
                status=tuple(run_status or ()),
                submitted_by=submitted_by,
                created_from=created_from,
                created_to=created_to,
                cursor=cursor,
                limit=limit,
            ),
        )
    except ValueError as error:
        _translate(error)


@router.get(
    "/assessments/{run_id}",
    response_model=RunDetailView,
    operation_id="get_assessment",
)
async def get_assessment(
    run_id: uuid.UUID,
    request: Request,
    principal: Annotated[Principal, Depends(require_scopes("assessments:read"))],
) -> RunDetailView:
    run = await request.app.state.assessments.get(principal, run_id)
    if run is None:
        raise ApiError(404, "assessment_not_found", "Assessment was not found")
    return run


@router.get(
    "/assessments/{run_id}/steps",
    response_model=list[RunStepView],
    operation_id="list_assessment_steps",
)
async def list_assessment_steps(
    run_id: uuid.UUID,
    request: Request,
    principal: Annotated[Principal, Depends(require_scopes("assessments:read"))],
) -> list[RunStepView]:
    try:
        return await request.app.state.assessments.steps(principal, run_id)
    except AssessmentNotFound as error:
        _translate(error)


@router.post(
    "/assessments/{run_id}/cancel",
    response_model=RunView,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="cancel_assessment",
)
async def cancel_assessment(
    run_id: uuid.UUID,
    request: Request,
    principal: Annotated[Principal, Depends(require_scopes("assessments:cancel"))],
) -> RunView:
    try:
        return await request.app.state.assessments.cancel(
            principal,
            run_id,
            request_id_for(request),
        )
    except (AssessmentNotFound, AssessmentAccessDenied) as error:
        _translate(error)


@router.post(
    "/assessments/{run_id}/retry",
    response_model=RunView,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="retry_assessment",
)
async def retry_assessment(
    run_id: uuid.UUID,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(require_scopes("assessments:submit"))],
) -> RunView:
    try:
        run = await request.app.state.assessments.retry(
            principal,
            run_id,
            request_id_for(request),
        )
    except (AssessmentNotFound, AssessmentAccessDenied, AssessmentRetryNotAllowed) as error:
        _translate(error)
    response.headers["Location"] = f"/api/v1/assessments/{run.id}"
    return run


@router.post(
    "/assessment-comparisons",
    response_model=ComparisonView,
    operation_id="compare_assessments",
)
async def compare_assessments(
    command: ComparisonRequest,
    request: Request,
    principal: Annotated[Principal, Depends(require_scopes("assessments:read"))],
) -> ComparisonView:
    try:
        return await request.app.state.assessments.compare(principal, command.run_ids)
    except AssessmentNotFound as error:
        _translate(error)

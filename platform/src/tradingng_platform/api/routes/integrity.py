import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from tradingng_platform.api.auth import require_scopes
from tradingng_platform.api.errors import ApiError, request_id_for
from tradingng_platform.assessments.contracts import RunView
from tradingng_platform.auth.principal import Principal
from tradingng_platform.integrity.contracts import IntegritySummaryView, IntegrityView
from tradingng_platform.integrity.service import (
    CleanReassessmentNotAllowed,
    IntegrityNotFound,
)

router = APIRouter(tags=["integrity"])


def _translate(error: Exception) -> None:
    if isinstance(error, IntegrityNotFound):
        raise ApiError(404, "assessment_not_found", "Assessment was not found") from None
    if isinstance(error, CleanReassessmentNotAllowed):
        raise ApiError(
            409,
            "clean_reassessment_not_allowed",
            "Assessment cannot be cleanly reassessed",
            {"reason": error.reason},
        ) from None
    raise error


@router.get(
    "/assessments/{run_id}/integrity",
    response_model=IntegrityView,
    operation_id="get_assessment_integrity",
)
async def get_assessment_integrity(
    run_id: uuid.UUID,
    request: Request,
    principal: Annotated[Principal, Depends(require_scopes("assessments:read"))],
) -> IntegrityView:
    try:
        return await request.app.state.integrity.get(principal, run_id)
    except IntegrityNotFound as error:
        _translate(error)


@router.get(
    "/integrity/summary",
    response_model=IntegritySummaryView,
    operation_id="get_integrity_summary",
)
async def get_integrity_summary(
    request: Request,
    principal: Annotated[Principal, Depends(require_scopes("assessments:read"))],
) -> IntegritySummaryView:
    return await request.app.state.integrity.summary(principal)


@router.post(
    "/assessments/{run_id}/clean-reassessment",
    response_model=RunView,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="clean_reassess_assessment",
)
async def clean_reassess_assessment(
    run_id: uuid.UUID,
    request: Request,
    response: Response,
    principal: Annotated[
        Principal,
        Depends(require_scopes("assessments:admin", "assessments:submit")),
    ],
) -> RunView:
    try:
        run = await request.app.state.integrity.clean_reassess(
            principal,
            run_id,
            request_id_for(request),
        )
    except (IntegrityNotFound, CleanReassessmentNotAllowed) as error:
        _translate(error)
    response.headers["Location"] = f"/api/v1/assessments/{run.id}"
    return run

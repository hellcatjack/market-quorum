import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status

from tradingng_platform.api.auth import require_scopes
from tradingng_platform.api.errors import ApiError, request_id_for
from tradingng_platform.auth.principal import Principal
from tradingng_platform.validation.contracts import ScheduleValidation, ValidationView

router = APIRouter(tags=["validations"])


@router.post(
    "/validations",
    response_model=list[ValidationView],
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="schedule_validation",
)
async def schedule_validation(
    command: ScheduleValidation,
    request: Request,
    principal: Annotated[Principal, Depends(require_scopes("validations:write"))],
) -> list[ValidationView]:
    try:
        return await request.app.state.validation.schedule(
            principal,
            command.run_id,
            command.horizons,
            request_id_for(request),
        )
    except ValueError:
        raise ApiError(409, "validation_not_allowed", "Assessment cannot be validated") from None


@router.get(
    "/validations",
    response_model=list[ValidationView],
    operation_id="list_validations",
)
async def list_validations(
    request: Request,
    principal: Annotated[Principal, Depends(require_scopes("validations:read"))],
    validation_status: Annotated[str | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[ValidationView]:
    return await request.app.state.validation.list(
        principal,
        status=validation_status,
        limit=limit,
    )


@router.get(
    "/assessments/{run_id}/validations",
    response_model=list[ValidationView],
    operation_id="list_assessment_validations",
)
async def list_assessment_validations(
    run_id: uuid.UUID,
    request: Request,
    principal: Annotated[Principal, Depends(require_scopes("validations:read"))],
) -> list[ValidationView]:
    return await request.app.state.validation.list_for_run(principal, run_id)


@router.post(
    "/validations/{validation_id}/retry",
    response_model=ValidationView,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="retry_validation",
)
async def retry_validation(
    validation_id: uuid.UUID,
    request: Request,
    principal: Annotated[Principal, Depends(require_scopes("validations:write"))],
) -> ValidationView:
    try:
        return await request.app.state.validation.retry(
            principal,
            validation_id,
            request_id_for(request),
        )
    except ValueError:
        raise ApiError(
            409,
            "validation_retry_not_allowed",
            "Validation cannot be retried",
        ) from None

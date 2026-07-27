import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from tradingng_platform.api.auth import require_scopes
from tradingng_platform.api.errors import ApiError, request_id_for
from tradingng_platform.auth.principal import Principal
from tradingng_platform.auth.tokens import (
    ApiCredentialView,
    CreateApiCredential,
    CreatedApiCredentialView,
    CreatedApiToken,
)
from tradingng_platform.system.contracts import (
    CapacityView,
    ModelRoutingPolicyCommand,
    ModelRoutingPolicyView,
    SchedulerPolicyCommand,
    SchedulerPolicyView,
)

router = APIRouter(tags=["system"])


@router.get("/system/status", operation_id="get_system_status")
async def system_status(
    request: Request,
    principal: Annotated[Principal, Depends(require_scopes("system:read"))],
) -> dict:
    return await request.app.state.system.status(principal)


@router.get(
    "/system/capacity",
    response_model=CapacityView,
    operation_id="get_system_capacity",
)
async def system_capacity(
    request: Request,
    principal: Annotated[Principal, Depends(require_scopes("system:read"))],
) -> CapacityView:
    return await request.app.state.system.capacity(principal)


@router.get(
    "/system/scheduler-policy",
    response_model=SchedulerPolicyView,
    operation_id="get_scheduler_policy",
)
async def scheduler_policy(
    request: Request,
    principal: Annotated[Principal, Depends(require_scopes("system:read"))],
) -> SchedulerPolicyView:
    return await request.app.state.system.get_scheduler_policy(principal)


@router.put(
    "/system/scheduler-policy",
    response_model=SchedulerPolicyView,
    operation_id="update_scheduler_policy",
)
async def update_scheduler_policy(
    command: SchedulerPolicyCommand,
    request: Request,
    principal: Annotated[Principal, Depends(require_scopes("assessments:admin"))],
) -> SchedulerPolicyView:
    return await request.app.state.system.update_scheduler_policy(
        principal,
        command,
        request_id_for(request),
    )


@router.get(
    "/system/model-routing",
    response_model=ModelRoutingPolicyView,
    operation_id="get_model_routing_policy",
)
async def model_routing_policy(
    request: Request,
    principal: Annotated[Principal, Depends(require_scopes("system:read"))],
) -> ModelRoutingPolicyView:
    return await request.app.state.system.get_model_routing(principal)


@router.put(
    "/system/model-routing",
    response_model=ModelRoutingPolicyView,
    operation_id="update_model_routing_policy",
)
async def update_model_routing_policy(
    command: ModelRoutingPolicyCommand,
    request: Request,
    principal: Annotated[Principal, Depends(require_scopes("assessments:admin"))],
) -> ModelRoutingPolicyView:
    return await request.app.state.system.update_model_routing(
        principal,
        command,
        request_id_for(request),
    )


@router.post(
    "/api-credentials",
    response_model=CreatedApiCredentialView,
    status_code=status.HTTP_201_CREATED,
    operation_id="create_api_credential",
)
async def create_api_credential(
    command: CreateApiCredential,
    request: Request,
    principal: Annotated[Principal, Depends(require_scopes("assessments:admin"))],
) -> CreatedApiCredentialView:
    created = await request.app.state.api_tokens.create(
        principal,
        command.scopes,
        command.expires_at,
        request_id=request_id_for(request),
    )
    if isinstance(created, CreatedApiToken):
        return CreatedApiCredentialView(
            id=created.credential.id,
            token=created.token,
            scopes=set(created.credential.scopes_json),
            expires_at=created.credential.expires_at,
        )
    return CreatedApiCredentialView.model_validate(created)


@router.get(
    "/api-credentials",
    response_model=list[ApiCredentialView],
    operation_id="list_api_credentials",
)
async def list_api_credentials(
    request: Request,
    principal: Annotated[Principal, Depends(require_scopes("assessments:admin"))],
) -> list[ApiCredentialView]:
    return await request.app.state.api_tokens.list(principal)


@router.delete(
    "/api-credentials/{credential_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="revoke_api_credential",
)
async def revoke_api_credential(
    credential_id: uuid.UUID,
    request: Request,
    principal: Annotated[Principal, Depends(require_scopes("assessments:admin"))],
) -> Response:
    try:
        await request.app.state.api_tokens.revoke(
            principal,
            credential_id,
            request_id_for(request),
        )
    except ValueError:
        raise ApiError(404, "api_credential_not_found", "API credential was not found") from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)

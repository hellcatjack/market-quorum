import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from tradingng_platform.api.auth import require_scopes
from tradingng_platform.api.errors import ApiError, request_id_for
from tradingng_platform.auth.principal import Principal
from tradingng_platform.webhooks.contracts import CreateWebhook, WebhookView
from tradingng_platform.webhooks.service import WebhookNotFound
from tradingng_platform.webhooks.worker import EndpointRejected

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post(
    "",
    response_model=WebhookView,
    status_code=status.HTTP_201_CREATED,
    operation_id="create_webhook",
)
async def create_webhook(
    command: CreateWebhook,
    request: Request,
    principal: Annotated[Principal, Depends(require_scopes("assessments:admin"))],
) -> WebhookView:
    try:
        return await request.app.state.webhooks.create(
            principal,
            command,
            request_id_for(request),
        )
    except EndpointRejected as exc:
        raise ApiError(422, "webhook_endpoint_rejected", str(exc)) from None


@router.get(
    "",
    response_model=list[WebhookView],
    operation_id="list_webhooks",
)
async def list_webhooks(
    request: Request,
    principal: Annotated[Principal, Depends(require_scopes("assessments:admin"))],
) -> list[WebhookView]:
    return await request.app.state.webhooks.list(principal)


@router.delete(
    "/{webhook_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="disable_webhook",
)
async def disable_webhook(
    webhook_id: uuid.UUID,
    request: Request,
    principal: Annotated[Principal, Depends(require_scopes("assessments:admin"))],
) -> Response:
    try:
        await request.app.state.webhooks.deactivate(
            principal,
            webhook_id,
            request_id_for(request),
        )
    except WebhookNotFound:
        raise ApiError(404, "webhook_not_found", "Webhook was not found") from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)

from typing import Annotated

from fastapi import APIRouter, Depends

from tradingng_platform.api.auth import current_principal
from tradingng_platform.api.routes.artifacts import router as artifacts_router
from tradingng_platform.api.routes.assessments import router as assessments_router
from tradingng_platform.api.routes.collaboration import router as collaboration_router
from tradingng_platform.api.routes.events import router as events_router
from tradingng_platform.api.routes.instruments import router as instruments_router
from tradingng_platform.api.routes.integrity import router as integrity_router
from tradingng_platform.api.routes.system import router as system_router
from tradingng_platform.api.routes.validations import router as validations_router
from tradingng_platform.api.routes.webhooks import router as webhooks_router
from tradingng_platform.auth.principal import Principal

api_router = APIRouter()


@api_router.get("/me", operation_id="get_current_principal")
async def me(principal: Annotated[Principal, Depends(current_principal)]) -> dict:
    return {
        "issuer": principal.issuer,
        "subject": principal.subject,
        "actor_type": principal.actor_type,
        "display_name": principal.display_name,
        "email": principal.email,
        "scopes": sorted(principal.scopes),
        "roles": sorted(principal.roles),
    }


api_router.include_router(assessments_router)
api_router.include_router(events_router)
api_router.include_router(artifacts_router)
api_router.include_router(collaboration_router)
api_router.include_router(instruments_router)
api_router.include_router(integrity_router)
api_router.include_router(system_router)
api_router.include_router(validations_router)
api_router.include_router(webhooks_router)

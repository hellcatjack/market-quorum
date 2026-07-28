import asyncio
import logging
from contextlib import AsyncExitStack, asynccontextmanager
from urllib.parse import urlsplit

import uvicorn
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from starlette.routing import Route

from tradingng_platform.api.dependencies import BrowserCsrfMiddleware, RequestIdMiddleware
from tradingng_platform.api.errors import (
    ApiError,
    api_error_handler,
    permission_error_handler,
    unexpected_error_handler,
    validation_error_handler,
)
from tradingng_platform.api.routes import api_router
from tradingng_platform.api.routes.health import health_router
from tradingng_platform.artifacts.store import LocalArtifactStore
from tradingng_platform.assessments.service import AssessmentService
from tradingng_platform.auth.oidc import OidcVerifier
from tradingng_platform.auth.tokens import ApiTokenService
from tradingng_platform.config import Settings
from tradingng_platform.db import Database
from tradingng_platform.gateway.client import GatewayClient
from tradingng_platform.instruments.classification import YahooInstrumentClassifier
from tradingng_platform.integrity.service import IntegrityService
from tradingng_platform.mcp.auth import McpSecurityMiddleware
from tradingng_platform.mcp.server import create_mcp_server, protected_resource_metadata
from tradingng_platform.mcp.services import McpServices
from tradingng_platform.observability.metrics import refresh_database_metrics, render_metrics
from tradingng_platform.records.service import RecordService
from tradingng_platform.scheduler.probes import SystemProbe
from tradingng_platform.system.service import SystemService
from tradingng_platform.validation.repository import ValidationRepository
from tradingng_platform.validation.service import ValidationService
from tradingng_platform.vendors.alpha_vantage_client import AsyncAlphaVantageBrokerClient
from tradingng_platform.webhooks.service import WebhookService


class _McpEndpoint:
    async def __call__(self, scope, receive, send) -> None:
        child_scope = dict(scope)
        child_scope["root_path"] = f"{scope.get('root_path', '')}/mcp"
        child_scope["path"] = "/"
        child_scope["raw_path"] = b"/"
        await scope["app"].state.mcp_asgi(child_scope, receive, send)


def create_app(
    *,
    settings: Settings | None = None,
    database: Database | None = None,
    oidc: OidcVerifier | None = None,
    mcp_oidc: OidcVerifier | None = None,
    instrument_classifier=None,
) -> FastAPI:
    app_settings = settings or Settings()
    mcp_server = None
    mcp_http_app = None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        nonlocal mcp_http_app, mcp_server
        owned_database = database is None
        resolved_database = database or Database(app_settings)
        resolved_classifier = instrument_classifier or YahooInstrumentClassifier()
        app.state.settings = app_settings
        app.state.database = resolved_database
        app.state.oidc = oidc or OidcVerifier(
            str(app_settings.oidc_issuer),
            app_settings.oidc_audience,
            app_settings.oidc_jwks_ttl_seconds,
        )
        app.state.api_tokens = ApiTokenService(
            resolved_database.sessions,
            app_settings.token_pepper.get_secret_value(),
        )
        app.state.assessments = AssessmentService(
            resolved_database.sessions,
            resolved_classifier,
        )
        app.state.integrity = IntegrityService(resolved_database.sessions)
        app.state.records = RecordService(
            resolved_database.sessions,
            LocalArtifactStore(app_settings.artifact_dir),
            app_settings.job_dir,
        )
        app.state.system = SystemService(
            resolved_database.sessions,
            GatewayClient(str(app_settings.gateway_url)),
            SystemProbe(app_settings.data_dir),
            alpha_broker_client=AsyncAlphaVantageBrokerClient(
                str(app_settings.alpha_vantage_broker_url),
                consumer="system",
                timeout=5,
            ),
            alpha_broker_queue_limit=(app_settings.alpha_vantage_broker_admission_queue_limit),
        )
        app.state.webhooks = WebhookService(
            resolved_database.sessions,
            app_settings.webhook_encryption_key.get_secret_value(),
            app_settings.webhook_private_host_allowlist,
        )
        app.state.validation = ValidationService(ValidationRepository(resolved_database.sessions))
        app.state.mcp_oidc = mcp_oidc or OidcVerifier(
            str(app_settings.oidc_issuer),
            str(app_settings.mcp_resource_uri),
            app_settings.oidc_jwks_ttl_seconds,
        )
        public_mcp_host = urlsplit(str(app_settings.mcp_resource_uri)).netloc
        mcp_server = create_mcp_server(
            McpServices.from_database(
                resolved_database,
                app_settings,
                resolved_classifier,
            ),
            allowed_hosts=(public_mcp_host, "127.0.0.1:*", "localhost:*", "[::1]:*"),
            allowed_origins=app_settings.mcp_allowed_origins,
        )
        mcp_http_app = mcp_server.streamable_http_app()
        app.state.mcp_asgi = McpSecurityMiddleware(
            mcp_http_app,
            verifier=app.state.mcp_oidc,
            allowed_origins=app_settings.mcp_allowed_origins,
            resource_metadata_url=(
                f"{str(app_settings.mcp_resource_uri).removesuffix('/mcp')}"
                "/.well-known/oauth-protected-resource/mcp"
            ),
        )
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(mcp_server.session_manager.run())
            yield
            if owned_database:
                await resolved_database.close()

    app = FastAPI(
        title="TradingNG Platform API",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(PermissionError, permission_error_handler)
    app.add_exception_handler(Exception, unexpected_error_handler)
    app.add_middleware(
        BrowserCsrfMiddleware,
        allowed_origins=app_settings.allowed_origins,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(app_settings.allowed_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID", "Last-Event-ID"],
        expose_headers=["X-Request-ID", "Location"],
    )
    app.add_middleware(RequestIdMiddleware)
    app.include_router(health_router)
    app.include_router(api_router, prefix="/api/v1")

    @app.get("/metrics", include_in_schema=False)
    async def prometheus_metrics(request):
        await refresh_database_metrics(request.app.state.database.sessions)
        return Response(render_metrics(), media_type="text/plain; version=0.0.4")

    @app.get("/.well-known/oauth-protected-resource/mcp")
    @app.get("/.well-known/oauth-protected-resource")
    async def oauth_protected_resource_metadata():
        return protected_resource_metadata(app_settings)

    app.router.routes.append(
        Route("/mcp", endpoint=_McpEndpoint(), methods=None, name="mcp", include_in_schema=False)
    )
    return app


def main() -> None:
    settings = Settings()
    logging.basicConfig(level=logging.INFO)
    config = uvicorn.Config(
        create_app(settings=settings),
        host=settings.api_host,
        port=settings.api_port,
        log_level="info",
    )
    asyncio.run(uvicorn.Server(config).serve())


if __name__ == "__main__":
    main()

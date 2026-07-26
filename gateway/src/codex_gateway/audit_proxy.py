from __future__ import annotations

import argparse
import ipaddress
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from codex_gateway.audit_store import AuditStore

_REQUEST_EXCLUDED_HEADERS = {
    "connection",
    "content-length",
    "host",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}
_RESPONSE_EXCLUDED_HEADERS = {
    "connection",
    "content-encoding",
    "content-length",
    "transfer-encoding",
}


def validate_loopback_host(host: str) -> str:
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError("audit proxy host must be a loopback IP address") from exc
    if not address.is_loopback:
        raise ValueError("audit proxy host must be a loopback IP address")
    return host


def _forward_request_headers(headers: httpx.Headers) -> dict[str, str]:
    return {
        name: value
        for name, value in headers.items()
        if name.lower() not in _REQUEST_EXCLUDED_HEADERS
    }


def _forward_response_headers(headers: httpx.Headers) -> dict[str, str]:
    return {
        name: value
        for name, value in headers.items()
        if name.lower() not in _RESPONSE_EXCLUDED_HEADERS
    }


def create_audit_proxy(
    *,
    upstream_url: str,
    audit_dir: Path,
    client: httpx.AsyncClient | None = None,
) -> FastAPI:
    store = AuditStore(audit_dir)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned_client = app.state.upstream_client is None
        if owned_client:
            app.state.upstream_client = httpx.AsyncClient(
                base_url=upstream_url,
                timeout=httpx.Timeout(620.0),
            )
        try:
            yield
        finally:
            if owned_client:
                await app.state.upstream_client.aclose()

    app = FastAPI(title="TradingNG Gateway Audit Proxy", lifespan=lifespan)
    app.state.upstream_client = client
    app.state.audit_store = store

    @app.api_route(
        "/{proxied_path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    )
    async def forward(request: Request, proxied_path: str) -> Response:
        del proxied_path
        body = await request.body()
        path = request.url.path
        if request.url.query:
            path = f"{path}?{request.url.query}"
        pending = await store.begin(
            method=request.method,
            path=path,
            headers=request.headers,
            body=body,
        )
        upstream_client: httpx.AsyncClient = app.state.upstream_client
        try:
            upstream_response = await upstream_client.request(
                request.method,
                path,
                content=body,
                headers=_forward_request_headers(request.headers),
            )
        except httpx.RequestError as exc:
            await store.fail(pending, error_type=type(exc).__name__)
            return JSONResponse(
                status_code=502,
                content={
                    "error": {
                        "message": "The Gateway upstream is unavailable",
                        "type": "server_error",
                        "code": "upstream_unavailable",
                    }
                },
            )

        response_body = upstream_response.content
        await store.complete(
            pending,
            status_code=upstream_response.status_code,
            headers=upstream_response.headers,
            body=response_body,
        )
        return Response(
            content=response_body,
            status_code=upstream_response.status_code,
            headers=_forward_response_headers(upstream_response.headers),
        )

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Record and forward Gateway HTTP exchanges")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--upstream", default="http://127.0.0.1:8000")
    parser.add_argument("--audit-dir", type=Path, required=True)
    args = parser.parse_args()
    host = validate_loopback_host(args.host)
    app = create_audit_proxy(upstream_url=args.upstream, audit_dir=args.audit_dir)
    uvicorn.run(app, host=host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()

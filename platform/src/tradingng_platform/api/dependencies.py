import re
import uuid

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from tradingng_platform.api.errors import (
    ApiError,
    error_response,
    request_id_context,
)

_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class BrowserCsrfMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, allowed_origins: tuple[str, ...]):
        super().__init__(app)
        self.allowed_origins = frozenset(origin.rstrip("/") for origin in allowed_origins)

    async def dispatch(self, request: Request, call_next):
        if request.method in _UNSAFE_METHODS:
            origin = request.headers.get("Origin")
            fetch_site = request.headers.get("Sec-Fetch-Site", "").lower()
            if fetch_site == "cross-site" or (
                origin is not None and origin.rstrip("/") not in self.allowed_origins
            ):
                return error_response(
                    ApiError(403, "csrf_rejected", "Cross-site browser write rejected"),
                    getattr(request.state, "request_id", request_id_context.get()),
                )
            if request.method in {"POST", "PUT", "PATCH"} and request.url.path.startswith("/api/"):
                media_type = request.headers.get("Content-Type", "").split(";", 1)[0].lower()
                if media_type != "application/json":
                    return error_response(
                        ApiError(
                            415,
                            "unsupported_media_type",
                            "Application JSON content is required",
                        ),
                        getattr(request.state, "request_id", request_id_context.get()),
                    )
        return await call_next(request)


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        supplied = request.headers.get("X-Request-ID", "")
        request_id = supplied if _REQUEST_ID.fullmatch(supplied) else uuid.uuid4().hex
        request.state.request_id = request_id
        token = request_id_context.set(request_id)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            request_id_context.reset(token)

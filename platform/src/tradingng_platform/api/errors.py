import logging
from contextvars import ContextVar

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from tradingng_platform.identity.errors import IdentityError

logger = logging.getLogger(__name__)
request_id_context: ContextVar[str] = ContextVar("request_id", default="unknown")


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict | None = None,
    ):
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
        super().__init__(message)

    def body(self, request_id: str) -> dict:
        error = {
            "code": self.code,
            "message": self.message,
            "request_id": request_id,
        }
        if self.details:
            error["details"] = self.details
        return {"error": error}


def request_id_for(request: Request) -> str:
    return getattr(request.state, "request_id", request_id_context.get())


def error_response(error: ApiError, request_id: str) -> JSONResponse:
    headers = {"X-Request-ID": request_id}
    if error.status_code == 401:
        headers["WWW-Authenticate"] = "Bearer"
    return JSONResponse(
        error.body(request_id),
        status_code=error.status_code,
        headers=headers,
    )


async def api_error_handler(request: Request, error: ApiError) -> JSONResponse:
    return error_response(error, request_id_for(request))


async def identity_error_handler(request: Request, error: IdentityError) -> JSONResponse:
    return error_response(
        ApiError(error.status_code, error.code, error.message),
        request_id_for(request),
    )


async def validation_error_handler(
    request: Request,
    error: RequestValidationError,
) -> JSONResponse:
    details = {
        "fields": [
            {
                "location": [str(part) for part in item["loc"]],
                "message": item["msg"],
                "type": item["type"],
            }
            for item in error.errors()
        ]
    }
    return error_response(
        ApiError(422, "invalid_request", "Request validation failed", details),
        request_id_for(request),
    )


async def permission_error_handler(request: Request, error: PermissionError) -> JSONResponse:
    return error_response(
        ApiError(403, "forbidden", "Required permission is missing"),
        request_id_for(request),
    )


async def unexpected_error_handler(request: Request, error: Exception) -> JSONResponse:
    logger.error("api_internal_error error_type=%s", type(error).__name__)
    return error_response(
        ApiError(500, "internal_error", "Internal server error"),
        request_id_for(request),
    )

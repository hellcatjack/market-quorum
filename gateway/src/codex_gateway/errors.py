class GatewayError(Exception):
    status_code = 500
    error_type = "api_error"
    code = "gateway_error"

    def __init__(self, message: str, *, param: str | None = None):
        super().__init__(message)
        self.message = message
        self.param = param

    def envelope(self) -> dict:
        return {
            "error": {
                "message": self.message,
                "type": self.error_type,
                "param": self.param,
                "code": self.code,
            }
        }


class InvalidRequest(GatewayError):
    status_code = 400
    error_type = "invalid_request_error"
    code = "invalid_request"


class ModelNotFound(GatewayError):
    status_code = 404
    error_type = "invalid_request_error"
    code = "model_not_found"

    def __init__(self, model: str):
        super().__init__(f"The model {model!r} does not exist", param="model")


class RequestTooLarge(GatewayError):
    status_code = 413
    error_type = "invalid_request_error"
    code = "request_too_large"


class CodexContextLimit(GatewayError):
    status_code = 400
    error_type = "invalid_request_error"
    code = "codex_context_limit"


class CodexRateLimit(GatewayError):
    status_code = 429
    error_type = "rate_limit_error"
    code = "codex_rate_limit"


class CodexRuntimeFailure(GatewayError):
    status_code = 502
    code = "codex_runtime_error"


class CodexInterrupted(GatewayError):
    status_code = 502
    code = "codex_interrupted"


class CodexUnavailable(GatewayError):
    status_code = 503
    code = "codex_unavailable"


class CodexTimeout(GatewayError):
    status_code = 504
    code = "codex_timeout"

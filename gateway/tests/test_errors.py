from codex_gateway.errors import (
    CodexContextLimit,
    CodexInterrupted,
    CodexRateLimit,
    CodexRuntimeFailure,
    CodexTimeout,
    CodexUnavailable,
    InvalidRequest,
    ModelNotFound,
    RequestTooLarge,
)


def test_error_statuses_and_envelopes():
    cases = [
        (InvalidRequest("bad", param="messages"), 400, "invalid_request"),
        (ModelNotFound("other"), 404, "model_not_found"),
        (CodexContextLimit("too large"), 400, "codex_context_limit"),
        (CodexRateLimit("limited"), 429, "codex_rate_limit"),
        (CodexInterrupted("stopped"), 502, "codex_interrupted"),
        (CodexRuntimeFailure("crashed"), 502, "codex_runtime_error"),
        (CodexUnavailable("login required"), 503, "codex_unavailable"),
        (CodexTimeout("slow"), 504, "codex_timeout"),
        (RequestTooLarge("large"), 413, "request_too_large"),
    ]
    for error, status, code in cases:
        assert error.status_code == status
        assert error.envelope()["error"]["code"] == code

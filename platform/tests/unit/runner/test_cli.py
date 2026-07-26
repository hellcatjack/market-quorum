from tradingng_platform.runner.cli import classify_runner_error


def test_runner_error_classification_uses_stable_non_secret_codes():
    RateLimitError = type("RateLimitError", (Exception,), {})
    APITimeoutError = type("APITimeoutError", (Exception,), {})
    VendorRateLimitError = type("AlphaVantageRateLimitError", (Exception,), {})

    assert classify_runner_error(RateLimitError("provider secret")) == "gateway_overload"
    assert classify_runner_error(APITimeoutError("provider secret")) == "gateway_unavailable"
    assert classify_runner_error(VendorRateLimitError("key=secret")) == "vendor_rate_limit"
    assert classify_runner_error(ValueError("secret")) == "runner_unhandled_error"

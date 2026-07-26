import json
import logging

from tradingng_platform.observability.logging import JsonFormatter
from tradingng_platform.observability.metrics import REGISTRY


def test_json_logging_recursively_redacts_keys_and_environment_values(monkeypatch):
    monkeypatch.setenv("TRADINGNG_TEST_API_KEY", "known-environment-secret")
    record = logging.LogRecord(
        "tradingng.test",
        logging.INFO,
        __file__,
        1,
        "processed known-environment-secret",
        (),
        None,
    )
    record.request_id = "request-1"
    record.details = {
        "Authorization": "Bearer visible-token",
        "nested": {"password": "visible-password", "safe": "ok"},
        "cookies": ["visible-cookie"],
    }

    rendered = JsonFormatter().format(record)
    payload = json.loads(rendered)

    assert "known-environment-secret" not in rendered
    assert "visible-token" not in rendered
    assert "visible-password" not in rendered
    assert "visible-cookie" not in rendered
    assert payload["details"]["nested"]["safe"] == "ok"
    assert payload["request_id"] == "request-1"


def test_metric_labels_are_bounded_and_never_contain_identity_dimensions():
    forbidden = {"ticker", "run_id", "user_id", "request_id"}
    for collector in REGISTRY._names_to_collectors.values():
        assert forbidden.isdisjoint(getattr(collector, "_labelnames", ()))

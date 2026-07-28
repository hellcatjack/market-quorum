import uuid
from datetime import date
from types import SimpleNamespace

import pytest

from tradingng_platform.integrity import main
from tradingng_platform.integrity.main import _alpha_availability_resolver, parse_args


def test_parse_args_accepts_bounded_limit_and_optional_run_id():
    run_id = uuid.UUID(int=7)

    arguments = parse_args(["--limit", "25", "--run-id", str(run_id)])

    assert arguments.limit == 25
    assert arguments.run_id == run_id


@pytest.mark.parametrize("limit", ["0", "501"])
def test_parse_args_rejects_out_of_range_limit(limit):
    with pytest.raises(SystemExit):
        parse_args(["--limit", limit])


def test_audit_uses_configured_research_broker_consumer(monkeypatch):
    observed = {}

    class FakeBroker:
        def __init__(self, base_url, *, consumer, timeout, client):
            observed.update(
                base_url=base_url,
                consumer=consumer,
                timeout=timeout,
                client=client,
            )

        def query(self, function_name, params, *, run_id):
            observed.update(function_name=function_name, params=params, run_id=run_id)
            return (
                '{"quarterlyEarnings": [{"fiscalDateEnding": "2025-06-30", '
                '"reportedDate": "2025-07-24"}]}'
            )

    monkeypatch.setattr(main, "SyncAlphaVantageBrokerClient", FakeBroker)
    client = object()
    settings = SimpleNamespace(
        alpha_vantage_broker_url="http://broker.test",
        alpha_vantage_broker_request_timeout_seconds=30,
    )

    resolver = _alpha_availability_resolver(settings, client)
    availability = resolver.resolve("NVDA", date(2025, 6, 30), "quarterly")

    assert availability is not None
    assert observed == {
        "base_url": "http://broker.test",
        "consumer": "research",
        "timeout": 30,
        "client": client,
        "function_name": "EARNINGS",
        "params": {"symbol": "NVDA"},
        "run_id": "integrity-audit",
    }

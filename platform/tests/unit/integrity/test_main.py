import uuid
from datetime import date
from types import SimpleNamespace

import pytest

from tradingng_platform.integrity.main import _stocklean_availability_resolver, parse_args


def test_parse_args_accepts_bounded_limit_and_optional_run_id():
    run_id = uuid.UUID(int=7)

    arguments = parse_args(["--limit", "25", "--run-id", str(run_id)])

    assert arguments.limit == 25
    assert arguments.run_id == run_id


@pytest.mark.parametrize("limit", ["0", "501"])
def test_parse_args_rejects_out_of_range_limit(limit):
    with pytest.raises(SystemExit):
        parse_args(["--limit", limit])


def test_audit_uses_stocklean_earnings_without_alpha_broker(monkeypatch):
    observed = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "items": [
                    {
                        "payload": {
                            "quarterlyEarnings": [
                                {
                                    "fiscalDateEnding": "2025-06-30",
                                    "reportedDate": "2025-07-24",
                                }
                            ]
                        }
                    }
                ]
            }

    class Client:
        def get(self, url, *, params, headers, timeout):
            observed.update(url=url, params=params, headers=headers, timeout=timeout)
            return Response()

    client = Client()
    settings = SimpleNamespace(
        stocklean_url="http://stocklean.test",
        stocklean_internal_token=SimpleNamespace(get_secret_value=lambda: "internal-token"),
        stocklean_timeout_seconds=30,
    )

    resolver = _stocklean_availability_resolver(settings, client)
    availability = resolver.resolve("NVDA", date(2025, 6, 30), "quarterly")

    assert availability is not None
    assert observed["url"].startswith("http://stocklean.test/api/internal/v1/alpha/documents/")
    assert observed["params"]["functions"] == "EARNINGS"
    assert observed["headers"]["Authorization"] == "Bearer internal-token"
    assert observed["timeout"] == 30

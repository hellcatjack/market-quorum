import httpx
import pytest

from tradingng_platform.vendors.alpha_vantage_client import (
    AlphaBrokerAuthenticationError,
    AlphaBrokerRateLimitError,
    AlphaBrokerStatus,
    AlphaBrokerTransientError,
    AsyncAlphaVantageBrokerClient,
    SyncAlphaVantageBrokerClient,
)


def _status_payload(**overrides):
    payload = {
        "status": "normal",
        "configured_requests_per_minute": 75,
        "effective_requests_per_minute": 60.0,
        "max_in_flight": 3,
        "in_flight": 1,
        "queued": 2,
        "oldest_queued_seconds": 4.5,
        "blocked_until": None,
        "requests": 12,
        "upstream_requests": 8,
        "cache_hits": 3,
        "coalesced_requests": 1,
        "rate_limits": 0,
        "transient_errors": 0,
    }
    payload.update(overrides)
    return payload


def test_sync_broker_client_returns_body_without_exposing_infrastructure():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/query"
        assert request.headers["x-tradingng-consumer"] == "research"
        assert b"apikey" not in request.content.lower()
        return httpx.Response(200, json={"body": "timestamp,close\n2026-07-27,1\n"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
        client = SyncAlphaVantageBrokerClient(
            "http://broker.test",
            consumer="research",
            client=transport,
        )

        result = client.query("TIME_SERIES_DAILY_ADJUSTED", {"symbol": "NVDA"})

    assert result.startswith("timestamp,close")
    assert "broker.test" not in repr(client)


@pytest.mark.parametrize(
    ("status_code", "code", "error_type"),
    [
        (429, "rate_limit", AlphaBrokerRateLimitError),
        (401, "authentication", AlphaBrokerAuthenticationError),
        (503, "transient", AlphaBrokerTransientError),
    ],
)
def test_sync_broker_client_maps_safe_typed_errors(status_code, code, error_type):
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            status_code,
            json={"code": code, "message": "safe broker message"},
        )
    )
    with httpx.Client(transport=transport) as http_client:
        client = SyncAlphaVantageBrokerClient(
            "http://broker.test",
            consumer="research",
            client=http_client,
        )
        with pytest.raises(error_type, match="safe broker message"):
            client.query("OVERVIEW", {"symbol": "NVDA"})


async def test_async_broker_client_reads_query_and_status():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/status":
            return httpx.Response(200, json=_status_payload())
        assert request.headers["x-tradingng-consumer"] == "validation"
        return httpx.Response(200, json={"body": '{"Time Series (Daily)":{}}'})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
        client = AsyncAlphaVantageBrokerClient(
            "http://broker.test",
            consumer="validation",
            client=transport,
        )

        body = await client.query("TIME_SERIES_DAILY_ADJUSTED", {"symbol": "IBM"})
        status = await client.status()

    assert body.startswith('{"Time Series')
    assert status == AlphaBrokerStatus.model_validate(_status_payload())
    assert status.admission_allowed(queue_limit=6)
    assert not status.model_copy(update={"status": "cooldown"}).admission_allowed(queue_limit=6)
    assert not status.model_copy(update={"queued": 6}).admission_allowed(queue_limit=6)


async def test_async_status_returns_unavailable_snapshot_on_connection_failure():
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
        client = AsyncAlphaVantageBrokerClient(
            "http://broker.test",
            consumer="scheduler",
            client=transport,
        )

        status = await client.status()

    assert status.status == "unavailable"
    assert status.queued == 0
    assert not status.admission_allowed(queue_limit=6)

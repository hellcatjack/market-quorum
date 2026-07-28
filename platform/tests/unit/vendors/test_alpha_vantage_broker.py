import asyncio
import time

import httpx
import pytest

from tradingng_platform.vendors.alpha_vantage import AlphaVantageRetryPolicy
from tradingng_platform.vendors.alpha_vantage_broker import (
    AlphaVantageBroker,
    GlobalQuotaCoordinator,
)


async def test_quota_coordinator_prioritizes_research_waiters():
    coordinator = GlobalQuotaCoordinator(
        requests_per_minute=10000,
        utilization=1,
        max_in_flight=1,
        minute_cooldown_seconds=0.01,
        daily_cooldown_seconds=1,
    )
    await coordinator.start()
    try:
        occupied = await coordinator.acquire(priority=0)
        order = []

        async def acquire(name, priority):
            lease = await coordinator.acquire(priority=priority)
            order.append(name)
            await coordinator.release(lease, outcome="success")

        validation = asyncio.create_task(acquire("validation", 20))
        await asyncio.sleep(0)
        research = asyncio.create_task(acquire("research", 0))
        await asyncio.sleep(0)
        await coordinator.release(occupied, outcome="success")
        await asyncio.gather(validation, research)

        assert order == ["research", "validation"]
    finally:
        await coordinator.close()


async def test_broker_coalesces_identical_in_flight_requests_and_caches_success(tmp_path):
    upstream_calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal upstream_calls
        upstream_calls += 1
        assert request.url.params["apikey"] == "premium-key"
        await asyncio.sleep(0.02)
        return httpx.Response(200, text="timestamp,close\n2026-07-27,100\n")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream:
        broker = AlphaVantageBroker(
            {"research": "premium-key", "validation": "premium-key"},
            requests_per_minute=10000,
            utilization=1,
            max_in_flight=3,
            retry_policy=AlphaVantageRetryPolicy(attempts=2, base_seconds=0.01, max_seconds=1),
            minute_cooldown_seconds=0.01,
            daily_cooldown_seconds=1,
            cache_dir=tmp_path,
            upstream_client=upstream,
            jitter=lambda: 0,
        )
        await broker.start()
        try:
            first, second = await asyncio.gather(
                broker.query("TIME_SERIES_DAILY_ADJUSTED", {"symbol": "NVDA"}, "research"),
                broker.query("TIME_SERIES_DAILY_ADJUSTED", {"symbol": "NVDA"}, "research"),
            )
            third = await broker.query(
                "TIME_SERIES_DAILY_ADJUSTED",
                {"symbol": "NVDA"},
                "validation",
            )
            status = broker.status()
        finally:
            await broker.close()

    assert first.body == second.body == third.body
    assert upstream_calls == 1
    assert status.requests == 3
    assert status.upstream_requests == 1
    assert status.coalesced_requests == 1
    assert status.cache_hits == 1


async def test_broker_bounds_global_in_flight_requests(tmp_path):
    active = 0
    peak = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.02)
        active -= 1
        return httpx.Response(200, text=f"body-{request.url.params['symbol']}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream:
        broker = AlphaVantageBroker(
            {"research": "premium-key"},
            requests_per_minute=10000,
            utilization=1,
            max_in_flight=2,
            retry_policy=AlphaVantageRetryPolicy(attempts=1, base_seconds=0.01, max_seconds=1),
            minute_cooldown_seconds=0.01,
            daily_cooldown_seconds=1,
            cache_dir=tmp_path,
            upstream_client=upstream,
            jitter=lambda: 0,
        )
        await broker.start()
        try:
            await asyncio.gather(
                *(
                    broker.query("OVERVIEW", {"symbol": f"T{index}"}, "research")
                    for index in range(5)
                )
            )
        finally:
            await broker.close()

    assert peak == 2


async def test_rate_limit_stops_dispatch_then_uses_one_half_open_probe(tmp_path):
    call_times = []
    active = 0
    peak_after_limit = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active, peak_after_limit
        call_times.append((request.url.params["symbol"], time.monotonic()))
        active += 1
        if len(call_times) > 1:
            peak_after_limit = max(peak_after_limit, active)
        await asyncio.sleep(0.002)
        active -= 1
        if len(call_times) == 1:
            return httpx.Response(429)
        return httpx.Response(200, text=f"body-{request.url.params['symbol']}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream:
        broker = AlphaVantageBroker(
            {"research": "premium-key"},
            requests_per_minute=10000,
            utilization=1,
            max_in_flight=3,
            retry_policy=AlphaVantageRetryPolicy(attempts=2, base_seconds=0.001, max_seconds=1),
            minute_cooldown_seconds=0.04,
            daily_cooldown_seconds=1,
            cache_dir=tmp_path,
            upstream_client=upstream,
            jitter=lambda: 0,
        )
        await broker.start()
        try:
            await asyncio.gather(
                broker.query("OVERVIEW", {"symbol": "A"}, "research"),
                broker.query("OVERVIEW", {"symbol": "B"}, "research"),
            )
            status = broker.status()
        finally:
            await broker.close()

    assert call_times[1][1] - call_times[0][1] >= 0.035
    assert peak_after_limit == 1
    assert status.status == "normal"
    assert status.rate_limits == 1


async def test_daily_limit_enters_long_cooldown_without_fast_retry(tmp_path):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"Information": "The requests per day limit has been reached."},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream:
        broker = AlphaVantageBroker(
            {"research": "premium-key"},
            requests_per_minute=10000,
            utilization=1,
            max_in_flight=1,
            retry_policy=AlphaVantageRetryPolicy(attempts=3, base_seconds=0.001, max_seconds=1),
            minute_cooldown_seconds=0.01,
            daily_cooldown_seconds=60,
            cache_dir=tmp_path,
            upstream_client=upstream,
            jitter=lambda: 0,
        )
        await broker.start()
        try:
            with pytest.raises(Exception) as captured:
                await broker.query("OVERVIEW", {"symbol": "NVDA"}, "research")
            status = broker.status()
        finally:
            await broker.close()

    assert type(captured.value).__name__ == "AlphaBrokerRateLimitError"
    assert status.status == "cooldown"
    assert status.blocked_until is not None

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from tradingng_platform.models import CircuitBreaker
from tradingng_platform.scheduler.circuits import CircuitBreakerRepository
from tradingng_platform.worker.service import persist_dependency_health

START = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)


async def _gateway_failure(session_factory, now):
    async with session_factory() as session, session.begin():
        await CircuitBreakerRepository(session).record_gateway_sample(
            healthy=False,
            latency_ms=20,
            error_code="gateway_overload",
            detail={"status_code": 429},
            now=now,
        )


async def _breaker(session_factory, name):
    async with session_factory() as session:
        return await session.scalar(select(CircuitBreaker).where(CircuitBreaker.name == name))


async def test_three_gateway_failures_open_and_half_open_allows_one_probe(
    session_factory,
):
    for seconds in (0, 20, 40):
        await _gateway_failure(session_factory, START + timedelta(seconds=seconds))

    breaker = await _breaker(session_factory, "gateway")
    assert breaker.status == "open"
    assert breaker.backoff_seconds == 300
    assert breaker.opened_until == START + timedelta(seconds=340)

    async with session_factory() as session, session.begin():
        repository = CircuitBreakerRepository(session)
        assert await repository.blockers(START + timedelta(seconds=339)) == ("gateway",)
        assert not await repository.acquire_probe("gateway", START + timedelta(seconds=339))

    async with session_factory() as session, session.begin():
        assert await CircuitBreakerRepository(session).acquire_probe(
            "gateway", START + timedelta(seconds=340)
        )
    async with session_factory() as session, session.begin():
        repository = CircuitBreakerRepository(session)
        assert not await repository.acquire_probe("gateway", START + timedelta(seconds=340))
        assert await repository.blockers(START + timedelta(seconds=340)) == ("gateway",)

    async with session_factory() as session, session.begin():
        await CircuitBreakerRepository(session).record_gateway_sample(
            healthy=True,
            latency_ms=5,
            detail={},
            now=START + timedelta(seconds=341),
        )
    breaker = await _breaker(session_factory, "gateway")
    assert breaker.status == "closed"
    assert breaker.failure_count == 0


async def test_repeated_half_open_failures_back_off_to_thirty_minute_cap(
    session_factory,
):
    for seconds in (0, 20, 40):
        await _gateway_failure(session_factory, START + timedelta(seconds=seconds))

    expected_cooldowns = (600, 1200, 1800, 1800)
    probe_at = START + timedelta(seconds=340)
    for expected_cooldown in expected_cooldowns:
        async with session_factory() as session, session.begin():
            assert await CircuitBreakerRepository(session).acquire_probe("gateway", probe_at)
        await _gateway_failure(session_factory, probe_at + timedelta(seconds=1))
        breaker = await _breaker(session_factory, "gateway")
        assert breaker.status == "open"
        assert breaker.backoff_seconds == expected_cooldown
        assert breaker.opened_until == probe_at + timedelta(seconds=expected_cooldown + 1)
        probe_at = breaker.opened_until


async def test_vendor_failures_are_isolated_by_vendor_and_category(session_factory):
    for seconds in (0, 20, 40):
        async with session_factory() as session, session.begin():
            await CircuitBreakerRepository(session).record_vendor_sample(
                vendor="alpha_vantage",
                category="market_data",
                healthy=False,
                latency_ms=50,
                error_code="vendor_rate_limit",
                detail={"status_code": 429},
                now=START + timedelta(seconds=seconds),
            )
    async with session_factory() as session, session.begin():
        await CircuitBreakerRepository(session).record_vendor_sample(
            vendor="yfinance",
            category="market_data",
            healthy=False,
            latency_ms=50,
            error_code="vendor_timeout",
            detail={},
            now=START + timedelta(seconds=40),
        )

    async with session_factory() as session:
        open_names = tuple(
            await session.scalars(
                select(CircuitBreaker.name)
                .where(CircuitBreaker.status == "open")
                .order_by(CircuitBreaker.name)
            )
        )
    assert open_names == ("vendor:market_data:alpha_vantage",)
    async with session_factory() as session:
        repository = CircuitBreakerRepository(session)
        assert await repository.blockers(START + timedelta(seconds=41), vendors={"yfinance"}) == ()
        assert await repository.blockers(
            START + timedelta(seconds=41), vendors={"alpha_vantage"}
        ) == ("vendor:market_data:alpha_vantage",)


async def test_worker_ingests_runner_dependency_health_events(session_factory, tmp_path):
    working = tmp_path / "working"
    working.mkdir()
    events = [
        {
            "scope": "vendor",
            "vendor": "alpha_vantage",
            "category": "core_stock_apis",
            "healthy": False,
            "latency_ms": 10,
            "error_code": "vendor_rate_limit",
            "observed_at": (START + timedelta(seconds=seconds)).isoformat(),
        }
        for seconds in (0, 20, 40)
    ]
    (working / "dependency_health.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )

    async with session_factory() as session, session.begin():
        assert await persist_dependency_health(session, tmp_path) == 3

    breaker = await _breaker(session_factory, "vendor:core_stock_apis:alpha_vantage")
    assert breaker.status == "open"
    assert breaker.last_error_code == "vendor_rate_limit"

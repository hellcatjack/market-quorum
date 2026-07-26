from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from tradingng_platform.gateway.client import GatewaySnapshot
from tradingng_platform.models import (
    CircuitBreaker,
    GatewayHealthSample,
    VendorHealthSample,
)
from tradingng_platform.persistence.upsert import insert_ignore, session_dialect

_FAILURE_WINDOW = timedelta(seconds=60)
_FAILURES_TO_OPEN = 3
_INITIAL_BACKOFF_SECONDS = 300
_MAX_BACKOFF_SECONDS = 1800


class CircuitBreakerRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def record_gateway_sample(
        self,
        *,
        healthy: bool,
        latency_ms: int,
        detail: dict,
        now: datetime | None = None,
        error_code: str | None = None,
        snapshot: GatewaySnapshot | None = None,
    ) -> CircuitBreaker:
        sampled_at = now or datetime.now(timezone.utc)
        self.session.add(
            GatewayHealthSample(
                sampled_at=sampled_at,
                healthy=healthy,
                latency_ms=latency_ms,
                detail_json=detail,
                active_completions=snapshot.active_completions if snapshot else 0,
                model=snapshot.model if snapshot else None,
                reasoning_effort=snapshot.reasoning_effort if snapshot else None,
                snapshot_id=snapshot.snapshot_id if snapshot else None,
            )
        )
        breaker = await self._locked_breaker("gateway")
        failures = await self.session.scalar(
            select(func.count())
            .select_from(GatewayHealthSample)
            .where(
                GatewayHealthSample.sampled_at >= sampled_at - _FAILURE_WINDOW,
                GatewayHealthSample.sampled_at <= sampled_at,
                GatewayHealthSample.healthy.is_(False),
            )
        )
        self._apply_sample(
            breaker,
            healthy=healthy,
            failures=int(failures or 0),
            error_code=error_code,
            now=sampled_at,
        )
        return breaker

    async def record_vendor_sample(
        self,
        *,
        vendor: str,
        category: str,
        healthy: bool,
        latency_ms: int,
        detail: dict,
        now: datetime | None = None,
        error_code: str | None = None,
    ) -> CircuitBreaker:
        sampled_at = now or datetime.now(timezone.utc)
        self.session.add(
            VendorHealthSample(
                sampled_at=sampled_at,
                healthy=healthy,
                latency_ms=latency_ms,
                detail_json=detail,
                vendor=vendor,
                category=category,
            )
        )
        breaker = await self._locked_breaker(f"vendor:{category}:{vendor}")
        failures = await self.session.scalar(
            select(func.count())
            .select_from(VendorHealthSample)
            .where(
                VendorHealthSample.vendor == vendor,
                VendorHealthSample.category == category,
                VendorHealthSample.sampled_at >= sampled_at - _FAILURE_WINDOW,
                VendorHealthSample.sampled_at <= sampled_at,
                VendorHealthSample.healthy.is_(False),
            )
        )
        self._apply_sample(
            breaker,
            healthy=healthy,
            failures=int(failures or 0),
            error_code=error_code,
            now=sampled_at,
        )
        return breaker

    async def blockers(
        self,
        now: datetime | None = None,
        *,
        vendors: set[str] | None = None,
    ) -> tuple[str, ...]:
        checked_at = now or datetime.now(timezone.utc)
        names = tuple(
            await self.session.scalars(
                select(CircuitBreaker.name)
                .where(
                    or_(
                        CircuitBreaker.status == "half_open",
                        (
                            (CircuitBreaker.status == "open")
                            & or_(
                                CircuitBreaker.opened_until.is_(None),
                                CircuitBreaker.opened_until > checked_at,
                            )
                        ),
                    )
                )
                .order_by(CircuitBreaker.name)
            )
        )
        return tuple(name for name in names if _is_relevant(name, vendors))

    async def acquire_probe(self, name: str, now: datetime | None = None) -> bool:
        checked_at = now or datetime.now(timezone.utc)
        breaker = await self.session.scalar(
            select(CircuitBreaker).where(CircuitBreaker.name == name).with_for_update()
        )
        if (
            breaker is None
            or breaker.status != "open"
            or breaker.opened_until is None
            or breaker.opened_until > checked_at
        ):
            return False
        breaker.status = "half_open"
        breaker.opened_until = None
        return True

    async def acquire_expired_probes(
        self,
        now: datetime | None = None,
        *,
        vendors: set[str] | None = None,
    ) -> tuple[str, ...]:
        checked_at = now or datetime.now(timezone.utc)
        names = tuple(
            await self.session.scalars(
                select(CircuitBreaker.name)
                .where(
                    CircuitBreaker.status == "open",
                    CircuitBreaker.opened_until.is_not(None),
                    CircuitBreaker.opened_until <= checked_at,
                )
                .order_by(CircuitBreaker.name)
                .with_for_update()
            )
        )
        names = tuple(name for name in names if _is_relevant(name, vendors))
        for name in names:
            breaker = await self.session.scalar(
                select(CircuitBreaker).where(CircuitBreaker.name == name)
            )
            if breaker is not None:
                breaker.status = "half_open"
                breaker.opened_until = None
        return names

    async def _locked_breaker(self, name: str) -> CircuitBreaker:
        await self.session.execute(
            insert_ignore(
                session_dialect(self.session),
                CircuitBreaker,
                {
                    "name": name,
                    "status": "closed",
                    "failure_count": 0,
                    "opened_until": None,
                    "backoff_seconds": _INITIAL_BACKOFF_SECONDS,
                    "last_error_code": None,
                    "created_at": datetime.now(timezone.utc),
                },
                [CircuitBreaker.name],
            )
        )
        breaker = await self.session.scalar(
            select(CircuitBreaker).where(CircuitBreaker.name == name).with_for_update()
        )
        if breaker is None:
            raise RuntimeError("circuit breaker seed is not visible")
        return breaker

    @staticmethod
    def _apply_sample(
        breaker: CircuitBreaker,
        *,
        healthy: bool,
        failures: int,
        error_code: str | None,
        now: datetime,
    ) -> None:
        if healthy:
            if breaker.status == "half_open":
                breaker.status = "closed"
                breaker.failure_count = 0
                breaker.opened_until = None
                breaker.backoff_seconds = _INITIAL_BACKOFF_SECONDS
                breaker.last_error_code = None
            return

        breaker.failure_count = failures
        breaker.last_error_code = error_code
        if breaker.status == "half_open":
            breaker.backoff_seconds = min(
                breaker.backoff_seconds * 2,
                _MAX_BACKOFF_SECONDS,
            )
            breaker.status = "open"
            breaker.opened_until = now + timedelta(seconds=breaker.backoff_seconds)
        elif breaker.status == "closed" and failures >= _FAILURES_TO_OPEN:
            breaker.status = "open"
            breaker.opened_until = now + timedelta(seconds=breaker.backoff_seconds)


def _is_relevant(name: str, vendors: set[str] | None) -> bool:
    if name == "gateway" or vendors is None:
        return True
    if not name.startswith("vendor:"):
        return False
    _, _, vendor = name.split(":", 2)
    return vendor in vendors

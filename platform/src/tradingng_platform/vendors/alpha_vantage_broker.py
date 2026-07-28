from __future__ import annotations

import asyncio
import contextlib
import hashlib
import heapq
import json
import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import httpx

from tradingng_platform.vendors.alpha_vantage import (
    AlphaVantageRetryPolicy,
    alpha_key_fingerprint,
    classify_alpha_payload,
)
from tradingng_platform.vendors.alpha_vantage_client import (
    AlphaBrokerAuthenticationError,
    AlphaBrokerRateLimitError,
    AlphaBrokerStatus,
    AlphaBrokerTransientError,
)

_ALPHA_VANTAGE_URL = "https://www.alphavantage.co/query"
_RESEARCH_PRIORITY = 0
_VALIDATION_PRIORITY = 20
_DEFAULT_PRIORITY = 50


@dataclass(frozen=True)
class QuotaLease:
    probe: bool


@dataclass
class _Waiter:
    priority: int
    sequence: int
    enqueued_at: float
    future: asyncio.Future


class GlobalQuotaCoordinator:
    """Dispatch just-in-time request leases for one API-key identity."""

    def __init__(
        self,
        *,
        requests_per_minute: int,
        utilization: float,
        max_in_flight: int,
        minute_cooldown_seconds: float,
        daily_cooldown_seconds: float,
        clock=time.monotonic,
        jitter=lambda: random.uniform(0, 5),
    ) -> None:
        self.configured_requests_per_minute = requests_per_minute
        self.effective_requests_per_minute = max(1.0, requests_per_minute * utilization)
        self.max_in_flight = max_in_flight
        self.minute_cooldown_seconds = minute_cooldown_seconds
        self.daily_cooldown_seconds = daily_cooldown_seconds
        self._interval = 60 / self.effective_requests_per_minute
        self._clock = clock
        self._jitter = jitter
        self._lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._waiters: list[tuple[int, int, _Waiter]] = []
        self._sequence = 0
        self._in_flight = 0
        self._next_allowed_at = 0.0
        self._state: Literal["normal", "cooldown", "half_open"] = "normal"
        self._blocked_until: float | None = None
        self._probe_in_flight = False
        self._consecutive_rate_limits = 0
        self._closed = False
        self._dispatcher: asyncio.Task | None = None

    async def start(self) -> None:
        if self._dispatcher is None:
            self._dispatcher = asyncio.create_task(
                self._dispatch_loop(),
                name="alpha-vantage-quota-dispatcher",
            )

    async def close(self) -> None:
        self._closed = True
        async with self._lock:
            for _, _, waiter in self._waiters:
                if not waiter.future.done():
                    waiter.future.set_exception(
                        AlphaBrokerTransientError("Alpha Vantage broker is stopping")
                    )
            self._waiters.clear()
            self._wake.set()
        if self._dispatcher is not None:
            self._dispatcher.cancel()
            with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError):
                await self._dispatcher
            self._dispatcher = None

    async def acquire(self, *, priority: int) -> QuotaLease:
        if self._closed:
            raise AlphaBrokerTransientError("Alpha Vantage broker is stopping")
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        async with self._lock:
            self._sequence += 1
            waiter = _Waiter(priority, self._sequence, self._clock(), future)
            heapq.heappush(self._waiters, (priority, waiter.sequence, waiter))
            self._wake.set()
        return await future

    async def release(
        self,
        lease: QuotaLease,
        *,
        outcome: Literal["success", "rate_limit", "transient"],
        retry_after: float | None = None,
        rate_scope: Literal["minute", "daily"] = "minute",
    ) -> None:
        async with self._lock:
            self._in_flight = max(0, self._in_flight - 1)
            if outcome == "rate_limit":
                self._consecutive_rate_limits += 1
                base = (
                    self.daily_cooldown_seconds
                    if rate_scope == "daily"
                    else self.minute_cooldown_seconds * (2 ** (self._consecutive_rate_limits - 1))
                )
                delay = max(base, retry_after or 0) + max(0.0, float(self._jitter()))
                self._state = "cooldown"
                self._blocked_until = self._clock() + delay
                self._probe_in_flight = False
            elif lease.probe and outcome == "success":
                self._state = "normal"
                self._blocked_until = None
                self._probe_in_flight = False
                self._consecutive_rate_limits = 0
            elif lease.probe:
                self._state = "cooldown"
                self._blocked_until = self._clock() + min(
                    self.minute_cooldown_seconds,
                    30,
                )
                self._probe_in_flight = False
            self._wake.set()

    @property
    def state(self) -> str:
        return self._state

    @property
    def in_flight(self) -> int:
        return self._in_flight

    @property
    def queued(self) -> int:
        return sum(1 for _, _, waiter in self._waiters if not waiter.future.done())

    @property
    def oldest_queued_seconds(self) -> float | None:
        enqueued = [
            waiter.enqueued_at for _, _, waiter in self._waiters if not waiter.future.done()
        ]
        return max(0.0, self._clock() - min(enqueued)) if enqueued else None

    @property
    def blocked_seconds(self) -> float | None:
        if self._blocked_until is None:
            return None
        return max(0.0, self._blocked_until - self._clock())

    async def _dispatch_loop(self) -> None:
        while not self._closed:
            lease_future = None
            probe = False
            delay = None
            async with self._lock:
                self._discard_cancelled_waiters()
                now = self._clock()
                if self._state == "cooldown":
                    blocked_until = self._blocked_until or now
                    if blocked_until <= now:
                        self._state = "half_open"
                        self._blocked_until = None
                    else:
                        delay = blocked_until - now
                if (
                    delay is None
                    and self._waiters
                    and self._in_flight < self.max_in_flight
                    and not (self._state == "half_open" and self._probe_in_flight)
                ):
                    if self._next_allowed_at > now:
                        delay = self._next_allowed_at - now
                    else:
                        _, _, waiter = heapq.heappop(self._waiters)
                        if not waiter.future.done():
                            probe = self._state == "half_open"
                            if probe:
                                self._probe_in_flight = True
                            self._in_flight += 1
                            self._next_allowed_at = now + self._interval
                            lease_future = waiter.future
                if lease_future is None:
                    self._wake.clear()
            if self._closed:
                return
            if lease_future is not None:
                if not lease_future.done():
                    lease_future.set_result(QuotaLease(probe=probe))
                continue
            await self._wait_for_change(delay)

    async def _wait_for_change(self, seconds: float | None) -> None:
        if seconds is None:
            await self._wake.wait()
            return
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._wake.wait(), timeout=max(0.001, seconds))

    def _discard_cancelled_waiters(self) -> None:
        while self._waiters and self._waiters[0][2].future.done():
            heapq.heappop(self._waiters)


@dataclass(frozen=True)
class BrokerResult:
    body: str
    cache_hit: bool


class _ResponseCache:
    def __init__(self, directory: Path, clock=time.time) -> None:
        self.directory = directory
        self.clock = clock

    def get(self, key: str) -> str | None:
        path = self.directory / f"{key}.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if float(payload["expires_at"]) <= self.clock():
                path.unlink(missing_ok=True)
                return None
            body = payload["body"]
            return body if isinstance(body, str) else None
        except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def put(self, key: str, body: str, ttl_seconds: int) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{key}.json"
        temporary = path.with_name(f".{path.name}.{time.time_ns()}.tmp")
        try:
            temporary.write_text(
                json.dumps(
                    {"expires_at": self.clock() + ttl_seconds, "body": body},
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
            temporary.chmod(0o600)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


@dataclass(frozen=True)
class _KeyRuntime:
    api_key: str
    fingerprint: str
    coordinator: GlobalQuotaCoordinator


class AlphaVantageBroker:
    def __init__(
        self,
        api_keys: dict[str, str],
        *,
        requests_per_minute: int,
        utilization: float,
        max_in_flight: int,
        retry_policy: AlphaVantageRetryPolicy,
        minute_cooldown_seconds: float,
        daily_cooldown_seconds: float,
        cache_dir: Path,
        upstream_client: httpx.AsyncClient | None = None,
        jitter=lambda: random.uniform(0, 5),
    ) -> None:
        self._client = upstream_client
        self._owns_client = upstream_client is None
        self._retry_policy = retry_policy
        self._cache = _ResponseCache(cache_dir)
        self._pending: dict[str, asyncio.Task[BrokerResult]] = {}
        self._pending_lock = asyncio.Lock()
        self._authentication_failed = False
        self.requests = 0
        self.upstream_requests = 0
        self.cache_hits = 0
        self.coalesced_requests = 0
        self.rate_limits = 0
        self.transient_errors = 0

        runtimes_by_fingerprint: dict[str, _KeyRuntime] = {}
        self._runtimes: dict[str, _KeyRuntime] = {}
        for consumer, api_key in api_keys.items():
            if not api_key:
                continue
            fingerprint = alpha_key_fingerprint(api_key)
            runtime = runtimes_by_fingerprint.get(fingerprint)
            if runtime is None:
                runtime = _KeyRuntime(
                    api_key=api_key,
                    fingerprint=fingerprint,
                    coordinator=GlobalQuotaCoordinator(
                        requests_per_minute=requests_per_minute,
                        utilization=utilization,
                        max_in_flight=max_in_flight,
                        minute_cooldown_seconds=minute_cooldown_seconds,
                        daily_cooldown_seconds=daily_cooldown_seconds,
                        jitter=jitter,
                    ),
                )
                runtimes_by_fingerprint[fingerprint] = runtime
            self._runtimes[consumer] = runtime
        self._unique_runtimes = tuple(runtimes_by_fingerprint.values())

    async def start(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30)
        await asyncio.gather(*(runtime.coordinator.start() for runtime in self._unique_runtimes))

    async def close(self) -> None:
        await asyncio.gather(*(runtime.coordinator.close() for runtime in self._unique_runtimes))
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def query(
        self,
        function_name: str,
        params: dict,
        consumer: str,
        *,
        run_id: str | None = None,
        analysis_date: str | None = None,
    ) -> BrokerResult:
        del run_id, analysis_date
        self.requests += 1
        if self._authentication_failed:
            raise AlphaBrokerAuthenticationError(
                "Alpha Vantage rejected the configured credentials"
            )
        runtime = self._runtime_for(consumer)
        normalized_params = {
            str(key): value
            for key, value in params.items()
            if str(key).lower() not in {"apikey", "function", "source"}
        }
        key = _request_key(runtime.fingerprint, function_name, normalized_params)
        cached = await asyncio.to_thread(self._cache.get, key)
        if cached is not None:
            self.cache_hits += 1
            return BrokerResult(cached, cache_hit=True)

        async with self._pending_lock:
            task = self._pending.get(key)
            if task is None:
                task = asyncio.create_task(
                    self._fetch(
                        runtime,
                        function_name,
                        normalized_params,
                        key,
                        priority=_priority_for_consumer(consumer),
                    ),
                    name=f"alpha-vantage-{function_name.lower()}",
                )
                self._pending[key] = task
            else:
                self.coalesced_requests += 1
        try:
            return await asyncio.shield(task)
        finally:
            if task.done():
                async with self._pending_lock:
                    if self._pending.get(key) is task:
                        self._pending.pop(key, None)

    def status(self) -> AlphaBrokerStatus:
        if not self._unique_runtimes or self._authentication_failed:
            base = AlphaBrokerStatus.unavailable()
            return base.model_copy(
                update={
                    "requests": self.requests,
                    "upstream_requests": self.upstream_requests,
                    "cache_hits": self.cache_hits,
                    "coalesced_requests": self.coalesced_requests,
                    "rate_limits": self.rate_limits,
                    "transient_errors": self.transient_errors,
                }
            )
        coordinators = tuple(runtime.coordinator for runtime in self._unique_runtimes)
        states = {coordinator.state for coordinator in coordinators}
        if "cooldown" in states:
            state = "cooldown"
        elif "half_open" in states:
            state = "half_open"
        else:
            state = "normal"
        blocked = [value for coordinator in coordinators if (value := coordinator.blocked_seconds)]
        blocked_until = None
        if blocked:
            blocked_until = (
                datetime.now(timezone.utc) + timedelta(seconds=max(blocked))
            ).isoformat()
        oldest = [
            value
            for coordinator in coordinators
            if (value := coordinator.oldest_queued_seconds) is not None
        ]
        return AlphaBrokerStatus(
            status=state,
            configured_requests_per_minute=sum(
                value.configured_requests_per_minute for value in coordinators
            ),
            effective_requests_per_minute=sum(
                value.effective_requests_per_minute for value in coordinators
            ),
            max_in_flight=sum(value.max_in_flight for value in coordinators),
            in_flight=sum(value.in_flight for value in coordinators),
            queued=sum(value.queued for value in coordinators),
            oldest_queued_seconds=max(oldest) if oldest else None,
            blocked_until=blocked_until,
            requests=self.requests,
            upstream_requests=self.upstream_requests,
            cache_hits=self.cache_hits,
            coalesced_requests=self.coalesced_requests,
            rate_limits=self.rate_limits,
            transient_errors=self.transient_errors,
        )

    def _runtime_for(self, consumer: str) -> _KeyRuntime:
        runtime = self._runtimes.get(consumer)
        if runtime is None:
            raise AlphaBrokerAuthenticationError(
                "Alpha Vantage credentials are not configured for this consumer"
            )
        return runtime

    async def _fetch(
        self,
        runtime: _KeyRuntime,
        function_name: str,
        params: dict,
        cache_key: str,
        *,
        priority: int,
    ) -> BrokerResult:
        if self._client is None:
            raise AlphaBrokerTransientError("Alpha Vantage broker is not started")
        for attempt in range(1, self._retry_policy.attempts + 1):
            lease = await runtime.coordinator.acquire(priority=priority)
            self.upstream_requests += 1
            try:
                response = await self._client.get(
                    _ALPHA_VANTAGE_URL,
                    params={
                        **params,
                        "function": function_name,
                        "apikey": runtime.api_key,
                        "source": "trading_agents",
                    },
                )
            except httpx.HTTPError as error:
                self.transient_errors += 1
                await runtime.coordinator.release(lease, outcome="transient")
                if attempt == self._retry_policy.attempts:
                    raise AlphaBrokerTransientError(
                        "Alpha Vantage request failed after coordinated retries"
                    ) from error
                await asyncio.sleep(self._retry_policy.delay(attempt))
                continue

            if response.status_code == 429:
                self.rate_limits += 1
                retry_after = _numeric_retry_after(response.headers.get("Retry-After"))
                await runtime.coordinator.release(
                    lease,
                    outcome="rate_limit",
                    retry_after=retry_after,
                )
                if attempt == self._retry_policy.attempts:
                    raise AlphaBrokerRateLimitError(
                        "Alpha Vantage rate limit persisted after coordinated retries"
                    )
                continue
            if response.status_code >= 500:
                self.transient_errors += 1
                await runtime.coordinator.release(lease, outcome="transient")
                if attempt == self._retry_policy.attempts:
                    raise AlphaBrokerTransientError(
                        "Alpha Vantage service remained unavailable after coordinated retries"
                    )
                await asyncio.sleep(self._retry_policy.delay(attempt))
                continue
            if response.is_error:
                await runtime.coordinator.release(lease, outcome="transient")
                raise AlphaBrokerTransientError("Alpha Vantage rejected the request")

            body = response.text
            payload = _json_payload(body)
            classification = classify_alpha_payload(payload)
            if classification == "authentication":
                self._authentication_failed = True
                await runtime.coordinator.release(lease, outcome="success")
                raise AlphaBrokerAuthenticationError(
                    "Alpha Vantage rejected the configured credentials"
                )
            if classification == "rate_limit":
                scope = _rate_limit_scope(payload)
                self.rate_limits += 1
                await runtime.coordinator.release(
                    lease,
                    outcome="rate_limit",
                    rate_scope=scope,
                )
                if scope == "daily" or attempt == self._retry_policy.attempts:
                    raise AlphaBrokerRateLimitError(
                        "Alpha Vantage quota is unavailable for the current period"
                    )
                continue
            if classification == "transient":
                self.transient_errors += 1
                await runtime.coordinator.release(lease, outcome="transient")
                if attempt == self._retry_policy.attempts:
                    raise AlphaBrokerTransientError(
                        "Alpha Vantage returned transient errors after coordinated retries"
                    )
                await asyncio.sleep(self._retry_policy.delay(attempt))
                continue

            await runtime.coordinator.release(lease, outcome="success")
            await asyncio.to_thread(
                self._cache.put,
                cache_key,
                body,
                _cache_ttl_seconds(function_name),
            )
            return BrokerResult(body, cache_hit=False)
        raise RuntimeError("unreachable Alpha Vantage broker retry state")


def _priority_for_consumer(consumer: str) -> int:
    if consumer == "research":
        return _RESEARCH_PRIORITY
    if consumer == "validation":
        return _VALIDATION_PRIORITY
    return _DEFAULT_PRIORITY


def _request_key(fingerprint: str, function_name: str, params: dict) -> str:
    canonical = json.dumps(
        {
            "key": fingerprint,
            "function": function_name.upper(),
            "params": params,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


def _cache_ttl_seconds(function_name: str) -> int:
    normalized = function_name.upper()
    if normalized in {"OVERVIEW", "BALANCE_SHEET", "CASH_FLOW", "INCOME_STATEMENT"}:
        return 6 * 60 * 60
    if normalized == "NEWS_SENTIMENT":
        return 5 * 60
    return 15 * 60


def _json_payload(body: str) -> object:
    if not body.lstrip().startswith("{"):
        return None
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return None


def _rate_limit_scope(payload: object) -> Literal["minute", "daily"]:
    if not isinstance(payload, dict):
        return "minute"
    message = " ".join(str(value) for value in payload.values()).lower()
    return "daily" if "per day" in message or "requests per day" in message else "minute"


def _numeric_retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if parsed > 0 else None

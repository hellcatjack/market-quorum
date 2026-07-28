from __future__ import annotations

from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

BrokerState = Literal["normal", "cooldown", "half_open", "unavailable"]


class AlphaBrokerStatus(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: BrokerState
    configured_requests_per_minute: int = Field(ge=0)
    effective_requests_per_minute: float = Field(ge=0)
    max_in_flight: int = Field(ge=0)
    in_flight: int = Field(ge=0)
    queued: int = Field(ge=0)
    oldest_queued_seconds: float | None = Field(default=None, ge=0)
    blocked_until: str | None = None
    requests: int = Field(default=0, ge=0)
    upstream_requests: int = Field(default=0, ge=0)
    cache_hits: int = Field(default=0, ge=0)
    coalesced_requests: int = Field(default=0, ge=0)
    rate_limits: int = Field(default=0, ge=0)
    transient_errors: int = Field(default=0, ge=0)

    def admission_allowed(self, *, queue_limit: int) -> bool:
        return self.status == "normal" and self.queued < queue_limit

    @classmethod
    def unavailable(cls) -> AlphaBrokerStatus:
        return cls(
            status="unavailable",
            configured_requests_per_minute=0,
            effective_requests_per_minute=0,
            max_in_flight=0,
            in_flight=0,
            queued=0,
        )


class AlphaBrokerError(RuntimeError):
    code = "broker_error"


class AlphaBrokerRateLimitError(AlphaBrokerError):
    code = "rate_limit"


class AlphaBrokerAuthenticationError(AlphaBrokerError):
    code = "authentication"


class AlphaBrokerTransientError(AlphaBrokerError):
    code = "transient"


def _safe_error(response: httpx.Response) -> AlphaBrokerError:
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    code = str(payload.get("code") or "transient") if isinstance(payload, dict) else "transient"
    message = (
        str(payload.get("message") or "Alpha Vantage broker request failed")
        if isinstance(payload, dict)
        else "Alpha Vantage broker request failed"
    )
    error_type = {
        "rate_limit": AlphaBrokerRateLimitError,
        "daily_rate_limit": AlphaBrokerRateLimitError,
        "authentication": AlphaBrokerAuthenticationError,
        "transient": AlphaBrokerTransientError,
    }.get(code, AlphaBrokerTransientError)
    return error_type(message)


class SyncAlphaVantageBrokerClient:
    def __init__(
        self,
        base_url: str,
        *,
        consumer: str,
        timeout: float = 2100,
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._consumer = consumer
        self._timeout = timeout
        self._client = client

    def __repr__(self) -> str:
        return f"{type(self).__name__}(consumer={self._consumer!r})"

    def query(
        self,
        function_name: str,
        params: dict,
        *,
        run_id: str | None = None,
        analysis_date: str | None = None,
    ) -> str:
        payload = {
            "function": function_name,
            "params": dict(params),
            "run_id": run_id,
            "analysis_date": analysis_date,
        }
        headers = {"X-TradingNG-Consumer": self._consumer}
        try:
            if self._client is None:
                with httpx.Client(timeout=self._timeout) as client:
                    response = client.post(
                        f"{self._base_url}/v1/query",
                        json=payload,
                        headers=headers,
                    )
            else:
                response = self._client.post(
                    f"{self._base_url}/v1/query",
                    json=payload,
                    headers=headers,
                )
        except httpx.HTTPError as error:
            raise AlphaBrokerTransientError("Alpha Vantage broker is unavailable") from error
        if response.is_error:
            raise _safe_error(response)
        try:
            body = response.json()["body"]
        except (KeyError, TypeError, ValueError) as error:
            raise AlphaBrokerTransientError(
                "Alpha Vantage broker returned an invalid response"
            ) from error
        if not isinstance(body, str):
            raise AlphaBrokerTransientError("Alpha Vantage broker returned an invalid response")
        return body


class AsyncAlphaVantageBrokerClient:
    def __init__(
        self,
        base_url: str,
        *,
        consumer: str,
        timeout: float = 2100,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._consumer = consumer
        self._timeout = timeout
        self._client = client

    def __repr__(self) -> str:
        return f"{type(self).__name__}(consumer={self._consumer!r})"

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        try:
            if self._client is None:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    return await client.request(method, f"{self._base_url}{path}", **kwargs)
            return await self._client.request(method, f"{self._base_url}{path}", **kwargs)
        except httpx.HTTPError as error:
            raise AlphaBrokerTransientError("Alpha Vantage broker is unavailable") from error

    async def query(
        self,
        function_name: str,
        params: dict,
        *,
        run_id: str | None = None,
        analysis_date: str | None = None,
    ) -> str:
        response = await self._request(
            "POST",
            "/v1/query",
            json={
                "function": function_name,
                "params": dict(params),
                "run_id": run_id,
                "analysis_date": analysis_date,
            },
            headers={"X-TradingNG-Consumer": self._consumer},
        )
        if response.is_error:
            raise _safe_error(response)
        try:
            body = response.json()["body"]
        except (KeyError, TypeError, ValueError) as error:
            raise AlphaBrokerTransientError(
                "Alpha Vantage broker returned an invalid response"
            ) from error
        if not isinstance(body, str):
            raise AlphaBrokerTransientError("Alpha Vantage broker returned an invalid response")
        return body

    async def status(self) -> AlphaBrokerStatus:
        try:
            response = await self._request("GET", "/v1/status")
            if response.is_error:
                return AlphaBrokerStatus.unavailable()
            return AlphaBrokerStatus.model_validate(response.json())
        except (AlphaBrokerTransientError, ValueError):
            return AlphaBrokerStatus.unavailable()

import time
from typing import Literal

import httpx
from pydantic import BaseModel, Field, ValidationError


class GatewayStatusError(RuntimeError):
    def __init__(
        self,
        error_code: str,
        *,
        latency_ms: int,
        status_code: int | None = None,
    ):
        self.error_code = error_code
        self.latency_ms = latency_ms
        self.status_code = status_code
        suffix = f" status_code={status_code}" if status_code is not None else ""
        super().__init__(f"Gateway status request failed: {error_code}{suffix}")


class GatewaySnapshot(BaseModel):
    status: Literal["ok"]
    active_completions: int = Field(ge=0)
    model: str = Field(min_length=1)
    reasoning_effort: str = Field(min_length=1)
    snapshot_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    latency_ms: int = Field(ge=0)


class GatewayClient:
    def __init__(
        self,
        base_url: str,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 5.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.client = client
        self.timeout_seconds = timeout_seconds

    async def status(self) -> GatewaySnapshot:
        started = time.monotonic()
        try:
            if self.client is None:
                async with httpx.AsyncClient() as client:
                    response = await client.get(
                        f"{self.base_url}/internal/status",
                        timeout=self.timeout_seconds,
                    )
            else:
                response = await self.client.get(
                    f"{self.base_url}/internal/status",
                    timeout=self.timeout_seconds,
                )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            latency_ms = max(0, int((time.monotonic() - started) * 1000))
            status_code = exc.response.status_code
            error_code = (
                "gateway_overload" if status_code in {429, 502, 503, 504} else "gateway_unavailable"
            )
            raise GatewayStatusError(
                error_code,
                latency_ms=latency_ms,
                status_code=status_code,
            ) from exc
        except httpx.HTTPError as exc:
            latency_ms = max(0, int((time.monotonic() - started) * 1000))
            raise GatewayStatusError(
                "gateway_unavailable",
                latency_ms=latency_ms,
            ) from exc

        latency_ms = max(0, int((time.monotonic() - started) * 1000))
        try:
            payload = response.json()
            payload["latency_ms"] = latency_ms
            return GatewaySnapshot.model_validate(payload)
        except (TypeError, ValueError, ValidationError) as exc:
            raise GatewayStatusError(
                "gateway_invalid_status",
                latency_ms=latency_ms,
                status_code=response.status_code,
            ) from exc

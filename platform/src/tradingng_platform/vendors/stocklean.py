from __future__ import annotations

from datetime import date, datetime
from typing import Literal

import httpx
from pydantic import BaseModel, Field, model_validator

STOCKLEAN_RESEARCH_INTAKE_CONTRACT_VERSION = "stocklean.research-intake.v1"


class StockLeanInstrumentIdentity(BaseModel):
    asset_type: Literal["stock", "fund"]
    exchange: str | None = None
    name: str | None = None
    vendor_symbol: str


class StockLeanJobProgress(BaseModel):
    batch_id: int
    stage: str
    completed_items: int
    total_items: int
    last_watermark: str | None = None
    next_retry_at: datetime | None = None
    error_code: str | None = None


class StockLeanManifestRef(BaseModel):
    snapshot_id: str
    manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    captured_at: datetime
    max_observation_date: date | None = None


class StockLeanResearchError(BaseModel):
    code: str
    message: str
    retryable: bool = False
    retry_after: datetime | None = None


class StockLeanResearchCandidateItem(BaseModel):
    external_request_key: str
    candidate_request_id: int | None = None
    candidate_id: int | None = None
    symbol: str
    scope: Literal["production", "research"]
    identity: StockLeanInstrumentIdentity | None = None
    readiness: Literal["ready", "waiting", "rejected"]
    required_products: tuple[str, ...]
    job: StockLeanJobProgress | None = None
    manifest: StockLeanManifestRef | None = None
    error: StockLeanResearchError | None = None

    @model_validator(mode="after")
    def validate_readiness_payload(self):
        if self.readiness == "ready" and self.manifest is None:
            raise ValueError("ready result requires a manifest")
        if self.readiness == "waiting" and self.job is None:
            raise ValueError("waiting result requires job progress")
        if self.readiness == "rejected" and self.error is None:
            raise ValueError("rejected result requires an error")
        return self


class StockLeanResearchCandidateResponse(BaseModel):
    contract_version: Literal["stocklean.research-intake.v1"]
    items: tuple[StockLeanResearchCandidateItem, ...]


class StockLeanDailyPrice(BaseModel):
    session_date: date
    open: str
    high: str
    low: str
    close: str
    adjusted_close: str
    volume: str
    dividend_amount: str
    split_coefficient: str
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class StockLeanDailyPrices(BaseModel):
    contract_version: Literal["stocklean.alpha.v1"]
    symbol: str
    rows: tuple[StockLeanDailyPrice, ...]
    max_observation_date: date | None = None


class StockLeanManifestItem(BaseModel):
    product: str
    symbol: str | None = None
    instrument_id: int | None = None
    source_batch_id: int | None = None
    version_ref: str
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    freshness: str
    max_observation_date: date | None = None
    point_in_time_end: datetime | None = None


class StockLeanManifest(BaseModel):
    contract_version: Literal["stocklean.alpha.v1"]
    snapshot_id: str
    manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    candidate_request_id: int | None = None
    analysis_date: date
    captured_at: datetime
    max_observation_date: date | None = None
    items: tuple[StockLeanManifestItem, ...]


class StockLeanClientError(RuntimeError):
    def __init__(self, code: str, *, status_code: int | None = None):
        self.code = code
        self.status_code = status_code
        super().__init__(code)


class StockLeanClient:
    def __init__(
        self,
        base_url: str,
        *,
        token: str,
        timeout: float = 15.0,
        client: httpx.AsyncClient | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self._token = token.strip()
        if not self._token:
            raise ValueError("StockLean internal token is required")
        self.timeout = timeout
        self._client = client

    def __repr__(self) -> str:
        return f"StockLeanClient(base_url={self.base_url!r}, timeout={self.timeout!r})"

    async def resolve_candidates(self, *, subject_ref: str, items: list[dict]):
        normalized_items = []
        for item in items:
            normalized = dict(item)
            if isinstance(normalized.get("analysis_date"), date):
                normalized["analysis_date"] = normalized["analysis_date"].isoformat()
            normalized_items.append(normalized)
        payload = await self._request(
            "POST",
            "/api/internal/v1/research-candidates/resolve",
            json={"subject_ref": subject_ref, "items": normalized_items},
        )
        return StockLeanResearchCandidateResponse.model_validate(payload)

    async def candidate_status(self, request_id: int) -> StockLeanResearchCandidateItem:
        payload = await self._request(
            "GET", f"/api/internal/v1/research-candidate-requests/{request_id}"
        )
        return StockLeanResearchCandidateItem.model_validate(payload)

    async def instrument(self, symbol: str) -> StockLeanInstrumentIdentity:
        payload = await self._request("GET", f"/api/internal/v1/alpha/instruments/{symbol}")
        return StockLeanInstrumentIdentity.model_validate(payload)

    async def daily_prices(
        self, symbol: str, *, start: date, end: date, limit: int = 5000
    ) -> StockLeanDailyPrices:
        payload = await self._request(
            "GET",
            "/api/internal/v1/alpha/prices/daily",
            params={
                "symbol": symbol,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "limit": limit,
            },
        )
        return StockLeanDailyPrices.model_validate(payload)

    async def manifest(self, snapshot_id: str) -> StockLeanManifest:
        payload = await self._request("GET", f"/api/internal/v1/alpha/manifests/{snapshot_id}")
        return StockLeanManifest.model_validate(payload)

    async def _request(self, method: str, path: str, **kwargs):
        headers = {
            "Authorization": f"Bearer {self._token}",
            "X-Caller-Service": "tradingng",
            "Accept": "application/json",
        }
        try:
            if self._client is not None:
                response = await self._client.request(
                    method,
                    f"{self.base_url}{path}",
                    headers=headers,
                    timeout=self.timeout,
                    **kwargs,
                )
            else:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.request(
                        method,
                        f"{self.base_url}{path}",
                        headers=headers,
                        **kwargs,
                    )
        except httpx.HTTPError as exc:
            raise StockLeanClientError("stocklean_unavailable") from exc
        if response.status_code >= 400:
            code = "stocklean_not_found" if response.status_code == 404 else "stocklean_rejected"
            if response.status_code >= 500:
                code = "stocklean_unavailable"
            raise StockLeanClientError(code, status_code=response.status_code)
        try:
            return response.json()
        except ValueError as exc:
            raise StockLeanClientError("stocklean_invalid_response") from exc


class UnavailableStockLeanClient:
    def __init__(self, reason: str = "stocklean_not_configured"):
        self.reason = reason

    async def resolve_candidates(self, **kwargs):
        raise StockLeanClientError(self.reason)

    async def candidate_status(self, request_id: int):
        raise StockLeanClientError(self.reason)

    async def instrument(self, symbol: str):
        raise StockLeanClientError(self.reason)

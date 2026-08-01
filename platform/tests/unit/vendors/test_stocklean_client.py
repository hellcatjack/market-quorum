import json
from datetime import date

import httpx

from tradingng_platform.vendors.stocklean import (
    StockLeanClient,
    StockLeanResearchCandidateResponse,
)


async def test_stocklean_client_resolves_and_polls_candidates():
    calls = []

    def handler(request: httpx.Request):
        calls.append(request)
        payload = json.loads(request.content) if request.content else None
        if request.url.path.endswith("/resolve"):
            assert payload["items"][0]["symbol"] == "XYZ"
            body = {
                "contract_version": "stocklean.research-intake.v1",
                "items": [_candidate_payload("queued")],
            }
            return httpx.Response(202, json=body)
        return httpx.Response(200, json=_candidate_payload("loading_market_history"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = StockLeanClient("http://stocklean.test", token="private-token", client=http)
        response = await client.resolve_candidates(
            subject_ref="user:test",
            items=[
                {
                    "external_request_key": "run:XYZ",
                    "symbol": "XYZ",
                    "analysis_date": date(2026, 7, 31),
                    "analysts": ["market"],
                    "required_products": ["market"],
                }
            ],
        )
        candidate = await client.candidate_status(42)

    assert isinstance(response, StockLeanResearchCandidateResponse)
    assert response.items[0].readiness == "waiting"
    assert candidate.job.stage == "loading_market_history"
    assert all(request.headers["Authorization"] == "Bearer private-token" for request in calls)
    assert "private-token" not in repr(client)


async def test_stocklean_client_reads_prices_and_manifest():
    def handler(request: httpx.Request):
        if request.url.path.endswith("/prices/daily"):
            return httpx.Response(
                200,
                json={
                    "contract_version": "stocklean.alpha.v1",
                    "symbol": "XYZ",
                    "rows": [
                        {
                            "session_date": "2026-07-31",
                            "open": "10",
                            "high": "12",
                            "low": "9",
                            "close": "11",
                            "adjusted_close": "10.5",
                            "volume": "100",
                            "dividend_amount": "0",
                            "split_coefficient": "1",
                            "content_sha256": "a" * 64,
                        }
                    ],
                    "max_observation_date": "2026-07-31",
                },
            )
        return httpx.Response(
            200,
            json={
                "contract_version": "stocklean.alpha.v1",
                "snapshot_id": "snap-1",
                "manifest_sha256": "b" * 64,
                "analysis_date": "2026-07-31",
                "captured_at": "2026-08-01T00:00:00Z",
                "max_observation_date": "2026-07-31",
                "items": [],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = StockLeanClient("http://stocklean.test", token="token", client=http)
        prices = await client.daily_prices("XYZ", start=date(2026, 7, 1), end=date(2026, 7, 31))
        manifest = await client.manifest("snap-1")

    assert prices.rows[0].adjusted_close == "10.5"
    assert manifest.manifest_sha256 == "b" * 64


def _candidate_payload(stage):
    return {
        "external_request_key": "run:XYZ",
        "candidate_request_id": 42,
        "candidate_id": 7,
        "symbol": "XYZ",
        "scope": "research",
        "identity": {
            "asset_type": "stock",
            "exchange": "NASDAQ",
            "name": "Example",
            "vendor_symbol": "XYZ",
        },
        "readiness": "waiting",
        "required_products": ["market"],
        "job": {
            "batch_id": 4,
            "stage": stage,
            "completed_items": 0,
            "total_items": 1,
        },
    }

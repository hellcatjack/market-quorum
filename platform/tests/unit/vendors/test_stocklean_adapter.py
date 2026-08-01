import httpx

from tradingng_platform.vendors.stocklean_adapter import StockLeanResearchAdapter


def _handler(request: httpx.Request):
    if request.url.path.endswith("/prices/daily"):
        return httpx.Response(
            200,
            json={
                "contract_version": "stocklean.alpha.v1",
                "symbol": "XYZ",
                "rows": [
                    {
                        "session_date": f"2026-07-{day:02d}",
                        "open": str(10 + day),
                        "high": str(12 + day),
                        "low": str(9 + day),
                        "close": str(11 + day),
                        "adjusted_close": str(10.5 + day),
                        "volume": str(100 + day),
                        "dividend_amount": "0",
                        "split_coefficient": "1",
                        "content_sha256": "a" * 64,
                    }
                    for day in range(1, 32)
                ],
                "max_observation_date": "2026-07-31",
            },
        )
    if "/documents/" in request.url.path:
        return httpx.Response(
            200,
            json={
                "contract_version": "stocklean.alpha.v1",
                "symbol": "XYZ",
                "items": [
                    {
                        "function": request.url.params["functions"],
                        "payload": {"Symbol": "XYZ", "annualReports": []},
                        "content_sha256": "b" * 64,
                        "schema_status": "valid",
                        "captured_at": "2026-07-31T20:00:00Z",
                        "available_at": "2026-07-31T20:00:00Z",
                    }
                ],
            },
        )
    return httpx.Response(
        200,
        json={
            "contract_version": "stocklean.alpha.v1",
            "items": [{"title": "Example news", "time_published": "2026-07-31T12:00:00Z"}],
        },
    )


def test_adapter_formats_stocklean_prices_for_tradingagents_without_network_fallback():
    with httpx.Client(transport=httpx.MockTransport(_handler)) as client:
        adapter = StockLeanResearchAdapter(
            "http://stocklean.test",
            token="internal-token",
            snapshot_id="snap-1",
            client=client,
        )
        result = adapter.get_stock("XYZ", "2026-07-01", "2026-07-31")

    assert result.splitlines()[0].startswith("timestamp,open,high,low,close")
    assert "2026-07-31" in result
    assert "snap-1" not in result


def test_adapter_reads_fundamentals_and_computes_indicators_locally():
    with httpx.Client(transport=httpx.MockTransport(_handler)) as client:
        adapter = StockLeanResearchAdapter(
            "http://stocklean.test",
            token="internal-token",
            snapshot_id="snap-1",
            client=client,
        )
        overview = adapter.get_fundamentals("XYZ", "2026-07-31")
        indicator = adapter.get_indicator("XYZ", "close_10_ema", "2026-07-31", 30)

    assert '"Symbol": "XYZ"' in overview
    assert "CLOSE_10_EMA" in indicator
    assert "2026-07-31" in indicator

from __future__ import annotations

import csv
import json
from datetime import date, datetime, timedelta
from io import StringIO

import httpx
import pandas as pd

from tradingng_platform.vendors.stocklean import StockLeanClientError


class StockLeanResearchAdapter:
    def __init__(
        self,
        base_url: str,
        *,
        token: str,
        snapshot_id: str,
        timeout: float = 30.0,
        client: httpx.Client | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self._token = token.strip()
        if not self._token:
            raise ValueError("StockLean internal token is required")
        if not snapshot_id:
            raise ValueError("StockLean manifest snapshot is required")
        self.snapshot_id = snapshot_id
        self.timeout = timeout
        self._client = client

    def __repr__(self) -> str:
        return (
            f"StockLeanResearchAdapter(base_url={self.base_url!r}, "
            f"snapshot_id={self.snapshot_id!r})"
        )

    def get_stock(self, symbol: str, start_date: str, end_date: str) -> str:
        payload = self._get(
            "/api/internal/v1/alpha/prices/daily",
            params={
                "symbol": symbol,
                "start": start_date,
                "end": end_date,
                "snapshot_id": self.snapshot_id,
                "limit": 5000,
            },
        )
        output = StringIO()
        fields = (
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "adjusted_close",
            "volume",
            "dividend_amount",
            "split_coefficient",
        )
        writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in payload.get("rows", ()):
            writer.writerow(
                {
                    "timestamp": row["session_date"],
                    **{field: row[field] for field in fields if field != "timestamp"},
                }
            )
        return output.getvalue()

    def get_fundamentals(self, ticker: str, curr_date: str | None = None) -> str:
        return self._document(ticker, "OVERVIEW", curr_date)

    def get_balance_sheet(
        self, ticker: str, freq: str = "quarterly", curr_date: str | None = None
    ) -> str:
        return self._document(ticker, "BALANCE_SHEET", curr_date)

    def get_cashflow(
        self, ticker: str, freq: str = "quarterly", curr_date: str | None = None
    ) -> str:
        return self._document(ticker, "CASH_FLOW", curr_date)

    def get_income_statement(
        self, ticker: str, freq: str = "quarterly", curr_date: str | None = None
    ) -> str:
        return self._document(ticker, "INCOME_STATEMENT", curr_date)

    def get_earnings(self, ticker: str, curr_date: str | None = None) -> str:
        return self._document(ticker, "EARNINGS", curr_date)

    def get_insider_transactions(self, symbol: str) -> str:
        return self._document(symbol, "INSIDER_TRANSACTIONS", None)

    def get_news(self, ticker: str, start_date: str, end_date: str) -> str:
        payload = self._get(
            "/api/internal/v1/alpha/news",
            params={
                "symbol": ticker,
                "start": f"{start_date}T00:00:00",
                "end": f"{end_date}T23:59:59",
                "snapshot_id": self.snapshot_id,
                "limit": 500,
            },
        )
        return json.dumps({"feed": payload.get("items", [])}, ensure_ascii=False)

    def get_global_news(self, curr_date: str, look_back_days: int = 7, limit: int = 50) -> str:
        end = date.fromisoformat(curr_date)
        start = end - timedelta(days=look_back_days)
        payload = self._get(
            "/api/internal/v1/alpha/news",
            params={
                "start": f"{start.isoformat()}T00:00:00",
                "end": f"{end.isoformat()}T23:59:59",
                "snapshot_id": self.snapshot_id,
                "limit": min(500, max(1, int(limit))),
            },
        )
        return json.dumps({"feed": payload.get("items", [])}, ensure_ascii=False)

    def get_indicator(
        self,
        symbol: str,
        indicator: str,
        curr_date: str,
        look_back_days: int,
        interval: str = "daily",
        time_period: int = 14,
        series_type: str = "close",
    ) -> str:
        end = date.fromisoformat(curr_date)
        requested_start = end - timedelta(days=look_back_days)
        history_start = min(requested_start, end - timedelta(days=400))
        csv_payload = self.get_stock(symbol, history_start.isoformat(), end.isoformat())
        frame = pd.read_csv(StringIO(csv_payload))
        if frame.empty:
            raise StockLeanClientError("stocklean_market_data_empty")
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="coerce")
        for column in ("open", "high", "low", "close", "adjusted_close", "volume"):
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame = frame.dropna(subset=["timestamp", "adjusted_close"]).sort_values("timestamp")
        close = frame["adjusted_close"]
        values = self._indicator_series(frame, close, indicator, time_period)
        visible = frame.loc[
            frame["timestamp"] >= pd.Timestamp(requested_start), ["timestamp"]
        ].copy()
        visible["value"] = values.loc[visible.index]
        lines = [
            f"{row.timestamp:%Y-%m-%d}: {row.value:.6f}"
            for row in visible.itertuples()
            if pd.notna(row.value)
        ]
        body = "\n".join(lines) or "No data available for the specified date range."
        return (
            f"## {indicator.upper()} values from {requested_start.isoformat()} "
            f"to {curr_date}:\n\n{body}\n"
        )

    @staticmethod
    def _indicator_series(frame, close, indicator: str, time_period: int):
        if indicator == "close_50_sma":
            return close.rolling(50).mean()
        if indicator == "close_200_sma":
            return close.rolling(200).mean()
        if indicator == "close_10_ema":
            return close.ewm(span=10, adjust=False).mean()
        if indicator in {"macd", "macds", "macdh"}:
            macd = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
            signal = macd.ewm(span=9, adjust=False).mean()
            return {"macd": macd, "macds": signal, "macdh": macd - signal}[indicator]
        if indicator == "rsi":
            delta = close.diff()
            gain = delta.clip(lower=0).ewm(alpha=1 / time_period, adjust=False).mean()
            loss = (-delta.clip(upper=0)).ewm(alpha=1 / time_period, adjust=False).mean()
            return 100 - (100 / (1 + gain / loss.replace(0, float("nan"))))
        if indicator in {"boll", "boll_ub", "boll_lb"}:
            middle = close.rolling(20).mean()
            deviation = close.rolling(20).std(ddof=0)
            return {
                "boll": middle,
                "boll_ub": middle + 2 * deviation,
                "boll_lb": middle - 2 * deviation,
            }[indicator]
        if indicator == "atr":
            previous = close.shift(1)
            true_range = pd.concat(
                [
                    frame["high"] - frame["low"],
                    (frame["high"] - previous).abs(),
                    (frame["low"] - previous).abs(),
                ],
                axis=1,
            ).max(axis=1)
            return true_range.ewm(alpha=1 / time_period, adjust=False).mean()
        if indicator == "vwma":
            weighted = close * frame["volume"]
            return weighted.rolling(time_period).sum() / frame["volume"].rolling(time_period).sum()
        raise ValueError(f"unsupported indicator: {indicator}")

    def _document(self, ticker: str, function: str, curr_date: str | None) -> str:
        as_of = f"{curr_date}T23:59:59" if curr_date else datetime.utcnow().isoformat()
        payload = self._get(
            f"/api/internal/v1/alpha/documents/{ticker}",
            params={
                "functions": function,
                "as_of": as_of,
                "snapshot_id": self.snapshot_id,
            },
        )
        items = payload.get("items") or []
        if not items:
            raise StockLeanClientError("stocklean_document_unavailable")
        return json.dumps(items[0]["payload"], ensure_ascii=False)

    def _get(self, path: str, *, params: dict):
        headers = {
            "Authorization": f"Bearer {self._token}",
            "X-Caller-Service": "tradingng",
            "Accept": "application/json",
        }
        try:
            if self._client is not None:
                response = self._client.get(
                    f"{self.base_url}{path}",
                    params=params,
                    headers=headers,
                    timeout=self.timeout,
                )
            else:
                with httpx.Client(timeout=self.timeout) as client:
                    response = client.get(f"{self.base_url}{path}", params=params, headers=headers)
        except httpx.HTTPError as exc:
            raise StockLeanClientError("stocklean_unavailable") from exc
        if response.status_code >= 400:
            raise StockLeanClientError("stocklean_read_rejected", status_code=response.status_code)
        try:
            return response.json()
        except ValueError as exc:
            raise StockLeanClientError("stocklean_invalid_response") from exc

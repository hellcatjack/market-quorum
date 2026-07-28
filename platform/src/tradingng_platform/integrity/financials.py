from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal, Protocol

import httpx

from tradingng_platform.integrity.contracts import IntegrityFinding, IntegrityStatus

_SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_SEC_SUBMISSIONS_BASE = "https://data.sec.gov/submissions"
_ANNUAL_FORMS = frozenset({"10-K", "20-F", "40-F"})
_QUARTERLY_FORMS = frozenset({"10-Q", "6-K"})
_EMPTY_NOTICE = "Historical statement data was unavailable under point-in-time.v1."


@dataclass(frozen=True)
class Availability:
    available_at: date
    source: Literal["sec", "alpha_vantage_earnings"]
    assurance: Literal["high", "medium"]


class FilingAvailabilityResolver(Protocol):
    def resolve(
        self,
        ticker: str,
        fiscal_end: date,
        frequency: str,
    ) -> Availability | None: ...


class SecFilingClient:
    def __init__(
        self,
        *,
        client: httpx.Client,
        user_agent: str,
        cache_dir: Path,
        timeout_seconds: float = 10,
    ):
        if not user_agent.strip():
            raise ValueError("SEC User-Agent cannot be empty")
        self.client = client
        self.user_agent = user_agent.strip()
        self.cache_dir = cache_dir
        self.timeout_seconds = timeout_seconds
        self._ticker_map: dict[str, tuple[str, ...]] | None = None
        self._resolved: dict[tuple[str, date, str], Availability | None] = {}

    def resolve(
        self,
        ticker: str,
        fiscal_end: date,
        frequency: str,
    ) -> Availability | None:
        key = (ticker.upper(), fiscal_end, frequency)
        if key in self._resolved:
            return self._resolved[key]
        cik = self._unique_cik(ticker)
        if cik is None:
            self._resolved[key] = None
            return None

        forms = _ANNUAL_FORMS if frequency == "annual" else _QUARTERLY_FORMS
        filing_dates = []
        for row in self._filing_rows(cik):
            if row.get("form") not in forms or row.get("reportDate") != fiscal_end.isoformat():
                continue
            filing_date = _parse_date(row.get("filingDate"))
            if filing_date is not None:
                filing_dates.append(filing_date)
        result = (
            Availability(min(filing_dates), "sec", "high") if filing_dates else None
        )
        self._resolved[key] = result
        return result

    def _unique_cik(self, ticker: str) -> str | None:
        mapping = self._ticker_mapping()
        matches = mapping.get(ticker.upper(), ())
        return matches[0] if len(matches) == 1 else None

    def _ticker_mapping(self) -> dict[str, tuple[str, ...]]:
        if self._ticker_map is not None:
            return self._ticker_map
        payload = self._fetch_json(_SEC_TICKERS_URL)
        mutable: dict[str, list[str]] = {}
        if isinstance(payload, dict):
            for item in payload.values():
                if not isinstance(item, dict):
                    continue
                ticker = str(item.get("ticker") or "").upper()
                raw_cik = item.get("cik_str")
                try:
                    cik = f"{int(raw_cik):010d}"
                except (TypeError, ValueError):
                    continue
                if ticker:
                    mutable.setdefault(ticker, []).append(cik)
        self._ticker_map = {
            ticker: tuple(dict.fromkeys(ciks)) for ticker, ciks in mutable.items()
        }
        return self._ticker_map

    def _filing_rows(self, cik: str) -> list[dict]:
        payload = self._fetch_json(f"{_SEC_SUBMISSIONS_BASE}/CIK{cik}.json")
        if not isinstance(payload, dict):
            return []
        filings = payload.get("filings")
        if not isinstance(filings, dict):
            return []
        rows = _columnar_rows(filings.get("recent"))
        files = filings.get("files")
        if isinstance(files, list):
            for item in files:
                name = item.get("name") if isinstance(item, dict) else None
                if isinstance(name, str) and name.endswith(".json"):
                    rows.extend(_columnar_rows(self._fetch_json(f"{_SEC_SUBMISSIONS_BASE}/{name}")))
        return rows

    def _fetch_json(self, url: str):
        cache_path = self.cache_dir / f"{hashlib.sha256(url.encode()).hexdigest()}.json"
        if cache_path.is_file():
            try:
                return json.loads(cache_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
        response = self.client.get(
            url,
            headers={"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(payload, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        return payload


class AlphaEarningsAvailabilityResolver:
    def __init__(self, loader):
        self.loader = loader
        self._payload_by_ticker: dict[str, dict | None] = {}

    def resolve(
        self,
        ticker: str,
        fiscal_end: date,
        frequency: str,
    ) -> Availability | None:
        payload = self._load(ticker)
        if payload is None:
            return None
        key = "annualEarnings" if frequency == "annual" else "quarterlyEarnings"
        rows = payload.get(key)
        if not isinstance(rows, list):
            return None
        matches = []
        for row in rows:
            if not isinstance(row, dict) or row.get("fiscalDateEnding") != fiscal_end.isoformat():
                continue
            reported_date = _parse_date(row.get("reportedDate"))
            if reported_date is not None and reported_date >= fiscal_end:
                matches.append(reported_date)
        if len(matches) != 1:
            return None
        return Availability(matches[0], "alpha_vantage_earnings", "medium")

    def _load(self, ticker: str) -> dict | None:
        normalized = ticker.upper()
        if normalized in self._payload_by_ticker:
            return self._payload_by_ticker[normalized]
        try:
            raw = self.loader(normalized)
            payload = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = None
        resolved = payload if isinstance(payload, dict) else None
        self._payload_by_ticker[normalized] = resolved
        return resolved


class CompositeAvailabilityResolver:
    def __init__(self, *resolvers: FilingAvailabilityResolver):
        self.resolvers = resolvers

    def resolve(
        self,
        ticker: str,
        fiscal_end: date,
        frequency: str,
    ) -> Availability | None:
        for resolver in self.resolvers:
            try:
                result = resolver.resolve(ticker, fiscal_end, frequency)
            except (httpx.HTTPError, OSError, RuntimeError, ValueError):
                continue
            if result is not None:
                return result
        return None


def filter_statement_payload(
    payload_text: str,
    *,
    ticker: str,
    analysis_date: date,
    statement_kind: str,
    resolver: FilingAvailabilityResolver,
) -> tuple[str, tuple[IntegrityFinding, ...]]:
    try:
        payload = json.loads(payload_text)
    except (TypeError, json.JSONDecodeError):
        return _empty_payload(ticker), (
            _filtered_finding(statement_kind, "unknown_schema_filtered"),
        )
    if not isinstance(payload, dict):
        return _empty_payload(ticker), (
            _filtered_finding(statement_kind, "unknown_schema_filtered"),
        )

    result = dict(payload)
    findings: list[IntegrityFinding] = []
    observed = 0
    for report_key, frequency in (
        ("annualReports", "annual"),
        ("quarterlyReports", "quarterly"),
    ):
        source_rows = payload.get(report_key)
        if not isinstance(source_rows, list):
            result[report_key] = []
            continue
        retained = []
        for row in source_rows:
            observed += 1
            if not isinstance(row, dict):
                findings.append(_filtered_finding(statement_kind, "unknown_schema_filtered"))
                continue
            fiscal_end = _parse_date(row.get("fiscalDateEnding"))
            if fiscal_end is None:
                findings.append(
                    _filtered_finding(statement_kind, "invalid_fiscal_date_filtered")
                )
                continue
            availability = resolver.resolve(ticker, fiscal_end, frequency)
            details = {
                "frequency": frequency,
                "fiscal_date_ending": fiscal_end.isoformat(),
            }
            if availability is None:
                findings.append(
                    _filtered_finding(
                        statement_kind,
                        "publication_unverified_filtered",
                        details,
                    )
                )
                continue
            details.update(
                {
                    "available_at": availability.available_at.isoformat(),
                    "availability_source": availability.source,
                    "assurance": availability.assurance,
                }
            )
            if availability.available_at > analysis_date:
                findings.append(
                    _filtered_finding(
                        statement_kind,
                        "future_publication_filtered",
                        details,
                    )
                )
                continue
            retained.append(row)
            findings.append(
                IntegrityFinding(
                    tool_name=statement_kind,
                    status=IntegrityStatus.SAFE,
                    reason_code="publication_verified",
                    details=details,
                )
            )
        result[report_key] = retained
    if observed == 0:
        findings.append(
            IntegrityFinding(
                tool_name=statement_kind,
                status=IntegrityStatus.SAFE,
                reason_code="no_statement_records",
            )
        )
    if not result.get("annualReports") and not result.get("quarterlyReports"):
        result["integrityNotice"] = _EMPTY_NOTICE
    return json.dumps(result, sort_keys=True), tuple(findings)


def _empty_payload(ticker: str) -> str:
    return json.dumps(
        {
            "symbol": ticker,
            "annualReports": [],
            "quarterlyReports": [],
            "integrityNotice": _EMPTY_NOTICE,
        },
        sort_keys=True,
    )


def _filtered_finding(
    tool_name: str,
    reason_code: str,
    details: dict | None = None,
) -> IntegrityFinding:
    return IntegrityFinding(
        tool_name=tool_name,
        status=IntegrityStatus.SAFE,
        reason_code=reason_code,
        details=dict(details or {}),
    )


def _parse_date(value) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _columnar_rows(value) -> list[dict]:
    if not isinstance(value, dict):
        return []
    columns = {
        key: items
        for key, items in value.items()
        if isinstance(key, str) and isinstance(items, list)
    }
    if not columns:
        return []
    size = max(len(items) for items in columns.values())
    return [
        {key: items[index] for key, items in columns.items() if index < len(items)}
        for index in range(size)
    ]

import json
from datetime import date

from tradingng_platform.integrity.contracts import IntegrityStatus
from tradingng_platform.integrity.financials import (
    AlphaEarningsAvailabilityResolver,
    Availability,
    CompositeAvailabilityResolver,
    SecFilingClient,
    filter_statement_payload,
)

PAYLOAD_WITH_2025_Q2 = json.dumps(
    {
        "symbol": "NVDA",
        "annualReports": [],
        "quarterlyReports": [
            {
                "fiscalDateEnding": "2025-06-30",
                "reportedCurrency": "USD",
                "totalRevenue": "100",
            }
        ],
    }
)


class StubResolver:
    def __init__(self, values):
        self.values = values

    def resolve(self, ticker: str, fiscal_end: date, frequency: str):
        return self.values.get((ticker, fiscal_end, frequency)) or self.values.get(fiscal_end)


class FakeResponse:
    def __init__(self, payload, *, status_code=200, headers=None):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class FakeHttpClient:
    def __init__(self, responses):
        self.responses = responses
        self.requests = []

    def get(self, url, *, headers, timeout):
        self.requests.append((url, dict(headers), timeout))
        return FakeResponse(self.responses[url])


def test_statement_after_analysis_date_is_removed():
    resolver = StubResolver({date(2025, 6, 30): Availability(date(2025, 7, 24), "sec", "high")})

    result, findings = filter_statement_payload(
        PAYLOAD_WITH_2025_Q2,
        ticker="NVDA",
        analysis_date=date(2025, 7, 1),
        statement_kind="get_income_statement",
        resolver=resolver,
    )

    assert json.loads(result)["quarterlyReports"] == []
    assert findings[0].status is IntegrityStatus.SAFE
    assert findings[0].reason_code == "future_publication_filtered"
    assert findings[0].details["available_at"] == "2025-07-24"


def test_statement_available_on_analysis_date_is_retained():
    resolver = StubResolver({date(2025, 6, 30): Availability(date(2025, 7, 24), "sec", "high")})

    result, findings = filter_statement_payload(
        PAYLOAD_WITH_2025_Q2,
        ticker="NVDA",
        analysis_date=date(2025, 7, 24),
        statement_kind="get_income_statement",
        resolver=resolver,
    )

    assert len(json.loads(result)["quarterlyReports"]) == 1
    assert findings[0].status is IntegrityStatus.SAFE
    assert findings[0].reason_code == "publication_verified"


def test_missing_availability_is_removed_instead_of_using_fiscal_end():
    result, findings = filter_statement_payload(
        PAYLOAD_WITH_2025_Q2,
        ticker="NVDA",
        analysis_date=date(2025, 9, 1),
        statement_kind="get_cashflow",
        resolver=StubResolver({}),
    )

    assert json.loads(result)["quarterlyReports"] == []
    assert findings[0].status is IntegrityStatus.SAFE
    assert findings[0].reason_code == "publication_unverified_filtered"


def test_invalid_statement_schema_fails_closed():
    result, findings = filter_statement_payload(
        "not-json",
        ticker="NVDA",
        analysis_date=date(2025, 9, 1),
        statement_kind="get_balance_sheet",
        resolver=StubResolver({}),
    )

    assert json.loads(result) == {
        "annualReports": [],
        "integrityNotice": "Historical statement data was unavailable under point-in-time.v1.",
        "quarterlyReports": [],
        "symbol": "NVDA",
    }
    assert findings[0].reason_code == "unknown_schema_filtered"


def test_sec_filing_client_maps_unique_ticker_and_uses_original_filing(tmp_path):
    ticker_url = "https://www.sec.gov/files/company_tickers.json"
    submissions_url = "https://data.sec.gov/submissions/CIK0001045810.json"
    client = FakeHttpClient(
        {
            ticker_url: {"0": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"}},
            submissions_url: {
                "filings": {
                    "recent": {
                        "form": ["10-Q/A", "10-Q"],
                        "filingDate": ["2025-07-28", "2025-07-24"],
                        "reportDate": ["2025-06-30", "2025-06-30"],
                    },
                    "files": [],
                }
            },
        }
    )
    resolver = SecFilingClient(
        client=client,
        user_agent="MarketQuorum test@example.invalid",
        cache_dir=tmp_path,
    )

    availability = resolver.resolve("nvda", date(2025, 6, 30), "quarterly")

    assert availability == Availability(date(2025, 7, 24), "sec", "high")
    assert all(
        request[1]["User-Agent"] == "MarketQuorum test@example.invalid"
        for request in client.requests
    )


def test_sec_filing_client_rejects_ambiguous_ticker_mapping(tmp_path):
    ticker_url = "https://www.sec.gov/files/company_tickers.json"
    client = FakeHttpClient(
        {
            ticker_url: {
                "0": {"cik_str": 1, "ticker": "DUPE", "title": "FIRST"},
                "1": {"cik_str": 2, "ticker": "DUPE", "title": "SECOND"},
            }
        }
    )
    resolver = SecFilingClient(
        client=client,
        user_agent="MarketQuorum test@example.invalid",
        cache_dir=tmp_path,
    )

    assert resolver.resolve("DUPE", date(2025, 6, 30), "quarterly") is None
    assert len(client.requests) == 1


def test_alpha_earnings_fallback_accepts_one_valid_reported_date():
    payload = json.dumps(
        {
            "quarterlyEarnings": [
                {
                    "fiscalDateEnding": "2025-06-30",
                    "reportedDate": "2025-07-23",
                    "reportedEPS": "1.00",
                }
            ]
        }
    )
    resolver = AlphaEarningsAvailabilityResolver(lambda ticker: payload)

    assert resolver.resolve("NVDA", date(2025, 6, 30), "quarterly") == Availability(
        date(2025, 7, 23),
        "alpha_vantage_earnings",
        "medium",
    )


def test_alpha_earnings_fallback_rejects_ambiguous_matches():
    row = {"fiscalDateEnding": "2025-06-30", "reportedDate": "2025-07-23"}
    resolver = AlphaEarningsAvailabilityResolver(
        lambda ticker: json.dumps({"quarterlyEarnings": [row, row]})
    )

    assert resolver.resolve("NVDA", date(2025, 6, 30), "quarterly") is None


def test_composite_resolver_prefers_sec_over_alpha():
    fiscal_end = date(2025, 6, 30)
    primary = StubResolver({fiscal_end: Availability(date(2025, 7, 24), "sec", "high")})
    fallback = StubResolver(
        {fiscal_end: Availability(date(2025, 7, 23), "alpha_vantage_earnings", "medium")}
    )

    resolver = CompositeAvailabilityResolver(primary, fallback)

    assert resolver.resolve("NVDA", fiscal_end, "quarterly").source == "sec"

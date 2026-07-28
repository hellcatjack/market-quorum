# SEC Official Instrument Names Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace third-party localized instrument names with complete SEC EDGAR registered names, retain auditable provenance, refresh them safely, and display them without abbreviation across the platform.

**Architecture:** Keep the compatible `Instrument.name` API field but change its semantics to an SEC-verified official name. Replace the EastMoney provider with an async SEC identity provider backed by bounded persistent caches, then extend the existing enrichment store to refresh existing names, archive old provenance, classify failures, and emit audit events. Reuse the current scheduler, SEC User-Agent configuration, API contracts, and two-line instrument display.

**Tech Stack:** Python 3.10, FastAPI/Pydantic, SQLAlchemy async ORM, httpx, MySQL, React 18, TypeScript, Vitest, pytest.

**Execution constraint:** Work directly on `main`; do not create a git worktree and do not modify `TradingAgents/`.

---

## File map

- Modify `platform/src/tradingng_platform/instruments/names.py`: SEC resolver, cache, retry taxonomy, refresh scheduling, provenance history, and audit writes.
- Modify `platform/src/tradingng_platform/scheduler/main.py`: pass SEC configuration into the enrichment task.
- Create `platform/src/tradingng_platform/instruments/backfill.py`: bounded one-shot production backfill entry point.
- Modify `platform/pyproject.toml`: register the backfill command.
- Modify `platform/src/tradingng_platform/assessments/contracts.py`: document `instrument_name` semantics.
- Modify `platform/src/tradingng_platform/records/contracts.py`: document `InstrumentIdentityView.name` semantics.
- Modify `platform/src/tradingng_platform/records/service.py`: expose official name and exchange on the instrument summary.
- Modify `platform/src/tradingng_platform/system/service.py`: expose aggregate official-name resolution health.
- Rewrite `platform/tests/unit/instruments/test_names.py`: SEC provider, ambiguity, caching, and failure classification.
- Modify `platform/tests/integration/test_instrument_names.py`: persistence, history, refresh preservation, and audit tests.
- Modify `platform/tests/operations/test_deploy_config.py`: assert the backfill command and SEC configuration remain deployable.
- Modify `web/src/features/dashboard/InstrumentLedgerTable.tsx`: expose the full official name accessibly.
- Modify `web/src/features/dashboard/RunTable.tsx`: keep the official name complete in task rows.
- Modify `web/src/features/dashboard/DashboardPage.test.tsx`: use official SEC fixture names.
- Modify `web/src/features/instruments/InstrumentHistoryPage.tsx`: show the official name in the instrument header.
- Modify `web/src/features/instruments/InstrumentHistoryPage.test.tsx`: verify history-page official-name presentation.
- Modify `web/src/features/runs/RunDetailPage.tsx`: show the official name in the assessment header.
- Modify `web/src/features/runs/RunDetailPage.test.tsx`: verify detail-page official-name presentation.
- Modify `web/src/features/system/SystemPage.tsx`: show official-name resolution counts.
- Modify `web/src/features/system/SystemPage.test.tsx`: verify resolution health presentation.
- Modify `web/src/styles/global.css`: widen identity space and permit complete-name wrapping.
- Modify `web/src/i18n/messages.ts`: add “官方名称 / Official name”.
- Modify `README.md` and `README.zh-CN.md`: document the official-name source and fallback semantics.

### Task 1: SEC identity provider and failure contract

**Files:**
- Modify: `platform/src/tradingng_platform/instruments/names.py`
- Test: `platform/tests/unit/instruments/test_names.py`

- [ ] **Step 1: Replace EastMoney provider tests with failing SEC fixtures**

Add exact fixtures for PG, PLD, and NVDA plus failure cases:

```python
SEC_TICKERS = {
    "0": {"cik_str": 80424, "ticker": "PG", "title": "PROCTER & GAMBLE Co"},
    "1": {"cik_str": 1045609, "ticker": "PLD", "title": "Prologis, Inc."},
    "2": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"},
}

def submission(name: str, ticker: str, exchange: str) -> dict:
    return {"name": name, "tickers": [ticker], "exchanges": [exchange]}

def fixture_client(index: dict, submissions: dict[str, dict]) -> httpx.AsyncClient:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/files/company_tickers.json":
            return httpx.Response(200, json=index)
        cik = request.url.path.removeprefix("/submissions/CIK").removesuffix(".json")
        payload = submissions.get(cik)
        return httpx.Response(200, json=payload) if payload else httpx.Response(404)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))

async def test_sec_provider_returns_registered_name_and_cik(tmp_path):
    async with fixture_client(
        SEC_TICKERS,
        {"0000080424": submission("PROCTER & GAMBLE Co", "PG", "NYSE")},
    ) as client:
        provider = SecInstrumentNameProvider(
            client=client,
            user_agent="MarketQuorum test",
            cache_dir=tmp_path,
        )
        result = await provider.resolve("PG", "NYQ")
    assert result.name == "PROCTER & GAMBLE Co"
    assert result.exchange == "NYSE"
    assert result.source == "sec_edgar"
    assert result.source_identifier == "CIK0000080424"
    assert result.locale == "en-US"
```

Add tests asserting:

```python
with pytest.raises(NameResolutionError, match="ticker_not_listed"):
    await provider.resolve("MISSING", "NYSE")

with pytest.raises(NameResolutionError, match="exchange_mismatch"):
    await provider.resolve("PG", "NASDAQ")

with pytest.raises(NameResolutionError) as error:
    await unavailable_provider.resolve("PG", "NYSE")
assert error.value.reason == "upstream_unavailable"
assert error.value.transient is True
```

- [ ] **Step 2: Run the focused tests and observe the expected import failure**

Run:

```bash
.venv/bin/pytest platform/tests/unit/instruments/test_names.py -q
```

Expected: FAIL because `SecInstrumentNameProvider` and `NameResolutionError` do not exist.

- [ ] **Step 3: Implement the SEC resolver and bounded cache**

Replace `_SEARCH_URL`, `_QUOTE_URL`, `EastMoneyInstrumentNameProvider`, and their helpers with:

```python
_SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_SEC_SUBMISSIONS_BASE = "https://data.sec.gov/submissions"
_TICKER_CACHE_TTL = timedelta(hours=24)
_SUBMISSION_CACHE_TTL = timedelta(days=7)

class NameResolutionError(RuntimeError):
    def __init__(self, reason: str, *, transient: bool):
        super().__init__(reason)
        self.reason = reason
        self.transient = transient

@dataclass(frozen=True)
class ResolvedInstrumentName:
    name: str
    exchange: str | None
    source: str
    source_identifier: str
    source_url: str
    locale: str = "en-US"

class SecInstrumentNameProvider:
    def __init__(self, client, *, user_agent, cache_dir, clock=None):
        if not user_agent.strip():
            raise ValueError("SEC User-Agent cannot be empty")
        self.client = client
        self.user_agent = user_agent.strip()
        self.cache_dir = cache_dir
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    async def resolve(self, ticker: str, exchange: str | None) -> ResolvedInstrumentName:
        normalized = canonicalize_ticker(ticker)
        index = await self._fetch_json(_SEC_TICKERS_URL, _TICKER_CACHE_TTL)
        candidates = _sec_candidates(index, normalized)
        if not candidates:
            raise NameResolutionError("ticker_not_listed", transient=False)
        matches = []
        for cik in candidates:
            url = f"{_SEC_SUBMISSIONS_BASE}/CIK{cik}.json"
            payload = await self._fetch_json(url, _SUBMISSION_CACHE_TTL)
            match = _submission_identity(payload, normalized, exchange)
            if match is not None:
                matches.append((cik, url, match))
        if not matches:
            raise NameResolutionError("exchange_mismatch", transient=False)
        if len(matches) != 1:
            raise NameResolutionError("ambiguous_cik", transient=False)
        cik, url, (name, resolved_exchange) = matches[0]
        return ResolvedInstrumentName(
            name=name,
            exchange=resolved_exchange,
            source="sec_edgar",
            source_identifier=f"CIK{cik}",
            source_url=url,
        )
```

Implement `_fetch_json()` as a SHA-256 keyed JSON envelope containing `fetched_at` and `payload`. Read a fresh cache before issuing HTTP, write through an atomic temporary file, use stale valid cache on a transient HTTP failure, and translate `httpx.HTTPError`, invalid JSON, and invalid top-level types into `NameResolutionError`.

Implement `_sec_candidates()`, `_submission_identity()`, and exchange normalization for `NYQ/NYSE`, `NMS/NASDAQ`, and `ASE/AMEX`. Require exact ticker and exact normalized exchange when the platform already knows an exchange.

- [ ] **Step 4: Run provider tests**

Run:

```bash
.venv/bin/pytest platform/tests/unit/instruments/test_names.py -q
```

Expected: provider tests PASS.

- [ ] **Step 5: Commit the provider**

```bash
git add platform/src/tradingng_platform/instruments/names.py platform/tests/unit/instruments/test_names.py
git commit -m "feat: resolve official instrument names from SEC"
```

### Task 2: Refresh scheduling, provenance history, and audits

**Files:**
- Modify: `platform/src/tradingng_platform/instruments/names.py`
- Modify: `platform/tests/integration/test_instrument_names.py`

- [ ] **Step 1: Write failing store integration tests**

Cover initial resolution, EastMoney replacement, refresh failure, and audit creation:

```python
async def test_enrichment_replaces_eastmoney_name_with_sec_and_archives_source(
    session_factory,
):
    async with session_factory() as session, session.begin():
        instrument = Instrument(
            canonical_ticker="PG",
            asset_type="stock",
            name="宝洁",
            exchange="NYQ",
            metadata_json={"name_resolution": {
                "status": "resolved",
                "provider": "eastmoney",
                "source_identifier": "106.PG",
                "resolved_at": "2026-07-26T00:00:00+00:00",
            }},
        )
        session.add(instrument)
        await session.flush()
        instrument_id = instrument.id

    class StubProvider:
        async def resolve(self, ticker, exchange):
            assert (ticker, exchange) == ("PG", "NYQ")
            return ResolvedInstrumentName(
                name="PROCTER & GAMBLE Co",
                exchange="NYSE",
                source="sec_edgar",
                source_identifier="CIK0000080424",
                source_url="https://data.sec.gov/submissions/CIK0000080424.json",
            )
    await InstrumentNameEnrichmentService(
        SqlInstrumentMetadataStore(session_factory),
        StubProvider(),
        clock=lambda: NOW,
    ).run_once()
    async with session_factory() as session:
        refreshed = await session.get(Instrument, instrument_id)
        audits = list(await session.scalars(
            select(AuditEvent).where(
                AuditEvent.action == "instrument.name_resolved",
                AuditEvent.object_id == str(instrument_id),
            )
        ))
        assert refreshed.name == "PROCTER & GAMBLE Co"
        assert refreshed.metadata_json["name_resolution"]["provider"] == "sec_edgar"
        assert refreshed.metadata_json["name_resolution_history"][0]["name"] == "宝洁"
        assert len(audits) == 1
```

Add a test where an existing `sec_edgar` name is due, the provider raises transient `upstream_unavailable`, and the name remains unchanged while `name_resolution_last_failure` records the retry.

- [ ] **Step 2: Run the focused integration test and verify failure**

Run:

```bash
.venv/bin/pytest platform/tests/integration/test_instrument_names.py -q
```

Expected: FAIL because current scheduling only selects `name IS NULL` and does not archive or audit.

- [ ] **Step 3: Implement due selection and durable provenance**

Extend `PendingInstrument` with `exchange` and `current_name`. Update `next_due()` so these are eligible:

```python
if provider != "sec_edgar":
    return pending(instrument)
if retry_at is not None and retry_at > now:
    continue
if next_refresh_at is None or next_refresh_at <= now:
    return pending(instrument)
```

On success, write:

```python
metadata["name_resolution"] = {
    "status": "resolved",
    "provider": "sec_edgar",
    "source_identifier": result.source_identifier,
    "source_url": result.source_url,
    "locale": result.locale,
    "verified_at": now.isoformat(),
    "next_refresh_at": (now + timedelta(days=7)).isoformat(),
}
metadata.pop("name_resolution_last_failure", None)
```

Archive the prior resolution only when source or value changes, deduplicate identical history entries, update the exchange using SEC’s normalized value, and insert:

```python
AuditEvent(
    actor_type="system",
    actor_id="instrument-name-enrichment",
    action="instrument.name_resolved",
    object_type="instrument",
    object_id=str(instrument.id),
    request_id=uuid.uuid4().hex,
    metadata_json={
        "ticker": instrument.canonical_ticker,
        "provider": "sec_edgar",
        "source_identifier": result.source_identifier,
        "previous_name": previous_name,
        "name": result.name,
    },
)
```

On transient failure, preserve an existing SEC name. On non-SEC or unresolved records, archive the legacy resolution and clear the unverified name. Store structured reason and retry time; use 15 minutes for transient failures and 24 hours for permanent misses.

- [ ] **Step 4: Expose aggregate resolution health in system status**

Modify `SystemService.status()` to load instruments with the existing worker/circuit query and classify metadata in Python:

```python
name_health = {
    "total": len(instruments),
    "official": 0,
    "pending": 0,
    "unresolved": 0,
    "conflicts": 0,
}
for instrument in instruments:
    resolution = (instrument.metadata_json or {}).get("name_resolution") or {}
    if resolution.get("provider") == "sec_edgar" and resolution.get("status") == "resolved":
        name_health["official"] += 1
    elif resolution.get("reason") in {"ambiguous_cik", "exchange_mismatch"}:
        name_health["conflicts"] += 1
    elif resolution.get("status") == "unresolved":
        name_health["unresolved"] += 1
    else:
        name_health["pending"] += 1
```

Return it as `instrument_names` in `/api/v1/system/status`. Add an integration assertion in `platform/tests/integration/test_records_system.py` proving SEC, pending, and conflict rows are counted separately.

- [ ] **Step 5: Update the service to pass exchange and reason taxonomy**

Use:

```python
try:
    result = await self.provider.resolve(pending.ticker, pending.exchange)
except NameResolutionError as error:
    delay = _TRANSIENT_RETRY_DELAY if error.transient else _PERMANENT_RETRY_DELAY
    await self.store.mark_unresolved(
        pending.id,
        now,
        now + delay,
        error.reason,
        transient=error.transient,
    )
    return True
```

Unexpected exceptions become transient `internal_error`, with only the exception type logged.

- [ ] **Step 6: Run unit and integration tests**

```bash
.venv/bin/pytest \
  platform/tests/unit/instruments/test_names.py \
  platform/tests/integration/test_instrument_names.py \
  platform/tests/integration/test_records_system.py -q
```

Expected: all PASS.

- [ ] **Step 7: Commit the persistence behavior**

```bash
git add platform/src/tradingng_platform/instruments/names.py \
  platform/src/tradingng_platform/system/service.py \
  platform/tests/integration/test_instrument_names.py \
  platform/tests/integration/test_records_system.py
git commit -m "feat: audit and refresh official instrument names"
```

### Task 3: Scheduler wiring and one-shot backfill

**Files:**
- Modify: `platform/src/tradingng_platform/scheduler/main.py`
- Create: `platform/src/tradingng_platform/instruments/backfill.py`
- Modify: `platform/pyproject.toml`
- Modify: `platform/tests/operations/test_deploy_config.py`

- [ ] **Step 1: Write failing operations assertions**

Add:

```python
def test_official_name_backfill_entry_point_is_installed():
    pyproject = tomllib.loads(Path("platform/pyproject.toml").read_text())
    assert (
        pyproject["project"]["scripts"]["tradingng-platform-name-backfill"]
        == "tradingng_platform.instruments.backfill:main"
    )
```

Add a scheduler source assertion that `settings.sec_user_agent` and `settings.sec_cache_dir / "instrument-names"` are passed to the enrichment loop.

- [ ] **Step 2: Run the operations test and verify failure**

```bash
.venv/bin/pytest platform/tests/operations/test_deploy_config.py -q
```

Expected: FAIL because the backfill entry point and scheduler arguments do not exist.

- [ ] **Step 3: Wire scheduler configuration**

Change the task construction to:

```python
run_instrument_name_enrichment(
    database.sessions,
    stopping,
    user_agent=settings.sec_user_agent,
    cache_dir=settings.sec_cache_dir / "instrument-names",
)
```

Update `run_instrument_name_enrichment()` to construct an `httpx.AsyncClient` with the configured SEC User-Agent and the new `SecInstrumentNameProvider`.

- [ ] **Step 4: Add bounded backfill command**

Create `backfill.py` with:

```python
async def backfill() -> int:
    settings = Settings()
    database = Database(settings)
    processed = 0
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            service = InstrumentNameEnrichmentService(
                SqlInstrumentMetadataStore(database.sessions),
                SecInstrumentNameProvider(
                    client,
                    user_agent=settings.sec_user_agent,
                    cache_dir=settings.sec_cache_dir / "instrument-names",
                ),
            )
            while await service.run_once():
                processed += 1
        return processed
    finally:
        await database.close()

def main() -> None:
    processed = asyncio.run(backfill())
    print(f"processed={processed}")
```

Register:

```toml
tradingng-platform-name-backfill = "tradingng_platform.instruments.backfill:main"
```

- [ ] **Step 5: Run operations and name tests**

```bash
.venv/bin/pytest \
  platform/tests/operations/test_deploy_config.py \
  platform/tests/unit/instruments/test_names.py \
  platform/tests/integration/test_instrument_names.py -q
```

Expected: all PASS.

- [ ] **Step 6: Commit scheduler and backfill**

```bash
git add platform/src/tradingng_platform/scheduler/main.py \
  platform/src/tradingng_platform/instruments/backfill.py \
  platform/pyproject.toml \
  platform/tests/operations/test_deploy_config.py
git commit -m "feat: backfill SEC instrument identities"
```

### Task 4: API semantics and complete-name presentation

**Files:**
- Modify: `platform/src/tradingng_platform/assessments/contracts.py`
- Modify: `platform/src/tradingng_platform/records/contracts.py`
- Modify: `platform/src/tradingng_platform/records/service.py`
- Modify: `web/src/features/dashboard/InstrumentLedgerTable.tsx`
- Modify: `web/src/features/dashboard/RunTable.tsx`
- Modify: `web/src/features/dashboard/DashboardPage.test.tsx`
- Modify: `web/src/features/instruments/InstrumentHistoryPage.tsx`
- Modify: `web/src/features/instruments/InstrumentHistoryPage.test.tsx`
- Modify: `web/src/features/runs/RunDetailPage.tsx`
- Modify: `web/src/features/runs/RunDetailPage.test.tsx`
- Modify: `web/src/features/system/SystemPage.tsx`
- Modify: `web/src/features/system/SystemPage.test.tsx`
- Modify: `web/src/api/system.ts`
- Modify: `web/src/styles/global.css`
- Modify: `web/src/i18n/messages.ts`

- [ ] **Step 1: Write failing web assertions using SEC names**

Replace localized fixtures with:

```typescript
instrument_name: "NVIDIA CORP"
```

Add:

```typescript
expect(
  await screen.findByRole("link", { name: "NVIDIA CORP NVDA NASDAQ" }),
).toBeVisible();
expect(screen.getByText("NVIDIA CORP")).toBeVisible();
```

Use a long fixture such as `INTERNATIONAL BUSINESS MACHINES CORP` and assert the full string is rendered without application-level truncation.

- [ ] **Step 2: Run web tests and record failure**

```bash
npm --prefix web test -- --run src/features/dashboard/DashboardPage.test.tsx
```

Expected: FAIL until fixtures and presentation are updated.

- [ ] **Step 3: Document compatible API fields**

Use Pydantic descriptions without renaming fields:

```python
instrument_name: str | None = Field(
    default=None,
    description="SEC-verified official instrument name when available.",
)

name: str | None = Field(
    default=None,
    description="SEC-verified official instrument name when available.",
)
```

Add `name` and `exchange` as optional fields to `InstrumentSummaryView`; populate them from the unique `Instrument` row in `RecordsService.instrument_summary()`. This additive response lets the history page use the same official identity as the ledger without breaking callers.

- [ ] **Step 4: Adjust every instrument header without abbreviating source values**

Keep the source string unchanged and add an accessible semantic label:

```tsx
<span className="instrument-name" aria-label={t("官方名称")}>
  {item.instrument.name ?? item.instrument.ticker}
</span>
```

Do the same in `RunTable`. Add `"官方名称": "Official name"` to `EN_US`.

Use the additive summary fields on the history page:

```tsx
<h1>
  {summary.data?.name ?? normalized}
  {summary.data?.name ? <small>{normalized}{summary.data.exchange ? ` · ${summary.data.exchange}` : ""}</small> : null}
</h1>
```

Use `run.data.instrument_name` on the run detail page with the ticker and exchange as secondary identity. Extend `SystemStatus` in `web/src/api/system.ts` with `instrument_names`, render its official/pending/unresolved/conflict counts on `SystemPage`, and cover the panel in `SystemPage.test.tsx`.

Change desktop column widths to 24/34/23/19 percent and allow names to wrap:

```css
.ledger-column-identity { width: 24%; }
.ledger-column-main { width: 34%; }
.ledger-column-signals { width: 23%; }
.ledger-column-operations { width: 19%; }

.ledger-line .instrument-name,
.run-table .instrument-name {
  white-space: normal;
  overflow-wrap: anywhere;
}
```

Do not use `text-overflow: ellipsis`, substring truncation, title-casing, or abbreviation.

- [ ] **Step 5: Regenerate OpenAPI TypeScript schema**

Run the exact OpenAPI generation commands:

```bash
PYTHONPATH=platform/src .venv/bin/python scripts/export_openapi.py
npm --prefix web run api:generate
```

Expected: `web/src/api/schema.d.ts` reflects descriptions without a breaking field change.

- [ ] **Step 6: Run API and web tests**

```bash
.venv/bin/pytest \
  platform/tests/unit/api/test_records.py \
  platform/tests/unit/api/test_assessments.py -q
npm --prefix web test -- --run src/features/dashboard/DashboardPage.test.tsx
npm --prefix web run build
```

Expected: all tests PASS and production build succeeds.

- [ ] **Step 7: Commit API and presentation**

```bash
git add platform/src/tradingng_platform/assessments/contracts.py \
  platform/src/tradingng_platform/records/contracts.py \
  platform/src/tradingng_platform/records/service.py \
  web/src/api/schema.d.ts \
  web/src/api/system.ts \
  web/src/features/dashboard/InstrumentLedgerTable.tsx \
  web/src/features/dashboard/RunTable.tsx \
  web/src/features/dashboard/DashboardPage.test.tsx \
  web/src/features/instruments/InstrumentHistoryPage.tsx \
  web/src/features/instruments/InstrumentHistoryPage.test.tsx \
  web/src/features/runs/RunDetailPage.tsx \
  web/src/features/runs/RunDetailPage.test.tsx \
  web/src/features/system/SystemPage.tsx \
  web/src/features/system/SystemPage.test.tsx \
  web/src/styles/global.css web/src/i18n/messages.ts
git commit -m "feat: display complete official instrument names"
```

### Task 5: Documentation and production-safe verification

**Files:**
- Modify: `README.md`
- Modify: `README.zh-CN.md`

- [ ] **Step 1: Document source semantics**

Add to both READMEs:

```markdown
Instrument names are resolved from SEC EDGAR and retain their SEC spelling,
capitalization, CIK, source URL, and verification time. If SEC cannot uniquely
verify an identity, the UI displays the ticker; third-party names are never
presented as official names.
```

The Chinese version must state the same contract, including that Alpha Vantage is not a fallback for official names.

- [ ] **Step 2: Run backend quality gates**

```bash
.venv/bin/ruff check platform/src platform/tests
.venv/bin/pytest platform/tests/unit platform/tests/integration platform/tests/operations -q
```

Expected: lint clean and all selected tests PASS.

- [ ] **Step 3: Run frontend quality gates**

```bash
npm --prefix web test -- --run
npm --prefix web run build
```

Expected: all tests PASS and production bundle builds.

- [ ] **Step 4: Confirm protected upstream is untouched**

```bash
git status --short
git diff --submodule=short -- TradingAgents
```

Expected: no `TradingAgents/` changes.

- [ ] **Step 5: Commit documentation**

```bash
git add README.md README.zh-CN.md
git commit -m "docs: explain SEC official instrument names"
```

### Task 6: Deploy, backfill, and inspect production data

**Files:**
- No source files expected.

- [ ] **Step 1: Restart only the API and scheduler user services**

Run:

```bash
systemctl --user restart tradingng-platform-api.service tradingng-platform-scheduler.service
systemctl --user is-active tradingng-platform-api.service tradingng-platform-scheduler.service
curl -fsS http://127.0.0.1:8010/health/live
```

Expected: both units print `active` and the API health request succeeds. Do not restart assessment workers; their code path does not import the name provider.

- [ ] **Step 2: Run the one-shot backfill**

```bash
PYTHONPATH=platform/src .venv/bin/python -m tradingng_platform.instruments.backfill
```

Expected: prints a finite `processed=<count>` and exits successfully.

- [ ] **Step 3: Query production aggregates without exposing secrets**

Run this read-only SQLAlchemy query:

```bash
PYTHONPATH=platform/src .venv/bin/python - <<'PY'
import asyncio
from sqlalchemy import select
from tradingng_platform.config import Settings
from tradingng_platform.db import Database
from tradingng_platform.models import Instrument

async def main():
    db = Database(Settings())
    try:
        async with db.sessions() as session:
            rows = list(await session.scalars(
                select(Instrument).order_by(Instrument.canonical_ticker)
            ))
        eastmoney = 0
        for item in rows:
            resolution = (item.metadata_json or {}).get("name_resolution") or {}
            if resolution.get("provider") == "eastmoney" and item.name:
                eastmoney += 1
            if item.canonical_ticker in {"PG", "PLD", "NVDA"}:
                print(item.canonical_ticker, repr(item.name), resolution.get("provider"))
        print("eastmoney_primary_names", eastmoney)
    finally:
        await db.close()

asyncio.run(main())
PY
```

Assert:

```text
provider=sec_edgar
PG=PROCTER & GAMBLE Co
PLD=Prologis, Inc.
NVDA=NVIDIA CORP
eastmoney_primary_names=0
```

For SEC-unmapped instruments, verify `name IS NULL`, a structured reason is present, and the UI uses the ticker.

- [ ] **Step 4: Verify no Alpha Vantage name traffic**

Compare Alpha broker counters before and after a second idempotent backfill. Expected: no new Alpha Vantage requests attributable to name resolution.

- [ ] **Step 5: Browser smoke test**

Open the production overview in Chinese and English, verify PG and PLD show complete SEC names, ticker and exchange remain on the second line, long names are not clipped, and navigation still works.

- [ ] **Step 6: Final repository verification**

```bash
git status --short
git log --oneline -6
```

Expected: clean worktree on `main`, implementation commits present, and no uncommitted production edits.

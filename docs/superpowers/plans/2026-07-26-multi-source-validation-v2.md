# Multi-Source Validation v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade outcome validation to an exact-session, lease-recoverable, provider-neutral `validation.v2` while preserving every existing `validation.v1` row and leaving `TradingAgents/` unchanged.

**Architecture:** Extend validation persistence with explicit versions, schedules, leases and dual return metrics. Normalize provider-specific daily prices into a canonical split-normalized series before calculating v2 outcomes; keep the current calculator and artifact parser for v1. Route optional Alpha Vantage and default yfinance adapters through one provider interface, then expose additive fields through REST, MCP and Web.

**Tech Stack:** Python 3.10, FastAPI, Pydantic 2, SQLAlchemy 2, Alembic, exchange_calendars, httpx, yfinance, MySQL/PostgreSQL, React 19, TypeScript, Vitest.

---

### Task 1: Persist validation versions, schedules, leases and target bases

**Files:**
- Create: `platform/migrations/versions/20260726_0007_multi_source_validation_v2.py`
- Modify: `platform/src/tradingng_platform/models/validation.py`
- Modify: `platform/src/tradingng_platform/models/__init__.py`
- Modify: `platform/src/tradingng_platform/validation/contracts.py`
- Test: `platform/tests/unit/models/test_validation_v2.py`
- Test: `platform/tests/operations/test_database_migration.py`

- [ ] **Step 1: Write failing model and contract tests**

```python
def test_validation_v2_columns_are_available():
    names = Validation.__table__.columns.keys()
    assert {"calculation_version", "matures_at", "lease_expires_at", "total_return"} <= set(names)
    assert DecisionPriceBasis.__table__.columns["run_id"].unique is True

def test_v1_view_preserves_legacy_return_aliases():
    view = ValidationView.model_validate(v1_fixture, from_attributes=True)
    assert view.calculation_version == "validation.v1"
    assert view.total_return == view.raw_return
```

- [ ] **Step 2: Run the tests and verify missing fields fail**

Run: `PYTHONPATH=platform/src .venv/bin/pytest platform/tests/unit/models/test_validation_v2.py -q`
Expected: FAIL because the v2 columns and `DecisionPriceBasis` do not exist.

- [ ] **Step 3: Add the additive migration and ORM fields**

The migration must add nullable schedule/provider/metric columns, add non-null `calculation_version` with server default `validation.v1`, create lease indexes, and create `decision_price_bases` with a unique `run_id`. The downgrade drops only revision 0007 objects. The ORM must use `PORTABLE_DATETIME`, `PORTABLE_JSON`, `Numeric(20, 10)` and UUID foreign keys already used by the project.

- [ ] **Step 4: Make the public contract additive and compatible**

`ValidationView` accepts `validation.v1` and `validation.v2`, exposes v2 metrics and provider provenance, and fills total-return fields from legacy fields when the row is v1. `ValidationTriggerResults` adds `price_target_status`, `rebased_price_target`, and `data_quality_status` without removing current fields.

- [ ] **Step 5: Run model, OpenAPI and migration tests**

Run: `PYTHONPATH=platform/src .venv/bin/pytest platform/tests/unit/models platform/tests/unit/validation/test_schedule.py platform/tests/operations/test_database_migration.py -q`
Expected: PASS, with only environment-gated migration integration tests skipped.

- [ ] **Step 6: Commit**

```bash
git add platform/migrations/versions/20260726_0007_multi_source_validation_v2.py platform/src/tradingng_platform/models platform/src/tradingng_platform/validation/contracts.py platform/tests
git commit -m "feat: version validation persistence"
```

### Task 2: Calculate exact market sessions and maturity times

**Files:**
- Modify: `platform/pyproject.toml`
- Create: `platform/src/tradingng_platform/validation/calendars.py`
- Modify: `platform/src/tradingng_platform/validation/repository.py`
- Modify: `platform/src/tradingng_platform/worker/repository.py`
- Test: `platform/tests/unit/validation/test_calendars.py`
- Test: `platform/tests/unit/validation/test_schedule.py`

- [ ] **Step 1: Write failing calendar tests**

```python
def test_us_weekend_schedule_uses_trading_sessions():
    schedule = resolver.schedule("stock", "NMS", date(2026, 7, 25), 20)
    assert schedule.calendar_code == "XNYS"
    assert schedule.entry_session == date(2026, 7, 27)
    assert schedule.exit_session == date(2026, 8, 24)
    assert schedule.matures_at > datetime(2026, 8, 24, 20, tzinfo=timezone.utc)

def test_crypto_uses_utc_daily_sessions():
    schedule = resolver.schedule("crypto", None, date(2026, 7, 25), 1)
    assert schedule.calendar_code == "24/7"
    assert schedule.exit_session == date(2026, 7, 26)
```

- [ ] **Step 2: Verify tests fail before the resolver exists**

Run: `PYTHONPATH=platform/src .venv/bin/pytest platform/tests/unit/validation/test_calendars.py -q`
Expected: FAIL importing `MarketCalendarResolver`.

- [ ] **Step 3: Implement the calendar boundary**

Add a pinned `exchange_calendars>=4.11,<5` dependency. Implement `ValidationSchedule(calendar_code, entry_session, exit_session, matures_at)` and `MarketCalendarResolver.schedule(asset_type, exchange, analysis_date, horizon)`. Map supported US exchange codes to XNYS, crypto to UTC daily sessions, and everything else to an explicit weekday fallback.

- [ ] **Step 4: Schedule new rows as v2 without changing existing rows**

Change `schedule_validations` to accept a calculation version and resolver. Automatic scheduling from successful assessment finalization passes `validation.v2`; user scheduling remains idempotent and does not update a pre-existing row. Persist exact entry, exit and maturity values for newly inserted v2 rows.

- [ ] **Step 5: Run calendar and scheduling tests**

Run: `PYTHONPATH=platform/src .venv/bin/pytest platform/tests/unit/validation/test_calendars.py platform/tests/unit/validation/test_schedule.py platform/tests/integration/test_scheduler_worker.py -q`
Expected: PASS or only the configured database integration test is skipped.

- [ ] **Step 6: Commit**

```bash
git add platform/pyproject.toml platform/src/tradingng_platform/validation/calendars.py platform/src/tradingng_platform/validation/repository.py platform/src/tradingng_platform/worker/repository.py platform/tests
git commit -m "feat: schedule validations by market session"
```

### Task 3: Define provider-neutral prices and canonical normalization

**Files:**
- Create: `platform/src/tradingng_platform/validation/price_contracts.py`
- Create: `platform/src/tradingng_platform/validation/normalizer.py`
- Test: `platform/tests/unit/validation/test_normalizer.py`

- [ ] **Step 1: Write failing normalization fixtures**

```python
def test_as_traded_split_and_split_normalized_series_match():
    alpha = provider_series(ohlc_basis="as_traded", closes=[100, 50, 55], splits=[1, 2, 1])
    yahoo = provider_series(ohlc_basis="split_normalized", closes=[50, 50, 55], splits=[1, 2, 1])
    assert normalize(alpha).close == normalize(yahoo).close == [Decimal("50"), Decimal("50"), Decimal("55")]

def test_cash_distribution_changes_total_not_price_return():
    series = normalized_fixture(closes=[100, 99], distributions=[0, 1])
    assert series.price_index[-1] == Decimal("99")
    assert series.total_return_index[-1] == Decimal("100")
```

- [ ] **Step 2: Run tests and verify imports fail**

Run: `PYTHONPATH=platform/src .venv/bin/pytest platform/tests/unit/validation/test_normalizer.py -q`
Expected: FAIL importing provider-neutral contracts.

- [ ] **Step 3: Implement strict provider contracts**

Define `OhlcBasis`, `CashDistributionKind`, `ProviderPriceSeries` and `CanonicalPriceSeries` as strict Pydantic models. Require equal-length monotonic sessions, finite positive OHLC values, non-negative distributions, positive split coefficients, explicit currency/timezone when supplied, adapter version, capabilities and request fingerprint.

- [ ] **Step 4: Implement `prices.v1` normalization**

For `as_traded`, divide each OHLC and cash distribution by the product of later split coefficients through the share-basis session. For `split_normalized`, retain OHLC and distributions as supplied. Build chained price and total-return indexes from the normalized values. Compare provider adjusted-close return to canonical total return and classify `matched`, `minor_difference`, `material_difference` or `not_available`.

- [ ] **Step 5: Run normalization tests**

Run: `PYTHONPATH=platform/src .venv/bin/pytest platform/tests/unit/validation/test_normalizer.py -q`
Expected: PASS for no-action, split, dividend, reverse-split and malformed-data fixtures.

- [ ] **Step 6: Commit**

```bash
git add platform/src/tradingng_platform/validation/price_contracts.py platform/src/tradingng_platform/validation/normalizer.py platform/tests/unit/validation/test_normalizer.py
git commit -m "feat: normalize provider-neutral prices"
```

### Task 4: Implement yfinance and optional Alpha Vantage adapters

**Files:**
- Modify: `platform/src/tradingng_platform/config.py`
- Replace responsibilities in: `platform/src/tradingng_platform/validation/prices.py`
- Create: `platform/src/tradingng_platform/validation/providers.py`
- Test: `platform/tests/unit/validation/test_prices.py`
- Test: `platform/tests/unit/validation/test_providers.py`
- Test: `platform/tests/unit/test_config.py`

- [ ] **Step 1: Write failing adapter and router tests**

```python
async def test_alpha_adapter_maps_daily_adjusted_payload(httpx_mock):
    httpx_mock.add_response(json=alpha_daily_adjusted_fixture)
    series = await AlphaVantagePriceProvider("secret").history("IBM", start, end)
    assert series.ohlc_basis == OhlcBasis.AS_TRADED
    assert series.split_coefficient[-1] == Decimal("2")

async def test_router_never_calls_alpha_without_a_key():
    router = build_price_provider(Settings(alpha_vantage_api_key=None))
    assert router.provider_ids == ("yfinance",)
```

- [ ] **Step 2: Verify adapter tests fail**

Run: `PYTHONPATH=platform/src .venv/bin/pytest platform/tests/unit/validation/test_prices.py platform/tests/unit/validation/test_providers.py -q`
Expected: FAIL because the adapters and router are absent.

- [ ] **Step 3: Upgrade the yfinance adapter**

Fetch `auto_adjust=False`, `actions=True`, `threads=False`; parse dividends, stock splits and capital gains when present; emit `ohlc_basis=split_normalized`, `provider_adapter_version=yfinance.v2`, and a secret-free request fingerprint.

- [ ] **Step 4: Add the Alpha Vantage adapter**

Call only `TIME_SERIES_DAILY_ADJUSTED` with an injected `httpx.AsyncClient`, classify JSON `Note`, `Information` and `Error Message` responses into rate-limit, unavailable and invalid-symbol errors, filter to the requested sessions, and never include the API key in exceptions or fingerprints.

- [ ] **Step 5: Add configuration and failover routing**

Add secret `alpha_vantage_api_key`, provider order, timeout and per-minute capacity settings. Build a router that tries providers in configured order only for transient/provider capability failures and exposes the selected provider in the returned series.

- [ ] **Step 6: Run adapters, configuration and secret-scanning tests**

Run: `PYTHONPATH=platform/src .venv/bin/pytest platform/tests/unit/validation/test_prices.py platform/tests/unit/validation/test_providers.py platform/tests/unit/test_config.py -q`
Expected: PASS and no assertion output containing the test API key.

- [ ] **Step 7: Commit**

```bash
git add platform/src/tradingng_platform/config.py platform/src/tradingng_platform/validation/prices.py platform/src/tradingng_platform/validation/providers.py platform/tests
git commit -m "feat: add pluggable validation price providers"
```

### Task 5: Calculate v2 outcomes and freeze target-price bases

**Files:**
- Create: `platform/src/tradingng_platform/validation/calculator_v2.py`
- Create: `platform/src/tradingng_platform/validation/bases.py`
- Modify: `platform/src/tradingng_platform/validation/calculator.py`
- Test: `platform/tests/unit/validation/test_calculator_v2.py`
- Test: `platform/tests/unit/validation/test_bases.py`

- [ ] **Step 1: Write failing v2 calculation tests**

```python
def test_v2_separates_price_and_total_returns():
    result = calculate_outcome_v2(instrument_with_dividend, benchmark, schedule, "Buy", None)
    assert result.price_return == Decimal("-0.0100000000")
    assert result.total_return == Decimal("0E-10")

def test_target_does_not_move_on_dividend_but_rebases_on_split():
    result = calculate_outcome_v2(split_path, benchmark, schedule, "Buy", target_basis)
    assert result.trigger_results["rebased_price_target"] == "60"
    assert result.trigger_results["price_target_hit"] is False
```

- [ ] **Step 2: Verify v2 tests fail**

Run: `PYTHONPATH=platform/src .venv/bin/pytest platform/tests/unit/validation/test_calculator_v2.py platform/tests/unit/validation/test_bases.py -q`
Expected: FAIL because v2 calculation and basis preparation are absent.

- [ ] **Step 3: Implement v2 calculation**

Use the frozen entry/exit sessions. Calculate price and total return pairs, both alphas, price-path MAE/MFE, direction correctness from total return, and target hit from a rebased target multiple. Quantize every metric to `0.0000000001`.

- [ ] **Step 4: Implement non-blocking target-basis preparation**

Create pending basis rows only when a decision has a target. The validation service fetches through the reference session, chooses the last session on or before analysis date, stores its normalized close and target multiple, and retries transient errors independently of horizon validations.

- [ ] **Step 5: Run calculator and basis tests**

Run: `PYTHONPATH=platform/src .venv/bin/pytest platform/tests/unit/validation/test_calculator.py platform/tests/unit/validation/test_calculator_v2.py platform/tests/unit/validation/test_bases.py -q`
Expected: PASS, demonstrating v1 remains unchanged.

- [ ] **Step 6: Commit**

```bash
git add platform/src/tradingng_platform/validation/calculator.py platform/src/tradingng_platform/validation/calculator_v2.py platform/src/tradingng_platform/validation/bases.py platform/tests/unit/validation
git commit -m "feat: calculate versioned validation outcomes"
```

### Task 6: Add leases, v1/v2 dispatch, minimal artifacts and retry operations

**Files:**
- Modify: `platform/src/tradingng_platform/validation/worker.py`
- Modify: `platform/src/tradingng_platform/validation/main.py`
- Modify: `platform/src/tradingng_platform/validation/repository.py`
- Modify: `platform/src/tradingng_platform/validation/service.py`
- Test: `platform/tests/unit/validation/test_worker_leases.py`
- Test: `platform/tests/integration/test_validation.py`

- [ ] **Step 1: Write failing worker recovery tests**

```python
async def test_expired_running_claim_is_recovered(session_factory):
    await seed_running_validation(lease_expires_at=now - timedelta(seconds=1))
    claim = await worker.claim(now)
    assert claim.id == validation_id
    assert await event_types(run_id) == ["validation.recovered"]

async def test_existing_v1_and_new_v2_use_their_own_calculators(session_factory):
    await seed_due_versions("validation.v1", "validation.v2")
    await worker.run_once(now)
    await worker.run_once(now)
    assert await stored_versions() == ["validation.v1", "validation.v2"]
```

- [ ] **Step 2: Verify recovery tests fail**

Run: `PYTHONPATH=platform/src .venv/bin/pytest platform/tests/unit/validation/test_worker_leases.py -q`
Expected: FAIL because running rows are not leased or recovered.

- [ ] **Step 3: Implement atomic lease claim and recovery**

Recover expired running rows before capacity counting, append `validation.recovered`, then claim one due row with `FOR UPDATE SKIP LOCKED`. Clear lease fields on completion/retry/failure. The main loop supplies a stable worker instance and continues its 30-second idle poll.

- [ ] **Step 4: Dispatch versioned providers and calculators**

v1 continues using the legacy yfinance series and calculator. v2 uses provider routing, normalization and `calculate_outcome_v2`. Persist both legacy aliases and v2 fields. Trim v2 artifacts to reference/background/entry-through-exit sessions and store provenance in artifact metadata.

- [ ] **Step 5: Add explicit retry service operation**

Permit `failed`, `unavailable` and expired `running` rows to return to `scheduled`; reject completed and actively leased rows. Reset error and lease fields, preserve attempts for audit, and append `validation.retry_requested`.

- [ ] **Step 6: Run worker and integration tests**

Run: `PYTHONPATH=platform/src .venv/bin/pytest platform/tests/unit/validation platform/tests/integration/test_validation.py -q`
Expected: PASS or only the environment-gated database integration test is skipped.

- [ ] **Step 7: Commit**

```bash
git add platform/src/tradingng_platform/validation platform/tests/unit/validation platform/tests/integration/test_validation.py
git commit -m "feat: harden validation execution"
```

### Task 7: Expose v2 through REST, MCP, OpenAPI and Web

**Files:**
- Modify: `platform/src/tradingng_platform/api/routes/validations.py`
- Modify: `platform/src/tradingng_platform/mcp/tools.py`
- Modify: `platform/tests/unit/mcp/test_tools.py`
- Modify: `platform/tests/integration/test_validation.py`
- Modify: `web/src/features/runs/ValidationReplayPanel.tsx`
- Modify: `web/src/features/runs/ValidationReplayPanel.test.tsx`
- Modify: `web/src/features/runs/validationReplay.ts`
- Modify: `web/src/features/runs/validationReplay.test.ts`
- Regenerate: `openapi/platform.openapi.json`
- Regenerate: `web/src/api/schema.d.ts`

- [ ] **Step 1: Write failing REST/MCP/Web assertions**

```python
assert payload["calculation_version"] == "validation.v2"
assert payload["total_return"] == payload["raw_return"]
assert payload["matures_at"] is not None
```

```tsx
expect(screen.getByText("总回报（含现金分配）")).toBeInTheDocument();
expect(screen.getByText("价格回报")).toBeInTheDocument();
expect(screen.getByText("prices.v1")).toBeInTheDocument();
```

- [ ] **Step 2: Verify API and Web tests fail**

Run: `PYTHONPATH=platform/src .venv/bin/pytest platform/tests/unit/mcp/test_tools.py platform/tests/integration/test_validation.py -q && npm --prefix web run test -- --run src/features/runs/ValidationReplayPanel.test.tsx`
Expected: FAIL on missing v2 display and retry operations.

- [ ] **Step 3: Add REST and MCP retry operations**

Add `POST /api/v1/validations/{validation_id}/retry` returning `ValidationView` and an MCP `retry_validation(validation_id)` tool. Both call the same service and require `validations:write`.

- [ ] **Step 4: Make Web rendering version-aware**

For v2 show total return, price return, total Alpha, provider and quality information; for v1 retain the existing labels and replay parser. Use `matures_at` for scheduled-state messaging when present. Do not change TradingView URL behavior.

- [ ] **Step 5: Regenerate OpenAPI and generated TypeScript**

Run: `.venv/bin/python scripts/export_openapi.py && npm --prefix web run api:generate`
Expected: both generated files change only additively for validation schemas and the retry operation.

- [ ] **Step 6: Run API, MCP and Web tests**

Run: `PYTHONPATH=platform/src .venv/bin/pytest platform/tests/unit/api platform/tests/unit/mcp platform/tests/integration/test_validation.py -q && npm --prefix web run test -- --run`
Expected: PASS, with environment-only skips documented.

- [ ] **Step 7: Commit**

```bash
git add platform/src/tradingng_platform/api/routes/validations.py platform/src/tradingng_platform/mcp/tools.py platform/tests openapi web
git commit -m "feat: expose validation v2 audit data"
```

### Task 8: Document configuration, migrate production and verify invariants

**Files:**
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `.env.example` or the repository's public environment template
- Modify: `scripts/verify_platform.sh`
- Test: `integration_tests/test_persistent_configuration.py`

- [ ] **Step 1: Add configuration contract tests**

Assert that public templates contain empty Alpha Vantage placeholders, default provider order remains yfinance-only, secrets are ignored by git, and service verification checks the validation worker.

- [ ] **Step 2: Document provider-neutral behavior**

Document v1/v2 compatibility, total versus price return, optional Alpha Vantage variables, no-real-time requirement for EOD validation, provider licensing responsibility, and the fact that TradingAgents is unchanged.

- [ ] **Step 3: Run pre-migration verification**

Run: `git diff --submodule=diff -- TradingAgents && git diff --check && PYTHONPATH=platform/src .venv/bin/pytest platform/tests/unit platform/tests/integration/test_validation.py integration_tests/test_persistent_configuration.py -q && npm --prefix web run test -- --run && npm --prefix web run build`
Expected: no TradingAgents diff; all runnable tests and build pass.

- [ ] **Step 4: Record production invariants and migrate**

Read-only capture counts grouped by validation version/status and hashes of existing completed rows. Run `.venv/bin/alembic -c platform/alembic.ini upgrade head`. Re-query and assert all pre-existing rows remain `validation.v1` with unchanged status, metrics and artifact identifiers.

- [ ] **Step 5: Restart and smoke-test services**

Restart API and validation services, confirm health, inspect recent journals, confirm no overdue backlog, create no synthetic production assessment, and verify the API/OpenAPI contract through existing authenticated smoke scripts.

- [ ] **Step 6: Run repository security and full verification**

Run: `./scripts/verify_platform.sh && git diff --check && git status --short && git diff --submodule=diff -- TradingAgents`
Expected: healthy services, clean formatting, no secrets, no TradingAgents change.

- [ ] **Step 7: Commit and push main**

```bash
git add README.md README.zh-CN.md scripts integration_tests
git commit -m "docs: operate multi-source validation"
git push origin main
```

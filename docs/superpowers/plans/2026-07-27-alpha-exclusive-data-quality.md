# Alpha-Only Data Quality Safeguards Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep every configured Alpha Vantage workload on Alpha Vantage through coordinated delayed retries, repair technical-indicator response handling, and make run-step projections reach an accurate terminal state.

**Architecture:** Add a TradingNG-owned cross-process request gate and retry policy, then temporarily install it around TradingAgents Alpha request functions and the verified snapshot loader without changing the submodule. Freeze the non-secret policy into each admitted run, route both validation generations through the effective Alpha-only provider, and close `RunStep` projections transactionally at stage and run boundaries.

**Tech Stack:** Python 3.10, Pydantic Settings, requests/httpx, pandas, SQLAlchemy async, Alembic, pytest, systemd user services.

---

## Constraints

- Work directly on `main`; do not use a worktree or subagent.
- Do not modify any path below `TradingAgents/`.
- Never print, persist or include either Alpha Vantage key in an exception.
- When an Alpha key is configured, neither research nor validation may call Yahoo.
- Rate-limit recovery must delay and retry the same Alpha operation.
- Existing active assessments must not be cancelled or rewritten.

## File map

- Create `platform/src/tradingng_platform/vendors/__init__.py`: export Alpha coordination primitives.
- Create `platform/src/tradingng_platform/vendors/alpha_vantage.py`: cross-process request gate, retry classification and policy.
- Modify `platform/src/tradingng_platform/config.py`: effective Alpha-only routes and retry settings.
- Modify `platform/src/tradingng_platform/scheduler/main.py`: freeze Alpha-only categories and retry policy.
- Modify `platform/src/tradingng_platform/scheduler/repository.py`: include vendor policy in immutable snapshots.
- Modify `platform/src/tradingng_platform/runner/contracts.py`: carry the frozen policy into the runner.
- Modify `platform/src/tradingng_platform/worker/service.py`: build the runner input from old and new snapshots compatibly.
- Modify `platform/src/tradingng_platform/runner/tradingagents.py`: install the Alpha request guard and Alpha OHLCV snapshot loader for the run context.
- Modify `platform/src/tradingng_platform/validation/providers.py`: delayed Alpha retries, Alpha-only effective router and v1 compatibility adapter.
- Modify `platform/src/tradingng_platform/validation/main.py`: use the same effective router for validation v1 and v2.
- Modify `platform/src/tradingng_platform/worker/repository.py`: finalize run steps on transitions and terminal outcomes.
- Create `platform/migrations/versions/20260727_0008_finalize_run_steps.py`: repair existing terminal-run step projections.
- Add focused tests below `platform/tests/unit/vendors`, `platform/tests/unit/runner`, `platform/tests/unit/validation`, `platform/tests/unit/worker`, `platform/tests/unit/scheduler` and `platform/tests/unit/test_config.py`.
- Modify `.env.platform.example`, `README.md` and `README.zh-CN.md`: document exclusive routing and delayed retries.

### Task 1: Pin Alpha-only configuration and immutable policy

**Files:**
- Modify: `platform/tests/unit/test_config.py`
- Modify: `platform/tests/unit/scheduler/test_main.py`
- Modify: `platform/tests/unit/scheduler/test_admission.py`
- Modify: `platform/src/tradingng_platform/config.py`
- Modify: `platform/src/tradingng_platform/scheduler/main.py`
- Modify: `platform/src/tradingng_platform/scheduler/repository.py`

- [ ] **Step 1: Write failing configuration tests**

Add assertions equivalent to:

```python
settings = Settings(
    _env_file=None,
    database_url="sqlite+aiosqlite:///:memory:",
    alpha_vantage_api_key="validation-key",
    research_alpha_vantage_api_key="research-key",
    research_data_vendor_chain=("alpha_vantage", "yfinance"),
    validation_price_providers=("alphavantage", "yfinance"),
)
assert settings.effective_research_data_vendor_chain == ("alpha_vantage",)
assert settings.effective_validation_price_providers == ("alphavantage",)
assert settings.alpha_vantage_retry_attempts == 6
```

Extend scheduler tests so all four supported categories equal exactly `alpha_vantage` and `metadata.vendor_policies` contains only non-secret retry numbers.

- [ ] **Step 2: Run RED**

Run:

```bash
.venv/bin/pytest platform/tests/unit/test_config.py platform/tests/unit/scheduler/test_main.py platform/tests/unit/scheduler/test_admission.py -q
```

Expected: failures because effective routes and `vendor_policies` do not exist.

- [ ] **Step 3: Implement minimal settings and snapshot changes**

Add secret research-key detection and bounded retry fields, plus computed properties:

```python
@computed_field
@property
def effective_research_data_vendor_chain(self) -> tuple[str, ...]:
    if self.research_alpha_vantage_api_key is not None:
        return ("alpha_vantage",)
    return self.research_data_vendor_chain

@computed_field
@property
def effective_validation_price_providers(self) -> tuple[str, ...]:
    if self.alpha_vantage_api_key is not None:
        return ("alphavantage",)
    return self.validation_price_providers
```

Add `vendor_policies: dict[str, dict]` to `ExecutionMetadata` with a default factory. Have `_execution_metadata` use the effective research chain and freeze:

```python
{"alpha_vantage": {
    "requests_per_minute": settings.alpha_vantage_requests_per_minute,
    "retry_attempts": settings.alpha_vantage_retry_attempts,
    "retry_base_seconds": settings.alpha_vantage_retry_base_seconds,
    "retry_max_seconds": settings.alpha_vantage_retry_max_seconds,
}}
```

Include that mapping in `build_run_snapshot`.

- [ ] **Step 4: Run GREEN and commit**

Run the Task 1 command again, then commit the isolated configuration change.

### Task 2: Build the shared Alpha request gate and retry classifier

**Files:**
- Create: `platform/src/tradingng_platform/vendors/__init__.py`
- Create: `platform/src/tradingng_platform/vendors/alpha_vantage.py`
- Create: `platform/tests/unit/vendors/test_alpha_vantage.py`

- [ ] **Step 1: Write failing gate and retry tests**

Cover these public interfaces:

```python
policy = AlphaVantageRetryPolicy(attempts=3, base_seconds=2, max_seconds=5)
assert policy.delay(1) == 2
assert policy.delay(2) == 4
assert policy.delay(3, retry_after=9) == 5

first = CrossProcessRateGate(tmp_path / "alpha.json", 60, clock=clock, sleep=sleep)
second = CrossProcessRateGate(tmp_path / "alpha.json", 60, clock=clock, sleep=sleep)
first.acquire()
second.acquire()
assert sleeps == [1.0]

assert classify_alpha_payload({"Note": "API call frequency exceeded"}) == "rate_limit"
assert classify_alpha_payload({"Error Message": "temporary upstream error"}) == "transient"
assert classify_alpha_payload({"Error Message": "invalid API key"}) == "authentication"
```

- [ ] **Step 2: Run RED**

```bash
.venv/bin/pytest platform/tests/unit/vendors/test_alpha_vantage.py -q
```

Expected: import failure because the vendor coordination package is absent.

- [ ] **Step 3: Implement the gate and policy**

Use `fcntl.flock` on a stable lock file. While locked, read `next_allowed_at`, reserve `max(now, next_allowed_at)`, write the next slot with `os.replace`, release the lock, then sleep outside the lock. `defer(seconds)` must move the shared timestamp forward. Corrupt or missing JSON starts from the current time.

Expose a key fingerprint helper using a truncated SHA-256 digest and never the key itself. Classify only known response keys and sanitized message text.

- [ ] **Step 4: Run GREEN and commit**

Run the Task 2 test and `ruff check` for the new package before committing.

### Task 3: Guard TradingAgents Alpha calls and replace the Yahoo verification snapshot

**Files:**
- Modify: `platform/src/tradingng_platform/runner/contracts.py`
- Modify: `platform/src/tradingng_platform/worker/service.py`
- Modify: `platform/src/tradingng_platform/runner/tradingagents.py`
- Modify: `platform/tests/unit/worker/test_service.py`
- Modify: `platform/tests/unit/runner/test_runner.py`

- [ ] **Step 1: Write failing runner tests**

Add tests that construct an Alpha-only `RunnerInput`, install the run context, and prove:

```python
responses = [AlphaVantageRateLimitError("limited"), "time,MACD\n2026-07-24,1.2\n"]
assert guarded_request("MACD", params).startswith("time,MACD")
assert alpha_calls == 2
assert sleeps == [configured_delay]
assert yahoo_calls == 0
```

Inject an Alpha daily-adjusted CSV into the verification loader and assert its columns are exactly `Date`, `Open`, `High`, `Low`, `Close`, `Volume`, its rows stop at the analysis date, and no yfinance function is invoked. Add old-snapshot compatibility coverage in `test_service.py`.

- [ ] **Step 2: Run RED**

```bash
.venv/bin/pytest platform/tests/unit/runner/test_runner.py platform/tests/unit/worker/test_service.py -q
```

Expected: failures because the guard, policy fields and Alpha loader are absent.

- [ ] **Step 3: Implement the external run context**

Add retry-policy fields to `RunnerInput`. In `build_runner_input`, read the frozen policy and fall back to current defaults for old snapshots.

Create a context manager in the TradingNG runner that temporarily replaces `_make_api_request` in the four imported Alpha modules. The wrapper must acquire the shared gate, retry typed rate limits, HTTP 429 and classified transient JSON responses, and return valid CSV/JSON unchanged. Restore every original in `finally`.

Within the same context replace `market_data_validator.load_ohlcv` with an Alpha loader that requests daily adjusted CSV through the guarded request, renames raw fields to the standard OHLCV columns, filters `Date <= curr_date`, and raises a clear error for an empty frame. Nest this context around the existing point-in-time guard.

- [ ] **Step 4: Run GREEN and commit**

Run the focused tests and confirm `git diff --name-only HEAD -- TradingAgents` is empty before committing.

### Task 4: Make validation Alpha-only and retry the same provider

**Files:**
- Modify: `platform/src/tradingng_platform/validation/providers.py`
- Modify: `platform/src/tradingng_platform/validation/main.py`
- Modify: `platform/tests/unit/validation/test_providers.py`

- [ ] **Step 1: Write failing async retry tests**

Use `httpx.MockTransport` to return a 429 or `{"Note": "call frequency"}` followed by valid daily data. Assert two Alpha calls, one delayed retry and an Alpha result. Build settings with both provider names and a configured Alpha key, then assert the router contains only `alphavantage`.

Add a legacy adapter test that converts `ProviderPriceSeries` into the v1 `PriceSeries` without invoking yfinance.

- [ ] **Step 2: Run RED**

```bash
.venv/bin/pytest platform/tests/unit/validation/test_providers.py -q
```

Expected: the router still contains Yahoo and Alpha rate limits surface immediately.

- [ ] **Step 3: Implement delayed retries and the v1 adapter**

Inject the shared gate, retry policy and async sleep into `AlphaVantagePriceProvider`. Retry only rate-limit/transient conditions; authentication and invalid symbols remain terminal. Have `build_price_provider` iterate `effective_validation_price_providers`.

Add `LegacyPriceProviderAdapter`, mapping all price arrays and `provider_id` into the v1 contract. In validation main, build one effective router and pass the adapter as the v1 provider and the router as v2.

- [ ] **Step 4: Run GREEN and commit**

Run the Task 4 test and validation worker tests before committing.

### Task 5: Finalize run-step projections and repair existing rows

**Files:**
- Modify: `platform/src/tradingng_platform/worker/repository.py`
- Create: `platform/tests/unit/worker/test_repository_steps.py`
- Create: `platform/migrations/versions/20260727_0008_finalize_run_steps.py`

- [ ] **Step 1: Write failing lifecycle tests**

Use an in-memory async SQLite database and create a run. Persist stage events for analysts and research, then a terminal result. Assert the former step closes on transition and the latter closes on result. Independently verify failure and cancellation change remaining steps to `failed` and `cancelled` with `finished_at` populated.

- [ ] **Step 2: Run RED**

```bash
.venv/bin/pytest platform/tests/unit/worker/test_repository_steps.py -q
```

Expected: all created steps remain `running`.

- [ ] **Step 3: Implement transactional step closure**

Add a repository helper equivalent to:

```python
async def _finish_running_steps(self, run, status, *, error_code=None):
    await self.session.execute(
        update(RunStep)
        .where(RunStep.run_id == run.id, RunStep.attempt == run.attempt, RunStep.status == "running")
        .values(status=status, finished_at=datetime.now(timezone.utc), error_code=error_code)
    )
```

Call it before entering a new stage, on `result`, and in failure/cancellation finalizers. Do not close the newly entered current stage.

- [ ] **Step 4: Add the repair migration**

Migration `20260727_0008` joins terminal assessment runs and updates only steps still marked `running`: succeeded to `completed`, failed to `failed`, cancelled to `cancelled`; set `finished_at` from the run and copy the failure code only for failed rows. Downgrade is intentionally a no-op because the original false running state cannot be reconstructed.

- [ ] **Step 5: Run GREEN and commit**

Run lifecycle tests plus migration/config deployment tests, then commit.

### Task 6: Documentation, full verification and deployment

**Files:**
- Modify: `.env.platform.example`
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Runtime: `.env.platform`, MySQL migration, user services

- [ ] **Step 1: Document the new semantics**

Document that configured Alpha keys make the relevant path exclusive, Yahoo is not a rate-limit fallback, retries use the shared per-key gate, and missing price targets are kept explicit rather than inferred.

- [ ] **Step 2: Configure the current deployment**

Set current non-secret routing values to Alpha-only and add retry values without changing or printing keys. Run Alembic upgrade before service restart.

- [ ] **Step 3: Run complete verification**

```bash
.venv/bin/ruff check platform/src platform/tests
.venv/bin/pytest platform/tests/unit -q
.venv/bin/pytest platform/tests/integration -q
./scripts/verify_platform.sh
git diff --check
git diff --name-only HEAD -- TradingAgents
```

Expected: zero lint/test/platform verification failures and no TradingAgents changes.

- [ ] **Step 4: Restart and verify runtime behavior**

Restart API, scheduler, validation and all worker instances without stopping Gateway or Caddy. Require all units and health endpoints active. Query the JPM run and require all five steps to be `completed`.

Perform one direct guarded MACD request and require a CSV `time,MACD,...` header. Inspect fresh scheduler metadata and the validation router: both must contain Alpha only and no Yahoo. Verify the 24 existing JPM artifact hashes remain unchanged.

- [ ] **Step 5: Commit implementation**

Commit only reviewed source, tests, migration and public documentation. Do not commit `.env.platform`, job data, artifacts or rate-gate runtime files.

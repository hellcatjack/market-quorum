# Alpha Vantage Default and Assessment Reset Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Alpha Vantage the primary research vendor for every new platform assessment and remove all existing assessment-domain database and filesystem records without touching identities or system configuration.

**Architecture:** Add an ordered research vendor chain to `TradingNG` settings, then let the scheduler overlay that chain onto the four Alpha-Vantage-capable TradingAgents categories when it builds immutable run metadata. Perform the one-time reset with API and assessment writers stopped, an ordered transactional database delete, and explicit deletion of only the project artifact and job directory contents.

**Tech Stack:** Python 3.10, Pydantic Settings, SQLAlchemy async, MySQL, pytest, systemd user services.

---

## Constraints

- Work directly on `main`; do not create a worktree or use subagents.
- Do not modify any file below `TradingAgents/`.
- Preserve users, roles, credentials, instruments, scheduler policy, health samples and system services.
- Keep FRED for macro data and Polymarket for prediction markets.
- Do not print either Alpha Vantage secret.
- Finish with zero active assessment records, artifacts and job directories.

## File map

- Modify `platform/src/tradingng_platform/config.py`: parse and validate the ordered research vendor chain.
- Modify `platform/src/tradingng_platform/scheduler/main.py`: overlay the configured chain onto supported categories and freeze it into execution metadata.
- Modify `platform/tests/unit/test_config.py`: prove defaults, ordering and invalid input rejection.
- Modify `platform/tests/unit/scheduler/test_main.py`: prove only supported categories are overlaid.
- Modify `.env.platform.example`: document the deployment variable.
- Modify `README.md` and `README.zh-CN.md`: explain research versus validation Alpha Vantage configuration.
- Runtime only: assessment-domain MySQL rows and contents below `var/artifacts` and `var/jobs`.

### Task 1: Pin configuration behavior with failing tests

**Files:**
- Modify: `platform/tests/unit/test_config.py`

- [ ] **Step 1: Add the default and explicit-order assertions**

Extend the canonical defaults test with:

```python
assert settings.research_data_vendor_chain == ("alpha_vantage", "yfinance")
```

Add:

```python
def test_research_vendor_chain_preserves_explicit_order(monkeypatch):
    monkeypatch.setenv(
        "TRADINGNG_RESEARCH_DATA_VENDOR_CHAIN",
        "yfinance,alpha_vantage",
    )
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://tradingng:test@127.0.0.1/tradingng",
    )
    assert settings.research_data_vendor_chain == ("yfinance", "alpha_vantage")
```

- [ ] **Step 2: Add invalid chain coverage**

```python
@pytest.mark.parametrize(
    "value",
    ["alpha_vantage,alpha_vantage", "unknown,yfinance", ""],
)
def test_research_vendor_chain_rejects_invalid_values(monkeypatch, value):
    monkeypatch.setenv("TRADINGNG_RESEARCH_DATA_VENDOR_CHAIN", value)
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            database_url="postgresql+psycopg://tradingng:test@127.0.0.1/tradingng",
        )
```

- [ ] **Step 3: Run the focused tests and observe RED**

Run:

```bash
.venv/bin/pytest platform/tests/unit/test_config.py -q
```

Expected: failures because `Settings` does not define `research_data_vendor_chain`.

### Task 2: Implement the validated research chain

**Files:**
- Modify: `platform/src/tradingng_platform/config.py`
- Test: `platform/tests/unit/test_config.py`

- [ ] **Step 1: Define the setting**

Add beside the validation provider settings:

```python
research_data_vendor_chain: Annotated[tuple[str, ...], NoDecode] = (
    "alpha_vantage",
    "yfinance",
)
```

- [ ] **Step 2: Parse and validate it**

Include `research_data_vendor_chain` in the existing comma-separated tuple parser and add a validator that normalizes to lower case, requires a non-empty unique sequence, and only accepts `alpha_vantage` or `yfinance`.

- [ ] **Step 3: Run the focused tests and observe GREEN**

Run:

```bash
.venv/bin/pytest platform/tests/unit/test_config.py -q
```

Expected: all configuration tests pass.

### Task 3: Pin scheduler metadata behavior with a failing test

**Files:**
- Modify: `platform/tests/unit/scheduler/test_main.py`

- [ ] **Step 1: Add the metadata overlay test**

```python
from tradingng_platform.config import Settings


def test_execution_metadata_prefers_configured_research_chain(monkeypatch):
    monkeypatch.setattr(main, "_commit", lambda path: path.name or "root")
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://tradingng:test@127.0.0.1/tradingng",
        research_data_vendor_chain=("alpha_vantage", "yfinance"),
    )

    metadata = main._execution_metadata(settings)

    for category in (
        "core_stock_apis",
        "technical_indicators",
        "fundamental_data",
        "news_data",
    ):
        assert metadata.data_vendors[category] == "alpha_vantage,yfinance"
    assert metadata.data_vendors["macro_data"] == "fred"
    assert metadata.data_vendors["prediction_markets"] == "polymarket"
```

- [ ] **Step 2: Run the scheduler test and observe RED**

Run:

```bash
.venv/bin/pytest platform/tests/unit/scheduler/test_main.py -q
```

Expected: failure because `_execution_metadata` does not accept settings and still copies the TradingAgents yfinance defaults.

### Task 4: Overlay the configured chain outside TradingAgents

**Files:**
- Modify: `platform/src/tradingng_platform/scheduler/main.py`
- Test: `platform/tests/unit/scheduler/test_main.py`

- [ ] **Step 1: Declare the supported categories**

```python
_ALPHA_VANTAGE_RESEARCH_CATEGORIES = (
    "core_stock_apis",
    "technical_indicators",
    "fundamental_data",
    "news_data",
)
```

- [ ] **Step 2: Accept settings and construct an isolated overlay**

Change `_execution_metadata` to accept `settings: Settings`, copy `DEFAULT_CONFIG["data_vendors"]`, join `settings.research_data_vendor_chain` with commas, and replace only the four supported category values. Keep tool-level overrides and source commit fingerprints unchanged.

- [ ] **Step 3: Pass the scheduler settings instance**

In `run_scheduler`, replace:

```python
metadata = _execution_metadata()
```

with:

```python
metadata = _execution_metadata(settings)
```

- [ ] **Step 4: Run both focused suites**

Run:

```bash
.venv/bin/pytest platform/tests/unit/test_config.py platform/tests/unit/scheduler/test_main.py -q
```

Expected: all tests pass.

### Task 5: Document the two Alpha Vantage paths

**Files:**
- Modify: `.env.platform.example`
- Modify: `README.md`
- Modify: `README.zh-CN.md`

- [ ] **Step 1: Add the research variable example**

```dotenv
ALPHA_VANTAGE_API_KEY=
TRADINGNG_RESEARCH_DATA_VENDOR_CHAIN=alpha_vantage,yfinance
```

Keep the separate validation variables unchanged.

- [ ] **Step 2: Explain the separation**

Document that `ALPHA_VANTAGE_API_KEY` is consumed by TradingAgents research workers, while `TRADINGNG_ALPHA_VANTAGE_API_KEY` is consumed by outcome validation. State that new run snapshots prefer Alpha Vantage for core prices, technical indicators, fundamentals and news, with yfinance fallback.

- [ ] **Step 3: Run static and platform regression checks**

Run:

```bash
.venv/bin/ruff check platform/src platform/tests
.venv/bin/pytest platform/tests/unit platform/tests/integration -q
git diff --check
```

Expected: zero lint errors, zero test failures and no whitespace errors.

### Task 6: Stop writers and transactionally clear assessment records

**Runtime targets:**
- MySQL assessment-domain rows
- `/app/devs/TradingNG/var/artifacts` contents
- `/app/devs/TradingNG/var/jobs` contents

- [ ] **Step 1: Stop all assessment writers**

```bash
systemctl --user stop \
  tradingng-platform-workers.target \
  tradingng-platform-scheduler.service \
  tradingng-platform-validation.service \
  tradingng-platform-api.service
```

- [ ] **Step 2: Delete assessment rows in one SQLAlchemy transaction**

Delete in this dependency order:

```text
webhook_deliveries
validations
decision_price_bases
evidence_items
reviews
comments
decisions
worker_leases
run_steps
artifacts
run_events
assessment_runs (clear retry_of_run_id first)
assessment_requests
assessment_batches
run_config_snapshots
assessment-related audit_events
```

Assert every delete target count is zero before committing the transaction. Do not delete `instruments`, identities, policies or health samples.

- [ ] **Step 3: Delete only the two resolved directory contents**

After the transaction succeeds, confirm both directories resolve below `/app/devs/TradingNG/var`, then remove their descendants without removing the directory roots.

### Task 7: Restart and prove the clean Alpha Vantage state

- [ ] **Step 1: Start services**

```bash
systemctl --user start \
  tradingng-platform-api.service \
  tradingng-platform-scheduler.service \
  tradingng-platform-workers.target \
  tradingng-platform-validation.service
```

- [ ] **Step 2: Verify runtime state**

Require:

- API, scheduler, worker target, validation and Gateway services are active;
- assessment-domain database tables contain zero rows;
- artifact files and job directories contain zero entries;
- users, roles, scheduler policy and instruments still exist;
- `main._execution_metadata(Settings())` reports `alpha_vantage,yfinance` for the four supported categories;
- configured research and validation Alpha Vantage keys are present without printing them.

- [ ] **Step 3: Verify repository boundaries and commit**

Run:

```bash
git diff --check
git status --short
git diff --name-only HEAD -- TradingAgents
```

Expected: no TradingAgents file changes. Commit the implementation and documentation on `main`.

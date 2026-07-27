# TSLA Monthly Continuous Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute and audit 12 chronological, deep TSLA assessments with point-in-time historical memory and fully matured Alpha Vantage outcome validation, repairing any discovered platform defect without modifying TradingAgents.

**Architecture:** A small resumable operations harness drives the existing bearer-authenticated REST API and stores only secret-free checkpoint state under `var/tsla-monthly-audit/`. Pure validation functions enforce the per-run quality gates and receive unit tests before the HTTP orchestration is implemented. Live runs are serialized so a later run snapshot can include only earlier outcomes that were already knowable at its analysis date.

**Tech Stack:** Python 3.10, httpx, pydantic/datatypes, pytest, FastAPI REST API, MySQL, systemd, Alpha Vantage, Codex Gateway

---

## File structure

- Create `scripts/tsla_monthly_audit.py`: fixed audit schedule, resumable state, Keycloak client-credentials authentication, REST polling, checkpoint validation, and secret-free JSON output.
- Create `platform/tests/operations/test_tsla_monthly_audit.py`: pure schedule, no-lookahead memory, Alpha-only routing, terminal-step, validation, and resume-state tests.
- Create `docs/reports/2026-07-27-tsla-monthly-audit.md`: final engineering results and monthly outcome table after all live checkpoints finish.
- Never modify `TradingAgents/`; runtime work stays in existing `var/` paths.

### Task 1: Define the resumable audit contract with failing tests

**Files:**
- Create: `platform/tests/operations/test_tsla_monthly_audit.py`
- Create after RED: `scripts/tsla_monthly_audit.py`

- [ ] **Step 1: Write the failing schedule and checkpoint tests**

Define the exact 12 dates and exercise a wished-for pure API:

```python
from datetime import date
from tsla_monthly_audit import AUDIT_DATES, validate_checkpoint


def test_schedule_has_one_session_per_month_and_a_mature_final_cutoff():
    assert AUDIT_DATES == (
        date(2025, 7, 31), date(2025, 8, 29), date(2025, 9, 30),
        date(2025, 10, 31), date(2025, 11, 28), date(2025, 12, 31),
        date(2026, 1, 30), date(2026, 2, 27), date(2026, 3, 31),
        date(2026, 4, 30), date(2026, 5, 29), date(2026, 6, 25),
    )
    assert len({(item.year, item.month) for item in AUDIT_DATES}) == 12


def test_checkpoint_rejects_yahoo_or_lookahead_memory(valid_checkpoint):
    invalid = deepcopy(valid_checkpoint)
    invalid["run"]["data_vendors"]["news_data"] = "alpha_vantage,yfinance"
    with pytest.raises(AuditFailure, match="exclusive"):
        validate_checkpoint(invalid)
    invalid = deepcopy(valid_checkpoint)
    invalid["run"]["memory"]["sources"][0]["exit_session"] = "2025-08-29"
    with pytest.raises(AuditFailure, match="look-ahead"):
        validate_checkpoint(invalid)
```

Add independent tests requiring five completed timestamped steps, three completed
`validation.v2` rows with horizons `{1, 5, 20}` and provider `alphavantage`, a
nonempty decision, at most five distinct earlier memory sources, and nonempty
artifact metadata.

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
PYTHONPATH=platform/src:scripts .venv/bin/pytest \
  platform/tests/operations/test_tsla_monthly_audit.py -q
```

Expected: collection fails because `tsla_monthly_audit` does not exist.

- [ ] **Step 3: Implement the minimal pure contract**

Create immutable `AUDIT_DATES`, `AuditFailure`, `validate_checkpoint`,
`load_state`, and `save_state`. `save_state` must atomically replace the JSON
file and serialize only run IDs, dates, public statuses, metrics, and hashes. It
must never receive or persist the bearer token or client secret.

The validator must explicitly check:

```python
ALPHA_RESEARCH_CATEGORIES = {
    "core_stock_apis", "technical_indicators", "fundamental_data", "news_data"
}
assert all(data_vendors[name] == "alpha_vantage" for name in ALPHA_RESEARCH_CATEGORIES)
assert {item["horizon"] for item in validations} == {1, 5, 20}
assert all(item["provider_id"] == "alphavantage" for item in validations)
assert all(source["exit_session"] < run["analysis_date"] for source in memory_sources)
```

- [ ] **Step 4: Verify GREEN and commit**

Run the focused test until all cases pass, then:

```bash
git add scripts/tsla_monthly_audit.py platform/tests/operations/test_tsla_monthly_audit.py
git commit -m "test: add resumable TSLA monthly audit harness"
```

### Task 2: Add REST orchestration with test-first HTTP boundaries

**Files:**
- Modify: `scripts/tsla_monthly_audit.py`
- Modify: `platform/tests/operations/test_tsla_monthly_audit.py`

- [ ] **Step 1: Write failing fake-transport tests**

Use `httpx.MockTransport` to require the client to:

- obtain a client-credentials token without placing it in saved state;
- refresh the in-memory token once after a `401` response, because one deep
  assessment can outlive the Keycloak access-token lifetime;
- submit exactly one item to `/api/v1/assessments` with `deep`, `historical`, all
  four analysts, Chinese output, and a deterministic per-date idempotency key;
- poll run detail, steps, decision, artifacts, and validations;
- resume an existing run ID instead of submitting a duplicate;
- stop at the first failed assessment or terminal validation.

Expected RED: `AuditApiClient` and `run_checkpoint` are not defined.

- [ ] **Step 2: Implement the minimal HTTP client and orchestrator**

Implement `AuditApiClient` around an injected `httpx.Client`. Read
`TRADINGNG_API_CLIENT_SECRET` from `.env.platform` only at process startup,
exchange it at the local Keycloak endpoint, keep the token in memory, and send it
only in the Authorization header. Refresh once and replay the request after a
`401`; never serialize either token. Implement bounded network retries for GET
polling; do not automatically retry a non-idempotent POST except by reusing the
same idempotency key and then listing TSLA runs to recover its run ID.

Poll every 10 seconds. Assessment timeout is 90 minutes and validation timeout is
20 minutes per checkpoint. Persist state after submission and after every
terminal transition.

- [ ] **Step 3: Verify focused GREEN and commit**

```bash
PYTHONPATH=platform/src:scripts .venv/bin/pytest \
  platform/tests/operations/test_tsla_monthly_audit.py -q
git add scripts/tsla_monthly_audit.py platform/tests/operations/test_tsla_monthly_audit.py
git commit -m "feat: orchestrate monthly assessment audit"
```

### Task 3: Run the 12 live chronological checkpoints

**Files:**
- Runtime state: `var/tsla-monthly-audit/state.json`

- [ ] **Step 1: Capture the production preflight**

Verify branch cleanliness, no unrelated active assessments, API/Gateway readiness,
effective Alpha-only provider routes, current scheduler capacity, and current
Alpha latest session. Abort before submission if a circuit is open or if Yahoo
appears in an effective run snapshot.

- [ ] **Step 2: Execute the resumable audit**

```bash
PYTHONPATH=platform/src:scripts .venv/bin/python scripts/tsla_monthly_audit.py \
  --env-file .env.platform \
  --state-dir var/tsla-monthly-audit
```

Run dates in ascending order. For each date, record the run ID, elapsed time,
decision, memory source identities, validation outcomes, provider adapter,
artifact hashes, and quality-gate result. Continue automatically only after the
checkpoint passes.

- [ ] **Step 3: Gather component-boundary evidence on any failure**

Before proposing a fix, collect the API error envelope, run events, step rows,
Worker journal for the run ID, Gateway journal request IDs, immutable snapshot,
provider error code, validation state, and relevant artifact. State one concrete
root-cause hypothesis and test it minimally.

### Task 4: Repair defects with strict TDD when required

**Files:**
- Test: the narrowest existing `platform/tests/...` or `gateway/tests/...` module
- Modify: only the responsible TradingNG or Gateway module

- [ ] **Step 1: Add a minimal regression test reproducing the observed failure**

Run it and confirm it fails for the observed reason rather than fixture setup.

- [ ] **Step 2: Implement one root-cause fix outside `TradingAgents/`**

Do not combine unrelated cleanup. Re-run the focused test and the directly
affected suite.

- [ ] **Step 3: Restart only affected systemd services and retry the same date**

Do not delete the failed evidence. Use the platform retry endpoint or a new
deterministic idempotency version only when the run state makes that necessary.
Continue the chain after the repaired checkpoint passes.

### Task 5: Aggregate and verify the yearly audit

**Files:**
- Create: `docs/reports/2026-07-27-tsla-monthly-audit.md`

- [ ] **Step 1: Generate the monthly results table**

Include analysis date, rating, price target status, 20-session raw return and
alpha, direction correctness, MAE/MFE, memory source count/horizon, elapsed time,
Gateway request count, retries, and final quality status. Explicitly separate
engineering reliability from investment accuracy.

- [ ] **Step 2: Run complete verification**

```bash
./scripts/verify_platform.sh
PYTHONPATH=platform/src:scripts .venv/bin/python scripts/tsla_monthly_audit.py \
  --env-file .env.platform --state-dir var/tsla-monthly-audit --verify-only
```

Expected: complete repository verification exits zero; the audit reports 12
successful assessments, 36 completed Alpha validations, no stale steps, no
look-ahead memory, and no artifact hash failures.

- [ ] **Step 3: Commit the report and final fixes**

```bash
git add docs/reports/2026-07-27-tsla-monthly-audit.md
git commit -m "docs: report TSLA monthly assessment audit"
```

Confirm `git status --porcelain` is empty, all platform units are active, and no
assessment remains queued or active before reporting completion.

# Point-in-Time Integrity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add fail-closed historical financial-data filtering, immutable run-integrity audits, retrospective quarantine, safe-memory/statistics gates, clean reassessment, and REST/MCP/Web visibility without modifying `TradingAgents/`.

**Architecture:** A new `integrity` package owns versioned policy contracts, financial availability resolution, persistence, and retrospective audit. The platform runner wraps vendored data routes at runtime and emits an integrity artifact; worker finalization persists it beside the original immutable report. Consumers join the latest supported integrity verdict and only treat `safe` runs as eligible by default.

**Tech Stack:** Python 3.10+, Pydantic 2, SQLAlchemy 2 async, Alembic, httpx, FastAPI, FastMCP, React 19, TypeScript, TanStack Query, Vitest, pytest.

---

## File structure

New focused files:

- `platform/src/tradingng_platform/integrity/contracts.py`: enums and Pydantic documents shared by runner, persistence, REST and MCP.
- `platform/src/tradingng_platform/integrity/policy.py`: policy registry, audit recorder, status aggregation, temporal evidence metadata.
- `platform/src/tradingng_platform/integrity/financials.py`: SEC/Alpha availability records and fail-closed statement filtering.
- `platform/src/tradingng_platform/integrity/repository.py`: latest-verdict queries, summary counts and immutable persistence.
- `platform/src/tradingng_platform/integrity/service.py`: authorization, clean reassessment and API-facing operations.
- `platform/src/tradingng_platform/integrity/audit.py`: sealed-evidence retrospective auditor.
- `platform/src/tradingng_platform/integrity/main.py`: resumable audit CLI.
- `platform/src/tradingng_platform/models/integrity.py`: persistence models.
- `platform/src/tradingng_platform/api/routes/integrity.py`: REST routes.
- `web/src/features/runs/IntegrityPanel.tsx`: compact bilingual detail panel.

Existing files remain responsible for their current layer and receive only narrow integration changes. No file under `TradingAgents/` is modified.

### Task 1: Define versioned integrity contracts and policy aggregation

**Files:**
- Create: `platform/src/tradingng_platform/integrity/__init__.py`
- Create: `platform/src/tradingng_platform/integrity/contracts.py`
- Create: `platform/src/tradingng_platform/integrity/policy.py`
- Test: `platform/tests/unit/integrity/test_policy.py`

- [ ] **Step 1: Write failing contract and aggregation tests**

```python
from datetime import date, datetime, timezone

from tradingng_platform.integrity.contracts import IntegrityStatus
from tradingng_platform.integrity.policy import PointInTimeRecorder


def test_at_risk_dominates_unknown_and_safe():
    recorder = PointInTimeRecorder(date(2025, 7, 1), now=datetime(2026, 7, 27, tzinfo=timezone.utc))
    recorder.record("get_stock_data", IntegrityStatus.SAFE, "date_bounded")
    recorder.record("get_unknown_data", IntegrityStatus.UNKNOWN, "unregistered_tool")
    recorder.record("get_income_statement", IntegrityStatus.AT_RISK, "future_publication")

    document = recorder.finalize()

    assert document.policy_version == "point-in-time.v1"
    assert document.status is IntegrityStatus.AT_RISK
    assert [item.reason_code for item in document.findings] == [
        "date_bounded",
        "unregistered_tool",
        "future_publication",
    ]
    assert len(document.input_fingerprint) == 64


def test_historical_run_without_observed_tools_is_unknown():
    recorder = PointInTimeRecorder(date(2025, 7, 1), now=datetime(2026, 7, 27, tzinfo=timezone.utc))
    assert recorder.finalize().status is IntegrityStatus.UNKNOWN


def test_live_run_is_safe_and_explicitly_scoped():
    now = datetime(2026, 7, 27, 15, tzinfo=timezone.utc)
    document = PointInTimeRecorder(now.date(), now=now).finalize()
    assert document.status is IntegrityStatus.SAFE
    assert document.temporal_scope == "contemporaneous"
```

- [ ] **Step 2: Run the new test and verify the import fails**

Run: `cd platform && pytest tests/unit/integrity/test_policy.py -v`

Expected: FAIL with `ModuleNotFoundError: tradingng_platform.integrity`.

- [ ] **Step 3: Implement strict contracts and deterministic aggregation**

```python
class IntegrityStatus(str, Enum):
    SAFE = "safe"
    AT_RISK = "at_risk"
    UNKNOWN = "unknown"


class IntegrityFinding(BaseModel):
    tool_name: str
    status: IntegrityStatus
    reason_code: str
    details: dict = Field(default_factory=dict)


class IntegrityDocument(BaseModel):
    policy_version: Literal["point-in-time.v1"] = "point-in-time.v1"
    status: IntegrityStatus
    temporal_scope: Literal["contemporaneous", "historical_reconstruction"]
    analysis_date: date
    checked_at: datetime
    findings: tuple[IntegrityFinding, ...]
    input_fingerprint: str
```

`PointInTimeRecorder.finalize()` serializes the analysis date, temporal scope and ordered findings with canonical JSON, hashes them with SHA-256, and applies precedence `at_risk > unknown > safe`. Historical runs with no findings become `unknown/no_observed_tools`; same-date runs are `safe/live_current_snapshot` with `temporal_scope=contemporaneous`.

- [ ] **Step 4: Run policy tests and the runner unit suite**

Run: `cd platform && pytest tests/unit/integrity/test_policy.py tests/unit/runner/test_runner.py -v`

Expected: all tests PASS.

- [ ] **Step 5: Commit the policy contracts**

```bash
git add platform/src/tradingng_platform/integrity platform/tests/unit/integrity/test_policy.py
git commit -m "feat: define point-in-time integrity policy"
```

### Task 2: Resolve actual financial-statement availability and fail closed

**Files:**
- Create: `platform/src/tradingng_platform/integrity/financials.py`
- Modify: `platform/src/tradingng_platform/config.py`
- Modify: `platform/src/tradingng_platform/runner/contracts.py`
- Modify: `platform/src/tradingng_platform/worker/service.py`
- Modify: `platform/src/tradingng_platform/worker/main.py`
- Test: `platform/tests/unit/integrity/test_financials.py`
- Test: `platform/tests/unit/test_config.py`
- Test: `platform/tests/unit/worker/test_service.py`

- [ ] **Step 1: Write failing availability and filtering tests**

```python
def test_statement_after_analysis_date_is_removed():
    resolver = StubResolver({date(2025, 6, 30): Availability(date(2025, 7, 24), "sec", "high")})
    result, findings = filter_statement_payload(
        PAYLOAD_WITH_2025_Q2,
        ticker="NVDA",
        analysis_date=date(2025, 7, 1),
        statement_kind="income_statement",
        resolver=resolver,
    )
    assert json.loads(result)["quarterlyReports"] == []
    assert findings[0].reason_code == "future_publication"


def test_statement_available_on_analysis_date_is_retained():
    resolver = StubResolver({date(2025, 6, 30): Availability(date(2025, 7, 24), "sec", "high")})
    result, findings = filter_statement_payload(
        PAYLOAD_WITH_2025_Q2,
        ticker="NVDA",
        analysis_date=date(2025, 7, 24),
        statement_kind="income_statement",
        resolver=resolver,
    )
    assert len(json.loads(result)["quarterlyReports"]) == 1
    assert findings[0].reason_code == "publication_verified"


def test_missing_availability_is_removed_instead_of_using_fiscal_end():
    result, findings = filter_statement_payload(
        PAYLOAD_WITH_2025_Q2,
        ticker="NVDA",
        analysis_date=date(2025, 9, 1),
        statement_kind="cashflow",
        resolver=StubResolver({}),
    )
    assert json.loads(result)["quarterlyReports"] == []
    assert findings[0].status is IntegrityStatus.UNKNOWN
    assert findings[0].reason_code == "publication_unverified"
```

Add SEC fixture tests that map `reportDate=2025-06-30`, `form=10-Q`, `filingDate=2025-07-24`, reject amendments as the original availability date, reject ambiguous ticker mappings, and accept Alpha `reportedDate` only for one valid quarterly match.

- [ ] **Step 2: Run and verify the financial module is missing**

Run: `cd platform && pytest tests/unit/integrity/test_financials.py -v`

Expected: FAIL importing `tradingng_platform.integrity.financials`.

- [ ] **Step 3: Implement the resolver boundary and pure filter**

```python
@dataclass(frozen=True)
class Availability:
    available_at: date
    source: Literal["sec", "alpha_vantage_earnings"]
    assurance: Literal["high", "medium"]


class FilingAvailabilityResolver(Protocol):
    def resolve(self, ticker: str, fiscal_end: date, frequency: str) -> Availability | None: ...


def filter_statement_payload(
    payload_text: str,
    *,
    ticker: str,
    analysis_date: date,
    statement_kind: str,
    resolver: FilingAvailabilityResolver,
) -> tuple[str, tuple[IntegrityFinding, ...]]:
    # Parse only dictionaries with annualReports/quarterlyReports.
    # Retain a row only when a resolver date exists and is <= analysis_date.
    # Invalid schemas return a legal empty payload and unknown_schema finding.
```

Implement `SecFilingClient` with injected `httpx.Client`, SEC ticker/submissions endpoints, explicit User-Agent, timeout, bounded disk cache and exact report-date/form matching. Implement `CompositeAvailabilityResolver` with SEC first and an injected Alpha EARNINGS loader second. The Alpha fallback consumes only `fiscalDateEnding` and `reportedDate`; EPS values never enter the filtered statement output or audit artifact.

- [ ] **Step 4: Add configuration and runner-input propagation**

Add validated settings:

```python
sec_user_agent: str = "MarketQuorum/0.1 (+https://ushome.amycat.com)"
sec_request_timeout_seconds: float = Field(default=10, ge=1, le=60)

@computed_field
@property
def sec_cache_dir(self) -> Path:
    return self.data_dir / "vendor-cache" / "sec"
```

Add the same runtime values to `RunnerInput`, pass them through `build_runner_input`, `WorkerService`, and `worker.main`. Do not include credentials in snapshots or artifacts.

- [ ] **Step 5: Run focused tests**

Run: `cd platform && pytest tests/unit/integrity/test_financials.py tests/unit/test_config.py tests/unit/worker/test_service.py -v`

Expected: all tests PASS.

- [ ] **Step 6: Commit the availability resolver**

```bash
git add platform/src/tradingng_platform/integrity/financials.py platform/src/tradingng_platform/config.py platform/src/tradingng_platform/runner/contracts.py platform/src/tradingng_platform/worker/service.py platform/src/tradingng_platform/worker/main.py platform/tests/unit/integrity/test_financials.py platform/tests/unit/test_config.py platform/tests/unit/worker/test_service.py
git commit -m "feat: resolve historical statement availability"
```

### Task 3: Integrate the policy with the external TradingAgents runner

**Files:**
- Modify: `platform/src/tradingng_platform/runner/tradingagents.py`
- Modify: `platform/src/tradingng_platform/runner/callbacks.py`
- Modify: `platform/tests/unit/runner/test_runner.py`
- Modify: `platform/tests/unit/runner/test_events.py`

- [ ] **Step 1: Extend the historical runner test to require publication filtering and restoration**

```python
def test_historical_runner_filters_financial_statements_by_publication_and_restores_routes(...):
    monkeypatch.setitem(VENDOR_METHODS["get_income_statement"], "alpha_vantage", original_route)
    runner_input = _runner_input(tmp_path).model_copy(update={"analysis_date": date(2025, 7, 1)})
    resolver = StubResolver({date(2025, 6, 30): Availability(date(2025, 7, 24), "sec", "high")})

    TradingAgentsRunner(runner_input, graph_factory=_FinancialProbeGraph, availability_resolver=resolver).run()

    assert json.loads(_FinancialProbeGraph.observed_tools["get_income_statement"])["quarterlyReports"] == []
    assert VENDOR_METHODS["get_income_statement"]["alpha_vantage"] is original_route
    audit = json.loads((runner_input.work_dir / "working/point_in_time_integrity.json").read_text())
    assert audit["status"] == "at_risk"
    assert audit["findings"][0]["reason_code"] == "future_publication"
```

Add callback tests asserting evidence rows now contain deterministic `effective_at` and `freshness` for date-bounded prices/news/FRED and `point_in_time_filtered` statements.

- [ ] **Step 2: Run and verify RED**

Run: `cd platform && pytest tests/unit/runner/test_runner.py tests/unit/runner/test_events.py -v`

Expected: FAIL because the runner has no resolver injection or integrity artifact.

- [ ] **Step 3: Wrap financial routes inside `_historical_point_in_time_guard`**

The guard receives `analysis_date`, recorder and resolver. Preserve and restore every vendor route for:

```python
_FINANCIAL_STATEMENT_METHODS = (
    "get_balance_sheet",
    "get_cashflow",
    "get_income_statement",
)
```

Each wrapper calls the original route, filters its serialized payload through `filter_statement_payload`, records every finding, and returns only the filtered payload. Existing current-snapshot, FRED vintage and social blocking wrappers also record their applied policy.

- [ ] **Step 4: Emit the immutable integrity document and evidence time metadata**

Create the recorder before `AuditCallback`, pass it into the callback, and write:

```python
integrity_path = directories["working"] / "point_in_time_integrity.json"
_write_json(integrity_path, recorder.finalize().model_dump(mode="json"))
```

Emit it as an artifact event. Callback evidence rows add `effective_at` and `freshness`; redaction and output hashing remain unchanged.

- [ ] **Step 5: Run focused runner tests**

Run: `cd platform && pytest tests/unit/runner/test_runner.py tests/unit/runner/test_events.py -v`

Expected: all tests PASS, including route restoration after success and exception.

- [ ] **Step 6: Commit runner integration**

```bash
git add platform/src/tradingng_platform/runner/tradingagents.py platform/src/tradingng_platform/runner/callbacks.py platform/tests/unit/runner/test_runner.py platform/tests/unit/runner/test_events.py
git commit -m "feat: enforce point-in-time policy in runner"
```

### Task 4: Persist immutable integrity verdicts during worker finalization

**Files:**
- Create: `platform/src/tradingng_platform/models/integrity.py`
- Create: `platform/migrations/versions/20260727_0010_point_in_time_integrity.py`
- Create: `platform/src/tradingng_platform/integrity/repository.py`
- Modify: `platform/src/tradingng_platform/models/__init__.py`
- Modify: `platform/src/tradingng_platform/models/assessments.py`
- Modify: `platform/src/tradingng_platform/worker/repository.py`
- Test: `platform/tests/unit/models/test_integrity.py`
- Test: `platform/tests/unit/worker/test_repository_steps.py`
- Test: `platform/tests/operations/test_database_migration.py`

- [ ] **Step 1: Write failing model and finalization tests**

```python
def test_integrity_model_has_immutable_identity():
    constraints = {constraint.name for constraint in RunIntegrityAssessment.__table__.constraints}
    assert "uq_run_integrity_policy_input" in constraints


async def test_finalize_success_archives_and_persists_integrity(...):
    write_integrity_document(work_dir, status="safe", fingerprint="a" * 64)
    await repository.finalize_success(run.id, work_dir, store)
    row = await session.scalar(select(RunIntegrityAssessment).where(RunIntegrityAssessment.run_id == run.id))
    artifact = await session.get(Artifact, row.artifact_id)
    assert row.status == "safe"
    assert artifact.kind == "point_in_time_integrity"
```

- [ ] **Step 2: Run and verify RED**

Run: `cd platform && pytest tests/unit/models/test_integrity.py tests/unit/worker/test_repository_steps.py -v`

Expected: FAIL importing `RunIntegrityAssessment`.

- [ ] **Step 3: Add schema and migration**

`run_integrity_assessments` contains UUID id/run/artifact, policy version, status, `audit_mode`, `temporal_scope`, analysis date, checked time, reason/tool JSON, fingerprint and timestamps. Add named unique constraint `uq_run_integrity_policy_input` over `(run_id, policy_version, input_fingerprint)` and indexes on `(policy_version, status)` and `checked_at`.

Add nullable self-FK `assessment_runs.clean_reassessment_of_run_id` and an index. The migration is additive and its downgrade removes only these new objects.

- [ ] **Step 4: Archive and persist the runner document atomically**

Add `point_in_time_integrity` to `_artifact_specs`. During `finalize_success`, retain its artifact id and call:

```python
await IntegrityRepository(self.session).persist_document(
    run_id,
    IntegrityDocument.model_validate_json(integrity_path.read_text()),
    artifact_id=integrity_artifact_id,
    audit_mode="live",
)
```

If a legacy/fake runner lacks the file, persist `unknown/integrity_artifact_missing` without fabricating a safe verdict. Populate EvidenceItem `effective_at` and `freshness` from callback rows.

- [ ] **Step 5: Run model, worker and migration tests**

Run: `cd platform && pytest tests/unit/models/test_integrity.py tests/unit/worker/test_repository_steps.py tests/operations/test_database_migration.py -v`

Expected: all tests PASS.

- [ ] **Step 6: Commit persistence**

```bash
git add platform/src/tradingng_platform/models platform/migrations/versions/20260727_0010_point_in_time_integrity.py platform/src/tradingng_platform/integrity/repository.py platform/src/tradingng_platform/worker/repository.py platform/tests/unit/models/test_integrity.py platform/tests/unit/worker/test_repository_steps.py platform/tests/operations/test_database_migration.py
git commit -m "feat: persist run integrity verdicts"
```

### Task 5: Retrospectively audit sealed historical evidence

**Files:**
- Create: `platform/src/tradingng_platform/integrity/audit.py`
- Create: `platform/src/tradingng_platform/integrity/main.py`
- Modify: `platform/pyproject.toml`
- Test: `platform/tests/unit/integrity/test_audit.py`
- Test: `platform/tests/integration/test_integrity_audit.py`

- [ ] **Step 1: Write failing sealed-evidence audit tests**

```python
def test_retrospective_audit_marks_confirmed_future_statement_at_risk(tmp_path):
    evidence = write_evidence(tmp_path, tool="get_income_statement", fiscal_end="2025-06-30")
    resolver = StubResolver({date(2025, 6, 30): Availability(date(2025, 7, 24), "sec", "high")})
    result = audit_evidence(evidence, analysis_date=date(2025, 7, 1), resolver=resolver)
    assert result.status is IntegrityStatus.AT_RISK
    assert "future_publication" in result.reason_codes


def test_retrospective_audit_marks_missing_or_unparseable_evidence_unknown(tmp_path):
    result = audit_evidence(tmp_path / "missing.jsonl", analysis_date=date(2025, 7, 1), resolver=StubResolver({}))
    assert result.status is IntegrityStatus.UNKNOWN
```

Integration coverage seeds safe, contaminated and missing-artifact runs, executes one bounded batch, then verifies immutable old Decision/Validation hashes and new verdict counts.

- [ ] **Step 2: Run and verify RED**

Run: `cd platform && pytest tests/unit/integrity/test_audit.py -v`

Expected: FAIL importing `tradingng_platform.integrity.audit`.

- [ ] **Step 3: Implement the pure auditor and resumable batch service**

The pure function verifies NDJSON shape, replays policy classification for observed tools, runs the same financial availability resolver, and never treats missing output as safe. The async batch selects succeeded runs lacking the current `(policy_version, input_fingerprint)`, verifies the Artifact SHA-256 before reading, writes a permanent `point_in_time_integrity_retro` artifact, then persists `audit_mode=retrospective`.

Add the entry point:

```toml
tradingng-platform-integrity-audit = "tradingng_platform.integrity.main:main"
```

CLI flags are `--limit` (1–500, default 50) and `--run-id` (optional UUID). Each invocation commits one run at a time, so interruption is safe and reruns are idempotent.

- [ ] **Step 4: Run audit tests**

Run: `cd platform && pytest tests/unit/integrity/test_audit.py tests/integration/test_integrity_audit.py -v`

Expected: all tests PASS.

- [ ] **Step 5: Commit retrospective auditing**

```bash
git add platform/src/tradingng_platform/integrity/audit.py platform/src/tradingng_platform/integrity/main.py platform/pyproject.toml platform/tests/unit/integrity/test_audit.py platform/tests/integration/test_integrity_audit.py
git commit -m "feat: audit historical assessment integrity"
```

### Task 6: Gate historical memory and accuracy statistics on safe verdicts

**Files:**
- Modify: `platform/src/tradingng_platform/memory/repository.py`
- Modify: `platform/src/tradingng_platform/memory/context.py`
- Modify: `platform/src/tradingng_platform/records/contracts.py`
- Modify: `platform/src/tradingng_platform/records/service.py`
- Modify: `platform/tests/unit/memory/test_repository.py`
- Modify: `platform/tests/unit/records/test_instrument_overviews.py`
- Modify: `platform/tests/integration/test_records_system.py`

- [ ] **Step 1: Write failing safe-only consumer tests**

```python
async def test_memory_excludes_at_risk_and_unknown_sources(...):
    safe, risky, unknown = await seed_three_validated_runs_with_integrity(session)
    snapshot = await HistoricalMemoryRepository(session).build("NVDA", date(2026, 7, 1), MemoryMode.HISTORICAL)
    assert [entry.source_run_id for entry in snapshot.entries] == [safe.id]


def test_validation_stats_report_integrity_exclusions():
    stats = _validation_stats(validations, integrity_by_run={safe_id: "safe", risky_id: "at_risk", unknown_id: "unknown"})
    twenty = next(item for item in stats if item.horizon == 20)
    assert twenty.completed == 1
    assert twenty.excluded_at_risk == 1
    assert twenty.excluded_unknown == 1
```

- [ ] **Step 2: Run and verify RED**

Run: `cd platform && pytest tests/unit/memory/test_repository.py tests/unit/records/test_instrument_overviews.py -v`

Expected: FAIL because consumers do not query integrity verdicts or expose exclusion counts.

- [ ] **Step 3: Add a reusable latest-verdict subquery**

`IntegrityRepository.latest_supported_subquery()` returns exactly one current-policy row per run using `row_number()` ordered by `checked_at DESC, created_at DESC, id DESC`. Reuse this subquery in memory and records code rather than duplicating status-selection logic.

- [ ] **Step 4: Enforce safe-only memory and default statistics**

Join the subquery in `HistoricalMemoryRepository.build` and require `status == "safe"`. Keep requested `historical` mode with zero entries when none qualify. Add `excluded_at_risk` and `excluded_unknown` to `InstrumentValidationStats`; record service passes a run-status map into `_validation_stats` and excludes unsafe validations from completed/accuracy counts.

- [ ] **Step 5: Run memory and record suites**

Run: `cd platform && pytest tests/unit/memory tests/unit/records tests/integration/test_records_system.py -v`

Expected: all tests PASS.

- [ ] **Step 6: Commit consumer gates**

```bash
git add platform/src/tradingng_platform/memory platform/src/tradingng_platform/records platform/tests/unit/memory platform/tests/unit/records platform/tests/integration/test_records_system.py
git commit -m "feat: quarantine unsafe results from consumers"
```

### Task 7: Expose integrity and clean reassessment through REST

**Files:**
- Create: `platform/src/tradingng_platform/integrity/service.py`
- Create: `platform/src/tradingng_platform/api/routes/integrity.py`
- Modify: `platform/src/tradingng_platform/api/routes/__init__.py`
- Modify: `platform/src/tradingng_platform/api/app.py`
- Modify: `platform/src/tradingng_platform/assessments/repository.py`
- Modify: `platform/src/tradingng_platform/assessments/contracts.py`
- Test: `platform/tests/unit/integrity/test_service.py`
- Test: `platform/tests/unit/api/test_assessments.py`
- Test: `platform/tests/integration/test_rest_api.py`

- [ ] **Step 1: Write failing service and endpoint tests**

```python
async def test_clean_reassessment_uses_independent_memory_and_preserves_source(...):
    clean = await service.clean_reassess(admin, risky_run.id, "request-1")
    context = await repository.get_run_context(clean.id)
    assert context.batch.defaults_json["memory_mode"] == "independent"
    assert context.run.clean_reassessment_of_run_id == risky_run.id
    assert context.run.retry_of_run_id is None


async def test_safe_run_cannot_be_clean_reassessed(...):
    with pytest.raises(CleanReassessmentNotAllowed):
        await service.clean_reassess(admin, safe_run.id, "request-2")
```

REST tests cover 404, 403, 409, response Location and audit event.

- [ ] **Step 2: Run and verify RED**

Run: `cd platform && pytest tests/unit/integrity/test_service.py tests/unit/api/test_assessments.py -v`

Expected: FAIL because the service and routes do not exist.

- [ ] **Step 3: Implement service contracts and clean-run creation**

Contracts expose `IntegrityView`, `IntegritySummaryView`, per-tool findings, exclusion counts and clean reassessment linkage. Repository creates a new internal batch owned by the source owner with deterministic idempotency key `clean-{source_run_id}-{policy_version}`, copies analysis settings, forces `memory_mode=independent`, then creates a new request/run with `clean_reassessment_of_run_id`.

Only Admin with `assessments:admin` and `assessments:submit` may invoke it, and only latest `at_risk`/`unknown` succeeded runs qualify. Repeat calls return the same clean run.

- [ ] **Step 4: Register REST routes**

```text
GET  /api/v1/assessments/{run_id}/integrity
GET  /api/v1/integrity/summary
POST /api/v1/assessments/{run_id}/clean-reassessment
```

Wire `IntegrityService` into app lifespan. Summary defaults to the current policy and returns safe/at-risk/unknown/unassessed counts.

- [ ] **Step 5: Run REST suites**

Run: `cd platform && pytest tests/unit/integrity/test_service.py tests/unit/api/test_assessments.py tests/integration/test_rest_api.py -v`

Expected: all tests PASS.

- [ ] **Step 6: Commit REST operations**

```bash
git add platform/src/tradingng_platform/integrity/service.py platform/src/tradingng_platform/api/routes platform/src/tradingng_platform/api/app.py platform/src/tradingng_platform/assessments platform/tests/unit/integrity/test_service.py platform/tests/unit/api/test_assessments.py platform/tests/integration/test_rest_api.py
git commit -m "feat: expose assessment integrity operations"
```

### Task 8: Add MCP parity

**Files:**
- Modify: `platform/src/tradingng_platform/mcp/services.py`
- Modify: `platform/src/tradingng_platform/mcp/resources.py`
- Modify: `platform/src/tradingng_platform/mcp/tools.py`
- Modify: `platform/tests/unit/mcp/test_resources.py`
- Modify: `platform/tests/unit/mcp/test_tools.py`
- Modify: `platform/tests/integration/test_mcp.py`

- [ ] **Step 1: Write failing MCP inventory and behavior tests**

```python
assert "clean_reassess_assessment" in {tool.name for tool in tools.tools}
assert "tradingng://assessments/{run_id}/integrity" in {
    str(template.uriTemplate) for template in templates.resourceTemplates
}

integrity = await session.read_resource(AnyUrl(f"tradingng://assessments/{run_id}/integrity"))
assert json.loads(integrity.contents[0].text)["policy_version"] == "point-in-time.v1"
```

- [ ] **Step 2: Run and verify RED**

Run: `cd platform && pytest tests/unit/mcp/test_resources.py tests/unit/mcp/test_tools.py tests/integration/test_mcp.py -v`

Expected: FAIL on the missing resource and tool.

- [ ] **Step 3: Wire the existing integrity service into MCP**

Add `integrity: IntegrityService` to `McpServices`, register the JSON resource, and implement `clean_reassess_assessment(run_id)` by delegating to the same service/principal/request-id path as REST. Do not duplicate authorization or persistence logic.

- [ ] **Step 4: Run MCP suites**

Run: `cd platform && pytest tests/unit/mcp tests/integration/test_mcp.py -v`

Expected: all tests PASS and the inventory count increases by one resource template and one tool.

- [ ] **Step 5: Commit MCP parity**

```bash
git add platform/src/tradingng_platform/mcp platform/tests/unit/mcp platform/tests/integration/test_mcp.py
git commit -m "feat: expose integrity through MCP"
```

### Task 9: Present integrity state and exclusion counts in the bilingual Web UI

**Files:**
- Create: `web/src/features/runs/IntegrityPanel.tsx`
- Create: `web/src/features/runs/IntegrityPanel.test.tsx`
- Modify: `web/src/features/runs/RunDetailPage.tsx`
- Modify: `web/src/api/records.ts`
- Modify: `web/src/api/schema.d.ts` (generated)
- Modify: `web/src/i18n/messages.ts`
- Modify: `web/src/styles.css`
- Modify: `web/src/features/dashboard/InstrumentLedgerTable.tsx`
- Modify: `web/src/features/dashboard/DashboardPage.test.tsx`

- [ ] **Step 1: Write failing panel tests**

```tsx
it("shows a compact risk warning and expandable tool findings", () => {
  render(<IntegrityPanel integrity={AT_RISK_INTEGRITY} canCleanReassess onCleanReassess={() => undefined} />);
  expect(screen.getByRole("alert")).toHaveTextContent("点时数据存在风险");
  expect(screen.getByText("未来发布的财务报表")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "创建干净重评估" })).toBeInTheDocument();
});

it("uses a low-emphasis safe state", () => {
  render(<IntegrityPanel integrity={SAFE_INTEGRITY} canCleanReassess={false} onCleanReassess={() => undefined} />);
  expect(screen.getByText("点时数据已核验")).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Run and verify RED**

Run: `npm --prefix web test -- --run src/features/runs/IntegrityPanel.test.tsx`

Expected: FAIL because `IntegrityPanel` is missing.

- [ ] **Step 3: Regenerate the API contract and add client calls**

Run:

```bash
PYTHONPATH=platform/src python scripts/export_openapi.py
npm --prefix web run api:generate
```

Add `getIntegrity`, `getIntegritySummary`, and `cleanReassessRun` using generated types.

- [ ] **Step 4: Implement the compact panel and detail-page action**

The panel renders safe as a quiet badge, unknown as warning, and at-risk as alert. Tool findings remain inside `<details>`. The clean action appears only for Admin scope and navigates to the returned run. A missing verdict displays “尚未完成点时数据审计” and is never styled as safe.

- [ ] **Step 5: Add exclusion counts to ledger reliability**

Display eligible accuracy first and a secondary bilingual label such as `排除 2 条风险 / 1 条未知`. Keep counts hidden when all are zero. Do not combine excluded results into the percentage.

- [ ] **Step 6: Run Web tests, typecheck, lint and build**

Run:

```bash
npm --prefix web test -- --run
npm --prefix web run typecheck
npm --prefix web run lint
npm --prefix web run build
```

Expected: all commands exit 0.

- [ ] **Step 7: Commit Web UI**

```bash
git add var/openapi.json web/src web/package.json web/package-lock.json
git commit -m "feat: show point-in-time integrity in web UI"
```

### Task 10: Migrate, backfill, verify, and deploy safely

**Files:**
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `.env.platform.example`
- Test: `platform/tests/operations/test_deploy_config.py`

- [ ] **Step 1: Write failing operations assertions for SEC configuration and audit command**

Add checks that `.env.platform.example` documents `TRADINGNG_SEC_USER_AGENT` without a real email/key and that the installed console-script inventory contains `tradingng-platform-integrity-audit`.

- [ ] **Step 2: Run and verify RED**

Run: `cd platform && pytest tests/operations/test_deploy_config.py -v`

Expected: FAIL on missing documented configuration or entry point.

- [ ] **Step 3: Document operation and rollback**

Document the fail-closed rule, integrity states, audit command, clean reassessment, SEC User-Agent override, default-safe statistics and the fact that `TradingAgents/` remains untouched. Rollback disables new consumers first; it never downgrades after production data exists.

- [ ] **Step 4: Run complete local verification before committing**

Run:

```bash
cd platform && ruff check src tests && pytest -q
npm --prefix web test -- --run
npm --prefix web run typecheck
npm --prefix web run lint
npm --prefix web run build
git diff --check
git diff -- TradingAgents
git submodule status TradingAgents
```

Expected: Python and Web suites pass, diff check is clean, `git diff -- TradingAgents` is empty, and the submodule remains at its pre-plan commit.

- [ ] **Step 5: Commit documentation and operations checks**

```bash
git add README.md README.zh-CN.md .env.platform.example platform/tests/operations/test_deploy_config.py
git commit -m "docs: operate point-in-time integrity audits"
```

- [ ] **Step 6: Inspect live state before migration**

Read active assessment/validation counts and service status without changing tasks. Record the current Alembic revision and table counts for `assessment_runs`, `decisions`, `validations`, and `artifacts`.

- [ ] **Step 7: Apply the additive migration and restart only affected services**

Run the repository's documented Alembic upgrade, then restart API, worker and MCP-bearing API service in that order. Do not restart Gateway, Alpha broker, scheduler or validation worker unless their unit file directly loads changed Python code.

- [ ] **Step 8: Audit old runs in bounded batches**

Run `tradingng-platform-integrity-audit --limit 25` repeatedly. After each batch, verify safe/at-risk/unknown counts, Alpha broker queue, SEC response health and assessment queue. Stop on systematic unknown schema, authentication or database errors; completed audit rows remain valid.

- [ ] **Step 9: Enable safe-only consumers after backfill coverage is known**

Confirm unassessed count is zero or explicitly accepted as excluded. Verify known suspicious NVDA/TSLA statement cases are not safe. Then exercise REST, MCP and Web reads and create one clean reassessment through the public service path.

- [ ] **Step 10: Run production smoke checks**

Verify API health, authenticated login, dashboard, one run detail, integrity summary, MCP inventory, system status, scheduler admission, Alpha broker state and unchanged in-flight task statuses. Compare original Decision/Validation hashes and counts captured before migration.

- [ ] **Step 11: Final verification and push**

Run the complete test/build commands again, inspect `git status --short`, review `git log --oneline` for the phase commits, and push `main` only after every required check has fresh passing output.

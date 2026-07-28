# Assessment Deletion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a safe, administrator-only permanent deletion workflow for individual assessments across REST, MCP, and the Web detail page.

**Architecture:** `AssessmentService.delete` is the single policy boundary. It locks and validates a run, delegates explicit foreign-key-safe deletion to `AssessmentRepository`, commits an immutable audit event, then performs constrained best-effort filesystem cleanup. REST, MCP, and Web are thin clients of that behavior.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async, MySQL, FastMCP, React, TypeScript, Vitest, pytest.

---

### Task 1: Service policy and database deletion

**Files:**
- Modify: `platform/tests/unit/assessments/test_service.py`
- Modify: `platform/tests/integration/test_assessment_workflow.py`
- Modify: `platform/src/tradingng_platform/assessments/service.py`
- Modify: `platform/src/tradingng_platform/assessments/repository.py`

- [ ] **Step 1: Write failing service tests**

Add tests that call `delete` with a non-admin, an active run, a terminal run with a dependent run, and an eligible terminal run. Assert `AssessmentAccessDenied`, `AssessmentDeleteNotAllowed`, and the returned deletion facts respectively.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `.venv/bin/pytest platform/tests/unit/assessments/test_service.py -q`

Expected: failure because `AssessmentService.delete` and `AssessmentDeleteNotAllowed` do not exist.

- [ ] **Step 3: Implement service and repository behavior**

Add:

```python
@dataclass(frozen=True)
class DeletedAssessment:
    run_id: uuid.UUID
    ticker: str
    analysis_date: date
    status: RunStatus

class AssessmentDeleteNotAllowed(Exception):
    def __init__(self, reason: str, *, status=None, dependent_run_ids=()): ...

async def delete(self, principal, run_id, request_id) -> DeletedAssessment:
    principal.require("assessments:admin")
    # require Admin, lock, validate, delete graph, append audit, clean files
```

Implement repository queries for active leases/validations, dependent runs, explicit child deletion, and conditional request/batch/snapshot removal. Keep every mutation in the caller's transaction.

- [ ] **Step 4: Add a full graph integration test**

Seed a terminal run with event deliveries, steps, artifacts, evidence, decision, validation, integrity, reviews/comments, and a config snapshot. Delete it and assert every run-owned row is absent, the orphan request/batch/snapshot is removed, the instrument remains, and `assessment.delete` is audited.

- [ ] **Step 5: Run service and integration tests**

Run: `.venv/bin/pytest platform/tests/unit/assessments/test_service.py platform/tests/integration/test_assessment_workflow.py -q`

Expected: all configured tests pass; integration tests may skip only when the dedicated database URL is intentionally absent.

### Task 2: Constrained filesystem cleanup

**Files:**
- Modify: `platform/tests/unit/artifacts/test_store.py`
- Modify: `platform/src/tradingng_platform/artifacts/store.py`
- Create: `platform/src/tradingng_platform/assessments/files.py`
- Modify: `platform/src/tradingng_platform/assessments/service.py`

- [ ] **Step 1: Write failing deletion safety tests**

Test an ordinary UUID directory, an absent directory, and a symlink pointing outside the configured root. Assert the first is recursively removed, absence is idempotent, and the symlink itself is removed without touching its target.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `.venv/bin/pytest platform/tests/unit/artifacts/test_store.py -q`

Expected: failure because run-directory deletion is not implemented.

- [ ] **Step 3: Implement exact-root cleanup**

Implement a reusable `delete_run_directory(root: Path, run_id: UUID)` that validates the direct child name, unlinks symlinks, recursively removes normal directories, and rejects unexpected non-directory files. Add `LocalArtifactStore.delete_run` and call both artifact and job cleanup only after transaction commit.

- [ ] **Step 4: Verify filesystem behavior**

Run: `.venv/bin/pytest platform/tests/unit/artifacts/test_store.py platform/tests/unit/assessments/test_service.py -q`

Expected: all tests pass.

### Task 3: REST contract and generated client schema

**Files:**
- Modify: `platform/tests/unit/api/test_assessments.py`
- Modify: `platform/src/tradingng_platform/api/routes/assessments.py`
- Modify: `platform/src/tradingng_platform/api/app.py`
- Modify: `web/src/api/schema.d.ts`

- [ ] **Step 1: Write failing REST tests**

Test `204` for an admin delete, `403` for missing scope, and `409 delete_not_allowed` with reason/status/dependent IDs.

- [ ] **Step 2: Run the route tests and verify RED**

Run: `.venv/bin/pytest platform/tests/unit/api/test_assessments.py -q`

Expected: `DELETE` returns `405` or the service lacks `delete`.

- [ ] **Step 3: Add the endpoint and error mapping**

Add `DELETE /assessments/{run_id}` with `status_code=204`, `assessments:admin`, and translations for not found, access denied, and deletion conflicts. Wire configured artifact/job roots into the service factory.

- [ ] **Step 4: Regenerate specifications**

Run: `.venv/bin/python scripts/export_openapi.py && cd web && npm run api:generate`

Expected: `var/openapi.json` and `web/src/api/schema.d.ts` include `delete_assessment`.

- [ ] **Step 5: Verify REST tests**

Run: `.venv/bin/pytest platform/tests/unit/api/test_assessments.py -q`

Expected: all tests pass.

### Task 4: MCP deletion tool

**Files:**
- Modify: `platform/tests/unit/mcp/test_tools.py`
- Modify: `platform/src/tradingng_platform/mcp/services.py`
- Modify: `platform/src/tradingng_platform/mcp/tools.py`

- [ ] **Step 1: Write a failing MCP tool test**

List tools and call `delete_assessment` with an admin principal. Assert the tool exists, returns the deleted run ID and `deleted` status, and delegates the same request ID to `AssessmentService.delete`.

- [ ] **Step 2: Run the MCP test and verify RED**

Run: `.venv/bin/pytest platform/tests/unit/mcp/test_tools.py -q`

Expected: failure because `delete_assessment` is absent.

- [ ] **Step 3: Add tool and service wiring**

Add a structured deletion result, register `delete_assessment`, and pass artifact/job roots through `McpServices.from_database`.

- [ ] **Step 4: Verify MCP behavior**

Run: `.venv/bin/pytest platform/tests/unit/mcp/test_tools.py -q`

Expected: all tests pass.

### Task 5: Web confirmation workflow

**Files:**
- Modify: `web/src/api/records.ts`
- Modify: `web/src/pages/RunDetailPage.tsx`
- Modify: `web/src/pages/RunDetailPage.test.tsx`
- Modify: `web/src/i18n/messages.ts`
- Modify: `web/src/styles/global.css`

- [ ] **Step 1: Write failing interaction tests**

Render a terminal run as an admin, open the delete dialog, cancel once, confirm once, and assert one DELETE request plus navigation to `/`. Also assert non-admin and non-terminal runs do not expose the button.

- [ ] **Step 2: Run the focused Web test and verify RED**

Run: `cd web && npm test -- --run src/pages/RunDetailPage.test.tsx`

Expected: failure because the delete control does not exist.

- [ ] **Step 3: Implement API and accessible dialog**

Add `deleteRun(runId)`, derive eligibility from role/scope/status, render localized destructive copy with `role="dialog"` and `aria-modal="true"`, disable actions while pending, show API errors in the dialog, and navigate to `/` after `204`.

- [ ] **Step 4: Add bilingual messages and styling**

Provide complete Chinese and English labels for the action, warning, conflict/error states, progress, cancel, and confirm. Style a restrained danger button and centered responsive confirmation panel with visible focus states.

- [ ] **Step 5: Verify focused Web tests**

Run: `cd web && npm test -- --run src/pages/RunDetailPage.test.tsx`

Expected: all tests pass.

### Task 6: Full verification, deployment, and delivery

**Files:**
- Modify: `docs/superpowers/plans/2026-07-28-assessment-deletion.md`

- [ ] **Step 1: Run full automated verification**

Run Platform unit/integration verification, Gateway tests, `npm test`, `npm run lint`, and `npm run build`. Expected: no failures and no unexpected skips.

- [ ] **Step 2: Inspect scope and privacy**

Run `git diff --check`, inspect `git diff --stat`, ensure no `.env`, credentials, runtime artifacts, or `TradingAgents/` changes are staged.

- [ ] **Step 3: Deploy safely**

Restart only the affected Platform API service after the Web build is installed by the established deployment workflow. Do not interrupt running Gateway or workers unnecessarily.

- [ ] **Step 4: Verify production health**

Check service status, `/health/live`, `/health/ready`, Web HTTPS loading, and OpenAPI exposure of `DELETE /api/v1/assessments/{run_id}`.

- [ ] **Step 5: Commit and push main**

Commit the implementation and generated schema with a descriptive message, push `main` to `origin`, and report the commit hash plus verification evidence.


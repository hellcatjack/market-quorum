# Assessment Deletion Design

## Goal

Allow an administrator to permanently delete one stored assessment from the Web UI, REST API, or MCP without leaving broken database references or allowing an in-flight job to be removed.

## Product behavior

- Deletion is a destructive administrative action. The caller must have the `Admin` role and the `assessments:admin` scope.
- Only terminal assessments (`succeeded`, `failed`, `cancelled`, or `needs_attention`) may be deleted.
- A terminal run is still protected while it has a worker lease, a running performance validation, or a running decision-price collection.
- A run referenced by a retry or clean reassessment cannot be deleted. The API reports the dependent run identifiers so the administrator can delete descendants first and preserve an intelligible lineage.
- The Web detail page exposes the action only when the signed-in user is an eligible administrator and the run is terminal. A dedicated confirmation dialog names the instrument and analysis date, explains permanence, prevents duplicate submission, and returns to the overview after success.
- REST uses `DELETE /api/v1/assessments/{run_id}` and returns `204 No Content`. State conflicts use a stable `409 delete_not_allowed` error with machine-readable details.
- MCP exposes `delete_assessment` with the same authorization and safety rules.

## Data deletion boundary

The service locks the run, rechecks all safety conditions, records the facts required for the audit entry, and deletes related rows in one database transaction. Child rows are deleted explicitly in foreign-key order:

1. webhook deliveries attached to run events;
2. integrity assessments, validations, decision-price basis, and evidence rows that may reference artifacts;
3. reviews, comments, decisions, worker leases, run steps, artifacts, and run events;
4. the assessment run;
5. the request, batch, and configuration snapshot only when no remaining run or request references them.

The instrument, users, webhooks, global configuration, workers, and historical audit records are retained. A new `assessment.delete` audit event remains after the run is gone and contains the deleted ticker, analysis date, status, request, batch, and cleanup counts.

## Filesystem behavior

After the database commit succeeds, the service removes only the exact `<artifact_dir>/<run_uuid>` and `<job_dir>/<run_uuid>` directories. Paths are resolved and constrained to their configured roots; a symlink is unlinked rather than followed. Missing directories are treated as already clean. A filesystem cleanup failure is logged with the run identifier but cannot roll back a successful canonical database deletion.

## Dependency wiring

`AssessmentService` accepts optional artifact and job roots so existing isolated tests remain simple. The API and MCP service factories provide the configured roots. `LocalArtifactStore` owns safe deletion inside the artifact root; a matching focused helper handles the worker job root.

## Verification

- Unit tests cover authorization, terminal-state enforcement, dependency conflicts, REST error translation, MCP registration, safe directory removal, and Web confirmation behavior.
- MySQL integration tests create a complete assessment graph, delete it, and prove that dependent rows disappear while shared/orphan-sensitive rows are handled correctly.
- OpenAPI and the generated TypeScript schema are regenerated.
- Platform, Gateway, and Web test/build suites run before deployment; the API is restarted and health endpoints are checked afterward.


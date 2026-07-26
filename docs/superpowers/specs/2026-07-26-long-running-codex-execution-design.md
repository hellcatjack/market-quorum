# Long-Running Codex Execution Design

## Goal

Allow TradingNG assessments to run for hours without the Gateway interrupting a healthy Codex turn, while preserving bounded failure recovery, auditable retries, and safe service shutdown.

## Constraints

- The assessment that is currently running in `/app/devs/TradingNG` must not be interrupted.
- Development and verification happen in an isolated worktree. No running service is reloaded until there are no active Runner processes and Gateway reports zero active completions.
- The existing OpenAI-compatible `/v1/chat/completions` contract remains compatible with TradingAgents and LangChain.
- Prompts, model responses, credentials, and hidden reasoning must not be added to operational logs.
- Existing LangGraph checkpoints remain the durable recovery boundary.

## Approaches Considered

### 1. Compatibility-first long turns (selected)

Make the Gateway wall-clock timeout optional and disabled in production, retain an optional positive safety limit, expose turn-age/progress telemetry, classify terminal Codex errors, capture redacted App Server diagnostics, and allow systemd shutdown to wait for active requests.

This directly removes the confirmed 600-second interruption without replacing the working TradingAgents integration. It also preserves the existing platform architecture: an assessment is already a durable asynchronous job, the Worker supervises its Runner process, and LangGraph checkpoints recover completed nodes.

### 2. A second asynchronous job system inside Gateway

Add submission, status, event-stream, result, and durable thread-resume endpoints to Gateway. This is appropriate if Gateway becomes a public multi-client service, but today it duplicates the platform job model and requires replacing the synchronous OpenAI-compatible client path. It is intentionally deferred.

### 3. Increase the timeout to 6 or 24 hours

This is operationally simple but only moves the failure boundary. It still interrupts valid work and provides no better diagnostics, so it is rejected as the final design. A positive timeout remains available as an emergency operator override.

## Architecture

The platform remains the durable outer orchestration layer:

```text
Assessment API -> Worker -> checkpointed Runner -> local Gateway -> Codex App Server
                     |              |                  |
                     |              |                  +-- turn lifecycle telemetry
                     |              +-- LLM success/failure audit records
                     +-- lease heartbeat and process supervision
```

An assessment may run for hours. Individual Codex turns are no longer interrupted because of elapsed wall-clock time. If the Runner or host fails, retrying the assessment reuses the existing checkpoint directory and resumes from the last completed graph node.

## Timeout Semantics

`CODEX_GATEWAY_REQUEST_TIMEOUT_SECONDS` has these semantics:

- unset or `0`: no Gateway wall-clock deadline;
- positive integer: enforce that explicit number of seconds;
- negative or non-integer: fail configuration validation.

The checked-in systemd unit explicitly sets the value to `0`. A positive override is intended only as an emergency safety control.

No automatic no-progress interruption is enabled. Some legitimate high-reasoning turns can be quiet for a long time, so inactivity is exposed as telemetry and logs for operator alerting rather than used as a destructive watchdog.

## Runtime Lifecycle and Telemetry

Each in-flight turn tracks:

- Gateway request ID and TradingNG run ID;
- Codex thread and turn IDs;
- start time and most recent App Server notification time;
- terminal status and normalized `codexErrorInfo` code.

`/internal/status` remains backward compatible and additionally reports:

- whether the runtime is accepting work;
- age of the oldest active completion;
- age since progress on the stalest active completion.

Operational logs record correlation IDs, elapsed time, SDK retry count, terminal status, and normalized error code. They do not record prompts or response content.

App Server stderr is drained as it is today, but bounded lines are redacted and forwarded to journald. Credential-like key/value pairs are replaced with `[REDACTED]`, oversized lines are truncated, and binary/invalid UTF-8 is replaced safely.

## Error Handling

Terminal `turn/completed` notifications are handled by status:

- `completed` with a final agent message: return normally;
- `interrupted`: return a retryable `codex_interrupted` 502 error;
- usage limit, overload, or session budget: return `codex_rate_limit` 429;
- unauthorized: mark the runtime unavailable and return 503;
- context-window exceeded: return non-retryable `codex_context_limit` 400;
- stream disconnect or repeated response failure: return retryable `codex_runtime_error` 502;
- malformed or unknown terminal errors: return a safe `codex_runtime_error` 502.

Both string and tagged-object forms of `codexErrorInfo` are normalized case-insensitively. Error responses expose only safe messages and codes.

The existing OpenAI SDK retry policy continues to retry transient HTTP failures. Gateway logs the incoming `x-stainless-retry-count` so repeated HTTP attempts are visible and correlated to the TradingNG run.

## Assessment Audit Records

`AuditCallback` writes one terminal LLM interaction record on both success and final failure:

- `status` is `completed` or `failed`;
- `completed_at` and duration are always present;
- failures include a safe error type/code but not exception text;
- dependency health continues to record the corresponding healthy/unhealthy sample.

SDK-internal attempts remain visible in Gateway journald through request ID, TradingNG run ID, and retry count. A final assessment can therefore be distinguished from a clean run even when an inner HTTP attempt was retried successfully.

## Shutdown and Deployment Safety

The systemd Gateway unit sets `TimeoutStopSec=infinity`. Uvicorn stops accepting new connections on shutdown and is allowed to wait for existing request tasks instead of systemd killing the process after its default stop timeout.

Activation follows this gate:

1. Build and test in the isolated worktree.
2. Confirm there are no `tradingng_platform.runner.cli` processes.
3. Confirm Gateway `/internal/status` reports `active_completions=0` twice across a short interval.
4. Integrate the branch while preserving unrelated main-worktree changes.
5. Run deployment verification.
6. Reload the user systemd unit and restart Gateway.
7. Run health and a bounded smoke completion.

If either activity check fails, activation stops without modifying or signaling the running service.

## Testing

Automated tests cover:

- absent and zero timeout values produce an unbounded setting;
- positive timeout still interrupts and returns 504;
- an unbounded completion remains alive beyond the old deadline and completes normally;
- terminal status/error variants map to the correct public errors;
- progress ages update on notifications;
- stderr is drained, redacted, bounded, and logged without deadlock;
- success and failure LLM audit records contain terminal timestamps and safe status;
- the systemd unit selects unbounded turns and unlimited graceful stop time;
- all existing Gateway and platform Runner tests continue to pass.

No test connects to or restarts the live Gateway.

## Deferred Work

- Durable Gateway-owned jobs and `thread/resume` are deferred until Gateway must survive independently of the platform Runner or serve remote clients.
- A per-job App Server pool is deferred until measurements show the shared process is a capacity or isolation bottleneck.
- Automatic idle-turn termination is intentionally omitted; alerting should precede any destructive watchdog policy.

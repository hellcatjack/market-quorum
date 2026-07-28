# Alpha Vantage Global Coordination Design

## Goal

Protect one or more Alpha Vantage API keys as global resources while allowing the
platform to accept large assessment batches. Rate limits must pause and resume work
without falling back to Yahoo, and vendor pressure must not be confused with Gateway
or runner failures.

## Constraints

- Do not modify the vendored `TradingAgents` project.
- Research, outcome validation, and future background consumers must share quota by
  API-key identity.
- Existing assessments and artifacts remain immutable; automatic retries create linked
  attempts.
- API keys, query URLs containing keys, response bodies, and local paths are never
  returned by diagnostics.
- The platform remains deployable as user-level systemd services on the current host.

## Architecture

Add a loopback-only `tradingng-platform-alpha-broker` service. It owns Alpha Vantage
credentials and outbound HTTP calls. The existing TradingAgents adapter replaces the
vendored Alpha request function at runtime with a synchronous broker client; the
validation provider uses the asynchronous broker client. No TradingAgents source file
changes.

The broker creates one coordinator per distinct API-key fingerprint. Each coordinator
uses a smooth effective request rate, a bounded in-flight count, a priority queue, and
a global cooldown state. The configured rate is multiplied by an 80% safety factor by
default. Research requests have priority over validation, while FIFO ordering prevents
reordering within one class.

Identical in-flight requests are coalesced. Successful responses are cached using a
fingerprint of the API-key identity, function, and normalized parameters. Cache policy
is endpoint-aware. Historical safety remains at the external TradingAgents adapter,
which already clips time series and financial reports to the analysis date; current-only
historical routes remain disabled.

## State machine

- `normal`: dispatch up to the in-flight limit at the smooth effective rate.
- `cooldown`: dispatch nothing until `blocked_until`.
- `half_open`: dispatch exactly one probe. Success returns to `normal`; another limit
  doubles the global backoff up to the configured maximum.
- `unavailable`: broker health/configuration cannot serve requests; scheduler blocks new
  Alpha-backed admissions.

Explicit per-minute notices and HTTP 429 responses affect the complete API-key scope.
Daily-quota notices receive a long cooldown. Authentication failures are terminal until
configuration changes. Network and 5xx failures use bounded per-request transient
retries without being mislabeled as quota exhaustion. Invalid-symbol/data responses do
not open a global circuit.

## Admission and recovery

The scheduler reads the broker snapshot before admission. It blocks new Alpha-backed
runs while the broker is cooling down, half-open, unavailable, or its request queue is
above the configured admission threshold. Already-running assessments are allowed to
wait in the broker and therefore receive priority to finish.

If the broker exhausts its bounded request retries, the runner emits either
`vendor_rate_limit` or `vendor_transient`. The failed run remains immutable and the
worker creates a linked queued attempt for those two codes only. At most two automatic
assessment retries are created by default. Authentication, invalid data, protocol, and
code failures are never automatically retried.

## Observability

The management API adds a safe Alpha Vantage broker snapshot containing state,
configured/effective RPM, in-flight count, queue length, oldest wait, cooldown deadline,
and aggregate request/cache/error counters. The system page renders these values in
Chinese and English. No API key or key fingerprint is exposed.

HTTP client informational logging is disabled inside the broker so the upstream query
URL cannot write the API key to journald.

## Rollout

Start the broker first. Stop scheduler admission, allow active assessment workers to
finish, then restart scheduler, validation, and workers against the broker. This avoids
terminating an existing assessment. The broker is enabled at login and required by
Alpha-dependent platform services.

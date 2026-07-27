# TSLA Monthly Continuous Validation Design

## Objective

Run one point-in-time TSLA assessment per calendar month over the most recent
12-month window for which a complete 20-session outcome is available. Exercise
the production REST API, scheduler, Workers, Codex Gateway, Alpha Vantage
research and validation adapters, immutable artifacts, and historical memory as
one continuous chain. Diagnose and repair any material defect outside the
`TradingAgents/` source tree, then rerun the affected checkpoint.

## Date selection

Alpha Vantage reports TSLA sessions through 2026-07-24. The latest session with
20 later reported sessions is 2026-06-25. The chain therefore uses the last
eligible TSLA session in each calendar month from July 2025 through June 2026:

1. 2025-07-31
2. 2025-08-29
3. 2025-09-30
4. 2025-10-31
5. 2025-11-28
6. 2025-12-31
7. 2026-01-30
8. 2026-02-27
9. 2026-03-31
10. 2026-04-30
11. 2026-05-29
12. 2026-06-25

The final date is capped by the fully mature 20-session cutoff instead of using
the June month-end. This avoids an incomplete outcome being mistaken for a
failure.

## Execution design

Submit each assessment through `POST /api/v1/assessments` with all four equity
analysts, `deep` depth, Chinese output, and `historical` memory mode. Use a stable
per-date idempotency key so the harness can resume safely without duplicating a
successful submission.

Run strictly in chronological order. After an assessment succeeds, wait until
its 1-, 5-, and 20-session `validation.v2` records reach a terminal state before
submitting the next date. Because historical memory applies point-in-time
eligibility (`exit_session < next analysis_date`), a short trading month may
legitimately contribute a mature 5-session result instead of an as-yet unknown
20-session result. This is expected and prevents look-ahead bias.

If a run or validation fails, stop the chain at that date. Preserve its events,
logs, and artifacts, identify the root cause across API, scheduler, Worker,
Gateway, provider, persistence, and UI projections, then add a failing regression
test before changing production code. Retry the same date after the repair and
continue only when it passes.

## Quality gates

For each of the 12 checkpoints:

- the assessment succeeds with every step completed and timestamped;
- the immutable snapshot names Alpha Vantage exclusively for the four research
  categories and the verified market snapshot does not invoke Yahoo;
- the final decision has a normalized rating, executive summary, investment
  thesis, and an unabridged time horizon; a missing price target remains absent
  rather than fabricated;
- every stored artifact passes its SHA-256 verification;
- all 1-, 5-, and 20-session validations complete through `alphavantage`, use
  `validation.v2`, and carry an explicit entry/exit session and price basis;
- historical memory contains at most five distinct earlier runs, never includes
  the current or a later date, and only includes outcomes whose exit session was
  knowable before the current analysis date;
- no API key, bearer token, or provider credential appears in logs, snapshots,
  events, reports, or artifacts.

At chain completion, aggregate rating, price target availability, 20-session raw
return, alpha, direction correctness, adverse/favorable excursion, memory source
count, elapsed time, Gateway request count, retry count, and provider identity.
The aggregate is an engineering quality audit, not a claim that 12 observations
establish investment performance.

## Operational safeguards

Do not change scheduler capacity: serial admission makes the existing limit
irrelevant and minimizes provider bursts. Do not cancel unrelated work. Keep
Gateway, API, scheduler, validation service, and Workers managed by their enabled
systemd units. Store a resumable, secret-free audit summary under `var/` and a
final human-readable test report under `docs/reports/`; only the latter is
eligible for version control.

## Approval

The user explicitly authorized the recommended approach without additional
confirmation unless a major issue requires a product decision. This design uses
that standing approval and remains within the requested TSLA test-and-repair
scope.

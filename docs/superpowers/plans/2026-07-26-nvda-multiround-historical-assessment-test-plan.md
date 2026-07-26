# NVDA Multi-Round Historical-Assisted Assessment Test Plan

> **Execution rule:** Run inline in the current session on `main`. Do not use
> subagents or Git worktrees.

**Goal:** Verify that TradingNG selects, pins, injects, displays, and uses
lookahead-safe NVDA historical assessment outcomes across multiple rounds, and
measure whether any decision change exceeds normal Codex output variation.

**Architecture:** Build one retrospective seed assessment, then three
chronological history-assisted assessments whose 20-session validations mature
before the next analysis date. Only after the historical chain is pinned, run
matched independent controls for the same dates. Repeat the last historical and
independent arms once to estimate nondeterministic model variance.

**Tech stack:** TradingNG REST API, MySQL run records, OAuth2 service token,
Codex Gateway, TradingAgents runner, YFinance validation data, run artifacts,
and the assessment comparison API.

## Global constraints

- Do not modify the TradingAgents submodule.
- Do not change scheduler concurrency, Gateway settings, or data-provider
  routing during the experiment.
- Submit NVDA runs sequentially. The existing same-ticker admission lock remains
  enabled.
- Pin every admitted run to `gpt-5.6-sol`, reasoning effort `xhigh`, depth
  `deep`, Chinese output, all four stock analysts, SPY benchmark, and the same
  provider routing.
- Historical runs must be submitted and admitted before any matched independent
  controls are submitted.
- Existing NVDA runs dated `2026-07-25` are not test seeds: they currently have
  no completed validation and contribute zero eligible memory entries.
- A failed or contaminated run is not silently replaced. Record the failure and
  apply the retry rule below.
- Store generated experiment manifests under
  `var/experiments/nvda-history-20260726/`; do not commit runtime data.

## Why this design

Three options were considered:

1. **Historical chain only:** Four runs are fast, but any decision change could
   be ordinary model variance or changing provider output.
2. **Matched sequential A/B chain (selected):** One seed, three historical
   rounds, three same-date independent controls, plus two last-date repeats.
   This preserves memory lineage and provides both a control and a variance
   estimate.
3. **Randomized repeated A/B:** Statistically stronger, but later historical
   runs could ingest earlier control runs, and the number of deep/xhigh calls
   would be disproportionate for an initial production test.

The selected design is the smallest one that tests lineage, lookahead safety,
actual prompt injection, paired decision behavior, and output stability.

## Known interpretation boundary

Historical memory is loaded into `past_context` and injected into the final
Portfolio Manager prompt. It is not injected into the market, social, news,
fundamentals, researcher, trader, or risk analyst prompts. Therefore:

- The test must prove that the Portfolio Manager received and used the memory.
- It must not claim that all TradingAgents stages learned from history.
- Changed analyst reports are treated as model/provider variation, not evidence
  of historical-memory use.

This is also a retrospective integration test, not a fully point-in-time
backtest. YFinance, FRED, and news sources may contain revised or presently
available data. Any evidence dated after an analysis date invalidates that
paired result.

## Fixed experiment matrix

YFinance was checked for both NVDA and SPY. Every selected date is a trading
session and has a complete 20-session exit:

| Order | Label | Analysis date | Mode | Expected memory sources | 20-session exit |
|---:|---|---|---|---|---|
| 1 | S0 | 2024-08-30 | independent | none | 2024-09-30 |
| 2 | H1 | 2024-12-02 | historical | S0 | 2024-12-31 |
| 3 | H2 | 2025-03-03 | historical | S0, H1 | 2025-03-31 |
| 4 | H3a | 2025-06-02 | historical | S0, H1, H2 | 2025-07-01 |
| 5 | H3b | 2025-06-02 | historical | S0, H1, H2 | 2025-07-01 |
| 6 | C1 | 2024-12-02 | independent | none | 2024-12-31 |
| 7 | C2 | 2025-03-03 | independent | none | 2025-03-31 |
| 8 | C3a | 2025-06-02 | independent | none | 2025-07-01 |
| 9 | C3b | 2025-06-02 | independent | none | 2025-07-01 |

H3a and H3b must have the same memory snapshot hash. Same-date runs are
ineligible as sources because source analysis dates must be strictly earlier,
so H3a cannot leak into H3b.

## Phase 1: Preflight and immutable baseline

- [ ] Record the root commit and TradingAgents submodule commit.

  ```bash
  git rev-parse HEAD
  git -C TradingAgents rev-parse HEAD
  ```

- [ ] Confirm the Gateway is idle and record its immutable model fingerprint.

  ```bash
  curl -fsS http://127.0.0.1:8000/internal/status | jq .
  ```

  Pass only when `status=ok`, `active_completions=0`,
  `model=gpt-5.6-sol`, and `reasoning_effort=xhigh`. Record `snapshot_id`.

- [ ] Confirm API, scheduler, validation worker, Gateway, and worker target are
  active.

  ```bash
  systemctl --user is-active \
    tradingng-platform-api.service \
    tradingng-platform-scheduler.service \
    tradingng-platform-validation.service \
    tradingng-codex-gateway.service \
    tradingng-platform-workers.target
  ```

- [ ] Read `/api/v1/system/status`, `/api/v1/system/capacity`, and
  `/api/v1/system/scheduler-policy` with a short-lived service token. Record the
  responses without changing policy.

- [ ] Query `GET /api/v1/assessments?ticker=NVDA&limit=200` and record the
  pre-test run IDs. The preflight expectation is three existing NVDA runs and
  zero historical sources eligible for `2026-07-26`.

- [ ] Create the experiment manifest directly from current observations:

  ```bash
  experiment_dir="var/experiments/nvda-history-20260726"
  mkdir -p "$experiment_dir/runs"
  gateway_snapshot="$(
    curl -fsS http://127.0.0.1:8000/internal/status | jq -r .snapshot_id
  )"
  jq -n \
    --arg experiment "nvda-history-20260726" \
    --arg root_commit "$(git rev-parse HEAD)" \
    --arg tradingagents_commit "$(git -C TradingAgents rev-parse HEAD)" \
    --arg gateway_snapshot_id "$gateway_snapshot" \
    '{
      experiment: $experiment,
      root_commit: $root_commit,
      tradingagents_commit: $tradingagents_commit,
      gateway_snapshot_id: $gateway_snapshot_id,
      runs: {}
    }' > "$experiment_dir/manifest.json"
  ```

## Phase 2: Submission contract

Use one short-lived token in `TRADINGNG_TEST_TOKEN` and this common request
shape:

```bash
experiment_api="https://ushome.amycat.com/api/v1"
curl -fsS -X POST "$experiment_api/assessments" \
  -H "Authorization: Bearer $TRADINGNG_TEST_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "items": [{"ticker": "NVDA", "analysis_date": "2024-08-30"}],
    "analysts": ["market", "social", "news", "fundamentals"],
    "depth": "deep",
    "memory_mode": "independent",
    "language": "Chinese",
    "idempotency_key": "nvda-historical-20260726-s0"
  }'
```

For each label, substitute only the matrix date, memory mode, and the following
idempotency key:

| Label | Idempotency key |
|---|---|
| S0 | `nvda-historical-20260726-s0` |
| H1 | `nvda-historical-20260726-h1` |
| H2 | `nvda-historical-20260726-h2` |
| H3a | `nvda-historical-20260726-h3a` |
| H3b | `nvda-historical-20260726-h3b` |
| C1 | `nvda-historical-20260726-c1` |
| C2 | `nvda-historical-20260726-c2` |
| C3a | `nvda-historical-20260726-c3a` |
| C3b | `nvda-historical-20260726-c3b` |

Immediately record each returned run ID in the manifest.

## Phase 3: Seed and historical chain

Perform each task completely before submitting the next label.

### Task S0: Create the validated seed

- [ ] Submit S0 as `independent`.
- [ ] Wait for terminal status `succeeded`.
- [ ] Assert run detail reports `memory.mode=independent` and zero sources.
- [ ] Wait for 1-, 5-, and 20-session validations to report `completed`.
- [ ] Assert the 20-session validation exit is `2024-09-30`.
- [ ] Save decision, validation, evidence, artifact, event, and run-detail JSON
  in the experiment directory.

### Task H1: Prove first historical injection

- [ ] Submit H1 as `historical`.
- [ ] As soon as it is admitted, fetch its run detail.
- [ ] Assert exactly one memory source: S0, horizon 20, exit `2024-09-30`.
- [ ] Assert the memory source exit is strictly earlier than `2024-12-02`.
- [ ] Wait for success and all validations; assert the 20-session exit is
  `2024-12-31`.
- [ ] Inspect `memory_context` and `llm_interactions` artifacts as described in
  Phase 5 before advancing.

### Task H2: Prove cumulative memory

- [ ] Submit H2 as `historical`.
- [ ] Assert sources are exactly S0 and H1, both using their highest completed
  eligible horizon.
- [ ] Assert neither source has an exit date on or after `2025-03-03`.
- [ ] Wait for success and all validations; assert the 20-session exit is
  `2025-03-31`.
- [ ] Complete the Phase 5 artifact checks before advancing.

### Tasks H3a and H3b: Prove stable source resolution

- [ ] Submit H3a as `historical`.
- [ ] Assert sources are exactly S0, H1, and H2.
- [ ] Record `memory.snapshot_sha256`, every source ID, validation ID, and
  `content_sha256`.
- [ ] Wait for H3a success. H3a validation need not finish before H3b admission
  because its analysis date equals H3b and is therefore ineligible.
- [ ] Submit H3b as `historical`.
- [ ] Assert H3b has exactly the same memory snapshot and source content hashes
  as H3a.
- [ ] Wait for both H3 runs to complete all validations and assert their
  20-session exits are `2025-07-01`.

## Phase 4: Matched independent controls

Only begin after H3b has been admitted and its memory snapshot recorded.

- [ ] Submit C1, C2, C3a, and C3b sequentially as `independent`.
- [ ] For every control, assert `memory.mode=independent`, zero sources, and an
  empty rendered memory context.
- [ ] Wait for success and all three validations for every control.
- [ ] For each same-date pair, assert entry date, exit date, raw return,
  benchmark return, alpha, MAE, and MFE are identical. A mismatch means provider
  data drift or a validation defect and invalidates the pair.
- [ ] Assert H3a/H3b share one historical memory hash while C3a/C3b both have
  empty independent memory.

## Phase 5: Traceability and prompt-use checks

Apply these checks to H1, H2, H3a, and H3b:

- [ ] The run snapshot, `memory_context` artifact, and run-detail API expose the
  same memory snapshot hash.
- [ ] Each source run ID links to an immutable successful run with a completed
  validation ID.
- [ ] Each memory entry contains the prior rating, prior decision, validated
  return, alpha, MAE, MFE, direction result, target result, and the warning that
  it is retrospective calibration rather than current evidence.
- [ ] The final Portfolio Manager request in `llm_interactions` contains
  `Lessons from prior decisions and outcomes`, the expected source dates, and
  the expected reflection text.
- [ ] Earlier analyst/research/trader/risk requests do not contain that lesson
  block. This confirms the actual designed injection boundary.
- [ ] No prompt contains a source from an equal or later analysis date.
- [ ] No evidence query requests an end date after the run analysis date.
- [ ] No report treats an event published after the analysis date as known.
  If an evidence item has no reliable publication/effective date, mark that
  item `unverified`; do not silently count it as lookahead-safe.
- [ ] Every artifact opened through the API matches its recorded SHA-256 and
  returns no integrity error.

## Phase 6: Decision and validation comparison

Call `POST /api/v1/assessment-comparisons` once with all nine run IDs. Then build
a comparison table with one row per run and these columns:

- label, run ID, status, analysis date, rating, price target, time horizon;
- gateway snapshot, config snapshot, memory snapshot, source count;
- 1/5/20-session `direction_correct` and `price_target_hit`;
- raw return, benchmark return, alpha, MAE, and MFE;
- evidence count, LLM call count, tool call count, total tokens, and runtime;
- retry count and any data-freshness or lookahead warning.

Use these paired comparisons:

| Historical | Independent | Purpose |
|---|---|---|
| H1 | C1 | One prior lesson |
| H2 | C2 | Two prior lessons |
| H3a | C3a | Three prior lessons |
| H3b | C3b | Repeat pair |
| H3a | H3b | Historical-mode variance |
| C3a | C3b | Independent-mode variance |

Do not compare raw alpha between same-date arms as a model-quality metric:
market alpha is shared by both arms. Compare the decisions against that outcome.
Calculate:

```text
rating exposure:
  Buy=+1.0, Overweight=+0.5, Hold=0.0, Underweight=-0.5, Sell=-1.0

decision-aligned alpha = rating exposure × 20-session alpha
rating distance = absolute difference in the ordinal scale
  Sell=0, Underweight=1, Hold=2, Overweight=3, Buy=4
```

Primary outcome measures:

1. 20-session `direction_correct`;
2. decision-aligned 20-session alpha;
3. price-target hit rate when both arms provide targets;
4. whether the Portfolio Manager accurately uses a prior failure/success without
   copying the old rating as current evidence;
5. historical-versus-control rating distance compared with H3 and C3 repeat
   variance.

## Pass, caution, and fail criteria

### Functional pass

All of the following are mandatory:

- Nine runs succeed and their three validations complete.
- H1/H2/H3 source sets and H3 repeat hashes match the matrix exactly.
- Every independent control has zero memory sources.
- Every source exit date is strictly earlier than its target analysis date.
- The Portfolio Manager prompt contains the expected memory; earlier stages do
  not.
- Same-date validation market paths are identical.
- No 403, lost session, artifact integrity failure, cross-run memory leak, or
  unbounded retry occurs.

### Benefit signal

Label the pilot `promising`, not statistically proven, only when:

- historical arms improve or preserve 20-session direction correctness in at
  least three of the four matched comparisons;
- mean decision-aligned alpha is no worse than controls;
- no historical arm is more than one rating step worse than its control when
  the control direction is correct;
- H3 historical repeat disagreement is no greater than C3 independent repeat
  disagreement; and
- manual trace review confirms that memory affected the final synthesis rather
  than merely being copied.

### Caution

Report `functionally correct, benefit inconclusive` when lineage and injection
pass but:

- paired decisions mostly match;
- historical/control differences are no larger than repeat variance; or
- too many historical evidence items lack point-in-time publication metadata.

### Fail

Stop and report failure immediately for:

- a missing, extra, equal-date, later-date, or wrong-ticker memory source;
- H3a and H3b receiving different memory hashes;
- historical lessons appearing in stages outside the Portfolio Manager;
- future-dated evidence influencing a report;
- config/model/reasoning/provider drift between paired runs;
- validation paths differing between same-date arms;
- memory artifacts not matching their immutable run snapshots; or
- an assessment or validation entering a repeated failure loop.

## Retry and stop policy

- Retry one failed assessment at most once and only before advancing to the next
  label.
- Keep a retry only if model, reasoning effort, provider routing, requested
  configuration, and expected memory sources still match. Record both run IDs.
- A second failure aborts the experiment.
- Validation `provider_unavailable` may retry under the existing scheduler.
  `invalid_market_data`, `calculation_error`, or a mismatched market path aborts
  the affected pair.
- Do not raise concurrency to accelerate this test. Same-ticker serialization is
  part of the safety boundary.
- Do not delete completed runs. Preserve them as immutable experiment evidence.

## Expected duration and resource budget

Existing successful NVDA deep/xhigh runs took approximately 27 minutes each.
Nine sequential runs therefore require about 4 hours of model time, plus
validation and artifact review. Reserve a 5-hour window and do not begin if
another NVDA task is active. Other tickers may continue under normal admission
policy.

## Final deliverables

Create these runtime-only files:

```text
var/experiments/nvda-history-20260726/
├── manifest.json
├── runs/S0/
├── runs/H1/
├── runs/H2/
├── runs/H3a/
├── runs/H3b/
├── runs/C1/
├── runs/C2/
├── runs/C3a/
├── runs/C3b/
├── comparison.csv
└── report.md
```

Each run directory contains `run.json`, `decision.json`, `validations.json`,
`events.json`, `evidence.json`, and `artifacts.json`.

`report.md` must state separately:

1. whether historical selection and injection are correct;
2. whether the test is lookahead-safe enough to interpret;
3. whether the benefit signal is promising, inconclusive, or adverse;
4. the observed stability of repeated historical and independent runs;
5. any Gateway, provider, artifact, or validation failures;
6. the exact nine run IDs and immutable snapshot hashes.

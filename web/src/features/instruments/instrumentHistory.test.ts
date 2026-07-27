import type { InstrumentHistoryItem } from "../../api/records";
import {
  groupInstrumentHistory,
  orderInstrumentHistory,
  projectInstrumentHistory,
} from "./instrumentHistory";

function item(
  id: string,
  requestId: string,
  attempt: number,
  analysisDate: string,
  createdAt: string,
): InstrumentHistoryItem {
  return {
    run: {
      id,
      request_id: requestId,
      ticker: "NVDA",
      asset_type: "stock",
      analysis_date: analysisDate,
      status: attempt === 2 ? "succeeded" : "failed",
      attempt,
      created_at: createdAt,
    },
    rating: attempt === 2 ? "Underweight" : null,
    executive_summary: null,
    price_target: null,
    gateway_model: "gpt-5.6-sol",
    gateway_reasoning_effort: "xhigh",
    config_snapshot_sha256: `sha-${id}`,
    validation_outcome: null,
    validations: [],
    memory_mode: "independent",
    memory_source_count: 0,
    is_latest_attempt: attempt === 2,
    request_attempt_count: requestId === "request-retry" ? 2 : 1,
  };
}

test("groups retries under the final attempt and returns research events chronologically", () => {
  const newest = item(
    "run-retry-2",
    "request-retry",
    2,
    "2026-07-25",
    "2026-07-25T14:00:00Z",
  );
  const firstAttempt = item(
    "run-retry-1",
    "request-retry",
    1,
    "2026-07-25",
    "2026-07-25T12:00:00Z",
  );
  const oldest = item(
    "run-old",
    "request-old",
    1,
    "2026-06-01",
    "2026-06-01T12:00:00Z",
  );
  const descending = [newest, firstAttempt, oldest];

  const groups = groupInstrumentHistory(descending);

  expect(groups.map((group) => group.primary.run.id)).toEqual(["run-old", "run-retry-2"]);
  expect(groups[1].priorAttempts.map((attempt) => attempt.run.id)).toEqual(["run-retry-1"]);
  expect(descending.map((entry) => entry.run.id)).toEqual([
    "run-retry-2",
    "run-retry-1",
    "run-old",
  ]);
});

test("defaults the research projection to newest first without reversing transition meaning", () => {
  const newest = item(
    "run-retry-2",
    "request-retry",
    2,
    "2026-07-25",
    "2026-07-25T14:00:00Z",
  );
  const firstAttempt = item(
    "run-retry-1",
    "request-retry",
    1,
    "2026-07-25",
    "2026-07-25T12:00:00Z",
  );
  const oldest = item(
    "run-old",
    "request-old",
    1,
    "2026-06-01",
    "2026-06-01T12:00:00Z",
  );
  oldest.run.status = "succeeded";
  oldest.rating = "Hold";

  const projected = projectInstrumentHistory([newest, firstAttempt, oldest]);

  expect(projected.map((event) => event.primary.run.id)).toEqual([
    "run-retry-2",
    "run-old",
  ]);
  expect(projected[0].transition).toBe("Hold → Underweight");
  expect(orderInstrumentHistory(projected, "oldest").map((event) => event.primary.run.id))
    .toEqual(["run-old", "run-retry-2"]);
  expect(projected.map((event) => event.primary.run.id)).toEqual([
    "run-retry-2",
    "run-old",
  ]);
});

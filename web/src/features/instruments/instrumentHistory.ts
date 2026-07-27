import type { InstrumentHistoryItem } from "../../api/records";

export interface InstrumentHistoryGroup {
  primary: InstrumentHistoryItem;
  priorAttempts: InstrumentHistoryItem[];
}

export function groupInstrumentHistory(
  items: InstrumentHistoryItem[],
): InstrumentHistoryGroup[] {
  const byRequest = new Map<string, InstrumentHistoryItem[]>();
  for (const item of items) {
    const attempts = byRequest.get(item.run.request_id) ?? [];
    attempts.push(item);
    byRequest.set(item.run.request_id, attempts);
  }

  return [...byRequest.values()]
    .map((attempts) => {
      const ordered = [...attempts].sort(
        (left, right) => right.run.attempt - left.run.attempt
          || right.run.created_at.localeCompare(left.run.created_at),
      );
      return { primary: ordered[0], priorAttempts: ordered.slice(1) };
    })
    .sort(
      (left, right) => left.primary.run.analysis_date.localeCompare(
        right.primary.run.analysis_date,
      ) || left.primary.run.created_at.localeCompare(right.primary.run.created_at),
    );
}

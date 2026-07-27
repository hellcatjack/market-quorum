import type { InstrumentHistoryItem } from "../../api/records";
import type { UiLocale } from "../../i18n/I18nProvider";
import { ratingTransition } from "../dashboard/instrumentPresentation";

export interface InstrumentHistoryGroup {
  primary: InstrumentHistoryItem;
  priorAttempts: InstrumentHistoryItem[];
}

export interface InstrumentHistoryEvent extends InstrumentHistoryGroup {
  transition: string | null;
}

export type InstrumentHistoryOrder = "newest" | "oldest";

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

export function projectInstrumentHistory(
  items: InstrumentHistoryItem[],
  locale: UiLocale = "zh-CN",
): InstrumentHistoryEvent[] {
  const chronological = groupInstrumentHistory(items);
  const projected = chronological.map((group, index) => {
    const previousRating = [...chronological.slice(0, index)]
      .reverse()
      .find((candidate) => Boolean(candidate.primary.rating))
      ?.primary.rating ?? null;
    const transition = group.primary.rating
      ? ratingTransition(previousRating, group.primary.rating, locale)
      : null;
    return { ...group, transition };
  });
  return projected.reverse();
}

export function orderInstrumentHistory(
  events: InstrumentHistoryEvent[],
  order: InstrumentHistoryOrder,
): InstrumentHistoryEvent[] {
  const newestFirst = [...events].sort(
    (left, right) => right.primary.run.analysis_date.localeCompare(
      left.primary.run.analysis_date,
    ) || right.primary.run.created_at.localeCompare(left.primary.run.created_at),
  );
  return order === "newest" ? newestFirst : newestFirst.reverse();
}

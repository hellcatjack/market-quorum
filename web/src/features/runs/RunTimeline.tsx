import type { RunEvent } from "../../api/events";
import type { Artifact, Evidence, LlmInteraction, RunStep } from "../../api/records";
import { useI18n } from "../../i18n/I18nProvider";
import {
  eventTypeLabel,
  freshnessLabel,
  modelRouteLabel,
  phaseLabel,
  reasoningEffortLabel,
  stepStatusLabel,
} from "../../i18n/domainLabels";
import { ArtifactPreview } from "./ArtifactPreview";
import { bindArtifactsToEvents } from "./artifactBindings";

const KIND_PRIORITY = {
  step: 0,
  event: 1,
  llm: 2,
  evidence: 3,
} as const;

type TimelineItem =
  | {
      kind: "llm";
      id: string;
      stableKey: string;
      timestamp: string;
      record: LlmInteraction;
      phase: string | null;
    }
  | {
      kind: "step";
      id: string;
      stableKey: string;
      timestamp: string | null;
      record: RunStep;
    }
  | {
      kind: "event";
      id: string;
      stableKey: string;
      timestamp: string;
      record: RunEvent;
    }
  | {
      kind: "evidence";
      id: string;
      stableKey: string;
      timestamp: string;
      record: Evidence;
      phase: string | null;
    };

interface RunTimelineProps {
  steps: RunStep[];
  events: RunEvent[];
  evidence: Evidence[];
  llmInteractions?: LlmInteraction[];
  artifacts: Artifact[];
  canReadArtifacts: boolean;
}

function timestampValue(value: string | null): number | null {
  if (!value) return null;
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? null : parsed;
}

function phaseForTimestamp(value: string, steps: RunStep[]): string | null {
  const timestamp = timestampValue(value);
  if (timestamp === null) return null;

  const timedSteps = steps
    .map((step) => ({
      step,
      start: timestampValue(step.started_at),
      finish: timestampValue(step.finished_at),
    }))
    .filter(
      (candidate): candidate is {
        step: RunStep;
        start: number;
        finish: number | null;
      } => candidate.start !== null,
    );
  const preceding = timedSteps
    .filter((candidate) => candidate.start <= timestamp)
    .sort((left, right) => right.start - left.start);

  const covering = preceding.find(
    (candidate) => candidate.finish === null || timestamp <= candidate.finish,
  );
  const following = timedSteps
    .filter((candidate) => candidate.start > timestamp)
    .sort((left, right) => left.start - right.start)[0];
  return (covering ?? preceding[0] ?? following)?.step.name ?? null;
}

function normalizeTimeline({
  steps,
  events,
  evidence,
  llmInteractions = [],
}: Pick<RunTimelineProps, "steps" | "events" | "evidence" | "llmInteractions">): TimelineItem[] {
  const items: TimelineItem[] = [
    ...steps.map((record) => ({
      kind: "step" as const,
      id: `step-${record.name}-${record.attempt}`,
      stableKey: `${record.name}-${record.attempt}`,
      timestamp: record.started_at,
      record,
    })),
    ...events.map((record) => ({
      kind: "event" as const,
      id: `event-${record.sequence}`,
      stableKey: String(record.sequence).padStart(12, "0"),
      timestamp: record.created_at,
      record,
    })),
    ...llmInteractions.map((record) => ({
      kind: "llm" as const,
      id: `llm-${record.sequence}`,
      stableKey: String(record.sequence).padStart(12, "0"),
      timestamp: record.started_at,
      record,
      phase: phaseForTimestamp(record.started_at, steps),
    })),
    ...evidence.map((record) => ({
      kind: "evidence" as const,
      id: `evidence-${record.id}`,
      stableKey: record.id,
      timestamp: record.collected_at,
      record,
      phase: phaseForTimestamp(record.collected_at, steps),
    })),
  ];

  return items.sort((left, right) => {
    const leftTime = timestampValue(left.timestamp);
    const rightTime = timestampValue(right.timestamp);
    if (leftTime === null && rightTime !== null) return 1;
    if (leftTime !== null && rightTime === null) return -1;
    if (leftTime !== rightTime) return (leftTime ?? 0) - (rightTime ?? 0);
    const kindDifference = KIND_PRIORITY[left.kind] - KIND_PRIORITY[right.kind];
    if (kindDifference !== 0) return kindDifference;
    return left.stableKey.localeCompare(right.stableKey);
  });
}

export function LocalTime({ value }: { value: string | null }) {
  const { formatDateTime } = useI18n();
  if (!value) return <span>—</span>;
  return (
    <time dateTime={value} title={value}>
      {formatDateTime(value)}
    </time>
  );
}

function TimePhase({ phase }: { phase: string | null }) {
  const { locale, t } = useI18n();
  return (
    <span className="timeline-entry__phase">
      {t("时间归属：{phase}", { phase: phase ? phaseLabel(phase, locale) : t("未关联阶段") })}
    </span>
  );
}

function StepEntry({ item }: { item: Extract<TimelineItem, { kind: "step" }> }) {
  const { record } = item;
  const { locale, t } = useI18n();
  return (
    <div className="timeline-entry__body" data-testid={item.id}>
      <div className="timeline-entry__headline">
        <span className="timeline-entry__badge">{t("阶段")}</span>
        <strong>{phaseLabel(record.name, locale)}</strong>
        <span>
          {stepStatusLabel(record.status, locale)} · {t("第 {attempt} 次", { attempt: record.attempt })}
        </span>
      </div>
      <div className="timeline-entry__times">
        <span>{t("开始")} <LocalTime value={record.started_at} /></span>
        <span>{t("结束")} <LocalTime value={record.finished_at} /></span>
      </div>
      {record.summary ? <p>{record.summary}</p> : null}
      {record.error_code ? <p className="timeline-entry__error">{t("错误")}：{record.error_code}</p> : null}
    </div>
  );
}

function EventEntry({
  item,
  artifacts,
}: {
  item: Extract<TimelineItem, { kind: "event" }>;
  artifacts: Artifact[];
}) {
  const { record } = item;
  const { locale, t } = useI18n();
  return (
    <div data-testid={item.id}>
      <details className="timeline-entry__disclosure" data-testid="timeline-disclosure">
        <summary className="timeline-entry__summary">
          <span className="timeline-entry__badge timeline-entry__badge--event">{t("事件")}</span>
          <span className="timeline-entry__title">
            <strong>{eventTypeLabel(record.event_type, locale)}</strong>
            <small>
              {t("事件序号 #{sequence}", { sequence: record.sequence })}
              {artifacts.length > 0 ? ` · ${t("产物 {count}", { count: artifacts.length })}` : ""}
            </small>
          </span>
          <LocalTime value={record.created_at} />
        </summary>
        <div className="timeline-entry__details">
          <code>{record.event_type}</code>
          <strong>{t("事件载荷")}</strong>
          <pre>{JSON.stringify(record.payload, null, 2)}</pre>
          {artifacts.length > 0 ? (
            <div className="timeline-entry__artifacts">
              <strong>{t("该事件产生的产物")}</strong>
              {artifacts.map((artifact) => (
                <ArtifactPreview key={artifact.id} artifact={artifact} />
              ))}
            </div>
          ) : null}
        </div>
      </details>
    </div>
  );
}

function LlmEntry({ item }: { item: Extract<TimelineItem, { kind: "llm" }> }) {
  const { record } = item;
  const { formatDuration, locale, t } = useI18n();
  return (
    <div className="timeline-entry__body timeline-entry__body--llm" data-testid={item.id}>
      <div className="timeline-entry__headline">
        <span className="timeline-entry__badge timeline-entry__badge--llm">{t("模型调用")}</span>
        <strong>{modelRouteLabel(record.route, locale)}</strong>
        <span>{stepStatusLabel(record.status, locale)}</span>
      </div>
      <TimePhase phase={item.phase} />
      <dl className="timeline-entry__facts timeline-entry__facts--llm">
        <div><dt>{t("实际模型")}</dt><dd>{record.physical_model ?? "—"}</dd></div>
        <div><dt>{t("思考深度")}</dt><dd>{reasoningEffortLabel(record.reasoning_effort, locale)}</dd></div>
        <div><dt>{t("模型别名")}</dt><dd><code>{record.model_alias ?? "—"}</code></dd></div>
        <div><dt>{t("耗时")}</dt><dd>{formatDuration(record.duration_ms ?? null)}</dd></div>
      </dl>
      <div className="timeline-entry__times">
        <span>{t("开始")} <LocalTime value={record.started_at} /></span>
        <span>{t("结束")} <LocalTime value={record.completed_at} /></span>
      </div>
      {record.error_code ? <p className="timeline-entry__error">{t("错误")}：{record.error_code}</p> : null}
    </div>
  );
}

function EvidenceEntry({ item }: { item: Extract<TimelineItem, { kind: "evidence" }> }) {
  const { record } = item;
  const { locale, t } = useI18n();
  return (
    <div data-testid={item.id}>
      <details className="timeline-entry__disclosure" data-testid="timeline-disclosure">
        <summary className="timeline-entry__summary">
          <span className="timeline-entry__badge timeline-entry__badge--evidence">{t("证据")}</span>
          <span className="timeline-entry__title">
            <strong>{record.source}</strong>
            <small>{record.tool_name}</small>
          </span>
          <TimePhase phase={item.phase} />
          <LocalTime value={record.collected_at} />
        </summary>
        <div className="timeline-entry__details">
          <dl className="timeline-entry__facts">
            <div><dt>{t("有效时间")}</dt><dd><LocalTime value={record.effective_at} /></dd></div>
            <div><dt>{t("新鲜度")}</dt><dd>{freshnessLabel(record.freshness, locale)}</dd></div>
          </dl>
          <strong>{t("调用参数")}</strong>
          <pre>{JSON.stringify(record.arguments, null, 2)}</pre>
          <code>sha256: {record.content_hash}</code>
        </div>
      </details>
    </div>
  );
}

export function RunTimeline(props: RunTimelineProps) {
  const { t } = useI18n();
  const items = normalizeTimeline(props);
  const artifactBindings = bindArtifactsToEvents(props.artifacts, props.events);
  return (
    <section className="detail-panel detail-panel--wide research-timeline">
      <div className="section-heading timeline-heading">
        <div><p className="eyebrow">{t("执行轨迹")}</p><h2>{t("研究时间线")}</h2></div>
        {!props.canReadArtifacts ? <p>{t("当前账号无产物读取权限")}</p> : null}
      </div>
      <ol className="timeline">
        {items.map((item) => (
          <li
            className={`timeline-entry timeline-entry--${item.kind}`}
            data-testid="timeline-entry"
            data-timeline-id={item.id}
            key={item.id}
          >
            <span
              className={`timeline-marker timeline-marker--${item.kind === "step" ? item.record.status : item.kind}`}
              aria-hidden="true"
            />
            {item.kind === "step" ? <StepEntry item={item} /> : null}
            {item.kind === "event" ? (
              <EventEntry
                item={item}
                artifacts={artifactBindings.byEventSequence.get(item.record.sequence) ?? []}
              />
            ) : null}
            {item.kind === "llm" ? <LlmEntry item={item} /> : null}
            {item.kind === "evidence" ? <EvidenceEntry item={item} /> : null}
          </li>
        ))}
      </ol>
      {artifactBindings.unassociated.length > 0 ? (
        <aside className="timeline-unassociated" data-testid="unassociated-artifacts">
          <strong>{t("未关联产物")}</strong>
          <p>{t("这些归档文件没有可用于确定业务产生时间的事件。")}</p>
          {artifactBindings.unassociated.map((artifact) => (
            <ArtifactPreview key={artifact.id} artifact={artifact} />
          ))}
        </aside>
      ) : null}
      {items.length === 0 && artifactBindings.unassociated.length === 0 ? (
        <p className="section-empty">{t("暂无研究轨迹。")}</p>
      ) : null}
    </section>
  );
}

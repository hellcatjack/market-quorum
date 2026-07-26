import type { RunEvent } from "../../api/events";
import type { Artifact, Evidence, RunStep } from "../../api/records";
import { ArtifactPreview } from "./ArtifactPreview";
import { bindArtifactsToEvents } from "./artifactBindings";

const PHASE_LABELS: Record<string, string> = {
  queued: "等待调度",
  admitted: "任务准入",
  starting: "启动准备",
  running_analysts: "分析师研究",
  research_debate: "研究辩论",
  trader_plan: "交易方案",
  risk_debate: "风险辩论",
  portfolio_decision: "组合决策",
  finalizing: "结果归档",
};

const STATUS_LABELS: Record<string, string> = {
  pending: "等待中",
  running: "进行中",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
};

const KIND_PRIORITY = {
  step: 0,
  event: 1,
  evidence: 2,
} as const;

type TimelineItem =
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
  artifacts: Artifact[];
  canReadArtifacts: boolean;
}

function timestampValue(value: string | null): number | null {
  if (!value) return null;
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? null : parsed;
}

function phaseLabel(name: string): string {
  return PHASE_LABELS[name] ?? name;
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
}: Pick<RunTimelineProps, "steps" | "events" | "evidence">): TimelineItem[] {
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
  if (!value) return <span>—</span>;
  return (
    <time dateTime={value} title={value}>
      {new Date(value).toLocaleString()}
    </time>
  );
}

function TimePhase({ phase }: { phase: string | null }) {
  return (
    <span className="timeline-entry__phase">
      时间归属：{phase ? phaseLabel(phase) : "未关联阶段"}
    </span>
  );
}

function StepEntry({ item }: { item: Extract<TimelineItem, { kind: "step" }> }) {
  const { record } = item;
  return (
    <div className="timeline-entry__body" data-testid={item.id}>
      <div className="timeline-entry__headline">
        <span className="timeline-entry__badge">阶段</span>
        <strong>{phaseLabel(record.name)}</strong>
        <span>
          {STATUS_LABELS[record.status] ?? record.status} · 第 {record.attempt} 次
        </span>
      </div>
      <div className="timeline-entry__times">
        <span>开始 <LocalTime value={record.started_at} /></span>
        <span>结束 <LocalTime value={record.finished_at} /></span>
      </div>
      {record.summary ? <p>{record.summary}</p> : null}
      {record.error_code ? <p className="timeline-entry__error">错误：{record.error_code}</p> : null}
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
  return (
    <div data-testid={item.id}>
      <details className="timeline-entry__disclosure" data-testid="timeline-disclosure">
        <summary className="timeline-entry__summary">
          <span className="timeline-entry__badge timeline-entry__badge--event">事件</span>
          <span className="timeline-entry__title">
            <strong>{record.event_type}</strong>
            <small>
              事件序号 #{record.sequence}
              {artifacts.length > 0 ? ` · 产物 ${artifacts.length}` : ""}
            </small>
          </span>
          <LocalTime value={record.created_at} />
        </summary>
        <div className="timeline-entry__details">
          <strong>事件载荷</strong>
          <pre>{JSON.stringify(record.payload, null, 2)}</pre>
          {artifacts.length > 0 ? (
            <div className="timeline-entry__artifacts">
              <strong>该事件产生的产物</strong>
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

function EvidenceEntry({ item }: { item: Extract<TimelineItem, { kind: "evidence" }> }) {
  const { record } = item;
  return (
    <div data-testid={item.id}>
      <details className="timeline-entry__disclosure" data-testid="timeline-disclosure">
        <summary className="timeline-entry__summary">
          <span className="timeline-entry__badge timeline-entry__badge--evidence">证据</span>
          <span className="timeline-entry__title">
            <strong>{record.source}</strong>
            <small>{record.tool_name}</small>
          </span>
          <TimePhase phase={item.phase} />
          <LocalTime value={record.collected_at} />
        </summary>
        <div className="timeline-entry__details">
          <dl className="timeline-entry__facts">
            <div><dt>有效时间</dt><dd><LocalTime value={record.effective_at} /></dd></div>
            <div><dt>新鲜度</dt><dd>{record.freshness ?? "未标注"}</dd></div>
          </dl>
          <strong>调用参数</strong>
          <pre>{JSON.stringify(record.arguments, null, 2)}</pre>
          <code>sha256: {record.content_hash}</code>
        </div>
      </details>
    </div>
  );
}

export function RunTimeline(props: RunTimelineProps) {
  const items = normalizeTimeline(props);
  const artifactBindings = bindArtifactsToEvents(props.artifacts, props.events);
  return (
    <section className="detail-panel detail-panel--wide research-timeline">
      <div className="section-heading timeline-heading">
        <div><p className="eyebrow">执行轨迹</p><h2>研究时间线</h2></div>
        {!props.canReadArtifacts ? <p>当前账号无产物读取权限</p> : null}
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
            {item.kind === "evidence" ? <EvidenceEntry item={item} /> : null}
          </li>
        ))}
      </ol>
      {artifactBindings.unassociated.length > 0 ? (
        <aside className="timeline-unassociated" data-testid="unassociated-artifacts">
          <strong>未关联产物</strong>
          <p>这些归档文件没有可用于确定业务产生时间的事件。</p>
          {artifactBindings.unassociated.map((artifact) => (
            <ArtifactPreview key={artifact.id} artifact={artifact} />
          ))}
        </aside>
      ) : null}
      {items.length === 0 && artifactBindings.unassociated.length === 0 ? (
        <p className="section-empty">暂无研究轨迹。</p>
      ) : null}
    </section>
  );
}

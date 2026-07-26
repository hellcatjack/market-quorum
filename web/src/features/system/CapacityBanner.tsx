import type { Capacity } from "../../api/assessments";

function duration(seconds: number | null): string {
  if (seconds === null) return "—";
  const minutes = Math.floor(seconds / 60);
  const remaining = seconds % 60;
  return minutes > 0 ? `${minutes}分${String(remaining).padStart(2, "0")}秒` : `${remaining}秒`;
}

export function CapacityBanner({ capacity }: { capacity: Capacity }) {
  const freeSlots = Math.max(0, capacity.max_running_total - capacity.admitted_or_running);
  return (
    <section
      className={capacity.admission_allowed ? "capacity-banner" : "capacity-banner capacity-banner--blocked"}
      aria-label="系统容量"
    >
      <div>
        <p className="eyebrow">安全容量</p>
        <strong>
          {capacity.admission_allowed ? "当前可准入" : "当前任务将排队"} · {freeSlots} 个空闲槽位
        </strong>
        <span>
          运行 {capacity.admitted_or_running}/{capacity.max_running_total}，排队 {capacity.queued}
        </span>
      </div>
      <div className="capacity-gateway">
        <span>Gateway 模型</span>
        <strong>{capacity.gateway_model} · {capacity.gateway_reasoning_effort}</strong>
      </div>
      {capacity.oldest_queued_seconds !== null && capacity.queued > 0 ? (
        <p className="capacity-wait">最早任务已等待 {duration(capacity.oldest_queued_seconds)}</p>
      ) : null}
      {!capacity.admission_allowed ? (
        <p className="capacity-reasons" role="alert">
          <strong>容量暂缓：</strong>
          {capacity.admission_reasons.join("、") || "系统正在保护性排队"}。任务仍可进入受控队列。
        </p>
      ) : null}
    </section>
  );
}

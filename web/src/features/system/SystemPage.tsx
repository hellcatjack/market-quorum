import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { getCapacity } from "../../api/assessments";
import { getCurrentUser } from "../../api/records";
import {
  getSchedulerPolicy,
  getSystemStatus,
  type SchedulerPolicy,
  updateSchedulerPolicy,
} from "../../api/system";
import { LocalTime } from "../runs/RunTimeline";
import { CapacityBanner } from "./CapacityBanner";

function PolicyForm({ policy, editable }: { policy: SchedulerPolicy; editable: boolean }) {
  const queryClient = useQueryClient();
  const update = useMutation({
    mutationFn: updateSchedulerPolicy,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["scheduler-policy"] }),
  });
  return (
    <form
      className="policy-form"
      key={`${policy.version}-${editable}`}
      onSubmit={(event) => {
        event.preventDefault();
        const data = new FormData(event.currentTarget);
        update.mutate({
          max_running_total: Number(data.get("max_running_total")),
          hard_max_running_total: policy.hard_max_running_total,
          gateway_active_limit: Number(data.get("gateway_active_limit")),
          cpu_limit_percent: Number(data.get("cpu_limit_percent")),
          minimum_memory_gib: Number(data.get("minimum_memory_gib")),
          minimum_disk_gib: Number(data.get("minimum_disk_gib")),
          minimum_disk_percent: Number(data.get("minimum_disk_percent")),
        });
      }}
    >
      <label><span>最大并发评估</span><input aria-label="最大并发评估" name="max_running_total" type="number" min="1" max={policy.hard_max_running_total} defaultValue={policy.max_running_total} disabled={!editable} /></label>
      <label><span>Gateway 活动上限</span><input name="gateway_active_limit" type="number" min="1" defaultValue={policy.gateway_active_limit} disabled={!editable} /></label>
      <label><span>CPU 上限 %</span><input name="cpu_limit_percent" type="number" min="1" max="100" defaultValue={policy.cpu_limit_percent} disabled={!editable} /></label>
      <label><span>最小可用内存 GiB</span><input name="minimum_memory_gib" type="number" min="0" step="0.5" defaultValue={policy.minimum_memory_gib} disabled={!editable} /></label>
      <label><span>最小可用磁盘 GiB</span><input name="minimum_disk_gib" type="number" min="0" step="0.5" defaultValue={policy.minimum_disk_gib} disabled={!editable} /></label>
      <label><span>最小可用磁盘 %</span><input name="minimum_disk_percent" type="number" min="0" max="100" defaultValue={policy.minimum_disk_percent} disabled={!editable} /></label>
      <p>硬上限 {policy.hard_max_running_total} · 策略版本 {policy.version} · <LocalTime value={policy.updated_at} /></p>
      {editable ? <button className="primary-button" type="submit" disabled={update.isPending}>{update.isPending ? "保存中…" : "保存调度策略"}</button> : <p className="readonly-note">当前账号只有只读权限。</p>}
    </form>
  );
}

export function SystemPage() {
  const user = useQuery({ queryKey: ["current-user"], queryFn: getCurrentUser, retry: false });
  const status = useQuery({ queryKey: ["system-status"], queryFn: getSystemStatus, refetchInterval: 5_000, retry: false });
  const capacity = useQuery({ queryKey: ["system-capacity"], queryFn: getCapacity, refetchInterval: 5_000, retry: false });
  const policy = useQuery({ queryKey: ["scheduler-policy"], queryFn: getSchedulerPolicy, retry: false });
  const editable = Boolean(user.data?.roles.includes("Admin") && user.data.scopes.includes("assessments:admin"));
  return (
    <section className="page-shell system-page">
      <header className="page-header"><p className="eyebrow">运行诊断 / 安全准入</p><h1>系统状态</h1><p>仅展示容量与健康元数据，不返回密钥、环境值或本地路径。</p></header>
      {capacity.data ? <CapacityBanner capacity={capacity.data} /> : null}
      {status.isError || capacity.isError || policy.isError ? <p className="page-warning" role="alert">部分系统诊断暂时不可用。</p> : null}
      <div className="system-grid">
        <section className="detail-panel">
          <div className="section-heading"><p className="eyebrow">Gateway</p><h2>Codex 接口</h2></div>
          {status.data ? <dl className="system-facts"><div><dt>状态</dt><dd>{status.data.gateway.status}</dd></div><div><dt>模型</dt><dd>{status.data.gateway.model}</dd></div><div><dt>思考深度</dt><dd>{status.data.gateway.reasoning_effort}</dd></div><div><dt>活动请求</dt><dd>{status.data.gateway.active_completions}</dd></div><div><dt>延迟</dt><dd>{status.data.gateway.latency_ms} ms</dd></div><div><dt>快照</dt><dd title={status.data.gateway.snapshot_id}>{status.data.gateway.snapshot_id}</dd></div></dl> : <p role="status">载入中…</p>}
        </section>
        <section className="detail-panel">
          <div className="section-heading"><p className="eyebrow">Worker</p><h2>执行器心跳</h2></div>
          <ul className="health-list">{status.data?.workers.map((worker) => <li key={worker.instance_name}><div><strong>{worker.instance_name}</strong><span>{worker.status}</span></div><LocalTime value={worker.heartbeat_at} /><code>{Object.keys(worker.capabilities).join(", ") || "default"}</code></li>)}</ul>
        </section>
        <section className="detail-panel">
          <div className="section-heading"><p className="eyebrow">依赖保护</p><h2>熔断器</h2></div>
          <ul className="health-list">{status.data?.circuits.map((circuit) => <li key={circuit.name}><div><strong>{circuit.name}</strong><span>{circuit.status} · {circuit.failure_count} 次失败</span></div><span>{circuit.last_error_code ?? "无错误"}</span></li>)}</ul>
        </section>
        <section className="detail-panel">
          <div className="section-heading"><p className="eyebrow">管理策略</p><h2>调度阈值</h2></div>
          {policy.data ? <PolicyForm policy={policy.data} editable={editable} /> : <p role="status">载入中…</p>}
        </section>
      </div>
    </section>
  );
}

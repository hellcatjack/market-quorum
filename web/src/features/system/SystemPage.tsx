import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { getCapacity } from "../../api/assessments";
import { getCurrentUser } from "../../api/records";
import {
  getModelRoutingPolicy,
  getSchedulerPolicy,
  getSystemStatus,
  type ModelRoutingPolicy,
  type SchedulerPolicy,
  updateModelRoutingPolicy,
  updateSchedulerPolicy,
} from "../../api/system";
import { LocalTime } from "../runs/RunTimeline";
import { useI18n } from "../../i18n/I18nProvider";
import { reasoningEffortLabel, systemStatusLabel } from "../../i18n/domainLabels";
import { CapacityBanner } from "./CapacityBanner";

type ModelName = ModelRoutingPolicy["fast"]["model"];
type ReasoningEffort = ModelRoutingPolicy["fast"]["reasoning_effort"];

function PolicyForm({ policy, editable }: { policy: SchedulerPolicy; editable: boolean }) {
  const { t } = useI18n();
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
      <label><span>{t("最大并发评估")}</span><input aria-label={t("最大并发评估")} name="max_running_total" type="number" min="1" max={policy.hard_max_running_total} defaultValue={policy.max_running_total} disabled={!editable} /></label>
      <label><span>{t("Gateway 活动上限")}</span><input name="gateway_active_limit" type="number" min="1" defaultValue={policy.gateway_active_limit} disabled={!editable} /></label>
      <label><span>{t("CPU 上限 %")}</span><input name="cpu_limit_percent" type="number" min="1" max="100" defaultValue={policy.cpu_limit_percent} disabled={!editable} /></label>
      <label><span>{t("最小可用内存 GiB")}</span><input name="minimum_memory_gib" type="number" min="0" step="0.5" defaultValue={policy.minimum_memory_gib} disabled={!editable} /></label>
      <label><span>{t("最小可用磁盘 GiB")}</span><input name="minimum_disk_gib" type="number" min="0" step="0.5" defaultValue={policy.minimum_disk_gib} disabled={!editable} /></label>
      <label><span>{t("最小可用磁盘 %")}</span><input name="minimum_disk_percent" type="number" min="0" max="100" defaultValue={policy.minimum_disk_percent} disabled={!editable} /></label>
      <p>{t("硬上限 {limit} · 策略版本 {version}", { limit: policy.hard_max_running_total, version: policy.version })} · <LocalTime value={policy.updated_at} /></p>
      {editable ? <button className="primary-button" type="submit" disabled={update.isPending}>{update.isPending ? t("保存中…") : t("保存调度策略")}</button> : <p className="readonly-note">{t("当前账号只有只读权限。")}</p>}
    </form>
  );
}

function ModelRoutingForm({
  policy,
  editable,
}: {
  policy: ModelRoutingPolicy;
  editable: boolean;
}) {
  const { locale, t } = useI18n();
  const queryClient = useQueryClient();
  const update = useMutation({
    mutationFn: updateModelRoutingPolicy,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["model-routing"] }),
  });
  const modelOptions = policy.available_models.map((model) => (
    <option key={model} value={model}>{model}</option>
  ));
  const effortOptions = policy.available_reasoning_efforts.map((effort) => (
    <option key={effort} value={effort}>{reasoningEffortLabel(effort, locale)}</option>
  ));
  return (
    <form
      className="policy-form"
      key={`${policy.version}-${editable}`}
      onSubmit={(event) => {
        event.preventDefault();
        const data = new FormData(event.currentTarget);
        update.mutate({
          fast: {
            model: String(data.get("fast_model")) as ModelName,
            reasoning_effort: String(data.get("fast_reasoning_effort")) as ReasoningEffort,
          },
          slow: {
            model: String(data.get("slow_model")) as ModelName,
            reasoning_effort: String(data.get("slow_reasoning_effort")) as ReasoningEffort,
          },
        });
      }}
    >
      <label>
        <span>{t("快速分析模型")}</span>
        <select aria-label={t("快速分析模型")} name="fast_model" defaultValue={policy.fast.model} disabled={!editable}>{modelOptions}</select>
      </label>
      <label>
        <span>{t("快速分析思考深度")}</span>
        <select aria-label={t("快速分析思考深度")} name="fast_reasoning_effort" defaultValue={policy.fast.reasoning_effort} disabled={!editable}>{effortOptions}</select>
      </label>
      <label>
        <span>{t("关键裁决模型")}</span>
        <select aria-label={t("关键裁决模型")} name="slow_model" defaultValue={policy.slow.model} disabled={!editable}>{modelOptions}</select>
      </label>
      <label>
        <span>{t("关键裁决思考深度")}</span>
        <select aria-label={t("关键裁决思考深度")} name="slow_reasoning_effort" defaultValue={policy.slow.reasoning_effort} disabled={!editable}>{effortOptions}</select>
      </label>
      <p>{t("路由版本 {version}", { version: policy.version })} · <LocalTime value={policy.updated_at} /></p>
      {update.isError ? <p className="page-warning" role="alert">{t("模型路由保存失败，请检查选择后重试。")}</p> : null}
      {editable ? <button className="primary-button" type="submit" disabled={update.isPending}>{update.isPending ? t("保存中…") : t("保存模型路由")}</button> : <p className="readonly-note">{t("当前账号只有只读权限。")}</p>}
    </form>
  );
}

export function SystemPage() {
  const { locale, t } = useI18n();
  const user = useQuery({ queryKey: ["current-user"], queryFn: getCurrentUser, retry: false });
  const status = useQuery({ queryKey: ["system-status"], queryFn: getSystemStatus, refetchInterval: 5_000, retry: false });
  const capacity = useQuery({ queryKey: ["system-capacity"], queryFn: getCapacity, refetchInterval: 5_000, retry: false });
  const policy = useQuery({ queryKey: ["scheduler-policy"], queryFn: getSchedulerPolicy, retry: false });
  const modelRouting = useQuery({ queryKey: ["model-routing"], queryFn: getModelRoutingPolicy, retry: false });
  const editable = Boolean(user.data?.roles.includes("Admin") && user.data.scopes.includes("assessments:admin"));
  return (
    <section className="page-shell system-page">
      <header className="page-header"><p className="eyebrow">{t("运行诊断 / 安全准入")}</p><h1>{t("系统状态")}</h1><p>{t("仅展示容量与健康元数据，不返回密钥、环境值或本地路径。")}</p></header>
      {capacity.data ? <CapacityBanner capacity={capacity.data} /> : null}
      {status.isError || capacity.isError || policy.isError || modelRouting.isError ? <p className="page-warning" role="alert">{t("部分系统诊断暂时不可用。")}</p> : null}
      <div className="system-grid">
        <section className="detail-panel">
          <div className="section-heading"><p className="eyebrow">Gateway</p><h2>{t("Gateway 运行状态")}</h2></div>
          {status.data ? <><dl className="system-facts"><div><dt>{t("状态")}</dt><dd>{systemStatusLabel(status.data.gateway.status, locale)}</dd></div><div><dt>{t("活动请求")}</dt><dd>{status.data.gateway.active_completions}</dd></div><div><dt>{t("延迟")}</dt><dd>{status.data.gateway.latency_ms} ms</dd></div><div><dt>{t("快照")}</dt><dd title={status.data.gateway.snapshot_id}>{status.data.gateway.snapshot_id}</dd></div></dl><details className="gateway-compatibility"><summary>{t("兼容默认路由（非 TradingAgents 评估路由）")}</summary><dl className="system-facts"><div><dt>{t("Gateway 默认模型")}</dt><dd>{status.data.gateway.model}</dd></div><div><dt>{t("Gateway 默认思考深度")}</dt><dd>{reasoningEffortLabel(status.data.gateway.reasoning_effort, locale)}</dd></div></dl><p>{t("该默认值仅服务旧版兼容调用；TradingAgents 评估使用下方独立的快速分析与关键裁决路由。")}</p></details></> : <p role="status">{t("载入中…")}</p>}
        </section>
        <section className="detail-panel">
          <div className="section-heading"><p className="eyebrow">Worker</p><h2>{t("执行器心跳")}</h2></div>
          <ul className="health-list">{status.data?.workers.map((worker) => <li key={worker.instance_name}><div><strong>{worker.instance_name}</strong><span>{systemStatusLabel(worker.status, locale)}</span></div><LocalTime value={worker.heartbeat_at} /><code>{Object.keys(worker.capabilities).join(", ") || "default"}</code></li>)}</ul>
        </section>
        <section className="detail-panel">
          <div className="section-heading"><p className="eyebrow">{t("依赖保护")}</p><h2>{t("熔断器")}</h2></div>
          <ul className="health-list">{status.data?.circuits.map((circuit) => <li key={circuit.name}><div><strong>{circuit.name}</strong><span>{systemStatusLabel(circuit.status, locale)} · {t("{count} 次失败", { count: circuit.failure_count })}</span></div><span>{circuit.last_error_code ?? t("无错误")}</span></li>)}</ul>
        </section>
        <section className="detail-panel">
          <div className="section-heading"><p className="eyebrow">{t("管理策略")}</p><h2>{t("调度阈值")}</h2></div>
          {policy.data ? <PolicyForm policy={policy.data} editable={editable} /> : <p role="status">{t("载入中…")}</p>}
        </section>
        <section className="detail-panel">
          <div className="section-heading"><p className="eyebrow">TradingAgents</p><h2>{t("评估模型路由")}</h2></div>
          <p>{t("快速分析路由承担高频研究与辩论；关键裁决路由负责研究裁决和最终投资组合判断。更改只影响之后准入的新任务。")}</p>
          {modelRouting.data ? <ModelRoutingForm policy={modelRouting.data} editable={editable} /> : <p role="status">{t("载入中…")}</p>}
        </section>
      </div>
    </section>
  );
}

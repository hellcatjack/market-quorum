import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useParams } from "wouter";

import { subscribeToRun, type RunEvent } from "../../api/events";
import {
  cancelRun,
  getArtifacts,
  getComments,
  getCurrentUser,
  getDecision,
  getEvidence,
  getReviews,
  getRun,
  getSteps,
  retryRun,
} from "../../api/records";
import { ApiClientError } from "../../api/client";
import { DecisionPanel } from "./DecisionPanel";
import { ReviewPanel } from "./ReviewPanel";
import { RunTimeline } from "./RunTimeline";

const CANCELLABLE = new Set([
  "queued", "admitted", "starting", "running_analysts", "research_debate", "trader_plan",
  "risk_debate", "portfolio_decision", "finalizing",
]);
const RETRYABLE = new Set(["failed", "cancelled", "needs_attention"]);

async function optionalRecord<T>(loader: () => Promise<T>): Promise<T | null> {
  try {
    return await loader();
  } catch (error) {
    if (error instanceof ApiClientError && error.status === 404) return null;
    throw error;
  }
}

function MetadataValue({ value }: { value: string | null | undefined }) {
  return <dd title={value ?? undefined}>{value ?? "等待准入后固定"}</dd>;
}

export function RunDetailPage() {
  const { runId = "" } = useParams<{ runId: string }>();
  const [, navigate] = useLocation();
  const queryClient = useQueryClient();
  const [degraded, setDegraded] = useState(false);
  const [liveEvents, setLiveEvents] = useState<RunEvent[]>([]);
  const lastSequence = useRef(0);
  const user = useQuery({ queryKey: ["current-user"], queryFn: getCurrentUser, retry: false });
  const run = useQuery({
    queryKey: ["run", runId],
    queryFn: () => getRun(runId),
    enabled: Boolean(runId),
    refetchInterval: degraded ? 5_000 : false,
    retry: false,
  });
  const steps = useQuery({
    queryKey: ["run-steps", runId],
    queryFn: () => getSteps(runId),
    enabled: Boolean(runId),
    refetchInterval: degraded ? 5_000 : false,
    retry: false,
  });
  const decision = useQuery({
    queryKey: ["run-decision", runId],
    queryFn: () => optionalRecord(() => getDecision(runId)),
    enabled: Boolean(runId),
    retry: false,
  });
  const evidence = useQuery({
    queryKey: ["run-evidence", runId], queryFn: () => getEvidence(runId), enabled: Boolean(runId), retry: false,
  });
  const artifacts = useQuery({
    queryKey: ["run-artifacts", runId], queryFn: () => getArtifacts(runId), enabled: Boolean(runId) && Boolean(user.data?.scopes.includes("artifacts:read")), retry: false,
  });
  const collaboration = useQuery({
    queryKey: ["run-collaboration", runId],
    queryFn: async () => {
      const [reviews, comments] = await Promise.all([getReviews(runId), getComments(runId)]);
      return { reviews, comments };
    },
    enabled: Boolean(runId),
    retry: false,
  });

  useEffect(() => {
    if (!runId || typeof EventSource === "undefined") return;
    const subscription = subscribeToRun(
      runId,
      lastSequence.current,
      (event) => {
        lastSequence.current = event.sequence;
        setLiveEvents((events) => [...events.filter((item) => item.sequence !== event.sequence), event]);
        void queryClient.invalidateQueries({ queryKey: ["run", runId] });
        void queryClient.invalidateQueries({ queryKey: ["run-steps", runId] });
        if (event.event_type.startsWith("assessment.")) {
          void queryClient.invalidateQueries({ queryKey: ["run-decision", runId] });
          void queryClient.invalidateQueries({ queryKey: ["run-evidence", runId] });
          void queryClient.invalidateQueries({ queryKey: ["run-artifacts", runId] });
        }
      },
      setDegraded,
    );
    return subscription.close;
  }, [queryClient, runId]);

  const cancel = useMutation({
    mutationFn: () => cancelRun(runId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["run", runId] }),
  });
  const retry = useMutation({
    mutationFn: () => retryRun(runId),
    onSuccess: (next) => navigate(`/runs/${next.id}`),
  });

  if (run.isLoading) return <p className="page-shell page-loading" role="status">正在载入评估记录…</p>;
  if (run.isError || !run.data) return <p className="page-shell page-warning" role="alert">无法读取该评估记录。</p>;
  const canCancel = Boolean(user.data?.scopes.includes("assessments:cancel")) && CANCELLABLE.has(run.data.status);
  const canRetry = Boolean(user.data?.scopes.includes("assessments:submit")) && RETRYABLE.has(run.data.status);
  const canReadArtifacts = Boolean(user.data?.scopes.includes("artifacts:read"));
  const completeReport = canReadArtifacts
    ? artifacts.data?.find((artifact) => artifact.kind === "report_18_complete_report") ?? null
    : null;

  return (
    <section className="page-shell run-detail-page">
      <header className="run-hero">
        <div>
          <p className="eyebrow">评估记录 / {run.data.analysis_date}</p>
          <h1>{run.data.ticker} 评估详情</h1>
          <p><span className={`run-status run-status--${run.data.status === "succeeded" ? "success" : "active"}`}>● {run.data.status}</span> · 第 {run.data.attempt} 次尝试</p>
        </div>
        <div className="run-actions">
          <Link href={`/instruments/${run.data.ticker}`}>查看标的历史</Link>
          {canCancel ? <button type="button" onClick={() => cancel.mutate()} disabled={cancel.isPending}>取消任务</button> : null}
          {canRetry ? <button type="button" onClick={() => retry.mutate()} disabled={retry.isPending}>重新评估</button> : null}
        </div>
      </header>
      {degraded ? <p className="page-warning" role="alert">实时连接不稳定，已启用每 5 秒 REST 轮询，记录不会丢失。</p> : null}
      <section className="metadata-strip" aria-label="不可变运行配置">
        <dl>
          <div><dt>Gateway 模型</dt><MetadataValue value={run.data.gateway_model} /></div>
          <div><dt>思考深度</dt><MetadataValue value={run.data.gateway_reasoning_effort} /></div>
          <div><dt>配置哈希</dt><MetadataValue value={run.data.config_snapshot_sha256} /></div>
          <div><dt>TradingAgents</dt><MetadataValue value={run.data.tradingagents_commit} /></div>
          <div><dt>平台版本</dt><MetadataValue value={run.data.root_commit} /></div>
          <div><dt>Prompt Schema</dt><MetadataValue value={run.data.prompt_schema_version} /></div>
        </dl>
        <details><summary>查看固定配置</summary><pre>{JSON.stringify({ request: run.data.request_config, resolved: run.data.resolved_config, data_vendors: run.data.data_vendors, tool_vendors: run.data.tool_vendors, gateway_snapshot_id: run.data.gateway_snapshot_id }, null, 2)}</pre></details>
      </section>
      <div className="detail-grid">
        <DecisionPanel decision={decision.data ?? null} completeReport={completeReport} />
        <RunTimeline
          steps={steps.data ?? []}
          events={liveEvents}
          evidence={evidence.data ?? []}
          artifacts={artifacts.data ?? []}
          canReadArtifacts={canReadArtifacts}
        />
        <section className="detail-panel"><div className="section-heading"><p className="eyebrow">后续验证</p><h2>表现验证</h2></div><p className="section-empty">验证引擎将在下一阶段写入结果；当前评估记录保持不可变。</p></section>
        <ReviewPanel runId={runId} reviews={collaboration.data?.reviews ?? []} comments={collaboration.data?.comments ?? []} canReview={Boolean(user.data?.scopes.includes("assessments:review"))} />
      </div>
    </section>
  );
}

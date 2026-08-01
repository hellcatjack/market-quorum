import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useParams } from "wouter";

import { subscribeToRun, type RunEvent } from "../../api/events";
import {
  cancelRun,
  cleanReassessRun,
  deleteRun,
  getArtifacts,
  getComments,
  getCurrentUser,
  getDecision,
  getEvidence,
  getIntegrity,
  getLlmInteractions,
  getReviews,
  getRun,
  getSteps,
  getValidations,
  retryRun,
} from "../../api/records";
import { ApiClientError } from "../../api/client";
import { useI18n } from "../../i18n/I18nProvider";
import { reasoningEffortLabel, runStatusLabel } from "../../i18n/domainLabels";
import { DecisionPanel } from "./DecisionPanel";
import { IntegrityPanel } from "./IntegrityPanel";
import { ReviewPanel } from "./ReviewPanel";
import { RunTimeline } from "./RunTimeline";
import { ValidationReplayPanel } from "./ValidationReplayPanel";

const CANCELLABLE = new Set([
  "waiting_for_data", "queued", "admitted", "starting", "running_analysts", "research_debate", "trader_plan",
  "risk_debate", "portfolio_decision", "finalizing",
]);
const RETRYABLE = new Set(["failed", "cancelled", "needs_attention"]);
const TERMINAL = new Set(["succeeded", "failed", "cancelled", "needs_attention"]);

function DeleteAssessmentDialog({
  ticker,
  analysisDate,
  pending,
  errorMessage,
  onCancel,
  onConfirm,
}: {
  ticker: string;
  analysisDate: string;
  pending: boolean;
  errorMessage: string | null;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const { t } = useI18n();

  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !pending) onCancel();
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [onCancel, pending]);

  return (
    <div
      className="delete-dialog-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !pending) onCancel();
      }}
    >
      <section
        className="delete-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="delete-assessment-title"
        aria-describedby="delete-assessment-description"
      >
        <span className="delete-dialog__icon" aria-hidden="true">!</span>
        <div>
          <p className="eyebrow">{t("删除评估")}</p>
          <h2 id="delete-assessment-title">
            {t("永久删除 {ticker} 的这次评估？", { ticker })}
          </h2>
          <p id="delete-assessment-description">
            {t("将永久删除 {date} 的评估结论、验证、证据、时间线与关联产物。", {
              date: analysisDate,
            })}
          </p>
          <p className="delete-dialog__notice">
            {t("此操作无法撤销，且不会影响该标的的其他评估。")}
          </p>
          {errorMessage ? <p className="delete-dialog__error" role="alert">{errorMessage}</p> : null}
          <div className="delete-dialog__actions">
            <button type="button" onClick={onCancel} disabled={pending} autoFocus>
              {t("暂不删除")}
            </button>
            <button
              type="button"
              className="delete-dialog__confirm"
              onClick={onConfirm}
              disabled={pending}
            >
              {pending ? t("正在删除…") : t("确认永久删除")}
            </button>
          </div>
        </div>
      </section>
    </div>
  );
}

async function optionalRecord<T>(loader: () => Promise<T>): Promise<T | null> {
  try {
    return await loader();
  } catch (error) {
    if (error instanceof ApiClientError && error.status === 404) return null;
    throw error;
  }
}

function MetadataValue({ value }: { value: string | null | undefined }) {
  const { t } = useI18n();
  return <dd title={value ?? undefined}>{value ?? t("等待准入后固定")}</dd>;
}

function ModelRouteValue({
  model,
  effort,
}: {
  model: string | null | undefined;
  effort: string | null | undefined;
}) {
  const { locale } = useI18n();
  return (
    <MetadataValue
      value={model ? `${model} · ${reasoningEffortLabel(effort ?? null, locale)}` : model}
    />
  );
}

function signedPercent(value: string, locale: "zh-CN" | "en-US"): string {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return value;
  return new Intl.NumberFormat(locale, {
    style: "percent",
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
    signDisplay: "always",
  }).format(parsed);
}

export function RunDetailPage() {
  const { locale, t } = useI18n();
  const { runId = "" } = useParams<{ runId: string }>();
  const [, navigate] = useLocation();
  const queryClient = useQueryClient();
  const [degraded, setDegraded] = useState(false);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [liveEvents, setLiveEvents] = useState<RunEvent[]>([]);
  const lastSequence = useRef(0);
  const deleteRequestStarted = useRef(false);
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
  const integrity = useQuery({
    queryKey: ["run-integrity", runId],
    queryFn: () => getIntegrity(runId),
    enabled: Boolean(runId),
    retry: false,
  });
  const evidence = useQuery({
    queryKey: ["run-evidence", runId], queryFn: () => getEvidence(runId), enabled: Boolean(runId), retry: false,
  });
  const llmInteractions = useQuery({
    queryKey: ["run-llm-interactions", runId],
    queryFn: () => getLlmInteractions(runId),
    enabled: Boolean(runId),
    refetchInterval: (query) => query.state.data?.complete === false ? 5_000 : false,
    retry: false,
  });
  const artifacts = useQuery({
    queryKey: ["run-artifacts", runId], queryFn: () => getArtifacts(runId), enabled: Boolean(runId) && Boolean(user.data?.scopes.includes("artifacts:read")), retry: false,
  });
  const validations = useQuery({
    queryKey: ["run-validations", runId],
    queryFn: () => getValidations(runId),
    enabled: Boolean(runId) && Boolean(user.data?.scopes.includes("validations:read")),
    retry: false,
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
          void queryClient.invalidateQueries({ queryKey: ["run-llm-interactions", runId] });
          void queryClient.invalidateQueries({ queryKey: ["run-integrity", runId] });
        }
        if (event.event_type.startsWith("validation.")) {
          void queryClient.invalidateQueries({ queryKey: ["run-validations", runId] });
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
  const cleanReassessment = useMutation({
    mutationFn: () => cleanReassessRun(runId),
    onSuccess: (next) => navigate(`/runs/${next.id}`),
  });
  const deleteAssessment = useMutation({
    mutationFn: () => deleteRun(runId),
    onSuccess: () => {
      setDeleteDialogOpen(false);
      navigate("/");
      queryClient.removeQueries({
        predicate: (query) => query.queryKey.includes(runId),
      });
      queryClient.removeQueries({ queryKey: ["assessments"] });
      queryClient.removeQueries({ queryKey: ["instrument-overviews"] });
      queryClient.removeQueries({ queryKey: ["instrument", run.data?.ticker] });
      queryClient.removeQueries({ queryKey: ["instrument-history", run.data?.ticker] });
    },
    onError: () => {
      deleteRequestStarted.current = false;
    },
  });

  if (run.isLoading) return <p className="page-shell page-loading" role="status">{t("正在载入评估记录…")}</p>;
  if (run.isError || !run.data) return <p className="page-shell page-warning" role="alert">{t("无法读取该评估记录。")}</p>;
  const canCancel = Boolean(user.data?.scopes.includes("assessments:cancel")) && CANCELLABLE.has(run.data.status);
  const canRetry = Boolean(user.data?.scopes.includes("assessments:submit")) && RETRYABLE.has(run.data.status);
  const canReadArtifacts = Boolean(user.data?.scopes.includes("artifacts:read"));
  const canDelete = Boolean(
    user.data?.roles.includes("Admin")
      && user.data.scopes.includes("assessments:admin")
      && TERMINAL.has(run.data.status),
  );
  const canCleanReassess = Boolean(
    user.data?.roles.includes("Admin")
      && user.data.scopes.includes("assessments:admin")
      && user.data.scopes.includes("assessments:submit")
      && run.data.status === "succeeded"
      && integrity.data
      && ["at_risk", "unknown"].includes(integrity.data.status),
  );
  const completeReport = canReadArtifacts
    ? artifacts.data?.find((artifact) => artifact.kind === "report_18_complete_report") ?? null
    : null;
  const memory = run.data.memory ?? {
    mode: "independent" as const,
    snapshot_sha256: null,
    sources: [],
  };
  const memoryLabel = memory.mode === "historical" ? t("历史辅助") : t("独立评估");
  const deleteErrorMessage = deleteAssessment.error instanceof ApiClientError
    ? deleteAssessment.error.details?.reason === "active_work"
      ? t("该评估正在被执行或验证，暂时不能删除。")
      : deleteAssessment.error.details?.reason === "dependent_runs_exist"
        ? t("该评估仍被后续重试或干净重评估引用，请先删除后续评估。")
        : deleteAssessment.error.code === "delete_not_allowed"
          ? t("当前状态不允许删除，请刷新后重试。")
          : t("删除失败，请稍后重试。")
    : deleteAssessment.isError
      ? t("删除失败，请稍后重试。")
      : null;
  const dataRequirement = run.data.data_requirement;
  const dataProgress = (dataRequirement?.progress ?? {}) as Record<string, unknown>;
  const dataCompleted = Number(dataProgress.completed_items ?? dataProgress.completed_units ?? 0);
  const dataTotal = Number(dataProgress.total_items ?? dataProgress.total_units ?? 0);
  const dataStage = String(dataProgress.stage ?? dataRequirement?.status ?? "-");
  const dataWatermark = String(dataProgress.last_watermark ?? "-");
  const dataError = String(dataProgress.error_code ?? dataProgress.stable_reason ?? "-");

  return (
    <section className="page-shell run-detail-page">
      <header className="run-hero">
        <div>
          <p className="eyebrow">{t("评估记录 / {date}", { date: run.data.analysis_date })}</p>
          <h1>{run.data.instrument_name ?? run.data.ticker}</h1>
          {run.data.instrument_name || run.data.exchange ? (
            <p className="run-hero__identity">
              {[
                run.data.instrument_name ? run.data.ticker : null,
                run.data.exchange,
              ].filter(Boolean).join(" · ")}
            </p>
          ) : null}
          <p><span className={`run-status run-status--${run.data.status === "succeeded" ? "success" : "active"}`}>● {runStatusLabel(run.data.status, locale)}</span> · {t("第 {attempt} 次尝试", { attempt: run.data.attempt })}</p>
        </div>
        <div className="run-actions">
          <Link href={`/instruments/${run.data.ticker}`}>{t("查看标的历史")}</Link>
          {canCancel ? <button type="button" onClick={() => cancel.mutate()} disabled={cancel.isPending}>{t("取消任务")}</button> : null}
          {canRetry ? <button type="button" onClick={() => retry.mutate()} disabled={retry.isPending}>{t("重新评估")}</button> : null}
          {canDelete ? (
            <button
              type="button"
              className="run-action--danger"
              onClick={() => {
                deleteRequestStarted.current = false;
                deleteAssessment.reset();
                setDeleteDialogOpen(true);
              }}
            >
              {t("删除评估")}
            </button>
          ) : null}
        </div>
      </header>
      {degraded ? <p className="page-warning" role="alert">{t("实时连接不稳定，已启用每 5 秒 REST 轮询，记录不会丢失。")}</p> : null}
      <section className="metadata-strip" aria-label={t("不可变运行配置")}>
        <dl>
          <div><dt>{t("快速分析路由")}</dt><ModelRouteValue model={run.data.gateway_fast_model} effort={run.data.gateway_fast_reasoning_effort} /></div>
          <div><dt>{t("关键裁决路由")}</dt><ModelRouteValue model={run.data.gateway_slow_model} effort={run.data.gateway_slow_reasoning_effort} /></div>
          <div><dt>{t("评估模式")}</dt><MetadataValue value={memoryLabel} /></div>
          <div><dt>{t("历史经验")}</dt><MetadataValue value={t("{count} 条", { count: memory.sources.length })} /></div>
          <div><dt>{t("配置哈希")}</dt><MetadataValue value={run.data.config_snapshot_sha256} /></div>
          <div><dt>TradingAgents</dt><MetadataValue value={run.data.tradingagents_commit} /></div>
          <div><dt>{t("平台版本")}</dt><MetadataValue value={run.data.root_commit} /></div>
          <div><dt>Prompt Schema</dt><MetadataValue value={run.data.prompt_schema_version} /></div>
        </dl>
        {memory.mode === "historical" ? (
          <details className="memory-sources">
            <summary>{t("历史经验 {count} 条", { count: memory.sources.length })}</summary>
            {memory.sources.length ? (
              <ol>
                {memory.sources.map((source) => (
                  <li key={source.validation_id}>
                    <div>
                      <strong>{t("{date} · {horizon} 个交易日验证", { date: source.analysis_date, horizon: source.horizon })}</strong>
                      <span>{source.rating} · {t("收益 {value}", { value: signedPercent(source.raw_return, locale) })} · Alpha {signedPercent(source.alpha, locale)}</span>
                    </div>
                    <Link href={`/runs/${source.source_run_id}`}>{t("查看来源评估")}</Link>
                    <small>
                      {t("验证截止 {date} · 条目哈希 {hash}", { date: source.exit_session, hash: source.content_sha256 })}
                    </small>
                  </li>
                ))}
              </ol>
            ) : <p>{t("本次未找到满足时间约束的已验证旧记录。")}</p>}
          </details>
        ) : null}
        <details><summary>{t("查看固定配置")}</summary><pre>{JSON.stringify({ request: run.data.request_config, resolved: run.data.resolved_config, memory: { mode: memory.mode, source_count: memory.sources.length, snapshot_sha256: memory.snapshot_sha256 }, data_vendors: run.data.data_vendors, tool_vendors: run.data.tool_vendors, gateway_snapshot_id: run.data.gateway_snapshot_id }, null, 2)}</pre></details>
      </section>
      {llmInteractions.isError ? <p className="page-warning" role="alert">{t("模型调用轨迹暂时不可用。")}</p> : null}
      {dataRequirement ? (
        <section className="data-readiness-panel" aria-label={t("数据准备")}>
          <div className="data-readiness-panel__heading">
            <div>
              <p className="eyebrow">StockLean</p>
              <h2>{t("数据准备")}</h2>
            </div>
            <span className="run-status run-status--muted">
              {runStatusLabel(run.data.status, locale)}
            </span>
          </div>
          <dl>
            <div><dt>{t("准备阶段")}</dt><dd>{dataStage}</dd></div>
            <div><dt>{t("完成量")}</dt><dd>{dataCompleted} / {dataTotal}</dd></div>
            <div><dt>{t("最新水位")}</dt><dd>{dataWatermark}</dd></div>
            <div><dt>{t("下次轮询")}</dt><dd>{dataRequirement.next_poll_at ?? "-"}</dd></div>
            <div><dt>{t("数据快照")}</dt><dd title={dataRequirement.manifest_snapshot_id ?? undefined}>{dataRequirement.manifest_snapshot_id ?? "-"}</dd></div>
            <div><dt>{t("稳定错误")}</dt><dd>{dataError}</dd></div>
          </dl>
          <div className="data-readiness-products" aria-label={t("产品")}>
            {dataRequirement.required_products.map((product) => <span key={product}>{product}</span>)}
          </div>
        </section>
      ) : null}
      <div className="detail-grid">
        <IntegrityPanel
          integrity={integrity.data ?? null}
          canCleanReassess={canCleanReassess}
          cleanPending={cleanReassessment.isPending}
          onCleanReassess={() => cleanReassessment.mutate()}
        />
        {cleanReassessment.isError ? (
          <p className="page-warning detail-panel--wide" role="alert">
            {t("无法创建干净重评估，请稍后重试。")}
          </p>
        ) : null}
        <DecisionPanel decision={decision.data ?? null} completeReport={completeReport} />
        <RunTimeline
          steps={steps.data ?? []}
          events={liveEvents}
          evidence={evidence.data ?? []}
          llmInteractions={llmInteractions.data?.items ?? []}
          artifacts={artifacts.data ?? []}
          canReadArtifacts={canReadArtifacts}
        />
        <ValidationReplayPanel
          ticker={run.data.ticker}
          exchange={run.data.exchange ?? null}
          analysisDate={run.data.analysis_date}
          priceTarget={decision.data?.price_target ?? null}
          validations={validations.data ?? []}
          artifacts={artifacts.data ?? []}
          canReadArtifacts={canReadArtifacts}
        />
        <ReviewPanel runId={runId} reviews={collaboration.data?.reviews ?? []} comments={collaboration.data?.comments ?? []} canReview={Boolean(user.data?.scopes.includes("assessments:review"))} />
      </div>
      {deleteDialogOpen ? (
        <DeleteAssessmentDialog
          ticker={run.data.ticker}
          analysisDate={run.data.analysis_date}
          pending={deleteAssessment.isPending}
          errorMessage={deleteErrorMessage}
          onCancel={() => {
            if (!deleteAssessment.isPending) {
              deleteRequestStarted.current = false;
              setDeleteDialogOpen(false);
            }
          }}
          onConfirm={() => {
            if (deleteRequestStarted.current) return;
            deleteRequestStarted.current = true;
            deleteAssessment.mutate();
          }}
        />
      ) : null}
    </section>
  );
}

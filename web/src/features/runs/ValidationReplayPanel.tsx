import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { getArtifactContent, type Artifact, type Validation } from "../../api/records";
import { ValidationChart } from "./ValidationChart";
import {
  buildReplayData,
  horizonLabel,
  parseValidationPriceArtifact,
  selectDefaultHorizon,
  tradingViewUrl,
  type ReplayData,
} from "./validationReplay";

interface ValidationReplayPanelProps {
  ticker: string;
  exchange: string | null;
  analysisDate: string;
  priceTarget: string | null;
  validations: Validation[];
  artifacts: Artifact[];
  canReadArtifacts: boolean;
  loadArtifact?: (artifactId: string) => Promise<string>;
}

function signedPercent(value: string | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return "—";
  return new Intl.NumberFormat("zh-CN", {
    style: "percent",
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
    signDisplay: "always",
  }).format(parsed);
}

function numberOrNull(value: string | null | undefined): number | null {
  if (value === null || value === undefined) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function directionLabel(validation: Validation): string {
  const correct = validation.trigger_results?.direction_correct;
  if (correct === true) return "方向正确";
  if (correct === false) return "方向未实现";
  return "方向待判定";
}

function statusMessage(validation: Validation): string | null {
  if (validation.status === "scheduled") {
    const dueAt = validation.matures_at ?? validation.scheduled_for;
    return `尚未到达验证时间，计划时间 ${new Date(dueAt).toLocaleString("zh-CN")}。`;
  }
  if (validation.status === "running") return "正在读取并核验市场价格。";
  if (validation.status === "retry_wait") return "行情暂不可用，验证引擎将在稍后自动重试。";
  if (validation.status === "failed" || validation.status === "unavailable") {
    return `验证无法完成${validation.error_code ? `：${validation.error_code}` : "。"}`;
  }
  return null;
}

function replayFrom(
  content: string | undefined,
  validation: Validation | undefined,
): { data: ReplayData | null; error: string | null } {
  if (!content || !validation) return { data: null, error: null };
  const trigger = validation.trigger_results;
  if (!trigger?.entry_session || !trigger.exit_session) {
    return { data: null, error: "验证结果缺少入场或退出交易日。" };
  }
  try {
    return {
      data: buildReplayData(parseValidationPriceArtifact(content), {
        entry_session: trigger.entry_session,
        exit_session: trigger.exit_session,
        horizon: validation.horizon,
      }),
      error: null,
    };
  } catch (error) {
    return {
      data: null,
      error: error instanceof Error ? error.message : "验证价格产物无法解析。",
    };
  }
}

export function ValidationReplayPanel({
  ticker,
  exchange,
  analysisDate,
  priceTarget,
  validations,
  artifacts,
  canReadArtifacts,
  loadArtifact = getArtifactContent,
}: ValidationReplayPanelProps) {
  const defaultHorizon = useMemo(() => selectDefaultHorizon(validations), [validations]);
  const [requestedHorizon, setRequestedHorizon] = useState<number | null>(null);
  const [copied, setCopied] = useState(false);
  const selectedHorizon = validations.some((item) => item.horizon === requestedHorizon)
    ? requestedHorizon
    : defaultHorizon;

  const selected = validations.find((item) => item.horizon === selectedHorizon);
  const artifactId = selected?.status === "completed" ? selected.data_artifact_id : null;
  const artifact = artifacts.find((item) => item.id === artifactId);
  const artifactContent = useQuery({
    queryKey: ["validation-artifact", artifactId],
    queryFn: () => loadArtifact(artifactId ?? ""),
    enabled: Boolean(artifactId) && canReadArtifacts,
    retry: false,
  });
  const replay = useMemo(
    () => replayFrom(artifactContent.data, selected),
    [artifactContent.data, selected],
  );

  const milestoneSessions = validations
    .filter((item) => item.status === "completed" && item.horizon <= (selectedHorizon ?? 0))
    .flatMap((item) => item.trigger_results?.exit_session
      ? [{ horizon: item.horizon, session: item.trigger_results.exit_session }]
      : []);
  const externalUrl = tradingViewUrl(ticker, exchange);
  const trigger = selected?.trigger_results;
  const stateMessage = selected ? statusMessage(selected) : null;
  const isV2 = selected?.calculation_version === "validation.v2";

  const copyRange = async () => {
    if (!trigger?.entry_session || !trigger.exit_session) return;
    const benchmark = replay.data?.benchmarkTicker ?? "SPY";
    await navigator.clipboard.writeText(
      `${ticker} | ${trigger.entry_session} 至 ${trigger.exit_session} | 日线 | 基准 ${benchmark}`,
    );
    setCopied(true);
  };

  return (
    <section className="detail-panel detail-panel--wide validation-replay" aria-labelledby="validation-replay-title">
      <div className="section-heading validation-replay__heading">
        <div>
          <p className="eyebrow">后续验证 / 不可变结果回放</p>
          <h2 id="validation-replay-title">表现验证</h2>
        </div>
        {validations.length ? (
          <div className="validation-replay__horizons" aria-label="验证期限">
            {validations.map((item) => (
              <button
                key={item.id}
                type="button"
                aria-pressed={item.horizon === selectedHorizon}
                onClick={() => {
                  setRequestedHorizon(item.horizon);
                  setCopied(false);
                }}
              >
                {horizonLabel(item.horizon)}
              </button>
            ))}
          </div>
        ) : null}
      </div>

      {!selected ? (
        <p className="section-empty">验证任务尚未安排；评估记录本身保持不可变。</p>
      ) : (
        <>
          <div className="validation-replay__summary">
            <div className="validation-replay__verdict">
              <span>原始判断</span>
              <strong>{trigger?.rating ?? "待生成"}</strong>
              <small>{directionLabel(selected)}</small>
            </div>
            <dl>
              {isV2 ? (
                <>
                  <div><dt>总回报（含现金分配）</dt><dd>{signedPercent(selected.total_return)}</dd></div>
                  <div><dt>价格回报</dt><dd>{signedPercent(selected.price_return)}</dd></div>
                  <div><dt>基准总回报</dt><dd>{signedPercent(selected.benchmark_total_return)}</dd></div>
                  <div><dt>基准价格回报</dt><dd>{signedPercent(selected.benchmark_price_return)}</dd></div>
                  <div><dt>总回报 Alpha</dt><dd>{signedPercent(selected.total_alpha)}</dd></div>
                  <div><dt>价格 Alpha</dt><dd>{signedPercent(selected.price_alpha)}</dd></div>
                </>
              ) : (
                <>
                  <div><dt>标的收益</dt><dd>{signedPercent(selected.raw_return)}</dd></div>
                  <div><dt>基准收益</dt><dd>{signedPercent(selected.benchmark_return)}</dd></div>
                  <div><dt>超额收益 Alpha</dt><dd>{signedPercent(selected.alpha)}</dd></div>
                </>
              )}
              <div><dt>最大不利波动</dt><dd>{signedPercent(selected.max_adverse_excursion)}</dd></div>
              <div><dt>最大有利波动</dt><dd>{signedPercent(selected.max_favorable_excursion)}</dd></div>
            </dl>
          </div>

          {stateMessage ? <p className="validation-replay__state" role="status">{stateMessage}</p> : null}
          {selected.status === "completed" && !artifactId ? (
            <p className="validation-replay__state validation-replay__state--warning" role="alert">
              验证结果缺少绑定价格产物，已保存指标仍可审计。
            </p>
          ) : null}
          {selected.status === "completed" && artifactId && !canReadArtifacts ? (
            <p className="validation-replay__state" role="status">
              缺少产物读取权限，已保存指标仍然可见，但无法绘制封存行情。
            </p>
          ) : null}
          {artifactContent.isLoading ? <p className="validation-replay__state">正在校验并读取封存行情…</p> : null}
          {artifactContent.isError ? (
            <p className="validation-replay__state validation-replay__state--warning" role="alert">
              无法读取或校验该验证的价格产物。
            </p>
          ) : null}
          {replay.error ? (
            <p className="validation-replay__state validation-replay__state--warning" role="alert">
              {replay.error}
            </p>
          ) : null}

          {replay.data ? (
            <>
              <p className="validation-replay__window-note">
                {selected.horizon === 20
                  ? `“20 个交易日后”从 ${replay.data.entrySession} 收盘计至 ${replay.data.exitSession} 收盘，共有 ${replay.data.verificationNodeCount} 个价格节点。`
                  : `${selected.horizon} 个交易日验证从 ${replay.data.entrySession} 收盘计至 ${replay.data.exitSession} 收盘。`}
              </p>
              <ValidationChart
                replay={replay.data}
                instrumentTicker={replay.data.instrumentTicker}
                benchmarkTicker={replay.data.benchmarkTicker}
                milestones={milestoneSessions}
                priceTarget={numberOrNull(priceTarget)}
                entryPrice={numberOrNull(trigger?.entry_price)}
                maxAdverseExcursion={numberOrNull(selected.max_adverse_excursion)}
                maxFavorableExcursion={numberOrNull(selected.max_favorable_excursion)}
              />
            </>
          ) : null}

          <details className="validation-replay__audit">
            <summary>计算与数据依据</summary>
            <dl>
              <div><dt>评估日期</dt><dd>{analysisDate}</dd></div>
              <div><dt>交易日历</dt><dd>{selected.calendar_code ?? "—"}</dd></div>
              <div><dt>验证起点</dt><dd>{trigger?.entry_session ?? "—"}</dd></div>
              <div><dt>验证终点</dt><dd>{trigger?.exit_session ?? "—"}</dd></div>
              <div><dt>{isV2 ? "入场标准化价" : "入场复权价"}</dt><dd>{trigger?.entry_price ?? "—"}</dd></div>
              <div><dt>{isV2 ? "退出标准化价" : "退出复权价"}</dt><dd>{trigger?.exit_price ?? "—"}</dd></div>
              <div><dt>目标价命中</dt><dd>{trigger?.price_target_hit === null || trigger?.price_target_hit === undefined ? "未设置" : trigger.price_target_hit ? "是" : "否"}</dd></div>
              {isV2 ? <div><dt>目标价判定状态</dt><dd>{trigger?.price_target_status ?? "—"}</dd></div> : null}
              {isV2 ? <div><dt>同股本口径目标价</dt><dd>{trigger?.rebased_price_target ?? "—"}</dd></div> : null}
              <div><dt>计算规则</dt><dd>{selected.calculation_version ?? "validation.v1"}</dd></div>
              <div><dt>行情来源</dt><dd>{selected.provider_id ?? replay.data?.source ?? "—"}</dd></div>
              {isV2 ? <div><dt>供应商适配器</dt><dd>{selected.provider_adapter_version ?? "—"}</dd></div> : null}
              {isV2 ? <div><dt>价格标准化规则</dt><dd>{selected.normalization_version ?? "—"}</dd></div> : null}
              {isV2 ? <div><dt>数据质量核对</dt><dd>{trigger?.data_quality_status ?? "—"}</dd></div> : null}
              <div><dt>采集时间</dt><dd>{replay.data?.collectedAt ?? "—"}</dd></div>
              <div><dt>价格产物 ID</dt><dd><code>{artifactId ?? "—"}</code></dd></div>
              <div><dt>SHA-256</dt><dd><code>{artifact?.sha256 ?? "—"}</code></dd></div>
            </dl>
            <p>
              {isV2
                ? "总回报包含现金分配，价格回报仅反映价格变化；两类 Alpha 均使用同口径的标的回报减去基准回报。MAE/MFE 使用拆股标准化后的价格路径，目标价按分析日股本口径固化并在需要时重基准。"
                : "收益率使用入场与退出复权收盘价；Alpha 为标的收益减基准收益；MAE/MFE 使用验证窗口内按当日复权因子换算的最低价与最高价。"}
            </p>
          </details>

          <div className="validation-replay__research">
            <a href={externalUrl} target="_blank" rel="noreferrer">在 TradingView 深入研究</a>
            <button
              type="button"
              onClick={() => void copyRange()}
              disabled={!trigger?.entry_session || !trigger.exit_session}
            >
              {copied ? "已复制" : "复制验证区间"}
            </button>
            <p>外部行情仅供延伸研究，可能因复权方式或数据修订而与本次封存验证略有差异。</p>
          </div>
        </>
      )}
    </section>
  );
}

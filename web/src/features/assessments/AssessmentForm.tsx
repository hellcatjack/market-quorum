import { useMutation } from "@tanstack/react-query";
import { useMemo, useRef, useState } from "react";
import { useLocation } from "wouter";

import type { Capacity, SubmitAssessmentBatch } from "../../api/assessments";
import { submitAssessmentBatch } from "../../api/assessments";
import { ApiClientError } from "../../api/client";
import { CapacityBanner } from "../system/CapacityBanner";
import { parseTickers } from "./tickers";

const ANALYSTS = [
  ["market", "市场"],
  ["social", "社交情绪"],
  ["news", "新闻"],
  ["fundamentals", "基本面"],
] as const;

function localToday(): string {
  const now = new Date();
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 10);
}

export function AssessmentForm({ capacity }: { capacity: Capacity | null }) {
  const [, navigate] = useLocation();
  const [tickerText, setTickerText] = useState("");
  const [analysisDate, setAnalysisDate] = useState(localToday());
  const [depth, setDepth] = useState<SubmitAssessmentBatch["depth"]>("deep");
  const [memoryMode, setMemoryMode] =
    useState<SubmitAssessmentBatch["memory_mode"]>("historical");
  const [language, setLanguage] = useState("Chinese");
  const [analysts, setAnalysts] = useState<string[]>(ANALYSTS.map(([value]) => value));
  const [validationError, setValidationError] = useState<string | null>(null);
  const idempotencyKey = useRef(globalThis.crypto.randomUUID());
  const tickers = useMemo(() => parseTickers(tickerText), [tickerText]);

  const submission = useMutation({
    mutationFn: submitAssessmentBatch,
    onSuccess: (page) => {
      idempotencyKey.current = globalThis.crypto.randomUUID();
      const first = page.items[0];
      if (first) navigate(`/runs/${first.id}`);
    },
  });

  function toggleAnalyst(value: string) {
    idempotencyKey.current = globalThis.crypto.randomUUID();
    setAnalysts((selected) =>
      selected.includes(value) ? selected.filter((item) => item !== value) : [...selected, value],
    );
  }

  function selectMemoryMode(value: SubmitAssessmentBatch["memory_mode"]) {
    setMemoryMode(value);
    idempotencyKey.current = globalThis.crypto.randomUUID();
  }

  function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    submission.reset();
    if (tickers.length === 0) {
      setValidationError("请至少输入一个标的代码");
      return;
    }
    if (tickers.length > 100) {
      setValidationError("一次最多派发 100 个标的");
      return;
    }
    if (!analysisDate) {
      setValidationError("请选择分析日期");
      return;
    }
    if (analysisDate > localToday()) {
      setValidationError("分析日期不能晚于今天");
      return;
    }
    if (analysts.length === 0) {
      setValidationError("请至少选择一位分析师");
      return;
    }
    setValidationError(null);
    submission.mutate({
      items: tickers.map((ticker) => ({ ticker, analysis_date: analysisDate })),
      analysts,
      depth,
      memory_mode: memoryMode,
      language,
      idempotency_key: idempotencyKey.current,
    });
  }

  const serverError = submission.error instanceof ApiClientError ? submission.error : null;
  return (
    <div className="dispatch-layout">
      <form className="assessment-form panel" onSubmit={submit} noValidate>
        <div className="form-heading">
          <div>
            <p className="eyebrow">任务配置</p>
            <h2>派发评估</h2>
          </div>
          <span className="ticker-count">{tickers.length} 个标的</span>
        </div>
        <label className="field field--wide">
          <span>标的代码</span>
          <textarea
            name="tickers"
            aria-label="标的代码"
            rows={5}
            value={tickerText}
            onChange={(event) => {
              setTickerText(event.target.value);
              idempotencyKey.current = globalThis.crypto.randomUUID();
            }}
            placeholder="例如：SPCX, NVDA, GLD"
            aria-describedby="ticker-help"
          />
          <small id="ticker-help">支持逗号、空格或换行分隔；自动大写并去重。</small>
        </label>
        <div className="form-grid">
          <label className="field">
            <span>分析日期</span>
            <input
              type="date"
              value={analysisDate}
              max={localToday()}
              onChange={(event) => {
                setAnalysisDate(event.target.value);
                idempotencyKey.current = globalThis.crypto.randomUUID();
              }}
            />
          </label>
          <label className="field">
            <span>分析深度</span>
            <select value={depth} onChange={(event) => {
              setDepth(event.target.value as typeof depth);
              idempotencyKey.current = globalThis.crypto.randomUUID();
            }}>
              <option value="shallow">Shallow</option>
              <option value="medium">Medium</option>
              <option value="deep">Deep</option>
            </select>
          </label>
          <label className="field">
            <span>输出语言</span>
            <select value={language} onChange={(event) => {
              setLanguage(event.target.value);
              idempotencyKey.current = globalThis.crypto.randomUUID();
            }}>
              <option value="Chinese">中文</option>
              <option value="English">English</option>
            </select>
          </label>
          <fieldset className="memory-mode-fieldset">
            <legend>评估记忆</legend>
            <div className="memory-mode-options">
              <label className="memory-mode-option">
                <input
                  type="radio"
                  name="memory-mode"
                  value="historical"
                  checked={memoryMode === "historical"}
                  onChange={() => selectMemoryMode("historical")}
                />
                <span className="memory-mode-copy">
                  <span className="memory-mode-title">
                    <strong>历史辅助</strong>
                    <em>推荐</em>
                  </span>
                  <span>参考同标的、分析日前已经完成表现验证的旧评估。</span>
                  <small>最多 5 条；没有合格记录时，以零记忆继续运行。</small>
                  <small>当前证据优先，历史结论不是投票。</small>
                </span>
              </label>
              <label className="memory-mode-option">
                <input
                  type="radio"
                  name="memory-mode"
                  value="independent"
                  checked={memoryMode === "independent"}
                  onChange={() => selectMemoryMode("independent")}
                />
                <span className="memory-mode-copy">
                  <span className="memory-mode-title"><strong>独立评估</strong></span>
                  <span>不读取任何旧评估结论。</span>
                  <small>适合基准对照、争议复核或需要隔离历史观点的任务。</small>
                </span>
              </label>
            </div>
            <p className="memory-mode-note">
              历史辅助不会训练或修改模型，只向最终投资判断提供可审计的历史校准信息。
            </p>
          </fieldset>
        </div>
        <fieldset className="analyst-fieldset">
          <legend>参与分析师</legend>
          <div className="analyst-options">
            {ANALYSTS.map(([value, label]) => (
              <label key={value}>
                <input
                  type="checkbox"
                  checked={analysts.includes(value)}
                  onChange={() => toggleAnalyst(value)}
                />
                <span>{label}</span>
              </label>
            ))}
          </div>
        </fieldset>
        <details className="advanced-summary">
          <summary>数据源策略</summary>
          <p>由后端策略统一选择 yfinance、Finnhub、FRED 等已配置数据源，前端不可覆盖密钥。</p>
        </details>
        {validationError ? <p className="form-error" role="alert">{validationError}</p> : null}
        {serverError ? (
          <div className="form-error" role="alert">
            <strong>{serverError.message}</strong>
            <span>请求编号：{serverError.requestId}</span>
          </div>
        ) : null}
        <button className="primary-button" type="submit" disabled={submission.isPending}>
          {submission.isPending ? "正在派发…" : "派发评估"}
        </button>
      </form>
      <aside className="dispatch-aside">
        {capacity ? <CapacityBanner capacity={capacity} /> : <div className="panel">容量数据载入中…</div>}
        <div className="panel dispatch-note">
          <p className="eyebrow">运行原则</p>
          <strong>深度研究按安全容量串并行调度</strong>
          <p>前端提交只负责入队。调度器根据 Gateway、CPU、内存和数据源熔断状态决定何时准入。</p>
        </div>
      </aside>
    </div>
  );
}

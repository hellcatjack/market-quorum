export interface ValidationPriceSeries {
  ticker: string;
  currency: string | null;
  sessions: string[];
  open: number[];
  high: number[];
  low: number[];
  close: number[];
  adjusted_close: number[];
  source: string;
  collected_at: string;
}

export interface ValidationPriceArtifact {
  instrument: ValidationPriceSeries;
  benchmark: ValidationPriceSeries;
}

export interface ReplayCandle {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
}

export interface ReplayPoint {
  time: string;
  value: number;
}

export interface ReplayData {
  candles: ReplayCandle[];
  instrumentPerformance: ReplayPoint[];
  benchmarkPerformance: ReplayPoint[];
  entrySession: string;
  exitSession: string;
  verificationNodeCount: number;
  source: string;
  collectedAt: string;
  instrumentTicker: string;
  benchmarkTicker: string;
}

interface ReplayWindow {
  entry_session: string;
  exit_session: string;
  horizon: number;
}

interface ValidationChoice {
  horizon: number;
  status: string;
  data_artifact_id?: string | null;
}

const PRICE_ARRAYS = ["open", "high", "low", "close", "adjusted_close"] as const;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function requiredText(value: unknown, field: string): string {
  if (typeof value !== "string" || !value.trim()) {
    throw new Error(`${field} 缺失`);
  }
  return value;
}

function optionalText(value: unknown, field: string): string | null {
  if (value === null) return null;
  return requiredText(value, field);
}

function priceArray(value: unknown, field: string): number[] {
  if (!Array.isArray(value)) throw new Error(`${field} 必须是正数数组`);
  return value.map((item) => {
    const parsed = typeof item === "number"
      ? item
      : typeof item === "string" && item.trim()
        ? Number(item)
        : Number.NaN;
    if (!Number.isFinite(parsed) || parsed <= 0) {
      throw new Error(`${field} 必须是正数数组`);
    }
    return parsed;
  });
}

function parsePriceSeries(value: unknown, label: string): ValidationPriceSeries {
  if (!isRecord(value)) throw new Error(`${label} 价格序列缺失`);
  const sessions = value.sessions;
  if (
    !Array.isArray(sessions)
    || sessions.length === 0
    || sessions.some((item) => typeof item !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(item))
  ) {
    throw new Error(`${label}.sessions 必须是 ISO 交易日数组`);
  }
  for (let index = 1; index < sessions.length; index += 1) {
    if (sessions[index] <= sessions[index - 1]) throw new Error("交易日必须严格递增");
  }

  const arrays = Object.fromEntries(
    PRICE_ARRAYS.map((field) => [field, priceArray(value[field], `${label}.${field}`)]),
  ) as Record<(typeof PRICE_ARRAYS)[number], number[]>;
  if (PRICE_ARRAYS.some((field) => arrays[field].length !== sessions.length)) {
    throw new Error("价格数组长度不一致");
  }

  return {
    ticker: requiredText(value.ticker, `${label}.ticker`).toUpperCase(),
    currency: optionalText(value.currency, `${label}.currency`),
    sessions: [...sessions],
    open: arrays.open,
    high: arrays.high,
    low: arrays.low,
    close: arrays.close,
    adjusted_close: arrays.adjusted_close,
    source: requiredText(value.source, `${label}.source`),
    collected_at: requiredText(value.collected_at, `${label}.collected_at`),
  };
}

export function parseValidationPriceArtifact(content: string): ValidationPriceArtifact {
  let parsed: unknown;
  try {
    parsed = JSON.parse(content);
  } catch {
    throw new Error("验证价格产物不是有效 JSON");
  }
  if (!isRecord(parsed)) throw new Error("验证价格产物格式无效");
  return {
    instrument: parsePriceSeries(parsed.instrument, "instrument"),
    benchmark: parsePriceSeries(parsed.benchmark, "benchmark"),
  };
}

function adjustedPrice(series: ValidationPriceSeries, field: "open" | "high" | "low", index: number): number {
  return series[field][index] * (series.adjusted_close[index] / series.close[index]);
}

export function buildReplayData(
  artifact: ValidationPriceArtifact,
  window: ReplayWindow,
): ReplayData {
  const entryIndex = artifact.instrument.sessions.indexOf(window.entry_session);
  const exitIndex = artifact.instrument.sessions.indexOf(window.exit_session);
  if (entryIndex < 0 || exitIndex < entryIndex) {
    throw new Error("验证产物缺少入场或退出交易日");
  }
  const verificationNodeCount = exitIndex - entryIndex + 1;
  if (verificationNodeCount !== window.horizon + 1) {
    throw new Error("验证价格节点与期限不一致");
  }

  const visibleStart = Math.max(0, entryIndex - 5);
  const candles = artifact.instrument.sessions
    .slice(visibleStart, exitIndex + 1)
    .map((time, offset) => {
      const index = visibleStart + offset;
      return {
        time,
        open: adjustedPrice(artifact.instrument, "open", index),
        high: adjustedPrice(artifact.instrument, "high", index),
        low: adjustedPrice(artifact.instrument, "low", index),
        close: artifact.instrument.adjusted_close[index],
      };
    });

  const benchmarkIndexes = new Map(
    artifact.benchmark.sessions.map((session, index) => [session, index]),
  );
  const benchmarkEntryIndex = benchmarkIndexes.get(window.entry_session);
  if (benchmarkEntryIndex === undefined) throw new Error("基准缺少验证起点交易日");
  const instrumentEntry = artifact.instrument.adjusted_close[entryIndex];
  const benchmarkEntry = artifact.benchmark.adjusted_close[benchmarkEntryIndex];
  const performanceSessions = artifact.instrument.sessions.slice(entryIndex, exitIndex + 1);
  const instrumentPerformance = performanceSessions.map((time, offset) => ({
    time,
    value: artifact.instrument.adjusted_close[entryIndex + offset] / instrumentEntry * 100,
  }));
  const benchmarkPerformance = performanceSessions.map((time) => {
    const index = benchmarkIndexes.get(time);
    if (index === undefined) throw new Error(`基准缺少交易日 ${time}`);
    return {
      time,
      value: artifact.benchmark.adjusted_close[index] / benchmarkEntry * 100,
    };
  });

  return {
    candles,
    instrumentPerformance,
    benchmarkPerformance,
    entrySession: window.entry_session,
    exitSession: window.exit_session,
    verificationNodeCount,
    source: artifact.instrument.source,
    collectedAt: artifact.instrument.collected_at,
    instrumentTicker: artifact.instrument.ticker,
    benchmarkTicker: artifact.benchmark.ticker,
  };
}

export function selectDefaultHorizon(validations: ValidationChoice[]): number | null {
  if (!validations.length) return null;
  const completed = validations.filter(
    (item) => item.status === "completed" && Boolean(item.data_artifact_id),
  );
  const candidates = completed.length ? completed : validations;
  return Math.max(...candidates.map((item) => item.horizon));
}

export function horizonLabel(horizon: number): string {
  return `${horizon} 日`;
}

const TRADING_VIEW_EXCHANGES: Record<string, string> = {
  ASE: "AMEX",
  NCM: "NASDAQ",
  NGM: "NASDAQ",
  NMS: "NASDAQ",
  NYQ: "NYSE",
  PCX: "AMEX",
};

export function tradingViewUrl(ticker: string, exchange: string | null | undefined): string {
  const normalizedTicker = ticker.trim().toUpperCase();
  const normalizedExchange = exchange?.trim().toUpperCase();
  const tradingViewExchange = normalizedExchange
    ? TRADING_VIEW_EXCHANGES[normalizedExchange] ?? normalizedExchange
    : null;
  const symbol = tradingViewExchange && !normalizedTicker.includes(":")
    ? `${tradingViewExchange}:${normalizedTicker}`
    : normalizedTicker;
  const url = new URL("https://www.tradingview.com/chart/");
  url.searchParams.set("symbol", symbol);
  url.searchParams.set("interval", "D");
  return url.toString();
}

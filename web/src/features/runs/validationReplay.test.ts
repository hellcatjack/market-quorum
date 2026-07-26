import {
  buildReplayData,
  horizonLabel,
  parseValidationPriceArtifact,
  selectDefaultHorizon,
  tradingViewUrl,
} from "./validationReplay";

function artifactJson(): string {
  const sessions = Array.from({ length: 23 }, (_, index) =>
    `2026-07-${String(index + 1).padStart(2, "0")}`,
  );
  const close = sessions.map((_, index) => 100 + index);
  const benchmarkClose = sessions.map((_, index) => 200 + index * 2);
  return JSON.stringify({
    instrument: {
      ticker: "NVDA",
      currency: "USD",
      sessions,
      open: close.map((value) => value - 1),
      high: close.map((value) => value + 2),
      low: close.map((value) => value - 2),
      close,
      adjusted_close: close.map((value) => value / 2),
      source: "fixture",
      collected_at: "2026-07-26T12:00:00Z",
    },
    benchmark: {
      ticker: "SPY",
      currency: "USD",
      sessions,
      open: benchmarkClose.map((value) => value - 1),
      high: benchmarkClose.map((value) => value + 1),
      low: benchmarkClose.map((value) => value - 1),
      close: benchmarkClose,
      adjusted_close: benchmarkClose,
      source: "fixture",
      collected_at: "2026-07-26T12:00:00Z",
    },
  });
}

test("parses equal-length monotonic validation price arrays", () => {
  const artifact = parseValidationPriceArtifact(artifactJson());

  expect(artifact.instrument.ticker).toBe("NVDA");
  expect(artifact.instrument.sessions).toHaveLength(23);
  expect(artifact.benchmark.ticker).toBe("SPY");
});

test("parses Decimal price arrays serialized as JSON strings by the backend", () => {
  const payload = JSON.parse(artifactJson());
  for (const series of [payload.instrument, payload.benchmark]) {
    for (const field of ["open", "high", "low", "close", "adjusted_close"]) {
      series[field] = series[field].map((value: number) => String(value));
    }
  }

  const artifact = parseValidationPriceArtifact(JSON.stringify(payload));

  expect(artifact.instrument.open[0]).toBe(99);
  expect(artifact.instrument.adjusted_close[0]).toBe(50);
  expect(artifact.benchmark.close[0]).toBe(200);
});

test("rejects malformed or non-monotonic validation price artifacts", () => {
  const malformed = JSON.parse(artifactJson());
  malformed.instrument.high.pop();
  expect(() => parseValidationPriceArtifact(JSON.stringify(malformed))).toThrow(
    "价格数组长度不一致",
  );

  const nonMonotonic = JSON.parse(artifactJson());
  [nonMonotonic.instrument.sessions[0], nonMonotonic.instrument.sessions[1]] = [
    nonMonotonic.instrument.sessions[1],
    nonMonotonic.instrument.sessions[0],
  ];
  expect(() => parseValidationPriceArtifact(JSON.stringify(nonMonotonic))).toThrow(
    "交易日必须严格递增",
  );
});

test("builds adjusted candles and entry-normalized benchmark replay", () => {
  const artifact = parseValidationPriceArtifact(artifactJson());
  const replay = buildReplayData(artifact, {
    entry_session: "2026-07-03",
    exit_session: "2026-07-23",
    horizon: 20,
  });

  expect(replay.candles).toHaveLength(23);
  expect(replay.candles[2]).toEqual({
    time: "2026-07-03",
    open: 50.5,
    high: 52,
    low: 50,
    close: 51,
  });
  expect(replay.candles.at(-1)?.time).toBe("2026-07-23");
  expect(replay.instrumentPerformance[0]).toEqual({ time: "2026-07-03", value: 100 });
  expect(replay.benchmarkPerformance[0]).toEqual({ time: "2026-07-03", value: 100 });
  expect(replay.instrumentPerformance.at(-1)?.value).toBeCloseTo(119.607843, 5);
  expect(replay.benchmarkPerformance.at(-1)?.value).toBeCloseTo(119.607843, 5);
  expect(replay.verificationNodeCount).toBe(21);
  expect(replay.source).toBe("fixture");
  expect(replay.collectedAt).toBe("2026-07-26T12:00:00Z");
});

test("selects the longest completed validation with a bound artifact", () => {
  const selected = selectDefaultHorizon([
    { horizon: 1, status: "completed", data_artifact_id: "artifact-1" },
    { horizon: 5, status: "retry_wait", data_artifact_id: null },
    { horizon: 20, status: "completed", data_artifact_id: "artifact-20" },
  ]);

  expect(selected).toBe(20);
  expect(selectDefaultHorizon([{ horizon: 20, status: "scheduled", data_artifact_id: null }]))
    .toBe(20);
});

test("formats audit horizon labels and exchange-aware TradingView URLs", () => {
  expect(horizonLabel(20)).toBe("20 日");
  expect(tradingViewUrl("NVDA", "NMS")).toBe(
    "https://www.tradingview.com/chart/?symbol=NASDAQ%3ANVDA&interval=D",
  );
  expect(tradingViewUrl("GLD", "PCX")).toContain("symbol=AMEX%3AGLD");
  expect(tradingViewUrl("BTC-USD", null)).toContain("symbol=BTC-USD");
});

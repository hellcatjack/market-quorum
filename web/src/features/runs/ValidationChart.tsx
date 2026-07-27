import { useEffect, useRef } from "react";
import {
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  LineSeries,
  LineStyle,
  type IChartApi,
  type IRange,
  type Time,
} from "lightweight-charts";

import type { ReplayData } from "./validationReplay";
import { useI18n } from "../../i18n/I18nProvider";

interface Milestone {
  horizon: number;
  session: string;
}

interface ValidationChartProps {
  replay: ReplayData;
  instrumentTicker: string;
  benchmarkTicker: string;
  milestones: Milestone[];
  priceTarget: number | null;
  entryPrice: number | null;
  maxAdverseExcursion: number | null;
  maxFavorableExcursion: number | null;
}

function finite(value: number | null): value is number {
  return value !== null && Number.isFinite(value);
}

function observeChart(container: HTMLDivElement, chart: IChartApi): () => void {
  const resize = () => chart.applyOptions({ width: container.clientWidth });
  resize();
  if (typeof ResizeObserver === "undefined") return () => undefined;
  const observer = new ResizeObserver(resize);
  observer.observe(container);
  return () => observer.disconnect();
}

export function ValidationChart({
  replay,
  instrumentTicker,
  benchmarkTicker,
  milestones,
  priceTarget,
  entryPrice,
  maxAdverseExcursion,
  maxFavorableExcursion,
}: ValidationChartProps) {
  const { locale, t } = useI18n();
  const priceContainer = useRef<HTMLDivElement>(null);
  const performanceContainer = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!priceContainer.current || !performanceContainer.current) return;
    const common = {
      layout: {
        background: { type: ColorType.Solid, color: "#fffdf8" },
        textColor: "#475467",
        attributionLogo: true,
      },
      grid: {
        vertLines: { color: "#edf0f2" },
        horzLines: { color: "#edf0f2" },
      },
      rightPriceScale: { borderColor: "#d8dee3" },
      timeScale: { borderColor: "#d8dee3", timeVisible: false },
      localization: { locale },
    };
    const priceChart = createChart(priceContainer.current, {
      ...common,
      height: 360,
    });
    const candleSeries = priceChart.addSeries(CandlestickSeries, {
      title: `${instrumentTicker} · ${t("复权日 K")}`,
      upColor: "#16794b",
      downColor: "#c43f4f",
      borderVisible: false,
      wickUpColor: "#16794b",
      wickDownColor: "#c43f4f",
    });
    candleSeries.setData(replay.candles.map((item) => ({ ...item, time: item.time as Time })));
    createSeriesMarkers(
      candleSeries,
      [
        {
          time: replay.entrySession as Time,
          position: "belowBar" as const,
          color: "#176b87",
          shape: "arrowUp" as const,
          text: t("验证起点"),
        },
        ...milestones.map((item) => ({
          time: item.session as Time,
          position: "aboveBar" as const,
          color: item.horizon === 20 ? "#7a3e00" : "#667085",
          shape: "circle" as const,
          text: `${item.horizon}D`,
        })),
      ],
    );

    if (finite(priceTarget) && priceTarget > 0) {
      candleSeries.createPriceLine({
        price: priceTarget,
        color: "#7a3e00",
        lineStyle: LineStyle.Dashed,
        lineWidth: 1,
        axisLabelVisible: true,
        title: t("原目标价"),
      });
    }
    if (finite(entryPrice) && finite(maxAdverseExcursion)) {
      candleSeries.createPriceLine({
        price: entryPrice * (1 + maxAdverseExcursion),
        color: "#c43f4f",
        lineStyle: LineStyle.Dotted,
        lineWidth: 1,
        axisLabelVisible: true,
        title: "MAE",
      });
    }
    if (finite(entryPrice) && finite(maxFavorableExcursion)) {
      candleSeries.createPriceLine({
        price: entryPrice * (1 + maxFavorableExcursion),
        color: "#16794b",
        lineStyle: LineStyle.Dotted,
        lineWidth: 1,
        axisLabelVisible: true,
        title: "MFE",
      });
    }

    const performanceChart = createChart(performanceContainer.current, {
      ...common,
      height: 190,
    });
    const instrumentSeries = performanceChart.addSeries(LineSeries, {
      title: instrumentTicker,
      color: "#176b87",
      lineWidth: 3,
      priceFormat: { type: "custom", formatter: (value: number) => value.toFixed(1) },
    });
    const benchmarkSeries = performanceChart.addSeries(LineSeries, {
      title: benchmarkTicker,
      color: "#8b95a1",
      lineWidth: 2,
      priceFormat: { type: "custom", formatter: (value: number) => value.toFixed(1) },
    });
    instrumentSeries.setData(
      replay.instrumentPerformance.map((item) => ({ ...item, time: item.time as Time })),
    );
    benchmarkSeries.setData(
      replay.benchmarkPerformance.map((item) => ({ ...item, time: item.time as Time })),
    );
    instrumentSeries.createPriceLine({
      price: 100,
      color: "#98a2b3",
      lineStyle: LineStyle.Dashed,
      lineWidth: 1,
      axisLabelVisible: true,
      title: t("起点 100"),
    });

    let syncing = false;
    const priceToPerformance = (range: IRange<Time> | null) => {
      if (!range || syncing) return;
      syncing = true;
      performanceChart.timeScale().setVisibleRange(range);
      syncing = false;
    };
    const performanceToPrice = (range: IRange<Time> | null) => {
      if (!range || syncing) return;
      syncing = true;
      priceChart.timeScale().setVisibleRange(range);
      syncing = false;
    };
    priceChart.timeScale().subscribeVisibleTimeRangeChange(priceToPerformance);
    performanceChart.timeScale().subscribeVisibleTimeRangeChange(performanceToPrice);
    priceChart.timeScale().fitContent();
    performanceChart.timeScale().fitContent();

    const stopPriceResize = observeChart(priceContainer.current, priceChart);
    const stopPerformanceResize = observeChart(performanceContainer.current, performanceChart);
    return () => {
      stopPriceResize();
      stopPerformanceResize();
      priceChart.timeScale().unsubscribeVisibleTimeRangeChange(priceToPerformance);
      performanceChart.timeScale().unsubscribeVisibleTimeRangeChange(performanceToPrice);
      priceChart.remove();
      performanceChart.remove();
    };
  }, [
    benchmarkTicker,
    entryPrice,
    instrumentTicker,
    locale,
    maxAdverseExcursion,
    maxFavorableExcursion,
    milestones,
    priceTarget,
    replay,
    t,
  ]);

  return (
    <div className="validation-chart" data-testid="validation-chart">
      <div className="validation-chart__legend" aria-label={t("图例")}>
        <span><i className="validation-chart__swatch validation-chart__swatch--instrument" />{t("{ticker} 复权价格", { ticker: instrumentTicker })}</span>
        <span><i className="validation-chart__swatch validation-chart__swatch--benchmark" />{t("{ticker} 基准", { ticker: benchmarkTicker })}</span>
      </div>
      <div ref={priceContainer} className="validation-chart__price" aria-label={t("{ticker} 复权日 K 线", { ticker: instrumentTicker })} />
      <p className="validation-chart__subtitle">{t("相对表现 · 验证起点归一化为 100")}</p>
      <div ref={performanceContainer} className="validation-chart__performance" aria-label={t("{ticker} 与 {benchmark} 相对表现", { ticker: instrumentTicker, benchmark: benchmarkTicker })} />
    </div>
  );
}

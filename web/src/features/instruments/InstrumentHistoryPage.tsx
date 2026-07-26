import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "wouter";

import { getInstrument, getInstrumentHistory } from "../../api/records";
import { LocalTime } from "../runs/RunTimeline";

export function InstrumentHistoryPage() {
  const { ticker = "" } = useParams<{ ticker: string }>();
  const normalized = ticker.toUpperCase();
  const summary = useQuery({
    queryKey: ["instrument", normalized],
    queryFn: () => getInstrument(normalized),
    enabled: Boolean(normalized),
    retry: false,
  });
  const history = useQuery({
    queryKey: ["instrument-history", normalized],
    queryFn: () => getInstrumentHistory(normalized),
    enabled: Boolean(normalized),
    retry: false,
  });

  if (summary.isError || history.isError) {
    return <p className="page-shell page-warning" role="alert">无法读取该标的的历史评估。</p>;
  }
  return (
    <section className="page-shell instrument-page">
      <header className="instrument-hero">
        <div><p className="eyebrow">标的档案 / 历次结论</p><h1>{normalized} 历史评估</h1><p>对比模型、配置和结论变化，为后续表现验证保留基线。</p></div>
        <dl>
          <div><dt>评估次数</dt><dd>{summary.data?.assessment_count ?? "—"}</dd></div>
          <div><dt>最新评级</dt><dd>{summary.data?.latest_rating ?? "—"}</dd></div>
          <div><dt>资产类型</dt><dd>{summary.data?.asset_types.join(" / ") ?? "—"}</dd></div>
        </dl>
      </header>
      <div className="history-table-wrap panel">
        <table className="history-table">
          <thead><tr><th scope="col">评估</th><th scope="col">评级 / 目标价</th><th scope="col">摘要</th><th scope="col">模型</th><th scope="col">配置哈希</th><th scope="col">验证</th></tr></thead>
          <tbody>
            {(history.data ?? []).map((item) => (
              <tr key={item.run.id}>
                <td><Link href={`/runs/${item.run.id}`}>{item.run.analysis_date}</Link><LocalTime value={item.run.created_at} /></td>
                <td><strong>{item.rating ?? "—"}</strong><span>{item.price_target ?? "—"}</span></td>
                <td>{item.executive_summary ?? "尚无摘要"}</td>
                <td>{item.gateway_model ?? "—"} / {item.gateway_reasoning_effort ?? "—"}</td>
                <td><code title={item.config_snapshot_sha256 ?? undefined}>{item.config_snapshot_sha256 ?? "—"}</code></td>
                <td>{item.validation_outcome ?? "待验证"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {history.isLoading ? <p className="table-empty" role="status">正在载入历史…</p> : null}
      </div>
    </section>
  );
}

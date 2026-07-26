import { useQuery } from "@tanstack/react-query";

import { getCapacity } from "../../api/assessments";
import { AssessmentForm } from "./AssessmentForm";

export function NewAssessmentPage() {
  const capacity = useQuery({
    queryKey: ["system-capacity"],
    queryFn: getCapacity,
    refetchInterval: 5_000,
    retry: false,
  });
  return (
    <section className="page-shell">
      <header className="page-header">
        <p className="eyebrow">TradingNG / 评估派发</p>
        <h1>新建评估</h1>
        <p>批量输入标的，由平台按实际容量安全调度。</p>
      </header>
      {capacity.isError ? <p className="page-warning" role="alert">暂时无法读取容量，任务仍可安全入队。</p> : null}
      <AssessmentForm capacity={capacity.data ?? null} />
    </section>
  );
}

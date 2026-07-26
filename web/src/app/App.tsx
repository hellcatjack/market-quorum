import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Route, Switch } from "wouter";

import { NewAssessmentPage } from "../features/assessments/NewAssessmentPage";
import { DashboardPage } from "../features/dashboard/DashboardPage";
import { InstrumentHistoryPage } from "../features/instruments/InstrumentHistoryPage";
import { RunDetailPage } from "../features/runs/RunDetailPage";
import { SystemPage } from "../features/system/SystemPage";
import { Layout } from "./Layout";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { refetchOnWindowFocus: false },
  },
});

function PlaceholderPage({ title, description }: { title: string; description: string }) {
  return (
    <section className="page-shell">
      <header className="page-header">
        <p className="eyebrow">TradingNG / 内部研究</p>
        <h1>{title}</h1>
        <p>{description}</p>
      </header>
      <div className="empty-state" role="status">
        <strong>模块已接入平台路由</strong>
        <span>数据组件将在下一阶段连接评估 API。</span>
      </div>
    </section>
  );
}

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <Layout>
        <Switch>
          <Route path="/">
            <DashboardPage />
          </Route>
          <Route path="/new">
            <NewAssessmentPage />
          </Route>
          <Route path="/runs/:runId">
            <RunDetailPage />
          </Route>
          <Route path="/instruments/:ticker">
            <InstrumentHistoryPage />
          </Route>
          <Route path="/system">
            <SystemPage />
          </Route>
          <Route>
            <PlaceholderPage title="页面不存在" description="请从主导航选择一个功能。" />
          </Route>
        </Switch>
      </Layout>
    </QueryClientProvider>
  );
}

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Route, Switch } from "wouter";

import { NewAssessmentPage } from "../features/assessments/NewAssessmentPage";
import { DashboardPage } from "../features/dashboard/DashboardPage";
import { InstrumentHistoryPage } from "../features/instruments/InstrumentHistoryPage";
import { RunDetailPage } from "../features/runs/RunDetailPage";
import { SystemPage } from "../features/system/SystemPage";
import { Layout } from "./Layout";
import { useI18n } from "../i18n/I18nProvider";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { refetchOnWindowFocus: false },
  },
});

function PlaceholderPage({ title, description }: { title: string; description: string }) {
  const { t } = useI18n();
  return (
    <section className="page-shell">
      <header className="page-header">
        <p className="eyebrow">{t("TradingNG / 内部研究")}</p>
        <h1>{title}</h1>
        <p>{description}</p>
      </header>
      <div className="empty-state" role="status">
        <strong>{t("模块已接入平台路由")}</strong>
        <span>{t("数据组件将在下一阶段连接评估 API。")}</span>
      </div>
    </section>
  );
}

export function App() {
  const { t } = useI18n();
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
            <PlaceholderPage title={t("页面不存在")} description={t("请从主导航选择一个功能。")} />
          </Route>
        </Switch>
      </Layout>
    </QueryClientProvider>
  );
}

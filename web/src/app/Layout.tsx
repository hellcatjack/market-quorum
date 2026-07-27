import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { Link, useLocation } from "wouter";

import { apiRequest } from "../api/client";
import { useI18n } from "../i18n/I18nProvider";

interface CurrentUser {
  subject: string;
  display_name: string;
  scopes: string[];
  roles: string[];
}

function navigationClass(isActive: boolean): string {
  return isActive ? "nav-link nav-link--active" : "nav-link";
}

function NavigationLink({
  href,
  end = false,
  children,
}: {
  href: string;
  end?: boolean;
  children: ReactNode;
}) {
  const [location] = useLocation();
  const isActive = end ? location === href : location.startsWith(href);
  return (
    <Link className={navigationClass(isActive)} href={href}>
      {children}
    </Link>
  );
}

export function Layout({ children }: { children: ReactNode }) {
  const { locale, setLocale, t } = useI18n();
  const currentUser = useQuery({
    queryKey: ["current-user"],
    queryFn: () => apiRequest<CurrentUser>("/api/v1/me"),
    staleTime: 60_000,
    retry: false,
  });

  return (
    <div className="app-frame">
      <header className="topbar">
        <Link className="brand" href="/" aria-label={t("TradingNG 首页")}>
          <span className="brand-mark" aria-hidden="true">
            TN
          </span>
          <span>
            <strong>TradingNG</strong>
            <small>{t("投资研究工作台")}</small>
          </span>
        </Link>
        <div className="topbar-meta">
          <span className="queue-badge" aria-label={t("排队任务")}>
            {t("队列")} <strong>—</strong>
          </span>
          <label className="locale-picker">
            <span className="sr-only">{t("界面语言")}</span>
            <select
              aria-label={t("界面语言")}
              value={locale}
              onChange={(event) => setLocale(event.target.value as "zh-CN" | "en-US")}
            >
              <option value="zh-CN">{t("中文")}</option>
              <option value="en-US">{t("English")}</option>
            </select>
          </label>
          <span className="current-user" aria-live="polite">
            {currentUser.data?.display_name || (currentUser.isError ? t("身份不可用") : t("载入中…"))}
          </span>
          <a className="logout-link" href="/oauth2/sign_out">
            {t("退出")}
          </a>
        </div>
      </header>
      <div className="app-body">
        <nav className="sidebar" aria-label={t("主导航")}>
          <NavigationLink href="/" end>
            <span aria-hidden="true">▦</span> {t("总览")}
          </NavigationLink>
          <NavigationLink href="/new">
            <span aria-hidden="true">＋</span> {t("新建评估")}
          </NavigationLink>
          <NavigationLink href="/system">
            <span aria-hidden="true">◉</span> {t("系统状态")}
          </NavigationLink>
        </nav>
        <main className="main-content" id="main-content">
          {children}
        </main>
      </div>
    </div>
  );
}

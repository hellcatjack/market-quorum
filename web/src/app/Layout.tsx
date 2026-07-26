import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { Link, useLocation } from "wouter";

import { apiRequest } from "../api/client";

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
  const currentUser = useQuery({
    queryKey: ["current-user"],
    queryFn: () => apiRequest<CurrentUser>("/api/v1/me"),
    staleTime: 60_000,
    retry: false,
  });

  return (
    <div className="app-frame">
      <header className="topbar">
        <Link className="brand" href="/" aria-label="TradingNG 首页">
          <span className="brand-mark" aria-hidden="true">
            TN
          </span>
          <span>
            <strong>TradingNG</strong>
            <small>投资研究工作台</small>
          </span>
        </Link>
        <div className="topbar-meta">
          <span className="queue-badge" aria-label="排队任务">
            队列 <strong>—</strong>
          </span>
          <span className="current-user" aria-live="polite">
            {currentUser.data?.display_name || (currentUser.isError ? "身份不可用" : "载入中…")}
          </span>
          <a className="logout-link" href="/oauth2/sign_out">
            退出
          </a>
        </div>
      </header>
      <div className="app-body">
        <nav className="sidebar" aria-label="主导航">
          <NavigationLink href="/" end>
            <span aria-hidden="true">▦</span> 总览
          </NavigationLink>
          <NavigationLink href="/new">
            <span aria-hidden="true">＋</span> 新建评估
          </NavigationLink>
          <NavigationLink href="/system">
            <span aria-hidden="true">◉</span> 系统状态
          </NavigationLink>
        </nav>
        <main className="main-content" id="main-content">
          {children}
        </main>
      </div>
    </div>
  );
}

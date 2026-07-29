import type { ReactNode } from "react";

import { useCurrentUser } from "../auth/CurrentUserContext";
import { AccessDeniedPage } from "../features/auth/AccessDeniedPage";
import { useI18n } from "../i18n/I18nProvider";

export function AuthorizedRoute({
  role,
  scope,
  children,
}: {
  role: "Admin" | "User";
  scope: string;
  children: ReactNode;
}) {
  const { t } = useI18n();
  const identity = useCurrentUser();
  if (identity.isLoading) {
    return <div className="empty-state" role="status">{t("正在核验访问权限…")}</div>;
  }
  if (
    identity.isError
    || !identity.hasRole(role)
    || !identity.hasScope(scope)
  ) {
    return <AccessDeniedPage />;
  }
  return children;
}

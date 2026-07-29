import { Link } from "wouter";

import { useI18n } from "../../i18n/I18nProvider";

export function AccessDeniedPage() {
  const { t } = useI18n();
  return (
    <section className="page-shell access-denied-page">
      <header className="page-header">
        <p className="eyebrow">{t("TradingNG / 权限边界")}</p>
        <h1>{t("无权访问")}</h1>
        <p>{t("当前账号没有访问此管理模块所需的角色和权限。")}</p>
      </header>
      <div className="empty-state" role="alert">
        <strong>{t("受限内容未被载入")}</strong>
        <span>{t("如需访问，请联系管理员调整账号角色后重新登录。")}</span>
        <Link className="secondary-button" href="/">{t("返回总览")}</Link>
      </div>
    </section>
  );
}

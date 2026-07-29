import { useEffect, useId, useState } from "react";

import { useI18n } from "../../i18n/I18nProvider";

export function TemporaryPasswordDialog({
  username,
  temporaryPassword,
  clearAndClose,
}: {
  username: string;
  temporaryPassword: string;
  clearAndClose: () => void;
}) {
  const { t } = useI18n();
  const titleId = useId();
  const descriptionId = useId();
  const [visiblePassword, setVisiblePassword] = useState(temporaryPassword);
  const [copyStatus, setCopyStatus] = useState<"idle" | "copied" | "failed">("idle");

  const close = () => {
    setVisiblePassword("");
    setCopyStatus("idle");
    clearAndClose();
  };

  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") close();
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", handleKeyDown);
    };
  });

  const copyPassword = async () => {
    try {
      await navigator.clipboard.writeText(visiblePassword);
      setCopyStatus("copied");
    } catch {
      setCopyStatus("failed");
    }
  };

  return (
    <div className="delete-dialog-backdrop">
      <section
        className="delete-dialog temporary-password-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
      >
        <span className="delete-dialog__icon" aria-hidden="true">✓</span>
        <div>
          <p className="eyebrow">{t("一次性凭据")}</p>
          <h2 id={titleId}>{t("{username} 的临时密码", { username })}</h2>
          <p id={descriptionId}>{t("该密码只显示一次。关闭前请通过安全渠道保存并交付给用户。")}</p>
          <code className="temporary-password-dialog__secret">{visiblePassword}</code>
          <div className="temporary-password-dialog__copy-row">
            <button type="button" onClick={copyPassword}>{t("复制临时密码")}</button>
            {copyStatus !== "idle" ? (
              <span role="status">
                {copyStatus === "copied" ? t("已复制") : t("复制失败，请手工保存")}
              </span>
            ) : null}
          </div>
          <p className="delete-dialog__notice">
            {t("用户首次登录时必须修改该密码；平台不会再次显示它。")}
          </p>
          <div className="delete-dialog__actions">
            <button type="button" className="delete-dialog__confirm" onClick={close} autoFocus>
              {t("我已安全保存，关闭")}
            </button>
          </div>
        </div>
      </section>
    </div>
  );
}

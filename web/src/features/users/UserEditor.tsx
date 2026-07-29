import { useId, useRef, useState, type FormEvent, type ReactNode } from "react";

import type {
  CreateUserPayload,
  UpdateUserPayload,
  UserDetail,
  UserRole,
} from "../../api/users";
import { useI18n } from "../../i18n/I18nProvider";

type EditorPayload = CreateUserPayload | UpdateUserPayload;

interface CommonProps {
  pending: boolean;
  error?: ReactNode;
  onSubmit: (payload: EditorPayload) => void;
  onCancel: () => void;
}

type UserEditorProps = CommonProps & (
  | { mode: "create"; detail?: never }
  | { mode: "edit"; detail: UserDetail }
);

type FieldErrors = Partial<Record<"username" | "displayName" | "email", string>>;

function actionReason(reason: string | undefined): "不能移除当前登录管理员自己的管理权限。" | "必须至少保留一个已启用的管理员。" | null {
  if (reason === "self_admin_change_forbidden") {
    return "不能移除当前登录管理员自己的管理权限。";
  }
  if (reason === "last_admin_protected") {
    return "必须至少保留一个已启用的管理员。";
  }
  return null;
}

export function UserEditor(props: UserEditorProps) {
  const { t } = useI18n();
  const formId = useId();
  const editing = props.mode === "edit" ? props.detail : undefined;
  const [username, setUsername] = useState(editing?.user.username ?? "");
  const [displayName, setDisplayName] = useState(editing?.user.display_name ?? "");
  const [email, setEmail] = useState(editing?.user.email ?? "");
  const [role, setRole] = useState<UserRole>(editing?.user.role ?? "User");
  const [enabled, setEnabled] = useState(editing?.user.enabled ?? true);
  const [errors, setErrors] = useState<FieldErrors>({});
  const usernameRef = useRef<HTMLInputElement>(null);
  const displayNameRef = useRef<HTMLInputElement>(null);
  const emailRef = useRef<HTMLInputElement>(null);

  const roleReason = actionReason(editing?.action_reasons.change_role);
  const enabledReason = actionReason(editing?.action_reasons.change_enabled);

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (props.pending) return;
    const normalizedUsername = username.trim().toLowerCase();
    const normalizedName = displayName.trim();
    const normalizedEmail = email.trim();
    const nextErrors: FieldErrors = {};
    if (props.mode === "create" && !normalizedUsername) {
      nextErrors.username = t("请输入用户名");
    } else if (props.mode === "create" && !/^[a-z0-9._-]{3,64}$/.test(normalizedUsername)) {
      nextErrors.username = t("用户名需为 3–64 位小写字母、数字、点、下划线或连字符");
    }
    if (!normalizedName) nextErrors.displayName = t("请输入显示名称");
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(normalizedEmail)) {
      nextErrors.email = t("请输入有效邮箱地址");
    }
    setErrors(nextErrors);
    if (nextErrors.username) usernameRef.current?.focus();
    else if (nextErrors.displayName) displayNameRef.current?.focus();
    else if (nextErrors.email) emailRef.current?.focus();
    if (Object.keys(nextErrors).length > 0) return;

    if (props.mode === "create") {
      props.onSubmit({
        username: normalizedUsername,
        display_name: normalizedName,
        email: normalizedEmail,
        role,
      });
      return;
    }
    props.onSubmit({
      display_name: normalizedName,
      email: normalizedEmail,
      role,
      enabled,
    });
  };

  return (
    <form className="user-editor" onSubmit={submit} noValidate>
      <div className="user-editor__grid">
        <div className="user-editor__field">
          <label htmlFor={`${formId}-username`}>{t("用户名")}</label>
          <input
            id={`${formId}-username`}
            ref={usernameRef}
            name="username"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            readOnly={props.mode === "edit"}
            disabled={props.pending}
            autoComplete="off"
          />
          {errors.username ? <small className="field-error">{errors.username}</small> : null}
        </div>
        <div className="user-editor__field">
          <label htmlFor={`${formId}-display-name`}>{t("显示名称")}</label>
          <input
            id={`${formId}-display-name`}
            ref={displayNameRef}
            name="display_name"
            value={displayName}
            onChange={(event) => setDisplayName(event.target.value)}
            disabled={props.pending || editing?.allowed_actions.edit_profile === false}
            autoComplete="name"
          />
          {errors.displayName ? <small className="field-error">{errors.displayName}</small> : null}
        </div>
        <div className="user-editor__field">
          <label htmlFor={`${formId}-email`}>{t("邮箱")}</label>
          <input
            id={`${formId}-email`}
            ref={emailRef}
            name="email"
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            disabled={props.pending || editing?.allowed_actions.edit_profile === false}
            autoComplete="email"
          />
          {errors.email ? <small className="field-error">{errors.email}</small> : null}
        </div>
        <div className="user-editor__field">
          <label htmlFor={`${formId}-role`}>{t("角色")}</label>
          <select
            id={`${formId}-role`}
            name="role"
            value={role}
            onChange={(event) => setRole(event.target.value as UserRole)}
            disabled={props.pending || editing?.allowed_actions.change_role === false}
          >
            <option value="User">{t("一般用户")}</option>
            <option value="Admin">{t("管理员")}</option>
          </select>
          {roleReason ? <small className="control-reason">{t(roleReason)}</small> : null}
        </div>
        {editing ? (
          <div className="user-editor__field user-editor__checkbox">
            <label>
              <input
                type="checkbox"
                checked={enabled}
                onChange={(event) => setEnabled(event.target.checked)}
                disabled={props.pending || !editing.allowed_actions.change_enabled}
              />
              <span>{t("账号已启用")}</span>
            </label>
            {enabledReason ? <small className="control-reason">{t(enabledReason)}</small> : null}
          </div>
        ) : null}
      </div>
      {props.error}
      <div className="user-editor__actions">
        <button type="button" onClick={props.onCancel} disabled={props.pending}>{t("取消")}</button>
        <button className="primary-button" type="submit" disabled={props.pending}>
          {props.pending ? t("保存中…") : props.mode === "create" ? t("创建账号") : t("保存更改")}
        </button>
      </div>
    </form>
  );
}

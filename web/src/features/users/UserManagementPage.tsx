import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";

import { ApiClientError } from "../../api/client";
import {
  createUser,
  getUser,
  listUsers,
  logoutUser,
  resetUserPassword,
  updateUser,
  type CreateUserPayload,
  type UpdateUserPayload,
  type UserRole,
  type UserStatusFilter,
  type UserView,
} from "../../api/users";
import { useI18n } from "../../i18n/I18nProvider";
import { TemporaryPasswordDialog } from "./TemporaryPasswordDialog";
import { UserEditor } from "./UserEditor";

const PAGE_SIZE = 20;

type Confirmation = {
  kind: "status" | "reset" | "logout";
  user: UserView;
};

function errorMessage(error: unknown) {
  if (!(error instanceof ApiClientError)) {
    return { message: "操作失败，请稍后重试。" as const, requestId: null };
  }
  const messages = {
    username_conflict: "该用户名已被使用。",
    email_conflict: "该邮箱已被使用。",
    user_not_found: "用户不存在或已被移除。",
    identity_provider_forbidden: "身份管理服务尚未正确配置。",
    identity_provider_unavailable: "身份提供方暂时不可用，请稍后重试。",
    identity_sync_pending: "身份提供方已完成变更，但本地同步仍在等待；请刷新核对，勿重复创建。",
    self_admin_change_forbidden: "不能移除当前登录管理员自己的管理权限。",
    last_admin_protected: "必须至少保留一个已启用的管理员。",
  } as const;
  return {
    message: messages[error.code as keyof typeof messages] ?? "操作失败，请稍后重试。",
    requestId: error.requestId,
  };
}

function ErrorNotice({ error }: { error: unknown }) {
  const { t } = useI18n();
  if (!error) return null;
  const mapped = errorMessage(error);
  return (
    <p className="form-error user-error" role="alert">
      <span>{t(mapped.message)}</span>
      {mapped.requestId ? <small>{t("请求编号：{id}", { id: mapped.requestId })}</small> : null}
    </p>
  );
}

function ConfirmationDialog({
  confirmation,
  pending,
  error,
  onCancel,
  onConfirm,
}: {
  confirmation: Confirmation;
  pending: boolean;
  error: unknown;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const { t } = useI18n();
  const { kind, user } = confirmation;
  const disabling = user.enabled;
  const title = kind === "status"
    ? disabling ? t("停用 {username}？", { username: user.username }) : t("启用 {username}？", { username: user.username })
    : kind === "reset"
      ? t("重置 {username} 的密码？", { username: user.username })
      : t("强制退出 {username}？", { username: user.username });
  const description = kind === "status"
    ? disabling ? t("停用后，该用户现有凭据将立即失效。") : t("启用后，该用户可以重新登录平台。")
    : kind === "reset"
      ? t("现有会话将退出，新临时密码只会显示一次。")
      : t("该用户的所有现有会话将立即失效。账号本身不受影响。");
  const confirmLabel = kind === "status"
    ? disabling ? t("确认停用") : t("确认启用")
    : kind === "reset" ? t("确认重置") : t("确认退出");

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !pending) onCancel();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [onCancel, pending]);

  return (
    <div className="delete-dialog-backdrop">
      <section className="delete-dialog user-confirmation" role="dialog" aria-modal="true" aria-label={title}>
        <span className="delete-dialog__icon" aria-hidden="true">!</span>
        <div>
          <p className="eyebrow">{t("确认管理操作")}</p>
          <h2>{title}</h2>
          <p>{description}</p>
          <ErrorNotice error={error} />
          <div className="delete-dialog__actions">
            <button type="button" onClick={onCancel} disabled={pending}>{t("取消")}</button>
            <button type="button" className="delete-dialog__confirm" onClick={onConfirm} disabled={pending}>
              {pending ? t("处理中…") : confirmLabel}
            </button>
          </div>
        </div>
      </section>
    </div>
  );
}

function EditorPanel({ children, title }: { children: ReactNode; title: string }) {
  return (
    <section className="user-editor-panel" aria-label={title}>
      <div className="section-heading"><p className="eyebrow">Identity</p><h2>{title}</h2></div>
      {children}
    </section>
  );
}

export function UserManagementPage() {
  const { formatDateTime, t } = useI18n();
  const queryClient = useQueryClient();
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [role, setRole] = useState<UserRole | "">("");
  const [status, setStatus] = useState<UserStatusFilter | "">("");
  const [page, setPage] = useState(1);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [editor, setEditor] = useState<"create" | "edit" | null>(null);
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);
  const [credential, setCredential] = useState<{ username: string; password: string } | null>(null);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedSearch(search.trim()), 300);
    return () => window.clearTimeout(timer);
  }, [search]);

  const users = useQuery({
    queryKey: ["admin-users", debouncedSearch, role, status, page, PAGE_SIZE],
    queryFn: () => listUsers({
      search: debouncedSearch || undefined,
      role: role || undefined,
      status: status || undefined,
      page,
      pageSize: PAGE_SIZE,
    }),
    retry: false,
  });
  const detail = useQuery({
    queryKey: ["admin-user", selectedId],
    queryFn: () => getUser(selectedId!),
    enabled: Boolean(selectedId),
    retry: false,
  });

  const refresh = async (updated?: Awaited<ReturnType<typeof updateUser>>) => {
    if (updated) queryClient.setQueryData(["admin-user", updated.user.id], updated);
    await queryClient.invalidateQueries({ queryKey: ["admin-users"] });
    if (selectedId) await queryClient.invalidateQueries({ queryKey: ["admin-user", selectedId] });
  };

  const createMutation = useMutation<void, unknown, CreateUserPayload>({
    mutationFn: async (payload) => {
      const created = await createUser(payload);
      setCredential({ username: created.user.username, password: created.temporary_password });
    },
    onSuccess: async () => {
      setEditor(null);
      await refresh();
    },
  });
  const updateMutation = useMutation({
    mutationFn: ({ userId, payload }: { userId: string; payload: UpdateUserPayload }) => updateUser(userId, payload),
    onSuccess: async (updated) => {
      setEditor(null);
      setConfirmation(null);
      await refresh(updated);
    },
  });
  const resetMutation = useMutation<void, unknown, UserView>({
    mutationFn: async (user) => {
      const reset = await resetUserPassword(user.id);
      setCredential({ username: reset.user.username, password: reset.temporary_password });
    },
    onSuccess: async () => {
      setConfirmation(null);
      await refresh();
    },
  });
  const logoutMutation = useMutation({
    mutationFn: (user: UserView) => logoutUser(user.id),
    onSuccess: async (updated) => {
      setConfirmation(null);
      await refresh(updated);
    },
  });

  const activeConfirmationError = confirmation?.kind === "reset"
    ? resetMutation.error
    : confirmation?.kind === "logout"
      ? logoutMutation.error
      : updateMutation.error;
  const confirmationPending = resetMutation.isPending || logoutMutation.isPending || updateMutation.isPending;

  return (
    <section className="page-shell user-management-page">
      <header className="page-header user-management-header">
        <div>
          <p className="eyebrow">{t("身份与访问控制")}</p>
          <h1>{t("用户管理")}</h1>
          <p>{t("账号由 Keycloak 统一管理；平台同步正式角色、状态与审计记录。")}</p>
        </div>
        <button className="primary-button" type="button" onClick={() => {
          createMutation.reset();
          setEditor("create");
        }}>{t("新建用户")}</button>
      </header>

      <section className="user-ledger" aria-label={t("用户台账")}>
        <div className="user-filters">
          <label className="user-search">
            <span>{t("搜索用户")}</span>
            <input
              type="search"
              value={search}
              placeholder={t("用户名、姓名或邮箱")}
              onChange={(event) => { setSearch(event.target.value); setPage(1); }}
            />
          </label>
          <label><span>{t("角色筛选")}</span><select value={role} onChange={(event) => { setRole(event.target.value as UserRole | ""); setPage(1); }}><option value="">{t("全部角色")}</option><option value="Admin">{t("管理员")}</option><option value="User">{t("一般用户")}</option></select></label>
          <label><span>{t("状态筛选")}</span><select value={status} onChange={(event) => { setStatus(event.target.value as UserStatusFilter | ""); setPage(1); }}><option value="">{t("全部状态")}</option><option value="active">{t("已启用")}</option><option value="disabled">{t("已停用")}</option></select></label>
        </div>
        {users.isError ? <ErrorNotice error={users.error} /> : null}
        {users.isLoading ? <p role="status">{t("正在载入用户…")}</p> : null}
        {users.data?.items.length === 0 ? <p className="empty-state">{t("没有符合条件的用户。")}</p> : null}
        {users.data?.items.length ? (
          <div className="user-table-wrap">
            <table className="user-table">
              <thead><tr><th>{t("用户")}</th><th>{t("邮箱")}</th><th>{t("角色")}</th><th>{t("状态")}</th><th>{t("最后同步")}</th><th><span className="sr-only">{t("操作")}</span></th></tr></thead>
              <tbody>{users.data.items.map((item) => (
                <tr key={item.id} className={selectedId === item.id ? "user-table__selected" : undefined}>
                  <td data-label={t("用户")}><strong>{item.display_name}</strong><small>@{item.username}</small></td>
                  <td data-label={t("邮箱")}>{item.email ?? "—"}</td>
                  <td data-label={t("角色")}><span className={`user-role user-role--${item.role.toLowerCase()}`}>{item.role === "Admin" ? t("管理员") : t("一般用户")}</span></td>
                  <td data-label={t("状态")}><span className={`user-status user-status--${item.enabled ? "active" : "disabled"}`}>{item.enabled ? `● ${t("已启用")}` : `○ ${t("已停用")}`}</span></td>
                  <td data-label={t("最后同步")}>{formatDateTime(item.synced_at)}</td>
                  <td><button type="button" onClick={() => { setSelectedId(item.id); setEditor(null); }} aria-label={t("查看 {username}", { username: item.username })}>{t("查看")}</button></td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        ) : null}
        {users.data ? (
          <nav className="user-pagination" aria-label={t("用户分页")}>
            <span>{t("第 {page} 页 · 共 {total} 位用户", { page: users.data.page, total: users.data.total })}</span>
            <div><button type="button" disabled={page <= 1} onClick={() => setPage((value) => value - 1)}>{t("上一页")}</button><button type="button" disabled={page * PAGE_SIZE >= users.data.total} onClick={() => setPage((value) => value + 1)}>{t("下一页")}</button></div>
          </nav>
        ) : null}
      </section>

      {editor === "create" ? (
        <EditorPanel title={t("创建用户")}>
          <UserEditor
            mode="create"
            pending={createMutation.isPending}
            error={<ErrorNotice error={createMutation.error} />}
            onCancel={() => { setEditor(null); createMutation.reset(); }}
            onSubmit={(payload) => createMutation.mutate(payload as CreateUserPayload)}
          />
        </EditorPanel>
      ) : null}

      {selectedId && detail.isLoading ? <p role="status">{t("正在载入用户详情…")}</p> : null}
      {selectedId && detail.isError ? <ErrorNotice error={detail.error} /> : null}
      {selectedId && detail.data && editor !== "create" ? (
        <section className="user-detail-panel">
          <div className="user-detail-panel__heading">
            <div><p className="eyebrow">@{detail.data.user.username}</p><h2>{detail.data.user.username}</h2><p>{detail.data.user.display_name} · {detail.data.user.email ?? "—"}</p></div>
            <button type="button" onClick={() => setSelectedId(null)} aria-label={t("关闭用户详情")}>×</button>
          </div>
          <dl className="user-session-summary">
            <div><dt>{t("当前会话")}</dt><dd>{t("{count} 个活动会话", { count: detail.data.sessions.active_count })}</dd></div>
            <div><dt>{t("最后访问")}</dt><dd>{detail.data.sessions.last_access_at ? formatDateTime(detail.data.sessions.last_access_at) : "—"}</dd></div>
            <div><dt>{t("身份同步")}</dt><dd>{formatDateTime(detail.data.user.synced_at)}</dd></div>
          </dl>
          {editor === "edit" ? (
            <UserEditor
              mode="edit"
              detail={detail.data}
              pending={updateMutation.isPending}
              error={<ErrorNotice error={updateMutation.error} />}
              onCancel={() => { setEditor(null); updateMutation.reset(); }}
              onSubmit={(payload) => updateMutation.mutate({ userId: detail.data.user.id, payload: payload as UpdateUserPayload })}
            />
          ) : (
            <div className="user-detail-actions">
              <button type="button" disabled={!detail.data.allowed_actions.edit_profile} onClick={() => { updateMutation.reset(); setEditor("edit"); }}>{t("编辑资料")}</button>
              <button type="button" disabled={!detail.data.allowed_actions.change_enabled} title={detail.data.action_reasons.change_enabled ? t(errorMessage(new ApiClientError(409, detail.data.action_reasons.change_enabled, "", "")).message) : undefined} onClick={() => setConfirmation({ kind: "status", user: detail.data.user })}>{detail.data.user.enabled ? t("停用账号") : t("启用账号")}</button>
              <button type="button" disabled={!detail.data.allowed_actions.reset_password} onClick={() => { resetMutation.reset(); setConfirmation({ kind: "reset", user: detail.data.user }); }}>{t("重置密码")}</button>
              <button type="button" disabled={!detail.data.allowed_actions.logout} onClick={() => { logoutMutation.reset(); setConfirmation({ kind: "logout", user: detail.data.user }); }}>{t("强制退出")}</button>
            </div>
          )}
        </section>
      ) : null}

      {confirmation ? (
        <ConfirmationDialog
          confirmation={confirmation}
          pending={confirmationPending}
          error={activeConfirmationError}
          onCancel={() => setConfirmation(null)}
          onConfirm={() => {
            if (confirmation.kind === "status") {
              updateMutation.mutate({ userId: confirmation.user.id, payload: { enabled: !confirmation.user.enabled } });
            } else if (confirmation.kind === "reset") resetMutation.mutate(confirmation.user);
            else logoutMutation.mutate(confirmation.user);
          }}
        />
      ) : null}
      {credential ? (
        <TemporaryPasswordDialog
          username={credential.username}
          temporaryPassword={credential.password}
          clearAndClose={() => setCredential(null)}
        />
      ) : null}
    </section>
  );
}

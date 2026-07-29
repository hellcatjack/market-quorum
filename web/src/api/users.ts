import { apiRequest, jsonBody } from "./client";

export type UserRole = "Admin" | "User";
export type UserStatusFilter = "active" | "disabled";

export interface UserView {
  id: string;
  subject: string;
  username: string;
  display_name: string;
  email: string | null;
  role: UserRole;
  enabled: boolean;
  synced_at: string;
}

export interface SessionSummary {
  active_count: number;
  last_access_at: string | null;
}

export interface UserActionFlags {
  edit_profile: boolean;
  change_role: boolean;
  change_enabled: boolean;
  reset_password: boolean;
  logout: boolean;
}

export interface UserDetail {
  user: UserView;
  sessions: SessionSummary;
  allowed_actions: UserActionFlags;
  action_reasons: Record<string, string>;
}

export interface UserPage {
  items: UserView[];
  page: number;
  page_size: number;
  total: number;
}

export interface UserFilters {
  search?: string;
  role?: UserRole;
  status?: UserStatusFilter;
  page?: number;
  pageSize?: number;
}

export interface CreateUserPayload {
  username: string;
  display_name: string;
  email: string;
  role: UserRole;
}

export interface UpdateUserPayload {
  display_name?: string;
  email?: string;
  role?: UserRole;
  enabled?: boolean;
}

export interface CreatedUserResponse {
  user: UserView;
  temporary_password: string;
}

export type ResetPasswordResponse = CreatedUserResponse;

export async function listUsers(filters: UserFilters = {}): Promise<UserPage> {
  const query = new URLSearchParams();
  if (filters.search) query.set("search", filters.search);
  if (filters.role) query.set("role", filters.role);
  if (filters.status) query.set("status", filters.status);
  query.set("page", String(filters.page ?? 1));
  query.set("page_size", String(filters.pageSize ?? 20));
  return apiRequest<UserPage>(`/api/v1/admin/users?${query.toString()}`);
}

export async function getUser(userId: string): Promise<UserDetail> {
  return apiRequest<UserDetail>(`/api/v1/admin/users/${encodeURIComponent(userId)}`);
}

export async function createUser(payload: CreateUserPayload): Promise<CreatedUserResponse> {
  return apiRequest<CreatedUserResponse>("/api/v1/admin/users", {
    method: "POST",
    body: jsonBody(payload),
  });
}

export async function updateUser(
  userId: string,
  payload: UpdateUserPayload,
): Promise<UserDetail> {
  return apiRequest<UserDetail>(`/api/v1/admin/users/${encodeURIComponent(userId)}`, {
    method: "PATCH",
    body: jsonBody(payload),
  });
}

export async function resetUserPassword(userId: string): Promise<ResetPasswordResponse> {
  return apiRequest<ResetPasswordResponse>(
    `/api/v1/admin/users/${encodeURIComponent(userId)}/reset-password`,
    { method: "POST", body: jsonBody({}) },
  );
}

export async function logoutUser(userId: string): Promise<UserDetail> {
  return apiRequest<UserDetail>(
    `/api/v1/admin/users/${encodeURIComponent(userId)}/logout`,
    { method: "POST", body: jsonBody({}) },
  );
}

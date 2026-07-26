export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    request_id: string;
    details?: Record<string, unknown>;
  };
}

export class ApiClientError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
    public readonly requestId: string,
    public readonly details?: Record<string, unknown>,
  ) {
    super(message);
    this.name = "ApiClientError";
  }
}

function requestId(): string {
  return globalThis.crypto.randomUUID();
}

function isErrorBody(value: unknown): value is ApiErrorBody {
  if (typeof value !== "object" || value === null || !("error" in value)) return false;
  const error = value.error;
  return (
    typeof error === "object" &&
    error !== null &&
    "code" in error &&
    typeof error.code === "string" &&
    "message" in error &&
    typeof error.message === "string" &&
    "request_id" in error &&
    typeof error.request_id === "string"
  );
}

async function apiResponse(
  path: string,
  init: RequestInit,
  accept: string,
): Promise<Response> {
  if (!path.startsWith("/api/")) {
    throw new TypeError("API requests must use a same-origin /api/ path");
  }
  const headers = new Headers(init.headers);
  headers.set("Accept", accept);
  headers.set("X-Request-ID", requestId());
  if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(path, {
    ...init,
    headers,
    credentials: "same-origin",
  });
  if (!response.ok) {
    const isJson = response.headers.get("Content-Type")?.includes("application/json") ?? false;
    const payload: unknown = isJson ? await response.json() : undefined;
    if (isErrorBody(payload)) {
      throw new ApiClientError(
        response.status,
        payload.error.code,
        payload.error.message,
        payload.error.request_id,
        payload.error.details,
      );
    }
    throw new ApiClientError(
      response.status,
      "unexpected_response",
      "服务器返回了无法识别的响应",
      response.headers.get("X-Request-ID") ?? "unknown",
    );
  }
  return response;
}

export async function apiRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await apiResponse(path, init, "application/json");
  const isJson = response.headers.get("Content-Type")?.includes("application/json") ?? false;
  const payload: unknown = isJson ? await response.json() : undefined;
  return payload as T;
}

export async function apiTextRequest(
  path: string,
  init: RequestInit = {},
): Promise<string> {
  const response = await apiResponse(
    path,
    init,
    "text/plain, text/markdown, application/json, application/x-ndjson",
  );
  return response.text();
}

export function jsonBody(value: unknown): string {
  return JSON.stringify(value);
}

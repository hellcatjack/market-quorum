import { ApiClientError, apiRequest, apiTextRequest } from "./client";

test("uses same-origin credentials and never injects an access token", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify({ status: "ok" }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  );

  await apiRequest<{ status: string }>("/api/v1/health-probe");

  const [url, options] = fetchMock.mock.calls[0];
  expect(url).toBe("/api/v1/health-probe");
  expect(options?.credentials).toBe("same-origin");
  expect(new Headers(options?.headers).get("Authorization")).toBeNull();
  expect(new Headers(options?.headers).get("Accept")).toBe("application/json");
  expect(new Headers(options?.headers).get("X-Request-ID")).toMatch(/^[0-9a-f-]{36}$/);
});

test("parses the shared API error envelope", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(
      JSON.stringify({
        error: {
          code: "capacity_blocked",
          message: "当前容量不足",
          request_id: "request-42",
        },
      }),
      { status: 409, headers: { "Content-Type": "application/json" } },
    ),
  );

  await expect(apiRequest("/api/v1/probe")).rejects.toEqual(
    new ApiClientError(409, "capacity_blocked", "当前容量不足", "request-42"),
  );
});

test("reads artifact text with the same authenticated request boundary", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response("# 完整报告\n全部正文", {
      status: 200,
      headers: { "Content-Type": "text/markdown; charset=utf-8" },
    }),
  );

  await expect(apiTextRequest("/api/v1/artifacts/artifact-1")).resolves.toBe(
    "# 完整报告\n全部正文",
  );

  const [url, options] = fetchMock.mock.calls[0];
  expect(url).toBe("/api/v1/artifacts/artifact-1");
  expect(options?.credentials).toBe("same-origin");
  expect(new Headers(options?.headers).get("Authorization")).toBeNull();
  expect(new Headers(options?.headers).get("Accept")).toContain("text/plain");
  expect(new Headers(options?.headers).get("X-Request-ID")).toMatch(/^[0-9a-f-]{36}$/);
});

test("keeps structured API errors for text requests", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(
      JSON.stringify({
        error: {
          code: "artifact_integrity_error",
          message: "产物完整性校验失败",
          request_id: "request-artifact",
        },
      }),
      { status: 409, headers: { "Content-Type": "application/json" } },
    ),
  );

  await expect(apiTextRequest("/api/v1/artifacts/artifact-1")).rejects.toEqual(
    new ApiClientError(
      409,
      "artifact_integrity_error",
      "产物完整性校验失败",
      "request-artifact",
    ),
  );
});

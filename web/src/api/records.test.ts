import { ApiClientError } from "./client";
import { deleteRun } from "./records";

function errorResponse(status: number, code: string) {
  return new Response(
    JSON.stringify({
      error: {
        code,
        message: "request failed",
        request_id: "request-delete",
      },
    }),
    { status, headers: { "Content-Type": "application/json" } },
  );
}

test("treats an already deleted assessment as a successful delete", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    errorResponse(404, "assessment_not_found"),
  );

  await expect(deleteRun("run-delete")).resolves.toBeUndefined();
});

test("preserves non-not-found delete failures", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    errorResponse(409, "delete_not_allowed"),
  );

  await expect(deleteRun("run-delete")).rejects.toEqual(
    new ApiClientError(409, "delete_not_allowed", "request failed", "request-delete"),
  );
});

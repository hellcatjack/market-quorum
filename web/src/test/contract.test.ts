import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

interface ApiOperation {
  operationId?: string;
  requestBody?: { content?: { "application/json"?: { schema?: { $ref?: string } } } };
  responses?: Record<string, { content?: { "application/json"?: { schema?: { $ref?: string } } } }>;
}

interface OpenApiDocument {
  paths: Record<string, Record<string, ApiOperation>>;
  components: { schemas: Record<string, { properties?: Record<string, unknown> }> };
}

const webRoot = resolve(process.cwd());
const projectRoot = resolve(webRoot, "..");

function exportOpenApi(): OpenApiDocument {
  execFileSync(resolve(projectRoot, ".venv/bin/python"), ["scripts/export_openapi.py"], {
    cwd: projectRoot,
    stdio: "pipe",
  });
  return JSON.parse(readFileSync(resolve(projectRoot, "var/openapi.json"), "utf8")) as OpenApiDocument;
}

function responseRef(operation: ApiOperation, status: string): string | undefined {
  return operation.responses?.[status]?.content?.["application/json"]?.schema?.$ref;
}

test("generated API declarations match the current OpenAPI schema", () => {
  exportOpenApi();
  const temporary = mkdtempSync(join(tmpdir(), "tradingng-contract-"));
  const generated = join(temporary, "schema.d.ts");
  try {
    execFileSync(
      resolve(webRoot, "node_modules/.bin/openapi-typescript"),
      [resolve(projectRoot, "var/openapi.json"), "-o", generated],
      { cwd: projectRoot, stdio: "pipe" },
    );
    expect(readFileSync(generated, "utf8")).toBe(
      readFileSync(resolve(webRoot, "src/api/schema.d.ts"), "utf8"),
    );
  } finally {
    rmSync(temporary, { recursive: true, force: true });
  }
});

test("publishes the complete user administration and admission contracts", () => {
  const schema = exportOpenApi();
  const collection = schema.paths["/api/v1/admin/users"];
  const member = schema.paths["/api/v1/admin/users/{user_id}"];
  const reset = schema.paths["/api/v1/admin/users/{user_id}/reset-password"].post;
  const logout = schema.paths["/api/v1/admin/users/{user_id}/logout"].post;
  const admission = schema.paths["/api/v1/assessments/admission-summary"].get;

  expect(collection.get.operationId).toBe("list_admin_users");
  expect(collection.post.operationId).toBe("create_admin_user");
  expect(member.get.operationId).toBe("get_admin_user");
  expect(member.patch.operationId).toBe("update_admin_user");
  expect(reset.operationId).toBe("reset_admin_user_password");
  expect(logout.operationId).toBe("logout_admin_user");
  expect(admission.operationId).toBe("get_assessment_admission_summary");
  expect(collection.delete).toBeUndefined();
  expect(member.delete).toBeUndefined();

  expect(responseRef(collection.get, "200")).toBe("#/components/schemas/UserPage");
  expect(responseRef(collection.post, "201")).toBe("#/components/schemas/TemporaryPasswordResponse");
  expect(responseRef(member.get, "200")).toBe("#/components/schemas/UserDetailView");
  expect(responseRef(member.patch, "200")).toBe("#/components/schemas/UserDetailView");
  expect(responseRef(reset, "200")).toBe("#/components/schemas/TemporaryPasswordResponse");
  expect(responseRef(logout, "200")).toBe("#/components/schemas/UserDetailView");
  expect(responseRef(admission, "200")).toBe("#/components/schemas/AdmissionSummaryView");
  expect(collection.post.requestBody?.content?.["application/json"]?.schema?.$ref).toBe(
    "#/components/schemas/CreateUserCommand",
  );
  expect(member.patch.requestBody?.content?.["application/json"]?.schema?.$ref).toBe(
    "#/components/schemas/UpdateUserCommand",
  );
});

test("limits temporary passwords to create and reset responses", () => {
  const schema = exportOpenApi();
  expect(schema.components.schemas.TemporaryPasswordResponse.properties).toHaveProperty(
    "temporary_password",
  );
  for (const name of ["UserView", "UserPage", "UserDetailView", "UserActionFlags"]) {
    expect(JSON.stringify(schema.components.schemas[name])).not.toContain("temporary_password");
  }
  expect(JSON.stringify(schema.paths["/api/v1/me"])).not.toContain("temporary_password");

  const operationsReturningSecret: string[] = [];
  for (const path of Object.values(schema.paths)) {
    for (const operation of Object.values(path)) {
      if (Object.values(operation.responses ?? {}).some(
        (response) => response.content?.["application/json"]?.schema?.$ref
          === "#/components/schemas/TemporaryPasswordResponse",
      )) {
        operationsReturningSecret.push(operation.operationId ?? "missing-operation-id");
      }
    }
  }
  expect(operationsReturningSecret.sort()).toEqual([
    "create_admin_user",
    "reset_admin_user_password",
  ]);
});

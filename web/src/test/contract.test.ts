import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

test("generated API declarations match the current OpenAPI schema", () => {
  const webRoot = resolve(process.cwd());
  const projectRoot = resolve(webRoot, "..");
  const temporary = mkdtempSync(join(tmpdir(), "tradingng-contract-"));
  const generated = join(temporary, "schema.d.ts");
  try {
    execFileSync(resolve(projectRoot, ".venv/bin/python"), ["scripts/export_openapi.py"], {
      cwd: projectRoot,
      stdio: "pipe",
    });
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

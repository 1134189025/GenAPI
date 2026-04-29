import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const root = join(import.meta.dir, "..");

function source(path: string) {
  return readFileSync(join(root, path), "utf8");
}

describe("frontend backend API contracts", () => {
  test("does not call the removed standalone proxy settings endpoints", () => {
    const api = source("src/lib/api.ts");

    expect(api).not.toContain('"/api/proxy"');
    expect(api).toContain('"/api/proxy/test"');
  });

  test("exposes admin update center API contracts", () => {
    const api = source("src/lib/api.ts");

    expect(api).toContain("UpdateStatus");
    expect(api).toContain("UpdateJob");
    expect(api).toContain("fetchUpdateStatus");
    expect(api).toContain("startSystemUpdate");
    expect(api).toContain("fetchUpdateJob");
    expect(api).toContain('"/api/admin/update/status"');
    expect(api).toContain('"/api/admin/update/start"');
    expect(api).toContain('`/api/admin/update/jobs/${jobId}`');
  });
});

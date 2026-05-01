import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { resolveStaticRequest } from "../scripts/serve-static.mjs";

let tempRoot = "";
let outDir = "";

beforeEach(() => {
  tempRoot = mkdtempSync(join(tmpdir(), "genapi-static-"));
  outDir = join(tempRoot, "out");
  mkdirSync(outDir, { recursive: true });
  mkdirSync(join(outDir, "assets"), { recursive: true });
  mkdirSync(join(outDir, "_next", "static", "chunks"), { recursive: true });
  writeFileSync(join(outDir, "index.html"), "<html></html>");
  writeFileSync(join(outDir, "assets", "app.js"), "console.log('ok');");
  writeFileSync(join(outDir, "_next", "static", "chunks", "current.js"), "console.log('current');");
  writeFileSync(join(tempRoot, "secret.txt"), "secret");
});

afterEach(() => {
  rmSync(tempRoot, { recursive: true, force: true });
});

describe("static export server request resolution", () => {
  test("serves existing files and falls back extensionless app routes to index", () => {
    expect(resolveStaticRequest("/assets/app.js", outDir)).toMatchObject({
      status: 200,
      filePath: join(outDir, "assets", "app.js"),
    });
    expect(resolveStaticRequest("/accounts?filter=active", outDir)).toMatchObject({
      status: 200,
      filePath: join(outDir, "index.html"),
      fallback: true,
    });
  });

  test("does not fall back API paths or missing extension assets to index", () => {
    expect(resolveStaticRequest("/api", outDir)).toMatchObject({ status: 404 });
    expect(resolveStaticRequest("/api/version/images", outDir)).toMatchObject({ status: 404 });
    expect(resolveStaticRequest("/v1", outDir)).toMatchObject({ status: 404 });
    expect(resolveStaticRequest("/v1/models", outDir)).toMatchObject({ status: 404 });
    expect(resolveStaticRequest("/auth/login", outDir)).toMatchObject({ status: 404 });
    expect(resolveStaticRequest("/assets/missing.js", outDir)).toMatchObject({ status: 404 });
    expect(resolveStaticRequest("/assets/missing.css", outDir)).toMatchObject({ status: 404 });
    expect(resolveStaticRequest("/missing.png", outDir)).toMatchObject({ status: 404 });
    expect(resolveStaticRequest("/favicon.ico", outDir)).toMatchObject({ status: 404 });
  });

  test("returns a reload shim for stale Next.js chunk requests after deploys", () => {
    expect(resolveStaticRequest("/_next/static/chunks/current.js", outDir)).toMatchObject({
      status: 200,
      filePath: join(outDir, "_next", "static", "chunks", "current.js"),
    });
    expect(resolveStaticRequest("/_next/static/chunks/missing-old.js", outDir)).toMatchObject({
      status: 200,
      staleNextScript: true,
      body: expect.stringContaining("location.reload"),
      headers: expect.objectContaining({
        "Cache-Control": "no-store",
        "Content-Type": "text/javascript; charset=utf-8",
      }),
    });
    expect(resolveStaticRequest("/_next/static/chunks/missing-old.css", outDir)).toMatchObject({ status: 404 });
  });

  test("rejects malformed encoded URLs without throwing", () => {
    expect(resolveStaticRequest("/%E0%A4%A", outDir)).toMatchObject({ status: 400 });
  });

  test("blocks encoded path traversal outside the export directory", () => {
    expect(resolveStaticRequest("/..%2Fsecret.txt", outDir)).toMatchObject({ status: 403 });
    expect(resolveStaticRequest("/%2e%2e/secret.txt", outDir)).toMatchObject({ status: 403 });
    expect(resolveStaticRequest("http://static.local/%2e%2e/secret.txt", outDir)).toMatchObject({ status: 403 });
  });
});

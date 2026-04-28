import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { getNavigationGroups, getPageMeta, isPublicAppRoute, normalizeDashboardPath } from "../src/components/layout/nav-config";

const root = join(import.meta.dir, "..");

function source(path: string) {
  return readFileSync(join(root, path), "utf8");
}

describe("dashboard shell navigation", () => {
  test("uses admin canonical routes for management pages", () => {
    const adminRoutes = getNavigationGroups("admin").flatMap((group) => group.items.map((item) => item.href));

    expect(adminRoutes).toContain("/admin/accounts");
    expect(adminRoutes).toContain("/admin/images");
    expect(adminRoutes).toContain("/admin/logs");
    expect(adminRoutes).toContain("/admin/settings");
  });

  test("keeps user navigation limited to image and redeem pages", () => {
    const userRoutes = getNavigationGroups("user").flatMap((group) => group.items.map((item) => item.href));

    expect(userRoutes).toEqual(["/image", "/redeem"]);
  });

  test("resolves page metadata for canonical and legacy routes", () => {
    expect(getPageMeta("/admin/accounts").title).toBe("账号池");
    expect(getPageMeta("/accounts").title).toBe("账号池");
    expect(getPageMeta("/admin/settings").description).toContain("SMTP");
  });

  test("canonicalizes legacy admin routes for active sidebar state", () => {
    expect(normalizeDashboardPath("/accounts")).toBe("/admin/accounts");
    expect(normalizeDashboardPath("/image-manager")).toBe("/admin/images");
    expect(normalizeDashboardPath("/logs")).toBe("/admin/logs");
    expect(normalizeDashboardPath("/settings")).toBe("/admin/settings");
  });

  test("marks only unauthenticated pages as public shell routes", () => {
    expect(isPublicAppRoute("/login")).toBe(true);
    expect(isPublicAppRoute("/register")).toBe(true);
    expect(isPublicAppRoute("/setup")).toBe(true);
    expect(isPublicAppRoute("/image")).toBe(false);
  });

  test("revokes the server JWT before clearing the local session on logout", () => {
    const shell = source("src/components/layout/app-shell.tsx");
    const api = source("src/lib/api.ts");
    const handleLogout = shell.slice(shell.indexOf("const handleLogout = async () => {"));

    expect(api).toContain('"/api/auth/logout"');
    expect(handleLogout).toContain("await logout");
    expect(handleLogout.indexOf("await logout")).toBeGreaterThanOrEqual(0);
    expect(handleLogout.indexOf("await logout")).toBeLessThan(handleLogout.indexOf("await clearStoredAuthSession"));
  });
});

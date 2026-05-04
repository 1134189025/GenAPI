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

    expect(adminRoutes).toContain("/membership");
    expect(adminRoutes).toContain("/admin/membership-plans");
    expect(adminRoutes).toContain("/admin/accounts");
    expect(adminRoutes).toContain("/admin/images");
    expect(adminRoutes).toContain("/admin/logs");
    expect(adminRoutes).toContain("/admin/settings");
  });

  test("keeps user navigation focused on image membership and redeem pages", () => {
    const userRoutes = getNavigationGroups("user").flatMap((group) => group.items.map((item) => item.href));

    expect(userRoutes).toEqual(["/image", "/gallery", "/membership", "/redeem"]);
  });

  test("resolves page metadata for canonical and legacy routes", () => {
    expect(getPageMeta("/membership").title).toBe("会员中心");
    expect(getPageMeta("/gallery").title).toBe("图库");
    expect(getPageMeta("/admin/membership-plans").description).toContain("会员套餐");
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
    expect(isPublicAppRoute("/gallery")).toBe(false);
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

  test("keeps account summary fetching out of the authenticated shell header", () => {
    const shell = source("src/components/layout/app-shell.tsx");
    const authenticatedShell = shell.slice(shell.indexOf("const normalizedPath = normalizeDashboardPath(pathname)"));

    expect(shell).not.toContain("./header-user-badge");
    expect(authenticatedShell).not.toContain("HeaderUserBadge");
  });
});

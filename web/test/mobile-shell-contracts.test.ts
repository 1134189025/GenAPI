import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { getNavigationGroups } from "../src/components/layout/nav-config";

const root = join(import.meta.dir, "..");

function source(path: string) {
  return readFileSync(join(root, path), "utf8");
}

describe("mobile authenticated app shell", () => {
  test("keeps mobile user bottom navigation tied to the user route contract", () => {
    const shell = source("src/components/layout/app-shell.tsx");
    const mobileNavStart = shell.indexOf("function MobileUserBottomNavigation");
    const mobileNavEnd = shell.indexOf("export function AppShell");
    const mobileNav = shell.slice(mobileNavStart, mobileNavEnd);
    const userRoutes = getNavigationGroups("user").flatMap((group) => group.items.map((item) => item.href));

    expect(userRoutes).toEqual(["/image", "/gallery", "/membership", "/redeem"]);
    expect(mobileNavStart).toBeGreaterThanOrEqual(0);
    expect(mobileNav).toContain('aria-label="移动端用户导航"');
    expect(mobileNav).toContain('getNavigationGroups("user")');
    expect(mobileNav).toContain("href={item.href}");
    expect(mobileNav).toContain("key={item.href}");
    expect(mobileNav).toContain("aria-current={active ? \"page\" : undefined}");
    expect(mobileNav).toContain("normalizedPath === item.href");
  });

  test("shows the bottom navigation only on authenticated mobile and pads content above it", () => {
    const shell = source("src/components/layout/app-shell.tsx");
    const mobileNav = shell.slice(shell.indexOf("function MobileUserBottomNavigation"), shell.indexOf("export function AppShell"));
    const authenticatedShell = shell.slice(shell.indexOf("if (isPublic || !session)"), shell.lastIndexOf("</main>"));

    expect(authenticatedShell).toContain("overflow-x-hidden");
    expect(authenticatedShell).toContain('const normalizedPath = normalizeDashboardPath(pathname)');
    expect(authenticatedShell).toContain('const isImageWorkspace = normalizedPath === "/image"');
    expect(authenticatedShell).toContain("pb-[calc(4.5rem+env(safe-area-inset-bottom))]");
    expect(authenticatedShell).toContain('isImageWorkspace ? "pt-3 pb-0 lg:pt-5 lg:pb-5"');
    expect(authenticatedShell).toContain("lg:pb-5");
    expect(authenticatedShell).toContain("session.role === \"user\"");
    expect(mobileNav).toContain("fixed inset-x-0 bottom-0");
    expect(mobileNav).toContain("lg:hidden");
    expect(mobileNav).toContain("env(safe-area-inset-bottom)");
    expect(mobileNav).toContain("pt-1.5");
    expect(mobileNav).toContain("text-[10px]");
    expect(mobileNav).toContain("size-7");
    expect(mobileNav).not.toContain("text-[11px]");
    expect(mobileNav).not.toContain("size-8");
  });
});

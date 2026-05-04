import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { getNavigationGroups } from "../src/components/layout/nav-config";

const root = join(import.meta.dir, "..");

function source(path: string) {
  return readFileSync(join(root, path), "utf8");
}

function buttonContainingIcon(sourceText: string, iconName: string) {
  const iconStart = sourceText.indexOf(`<${iconName}`);
  expect(iconStart).toBeGreaterThanOrEqual(0);
  const buttonStart = sourceText.lastIndexOf("<button", iconStart);
  const buttonEnd = sourceText.indexOf("</button>", iconStart);
  expect(buttonStart).toBeGreaterThanOrEqual(0);
  expect(buttonEnd).toBeGreaterThan(buttonStart);
  return sourceText.slice(buttonStart, buttonEnd);
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

  test("shows a visible fixed-height bottom navigation only for mobile users", () => {
    const shell = source("src/components/layout/app-shell.tsx");
    const mobileNav = shell.slice(shell.indexOf("function MobileUserBottomNavigation"), shell.indexOf("export function AppShell"));
    const authenticatedShell = shell.slice(shell.indexOf("if (isPublic || !session)"), shell.lastIndexOf("</main>"));

    expect(authenticatedShell).toContain('const normalizedPath = normalizeDashboardPath(pathname)');
    expect(authenticatedShell).toContain('const isImageWorkspace = normalizedPath === "/image"');
    expect(authenticatedShell).not.toContain("pb-[calc(4.5rem+env(safe-area-inset-bottom))]");
    expect(authenticatedShell).toContain('session.role === "user"');
    expect(authenticatedShell).toContain("pb-[calc(3.75rem+env(safe-area-inset-bottom))]");
    expect(authenticatedShell).toContain("lg:pb-8");
    expect(authenticatedShell).toContain('session.role === "admin" && mobileOpen');
    expect(authenticatedShell).toContain('session.role === "user" ? <MobileUserBottomNavigation pathname={pathname} /> : null');
    expect(mobileNav).toContain("fixed inset-x-0 bottom-0");
    expect(mobileNav).toContain("h-[calc(3.5rem+env(safe-area-inset-bottom))]");
    expect(mobileNav).toContain("pb-[calc(0.35rem+env(safe-area-inset-bottom))]");
    expect(mobileNav).toContain("z-50");
    expect(mobileNav).toContain("bg-white");
    expect(mobileNav).not.toContain("z-40");
    expect(mobileNav).not.toContain("bg-white/92");
    expect(mobileNav).not.toContain("pb-[calc(0.75rem+env(safe-area-inset-bottom))]");
    expect(mobileNav).toContain("lg:hidden");
    expect(mobileNav).toContain("env(safe-area-inset-bottom)");
    expect(mobileNav).toContain("pt-1.5");
    expect(mobileNav).toContain("text-[10px]");
    expect(mobileNav).toContain("size-7");
    expect(mobileNav).not.toContain("text-[11px]");
    expect(mobileNav).not.toContain("size-8");
  });

  test("labels icon-only shell buttons for mobile assistive tech", () => {
    const shell = source("src/components/layout/app-shell.tsx");
    const authenticatedShell = shell.slice(shell.indexOf("if (isPublic || !session)"), shell.lastIndexOf("</main>"));

    expect(buttonContainingIcon(authenticatedShell, "Menu")).toContain("aria-label=");
    expect(buttonContainingIcon(authenticatedShell, "X")).toContain("aria-label=");
    expect(buttonContainingIcon(authenticatedShell, "LogOut")).toContain("aria-label=");
    expect(buttonContainingIcon(authenticatedShell, "ChevronLeft")).toContain("aria-label=");
  });
});

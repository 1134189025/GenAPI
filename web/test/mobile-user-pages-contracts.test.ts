import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const root = join(import.meta.dir, "..");

function source(path: string) {
  return readFileSync(join(root, path), "utf8");
}

describe("mobile user page layout contracts", () => {
  test("gallery uses larger mobile touch targets and confirms destructive deletes", () => {
    const galleryPage = source("src/app/gallery/page.tsx");

    expect(galleryPage).toContain("pendingDeleteItem");
    expect(galleryPage).toContain("确认删除图片");
    expect(galleryPage).toContain("setPendingDeleteItem(item)");
    expect(galleryPage).toContain("grid gap-2 sm:grid-cols-3");
    expect(galleryPage).toContain("h-11 rounded-xl sm:h-9");
    expect(galleryPage).not.toContain("grid grid-cols-3 gap-2");
    expect(galleryPage).not.toContain("onClick={() => void handleDelete(item)}");
  });

  test("redeem page puts the code input before account statistics on mobile", () => {
    const redeemPage = source("src/app/redeem/page.tsx");

    expect(redeemPage.indexOf("Redeem Form")).toBeGreaterThan(-1);
    expect(redeemPage.indexOf("RedeemStatCard")).toBeGreaterThan(-1);
    expect(redeemPage.indexOf("Redeem Form")).toBeLessThan(redeemPage.indexOf("RedeemStatCard"));
    expect(redeemPage).toContain("grid gap-2 sm:grid-cols-2 xl:grid-cols-4");
    expect(redeemPage).toContain("space-y-2 p-4 sm:space-y-3 sm:p-6");
    expect(redeemPage).toContain("rounded-[20px] border border-slate-200/70 bg-white/90 p-3 shadow-sm sm:rounded-[24px] sm:p-4");
  });

  test("membership page avoids hard three-column mobile grids and preserves long values", () => {
    const membershipPage = source("src/app/membership/page.tsx");
    const statCard = source("src/components/common/stat-card.tsx");

    expect(membershipPage).not.toContain("grid grid-cols-3 gap-2 text-center");
    expect(membershipPage).toContain("grid gap-2 text-xs font-semibold text-slate-500 sm:grid-cols-3");
    expect(membershipPage).toContain("break-words");
    expect(membershipPage).toContain('className="min-w-0"');
    expect(membershipPage).toContain("break-words text-lg font-black text-slate-950");
    expect(membershipPage).toContain("break-words text-sm leading-6 text-slate-500");
    expect(statCard).toContain("break-words text-2xl");
    expect(statCard).not.toContain("truncate text-2xl");
  });

  test("auth shell and dialogs use mobile browser viewport and scroll-safe defaults", () => {
    const authCard = source("src/components/auth/auth-card.tsx");
    const dialog = source("src/components/ui/dialog.tsx");

    expect(authCard).toContain("min-h-dvh");
    expect(authCard).toContain("overscroll-contain");
    expect(authCard).toContain("pt-[max(1.5rem,env(safe-area-inset-top))]");
    expect(authCard).toContain("pb-[max(1.5rem,env(safe-area-inset-bottom))]");

    expect(dialog).toContain("max-h-[calc(100dvh_-_2rem_-_env(safe-area-inset-top)_-_env(safe-area-inset-bottom))]");
    expect(dialog).toContain("overflow-y-auto");
    expect(dialog).toContain("overscroll-contain");
    expect(dialog).toContain("scroll-pb-[env(safe-area-inset-bottom)]");
  });
});

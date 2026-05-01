import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const root = join(import.meta.dir, "..");

function source(path: string) {
  return readFileSync(join(root, path), "utf8");
}

describe("mobile image page contracts", () => {
  test("uses a dynamic viewport chat shell with safe-area composer spacing", () => {
    const page = source("src/app/image/page.tsx");
    const composer = source("src/app/image/components/image-composer.tsx");

    expect(page).not.toContain('className="relative mx-auto flex h-[calc(100vh-5rem)]');
    expect(page).toContain("h-[calc(100dvh-4.75rem)]");
    expect(page).toContain("sm:h-[calc(100vh-5rem)]");
    expect(page).toContain("overflow-hidden");
    expect(composer).toContain("pb-[calc(5.5rem+env(safe-area-inset-bottom))]");
    expect(composer).toContain("lg:pb-2");
  });

  test("keeps mobile prompt and send primary while secondary controls can wrap or collapse", () => {
    const composer = source("src/app/image/components/image-composer.tsx");

    expect(composer).toContain("min-h-[76px]");
    expect(composer).toContain("max-h-[32dvh]");
    expect(composer).toContain('isMoreSettingsOpen ? "flex" : "hidden sm:flex"');
    expect(composer).toContain("flex min-w-0 flex-1 flex-wrap");
    expect(composer).toContain("touch-manipulation");
  });

  test("keeps reference image previews from expanding the fixed mobile workspace", () => {
    const composer = source("src/app/image/components/image-composer.tsx");

    expect(composer).toContain("max-h-[5rem]");
    expect(composer).toContain("overflow-x-auto");
    expect(composer).toContain("overscroll-x-contain");
    expect(composer).toContain("sm:max-h-[9rem]");
    expect(composer).toContain("sm:overflow-y-auto");
    expect(composer).toContain("relative size-16 flex-none");
    expect(composer).toContain("absolute right-1 top-1");
    expect(composer).not.toContain("absolute -right-1 -top-1");
  });

  test("keeps the mobile size menu within the viewport above the bottom composer", () => {
    const composer = source("src/app/image/components/image-composer.tsx");

    expect(composer).toContain("right-0");
    expect(composer).toContain("w-[min(18rem,calc(100vw-2rem))]");
    expect(composer).toContain("max-h-[min(16rem,calc(100dvh-12rem))]");
    expect(composer).toContain("overflow-y-auto");
  });

  test("shows history delete controls on touch devices without hover", () => {
    const page = source("src/app/image/page.tsx");
    const sidebar = source("src/app/image/components/image-sidebar.tsx");

    expect(page).toContain("h-[min(82dvh,720px)]");
    expect(page).toContain("选择历史图片对话，或创建新的图片对话。");
    expect(page).toContain("pb-[calc(1rem+env(safe-area-inset-bottom))]");
    expect(page).toContain('aria-label="打开历史记录"');
    expect(page).toContain('aria-label="新建图片对话"');
    expect(sidebar).toContain("pr-12 sm:pr-8");
    expect(sidebar).toContain("size-9 sm:size-7");
    expect(sidebar).toContain("opacity-100 sm:opacity-0 sm:group-hover:opacity-100");
  });
});

import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const root = join(import.meta.dir, "..");

function source(path: string) {
  return readFileSync(join(root, path), "utf8");
}

describe("admin operations dashboard pages", () => {
  test("required auth settings are exposed in the settings UI", () => {
    const authSettings = source("src/app/settings/components/auth-settings-card.tsx");

    expect(authSettings).toContain("验证码有效期");
    expect(authSettings).toContain("最大尝试次数");
    expect(authSettings).toContain("SMTP TLS");
    expect(authSettings).toContain("verify_code_ttl_seconds");
    expect(authSettings).toContain("verify_max_attempts");
    expect(authSettings).toContain("smtp_tls");
    expect(authSettings).toContain("留空保留");
  });

  test("image cache guardrails are exposed in the settings UI", () => {
    const configCard = source("src/app/settings/components/config-card.tsx");
    const settingsStore = source("src/app/settings/store.ts");
    const api = source("src/lib/api.ts");

    expect(configCard).toContain("图片保留时间");
    expect(configCard).toContain("缓存大小上限");
    expect(configCard).toContain("自动删除图片缓存");
    expect(configCard).toContain("当前缓存");
    expect(settingsStore).toContain("setImageCacheMaxSizeMb");
    expect(settingsStore).toContain("setImageCacheAutoDeleteEnabled");
    expect(api).toContain("ImageCacheStatus");
    expect(api).toContain("image_cache_max_size_mb");
    expect(api).toContain("image_cache_auto_delete_enabled");
  });

  test("admin operation pages use shared dashboard primitives", () => {
    const pages = [
      "src/app/accounts/page.tsx",
      "src/app/image-manager/page.tsx",
      "src/app/logs/page.tsx",
      "src/app/settings/components/settings-header.tsx",
      "src/app/admin/register-machine/page.tsx",
    ];

    for (const path of pages) {
      const content = source(path);
      expect(content).toContain("@/components/common/page-header");
      expect(content).toContain("<PageHeader");
    }
  });

  test("data-heavy admin operation pages use shared panels and empty states", () => {
    const pages = [
      "src/app/accounts/page.tsx",
      "src/app/image-manager/page.tsx",
      "src/app/logs/page.tsx",
      "src/app/settings/components/cpa-pools-card.tsx",
      "src/app/settings/components/sub2api-connections.tsx",
    ];

    for (const path of pages) {
      const content = source(path);
      expect(content).toContain("@/components/common/data-panel");
      expect(content).toContain("<DataPanel");
      expect(content).toContain("@/components/common/empty-state");
      expect(content).toContain("<EmptyState");
    }
  });

  test("user and code admin pages defer API loading until the admin guard resolves", () => {
    const pages = [
      "src/app/admin/users/page.tsx",
      "src/app/admin/redeem-codes/page.tsx",
      "src/app/admin/promo-codes/page.tsx",
    ];

    for (const path of pages) {
      const content = source(path);
      expect(content).toContain('const canLoadAdminData = !isCheckingAuth && session?.role === "admin";');
      expect(content).toContain("if (!canLoadAdminData || didLoadRef.current) return;");
    }
  });
});

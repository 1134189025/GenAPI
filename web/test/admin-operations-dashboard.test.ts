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

  test("version update center is exposed in the settings UI", () => {
    const settingsPage = source("src/app/settings/page.tsx");
    const updateCard = source("src/app/settings/components/update-card.tsx");
    const api = source("src/lib/api.ts");

    expect(settingsPage).toContain("UpdateCard");
    expect(updateCard).toContain("版本更新中心");
    expect(updateCard).toContain("当前版本");
    expect(updateCard).toContain("最新版本");
    expect(updateCard).toContain("更新系统");
    expect(updateCard).toContain("回滚");
    expect(updateCard).toContain("重启服务");
    expect(updateCard).toContain("Release 说明");
    expect(updateCard).toContain("git pull && docker compose pull app && docker compose up -d app");
    expect(updateCard).toContain("同步最新 docker-compose.yml");
    expect(updateCard).toContain("git pull && restart service manually");
    expect(updateCard).toContain("容器更新会拉取最新镜像并重新创建容器");
    expect(updateCard).toContain("页面可能会短暂断开连接");
    expect(updateCard).toContain("checkSystemUpdates");
    expect(updateCard).toContain("performSystemUpdate");
    expect(updateCard).toContain("rollbackSystemUpdate");
    expect(updateCard).toContain("restartSystemService");
    expect(updateCard).not.toContain("GENAPI_ENABLE_WEB_UPDATER");
    expect(updateCard).not.toContain("Docker socket");
    expect(updateCard).not.toContain("Docker Socket");
    expect(updateCard).not.toContain("一键更新");
    expect(api).toContain("release_info");
    expect(api).toContain("deployment_mode");
    expect(api).toContain("checkSystemUpdates");
    expect(api).toContain("performSystemUpdate");
    expect(api).toContain("rollbackSystemUpdate");
    expect(api).toContain("restartSystemService");
  });

  test("membership API and dashboard pages expose required frontend contracts", () => {
    const api = source("src/lib/api.ts");
    const membershipPage = source("src/app/membership/page.tsx");
    const adminPlansPage = source("src/app/admin/membership-plans/page.tsx");
    const adminRedeemCodes = source("src/app/admin/redeem-codes/page.tsx");
    const redeemPage = source("src/app/redeem/page.tsx");
    const imagePage = source("src/app/image/page.tsx");
    const usersPage = source("src/app/admin/users/page.tsx");

    expect(api).toContain("MembershipPlan");
    expect(api).toContain("fetchMembershipPlans");
    expect(api).toContain('"/api/membership/plans"');
    expect(api).toContain("fetchUserMembership");
    expect(api).toContain('"/api/membership/me"');
    expect(api).toContain("membership_plan_id");
    expect(api).toContain("member_image_quota");
    expect(api).toContain("total_image_quota");

    expect(membershipPage).toContain("会员中心");
    expect(membershipPage).toContain("当前会员");
    expect(membershipPage).toContain("会员套餐");
    expect(membershipPage).toContain("兑换中心");

    expect(adminPlansPage).toContain("会员套餐管理");
    expect(adminPlansPage).toContain("周期 GGB");
    expect(adminPlansPage).toContain("排序");
    expect(adminPlansPage).toContain('const canLoadAdminData = !isCheckingAuth && session?.role === "admin";');

    expect(adminRedeemCodes).toContain("membership_plan_id");
    expect(adminRedeemCodes).toContain("会员兑换");
    expect(adminRedeemCodes).toContain("请选择会员套餐");
    expect(redeemPage).toContain("会员兑换码");
    expect(imagePage).toContain("member_image_quota");
    expect(usersPage).toContain("会员 GGB");
  });

  test("admin GGB UI keeps old payload fields and non-balance image limits distinct", () => {
    const usersPage = source("src/app/admin/users/page.tsx");
    const adminPlansPage = source("src/app/admin/membership-plans/page.tsx");
    const adminRedeemCodes = source("src/app/admin/redeem-codes/page.tsx");
    const promoCodesPage = source("src/app/admin/promo-codes/page.tsx");
    const redeemHelpers = source("src/app/admin/redeem-codes/components/redeem-code-helpers.ts");

    expect(usersPage).toContain("GGB 余额");
    expect(usersPage).toContain("会员 GGB");
    expect(usersPage).toContain("图片并发");
    expect(usersPage).toContain("image_quota:");
    expect(usersPage).toContain("image_concurrency:");

    expect(adminPlansPage).toContain("周期 GGB");
    expect(adminPlansPage).toContain("period_image_quota");

    expect(adminRedeemCodes).toContain("GGB 兑换码");
    expect(adminRedeemCodes).toContain("图片并发");
    expect(adminRedeemCodes).toContain("type: generateForm.type");
    expect(redeemHelpers).toContain('return "GGB"');

    expect(promoCodesPage).toContain("赠送 GGB");
    expect(promoCodesPage).toContain("image_quota:");
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
      "src/app/admin/membership-plans/page.tsx",
    ];

    for (const path of pages) {
      const content = source(path);
      expect(content).toContain('const canLoadAdminData = !isCheckingAuth && session?.role === "admin";');
      expect(content).toContain("if (!canLoadAdminData || didLoadRef.current) return;");
    }
  });
});

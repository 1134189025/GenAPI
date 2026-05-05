import { describe, expect, test } from "bun:test";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

const root = join(import.meta.dir, "..");

function source(path: string) {
  return readFileSync(join(root, path), "utf8");
}

describe("user GGB UI contracts", () => {
  test("centralizes dog coin display rules and the per-image cost constant", () => {
    const ggbPath = join(root, "src/lib/ggb.ts");

    expect(existsSync(ggbPath)).toBe(true);

    const ggb = readFileSync(ggbPath, "utf8");
    expect(ggb).toContain("export const IMAGE_COST_GGB = 5");
    expect(ggb).toContain('export const GGB_DISPLAY_NAME = "狗狗币"');
    expect(ggb).toContain("formatQuotaAsGgb");
    expect(ggb).toContain("formatImageCostGgb");
    expect(ggb).toContain("normalizeQuotaErrorMessage");
    expect(ggb).toContain("狗狗币余额不足");
  });

  test("image composer keeps count as images and shows dog coin cost", () => {
    const page = source("src/app/image/page.tsx");
    const composer = source("src/app/image/components/image-composer.tsx");
    const results = source("src/app/image/components/image-results.tsx");

    expect(composer).toContain("张数");
    expect(results).toContain("{turn.count} 张");
    expect(composer).toContain("预计消耗");
    expect(composer).toContain("formatImageCostGgb");
    expect(composer).toContain("availableQuotaLabel");
    expect(composer).toContain("estimatedUsageLabel");
    expect(composer).toContain("estimatedUsageValue");
    expect(page).toContain("每张图片消耗");
    expect(page).toContain("狗狗币");
    expect(page).toContain("membershipLevelLabel");
    expect(page).toContain('isAdmin ? "上游额度" : "狗狗币余额"');
    expect(page).toContain('isAdmin ? "预计生成" : "预计消耗"');
    expect(page).toContain("normalizeQuotaErrorMessage");
    expect(page).toContain('account.status === "正常"');
    expect(page).toContain("上游额度");
    expect(page).not.toContain("额度 {availableQuota}");
    expect(page).not.toContain("上游额度\" : \"GGB 余额\"}\n            activeTaskCount");
  });

  test("membership and redeem user pages display balances and rewards as dog coins", () => {
    const membership = source("src/app/membership/page.tsx");
    const redeem = source("src/app/redeem/page.tsx");
    const nav = source("src/components/layout/nav-config.ts");

    expect(membership).toContain("formatQuotaAsGgb");
    expect(membership).toContain("会员狗狗币");
    expect(membership).toContain("总狗狗币余额");
    expect(membership).toContain("今日奖励");
    expect(membership).toContain("每日签到领取普通狗狗币余额");
    expect(membership).toContain("奖励会发放到普通狗狗币余额");
    expect(membership).toContain("会员狗狗币按套餐周期刷新");
    expect(membership).toContain("狗狗币");
    expect(membership).not.toContain("张会员图片额度");
    expect(membership).not.toContain("普通图片额度");

    expect(redeem).toContain("formatQuotaAsGgb");
    expect(redeem).toContain("狗狗币余额");
    expect(redeem).toContain("狗狗币余额码");
    expect(redeem).toContain("邀请码仅用于注册流程");
    expect(redeem).toContain("兑换码会去除首尾空格后提交");
    expect(redeem).not.toContain("当前图片额度");

    expect(nav).toContain("狗狗币");
    expect(nav).toContain("会员状态、套餐和周期狗狗币");
    expect(nav).toContain("兑换狗狗币余额、并发和会员码");
    expect(nav).not.toContain("兑换图片额度、并发和会员码");
  });
});

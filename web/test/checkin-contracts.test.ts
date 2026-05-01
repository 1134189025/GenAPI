import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const root = join(import.meta.dir, "..");

function source(path: string) {
  return readFileSync(join(root, path), "utf8");
}

describe("daily checkin frontend contracts", () => {
  test("membership page exposes daily checkin actions and states", () => {
    const membershipPage = source("src/app/membership/page.tsx");

    expect(membershipPage).toContain("每日签到");
    expect(membershipPage).toContain("fetchCheckinStatus");
    expect(membershipPage).toContain("claimDailyCheckin");
    expect(membershipPage).toContain("今日已领取");
    expect(membershipPage).toContain("签到领取");
    expect(membershipPage).toContain("签到奖励暂未开启");
  });

  test("admin auth settings expose checkin configuration fields and labels", () => {
    const authSettings = source("src/app/settings/components/auth-settings-card.tsx");

    for (const field of [
      "checkin_enabled",
      "checkin_daily_image_quota",
      "checkin_streak_bonus_enabled",
      "checkin_streak_bonus_days",
      "checkin_streak_bonus_image_quota",
      "checkin_timezone",
    ]) {
      expect(authSettings).toContain(field);
    }

    expect(authSettings).toContain("签到");
    expect(authSettings).toContain("每日奖励");
    expect(authSettings).toContain("连续签到");
    expect(authSettings).toContain("签到时区");
  });
});

import { describe, expect, test } from "bun:test";

import {
  datetimeLocalToApiValue,
  getPromoCodeStatus,
  toDatetimeLocalValue,
} from "../src/app/admin/promo-codes/components/promo-code-helpers";
import {
  copyTextToClipboard,
  getRedeemCodeStatus,
  getRedeemCodeTypeLabel,
  getRedeemValueLabel,
} from "../src/app/admin/redeem-codes/components/redeem-code-helpers";
import {
  canDisableOrDeleteUser,
  coerceNonNegativeInteger,
  coercePositiveInteger,
} from "../src/app/admin/users/components/user-management-helpers";
import {
  buildAuthSettingsPayload,
  normalizeAuthSettingsForForm,
} from "../src/app/settings/components/auth-settings-helpers";
import type { AuthSettings } from "../src/lib/api";

type CheckinAuthSettings = AuthSettings & {
  checkin_enabled: boolean;
  checkin_daily_image_quota: number;
  checkin_streak_bonus_enabled: boolean;
  checkin_streak_bonus_days: number;
  checkin_streak_bonus_image_quota: number;
  checkin_timezone: string;
};

const baseAuthSettings: CheckinAuthSettings = {
  site_name: "Genapi",
  registration_enabled: true,
  email_verification_enabled: false,
  invitation_required: false,
  promo_codes_enabled: true,
  checkin_enabled: false,
  checkin_daily_image_quota: 0,
  checkin_streak_bonus_enabled: false,
  checkin_streak_bonus_days: 7,
  checkin_streak_bonus_image_quota: 0,
  checkin_timezone: "Asia/Shanghai",
  email_domain_whitelist: [],
  default_image_quota: 0,
  default_image_concurrency: 1,
  verify_code_ttl_seconds: 900,
  verify_send_cooldown_seconds: 60,
  verify_max_attempts: 5,
  smtp_host: "",
  smtp_port: 587,
  smtp_username: "",
  smtp_password: "",
  smtp_from: "",
  smtp_tls: true,
  has_smtp_password: false,
};

describe("admin dashboard helpers", () => {
  test("classifies promo code availability without relying on full code values", () => {
    const now = new Date("2026-04-28T12:00:00.000Z");

    expect(getPromoCodeStatus({ enabled: true, used_count: 1, max_uses: 5, expires_at: null }, now).key).toBe(
      "active",
    );
    expect(
      getPromoCodeStatus({ enabled: true, used_count: 0, max_uses: 5, expires_at: "2026-04-28T11:59:59.000Z" }, now)
        .key,
    ).toBe("expired");
    expect(getPromoCodeStatus({ enabled: true, used_count: 5, max_uses: 5, expires_at: null }, now).key).toBe(
      "exhausted",
    );
    expect(getPromoCodeStatus({ enabled: false, used_count: 0, max_uses: 5, expires_at: null }, now).key).toBe(
      "disabled",
    );
  });

  test("normalizes datetime-local values for API payloads and form inputs", () => {
    const localValue = "2026-04-28T20:30";

    expect(datetimeLocalToApiValue(localValue)).toBe(new Date(localValue).toISOString());
    expect(datetimeLocalToApiValue("")).toBeUndefined();
    expect(toDatetimeLocalValue(null)).toBe("");
    expect(toDatetimeLocalValue("not-a-date")).toBe("");
  });

  test("classifies redeem code status and display labels", () => {
    const now = new Date("2026-04-28T12:00:00.000Z");

    expect(getRedeemCodeStatus({ enabled: true, used: false, expires_at: null }, now).key).toBe("available");
    expect(getRedeemCodeStatus({ enabled: true, used: true, expires_at: null }, now).key).toBe("used");
    expect(getRedeemCodeStatus({ enabled: false, used: false, expires_at: null }, now).key).toBe("disabled");
    expect(getRedeemCodeStatus({ enabled: true, used: false, expires_at: "2026-04-28T11:00:00.000Z" }, now).key).toBe(
      "expired",
    );
    expect(getRedeemCodeTypeLabel("image_quota")).toBe("图片额度");
    expect(getRedeemCodeTypeLabel("membership")).toBe("会员兑换");
    expect(getRedeemValueLabel({ type: "invitation", value: 0 })).toBe("注册邀请");
    expect(getRedeemValueLabel({ type: "membership", value: 0, membership_plan_id: "plan-a" })).toBe("会员套餐");
    expect(getRedeemValueLabel({ type: "concurrency", value: 3 })).toBe("+3");
  });

  test("copies redeem codes through a textarea fallback when Clipboard API is blocked", async () => {
    const appended: unknown[] = [];
    let selectedText = "";
    const fakeTextarea = {
      value: "",
      setAttribute: () => undefined,
      style: {},
      focus: () => undefined,
      select() {
        selectedText = this.value;
      },
      setSelectionRange(start: number, end: number) {
        selectedText = this.value.slice(start, end);
      },
    };
    const fakeDocument = {
      body: {
        appendChild(node: unknown) {
          appended.push(node);
        },
        removeChild(node: unknown) {
          const index = appended.indexOf(node);
          if (index >= 0) appended.splice(index, 1);
        },
      },
      createElement(tagName: string) {
        expect(tagName).toBe("textarea");
        return fakeTextarea;
      },
      execCommand(command: string) {
        expect(command).toBe("copy");
        return selectedText === "code-a\ncode-b";
      },
    };

    const copied = await copyTextToClipboard("code-a\ncode-b", {
      clipboard: {
        writeText: async () => {
          throw new Error("not allowed");
        },
      },
      document: fakeDocument,
    });

    expect(copied).toBe(true);
    expect(appended).toHaveLength(0);
  });

  test("copies redeem codes with synchronous fallback first on insecure LAN pages", async () => {
    let clipboardCalled = false;
    let selectedText = "";
    const visibleTextarea = {
      value: "code-a\ncode-b",
      focus: () => undefined,
      select() {
        selectedText = this.value;
      },
      setSelectionRange(start: number, end: number) {
        selectedText = this.value.slice(start, end);
      },
    };
    const fakeDocument = {
      body: {
        appendChild: () => undefined,
        removeChild: () => undefined,
      },
      createElement() {
        throw new Error("visible source should be used instead of a hidden fallback");
      },
      execCommand(command: string) {
        expect(command).toBe("copy");
        return selectedText === "code-a\ncode-b";
      },
    };

    const copied = await copyTextToClipboard("code-a\ncode-b", {
      clipboard: {
        writeText: async () => {
          clipboardCalled = true;
        },
      },
      document: fakeDocument,
      isSecureContext: false,
      sourceElement: visibleTextarea,
    });

    expect(copied).toBe(true);
    expect(clipboardCalled).toBe(false);
  });

  test("does not report copied when redeem code text is empty", async () => {
    const copied = await copyTextToClipboard(" \n ", {
      clipboard: {
        writeText: async () => {
          throw new Error("should not copy blank text");
        },
      },
      document: null,
    });

    expect(copied).toBe(false);
  });

  test("keeps destructive user actions away from the current session user", () => {
    expect(canDisableOrDeleteUser("self", "self")).toBe(false);
    expect(canDisableOrDeleteUser("other", "self")).toBe(true);
    expect(coerceNonNegativeInteger("-2", 5)).toBe(0);
    expect(coerceNonNegativeInteger("abc", 5)).toBe(5);
    expect(coercePositiveInteger("0", 3)).toBe(1);
    expect(coercePositiveInteger("abc", 3)).toBe(3);
  });

  test("normalizes auth settings form values from legacy string payloads", () => {
    const settings = normalizeAuthSettingsForForm({
      ...baseAuthSettings,
      registration_enabled: "false",
      email_verification_enabled: "0",
      invitation_required: 0,
      promo_codes_enabled: "true",
      checkin_enabled: "1",
      checkin_daily_image_quota: "-8",
      checkin_streak_bonus_enabled: "true",
      checkin_streak_bonus_days: "0",
      checkin_streak_bonus_image_quota: "12",
      checkin_timezone: "",
      email_domain_whitelist: "Example.com, TEST.dev\n",
      default_image_quota: "12",
      default_image_concurrency: "3",
      verify_code_ttl_seconds: "",
      verify_send_cooldown_seconds: "45",
      verify_max_attempts: "0",
      smtp_port: "465",
      smtp_password: "server-secret",
      smtp_tls: "false",
    } as unknown as CheckinAuthSettings) as CheckinAuthSettings;

    expect(settings.registration_enabled).toBe(false);
    expect(settings.email_verification_enabled).toBe(false);
    expect(settings.invitation_required).toBe(false);
    expect(settings.promo_codes_enabled).toBe(true);
    expect(settings.checkin_enabled).toBe(true);
    expect(settings.checkin_daily_image_quota).toBe(0);
    expect(settings.checkin_streak_bonus_enabled).toBe(true);
    expect(settings.checkin_streak_bonus_days).toBe(1);
    expect(settings.checkin_streak_bonus_image_quota).toBe(12);
    expect(settings.checkin_timezone).toBe("Asia/Shanghai");
    expect(settings.email_domain_whitelist).toEqual(["example.com", "test.dev"]);
    expect(settings.default_image_quota).toBe(12);
    expect(settings.default_image_concurrency).toBe(3);
    expect(settings.verify_code_ttl_seconds).toBe(900);
    expect(settings.verify_send_cooldown_seconds).toBe(45);
    expect(settings.verify_max_attempts).toBe(5);
    expect(settings.smtp_port).toBe(465);
    expect(settings.smtp_password).toBe("");
    expect(settings.smtp_tls).toBe(false);
  });

  test("builds auth settings payload without clearing a saved SMTP password", () => {
    const emptyPasswordPayload = buildAuthSettingsPayload({
      ...baseAuthSettings,
      smtp_password: "   ",
      smtp_tls: false,
      smtp_port: 465,
      default_image_concurrency: 0,
      verify_max_attempts: 0,
      checkin_enabled: true,
      checkin_daily_image_quota: -2,
      checkin_streak_bonus_enabled: true,
      checkin_streak_bonus_days: 0,
      checkin_streak_bonus_image_quota: -5,
      checkin_timezone: "",
    }) as Partial<CheckinAuthSettings>;

    expect(emptyPasswordPayload).not.toHaveProperty("smtp_password");
    expect(emptyPasswordPayload.smtp_tls).toBe(false);
    expect(emptyPasswordPayload.smtp_port).toBe(465);
    expect(emptyPasswordPayload.default_image_concurrency).toBe(1);
    expect(emptyPasswordPayload.verify_max_attempts).toBe(5);
    expect(emptyPasswordPayload.checkin_enabled).toBe(true);
    expect(emptyPasswordPayload.checkin_daily_image_quota).toBe(0);
    expect(emptyPasswordPayload.checkin_streak_bonus_enabled).toBe(true);
    expect(emptyPasswordPayload.checkin_streak_bonus_days).toBe(1);
    expect(emptyPasswordPayload.checkin_streak_bonus_image_quota).toBe(0);
    expect(emptyPasswordPayload.checkin_timezone).toBe("Asia/Shanghai");

    const newPasswordPayload = buildAuthSettingsPayload({
      ...baseAuthSettings,
      smtp_password: "new-secret",
    });

    expect(newPasswordPayload.smtp_password).toBe("new-secret");
  });
});

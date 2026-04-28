import type { PromoCode } from "@/lib/api";

export type AdminBadgeTone = "success" | "secondary" | "warning" | "danger" | "info" | "violet";

export type PromoCodeStatusKey = "active" | "disabled" | "expired" | "exhausted";

export type PromoCodeStatus = {
  key: PromoCodeStatusKey;
  label: string;
  tone: AdminBadgeTone;
};

export function isPastDate(value: string | null | undefined, now = new Date()) {
  if (!value) return false;
  const date = new Date(value);
  return !Number.isNaN(date.getTime()) && date.getTime() < now.getTime();
}

export function getPromoCodeStatus(
  item: Pick<PromoCode, "enabled" | "used_count" | "max_uses" | "expires_at">,
  now = new Date(),
): PromoCodeStatus {
  if (!item.enabled) {
    return { key: "disabled", label: "禁用", tone: "secondary" };
  }
  if (isPastDate(item.expires_at, now)) {
    return { key: "expired", label: "已过期", tone: "danger" };
  }
  if (item.max_uses > 0 && item.used_count >= item.max_uses) {
    return { key: "exhausted", label: "已用尽", tone: "warning" };
  }
  return { key: "active", label: "可用", tone: "success" };
}

export function datetimeLocalToApiValue(value: string | null | undefined) {
  const text = String(value || "").trim();
  if (!text) return undefined;
  const date = new Date(text);
  return Number.isNaN(date.getTime()) ? text : date.toISOString();
}

export function toDatetimeLocalValue(value: string | null | undefined) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (num: number) => String(num).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(
    date.getMinutes(),
  )}`;
}

export function formatAdminDateTime(value: string | null | undefined) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

export function formatUsageLimit(item: Pick<PromoCode, "used_count" | "max_uses">) {
  return `${item.used_count}/${item.max_uses}`;
}

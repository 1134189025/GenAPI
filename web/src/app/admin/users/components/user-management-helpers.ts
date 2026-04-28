import type { ManagedUser } from "@/lib/api";

import type { AdminBadgeTone } from "../../promo-codes/components/promo-code-helpers";

export function canDisableOrDeleteUser(targetUserId: string, currentUserId: string) {
  return String(targetUserId || "").trim() !== String(currentUserId || "").trim();
}

export function coerceNonNegativeInteger(value: string | number, fallback = 0) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return fallback;
  return Math.max(0, Math.trunc(numeric));
}

export function coercePositiveInteger(value: string | number, fallback = 1) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return fallback;
  return Math.max(1, Math.trunc(numeric));
}

export function formatUserDateTime(value: string | null | undefined) {
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

export function getUserRoleLabel(role: ManagedUser["role"]) {
  return role === "admin" ? "管理员" : "普通用户";
}

export function getUserStatus(item: Pick<ManagedUser, "enabled">): {
  label: string;
  tone: AdminBadgeTone;
} {
  return item.enabled ? { label: "启用", tone: "success" } : { label: "禁用", tone: "secondary" };
}

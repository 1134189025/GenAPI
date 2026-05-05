export const IMAGE_COST_GGB = 5;
export const GGB_DISPLAY_NAME = "狗狗币";

function numericQuota(value: number | string | null | undefined) {
  const numeric = Number(value ?? 0);
  if (!Number.isFinite(numeric)) {
    return 0;
  }
  return Math.max(0, numeric);
}

export function quotaToGgb(value: number | string | null | undefined) {
  return numericQuota(value);
}

export function formatQuotaAsGgb(value: number | string | null | undefined) {
  return `${quotaToGgb(value)} ${GGB_DISPLAY_NAME}`;
}

export function formatImageCostGgb(imageCount: number | string | null | undefined) {
  const count = Math.max(1, Math.min(10, Math.trunc(Number(imageCount) || 1)));
  return `${count * IMAGE_COST_GGB} ${GGB_DISPLAY_NAME}`;
}

export function normalizeQuotaErrorMessage(value: unknown, fallback = "生成失败") {
  const message = value instanceof Error ? value.message : String(value || fallback);
  if (/额度不足|余额不足|可用额度|insufficient/i.test(message)) {
    return "狗狗币余额不足";
  }
  return message;
}

import type { AuthSettings } from "@/lib/api";

const booleanTrueValues = new Set(["1", "true", "yes", "on"]);
const booleanFalseValues = new Set(["0", "false", "no", "off"]);

function textValue(value: unknown) {
  return String(value ?? "").trim();
}

function booleanValue(value: unknown, fallback = false) {
  if (typeof value === "boolean") return value;
  if (typeof value === "number") return value !== 0;

  const normalized = textValue(value).toLowerCase();
  if (!normalized) return fallback;
  if (booleanTrueValues.has(normalized)) return true;
  if (booleanFalseValues.has(normalized)) return false;
  return fallback;
}

function integerValue(value: unknown, fallback: number, min = 0) {
  if (!textValue(value)) return fallback;
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return fallback;
  const integer = Math.trunc(numeric);
  return integer < min ? fallback : integer;
}

function domainWhitelistValue(value: unknown) {
  const items = Array.isArray(value) ? value : textValue(value).split(/[\n,]/);
  return items.map((item) => textValue(item).toLowerCase()).filter(Boolean);
}

export function normalizeAuthSettingsForForm(settings: AuthSettings): AuthSettings {
  const source = settings as Record<keyof AuthSettings, unknown>;

  return {
    site_name: textValue(source.site_name) || "Genapi",
    registration_enabled: booleanValue(source.registration_enabled, true),
    email_verification_enabled: booleanValue(source.email_verification_enabled, false),
    invitation_required: booleanValue(source.invitation_required, false),
    promo_codes_enabled: booleanValue(source.promo_codes_enabled, true),
    email_domain_whitelist: domainWhitelistValue(source.email_domain_whitelist),
    default_image_quota: integerValue(source.default_image_quota, 0),
    default_image_concurrency: integerValue(source.default_image_concurrency, 1, 1),
    verify_code_ttl_seconds: integerValue(source.verify_code_ttl_seconds, 900),
    verify_send_cooldown_seconds: integerValue(source.verify_send_cooldown_seconds, 60),
    verify_max_attempts: integerValue(source.verify_max_attempts, 5, 1),
    smtp_host: textValue(source.smtp_host),
    smtp_port: integerValue(source.smtp_port, 587),
    smtp_username: textValue(source.smtp_username),
    smtp_password: "",
    smtp_from: textValue(source.smtp_from),
    smtp_tls: booleanValue(source.smtp_tls, true),
    has_smtp_password: booleanValue(source.has_smtp_password, false),
  };
}

export function buildAuthSettingsPayload(settings: AuthSettings): Partial<AuthSettings> {
  const normalized = normalizeAuthSettingsForForm(settings);
  const smtpPassword = textValue((settings as Record<keyof AuthSettings, unknown>).smtp_password);
  const payload: Partial<AuthSettings> = {
    site_name: normalized.site_name,
    registration_enabled: normalized.registration_enabled,
    email_verification_enabled: normalized.email_verification_enabled,
    invitation_required: normalized.invitation_required,
    promo_codes_enabled: normalized.promo_codes_enabled,
    email_domain_whitelist: normalized.email_domain_whitelist,
    default_image_quota: normalized.default_image_quota,
    default_image_concurrency: normalized.default_image_concurrency,
    verify_code_ttl_seconds: normalized.verify_code_ttl_seconds,
    verify_send_cooldown_seconds: normalized.verify_send_cooldown_seconds,
    verify_max_attempts: normalized.verify_max_attempts,
    smtp_host: normalized.smtp_host,
    smtp_port: normalized.smtp_port,
    smtp_username: normalized.smtp_username,
    smtp_from: normalized.smtp_from,
    smtp_tls: normalized.smtp_tls,
  };

  if (smtpPassword) {
    payload.smtp_password = smtpPassword;
  }

  return payload;
}

import type { RedeemCode, RedeemCodeType } from "@/lib/api";

import type { AdminBadgeTone } from "../../promo-codes/components/promo-code-helpers";
import { isPastDate } from "../../promo-codes/components/promo-code-helpers";

export type RedeemCodeStatusKey = "available" | "disabled" | "used" | "expired";

export type RedeemCodeStatus = {
  key: RedeemCodeStatusKey;
  label: string;
  tone: AdminBadgeTone;
};

export function getRedeemCodeStatus(
  item: Pick<RedeemCode, "enabled" | "used" | "expires_at">,
  now = new Date(),
): RedeemCodeStatus {
  if (item.used) {
    return { key: "used", label: "已使用", tone: "secondary" };
  }
  if (!item.enabled) {
    return { key: "disabled", label: "禁用", tone: "secondary" };
  }
  if (isPastDate(item.expires_at, now)) {
    return { key: "expired", label: "已过期", tone: "danger" };
  }
  return { key: "available", label: "可用", tone: "success" };
}

export function getRedeemCodeTypeLabel(type: RedeemCodeType) {
  if (type === "image_quota") return "图片额度";
  if (type === "concurrency") return "图片并发";
  return "邀请码";
}

export function getRedeemValueLabel(item: Pick<RedeemCode, "type" | "value">) {
  if (item.type === "invitation") return "注册邀请";
  return `+${item.value}`;
}

type ClipboardLike = {
  writeText: (text: string) => Promise<void>;
};

type CopyDocumentLike = {
  body?: {
    appendChild: (node: HTMLTextAreaElement | unknown) => unknown;
    removeChild: (node: HTMLTextAreaElement | unknown) => unknown;
  };
  createElement: (tagName: "textarea") => HTMLTextAreaElement;
  execCommand?: (command: "copy") => boolean;
};

type CopyTextOptions = {
  clipboard?: ClipboardLike | null;
  document?: CopyDocumentLike | null;
  isSecureContext?: boolean;
  sourceElement?: Pick<HTMLTextAreaElement, "focus" | "select" | "setSelectionRange" | "value"> | null;
};

export async function copyTextToClipboard(text: string, options: CopyTextOptions = {}) {
  if (!text.trim()) {
    return false;
  }

  const secureContext =
    options.isSecureContext ?? (typeof window !== "undefined" ? window.isSecureContext : false);
  if (!secureContext && copyTextWithSelectionFallback(text, options)) {
    return true;
  }

  const clipboard =
    options.clipboard ?? (typeof navigator !== "undefined" ? navigator.clipboard : null);
  if (clipboard?.writeText) {
    try {
      await clipboard.writeText(text);
      return true;
    } catch {
      // Fall back for LAN HTTP pages where Clipboard API is commonly blocked.
    }
  }

  return copyTextWithSelectionFallback(text, options);
}

function copyTextWithSelectionFallback(text: string, options: CopyTextOptions) {
  const targetDocument = options.document ?? (typeof document !== "undefined" ? document : null);
  if (!targetDocument?.body || !targetDocument.execCommand) {
    return false;
  }

  const sourceElement = options.sourceElement;
  if (sourceElement) {
    sourceElement.focus();
    sourceElement.select();
    sourceElement.setSelectionRange(0, sourceElement.value.length);
    return targetDocument.execCommand("copy");
  }

  const textarea = targetDocument.createElement("textarea") as HTMLTextAreaElement;
  textarea.value = text;
  textarea.setAttribute("readonly", "true");
  textarea.style.position = "fixed";
  textarea.style.left = "-9999px";
  textarea.style.top = "0";
  targetDocument.body.appendChild(textarea);
  try {
    textarea.focus();
    textarea.select();
    textarea.setSelectionRange(0, textarea.value.length);
    return targetDocument.execCommand("copy");
  } finally {
    targetDocument.body.removeChild(textarea);
  }
}

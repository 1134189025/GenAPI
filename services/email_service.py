from __future__ import annotations

import html
import smtplib
from email.message import EmailMessage
from email.utils import formataddr

from services.user_service import UserServiceError, clean_string


def _mail_header_text(value: object, fallback: str) -> str:
    text = clean_string(value) or fallback
    return text.replace("\r", " ").replace("\n", " ").strip() or fallback


def _verification_ttl_minutes(value: object) -> int:
    try:
        seconds = int(value or 900)
    except (TypeError, ValueError):
        seconds = 900
    return max(1, (max(1, seconds) + 59) // 60)


def _verification_plain_text(site_name: str, code: str, ttl_minutes: int) -> str:
    return "\n".join(
        [
            f"你的验证码是：{code}",
            "",
            f"验证码将在 {ttl_minutes} 分钟内有效。",
            "如果不是你本人操作，请忽略此邮件。",
            "",
            f"这是一封来自 {site_name} 的自动邮件，请勿回复。",
        ]
    )


def _verification_html(site_name: str, code: str, ttl_minutes: int) -> str:
    safe_site_name = html.escape(site_name, quote=True)
    safe_code = html.escape(code, quote=True)
    safe_ttl = html.escape(str(ttl_minutes), quote=True)
    return f"""<!doctype html>
<html lang="zh-CN">
  <body style="margin:0;background:#f6f7fb;padding:32px 16px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',Arial,sans-serif;color:#1f2937;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;">
      <tr>
        <td align="center">
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:520px;border-collapse:collapse;">
            <tr>
              <td style="padding:0 0 16px;text-align:center;font-size:18px;font-weight:700;color:#0f766e;">{safe_site_name}</td>
            </tr>
            <tr>
              <td style="border:1px solid #e5e7eb;border-radius:20px;background:#ffffff;padding:32px 28px;text-align:center;box-shadow:0 18px 48px rgba(15,23,42,0.08);">
                <div style="font-size:14px;font-weight:700;letter-spacing:0.08em;color:#64748b;text-transform:uppercase;">Email Verification Code</div>
                <h1 style="margin:12px 0 8px;font-size:24px;line-height:1.35;color:#111827;">邮箱验证码</h1>
                <p style="margin:0 0 24px;font-size:14px;line-height:1.8;color:#64748b;">请在注册页面输入以下验证码完成邮箱验证。</p>
                <div style="display:inline-block;border-radius:18px;background:#0f172a;padding:18px 28px;font-size:36px;font-weight:800;line-height:1;letter-spacing:0.22em;color:#ffffff;">{safe_code}</div>
                <p style="margin:24px 0 0;font-size:15px;line-height:1.8;color:#0f766e;font-weight:700;">验证码将在 {safe_ttl} 分钟内有效。</p>
                <p style="margin:8px 0 0;font-size:13px;line-height:1.8;color:#64748b;">如果不是你本人操作，请忽略此邮件。</p>
              </td>
            </tr>
            <tr>
              <td style="padding:18px 8px 0;text-align:center;font-size:12px;line-height:1.7;color:#94a3b8;">这是一封自动邮件，请勿回复。</td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>"""


class EmailService:
    def send_verification_code(self, email: str, code: str) -> None:
        from services.user_service import user_service

        settings = user_service.get_settings()
        site_name = _mail_header_text(settings.get("site_name"), "Genapi")
        host = clean_string(settings.get("smtp_host"))
        if not host:
            raise UserServiceError("SMTP is not configured", status_code=400, code="smtp_not_configured")
        port = int(settings.get("smtp_port") or 587)
        username = clean_string(settings.get("smtp_username"))
        password = clean_string(settings.get("smtp_password"))
        sender = clean_string(settings.get("smtp_from")) or username
        if not sender:
            raise UserServiceError("SMTP sender is not configured", status_code=400, code="smtp_not_configured")

        message = EmailMessage()
        ttl_minutes = _verification_ttl_minutes(settings.get("verify_code_ttl_seconds"))
        message["Subject"] = f"【{site_name}】邮箱验证码"
        message["From"] = formataddr((site_name, sender))
        message["To"] = email
        message.set_content(_verification_plain_text(site_name, code, ttl_minutes), charset="utf-8")
        message.add_alternative(_verification_html(site_name, code, ttl_minutes), subtype="html", charset="utf-8")

        use_implicit_ssl = port == 465
        use_starttls = bool(settings.get("smtp_tls", True)) and not use_implicit_ssl
        smtp_factory = smtplib.SMTP_SSL if use_implicit_ssl else smtplib.SMTP
        try:
            with smtp_factory(host, port, timeout=15) as smtp:
                if use_starttls:
                    smtp.starttls()
                if username:
                    smtp.login(username, password)
                smtp.send_message(message)
        except (OSError, smtplib.SMTPException) as exc:
            raise UserServiceError(
                f"failed to send verification email: {exc}",
                status_code=502,
                code="smtp_send_failed",
            ) from exc


email_service = EmailService()

from __future__ import annotations

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
        message.set_content(
            "\n".join(
                [
                    f"你的验证码是：{code}",
                    "",
                    f"验证码将在 {ttl_minutes} 分钟内有效。",
                    "如果不是你本人操作，请忽略此邮件。",
                ]
            ),
            charset="utf-8",
        )

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

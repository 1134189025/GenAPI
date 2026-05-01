from __future__ import annotations

import unittest
from email.utils import parseaddr
from unittest.mock import MagicMock, patch

from services.email_service import email_service


def tearDownModule() -> None:
    import services.user_service as user_service_module

    engine = getattr(user_service_module.user_service, "engine", None)
    if engine is not None:
        engine.dispose()


class EmailServiceTests(unittest.TestCase):
    def _smtp_context(self) -> tuple[MagicMock, MagicMock]:
        smtp = MagicMock()
        context = MagicMock()
        context.__enter__.return_value = smtp
        context.__exit__.return_value = None
        return context, smtp

    def test_port_465_uses_implicit_ssl_without_starttls(self) -> None:
        context, smtp = self._smtp_context()
        settings = {
            "smtp_host": "smtp.qq.com",
            "smtp_port": 465,
            "smtp_username": "user@qq.com",
            "smtp_password": "auth-code",
            "smtp_from": "",
            "smtp_tls": True,
        }

        with patch("services.user_service.user_service.get_settings", return_value=settings):
            with patch("services.email_service.smtplib.SMTP_SSL", return_value=context) as smtp_ssl:
                with patch("services.email_service.smtplib.SMTP") as smtp_plain:
                    email_service.send_verification_code("target@example.com", "123456")

        smtp_ssl.assert_called_once_with("smtp.qq.com", 465, timeout=15)
        smtp_plain.assert_not_called()
        smtp.starttls.assert_not_called()
        smtp.login.assert_called_once_with("user@qq.com", "auth-code")
        smtp.send_message.assert_called_once()

    def test_starttls_is_used_for_non_ssl_ports_when_enabled(self) -> None:
        context, smtp = self._smtp_context()
        settings = {
            "smtp_host": "smtp.example.com",
            "smtp_port": 587,
            "smtp_username": "user@example.com",
            "smtp_password": "password",
            "smtp_from": "",
            "smtp_tls": True,
        }

        with patch("services.user_service.user_service.get_settings", return_value=settings):
            with patch("services.email_service.smtplib.SMTP", return_value=context) as smtp_plain:
                email_service.send_verification_code("target@example.com", "123456")

        smtp_plain.assert_called_once_with("smtp.example.com", 587, timeout=15)
        smtp.starttls.assert_called_once()
        smtp.login.assert_called_once_with("user@example.com", "password")
        smtp.send_message.assert_called_once()

    def test_verification_message_uses_site_name_sender_and_localized_content(self) -> None:
        context, smtp = self._smtp_context()
        settings = {
            "site_name": "绘图站",
            "verify_code_ttl_seconds": 1200,
            "smtp_host": "smtp.qq.com",
            "smtp_port": 465,
            "smtp_username": "user@qq.com",
            "smtp_password": "auth-code",
            "smtp_from": "",
            "smtp_tls": True,
        }

        with patch("services.user_service.user_service.get_settings", return_value=settings):
            with patch("services.email_service.smtplib.SMTP_SSL", return_value=context):
                email_service.send_verification_code("target@example.com", "654321")

        sent_message = smtp.send_message.call_args.args[0]
        self.assertEqual(sent_message["Subject"], "【绘图站】邮箱验证码")
        self.assertEqual(parseaddr(sent_message["From"]), ("绘图站", "user@qq.com"))
        self.assertEqual(sent_message["To"], "target@example.com")
        body = sent_message.get_body(("plain",)).get_content()
        self.assertIn("你的验证码是：654321", body)
        self.assertIn("验证码将在 20 分钟内有效", body)
        self.assertIn("如果不是你本人操作，请忽略此邮件", body)

    def test_verification_message_includes_html_alternative_with_escaped_brand(self) -> None:
        context, smtp = self._smtp_context()
        settings = {
            "site_name": '绘图站 <Beta> "A"',
            "verify_code_ttl_seconds": 300,
            "smtp_host": "smtp.qq.com",
            "smtp_port": 465,
            "smtp_username": "user@qq.com",
            "smtp_password": "auth-code",
            "smtp_from": "",
            "smtp_tls": True,
        }

        with patch("services.user_service.user_service.get_settings", return_value=settings):
            with patch("services.email_service.smtplib.SMTP_SSL", return_value=context):
                email_service.send_verification_code("target@example.com", "987654")

        sent_message = smtp.send_message.call_args.args[0]
        self.assertTrue(sent_message.is_multipart())
        plain = sent_message.get_body(("plain",))
        html = sent_message.get_body(("html",))
        self.assertIsNotNone(plain)
        self.assertIsNotNone(html)

        plain_body = plain.get_content()
        html_body = html.get_content()
        self.assertIn("你的验证码是：987654", plain_body)
        self.assertIn("验证码将在 5 分钟内有效", plain_body)
        self.assertIn("987654", html_body)
        self.assertIn("验证码将在 5 分钟内有效", html_body)
        self.assertIn("这是一封自动邮件，请勿回复", html_body)
        self.assertIn("绘图站 &lt;Beta&gt; &quot;A&quot;", html_body)
        self.assertNotIn('<Beta> "A"', html_body)


if __name__ == "__main__":
    unittest.main()

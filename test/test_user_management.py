from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, local
from unittest.mock import patch

from fastapi.testclient import TestClient


class UserManagementAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        db_path = Path(self.tmp.name) / "users.db"
        self.old_user_db = os.environ.get("GENAPI_USER_DATABASE_URL")
        self.old_jwt_secret = os.environ.get("JWT_SECRET")
        self.old_jwt_secret_file = os.environ.get("GENAPI_JWT_SECRET_FILE")
        os.environ["GENAPI_USER_DATABASE_URL"] = f"sqlite:///{db_path}"
        os.environ.pop("GENAPI_JWT_SECRET_FILE", None)

        self.reload_app(jwt_secret="unit-test-secret-with-at-least-32-bytes")

    def reload_app(self, *, jwt_secret: str | None, jwt_secret_file: Path | None = None) -> None:
        client = getattr(self, "client", None)
        if client is not None:
            client.close()
        service_module = sys.modules.get("services.user_service")
        service = getattr(service_module, "user_service", None)
        engine = getattr(service, "engine", None)
        if engine is not None:
            engine.dispose()

        if jwt_secret is None:
            os.environ.pop("JWT_SECRET", None)
        else:
            os.environ["JWT_SECRET"] = jwt_secret
        if jwt_secret_file is None:
            os.environ.pop("GENAPI_JWT_SECRET_FILE", None)
        else:
            os.environ["GENAPI_JWT_SECRET_FILE"] = str(jwt_secret_file)
        for module_name in list(sys.modules):
            if (
                module_name.startswith("api.")
                or module_name.startswith("services.user")
                or module_name == "api"
            ):
                sys.modules.pop(module_name, None)

        app_module = importlib.import_module("api.app")
        self.client = TestClient(app_module.create_app())

    def tearDown(self) -> None:
        self.client.close()
        service_module = sys.modules.get("services.user_service")
        service = getattr(service_module, "user_service", None)
        engine = getattr(service, "engine", None)
        if engine is not None:
            engine.dispose()
        if self.old_user_db is None:
            os.environ.pop("GENAPI_USER_DATABASE_URL", None)
        else:
            os.environ["GENAPI_USER_DATABASE_URL"] = self.old_user_db
        if self.old_jwt_secret is None:
            os.environ.pop("JWT_SECRET", None)
        else:
            os.environ["JWT_SECRET"] = self.old_jwt_secret
        if self.old_jwt_secret_file is None:
            os.environ.pop("GENAPI_JWT_SECRET_FILE", None)
        else:
            os.environ["GENAPI_JWT_SECRET_FILE"] = self.old_jwt_secret_file
        self.tmp.cleanup()

    def create_admin(self) -> str:
        response = self.client.post(
            "/api/setup/admin",
            json={"email": "admin@example.com", "password": "AdminPass123!"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        return str(payload["token"])

    def auth_headers(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def user_service(self):
        return importlib.import_module("services.user_service").user_service

    def test_openai_compatible_v1_routes_are_not_exposed(self) -> None:
        for method, path, payload in [
            ("get", "/v1/models", None),
            ("post", "/v1/images/generations", {"prompt": "draw", "model": "gpt-image-2", "n": 1}),
            ("post", "/v1/images/edits", {}),
            ("post", "/v1/chat/completions", {"model": "gpt-5", "messages": []}),
            ("post", "/v1/responses", {"model": "gpt-5", "input": "hello"}),
            ("post", "/v1/messages", {"model": "claude", "messages": []}),
        ]:
            request = getattr(self.client, method)
            response = request(path, json=payload) if payload is not None else request(path)
            self.assertEqual(response.status_code, 404, f"{path} should not be served")

    def test_bare_api_and_v1_routes_do_not_fall_back_to_spa(self) -> None:
        web_dist = Path(self.tmp.name) / "web_dist"
        web_dist.mkdir()
        (web_dist / "index.html").write_text("<html>spa</html>", encoding="utf-8")

        with patch("api.support.WEB_DIST_DIR", web_dist):
            self.assertEqual(self.client.get("/api").status_code, 404)
            self.assertEqual(self.client.get("/api/").status_code, 404)
            self.assertEqual(self.client.get("/v1").status_code, 404)
            self.assertEqual(self.client.get("/v1/").status_code, 404)
            self.assertEqual(self.client.post("/auth/login", headers={"Authorization": "Bearer legacy"}).status_code, 404)

    def test_setup_creates_first_admin_once_and_jwt_authenticates(self) -> None:
        status = self.client.get("/api/setup/status")
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["requires_setup"], True)

        token = self.create_admin()

        status = self.client.get("/api/setup/status")
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["requires_setup"], False)

        second = self.client.post(
            "/api/setup/admin",
            json={"email": "other@example.com", "password": "AdminPass123!"},
        )
        self.assertEqual(second.status_code, 409)

        me = self.client.get("/api/auth/me", headers=self.auth_headers(token))
        self.assertEqual(me.status_code, 200, me.text)
        self.assertEqual(me.json()["user"]["email"], "admin@example.com")
        self.assertEqual(me.json()["user"]["role"], "admin")

    def test_jwt_secret_without_env_is_file_backed_not_auth_settings(self) -> None:
        secret_file = Path(self.tmp.name) / "jwt_hmac_secret"
        self.reload_app(jwt_secret=None, jwt_secret_file=secret_file)

        self.assertTrue(secret_file.is_file())
        service_module = importlib.import_module("services.user_service")
        service = service_module.user_service
        with service.Session() as session:
            self.assertIsNone(session.get(service_module.AuthSettingModel, "jwt_secret"))

        token = self.create_admin()
        me = self.client.get("/api/auth/me", headers=self.auth_headers(token))
        self.assertEqual(me.status_code, 200, me.text)

    def test_register_consumes_invitation_and_applies_promo_code(self) -> None:
        admin_token = self.create_admin()
        headers = self.auth_headers(admin_token)
        settings = self.client.patch(
            "/api/admin/auth-settings",
            headers=headers,
            json={
                "email_verification_enabled": False,
                "invitation_required": True,
                "promo_codes_enabled": True,
                "default_image_quota": 0,
            },
        )
        self.assertEqual(settings.status_code, 200, settings.text)

        invite_response = self.client.post(
            "/api/admin/redeem-codes/generate",
            headers=headers,
            json={"type": "invitation", "count": 1},
        )
        self.assertEqual(invite_response.status_code, 200, invite_response.text)
        invitation_code = invite_response.json()["codes"][0]["code"]

        promo_response = self.client.post(
            "/api/admin/promo-codes",
            headers=headers,
            json={"code": "WELCOME", "image_quota": 3, "max_uses": 1},
        )
        self.assertEqual(promo_response.status_code, 200, promo_response.text)

        register = self.client.post(
            "/api/auth/register",
            json={
                "email": "user@example.com",
                "password": "UserPass123!",
                "invitation_code": invitation_code,
                "promo_code": "WELCOME",
            },
        )
        self.assertEqual(register.status_code, 200, register.text)
        self.assertEqual(register.json()["user"]["image_quota"], 3)

        reused = self.client.post(
            "/api/auth/register",
            json={
                "email": "second@example.com",
                "password": "UserPass123!",
                "invitation_code": invitation_code,
                "promo_code": "WELCOME",
            },
        )
        self.assertEqual(reused.status_code, 400)

    def test_internal_web_image_generation_reserves_and_settles_quota(self) -> None:
        admin_token = self.create_admin()
        created = self.client.post(
            "/api/admin/users",
            headers=self.auth_headers(admin_token),
            json={
                "email": "artist@example.com",
                "password": "UserPass123!",
                "role": "user",
                "enabled": True,
                "image_quota": 1,
                "image_concurrency": 1,
            },
        )
        self.assertEqual(created.status_code, 200, created.text)

        login = self.client.post(
            "/api/auth/login",
            json={"email": "artist@example.com", "password": "UserPass123!"},
        )
        self.assertEqual(login.status_code, 200, login.text)
        user_token = login.json()["token"]
        headers = self.auth_headers(user_token)

        with patch(
            "api.ai.openai_v1_image_generations.handle",
            return_value={"created": 1, "data": [{"b64_json": "abc"}]},
        ):
            first = self.client.post(
                "/api/image/generations",
                headers=headers,
                json={"prompt": "draw", "model": "gpt-image-2", "n": 1},
            )
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(self.client.get("/api/auth/me", headers=headers).json()["user"]["image_quota"], 0)

        second = self.client.post(
            "/api/image/generations",
            headers=headers,
            json={"prompt": "draw again", "model": "gpt-image-2", "n": 1},
        )
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second.json()["error"]["code"], "insufficient_quota")

    def test_failed_image_request_refunds_reserved_quota(self) -> None:
        admin_token = self.create_admin()
        self.client.post(
            "/api/admin/users",
            headers=self.auth_headers(admin_token),
            json={
                "email": "refund@example.com",
                "password": "UserPass123!",
                "role": "user",
                "enabled": True,
                "image_quota": 1,
                "image_concurrency": 1,
            },
        )
        login = self.client.post(
            "/api/auth/login",
            json={"email": "refund@example.com", "password": "UserPass123!"},
        )
        headers = self.auth_headers(login.json()["token"])

        with patch("api.ai.openai_v1_image_generations.handle", side_effect=RuntimeError("upstream failed")):
            response = self.client.post(
                "/api/image/generations",
                headers=headers,
                json={"prompt": "draw", "model": "gpt-image-2", "n": 1},
        )
        self.assertEqual(response.status_code, 502, response.text)
        self.assertEqual(self.client.get("/api/auth/me", headers=headers).json()["user"]["image_quota"], 1)

    def test_concurrent_failed_image_settlements_refund_all_reserved_quota(self) -> None:
        admin_token = self.create_admin()
        created = self.client.post(
            "/api/admin/users",
            headers=self.auth_headers(admin_token),
            json={
                "email": "quota-race@example.com",
                "password": "UserPass123!",
                "role": "user",
                "enabled": True,
                "image_quota": 2,
                "image_concurrency": 2,
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        user_id = created.json()["item"]["id"]

        service = self.user_service()
        identity = {"id": user_id, "role": "user", "name": "quota-race@example.com"}
        reservations = [
            service.reserve_image_quota(identity, 1, "/api/image/generations"),
            service.reserve_image_quota(identity, 1, "/api/image/generations"),
        ]

        listed_before = self.client.get("/api/admin/users", headers=self.auth_headers(admin_token)).json()["items"]
        target_before = next(user for user in listed_before if user["id"] == user_id)
        self.assertEqual(target_before["image_quota"], 0)
        self.assertEqual(target_before["active_image_requests"], 2)

        service_module = importlib.import_module("services.user_service")
        real_utc_now = service_module.utc_now
        barrier = Barrier(2)
        thread_state = local()

        def synchronized_utc_now():
            if not getattr(thread_state, "blocked_once", False):
                thread_state.blocked_once = True
                barrier.wait(timeout=5)
            return real_utc_now()

        with patch("services.user_service.utc_now", synchronized_utc_now):
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [
                    executor.submit(
                        service.settle_image_quota,
                        reservation,
                        success=False,
                        error="upstream failed",
                    )
                    for reservation in reservations
                ]
                for future in futures:
                    future.result(timeout=10)

        listed_after = self.client.get("/api/admin/users", headers=self.auth_headers(admin_token)).json()["items"]
        target_after = next(user for user in listed_after if user["id"] == user_id)
        self.assertEqual(target_after["image_quota"], 2)
        self.assertEqual(target_after["active_image_requests"], 0)

    def test_internal_web_image_edit_route_remains_available_after_v1_removal(self) -> None:
        admin_token = self.create_admin()
        created = self.client.post(
            "/api/admin/users",
            headers=self.auth_headers(admin_token),
            json={
                "email": "editor@example.com",
                "password": "UserPass123!",
                "role": "user",
                "enabled": True,
                "image_quota": 1,
                "image_concurrency": 1,
            },
        )
        self.assertEqual(created.status_code, 200, created.text)

        login = self.client.post(
            "/api/auth/login",
            json={"email": "editor@example.com", "password": "UserPass123!"},
        )
        self.assertEqual(login.status_code, 200, login.text)
        headers = self.auth_headers(login.json()["token"])

        with patch(
            "api.ai.openai_v1_image_edit.handle",
            return_value={"created": 1, "data": [{"b64_json": "edited"}]},
        ) as edit_handler:
            response = self.client.post(
                "/api/image/edits",
                headers=headers,
                data={"prompt": "make it brighter", "model": "gpt-image-2", "n": "1"},
                files={"image": ("reference.png", b"fake-image", "image/png")},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["data"][0]["b64_json"], "edited")
        payload = edit_handler.call_args.args[0]
        self.assertEqual(payload["prompt"], "make it brighter")
        self.assertEqual(payload["model"], "gpt-image-2")
        self.assertEqual(payload["images"][0][1], "reference.png")

    def test_send_verify_code_uses_register_purpose_for_cooldown(self) -> None:
        with patch("api.user_management.email_service.send_verification_code", return_value=None):
            first = self.client.post(
                "/api/auth/send-verify-code",
                json={"email": "cooldown@example.com", "purpose": "register"},
            )
            self.assertEqual(first.status_code, 200, first.text)

            second = self.client.post(
                "/api/auth/send-verify-code",
                json={"email": "cooldown@example.com", "purpose": "bypass"},
            )
        self.assertEqual(second.status_code, 429, second.text)

    def test_send_verify_code_rejects_closed_registration_before_smtp(self) -> None:
        admin_token = self.create_admin()
        settings = self.client.patch(
            "/api/admin/auth-settings",
            headers=self.auth_headers(admin_token),
            json={"registration_enabled": False},
        )
        self.assertEqual(settings.status_code, 200, settings.text)

        with patch("api.user_management.email_service.send_verification_code", return_value=None) as send_mock:
            response = self.client.post(
                "/api/auth/send-verify-code",
                json={"email": "closed@example.com", "purpose": "register"},
            )
        self.assertEqual(response.status_code, 403, response.text)
        send_mock.assert_not_called()

    def test_failed_verify_email_send_does_not_start_cooldown(self) -> None:
        error_cls = importlib.import_module("services.user_service").UserServiceError
        with patch(
            "api.user_management.email_service.send_verification_code",
            side_effect=error_cls("smtp failed", status_code=502, code="smtp_send_failed"),
        ):
            failed = self.client.post(
                "/api/auth/send-verify-code",
                json={"email": "retry@example.com", "purpose": "register"},
            )
        self.assertEqual(failed.status_code, 502, failed.text)

        with patch("api.user_management.email_service.send_verification_code", return_value=None):
            retried = self.client.post(
                "/api/auth/send-verify-code",
                json={"email": "retry@example.com", "purpose": "register"},
            )
        self.assertEqual(retried.status_code, 200, retried.text)

    def test_register_does_not_consume_verification_code_until_later_checks_pass(self) -> None:
        admin_token = self.create_admin()
        headers = self.auth_headers(admin_token)
        settings = self.client.patch(
            "/api/admin/auth-settings",
            headers=headers,
            json={"email_verification_enabled": True, "invitation_required": True},
        )
        self.assertEqual(settings.status_code, 200, settings.text)
        code = self.user_service().create_email_verification_code("delayed@example.com", "register")

        missing_invite = self.client.post(
            "/api/auth/register",
            json={
                "email": "delayed@example.com",
                "password": "UserPass123!",
                "verification_code": code,
            },
        )
        self.assertEqual(missing_invite.status_code, 400, missing_invite.text)

        invite_response = self.client.post(
            "/api/admin/redeem-codes/generate",
            headers=headers,
            json={"type": "invitation", "count": 1},
        )
        self.assertEqual(invite_response.status_code, 200, invite_response.text)
        invitation_code = invite_response.json()["codes"][0]["code"]

        registered = self.client.post(
            "/api/auth/register",
            json={
                "email": "delayed@example.com",
                "password": "UserPass123!",
                "verification_code": code,
                "invitation_code": invitation_code,
            },
        )
        self.assertEqual(registered.status_code, 200, registered.text)

    def test_last_enabled_admin_cannot_be_disabled_or_deleted(self) -> None:
        admin_token = self.create_admin()
        headers = self.auth_headers(admin_token)
        me = self.client.get("/api/auth/me", headers=headers)
        self.assertEqual(me.status_code, 200, me.text)
        admin_id = me.json()["user"]["id"]

        disabled = self.client.patch(
            f"/api/admin/users/{admin_id}",
            headers=headers,
            json={"enabled": False},
        )
        self.assertIn(disabled.status_code, {400, 409}, disabled.text)

        deleted = self.client.delete(f"/api/admin/users/{admin_id}", headers=headers)
        self.assertIn(deleted.status_code, {400, 409}, deleted.text)

    def test_admin_cannot_disable_delete_or_demote_self_even_with_another_admin(self) -> None:
        admin_token = self.create_admin()
        headers = self.auth_headers(admin_token)
        me = self.client.get("/api/auth/me", headers=headers)
        self.assertEqual(me.status_code, 200, me.text)
        admin_id = me.json()["user"]["id"]

        second_admin = self.client.post(
            "/api/admin/users",
            headers=headers,
            json={
                "email": "second-admin@example.com",
                "password": "AdminPass123!",
                "role": "admin",
                "enabled": True,
            },
        )
        self.assertEqual(second_admin.status_code, 200, second_admin.text)

        demoted = self.client.patch(
            f"/api/admin/users/{admin_id}",
            headers=headers,
            json={"role": "user"},
        )
        self.assertEqual(demoted.status_code, 400, demoted.text)

        disabled = self.client.patch(
            f"/api/admin/users/{admin_id}",
            headers=headers,
            json={"enabled": False},
        )
        self.assertEqual(disabled.status_code, 400, disabled.text)

        deleted = self.client.delete(f"/api/admin/users/{admin_id}", headers=headers)
        self.assertEqual(deleted.status_code, 400, deleted.text)

        still_admin = self.client.get("/api/auth/me", headers=headers)
        self.assertEqual(still_admin.status_code, 200, still_admin.text)
        self.assertEqual(still_admin.json()["user"]["role"], "admin")
        self.assertEqual(still_admin.json()["user"]["enabled"], True)

    def test_demoting_admin_without_image_concurrency_sets_user_minimum(self) -> None:
        admin_token = self.create_admin()
        headers = self.auth_headers(admin_token)
        second_admin = self.client.post(
            "/api/admin/users",
            headers=headers,
            json={
                "email": "demote@example.com",
                "password": "AdminPass123!",
                "role": "admin",
                "enabled": True,
            },
        )
        self.assertEqual(second_admin.status_code, 200, second_admin.text)
        second_admin_id = second_admin.json()["item"]["id"]
        self.assertEqual(second_admin.json()["item"]["image_concurrency"], 0)

        demoted = self.client.patch(
            f"/api/admin/users/{second_admin_id}",
            headers=headers,
            json={"role": "user"},
        )
        self.assertEqual(demoted.status_code, 200, demoted.text)
        self.assertEqual(demoted.json()["item"]["role"], "user")
        self.assertEqual(demoted.json()["item"]["image_concurrency"], 1)

    def test_logout_revokes_current_jwt(self) -> None:
        token = self.create_admin()
        headers = self.auth_headers(token)

        logout = self.client.post("/api/auth/logout", headers=headers)
        self.assertEqual(logout.status_code, 200, logout.text)

        me = self.client.get("/api/auth/me", headers=headers)
        self.assertEqual(me.status_code, 401, me.text)

    def test_password_change_invalidates_older_jwts(self) -> None:
        admin_token = self.create_admin()
        headers = self.auth_headers(admin_token)
        created = self.client.post(
            "/api/admin/users",
            headers=headers,
            json={
                "email": "rotate@example.com",
                "password": "OldPass123!",
                "role": "user",
                "enabled": True,
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        user_id = created.json()["item"]["id"]

        login = self.client.post(
            "/api/auth/login",
            json={"email": "rotate@example.com", "password": "OldPass123!"},
        )
        self.assertEqual(login.status_code, 200, login.text)
        old_headers = self.auth_headers(login.json()["token"])

        changed = self.client.patch(
            f"/api/admin/users/{user_id}",
            headers=headers,
            json={"password": "NewPass123!"},
        )
        self.assertEqual(changed.status_code, 200, changed.text)

        old_me = self.client.get("/api/auth/me", headers=old_headers)
        self.assertEqual(old_me.status_code, 401, old_me.text)

        old_login = self.client.post(
            "/api/auth/login",
            json={"email": "rotate@example.com", "password": "OldPass123!"},
        )
        self.assertEqual(old_login.status_code, 401, old_login.text)

        new_login = self.client.post(
            "/api/auth/login",
            json={"email": "rotate@example.com", "password": "NewPass123!"},
        )
        self.assertEqual(new_login.status_code, 200, new_login.text)

    def test_disabling_user_invalidates_token_after_reenable(self) -> None:
        admin_token = self.create_admin()
        admin_headers = self.auth_headers(admin_token)
        created = self.client.post(
            "/api/admin/users",
            headers=admin_headers,
            json={
                "email": "suspend@example.com",
                "password": "UserPass123!",
                "role": "user",
                "enabled": True,
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        user_id = created.json()["item"]["id"]

        login = self.client.post(
            "/api/auth/login",
            json={"email": "suspend@example.com", "password": "UserPass123!"},
        )
        self.assertEqual(login.status_code, 200, login.text)
        old_headers = self.auth_headers(login.json()["token"])

        disabled = self.client.patch(
            f"/api/admin/users/{user_id}",
            headers=admin_headers,
            json={"enabled": False},
        )
        self.assertEqual(disabled.status_code, 200, disabled.text)
        self.assertEqual(self.client.get("/api/auth/me", headers=old_headers).status_code, 401)

        reenabled = self.client.patch(
            f"/api/admin/users/{user_id}",
            headers=admin_headers,
            json={"enabled": True},
        )
        self.assertEqual(reenabled.status_code, 200, reenabled.text)
        self.assertEqual(self.client.get("/api/auth/me", headers=old_headers).status_code, 401)

        new_login = self.client.post(
            "/api/auth/login",
            json={"email": "suspend@example.com", "password": "UserPass123!"},
        )
        self.assertEqual(new_login.status_code, 200, new_login.text)

    def test_concurrent_verification_code_consume_is_one_time(self) -> None:
        service = self.user_service()
        email = "consume-race@example.com"
        code = service.create_email_verification_code(email, "register")
        original_code_hash = service.code_hash
        barrier = __import__("threading").Barrier(2)

        def delayed_code_hash(value: str) -> str:
            if value == code:
                barrier.wait(timeout=5)
            return original_code_hash(value)

        def consume_once() -> str:
            try:
                service.consume_email_verification_code(email, code, "register")
                return "ok"
            except Exception as exc:
                return str(getattr(exc, "code", exc.__class__.__name__))

        with patch.object(service, "code_hash", side_effect=delayed_code_hash):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: consume_once(), range(2)))

        self.assertEqual(results.count("ok"), 1, results)

    def test_concurrent_wrong_verification_attempts_are_not_lost(self) -> None:
        service = self.user_service()
        service.update_settings({"verify_max_attempts": 5})
        email = "attempt-race@example.com"
        code = service.create_email_verification_code(email, "register")
        wrong_code = "000000" if code != "000000" else "111111"
        original_code_hash = service.code_hash
        barrier = __import__("threading").Barrier(2)

        def delayed_code_hash(value: str) -> str:
            if value == wrong_code:
                barrier.wait(timeout=5)
            return original_code_hash(value)

        def fail_once() -> str:
            try:
                service.consume_email_verification_code(email, wrong_code, "register")
                return "ok"
            except Exception as exc:
                return str(getattr(exc, "code", exc.__class__.__name__))

        with patch.object(service, "code_hash", side_effect=delayed_code_hash):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: fail_once(), range(2)))

        self.assertEqual(results.count("ok"), 0, results)
        service_module = importlib.import_module("services.user_service")
        with service.Session() as session:
            row = (
                session.query(service_module.EmailVerificationCodeModel)
                .filter(service_module.EmailVerificationCodeModel.email == email)
                .one()
            )
            self.assertEqual(row.attempts, 2)

    def test_concurrent_register_duplicate_email_returns_user_error(self) -> None:
        service = self.user_service()
        service.update_settings({"email_verification_enabled": False})
        service_module = importlib.import_module("services.user_service")
        second_service = service_module.UserService(service.database_url)
        barrier = __import__("threading").Barrier(2)

        def register_once(candidate_service) -> str:
            original_hash_password = candidate_service.hash_password

            def delayed_hash_password(password: str) -> str:
                barrier.wait(timeout=5)
                return original_hash_password(password)

            try:
                with patch.object(candidate_service, "hash_password", side_effect=delayed_hash_password):
                    candidate_service.register(email="duplicate-race@example.com", password="UserPass123!")
                    return "ok"
            except Exception as exc:
                return str(getattr(exc, "code", exc.__class__.__name__))
            finally:
                if candidate_service is second_service:
                    second_service.engine.dispose()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(register_once, [service, second_service]))

        self.assertEqual(results.count("ok"), 1, results)
        self.assertEqual(results.count("duplicate_email"), 1, results)

    def test_concurrent_admin_disable_cannot_remove_all_enabled_admins(self) -> None:
        admin_token = self.create_admin()
        headers = self.auth_headers(admin_token)
        first_admin_id = self.client.get("/api/auth/me", headers=headers).json()["user"]["id"]
        second_admin = self.client.post(
            "/api/admin/users",
            headers=headers,
            json={
                "email": "race-disable-admin@example.com",
                "password": "AdminPass123!",
                "role": "admin",
                "enabled": True,
            },
        )
        self.assertEqual(second_admin.status_code, 200, second_admin.text)
        second_admin_id = second_admin.json()["item"]["id"]

        def disable_admin(user_id: str) -> str:
            try:
                service.update_user(user_id, {"enabled": False})
                return "ok"
            except Exception as exc:
                return str(getattr(exc, "code", exc.__class__.__name__))

        service = self.user_service()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(disable_admin, [first_admin_id, second_admin_id]))

        users = service.list_users()
        enabled_admins = [user for user in users if user["role"] == "admin" and user["enabled"]]
        self.assertEqual(results.count("ok"), 1, results)
        self.assertEqual(len(enabled_admins), 1, users)
        self.assertEqual(self.client.get("/api/setup/status").json()["requires_setup"], False)

    def test_concurrent_admin_delete_cannot_remove_all_enabled_admins(self) -> None:
        admin_token = self.create_admin()
        headers = self.auth_headers(admin_token)
        first_admin_id = self.client.get("/api/auth/me", headers=headers).json()["user"]["id"]
        second_admin = self.client.post(
            "/api/admin/users",
            headers=headers,
            json={
                "email": "race-delete-admin@example.com",
                "password": "AdminPass123!",
                "role": "admin",
                "enabled": True,
            },
        )
        self.assertEqual(second_admin.status_code, 200, second_admin.text)
        second_admin_id = second_admin.json()["item"]["id"]

        def delete_admin(user_id: str) -> str:
            try:
                service.delete_user(user_id)
                return "ok"
            except Exception as exc:
                return str(getattr(exc, "code", exc.__class__.__name__))

        service = self.user_service()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(delete_admin, [first_admin_id, second_admin_id]))

        users = service.list_users()
        enabled_admins = [user for user in users if user["role"] == "admin" and user["enabled"]]
        self.assertEqual(results.count("ok"), 1, results)
        self.assertEqual(len(enabled_admins), 1, users)
        self.assertEqual(self.client.get("/api/setup/status").json()["requires_setup"], False)

    def test_redeem_codes_add_image_quota_and_concurrency_once(self) -> None:
        admin_token = self.create_admin()
        user = self.client.post(
            "/api/admin/users",
            headers=self.auth_headers(admin_token),
            json={
                "email": "redeem@example.com",
                "password": "UserPass123!",
                "role": "user",
                "enabled": True,
                "image_quota": 0,
                "image_concurrency": 1,
            },
        )
        self.assertEqual(user.status_code, 200, user.text)
        login = self.client.post(
            "/api/auth/login",
            json={"email": "redeem@example.com", "password": "UserPass123!"},
        )
        headers = self.auth_headers(login.json()["token"])

        quota_code = self.client.post(
            "/api/admin/redeem-codes/generate",
            headers=self.auth_headers(admin_token),
            json={"type": "image_quota", "value": 5, "count": 1},
        ).json()["codes"][0]["code"]
        concurrency_code = self.client.post(
            "/api/admin/redeem-codes/generate",
            headers=self.auth_headers(admin_token),
            json={"type": "concurrency", "value": 2, "count": 1},
        ).json()["codes"][0]["code"]

        quota = self.client.post("/api/redeem", headers=headers, json={"code": quota_code})
        self.assertEqual(quota.status_code, 200, quota.text)
        concurrency = self.client.post("/api/redeem", headers=headers, json={"code": concurrency_code})
        self.assertEqual(concurrency.status_code, 200, concurrency.text)

        me = self.client.get("/api/auth/me", headers=headers)
        self.assertEqual(me.json()["user"]["image_quota"], 5)
        self.assertEqual(me.json()["user"]["image_concurrency"], 3)

        reused = self.client.post("/api/redeem", headers=headers, json={"code": quota_code})
        self.assertEqual(reused.status_code, 400)

    def test_same_redeem_code_cannot_be_used_by_two_concurrent_requests(self) -> None:
        admin_token = self.create_admin()
        user = self.client.post(
            "/api/admin/users",
            headers=self.auth_headers(admin_token),
            json={
                "email": "race@example.com",
                "password": "UserPass123!",
                "role": "user",
                "enabled": True,
                "image_quota": 0,
                "image_concurrency": 1,
            },
        )
        self.assertEqual(user.status_code, 200, user.text)
        user_id = user.json()["item"]["id"]
        code = self.client.post(
            "/api/admin/redeem-codes/generate",
            headers=self.auth_headers(admin_token),
            json={"type": "image_quota", "value": 5, "count": 1},
        ).json()["codes"][0]["code"]

        service = self.user_service()
        original_find = service._find_valid_redeem_code
        barrier = __import__("threading").Barrier(2)

        def delayed_find(session, value, *, expected_type=None):
            row = original_find(session, value, expected_type=expected_type)
            barrier.wait(timeout=5)
            return row

        def redeem_once() -> str:
            try:
                service.redeem(user_id, code)
                return "ok"
            except Exception as exc:
                return str(getattr(exc, "code", exc.__class__.__name__))

        with patch.object(service, "_find_valid_redeem_code", side_effect=delayed_find):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: redeem_once(), range(2)))

        self.assertEqual(results.count("ok"), 1, results)


if __name__ == "__main__":
    unittest.main()

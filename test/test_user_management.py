from __future__ import annotations

import base64
import importlib
import os
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Barrier, local
from unittest.mock import patch

from fastapi.testclient import TestClient

PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)
PNG_B64 = base64.b64encode(PNG_BYTES).decode("ascii")
GGB_PER_IMAGE = 5


class UserManagementAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        db_path = Path(self.tmp.name) / "users.db"
        self.old_user_db = os.environ.get("GENAPI_USER_DATABASE_URL")
        self.old_jwt_secret = os.environ.get("JWT_SECRET")
        self.old_jwt_secret_file = os.environ.get("GENAPI_JWT_SECRET_FILE")
        self.old_stale_seconds = os.environ.get("GENAPI_STALE_IMAGE_QUOTA_SECONDS")
        self.old_stale_throttle = os.environ.get("GENAPI_STALE_IMAGE_QUOTA_RECOVERY_THROTTLE_SECONDS")
        os.environ["GENAPI_USER_DATABASE_URL"] = f"sqlite:///{db_path}"
        os.environ.pop("GENAPI_JWT_SECRET_FILE", None)
        os.environ.pop("GENAPI_STALE_IMAGE_QUOTA_SECONDS", None)
        os.environ.pop("GENAPI_STALE_IMAGE_QUOTA_RECOVERY_THROTTLE_SECONDS", None)

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
                or module_name.startswith("services.gallery")
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
        if self.old_stale_seconds is None:
            os.environ.pop("GENAPI_STALE_IMAGE_QUOTA_SECONDS", None)
        else:
            os.environ["GENAPI_STALE_IMAGE_QUOTA_SECONDS"] = self.old_stale_seconds
        if self.old_stale_throttle is None:
            os.environ.pop("GENAPI_STALE_IMAGE_QUOTA_RECOVERY_THROTTLE_SECONDS", None)
        else:
            os.environ["GENAPI_STALE_IMAGE_QUOTA_RECOVERY_THROTTLE_SECONDS"] = self.old_stale_throttle
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

    def assert_user_ggb(self, user: dict[str, object], expected_ggb: int) -> None:
        self.assertIn("ggb", user)
        self.assertEqual(user["ggb"], expected_ggb)
        self.assertEqual(user["image_quota"], expected_ggb)

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

    def test_login_failures_lock_email_with_exponential_backoff_and_success_clears(self) -> None:
        admin_token = self.create_admin()
        created = self.client.post(
            "/api/admin/users",
            headers=self.auth_headers(admin_token),
            json={
                "email": "locked@example.com",
                "password": "UserPass123!",
                "role": "user",
                "enabled": True,
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        service_module = importlib.import_module("services.user_service")
        current = service_module.utc_now()

        def now():
            return current

        with patch("services.user_service.utc_now", side_effect=now):
            for _ in range(5):
                failed = self.client.post(
                    "/api/auth/login",
                    json={"email": "Locked@Example.com", "password": "wrong-pass"},
                )
                self.assertEqual(failed.status_code, 401, failed.text)

            locked = self.client.post(
                "/api/auth/login",
                json={"email": "locked@example.com", "password": "UserPass123!"},
            )
            self.assertEqual(locked.status_code, 429, locked.text)
            self.assertEqual(locked.json()["detail"]["error"]["code"], "login_rate_limited")

            current += timedelta(seconds=61)
            failed_after_first_lock = self.client.post(
                "/api/auth/login",
                json={"email": "locked@example.com", "password": "wrong-pass"},
            )
            self.assertEqual(failed_after_first_lock.status_code, 401, failed_after_first_lock.text)

            still_locked = self.client.post(
                "/api/auth/login",
                json={"email": "locked@example.com", "password": "UserPass123!"},
            )
            self.assertEqual(still_locked.status_code, 429, still_locked.text)

            current += timedelta(seconds=121)
            logged_in = self.client.post(
                "/api/auth/login",
                json={"email": "locked@example.com", "password": "UserPass123!"},
            )
            self.assertEqual(logged_in.status_code, 200, logged_in.text)

            for _ in range(5):
                failed = self.client.post(
                    "/api/auth/login",
                    json={"email": "locked@example.com", "password": "wrong-pass"},
                )
                self.assertEqual(failed.status_code, 401, failed.text)
            relocked = self.client.post(
                "/api/auth/login",
                json={"email": "locked@example.com", "password": "UserPass123!"},
            )
            self.assertEqual(relocked.status_code, 429, relocked.text)

    def test_unknown_and_invalid_login_failures_do_not_persist_or_lock_later_user(self) -> None:
        admin_token = self.create_admin()
        service_module = importlib.import_module("services.user_service")

        for _ in range(6):
            missing = self.client.post(
                "/api/auth/login",
                json={"email": "future@example.com", "password": "wrong-pass"},
            )
            self.assertEqual(missing.status_code, 401, missing.text)
            invalid = self.client.post(
                "/api/auth/login",
                json={"email": "not-an-email", "password": "wrong-pass"},
            )
            self.assertEqual(invalid.status_code, 401, invalid.text)

        service = self.user_service()
        with service.Session() as session:
            self.assertIsNone(session.get(service_module.LoginFailureLimitModel, "future@example.com"))
            self.assertIsNone(session.get(service_module.LoginFailureLimitModel, "not-an-email"))

        created = self.client.post(
            "/api/admin/users",
            headers=self.auth_headers(admin_token),
            json={
                "email": "future@example.com",
                "password": "UserPass123!",
                "role": "user",
                "enabled": True,
            },
        )
        self.assertEqual(created.status_code, 200, created.text)

        logged_in = self.client.post(
            "/api/auth/login",
            json={"email": "future@example.com", "password": "UserPass123!"},
        )
        self.assertEqual(logged_in.status_code, 200, logged_in.text)

    def test_concurrent_failed_logins_increment_existing_user_limit_safely(self) -> None:
        admin_token = self.create_admin()
        created = self.client.post(
            "/api/admin/users",
            headers=self.auth_headers(admin_token),
            json={
                "email": "concurrent@example.com",
                "password": "UserPass123!",
                "role": "user",
                "enabled": True,
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        service = self.user_service()
        service_module = importlib.import_module("services.user_service")
        worker_count = service_module.LOGIN_FAILURE_THRESHOLD
        barrier = Barrier(worker_count)

        def always_wrong(_hash: str, _password: str) -> bool:
            barrier.wait(timeout=5)
            return False

        def attempt_login() -> str:
            try:
                service.login("concurrent@example.com", "wrong-pass")
            except service_module.UserServiceError as exc:
                return exc.code
            return "unexpected_success"

        with patch.object(service, "verify_password", side_effect=always_wrong):
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                results = list(executor.map(lambda _: attempt_login(), range(worker_count)))

        self.assertEqual(results, ["invalid_credentials"] * worker_count)
        with service.Session() as session:
            row = session.get(service_module.LoginFailureLimitModel, "concurrent@example.com")
            self.assertIsNotNone(row)
            self.assertEqual(row.failed_attempts, worker_count)
            self.assertIsNotNone(row.locked_until)

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
                "image_quota": GGB_PER_IMAGE,
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
            return_value={"created": 1, "data": [{"b64_json": PNG_B64}]},
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
                "image_quota": GGB_PER_IMAGE,
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
        self.assertEqual(self.client.get("/api/auth/me", headers=headers).json()["user"]["image_quota"], GGB_PER_IMAGE)

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
                "image_quota": 2 * GGB_PER_IMAGE,
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
        self.assertEqual(target_after["image_quota"], 2 * GGB_PER_IMAGE)
        self.assertEqual(target_after["active_image_requests"], 0)

    def test_stale_reserved_image_usage_events_are_recovered_once(self) -> None:
        admin_token = self.create_admin()
        created = self.client.post(
            "/api/admin/users",
            headers=self.auth_headers(admin_token),
            json={
                "email": "quota-stale@example.com",
                "password": "UserPass123!",
                "role": "user",
                "enabled": True,
                "image_quota": 2 * GGB_PER_IMAGE,
                "image_concurrency": 2,
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        user_id = created.json()["item"]["id"]

        service = self.user_service()
        service_module = importlib.import_module("services.user_service")
        identity = {"id": user_id, "role": "user", "name": "quota-stale@example.com"}
        reservation = service.reserve_image_quota(identity, 2, "/api/image/generations")
        with service.Session() as session:
            event = session.get(service_module.ImageUsageEventModel, reservation.event_id)
            event.created_at = service_module.utc_now() - timedelta(hours=1)
            session.commit()

        before = service.get_user(user_id)
        self.assertEqual(before["image_quota"], 0)
        self.assertEqual(before["active_image_requests"], 1)

        recovered = service.recover_stale_image_quota_reservations(stale_after_seconds=0)
        recovered_again = service.recover_stale_image_quota_reservations(stale_after_seconds=0)

        after = service.get_user(user_id)
        self.assertEqual(recovered, 1)
        self.assertEqual(recovered_again, 0)
        self.assertEqual(after["image_quota"], 2 * GGB_PER_IMAGE)
        self.assertEqual(after["active_image_requests"], 0)

    def test_startup_stale_recovery_waits_six_hours_by_default_and_honors_env_override(self) -> None:
        admin_token = self.create_admin()
        created = self.client.post(
            "/api/admin/users",
            headers=self.auth_headers(admin_token),
            json={
                "email": "quota-startup-stale@example.com",
                "password": "UserPass123!",
                "role": "user",
                "enabled": True,
                "image_quota": 2 * GGB_PER_IMAGE,
                "image_concurrency": 2,
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        user_id = created.json()["item"]["id"]

        service = self.user_service()
        service_module = importlib.import_module("services.user_service")
        identity = {"id": user_id, "role": "user", "name": "quota-startup-stale@example.com"}
        reservation = service.reserve_image_quota(identity, 1, "/api/image/generations")
        with service.Session() as session:
            event = session.get(service_module.ImageUsageEventModel, reservation.event_id)
            event.created_at = service_module.utc_now() - timedelta(hours=1)
            session.commit()

        second_service = service_module.UserService(service.database_url)
        try:
            self.assertEqual(service.get_user(user_id)["image_quota"], GGB_PER_IMAGE)
            self.assertEqual(service.get_user(user_id)["active_image_requests"], 1)
        finally:
            second_service.engine.dispose()

        os.environ["GENAPI_STALE_IMAGE_QUOTA_SECONDS"] = "60"
        os.environ["GENAPI_STALE_IMAGE_QUOTA_RECOVERY_THROTTLE_SECONDS"] = "0"
        third_service = service_module.UserService(service.database_url)
        try:
            self.assertEqual(service.get_user(user_id)["image_quota"], 2 * GGB_PER_IMAGE)
            self.assertEqual(service.get_user(user_id)["active_image_requests"], 0)
        finally:
            third_service.engine.dispose()

    def test_empty_stale_recovery_attempts_are_throttled(self) -> None:
        service = self.user_service()
        service_module = importlib.import_module("services.user_service")
        service_module._LAST_STALE_RECOVERY_AT = None
        os.environ["GENAPI_STALE_IMAGE_QUOTA_RECOVERY_THROTTLE_SECONDS"] = "60"

        with patch.object(service, "recover_stale_image_quota_reservations", return_value=0) as recover_mock:
            first = service._recover_stale_image_quota_reservations_throttled()
            second = service._recover_stale_image_quota_reservations_throttled()

        self.assertEqual(first, 0)
        self.assertEqual(second, 0)
        self.assertEqual(recover_mock.call_count, 1)

    def test_reserve_image_quota_recovers_stale_reservation_without_restart(self) -> None:
        admin_token = self.create_admin()
        created = self.client.post(
            "/api/admin/users",
            headers=self.auth_headers(admin_token),
            json={
                "email": "quota-live-stale@example.com",
                "password": "UserPass123!",
                "role": "user",
                "enabled": True,
                "image_quota": GGB_PER_IMAGE,
                "image_concurrency": 1,
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        user_id = created.json()["item"]["id"]

        os.environ["GENAPI_STALE_IMAGE_QUOTA_RECOVERY_THROTTLE_SECONDS"] = "0"
        service = self.user_service()
        service_module = importlib.import_module("services.user_service")
        identity = {"id": user_id, "role": "user", "name": "quota-live-stale@example.com"}
        reservation = service.reserve_image_quota(identity, 1, "/api/image/generations")
        with service.Session() as session:
            event = session.get(service_module.ImageUsageEventModel, reservation.event_id)
            event.created_at = service_module.utc_now() - timedelta(hours=1)
            session.commit()

        os.environ["GENAPI_STALE_IMAGE_QUOTA_SECONDS"] = "60"
        second_reservation = service.reserve_image_quota(identity, 1, "/api/image/generations")

        after = service.get_user(user_id)
        self.assertTrue(second_reservation.event_id)
        self.assertEqual(after["image_quota"], 0)
        self.assertEqual(after["active_image_requests"], 1)
        with service.Session() as session:
            recovered = session.get(service_module.ImageUsageEventModel, reservation.event_id)
            current = session.get(service_module.ImageUsageEventModel, second_reservation.event_id)
        self.assertEqual(recovered.status, "recovered")
        self.assertEqual(current.status, "reserved")

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
                "image_quota": GGB_PER_IMAGE,
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
            return_value={"created": 1, "data": [{"b64_json": PNG_B64}]},
        ) as edit_handler:
            response = self.client.post(
                "/api/image/edits",
                headers=headers,
                data={"prompt": "make it brighter", "model": "gpt-image-2", "n": "1"},
                files={"image": ("reference.png", b"fake-image", "image/png")},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["data"][0]["b64_json"], PNG_B64)
        payload = edit_handler.call_args.args[0]
        self.assertEqual(payload["prompt"], "make it brighter")
        self.assertEqual(payload["model"], "gpt-image-2")
        self.assertEqual(payload["images"][0][1], "reference.png")

    def test_image_edit_read_unexpected_exception_refunds_quota_and_releases_concurrency(self) -> None:
        admin_token = self.create_admin()
        created = self.client.post(
            "/api/admin/users",
            headers=self.auth_headers(admin_token),
            json={
                "email": "edit-read-fail@example.com",
                "password": "UserPass123!",
                "role": "user",
                "enabled": True,
                "image_quota": GGB_PER_IMAGE,
                "image_concurrency": 1,
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        login = self.client.post(
            "/api/auth/login",
            json={"email": "edit-read-fail@example.com", "password": "UserPass123!"},
        )
        self.assertEqual(login.status_code, 200, login.text)
        headers = self.auth_headers(login.json()["token"])

        with patch("api.ai._read_limited_uploads", side_effect=RuntimeError("upload read failed")):
            with self.assertRaisesRegex(RuntimeError, "upload read failed"):
                self.client.post(
                    "/api/image/edits",
                    headers=headers,
                    data={"prompt": "edit", "model": "gpt-image-2", "n": "1"},
                    files={"image": ("reference.png", b"fake-image", "image/png")},
                )

        user = self.client.get("/api/auth/me", headers=headers).json()["user"]
        self.assertEqual(user["image_quota"], GGB_PER_IMAGE)
        self.assertEqual(user["active_image_requests"], 0)

    def test_image_edit_rejects_too_many_uploads_before_handler(self) -> None:
        admin_token = self.create_admin()

        with patch("api.ai.openai_v1_image_edit.handle", return_value={"data": []}) as edit_handler:
            response = self.client.post(
                "/api/image/edits",
                headers=self.auth_headers(admin_token),
                data={"prompt": "too many", "model": "gpt-image-2", "n": "1"},
                files=[
                    ("image", (f"reference-{index}.png", b"fake-image", "image/png"))
                    for index in range(5)
                ],
            )

        self.assertEqual(response.status_code, 400, response.text)
        edit_handler.assert_not_called()

    def test_image_edit_rejects_oversized_upload_before_handler(self) -> None:
        admin_token = self.create_admin()
        oversized = b"x" * (20 * 1024 * 1024 + 1)

        with patch("api.ai.openai_v1_image_edit.handle", return_value={"data": []}) as edit_handler:
            response = self.client.post(
                "/api/image/edits",
                headers=self.auth_headers(admin_token),
                data={"prompt": "too large", "model": "gpt-image-2", "n": "1"},
                files={"image": ("reference.png", oversized, "image/png")},
            )

        self.assertEqual(response.status_code, 413, response.text)
        edit_handler.assert_not_called()

    def test_image_edit_rejects_total_upload_size_before_handler(self) -> None:
        admin_token = self.create_admin()
        ai_module = sys.modules["api.ai"]

        with (
            patch.object(ai_module, "IMAGE_EDIT_MAX_TOTAL_SIZE_BYTES", 10, create=True),
            patch("api.ai.openai_v1_image_edit.handle", return_value={"data": []}) as edit_handler,
        ):
            response = self.client.post(
                "/api/image/edits",
                headers=self.auth_headers(admin_token),
                data={"prompt": "too large together", "model": "gpt-image-2", "n": "1"},
                files=[
                    ("image", ("first.png", b"123456", "image/png")),
                    ("image", ("second.png", b"123456", "image/png")),
                ],
            )

        self.assertEqual(response.status_code, 413, response.text)
        edit_handler.assert_not_called()

    def test_send_verify_code_sends_email_outside_route_thread(self) -> None:
        user_module = sys.modules["api.user_management"]
        original_create_code = user_module.user_service.create_email_verification_code
        threads: dict[str, int] = {}

        def tracked_create_code(email: str, purpose: str) -> str:
            threads["route"] = threading.get_ident()
            return original_create_code(email, purpose)

        def tracked_send(email: str, code: str) -> None:
            threads["send"] = threading.get_ident()

        with (
            patch.object(user_module.user_service, "create_email_verification_code", side_effect=tracked_create_code),
            patch.object(user_module.email_service, "send_verification_code", side_effect=tracked_send),
        ):
            response = self.client.post(
                "/api/auth/send-verify-code",
                json={"email": "threaded-send@example.com", "purpose": "register"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("route", threads)
        self.assertIn("send", threads)
        self.assertNotEqual(threads["route"], threads["send"])

    def test_login_runs_user_service_login_outside_route_thread(self) -> None:
        admin_token = self.create_admin()
        created = self.client.post(
            "/api/admin/users",
            headers=self.auth_headers(admin_token),
            json={
                "email": "threaded-login@example.com",
                "password": "UserPass123!",
                "role": "user",
                "enabled": True,
            },
        )
        self.assertEqual(created.status_code, 200, created.text)

        user_module = sys.modules["api.user_management"]
        original_login = user_module.user_service.login
        original_auth_payload = user_module.auth_payload
        threads: dict[str, int] = {}

        def tracked_login(email: str, password: str) -> dict[str, object]:
            threads["login"] = threading.get_ident()
            return original_login(email, password)

        def tracked_auth_payload(result: dict[str, object], app_version: str) -> dict[str, object]:
            threads["route"] = threading.get_ident()
            return original_auth_payload(result, app_version)

        with (
            patch.object(user_module.user_service, "login", side_effect=tracked_login),
            patch.object(user_module, "auth_payload", side_effect=tracked_auth_payload),
        ):
            response = self.client.post(
                "/api/auth/login",
                json={"email": "threaded-login@example.com", "password": "UserPass123!"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("route", threads)
        self.assertIn("login", threads)
        self.assertNotEqual(threads["route"], threads["login"])

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

    def test_admin_self_protection_normalizes_path_user_id(self) -> None:
        admin_token = self.create_admin()
        headers = self.auth_headers(admin_token)
        me = self.client.get("/api/auth/me", headers=headers)
        self.assertEqual(me.status_code, 200, me.text)
        admin_id = me.json()["user"]["id"]

        second_admin = self.client.post(
            "/api/admin/users",
            headers=headers,
            json={
                "email": "second-normalized-admin@example.com",
                "password": "AdminPass123!",
                "role": "admin",
                "enabled": True,
            },
        )
        self.assertEqual(second_admin.status_code, 200, second_admin.text)

        encoded_self_path = f"/api/admin/users/%20{admin_id}%20"
        demoted = self.client.patch(encoded_self_path, headers=headers, json={"role": "user"})
        self.assertEqual(demoted.status_code, 400, demoted.text)

        disabled = self.client.patch(encoded_self_path, headers=headers, json={"enabled": False})
        self.assertEqual(disabled.status_code, 400, disabled.text)

        deleted = self.client.delete(encoded_self_path, headers=headers)
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

    def test_role_change_invalidates_older_jwts(self) -> None:
        admin_token = self.create_admin()
        admin_headers = self.auth_headers(admin_token)
        created = self.client.post(
            "/api/admin/users",
            headers=admin_headers,
            json={
                "email": "promote@example.com",
                "password": "UserPass123!",
                "role": "user",
                "enabled": True,
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        user_id = created.json()["item"]["id"]

        login = self.client.post(
            "/api/auth/login",
            json={"email": "promote@example.com", "password": "UserPass123!"},
        )
        self.assertEqual(login.status_code, 200, login.text)
        old_headers = self.auth_headers(login.json()["token"])
        self.assertEqual(self.client.get("/api/admin/users", headers=old_headers).status_code, 403)

        promoted = self.client.patch(
            f"/api/admin/users/{user_id}",
            headers=admin_headers,
            json={"role": "admin"},
        )
        self.assertEqual(promoted.status_code, 200, promoted.text)

        self.assertEqual(self.client.get("/api/auth/me", headers=old_headers).status_code, 401)
        self.assertEqual(self.client.get("/api/admin/users", headers=old_headers).status_code, 401)

        new_login = self.client.post(
            "/api/auth/login",
            json={"email": "promote@example.com", "password": "UserPass123!"},
        )
        self.assertEqual(new_login.status_code, 200, new_login.text)
        self.assertEqual(new_login.json()["user"]["role"], "admin")

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

    def test_redeem_image_quota_code_awards_ggb(self) -> None:
        admin_token = self.create_admin()
        user = self.client.post(
            "/api/admin/users",
            headers=self.auth_headers(admin_token),
            json={
                "email": "redeem-ggb@example.com",
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
            json={"email": "redeem-ggb@example.com", "password": "UserPass123!"},
        )
        self.assertEqual(login.status_code, 200, login.text)
        headers = self.auth_headers(login.json()["token"])
        code = self.client.post(
            "/api/admin/redeem-codes/generate",
            headers=self.auth_headers(admin_token),
            json={"type": "image_quota", "value": 13, "count": 1},
        ).json()["codes"][0]["code"]

        redeemed = self.client.post("/api/redeem", headers=headers, json={"code": code})

        self.assertEqual(redeemed.status_code, 200, redeemed.text)
        self.assert_user_ggb(redeemed.json()["user"], 13)
        me = self.client.get("/api/auth/me", headers=headers)
        self.assertEqual(me.status_code, 200, me.text)
        self.assert_user_ggb(me.json()["user"], 13)

    def test_admin_user_legacy_image_quota_and_ggb_fields_are_consistent(self) -> None:
        admin_token = self.create_admin()
        headers = self.auth_headers(admin_token)
        created = self.client.post(
            "/api/admin/users",
            headers=headers,
            json={
                "email": "ggb-compat@example.com",
                "password": "UserPass123!",
                "role": "user",
                "enabled": True,
                "image_quota": 11,
                "image_concurrency": 1,
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        user_id = created.json()["item"]["id"]
        self.assert_user_ggb(created.json()["item"], 11)

        updated = self.client.patch(
            f"/api/admin/users/{user_id}",
            headers=headers,
            json={"ggb": 23},
        )

        self.assertEqual(updated.status_code, 200, updated.text)
        self.assert_user_ggb(updated.json()["item"], 23)
        listed = self.client.get("/api/admin/users", headers=headers)
        self.assertEqual(listed.status_code, 200, listed.text)
        listed_user = next(item for item in listed.json()["items"] if item["id"] == user_id)
        self.assert_user_ggb(listed_user, 23)

    def test_admin_create_endpoints_accept_ggb_aliases_without_legacy_fields(self) -> None:
        admin_token = self.create_admin()
        headers = self.auth_headers(admin_token)

        created_user = self.client.post(
            "/api/admin/users",
            headers=headers,
            json={
                "email": "ggb-alias-create@example.com",
                "password": "UserPass123!",
                "role": "user",
                "enabled": True,
                "ggb": 17,
                "image_concurrency": 1,
            },
        )
        self.assertEqual(created_user.status_code, 200, created_user.text)
        self.assert_user_ggb(created_user.json()["item"], 17)

        created_plan = self.client.post(
            "/api/admin/membership-plans",
            headers=headers,
            json={
                "name": "GGB 别名卡",
                "duration_days": 3,
                "period_days": 1,
                "period_ggb": 33,
                "enabled": True,
            },
        )
        self.assertEqual(created_plan.status_code, 200, created_plan.text)
        self.assertEqual(created_plan.json()["item"]["period_image_quota"], 33)
        self.assertEqual(created_plan.json()["item"]["period_ggb"], 33)

        generated_code = self.client.post(
            "/api/admin/redeem-codes/generate",
            headers=headers,
            json={"type": "image_quota", "ggb_value": 19, "count": 1},
        )
        self.assertEqual(generated_code.status_code, 200, generated_code.text)
        self.assertEqual(generated_code.json()["codes"][0]["value"], 19)
        self.assertEqual(generated_code.json()["codes"][0]["ggb_value"], 19)

        created_promo = self.client.post(
            "/api/admin/promo-codes",
            headers=headers,
            json={"code": "GGBALIAS", "ggb_amount": 21, "max_uses": 2},
        )
        self.assertEqual(created_promo.status_code, 200, created_promo.text)
        self.assertEqual(created_promo.json()["item"]["image_quota"], 21)
        self.assertEqual(created_promo.json()["item"]["ggb_amount"], 21)

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

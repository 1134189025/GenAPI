from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient


class AccountRouteThreadpoolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.old_config_file = os.environ.get("GENAPI_CONFIG_FILE")
        self.old_user_db = os.environ.get("GENAPI_USER_DATABASE_URL")
        self.old_jwt_secret = os.environ.get("JWT_SECRET")
        os.environ["GENAPI_CONFIG_FILE"] = str(base / "config.json")
        os.environ["GENAPI_USER_DATABASE_URL"] = f"sqlite:///{base / 'users.db'}"
        os.environ["JWT_SECRET"] = "unit-test-secret-with-at-least-32-bytes"
        self._clear_modules()
        app_module = importlib.import_module("api.app")
        self.client = TestClient(app_module.create_app())
        response = self.client.post(
            "/api/setup/admin",
            json={"email": "admin@example.com", "password": "AdminPass123!"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.headers = {"Authorization": f"Bearer {response.json()['token']}"}

    def tearDown(self) -> None:
        self.client.close()
        service_module = sys.modules.get("services.user_service")
        service = getattr(service_module, "user_service", None)
        engine = getattr(service, "engine", None)
        if engine is not None:
            engine.dispose()
        self._restore_env("GENAPI_CONFIG_FILE", self.old_config_file)
        self._restore_env("GENAPI_USER_DATABASE_URL", self.old_user_db)
        self._restore_env("JWT_SECRET", self.old_jwt_secret)
        self._clear_modules()
        self.tmp.cleanup()

    @staticmethod
    def _restore_env(key: str, value: str | None) -> None:
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value

    @staticmethod
    def _clear_modules() -> None:
        for module_name in list(sys.modules):
            if module_name == "api" or module_name.startswith("api.") or module_name.startswith("services."):
                sys.modules.pop(module_name, None)

    def test_accounts_list_runs_outside_route_thread(self) -> None:
        accounts_module = sys.modules["api.accounts"]
        original_require_admin = accounts_module.require_admin
        threads: dict[str, int] = {}

        def tracked_require_admin(authorization):
            threads["route"] = threading.get_ident()
            return original_require_admin(authorization)

        def tracked_list_accounts():
            threads["list"] = threading.get_ident()
            return []

        with (
            patch.object(accounts_module, "require_admin", side_effect=tracked_require_admin),
            patch.object(accounts_module.account_service, "list_accounts", side_effect=tracked_list_accounts),
        ):
            response = self.client.get("/api/accounts", headers=self.headers)

        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("route", threads)
        self.assertIn("list", threads)
        self.assertNotEqual(threads["route"], threads["list"])

    def test_accounts_refresh_runs_outside_route_thread(self) -> None:
        accounts_module = sys.modules["api.accounts"]
        original_require_admin = accounts_module.require_admin
        threads: dict[str, int] = {}

        def tracked_require_admin(authorization):
            threads["route"] = threading.get_ident()
            return original_require_admin(authorization)

        def tracked_refresh_accounts(access_tokens):
            threads["refresh"] = threading.get_ident()
            return {"refreshed": len(access_tokens), "errors": [], "items": []}

        with (
            patch.object(accounts_module, "require_admin", side_effect=tracked_require_admin),
            patch.object(accounts_module.account_service, "refresh_accounts", side_effect=tracked_refresh_accounts),
        ):
            response = self.client.post(
                "/api/accounts/refresh",
                headers=self.headers,
                json={"access_tokens": ["token-a"]},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("route", threads)
        self.assertIn("refresh", threads)
        self.assertNotEqual(threads["route"], threads["refresh"])

    def test_image_edit_checks_quota_before_reading_uploads(self) -> None:
        created = self.client.post(
            "/api/admin/users",
            headers=self.headers,
            json={
                "email": "no-quota@example.com",
                "password": "UserPass123!",
                "role": "user",
                "enabled": True,
                "image_quota": 0,
                "image_concurrency": 1,
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        login = self.client.post(
            "/api/auth/login",
            json={"email": "no-quota@example.com", "password": "UserPass123!"},
        )
        self.assertEqual(login.status_code, 200, login.text)
        user_headers = {"Authorization": f"Bearer {login.json()['token']}"}
        ai_module = sys.modules["api.ai"]

        with patch.object(ai_module, "_read_limited_uploads", side_effect=AssertionError("upload read should not run")) as read_mock:
            response = self.client.post(
                "/api/image/edits",
                headers=user_headers,
                data={"prompt": "edit", "model": "gpt-image-2", "n": "1"},
                files={"image": ("image.png", b"image-bytes", "image/png")},
            )

        self.assertEqual(response.status_code, 429, response.text)
        read_mock.assert_not_called()

    def test_image_edit_rejects_oversized_content_length_before_route_work(self) -> None:
        ai_module = sys.modules["api.ai"]

        with (
            patch.object(ai_module, "IMAGE_EDIT_MAX_TOTAL_SIZE_BYTES", 10, create=True),
            patch.object(ai_module, "IMAGE_EDIT_MAX_MULTIPART_OVERHEAD_BYTES", 0, create=True),
            patch.object(ai_module, "require_identity", side_effect=AssertionError("route should not run")) as auth_mock,
        ):
            response = self.client.post(
                "/api/image/edits",
                headers={
                    **self.headers,
                    "content-length": "11",
                    "content-type": "multipart/form-data; boundary=x",
                },
                content=b"--x--\r\n",
            )

        self.assertEqual(response.status_code, 413, response.text)
        auth_mock.assert_not_called()

    def test_register_runs_outside_route_thread(self) -> None:
        user_module = sys.modules["api.user_management"]
        threads: dict[str, int] = {}

        def tracked_register(**_kwargs):
            threads["register"] = threading.get_ident()
            return {
                "token": "jwt-token",
                "user": {
                    "id": "user-id",
                    "email": "thread-register@example.com",
                    "role": "user",
                    "enabled": True,
                    "image_quota": 0,
                    "image_concurrency": 1,
                },
            }

        def tracked_auth_payload(result, app_version):
            threads["route"] = threading.get_ident()
            return {"token": result["token"], "user": result["user"], "app_version": app_version}

        with (
            patch.object(user_module.user_service, "register", side_effect=tracked_register),
            patch.object(user_module, "auth_payload", side_effect=tracked_auth_payload),
        ):
            response = self.client.post(
                "/api/auth/register",
                json={
                    "email": "thread-register@example.com",
                    "password": "UserPass123!",
                    "verification_code": "123456",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("route", threads)
        self.assertIn("register", threads)
        self.assertNotEqual(threads["route"], threads["register"])


class LogServiceBoundedListTests(unittest.TestCase):
    def test_list_does_not_read_entire_file_before_filtering_and_limiting(self) -> None:
        log_module = importlib.import_module("services.log_service")
        path = Path(tempfile.mkdtemp()) / "logs.jsonl"
        service = log_module.LogService(path)
        rows = [
            {"time": "2026-04-27 10:00:00", "type": "account", "summary": "old"},
            {"time": "2026-04-28 10:00:00", "type": "call", "summary": "first"},
            {"time": "2026-04-29 10:00:00", "type": "call", "summary": "second"},
        ]
        path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

        with patch.object(Path, "read_text", side_effect=AssertionError("read_text loads the full log")):
            items = service.list(type="call", limit=2)

        self.assertEqual([item["summary"] for item in items], ["second", "first"])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import importlib
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from fastapi.testclient import TestClient


class SettingsImageCacheAPITests(unittest.TestCase):
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
        if self.old_config_file is None:
            os.environ.pop("GENAPI_CONFIG_FILE", None)
        else:
            os.environ["GENAPI_CONFIG_FILE"] = self.old_config_file
        if self.old_user_db is None:
            os.environ.pop("GENAPI_USER_DATABASE_URL", None)
        else:
            os.environ["GENAPI_USER_DATABASE_URL"] = self.old_user_db
        if self.old_jwt_secret is None:
            os.environ.pop("JWT_SECRET", None)
        else:
            os.environ["JWT_SECRET"] = self.old_jwt_secret
        self._clear_modules()
        self.tmp.cleanup()

    @staticmethod
    def _clear_modules() -> None:
        for module_name in list(sys.modules):
            if module_name == "api" or module_name.startswith("api.") or module_name.startswith("services."):
                sys.modules.pop(module_name, None)

    def test_settings_response_includes_image_cache_status(self) -> None:
        response = self.client.get("/api/settings", headers=self.headers)

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["config"]["image_cache_max_size_mb"], 10240)
        self.assertEqual(payload["config"]["image_cache_auto_delete_enabled"], True)
        self.assertEqual(payload["image_cache"]["max_size_bytes"], 10240 * 1024 * 1024)
        self.assertEqual(payload["image_cache"]["auto_delete_enabled"], True)
        self.assertIn("total_size_bytes", payload["image_cache"])
        self.assertIn("file_count", payload["image_cache"])

    def test_settings_status_scan_runs_outside_route_thread(self) -> None:
        system_module = sys.modules["api.system"]
        config = system_module.config
        original_get = config.get
        original_status = config.get_image_cache_status
        threads: dict[str, int] = {}

        def tracked_get():
            threads["get"] = threading.get_ident()
            return original_get()

        def tracked_status():
            threads["status"] = threading.get_ident()
            return original_status()

        config.get = tracked_get
        config.get_image_cache_status = tracked_status
        try:
            response = self.client.get("/api/settings", headers=self.headers)
        finally:
            config.get = original_get
            config.get_image_cache_status = original_status

        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("get", threads)
        self.assertIn("status", threads)
        self.assertNotEqual(threads["get"], threads["status"])

    def test_saving_image_cache_settings_returns_normalized_status(self) -> None:
        response = self.client.post(
            "/api/settings",
            headers=self.headers,
            json={
                "image_retention_days": "7",
                "image_cache_max_size_mb": "2",
                "image_cache_auto_delete_enabled": False,
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["config"]["image_retention_days"], 7)
        self.assertEqual(payload["config"]["image_cache_max_size_mb"], 2)
        self.assertEqual(payload["config"]["image_cache_auto_delete_enabled"], False)
        self.assertEqual(payload["image_cache"]["max_size_bytes"], 2 * 1024 * 1024)
        self.assertEqual(payload["image_cache"]["auto_delete_enabled"], False)

    def test_settings_cleanup_runs_outside_route_thread(self) -> None:
        system_module = sys.modules["api.system"]
        config = system_module.config
        original_update = config.update
        original_cleanup = config.cleanup_old_images
        original_status = config.get_image_cache_status
        threads: dict[str, int] = {}

        def tracked_update(data):
            threads["update"] = threading.get_ident()
            return original_update(data)

        def tracked_cleanup(*args, **kwargs):
            threads["cleanup"] = threading.get_ident()
            return original_cleanup(*args, **kwargs)

        def tracked_status():
            threads["status"] = threading.get_ident()
            return original_status()

        config.update = tracked_update
        config.cleanup_old_images = tracked_cleanup
        config.get_image_cache_status = tracked_status
        try:
            response = self.client.post(
                "/api/settings",
                headers=self.headers,
                json={"image_retention_days": "14"},
            )
        finally:
            config.update = original_update
            config.cleanup_old_images = original_cleanup
            config.get_image_cache_status = original_status

        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("update", threads)
        self.assertIn("cleanup", threads)
        self.assertIn("status", threads)
        self.assertNotEqual(threads["update"], threads["cleanup"])
        self.assertNotEqual(threads["update"], threads["status"])


if __name__ == "__main__":
    unittest.main()

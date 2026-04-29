from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient


class UpdateAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.old_config_file = os.environ.get("GENAPI_CONFIG_FILE")
        self.old_user_db = os.environ.get("GENAPI_USER_DATABASE_URL")
        self.old_jwt_secret = os.environ.get("JWT_SECRET")
        self.old_enable_updater = os.environ.get("GENAPI_ENABLE_WEB_UPDATER")
        self.old_compose_dir = os.environ.get("GENAPI_UPDATE_COMPOSE_DIR")
        os.environ["GENAPI_CONFIG_FILE"] = str(base / "config.json")
        os.environ["GENAPI_USER_DATABASE_URL"] = f"sqlite:///{base / 'users.db'}"
        os.environ["JWT_SECRET"] = "unit-test-secret-with-at-least-32-bytes"
        os.environ["GENAPI_ENABLE_WEB_UPDATER"] = "true"
        os.environ["GENAPI_UPDATE_COMPOSE_DIR"] = str(base)
        (base / "docker-compose.yml").write_text("services:\n  app:\n    image: genapi:test\n", encoding="utf-8")
        self._clear_modules()
        app_module = importlib.import_module("api.app")
        update_module = importlib.import_module("api.update")
        update_module.DATA_DIR = base / "data"
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
        self._restore_env("GENAPI_ENABLE_WEB_UPDATER", self.old_enable_updater)
        self._restore_env("GENAPI_UPDATE_COMPOSE_DIR", self.old_compose_dir)
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

    def test_update_status_is_admin_only_and_exposes_preflight(self) -> None:
        unauthenticated = self.client.get("/api/admin/update/status")
        self.assertEqual(unauthenticated.status_code, 401)

        update_module = sys.modules["api.update"]
        original_build_status = update_module.build_release_status
        original_executor = update_module.DockerUpdateExecutor
        class FakeExecutor:
            def __init__(self, data_dir, settings):
                self.data_dir = data_dir
                self.settings = settings

            def preflight(self):
                return {"ok": True, "errors": [], "warnings": []}

        try:
            update_module.build_release_status = lambda **kwargs: {
                "enabled": True,
                "mode": "docker-compose",
                "current_version": "0.1.3",
                "latest_version": "0.1.4",
                "latest_tag": "v0.1.4",
                "release_url": "https://github.com/owner/project/releases/tag/v0.1.4",
                "update_available": True,
            }
            update_module.DockerUpdateExecutor = FakeExecutor
            response = self.client.get("/api/admin/update/status", headers=self.headers)
        finally:
            update_module.build_release_status = original_build_status
            update_module.DockerUpdateExecutor = original_executor

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertTrue(payload["status"]["update_available"])
        self.assertTrue(payload["preflight"]["ok"])
        self.assertIn("jobs", payload)

    def test_start_update_creates_job_and_invokes_executor(self) -> None:
        update_module = sys.modules["api.update"]
        started: dict[str, object] = {}

        class FakeExecutor:
            def __init__(self, data_dir, settings):
                self.data_dir = data_dir
                self.settings = settings

            def preflight(self):
                return {"ok": True, "errors": [], "warnings": []}

            def start(self, **kwargs):
                started.update(kwargs)

        original_build_status = update_module.build_release_status
        original_executor = update_module.DockerUpdateExecutor
        try:
            update_module.build_release_status = lambda **kwargs: {
                "enabled": True,
                "mode": "docker-compose",
                "current_version": "0.1.3",
                "latest_version": "0.1.4",
                "latest_tag": "v0.1.4",
                "release_url": "https://github.com/owner/project/releases/tag/v0.1.4",
                "update_available": True,
            }
            update_module.DockerUpdateExecutor = FakeExecutor
            response = self.client.post("/api/admin/update/start", headers=self.headers)
        finally:
            update_module.build_release_status = original_build_status
            update_module.DockerUpdateExecutor = original_executor

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["job"]["target_version"], "0.1.4")
        self.assertEqual(started["target_tag"], "v0.1.4")
        self.assertEqual(started["target_version"], "0.1.4")


if __name__ == "__main__":
    unittest.main()

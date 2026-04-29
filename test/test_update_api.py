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
        original_executor = getattr(update_module, "BinaryUpdateExecutor", None)
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
            update_module.BinaryUpdateExecutor = FakeExecutor
            response = self.client.get("/api/admin/update/status", headers=self.headers)
        finally:
            update_module.build_release_status = original_build_status
            if original_executor is None:
                delattr(update_module, "BinaryUpdateExecutor")
            else:
                update_module.BinaryUpdateExecutor = original_executor

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertTrue(payload["status"]["update_available"])
        self.assertTrue(payload["preflight"]["ok"])
        self.assertIn("jobs", payload)

    def test_system_version_and_check_updates_are_admin_only(self) -> None:
        self.assertEqual(self.client.get("/api/admin/system/version").status_code, 401)
        self.assertEqual(self.client.get("/api/admin/system/check-updates").status_code, 401)

        update_module = sys.modules["api.update"]
        original_build_status = update_module.build_release_status
        captured: dict[str, object] = {}
        try:
            def fake_build_status(**kwargs):
                captured.update(kwargs)
                return {
                    "enabled": True,
                    "current_version": kwargs["current_version"],
                    "latest_version": "0.1.6",
                    "latest_tag": "v0.1.6",
                    "release_url": "https://github.com/owner/project/releases/tag/v0.1.6",
                    "update_available": True,
                }

            update_module.build_release_status = fake_build_status

            version_response = self.client.get("/api/admin/system/version", headers=self.headers)
            updates_response = self.client.get("/api/admin/system/check-updates?force=true", headers=self.headers)
        finally:
            update_module.build_release_status = original_build_status

        self.assertEqual(version_response.status_code, 200, version_response.text)
        self.assertIn("version", version_response.json())
        self.assertEqual(updates_response.status_code, 200, updates_response.text)
        self.assertEqual(updates_response.json()["latest_version"], "0.1.6")
        self.assertTrue(captured["force"])

    def test_check_updates_merges_preflight_into_can_update(self) -> None:
        update_module = sys.modules["api.update"]

        class FakeExecutor:
            def __init__(self, data_dir, settings):
                self.data_dir = data_dir
                self.settings = settings

            def preflight(self, release_info=None):
                return {"ok": False, "errors": ["binary directory is not writable"], "warnings": []}

        original_build_status = update_module.build_release_status
        original_executor = getattr(update_module, "BinaryUpdateExecutor", None)
        try:
            update_module.build_release_status = lambda **kwargs: {
                "enabled": True,
                "can_update": True,
                "mode": "systemd-binary",
                "current_version": "0.1.5",
                "latest_version": "0.1.6",
                "latest_tag": "v0.1.6",
                "release_url": "https://github.com/owner/project/releases/tag/v0.1.6",
                "release_info": {"assets": [{"name": "genapi_0.1.6_linux_amd64.tar.gz"}]},
                "update_available": True,
            }
            update_module.BinaryUpdateExecutor = FakeExecutor
            response = self.client.get("/api/admin/system/check-updates", headers=self.headers)
        finally:
            update_module.build_release_status = original_build_status
            if original_executor is None:
                delattr(update_module, "BinaryUpdateExecutor")
            else:
                update_module.BinaryUpdateExecutor = original_executor

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertFalse(payload["can_update"])
        self.assertIn("binary directory is not writable", payload["disabled_reason"])

    def test_system_update_creates_job_invokes_executor_and_legacy_start_wraps_job(self) -> None:
        update_module = sys.modules["api.update"]
        performed: list[dict[str, object]] = []

        class FakeExecutor:
            def __init__(self, data_dir, settings):
                self.data_dir = data_dir
                self.settings = settings

            def preflight(self):
                return {"ok": True, "errors": [], "warnings": []}

            def perform_update(self, **kwargs):
                performed.append(kwargs)

        original_build_status = update_module.build_release_status
        original_executor = getattr(update_module, "BinaryUpdateExecutor", None)
        try:
            update_module.build_release_status = lambda **kwargs: {
                "enabled": True,
                "mode": "binary",
                "current_version": "0.1.3",
                "latest_version": "0.1.4",
                "latest_tag": "v0.1.4",
                "release_url": "https://github.com/owner/project/releases/tag/v0.1.4",
                "update_available": True,
            }
            update_module.BinaryUpdateExecutor = FakeExecutor
            response = self.client.post("/api/admin/system/update", headers=self.headers)
            legacy_response = self.client.post("/api/admin/update/start", headers=self.headers)
        finally:
            update_module.build_release_status = original_build_status
            if original_executor is None:
                delattr(update_module, "BinaryUpdateExecutor")
            else:
                update_module.BinaryUpdateExecutor = original_executor

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertTrue(payload["need_restart"])
        self.assertEqual(payload["job"]["target_version"], "0.1.4")
        self.assertEqual(payload["job"]["status"], "succeeded")
        self.assertEqual(performed[0]["target_tag"], "v0.1.4")
        self.assertEqual(performed[0]["target_version"], "0.1.4")
        self.assertEqual(legacy_response.status_code, 200, legacy_response.text)
        self.assertEqual(legacy_response.json()["job"]["status"], "succeeded")

    def test_system_update_passes_release_info_to_real_executor_signature(self) -> None:
        update_module = sys.modules["api.update"]
        captured: dict[str, object] = {}
        release_info = {"assets": [{"name": "genapi_0.1.6_linux_amd64.tar.gz"}]}

        class FakeExecutor:
            def __init__(self, data_dir, settings):
                self.data_dir = data_dir
                self.settings = settings

            def preflight(self):
                return {"ok": True, "errors": [], "warnings": []}

            def perform_update(self, target_tag, target_version, release_info):
                captured["target_tag"] = target_tag
                captured["target_version"] = target_version
                captured["release_info"] = release_info

        original_build_status = update_module.build_release_status
        original_executor = getattr(update_module, "BinaryUpdateExecutor", None)
        try:
            update_module.build_release_status = lambda **kwargs: {
                "enabled": True,
                "can_update": True,
                "mode": "systemd-binary",
                "current_version": "0.1.5",
                "latest_version": "0.1.6",
                "latest_tag": "v0.1.6",
                "release_url": "https://github.com/owner/project/releases/tag/v0.1.6",
                "release_info": release_info,
                "update_available": True,
            }
            update_module.BinaryUpdateExecutor = FakeExecutor
            response = self.client.post("/api/admin/system/update", headers=self.headers)
        finally:
            update_module.build_release_status = original_build_status
            if original_executor is None:
                delattr(update_module, "BinaryUpdateExecutor")
            else:
                update_module.BinaryUpdateExecutor = original_executor

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(captured["target_tag"], "v0.1.6")
        self.assertEqual(captured["target_version"], "0.1.6")
        self.assertEqual(captured["release_info"], release_info)

    def test_system_update_rejects_no_update_preflight_failure_and_active_job(self) -> None:
        update_module = sys.modules["api.update"]

        class FakeExecutor:
            preflight_result = {"ok": True, "errors": [], "warnings": []}

            def __init__(self, data_dir, settings):
                self.data_dir = data_dir
                self.settings = settings

            def preflight(self):
                return self.preflight_result

            def perform_update(self, **kwargs):
                return None

        original_build_status = update_module.build_release_status
        original_executor = getattr(update_module, "BinaryUpdateExecutor", None)
        try:
            update_module.BinaryUpdateExecutor = FakeExecutor
            update_module.build_release_status = lambda **kwargs: {
                "enabled": True,
                "mode": "binary",
                "current_version": "0.1.4",
                "latest_version": "0.1.4",
                "latest_tag": "v0.1.4",
                "release_url": "https://github.com/owner/project/releases/tag/v0.1.4",
                "update_available": False,
            }
            no_update = self.client.post("/api/admin/system/update", headers=self.headers)

            update_module.build_release_status = lambda **kwargs: {
                "enabled": True,
                "mode": "binary",
                "current_version": "0.1.3",
                "latest_version": "0.1.4",
                "latest_tag": "v0.1.4",
                "release_url": "https://github.com/owner/project/releases/tag/v0.1.4",
                "update_available": True,
            }
            FakeExecutor.preflight_result = {"ok": False, "errors": ["missing binary"], "warnings": []}
            preflight_failure = self.client.post("/api/admin/system/update", headers=self.headers)

            FakeExecutor.preflight_result = {"ok": True, "errors": [], "warnings": []}
            store = update_module.UpdateJobStore(update_module.DATA_DIR)
            store.create_if_idle(
                target_version="0.1.4",
                target_tag="v0.1.4",
                release_url="https://github.com/owner/project/releases/tag/v0.1.4",
                actor_id="unit-test",
            )
            active_job = self.client.post("/api/admin/system/update", headers=self.headers)
        finally:
            update_module.build_release_status = original_build_status
            if original_executor is None:
                delattr(update_module, "BinaryUpdateExecutor")
            else:
                update_module.BinaryUpdateExecutor = original_executor

        self.assertEqual(no_update.status_code, 400, no_update.text)
        self.assertEqual(preflight_failure.status_code, 400, preflight_failure.text)
        self.assertEqual(active_job.status_code, 409, active_job.text)

    def test_system_rollback_and_restart_delegate_to_binary_executor(self) -> None:
        update_module = sys.modules["api.update"]
        calls: list[str] = []

        class FakeExecutor:
            def __init__(self, data_dir, settings):
                self.data_dir = data_dir
                self.settings = settings

            def preflight(self, allow_pending_current=False):
                calls.append(f"preflight:{allow_pending_current}")
                return {"ok": True, "errors": [], "warnings": []}

            def rollback(self):
                calls.append("rollback")

            def restart(self):
                calls.append("restart")

        original_executor = getattr(update_module, "BinaryUpdateExecutor", None)
        try:
            update_module.BinaryUpdateExecutor = FakeExecutor
            rollback_response = self.client.post("/api/admin/system/rollback", headers=self.headers)
            restart_response = self.client.post("/api/admin/system/restart", headers=self.headers)
        finally:
            if original_executor is None:
                delattr(update_module, "BinaryUpdateExecutor")
            else:
                update_module.BinaryUpdateExecutor = original_executor

        self.assertEqual(rollback_response.status_code, 200, rollback_response.text)
        self.assertTrue(rollback_response.json()["need_restart"])
        self.assertEqual(restart_response.status_code, 200, restart_response.text)
        self.assertEqual(calls, ["preflight:True", "rollback", "preflight:True", "restart"])

    def test_system_rollback_and_restart_reject_failed_preflight(self) -> None:
        update_module = sys.modules["api.update"]

        class FakeExecutor:
            def __init__(self, data_dir, settings):
                self.data_dir = data_dir
                self.settings = settings

            def preflight(self, allow_pending_current=False):
                return {"ok": False, "errors": ["GENAPI_DEPLOYMENT_MODE must be systemd-binary"], "warnings": []}

            def rollback(self):
                raise AssertionError("rollback should not run")

            def restart(self):
                raise AssertionError("restart should not run")

        original_executor = getattr(update_module, "BinaryUpdateExecutor", None)
        try:
            update_module.BinaryUpdateExecutor = FakeExecutor
            rollback_response = self.client.post("/api/admin/system/rollback", headers=self.headers)
            restart_response = self.client.post("/api/admin/system/restart", headers=self.headers)
        finally:
            if original_executor is None:
                delattr(update_module, "BinaryUpdateExecutor")
            else:
                update_module.BinaryUpdateExecutor = original_executor

        self.assertEqual(rollback_response.status_code, 400, rollback_response.text)
        self.assertIn("rollback preflight failed", rollback_response.text)
        self.assertEqual(restart_response.status_code, 400, restart_response.text)
        self.assertIn("restart preflight failed", restart_response.text)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient


class StaticSpaRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.web_dist = base / "web_dist"
        self.web_dist.mkdir(parents=True)
        (self.web_dist / "index.html").write_text("<!doctype html><title>Genapi</title>", encoding="utf-8")
        next_static = self.web_dist / "_next" / "static" / "chunks"
        next_static.mkdir(parents=True)
        (next_static / "current.js").write_text("console.log('current')", encoding="utf-8")
        self.old_config_file = os.environ.get("GENAPI_CONFIG_FILE")
        self.old_user_db = os.environ.get("GENAPI_USER_DATABASE_URL")
        self.old_jwt_secret = os.environ.get("JWT_SECRET")
        os.environ["GENAPI_CONFIG_FILE"] = str(base / "config.json")
        os.environ["GENAPI_USER_DATABASE_URL"] = f"sqlite:///{base / 'users.db'}"
        os.environ["JWT_SECRET"] = "unit-test-secret-with-at-least-32-bytes"
        self._clear_modules()
        support_module = importlib.import_module("api.support")
        support_module.WEB_DIST_DIR = self.web_dist
        app_module = importlib.import_module("api.app")
        self.client = TestClient(app_module.create_app())

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

    def test_static_spa_routes_accept_head_requests(self) -> None:
        for path in ("/login/", "/setup/", "/register/", "/admin/users/", "/image/"):
            with self.subTest(path=path):
                response = self.client.head(path)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertIn("no-cache", response.headers.get("cache-control", ""))

    def test_stale_next_js_chunk_refreshes_instead_of_hard_404(self) -> None:
        response = self.client.get("/_next/static/chunks/missing-old.js")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("text/javascript", response.headers.get("content-type", ""))
        self.assertEqual(response.headers.get("cache-control"), "no-store")
        self.assertIn("location.reload", response.text)

        existing = self.client.get("/_next/static/chunks/current.js")
        self.assertEqual(existing.status_code, 200, existing.text)
        self.assertIn("max-age=31536000", existing.headers.get("cache-control", ""))

        missing_css = self.client.get("/_next/static/chunks/missing-old.css")
        self.assertEqual(missing_css.status_code, 404, missing_css.text)

    def test_missing_extension_assets_and_api_roots_do_not_fallback(self) -> None:
        for path in ("/assets/missing.css", "/missing.png", "/favicon.ico"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 404, response.text)
                self.assertNotIn("text/html", response.headers.get("content-type", ""))

        for path in ("/api", "/api/", "/v1", "/v1/", "/auth/login"):
            with self.subTest(path=path):
                response = self.client.head(path)
                self.assertEqual(response.status_code, 404, response.text)

    def test_web_dist_resolves_from_pyinstaller_resource_dir(self) -> None:
        support_module = sys.modules["api.support"]
        old_frozen = getattr(sys, "frozen", None)
        old_meipass = getattr(sys, "_MEIPASS", None)
        with tempfile.TemporaryDirectory() as tmp_dir:
            resource_dir = Path(tmp_dir)
            try:
                sys.frozen = True
                sys._MEIPASS = str(resource_dir)

                self.assertEqual(support_module._resolve_web_dist_dir(), resource_dir / "web_dist")
            finally:
                if old_frozen is None:
                    try:
                        delattr(sys, "frozen")
                    except AttributeError:
                        pass
                else:
                    sys.frozen = old_frozen
                if old_meipass is None:
                    try:
                        delattr(sys, "_MEIPASS")
                    except AttributeError:
                        pass
                else:
                    sys._MEIPASS = old_meipass


if __name__ == "__main__":
    unittest.main()

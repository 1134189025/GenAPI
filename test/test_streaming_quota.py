from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class StreamingQuotaSettlementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        db_path = Path(self.tmp.name) / "users.db"
        self.old_user_db = os.environ.get("GENAPI_USER_DATABASE_URL")
        self.old_jwt_secret = os.environ.get("JWT_SECRET")
        os.environ["GENAPI_USER_DATABASE_URL"] = f"sqlite:///{db_path}"
        os.environ["JWT_SECRET"] = "unit-test-secret-with-at-least-32-bytes"

        for module_name in list(sys.modules):
            if module_name in {
                "services.log_service",
                "services.quota_service",
                "services.user_service",
            }:
                sys.modules.pop(module_name, None)

        self.user_service_module = importlib.import_module("services.user_service")
        self.log_service_module = importlib.import_module("services.log_service")
        self.user_service = self.user_service_module.user_service

    def tearDown(self) -> None:
        engine = getattr(self.user_service, "engine", None)
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
        self.tmp.cleanup()

    def test_failed_stream_with_delivered_image_refunds_only_undelivered_quota(self) -> None:
        user = self.user_service.create_user(
            email="stream-partial@example.com",
            password="UserPass123!",
            image_quota=2,
            image_concurrency=1,
        )
        reservation = self.user_service.reserve_image_quota(user, 2, "/api/image/generations")
        self.assertEqual(self.user_service.get_user(user["id"])["image_quota"], 0)
        self.assertEqual(self.user_service.get_user(user["id"])["active_image_requests"], 1)

        def failing_items():
            yield {"created": 1, "data": [{"b64_json": "generated-image"}]}
            raise RuntimeError("upstream stream failed")

        call = self.log_service_module.LoggedCall(
            user,
            "/api/image/generations",
            "gpt-image-2",
            "stream-test",
        )
        with patch.object(self.log_service_module.log_service, "add"):
            stream = call.stream(failing_items(), quota_reservation=reservation)
            self.assertEqual(next(stream)["data"][0]["b64_json"], "generated-image")
            with self.assertRaisesRegex(RuntimeError, "upstream stream failed"):
                next(stream)

        refreshed = self.user_service.get_user(user["id"])
        self.assertEqual(refreshed["image_quota"], 1)
        self.assertEqual(refreshed["active_image_requests"], 0)

    def test_failed_stream_without_delivered_images_refunds_full_reserved_quota(self) -> None:
        user = self.user_service.create_user(
            email="stream-empty@example.com",
            password="UserPass123!",
            image_quota=2,
            image_concurrency=1,
        )
        reservation = self.user_service.reserve_image_quota(user, 2, "/api/image/generations")

        def failing_items():
            raise RuntimeError("upstream stream failed before image")
            yield

        call = self.log_service_module.LoggedCall(
            user,
            "/api/image/generations",
            "gpt-image-2",
            "stream-test",
        )
        with patch.object(self.log_service_module.log_service, "add"):
            stream = call.stream(failing_items(), quota_reservation=reservation)
            with self.assertRaisesRegex(RuntimeError, "upstream stream failed before image"):
                next(stream)

        refreshed = self.user_service.get_user(user["id"])
        self.assertEqual(refreshed["image_quota"], 2)
        self.assertEqual(refreshed["active_image_requests"], 0)

    def test_failed_stream_with_progress_url_does_not_charge_image_quota(self) -> None:
        user = self.user_service.create_user(
            email="stream-progress-url@example.com",
            password="UserPass123!",
            image_quota=2,
            image_concurrency=1,
        )
        reservation = self.user_service.reserve_image_quota(user, 2, "/api/image/generations")

        def failing_items():
            yield {
                "object": "image.generation.chunk",
                "progress_text": "working",
                "metadata": {"url": "https://progress.example/status"},
                "data": [],
            }
            raise RuntimeError("upstream stream failed before image")

        call = self.log_service_module.LoggedCall(
            user,
            "/api/image/generations",
            "gpt-image-2",
            "stream-test",
        )
        with patch.object(self.log_service_module.log_service, "add"):
            stream = call.stream(failing_items(), quota_reservation=reservation)
            self.assertEqual(next(stream)["progress_text"], "working")
            with self.assertRaisesRegex(RuntimeError, "upstream stream failed before image"):
                next(stream)

        refreshed = self.user_service.get_user(user["id"])
        self.assertEqual(refreshed["image_quota"], 2)
        self.assertEqual(refreshed["active_image_requests"], 0)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from sqlalchemy import inspect


class DailyCheckinTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "users.db"
        self.old_user_db = os.environ.get("GENAPI_USER_DATABASE_URL")
        self.old_jwt_secret = os.environ.get("JWT_SECRET")
        os.environ["GENAPI_USER_DATABASE_URL"] = f"sqlite:///{self.db_path}"
        os.environ["JWT_SECRET"] = "unit-test-secret-with-at-least-32-bytes"
        self._clear_modules()
        app_module = importlib.import_module("api.app")
        self.client = TestClient(app_module.create_app())
        self.user_service_module = importlib.import_module("services.user_service")
        self.user_service = self.user_service_module.user_service

    def tearDown(self) -> None:
        self.client.close()
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
        self._clear_modules()
        self.tmp.cleanup()

    @staticmethod
    def _clear_modules() -> None:
        for module_name in list(sys.modules):
            if module_name == "api" or module_name.startswith("api.") or module_name.startswith("services."):
                sys.modules.pop(module_name, None)

    @staticmethod
    def auth_headers(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    @staticmethod
    def local_noon_utc(year: int, month: int, day: int) -> datetime:
        local = datetime(year, month, day, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        return local.astimezone(timezone.utc)

    def create_admin(self) -> str:
        response = self.client.post(
            "/api/setup/admin",
            json={"email": "admin@example.com", "password": "AdminPass123!"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return str(response.json()["token"])

    def create_user(
        self,
        admin_token: str,
        *,
        email: str = "checkin-user@example.com",
        image_quota: int = 0,
    ) -> tuple[str, dict[str, str]]:
        created = self.client.post(
            "/api/admin/users",
            headers=self.auth_headers(admin_token),
            json={
                "email": email,
                "password": "UserPass123!",
                "role": "user",
                "enabled": True,
                "image_quota": image_quota,
                "image_concurrency": 1,
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        login = self.client.post("/api/auth/login", json={"email": email, "password": "UserPass123!"})
        self.assertEqual(login.status_code, 200, login.text)
        return str(created.json()["item"]["id"]), self.auth_headers(str(login.json()["token"]))

    def checkin_rows(self, user_id: str) -> list[object]:
        with self.user_service.Session() as session:
            return (
                session.query(self.user_service_module.DailyCheckinModel)
                .filter_by(user_id=user_id)
                .order_by(self.user_service_module.DailyCheckinModel.checkin_date)
                .all()
            )

    def assert_user_ggb(self, user: dict[str, object], expected_ggb: int) -> None:
        self.assertIn("ggb", user)
        self.assertEqual(user["ggb"], expected_ggb)
        self.assertEqual(user["image_quota"], expected_ggb)

    def test_defaults_and_daily_checkins_table_are_created_in_users_db(self) -> None:
        admin_token = self.create_admin()

        response = self.client.get("/api/admin/auth-settings", headers=self.auth_headers(admin_token))
        self.assertEqual(response.status_code, 200, response.text)
        settings = response.json()["settings"]
        self.assertEqual(settings["checkin_enabled"], True)
        self.assertEqual(settings["checkin_daily_image_quota"], 5)
        self.assertEqual(settings["checkin_streak_bonus_enabled"], True)
        self.assertEqual(settings["checkin_streak_bonus_days"], 7)
        self.assertEqual(settings["checkin_streak_bonus_image_quota"], 15)
        self.assertEqual(settings["checkin_timezone"], "Asia/Shanghai")

        inspector = inspect(self.user_service.engine)
        self.assertIn("daily_checkins", inspector.get_table_names())
        columns = {column["name"] for column in inspector.get_columns("daily_checkins")}
        self.assertGreaterEqual(
            columns,
            {"id", "user_id", "checkin_date", "reward_image_quota", "streak_days", "created_at"},
        )
        unique_columns = {
            tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints("daily_checkins")
        }
        self.assertIn(("user_id", "checkin_date"), unique_columns)

    def test_first_checkin_increases_regular_quota_and_updates_status(self) -> None:
        admin_token = self.create_admin()
        user_id, headers = self.create_user(admin_token, image_quota=2)
        now = self.local_noon_utc(2026, 5, 1)

        with patch("services.user_service.utc_now", return_value=now):
            status = self.client.get("/api/checkin/status", headers=headers)
            self.assertEqual(status.status_code, 200, status.text)
            self.assertEqual(status.json()["checked_in_today"], False)
            self.assertEqual(status.json()["streak_days"], 0)

            checked_in = self.client.post("/api/checkin", headers=headers)

            self.assertEqual(checked_in.status_code, 200, checked_in.text)
            payload = checked_in.json()
            self.assertEqual(payload["already_checked_in"], False)
            self.assertEqual(payload["reward_image_quota"], 5)
            self.assertEqual(payload["streak_days"], 1)
            self.assertEqual(payload["checkin_date"], "2026-05-01")
            self.assertEqual(payload["user"]["image_quota"], 7)

            status_after = self.client.get("/api/checkin/status", headers=headers)
            self.assertEqual(status_after.status_code, 200, status_after.text)
            self.assertEqual(status_after.json()["checked_in_today"], True)
            self.assertEqual(status_after.json()["streak_days"], 1)
            self.assertEqual(status_after.json()["reward_image_quota"], 5)
            self.assertEqual(status_after.json()["can_checkin"], False)

        rows = self.checkin_rows(user_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].reward_image_quota, 5)

    def test_checkin_awards_ggb_and_keeps_legacy_reward_field_consistent(self) -> None:
        admin_token = self.create_admin()
        _, headers = self.create_user(admin_token, image_quota=0)
        updated_settings = self.client.patch(
            "/api/admin/auth-settings",
            headers=self.auth_headers(admin_token),
            json={
                "checkin_daily_image_quota": 7,
                "checkin_streak_bonus_enabled": False,
            },
        )
        self.assertEqual(updated_settings.status_code, 200, updated_settings.text)
        now = self.local_noon_utc(2026, 5, 6)

        with patch("services.user_service.utc_now", return_value=now):
            checked_in = self.client.post("/api/checkin", headers=headers)

        self.assertEqual(checked_in.status_code, 200, checked_in.text)
        payload = checked_in.json()
        self.assertIn("reward_ggb", payload)
        self.assertEqual(payload["reward_ggb"], 7)
        self.assertEqual(payload["reward_image_quota"], 7)
        self.assert_user_ggb(payload["user"], 7)

    def test_same_day_repeat_is_idempotent_and_does_not_award_again(self) -> None:
        admin_token = self.create_admin()
        user_id, headers = self.create_user(admin_token, image_quota=0)
        now = self.local_noon_utc(2026, 5, 2)

        with patch("services.user_service.utc_now", return_value=now):
            first = self.client.post("/api/checkin", headers=headers)
            second = self.client.post("/api/checkin", headers=headers)

        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(first.json()["already_checked_in"], False)
        self.assertEqual(second.json()["already_checked_in"], True)
        self.assertEqual(second.json()["reward_image_quota"], 0)
        self.assertEqual(second.json()["user"]["image_quota"], 5)
        self.assertEqual(len(self.checkin_rows(user_id)), 1)

    def test_cross_day_checkins_continue_streak_and_seventh_day_gets_bonus(self) -> None:
        admin_token = self.create_admin()
        user_id, headers = self.create_user(admin_token, image_quota=0)

        for offset in range(7):
            now = self.local_noon_utc(2026, 5, 1 + offset)
            with patch("services.user_service.utc_now", return_value=now):
                response = self.client.post("/api/checkin", headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["streak_days"], offset + 1)
            expected_reward = 20 if offset == 6 else 5
            self.assertEqual(response.json()["reward_image_quota"], expected_reward)

        self.assertEqual(self.user_service.get_user(user_id)["image_quota"], 50)
        rows = self.checkin_rows(user_id)
        self.assertEqual(len(rows), 7)
        self.assertEqual(rows[-1].streak_days, 7)
        self.assertEqual(rows[-1].reward_image_quota, 20)

    def test_checkin_disabled_returns_403_without_awarding_quota(self) -> None:
        admin_token = self.create_admin()
        user_id, headers = self.create_user(admin_token, image_quota=5)
        disabled = self.client.patch(
            "/api/admin/auth-settings",
            headers=self.auth_headers(admin_token),
            json={"checkin_enabled": False},
        )
        self.assertEqual(disabled.status_code, 200, disabled.text)

        response = self.client.post("/api/checkin", headers=headers)

        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(response.json()["detail"]["error"]["code"], "checkin_disabled")
        self.assertEqual(self.user_service.get_user(user_id)["image_quota"], 5)
        self.assertEqual(self.checkin_rows(user_id), [])

    def test_delete_user_with_checkin_history_soft_disables_and_keeps_records(self) -> None:
        admin_token = self.create_admin()
        user_id, headers = self.create_user(admin_token, image_quota=0)
        now = self.local_noon_utc(2026, 5, 5)
        with patch("services.user_service.utc_now", return_value=now):
            checked_in = self.client.post("/api/checkin", headers=headers)
        self.assertEqual(checked_in.status_code, 200, checked_in.text)

        deleted = self.client.delete(f"/api/admin/users/{user_id}", headers=self.auth_headers(admin_token))

        self.assertEqual(deleted.status_code, 200, deleted.text)
        user = self.user_service.get_user(user_id)
        self.assertIsNotNone(user)
        self.assertEqual(user["enabled"], False)
        self.assertEqual(len(self.checkin_rows(user_id)), 1)

    def test_invalid_path_checkin_timezone_falls_back_in_status_response(self) -> None:
        admin_token = self.create_admin()
        _, headers = self.create_user(admin_token, image_quota=0)
        with self.user_service.Session() as session:
            row = session.get(self.user_service_module.AuthSettingModel, "checkin_timezone")
            self.assertIsNotNone(row)
            row.value = "../UTC"
            session.commit()

        response = self.client.get("/api/checkin/status", headers=headers)

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["timezone"], "Asia/Shanghai")

    def test_admin_checkin_timezone_path_value_is_normalized_to_default(self) -> None:
        admin_token = self.create_admin()

        response = self.client.patch(
            "/api/admin/auth-settings",
            headers=self.auth_headers(admin_token),
            json={"checkin_timezone": "../UTC"},
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["settings"]["checkin_timezone"], "Asia/Shanghai")

    def test_checkin_award_does_not_modify_member_image_quota(self) -> None:
        admin_token = self.create_admin()
        user_id, headers = self.create_user(admin_token, image_quota=0)
        now = self.local_noon_utc(2026, 5, 3)
        with self.user_service.Session() as session:
            session.add(
                self.user_service_module.UserMembershipModel(
                    id=str(uuid4()),
                    user_id=user_id,
                    plan_name="测试会员",
                    status="active",
                    activated_at=now,
                    expires_at=now + timedelta(days=7),
                    current_period_started_at=now,
                    current_period_ends_at=now + timedelta(days=7),
                    member_image_quota=8,
                    period_image_quota=8,
                    duration_days=7,
                    period_days=7,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.commit()

        with patch("services.user_service.utc_now", return_value=now):
            response = self.client.post("/api/checkin", headers=headers)

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["user"]["image_quota"], 5)
        self.assertEqual(response.json()["user"]["member_image_quota"], 8)
        with self.user_service.Session() as session:
            membership = session.query(self.user_service_module.UserMembershipModel).filter_by(user_id=user_id).one()
            self.assertEqual(membership.member_image_quota, 8)

    def test_concurrent_same_day_checkins_create_one_record_and_award_once(self) -> None:
        admin_token = self.create_admin()
        user_id, _ = self.create_user(admin_token, image_quota=0)
        now = self.local_noon_utc(2026, 5, 4)
        worker_count = 8
        barrier = Barrier(worker_count)

        def checkin_once() -> dict[str, object]:
            barrier.wait(timeout=5)
            return self.user_service.checkin(user_id)

        with patch("services.user_service.utc_now", return_value=now):
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                results = list(executor.map(lambda _: checkin_once(), range(worker_count)))

        self.assertEqual(len(self.checkin_rows(user_id)), 1)
        self.assertEqual(self.user_service.get_user(user_id)["image_quota"], 5)
        self.assertEqual(sum(1 for result in results if result["already_checked_in"] is False), 1)
        self.assertEqual(sum(1 for result in results if result["already_checked_in"] is True), worker_count - 1)

    def test_checkin_api_requires_authorization(self) -> None:
        status = self.client.get("/api/checkin/status")
        checkin = self.client.post("/api/checkin")

        self.assertEqual(status.status_code, 401, status.text)
        self.assertEqual(checkin.status_code, 401, checkin.text)

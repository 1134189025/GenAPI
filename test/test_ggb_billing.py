from __future__ import annotations

import asyncio
import importlib
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


GGB_PER_IMAGE = 5


class GGBBillingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "users.db"
        self.old_user_db = os.environ.get("GENAPI_USER_DATABASE_URL")
        self.old_jwt_secret = os.environ.get("JWT_SECRET")
        os.environ["GENAPI_USER_DATABASE_URL"] = f"sqlite:///{self.db_path}"
        os.environ["JWT_SECRET"] = "unit-test-secret-with-at-least-32-bytes"
        self._clear_modules()
        self.user_service_module = None
        self.user_service = None

    def tearDown(self) -> None:
        service = self.user_service
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
        self._clear_modules()
        self.tmp.cleanup()

    @staticmethod
    def _clear_modules() -> None:
        for module_name in list(sys.modules):
            if module_name.startswith("services.user") or module_name.startswith("services.quota"):
                sys.modules.pop(module_name, None)

    def load_service(self):
        if self.user_service is not None:
            engine = getattr(self.user_service, "engine", None)
            if engine is not None:
                engine.dispose()
        self._clear_modules()
        self.user_service_module = importlib.import_module("services.user_service")
        self.user_service = self.user_service_module.user_service
        return self.user_service

    def create_legacy_users_table(self, *, user_id: str = "legacy-user", image_quota: int = 3) -> str:
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                """
                CREATE TABLE users (
                    id VARCHAR(36) PRIMARY KEY,
                    email VARCHAR(255) NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    role VARCHAR(16) NOT NULL DEFAULT 'user',
                    enabled BOOLEAN NOT NULL DEFAULT 1,
                    image_quota INTEGER NOT NULL DEFAULT 0,
                    image_concurrency INTEGER NOT NULL DEFAULT 1,
                    active_image_requests INTEGER NOT NULL DEFAULT 0,
                    token_version INTEGER NOT NULL DEFAULT 0,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    last_login_at DATETIME
                )
                """
            )
            connection.execute(
                """
                INSERT INTO users (
                    id, email, password_hash, role, enabled, image_quota, image_concurrency,
                    active_image_requests, token_version, created_at, updated_at, last_login_at
                )
                VALUES (?, ?, ?, 'user', 1, ?, 1, 0, 0, ?, ?, NULL)
                """,
                (
                    user_id,
                    "legacy-ggb@example.com",
                    "legacy-hash",
                    image_quota,
                    "2026-05-01 00:00:00.000000",
                    "2026-05-01 00:00:00.000000",
                ),
            )
            connection.commit()
        finally:
            connection.close()
        return user_id

    def create_legacy_billing_tables(self) -> None:
        connection = sqlite3.connect(self.db_path)
        try:
            connection.executescript(
                """
                CREATE TABLE users (
                    id VARCHAR(36) PRIMARY KEY,
                    email VARCHAR(255) NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    role VARCHAR(16) NOT NULL DEFAULT 'user',
                    enabled BOOLEAN NOT NULL DEFAULT 1,
                    image_quota INTEGER NOT NULL DEFAULT 0,
                    image_concurrency INTEGER NOT NULL DEFAULT 1,
                    active_image_requests INTEGER NOT NULL DEFAULT 0,
                    token_version INTEGER NOT NULL DEFAULT 0,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    last_login_at DATETIME
                );
                CREATE TABLE membership_plans (
                    id VARCHAR(36) PRIMARY KEY,
                    name VARCHAR(80) NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    duration_days INTEGER NOT NULL DEFAULT 1,
                    period_days INTEGER NOT NULL DEFAULT 1,
                    period_image_quota INTEGER NOT NULL DEFAULT 1,
                    enabled BOOLEAN NOT NULL DEFAULT 1,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                );
                CREATE TABLE user_memberships (
                    id VARCHAR(36) PRIMARY KEY,
                    user_id VARCHAR(36) NOT NULL UNIQUE,
                    plan_id VARCHAR(36),
                    plan_name VARCHAR(80) NOT NULL DEFAULT '',
                    status VARCHAR(32) NOT NULL DEFAULT 'active',
                    activated_at DATETIME,
                    expires_at DATETIME,
                    current_period_started_at DATETIME,
                    current_period_ends_at DATETIME,
                    member_image_quota INTEGER NOT NULL DEFAULT 0,
                    period_image_quota INTEGER NOT NULL DEFAULT 0,
                    duration_days INTEGER NOT NULL DEFAULT 0,
                    period_days INTEGER NOT NULL DEFAULT 0,
                    source_redeem_code_id VARCHAR(36),
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                );
                CREATE TABLE daily_checkins (
                    id VARCHAR(36) PRIMARY KEY,
                    user_id VARCHAR(36) NOT NULL,
                    checkin_date DATE NOT NULL,
                    reward_image_quota INTEGER NOT NULL DEFAULT 0,
                    streak_days INTEGER NOT NULL DEFAULT 1,
                    created_at DATETIME NOT NULL
                );
                CREATE TABLE promo_codes (
                    id VARCHAR(36) PRIMARY KEY,
                    code_hash VARCHAR(64) NOT NULL UNIQUE,
                    code_prefix VARCHAR(8) NOT NULL,
                    code_suffix VARCHAR(8) NOT NULL,
                    image_quota INTEGER NOT NULL DEFAULT 0,
                    max_uses INTEGER NOT NULL DEFAULT 1,
                    used_count INTEGER NOT NULL DEFAULT 0,
                    enabled BOOLEAN NOT NULL DEFAULT 1,
                    expires_at DATETIME,
                    created_at DATETIME NOT NULL
                );
                CREATE TABLE redeem_codes (
                    id VARCHAR(36) PRIMARY KEY,
                    code_hash VARCHAR(64) NOT NULL UNIQUE,
                    code_prefix VARCHAR(8) NOT NULL,
                    code_suffix VARCHAR(8) NOT NULL,
                    type VARCHAR(32) NOT NULL,
                    value INTEGER NOT NULL DEFAULT 0,
                    membership_plan_id VARCHAR(36),
                    metadata TEXT NOT NULL DEFAULT '',
                    enabled BOOLEAN NOT NULL DEFAULT 1,
                    used_by_user_id VARCHAR(36),
                    used_at DATETIME,
                    expires_at DATETIME,
                    created_at DATETIME NOT NULL
                );
                CREATE TABLE image_usage_events (
                    id VARCHAR(36) PRIMARY KEY,
                    user_id VARCHAR(36) NOT NULL,
                    endpoint VARCHAR(128) NOT NULL,
                    requested_count INTEGER NOT NULL DEFAULT 0,
                    actual_count INTEGER NOT NULL DEFAULT 0,
                    refunded_count INTEGER NOT NULL DEFAULT 0,
                    member_reserved_count INTEGER NOT NULL DEFAULT 0,
                    regular_reserved_count INTEGER NOT NULL DEFAULT 0,
                    membership_source_redeem_code_id VARCHAR(36),
                    membership_activation_key VARCHAR(255) NOT NULL DEFAULT '',
                    member_actual_count INTEGER NOT NULL DEFAULT 0,
                    regular_actual_count INTEGER NOT NULL DEFAULT 0,
                    member_refunded_count INTEGER NOT NULL DEFAULT 0,
                    regular_refunded_count INTEGER NOT NULL DEFAULT 0,
                    status VARCHAR(32) NOT NULL DEFAULT 'success',
                    error TEXT NOT NULL DEFAULT '',
                    created_at DATETIME NOT NULL,
                    settled_at DATETIME
                );
                CREATE TABLE auth_settings (
                    key VARCHAR(128) PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at DATETIME NOT NULL
                );
                """
            )
            now = "2026-05-01 00:00:00.000000"
            connection.execute(
                """
                INSERT INTO users (
                    id, email, password_hash, role, enabled, image_quota, image_concurrency,
                    active_image_requests, token_version, created_at, updated_at, last_login_at
                )
                VALUES ('legacy-user', 'legacy-full@example.com', 'legacy-hash', 'user', 1, 3, 1, 0, 0, ?, ?, NULL)
                """,
                (now, now),
            )
            connection.execute(
                """
                INSERT INTO membership_plans (
                    id, name, description, duration_days, period_days, period_image_quota,
                    enabled, sort_order, created_at, updated_at
                )
                VALUES ('legacy-plan', 'Legacy Plan', '', 30, 30, 4, 1, 1, ?, ?)
                """,
                (now, now),
            )
            connection.execute(
                """
                INSERT INTO user_memberships (
                    id, user_id, plan_id, plan_name, status, activated_at, expires_at,
                    current_period_started_at, current_period_ends_at, member_image_quota,
                    period_image_quota, duration_days, period_days, source_redeem_code_id,
                    created_at, updated_at
                )
                VALUES (
                    'legacy-membership', 'legacy-user', 'legacy-plan', 'Legacy Plan', 'active',
                    ?, '2026-06-01 00:00:00.000000', ?, '2026-06-01 00:00:00.000000',
                    2, 4, 30, 30, 'redeem-membership', ?, ?
                )
                """,
                (now, now, now, now),
            )
            connection.execute(
                """
                INSERT INTO daily_checkins (id, user_id, checkin_date, reward_image_quota, streak_days, created_at)
                VALUES ('legacy-checkin', 'legacy-user', '2026-05-01', 1, 1, ?)
                """,
                (now,),
            )
            connection.execute(
                """
                INSERT INTO promo_codes (
                    id, code_hash, code_prefix, code_suffix, image_quota, max_uses,
                    used_count, enabled, expires_at, created_at
                )
                VALUES ('legacy-promo', 'promo-hash', 'PROMO', 'TAIL', 7, 2, 0, 1, NULL, ?)
                """,
                (now,),
            )
            connection.execute(
                """
                INSERT INTO redeem_codes (
                    id, code_hash, code_prefix, code_suffix, type, value, membership_plan_id,
                    metadata, enabled, used_by_user_id, used_at, expires_at, created_at
                )
                VALUES ('redeem-ggb', 'redeem-hash', 'IMG', 'TAIL', 'image_quota', 8, NULL, '', 1, NULL, NULL, NULL, ?)
                """,
                (now,),
            )
            connection.execute(
                """
                INSERT INTO redeem_codes (
                    id, code_hash, code_prefix, code_suffix, type, value, membership_plan_id,
                    metadata, enabled, used_by_user_id, used_at, expires_at, created_at
                )
                VALUES (
                    'redeem-membership', 'member-hash', 'MEM', 'TAIL', 'membership', 0,
                    'legacy-plan', '{"period_image_quota":6}', 1, NULL, NULL, NULL, ?
                )
                """,
                (now,),
            )
            connection.execute(
                """
                INSERT INTO image_usage_events (
                    id, user_id, endpoint, requested_count, actual_count, refunded_count,
                    member_reserved_count, regular_reserved_count, membership_source_redeem_code_id,
                    membership_activation_key, member_actual_count, regular_actual_count,
                    member_refunded_count, regular_refunded_count, status, error, created_at, settled_at
                )
                VALUES (
                    'legacy-event', 'legacy-user', '/api/image/generations', 2, 1, 1,
                    1, 1, 'redeem-membership', 'legacy-key', 1, 0, 0, 1,
                    'success', '', ?, ?
                )
                """,
                (now, now),
            )
            for key, value in {
                "default_image_quota": 9,
                "checkin_daily_image_quota": 1,
                "checkin_streak_bonus_image_quota": 3,
            }.items():
                connection.execute(
                    "INSERT INTO auth_settings (key, value, updated_at) VALUES (?, ?, ?)",
                    (key, str(value), now),
                )
            connection.commit()
        finally:
            connection.close()

    def assert_full_legacy_billing_migrated_once(self) -> None:
        connection = sqlite3.connect(self.db_path)
        try:
            checks = {
                "users": ("SELECT image_quota FROM users WHERE id = 'legacy-user'", 3 * GGB_PER_IMAGE),
                "membership_plan": (
                    "SELECT period_image_quota FROM membership_plans WHERE id = 'legacy-plan'",
                    4 * GGB_PER_IMAGE,
                ),
                "membership_member": (
                    "SELECT member_image_quota FROM user_memberships WHERE id = 'legacy-membership'",
                    2 * GGB_PER_IMAGE,
                ),
                "membership_period": (
                    "SELECT period_image_quota FROM user_memberships WHERE id = 'legacy-membership'",
                    4 * GGB_PER_IMAGE,
                ),
                "checkin": (
                    "SELECT reward_image_quota FROM daily_checkins WHERE id = 'legacy-checkin'",
                    GGB_PER_IMAGE,
                ),
                "promo": ("SELECT image_quota FROM promo_codes WHERE id = 'legacy-promo'", 7 * GGB_PER_IMAGE),
                "redeem": ("SELECT value FROM redeem_codes WHERE id = 'redeem-ggb'", 8 * GGB_PER_IMAGE),
                "event_requested": (
                    "SELECT requested_ggb FROM image_usage_events WHERE id = 'legacy-event'",
                    2 * GGB_PER_IMAGE,
                ),
                "event_actual": (
                    "SELECT actual_ggb FROM image_usage_events WHERE id = 'legacy-event'",
                    GGB_PER_IMAGE,
                ),
                "event_refunded": (
                    "SELECT refunded_ggb FROM image_usage_events WHERE id = 'legacy-event'",
                    GGB_PER_IMAGE,
                ),
                "event_member_reserved": (
                    "SELECT member_reserved_ggb FROM image_usage_events WHERE id = 'legacy-event'",
                    GGB_PER_IMAGE,
                ),
                "event_regular_reserved": (
                    "SELECT regular_reserved_ggb FROM image_usage_events WHERE id = 'legacy-event'",
                    GGB_PER_IMAGE,
                ),
            }
            for label, (query, expected) in checks.items():
                with self.subTest(label=label):
                    self.assertEqual(connection.execute(query).fetchone()[0], expected)
            metadata = connection.execute(
                "SELECT metadata FROM redeem_codes WHERE id = 'redeem-membership'"
            ).fetchone()[0]
            self.assertIn('"period_image_quota":30', metadata.replace(" ", ""))
            for key, expected in {
                "default_image_quota": 9 * GGB_PER_IMAGE,
                "checkin_daily_image_quota": GGB_PER_IMAGE,
                "checkin_streak_bonus_image_quota": 3 * GGB_PER_IMAGE,
            }.items():
                with self.subTest(setting=key):
                    value = connection.execute("SELECT value FROM auth_settings WHERE key = ?", (key,)).fetchone()[0]
                    self.assertEqual(value, str(expected))
        finally:
            connection.close()

    def assert_regular_ggb(self, user: dict[str, object], expected_ggb: int) -> None:
        self.assertIn("ggb", user)
        self.assertEqual(user["ggb"], expected_ggb)
        self.assertEqual(user["image_quota"], expected_ggb)

    def test_legacy_image_quota_is_migrated_to_ggb_once(self) -> None:
        user_id = self.create_legacy_users_table(image_quota=3)

        first_service = self.load_service()
        first_user = first_service.get_user(user_id)
        self.assertIsNotNone(first_user)
        self.assert_regular_ggb(first_user, 3 * GGB_PER_IMAGE)

        second_service = self.load_service()
        second_user = second_service.get_user(user_id)
        self.assertIsNotNone(second_user)
        self.assert_regular_ggb(second_user, 3 * GGB_PER_IMAGE)

    def test_legacy_billing_tables_are_migrated_to_ggb_once(self) -> None:
        self.create_legacy_billing_tables()

        first_service = self.load_service()
        self.assertIsNotNone(first_service)
        self.assert_full_legacy_billing_migrated_once()

        second_service = self.load_service()
        self.assertIsNotNone(second_service)
        self.assert_full_legacy_billing_migrated_once()

    def test_ggb_migration_uses_conflict_safe_marker_acquisition(self) -> None:
        user_id = self.create_legacy_users_table(image_quota=3)
        from sqlalchemy import text
        from sqlalchemy.engine import Connection

        original_execute = Connection.execute
        race_inserted = False

        def racing_execute(connection, statement, parameters=None, *args, **kwargs):
            nonlocal race_inserted
            statement_text = str(statement)
            if (
                "INSERT INTO schema_migrations" in statement_text
                and "ON CONFLICT" not in statement_text.upper()
                and not race_inserted
            ):
                race_inserted = True
                original_execute(
                    connection,
                    text("INSERT INTO schema_migrations (key, applied_at) VALUES (:key, :applied_at)"),
                    {"key": "ggb_v1", "applied_at": "2026-05-01 00:00:00.000000"},
                )
            if parameters is None:
                return original_execute(connection, statement, *args, **kwargs)
            return original_execute(connection, statement, parameters, *args, **kwargs)

        with patch.object(Connection, "execute", racing_execute):
            service = self.load_service()

        user = service.get_user(user_id)
        self.assert_regular_ggb(user, 3 * GGB_PER_IMAGE)

    def test_one_image_costs_5_ggb_and_4_ggb_is_insufficient(self) -> None:
        service = self.load_service()
        user = service.create_user(
            email="four-ggb@example.com",
            password="UserPass123!",
            image_quota=4,
            image_concurrency=1,
        )
        self.assert_regular_ggb(user, 4)

        with self.assertRaises(self.user_service_module.UserServiceError) as raised:
            service.reserve_image_quota(user, 1, "/api/image/generations")

        self.assertEqual(raised.exception.code, "insufficient_quota")

        updated = service.update_user(user["id"], {"image_quota": GGB_PER_IMAGE})
        self.assert_regular_ggb(updated, GGB_PER_IMAGE)
        reservation = service.reserve_image_quota(updated, 1, "/api/image/generations")
        after_reserve = service.get_user(user["id"])
        self.assert_regular_ggb(after_reserve, 0)

        service.settle_image_quota(reservation, success=True, actual_count=1)
        after_settle = service.get_user(user["id"])
        self.assert_regular_ggb(after_settle, 0)
        self.assertEqual(after_settle["active_image_requests"], 0)

    def test_partial_success_refunds_unspent_ggb(self) -> None:
        service = self.load_service()
        user = service.create_user(
            email="partial-ggb@example.com",
            password="UserPass123!",
            image_quota=2 * GGB_PER_IMAGE,
            image_concurrency=1,
        )
        self.assert_regular_ggb(user, 2 * GGB_PER_IMAGE)

        reservation = service.reserve_image_quota(user, 2, "/api/image/generations")
        self.assert_regular_ggb(service.get_user(user["id"]), 0)

        service.settle_image_quota(reservation, success=True, actual_count=1)
        refreshed = service.get_user(user["id"])
        self.assert_regular_ggb(refreshed, GGB_PER_IMAGE)
        self.assertEqual(refreshed["active_image_requests"], 0)

    def test_cancelled_logged_image_call_refunds_reserved_ggb_and_releases_concurrency(self) -> None:
        service = self.load_service()
        user = service.create_user(
            email="cancelled-ggb@example.com",
            password="UserPass123!",
            image_quota=GGB_PER_IMAGE,
            image_concurrency=1,
        )
        reservation = service.reserve_image_quota(user, 1, "/api/image/generations")
        after_reserve = service.get_user(user["id"])
        self.assert_regular_ggb(after_reserve, 0)
        self.assertEqual(after_reserve["active_image_requests"], 1)

        from services.log_service import LoggedCall

        def cancelled_handler(_payload):
            raise asyncio.CancelledError()

        async def run_cancelled_call() -> None:
            with self.assertRaises(asyncio.CancelledError):
                await LoggedCall(user, "/api/image/generations", "gpt-image-2", "文生图").run(
                    cancelled_handler,
                    {},
                    reservation,
                )

        asyncio.run(run_cancelled_call())

        refreshed = service.get_user(user["id"])
        self.assert_regular_ggb(refreshed, GGB_PER_IMAGE)
        self.assertEqual(refreshed["active_image_requests"], 0)

    def test_cancelled_stream_first_item_refunds_reserved_ggb_and_releases_concurrency(self) -> None:
        service = self.load_service()
        user = service.create_user(
            email="cancelled-stream-ggb@example.com",
            password="UserPass123!",
            image_quota=GGB_PER_IMAGE,
            image_concurrency=1,
        )
        reservation = service.reserve_image_quota(user, 1, "/api/image/generations")
        after_reserve = service.get_user(user["id"])
        self.assert_regular_ggb(after_reserve, 0)
        self.assertEqual(after_reserve["active_image_requests"], 1)

        from services.log_service import LoggedCall

        class CancelledIterator:
            def __iter__(self):
                return self

            def __next__(self):
                raise asyncio.CancelledError()

        async def run_cancelled_stream() -> None:
            with self.assertRaises(asyncio.CancelledError):
                await LoggedCall(user, "/api/image/generations", "gpt-image-2", "文生图").run(
                    lambda _payload: CancelledIterator(),
                    {},
                    reservation,
                )

        asyncio.run(run_cancelled_stream())

        refreshed = service.get_user(user["id"])
        self.assert_regular_ggb(refreshed, GGB_PER_IMAGE)
        self.assertEqual(refreshed["active_image_requests"], 0)


if __name__ == "__main__":
    unittest.main()

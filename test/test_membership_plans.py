from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient

GGB_PER_IMAGE = 5


class MembershipPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.old_user_db = os.environ.get("GENAPI_USER_DATABASE_URL")
        self.old_jwt_secret = os.environ.get("JWT_SECRET")
        os.environ["GENAPI_USER_DATABASE_URL"] = f"sqlite:///{Path(self.tmp.name) / 'users.db'}"
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

    def auth_headers(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def create_admin(self) -> str:
        response = self.client.post(
            "/api/setup/admin",
            json={"email": "admin@example.com", "password": "AdminPass123!"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return str(response.json()["token"])

    def create_user(self, admin_token: str, *, image_quota: int = 0) -> tuple[str, dict[str, str]]:
        created = self.client.post(
            "/api/admin/users",
            headers=self.auth_headers(admin_token),
            json={
                "email": "member@example.com",
                "password": "UserPass123!",
                "role": "user",
                "enabled": True,
                "image_quota": image_quota,
                "image_concurrency": 1,
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        login = self.client.post("/api/auth/login", json={"email": "member@example.com", "password": "UserPass123!"})
        self.assertEqual(login.status_code, 200, login.text)
        return str(created.json()["item"]["id"]), self.auth_headers(str(login.json()["token"]))

    def assert_user_ggb(self, user: dict[str, object], *, regular: int, member: int = 0) -> None:
        self.assertIn("ggb", user)
        self.assertEqual(user["ggb"], regular)
        self.assertEqual(user["image_quota"], regular)
        self.assertIn("member_ggb", user)
        self.assertEqual(user["member_ggb"], member)
        self.assertEqual(user["member_image_quota"], member)
        self.assertEqual(user["total_image_quota"], regular + member)

    def test_admin_creates_plan_and_user_redeems_membership_code(self) -> None:
        admin_token = self.create_admin()
        admin_headers = self.auth_headers(admin_token)
        _, user_headers = self.create_user(admin_token, image_quota=4)

        defaults = self.client.get("/api/admin/membership-plans", headers=admin_headers)
        self.assertEqual(defaults.status_code, 200, defaults.text)
        self.assertGreaterEqual(len(defaults.json()["items"]), 3)

        created_plan = self.client.post(
            "/api/admin/membership-plans",
            headers=admin_headers,
            json={
                "name": "测试周卡",
                "description": "每 7 天 8 张图",
                "duration_days": 7,
                "period_days": 7,
                "period_image_quota": 8,
                "enabled": True,
                "sort_order": 5,
            },
        )
        self.assertEqual(created_plan.status_code, 200, created_plan.text)
        plan = created_plan.json()["item"]

        generated = self.client.post(
            "/api/admin/redeem-codes/generate",
            headers=admin_headers,
            json={"type": "membership", "membership_plan_id": plan["id"], "count": 1},
        )
        self.assertEqual(generated.status_code, 200, generated.text)
        code = generated.json()["codes"][0]["code"]
        self.assertEqual(generated.json()["codes"][0]["type"], "membership")
        self.assertEqual(generated.json()["codes"][0]["membership_plan_id"], plan["id"])

        redeemed = self.client.post("/api/redeem", headers=user_headers, json={"code": code})
        self.assertEqual(redeemed.status_code, 200, redeemed.text)
        user = redeemed.json()["user"]
        self.assertEqual(user["image_quota"], 4)
        self.assertEqual(user["member_image_quota"], 8)
        self.assertEqual(user["total_image_quota"], 12)
        self.assertEqual(user["membership"]["plan_name"], "测试周卡")
        self.assertEqual(user["membership"]["status"], "active")

    def test_membership_plan_awards_member_ggb_and_keeps_legacy_fields_consistent(self) -> None:
        admin_token = self.create_admin()
        admin_headers = self.auth_headers(admin_token)
        _, user_headers = self.create_user(admin_token, image_quota=3)
        created_plan = self.client.post(
            "/api/admin/membership-plans",
            headers=admin_headers,
            json={
                "name": "GGB 周卡",
                "description": "每 7 天发放 25 GGB",
                "duration_days": 7,
                "period_days": 7,
                "period_image_quota": 25,
                "enabled": True,
                "sort_order": 6,
            },
        )
        self.assertEqual(created_plan.status_code, 200, created_plan.text)
        plan = created_plan.json()["item"]
        generated = self.client.post(
            "/api/admin/redeem-codes/generate",
            headers=admin_headers,
            json={"type": "membership", "membership_plan_id": plan["id"], "count": 1},
        )
        self.assertEqual(generated.status_code, 200, generated.text)
        code = generated.json()["codes"][0]["code"]

        redeemed = self.client.post("/api/redeem", headers=user_headers, json={"code": code})

        self.assertEqual(redeemed.status_code, 200, redeemed.text)
        user = redeemed.json()["user"]
        self.assert_user_ggb(user, regular=3, member=25)
        membership = user["membership"]
        self.assertIn("member_ggb", membership)
        self.assertEqual(membership["member_ggb"], 25)
        self.assertEqual(membership["member_image_quota"], 25)
        self.assertIn("period_ggb", membership)
        self.assertEqual(membership["period_ggb"], 25)
        self.assertEqual(membership["period_image_quota"], 25)

    def test_member_quota_is_reserved_first_and_refunded_by_source(self) -> None:
        admin_token = self.create_admin()
        admin_headers = self.auth_headers(admin_token)
        user_id, _ = self.create_user(admin_token, image_quota=GGB_PER_IMAGE)
        plan = self.client.get("/api/admin/membership-plans", headers=admin_headers).json()["items"][0]
        code = self.client.post(
            "/api/admin/redeem-codes/generate",
            headers=admin_headers,
            json={"type": "membership", "membership_plan_id": plan["id"], "count": 1},
        ).json()["codes"][0]["code"]
        self.user_service.redeem(user_id, code)
        with self.user_service.Session() as session:
            membership = session.query(self.user_service_module.UserMembershipModel).filter_by(user_id=user_id).one()
            membership.member_image_quota = GGB_PER_IMAGE
            membership.period_image_quota = GGB_PER_IMAGE
            session.commit()

        reservation = self.user_service.reserve_image_quota(
            self.user_service.get_user(user_id),
            2,
            "/api/image/generations",
        )

        after_reserve = self.user_service.get_user(user_id)
        self.assertEqual(after_reserve["member_image_quota"], 0)
        self.assertEqual(after_reserve["image_quota"], 0)
        self.assertEqual(reservation.member_reserved_count, 1)
        self.assertEqual(reservation.regular_reserved_count, 1)

        self.user_service.settle_image_quota(reservation, success=False, error="upstream failed")

        after_refund = self.user_service.get_user(user_id)
        self.assertEqual(after_refund["member_image_quota"], GGB_PER_IMAGE)
        self.assertEqual(after_refund["image_quota"], GGB_PER_IMAGE)
        self.assertEqual(after_refund["active_image_requests"], 0)

    def test_old_member_quota_refund_does_not_credit_replacement_membership(self) -> None:
        admin_token = self.create_admin()
        admin_headers = self.auth_headers(admin_token)
        user_id, _ = self.create_user(admin_token, image_quota=2 * GGB_PER_IMAGE)
        first_plan = self.client.get("/api/admin/membership-plans", headers=admin_headers).json()["items"][0]
        replacement_plan = self.client.post(
            "/api/admin/membership-plans",
            headers=admin_headers,
            json={
                "name": "替换月卡",
                "description": "新会员不应收到旧会员退款",
                "duration_days": 30,
                "period_days": 30,
                "period_image_quota": 9,
                "enabled": True,
                "sort_order": 1,
            },
        ).json()["item"]
        first_code = self.client.post(
            "/api/admin/redeem-codes/generate",
            headers=admin_headers,
            json={"type": "membership", "membership_plan_id": first_plan["id"], "count": 1},
        ).json()["codes"][0]["code"]
        replacement_code = self.client.post(
            "/api/admin/redeem-codes/generate",
            headers=admin_headers,
            json={"type": "membership", "membership_plan_id": replacement_plan["id"], "count": 1},
        ).json()["codes"][0]["code"]
        self.user_service.redeem(user_id, first_code)
        with self.user_service.Session() as session:
            membership = session.query(self.user_service_module.UserMembershipModel).filter_by(user_id=user_id).one()
            membership.member_image_quota = GGB_PER_IMAGE
            membership.period_image_quota = GGB_PER_IMAGE
            session.commit()

        reservation = self.user_service.reserve_image_quota(
            self.user_service.get_user(user_id),
            1,
            "/api/image/generations",
        )
        self.user_service.redeem(user_id, replacement_code)
        replaced = self.user_service.get_user(user_id)
        self.assertEqual(replaced["member_image_quota"], 9)

        self.user_service.settle_image_quota(reservation, success=False, error="upstream failed")

        after_refund = self.user_service.get_user(user_id)
        self.assertEqual(after_refund["membership"]["plan_name"], "替换月卡")
        self.assertEqual(after_refund["member_image_quota"], 9)
        self.assertEqual(after_refund["image_quota"], 2 * GGB_PER_IMAGE)
        self.assertEqual(after_refund["active_image_requests"], 0)

    def test_deleting_used_membership_code_clears_membership_source_reference(self) -> None:
        admin_token = self.create_admin()
        admin_headers = self.auth_headers(admin_token)
        user_id, _ = self.create_user(admin_token, image_quota=1)
        plan = self.client.get("/api/admin/membership-plans", headers=admin_headers).json()["items"][0]
        generated = self.client.post(
            "/api/admin/redeem-codes/generate",
            headers=admin_headers,
            json={"type": "membership", "membership_plan_id": plan["id"], "count": 1},
        ).json()["codes"][0]
        self.user_service.redeem(user_id, generated["code"])

        deleted = self.client.delete(f"/api/admin/redeem-codes/{generated['id']}", headers=admin_headers)
        self.assertEqual(deleted.status_code, 200, deleted.text)

        with self.user_service.Session() as session:
            membership = session.query(self.user_service_module.UserMembershipModel).filter_by(user_id=user_id).one()
            self.assertIsNone(membership.source_redeem_code_id)

    def test_old_member_quota_refund_does_not_credit_replacement_after_source_code_deleted(self) -> None:
        admin_token = self.create_admin()
        admin_headers = self.auth_headers(admin_token)
        user_id, _ = self.create_user(admin_token, image_quota=2 * GGB_PER_IMAGE)
        first_plan = self.client.get("/api/admin/membership-plans", headers=admin_headers).json()["items"][0]
        replacement_plan = self.client.post(
            "/api/admin/membership-plans",
            headers=admin_headers,
            json={
                "name": "无来源替换卡",
                "description": "删除来源码后仍不能串退款",
                "duration_days": 30,
                "period_days": 30,
                "period_image_quota": 6,
                "enabled": True,
                "sort_order": 2,
            },
        ).json()["item"]
        first_generated = self.client.post(
            "/api/admin/redeem-codes/generate",
            headers=admin_headers,
            json={"type": "membership", "membership_plan_id": first_plan["id"], "count": 1},
        ).json()["codes"][0]
        replacement_code = self.client.post(
            "/api/admin/redeem-codes/generate",
            headers=admin_headers,
            json={"type": "membership", "membership_plan_id": replacement_plan["id"], "count": 1},
        ).json()["codes"][0]["code"]
        self.user_service.redeem(user_id, first_generated["code"])
        deleted = self.client.delete(f"/api/admin/redeem-codes/{first_generated['id']}", headers=admin_headers)
        self.assertEqual(deleted.status_code, 200, deleted.text)
        with self.user_service.Session() as session:
            membership = session.query(self.user_service_module.UserMembershipModel).filter_by(user_id=user_id).one()
            membership.member_image_quota = GGB_PER_IMAGE
            membership.period_image_quota = GGB_PER_IMAGE
            self.assertIsNone(membership.source_redeem_code_id)
            session.commit()

        reservation = self.user_service.reserve_image_quota(
            self.user_service.get_user(user_id),
            1,
            "/api/image/generations",
        )
        self.user_service.redeem(user_id, replacement_code)
        replaced = self.user_service.get_user(user_id)
        self.assertEqual(replaced["member_image_quota"], 6)

        self.user_service.settle_image_quota(reservation, success=False, error="upstream failed")

        after_refund = self.user_service.get_user(user_id)
        self.assertEqual(after_refund["membership"]["plan_name"], "无来源替换卡")
        self.assertEqual(after_refund["member_image_quota"], 6)
        self.assertEqual(after_refund["image_quota"], 2 * GGB_PER_IMAGE)
        self.assertEqual(after_refund["active_image_requests"], 0)

    def test_old_period_member_refund_does_not_credit_refreshed_period(self) -> None:
        admin_token = self.create_admin()
        admin_headers = self.auth_headers(admin_token)
        user_id, _ = self.create_user(admin_token, image_quota=GGB_PER_IMAGE)
        plan = self.client.get("/api/admin/membership-plans", headers=admin_headers).json()["items"][0]
        code = self.client.post(
            "/api/admin/redeem-codes/generate",
            headers=admin_headers,
            json={"type": "membership", "membership_plan_id": plan["id"], "count": 1},
        ).json()["codes"][0]["code"]
        self.user_service.redeem(user_id, code)

        now = self.user_service_module.utc_now()
        old_period_start = now - timedelta(days=2)
        old_period_end = now + timedelta(hours=1)
        with self.user_service.Session() as session:
            membership = session.query(self.user_service_module.UserMembershipModel).filter_by(user_id=user_id).one()
            membership.member_image_quota = GGB_PER_IMAGE
            membership.period_image_quota = GGB_PER_IMAGE
            membership.current_period_started_at = old_period_start
            membership.current_period_ends_at = old_period_end
            membership.expires_at = now + timedelta(days=3)
            session.commit()

        reservation = self.user_service.reserve_image_quota(
            self.user_service.get_user(user_id),
            1,
            "/api/image/generations",
        )
        self.assertEqual(self.user_service.get_user(user_id)["member_image_quota"], 0)

        with self.user_service.Session() as session:
            membership = session.query(self.user_service_module.UserMembershipModel).filter_by(user_id=user_id).one()
            membership.current_period_started_at = old_period_start
            membership.current_period_ends_at = now - timedelta(seconds=1)
            session.commit()
        refreshed_period = self.user_service.get_user(user_id)
        self.assertEqual(refreshed_period["member_image_quota"], GGB_PER_IMAGE)

        self.user_service.settle_image_quota(reservation, success=False, error="upstream failed")

        after_refund = self.user_service.get_user(user_id)
        self.assertEqual(after_refund["member_image_quota"], GGB_PER_IMAGE)
        self.assertEqual(after_refund["image_quota"], GGB_PER_IMAGE)
        self.assertEqual(after_refund["active_image_requests"], 0)

    def test_stale_old_period_member_refund_does_not_credit_refreshed_period(self) -> None:
        admin_token = self.create_admin()
        admin_headers = self.auth_headers(admin_token)
        user_id, _ = self.create_user(admin_token, image_quota=GGB_PER_IMAGE)
        plan = self.client.get("/api/admin/membership-plans", headers=admin_headers).json()["items"][0]
        code = self.client.post(
            "/api/admin/redeem-codes/generate",
            headers=admin_headers,
            json={"type": "membership", "membership_plan_id": plan["id"], "count": 1},
        ).json()["codes"][0]["code"]
        self.user_service.redeem(user_id, code)

        now = self.user_service_module.utc_now()
        old_period_start = now - timedelta(days=2)
        old_period_end = now + timedelta(hours=1)
        with self.user_service.Session() as session:
            membership = session.query(self.user_service_module.UserMembershipModel).filter_by(user_id=user_id).one()
            membership.member_image_quota = GGB_PER_IMAGE
            membership.period_image_quota = GGB_PER_IMAGE
            membership.current_period_started_at = old_period_start
            membership.current_period_ends_at = old_period_end
            membership.expires_at = now + timedelta(days=3)
            session.commit()

        reservation = self.user_service.reserve_image_quota(
            self.user_service.get_user(user_id),
            1,
            "/api/image/generations",
        )
        with self.user_service.Session() as session:
            event = session.get(self.user_service_module.ImageUsageEventModel, reservation.event_id)
            self.assertIsNotNone(event)
            event.created_at = old_period_start + timedelta(hours=1)
            membership = session.query(self.user_service_module.UserMembershipModel).filter_by(user_id=user_id).one()
            membership.current_period_started_at = old_period_start
            membership.current_period_ends_at = now - timedelta(seconds=1)
            session.commit()
        refreshed_period = self.user_service.get_user(user_id)
        self.assertEqual(refreshed_period["member_image_quota"], GGB_PER_IMAGE)

        recovered = self.user_service.recover_stale_image_quota_reservations(stale_after_seconds=0)

        after_recovery = self.user_service.get_user(user_id)
        self.assertEqual(recovered, 1)
        self.assertEqual(after_recovery["member_image_quota"], GGB_PER_IMAGE)
        self.assertEqual(after_recovery["image_quota"], GGB_PER_IMAGE)
        self.assertEqual(after_recovery["active_image_requests"], 0)

    def test_membership_period_resets_without_rollover_and_expiry_clears_member_quota(self) -> None:
        admin_token = self.create_admin()
        admin_headers = self.auth_headers(admin_token)
        user_id, _ = self.create_user(admin_token, image_quota=3)
        plan = self.client.get("/api/admin/membership-plans", headers=admin_headers).json()["items"][0]
        code = self.client.post(
            "/api/admin/redeem-codes/generate",
            headers=admin_headers,
            json={"type": "membership", "membership_plan_id": plan["id"], "count": 1},
        ).json()["codes"][0]["code"]
        self.user_service.redeem(user_id, code)

        now = self.user_service_module.utc_now()
        with self.user_service.Session() as session:
            membership = session.query(self.user_service_module.UserMembershipModel).filter_by(user_id=user_id).one()
            membership.member_image_quota = 1
            membership.period_image_quota = 5
            membership.current_period_started_at = now - timedelta(days=2)
            membership.current_period_ends_at = now - timedelta(days=1)
            membership.expires_at = now + timedelta(days=3)
            session.commit()

        refreshed = self.user_service.get_user(user_id)
        self.assertEqual(refreshed["member_image_quota"], 5)
        self.assertEqual(refreshed["image_quota"], 3)

        with self.user_service.Session() as session:
            membership = session.query(self.user_service_module.UserMembershipModel).filter_by(user_id=user_id).one()
            membership.member_image_quota = 4
            membership.expires_at = now - timedelta(seconds=1)
            session.commit()

        expired = self.user_service.get_user(user_id)
        self.assertEqual(expired["member_image_quota"], 0)
        self.assertEqual(expired["image_quota"], 3)
        self.assertEqual(expired["membership"]["status"], "expired")


if __name__ == "__main__":
    unittest.main()

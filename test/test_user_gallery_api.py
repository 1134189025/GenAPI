from __future__ import annotations

import base64
import importlib
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient


PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)
PNG_B64 = base64.b64encode(PNG_BYTES).decode("ascii")


class UserGalleryAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.data_dir = base / "data"
        self.old_data_dir = os.environ.get("GENAPI_DATA_DIR")
        self.old_config_file = os.environ.get("GENAPI_CONFIG_FILE")
        self.old_user_db = os.environ.get("GENAPI_USER_DATABASE_URL")
        self.old_jwt_secret = os.environ.get("JWT_SECRET")
        os.environ["GENAPI_DATA_DIR"] = str(self.data_dir)
        os.environ["GENAPI_CONFIG_FILE"] = str(base / "config.json")
        os.environ["GENAPI_USER_DATABASE_URL"] = f"sqlite:///{base / 'users.db'}"
        os.environ["JWT_SECRET"] = "unit-test-secret-with-at-least-32-bytes"
        self._clear_modules()
        app_module = importlib.import_module("api.app")
        self.client = TestClient(app_module.create_app())
        self.user_module = importlib.import_module("services.user_service")
        self.gallery_module = importlib.import_module("services.gallery_service")
        self.user_service = self.user_module.user_service
        self.gallery_service = self.gallery_module.gallery_service
        self.admin_headers = self.auth_headers(self.create_admin())

    def tearDown(self) -> None:
        self.client.close()
        engine = getattr(self.user_service, "engine", None)
        if engine is not None:
            engine.dispose()
        self._restore_env("GENAPI_DATA_DIR", self.old_data_dir)
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

    @staticmethod
    def auth_headers(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def create_admin(self) -> str:
        response = self.client.post(
            "/api/setup/admin",
            json={"email": "admin@example.com", "password": "AdminPass123!"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return str(response.json()["token"])

    def create_user(self, email: str, *, image_quota: int = 1) -> tuple[str, dict[str, str]]:
        created = self.client.post(
            "/api/admin/users",
            headers=self.admin_headers,
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

    def set_gallery_created_at(self, item_id: str, created_at: datetime) -> None:
        with self.user_service.Session() as session:
            row = session.get(self.user_module.UserGalleryImageModel, item_id)
            self.assertIsNotNone(row)
            row.created_at = created_at
            row.updated_at = created_at
            session.commit()

    def test_gallery_routes_require_auth_and_enforce_owner_scope(self) -> None:
        owner_id, owner_headers = self.create_user("owner@example.com")
        _, other_headers = self.create_user("other@example.com")
        item = self.gallery_service.save_user_image(user_id=owner_id, image_data=PNG_BYTES)

        self.assertEqual(self.client.get("/api/gallery/images").status_code, 401)

        listed = self.client.get("/api/gallery/images", headers=owner_headers)
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual([listed_item["id"] for listed_item in listed.json()["items"]], [item["id"]])
        self.assertIn("next_cursor", listed.json())
        self.assertIn("has_more", listed.json())
        self.assertNotIn("b64_json", listed.json()["items"][0])
        self.assertNotIn("storage_path", listed.json()["items"][0])

        detail = self.client.get(f"/api/gallery/images/{item['id']}", headers=owner_headers)
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(detail.json()["item"]["content_url"], f"/api/gallery/images/{item['id']}/content")

        content = self.client.get(f"/api/gallery/images/{item['id']}/content", headers=owner_headers)
        self.assertEqual(content.status_code, 200, content.text)
        self.assertEqual(content.content, PNG_BYTES)
        self.assertTrue(content.headers["content-type"].startswith("image/png"))

        self.assertEqual(self.client.get(f"/api/gallery/images/{item['id']}", headers=other_headers).status_code, 404)
        self.assertEqual(
            self.client.get(f"/api/gallery/images/{item['id']}/content", headers=other_headers).status_code,
            404,
        )
        self.assertEqual(self.client.delete(f"/api/gallery/images/{item['id']}", headers=other_headers).status_code, 404)

        deleted = self.client.delete(f"/api/gallery/images/{item['id']}", headers=owner_headers)
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertEqual(deleted.json(), {"ok": True})
        self.assertEqual(self.client.get(f"/api/gallery/images/{item['id']}/content", headers=owner_headers).status_code, 404)

    def test_gallery_list_accepts_limit_cursor_and_keeps_owner_scope(self) -> None:
        owner_id, owner_headers = self.create_user("paged-owner@example.com")
        other_id, _ = self.create_user("paged-other@example.com")
        owner_items = [
            self.gallery_service.save_user_image(user_id=owner_id, image_data=PNG_BYTES)
            for _ in range(3)
        ]
        other_item = self.gallery_service.save_user_image(user_id=other_id, image_data=PNG_BYTES)
        base = datetime(2026, 1, 4, 12, 0, tzinfo=timezone.utc)
        self.set_gallery_created_at(str(owner_items[0]["id"]), base)
        self.set_gallery_created_at(str(other_item["id"]), base - timedelta(seconds=1))
        self.set_gallery_created_at(str(owner_items[1]["id"]), base - timedelta(seconds=2))
        self.set_gallery_created_at(str(owner_items[2]["id"]), base - timedelta(seconds=3))

        first_page = self.client.get("/api/gallery/images?limit=2", headers=owner_headers)
        self.assertEqual(first_page.status_code, 200, first_page.text)
        first_payload = first_page.json()
        self.assertEqual([item["id"] for item in first_payload["items"]], [owner_items[0]["id"], owner_items[1]["id"]])
        self.assertTrue(first_payload["has_more"])
        self.assertTrue(first_payload["next_cursor"])
        self.assertTrue(all(item["user_id"] == owner_id for item in first_payload["items"]))

        second_page = self.client.get(
            f"/api/gallery/images?limit=2&cursor={first_payload['next_cursor']}",
            headers=owner_headers,
        )
        self.assertEqual(second_page.status_code, 200, second_page.text)
        second_payload = second_page.json()
        self.assertEqual([item["id"] for item in second_payload["items"]], [owner_items[2]["id"]])
        self.assertFalse(second_payload["has_more"])
        self.assertIsNone(second_payload["next_cursor"])
        self.assertTrue(all(item["user_id"] == owner_id for item in second_payload["items"]))

    def test_gallery_list_invalid_cursor_returns_error_envelope(self) -> None:
        _, headers = self.create_user("bad-cursor@example.com")

        response = self.client.get("/api/gallery/images?cursor=not-base64-json", headers=headers)

        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(
            response.json(),
            {"detail": {"error": {"message": "invalid gallery cursor", "code": "bad_request"}}},
        )

    def test_image_generation_success_writes_gallery_record_and_augments_response(self) -> None:
        _, headers = self.create_user("generator@example.com", image_quota=1)

        with patch(
            "api.ai.openai_v1_image_generations.handle",
            return_value={"created": 123, "data": [{"b64_json": PNG_B64, "revised_prompt": "a small cat"}]},
        ):
            response = self.client.post(
                "/api/image/generations",
                headers=headers,
                json={"prompt": "draw", "model": "gpt-image-2", "n": 1},
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        item = payload["data"][0]
        self.assertEqual(item["b64_json"], PNG_B64)
        self.assertTrue(item["gallery_id"])
        self.assertEqual(item["content_url"], f"/api/gallery/images/{item['gallery_id']}/content")
        self.assertEqual(item["url"], item["content_url"])
        self.assertTrue(item["expires_at"])

        content = self.client.get(item["content_url"], headers=headers)
        self.assertEqual(content.status_code, 200, content.text)
        self.assertEqual(content.content, PNG_BYTES)
        listed = self.client.get("/api/gallery/images", headers=headers)
        self.assertEqual(len(listed.json()["items"]), 1)
        self.assertNotIn("b64_json", listed.json()["items"][0])
        self.assertFalse(any((self.data_dir / "images").rglob("*.png")) if (self.data_dir / "images").exists() else False)
        self.assertEqual(self.client.get("/api/auth/me", headers=headers).json()["user"]["image_quota"], 0)

    def test_image_generation_records_target_and_actual_resolution_metadata(self) -> None:
        _, headers = self.create_user("resolution-generator@example.com", image_quota=1)

        with patch(
            "api.ai.openai_v1_image_generations.handle",
            return_value={"created": 123, "data": [{"b64_json": PNG_B64, "revised_prompt": "wide result"}]},
        ):
            response = self.client.post(
                "/api/image/generations",
                headers=headers,
                json={"prompt": "draw", "model": "gpt-image-2", "n": 1, "size": "1536x864"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        item = response.json()["data"][0]
        self.assertEqual(item["width"], 1)
        self.assertEqual(item["height"], 1)
        self.assertEqual(item["target_size"], "1536x864")
        self.assertEqual(item["target_width"], 1536)
        self.assertEqual(item["target_height"], 864)

        listed = self.client.get("/api/gallery/images", headers=headers)
        self.assertEqual(listed.status_code, 200, listed.text)
        gallery_item = listed.json()["items"][0]
        self.assertEqual(gallery_item["width"], 1)
        self.assertEqual(gallery_item["height"], 1)
        self.assertEqual(gallery_item["target_size"], "1536x864")
        self.assertEqual(gallery_item["target_width"], 1536)
        self.assertEqual(gallery_item["target_height"], 864)

    def test_image_generation_rejects_non_preset_resolution(self) -> None:
        _, headers = self.create_user("bad-resolution@example.com", image_quota=1)

        with patch("api.ai.openai_v1_image_generations.handle") as handle:
            response = self.client.post(
                "/api/image/generations",
                headers=headers,
                json={"prompt": "draw", "model": "gpt-image-2", "n": 1, "size": "123x456"},
            )

        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("unsupported image size", response.text)
        handle.assert_not_called()
        self.assertEqual(self.client.get("/api/auth/me", headers=headers).json()["user"]["image_quota"], 1)

    def test_image_edit_success_writes_gallery_record(self) -> None:
        _, headers = self.create_user("editor@example.com", image_quota=1)

        with patch(
            "api.ai.openai_v1_image_edit.handle",
            return_value={"created": 123, "data": [{"b64_json": PNG_B64, "revised_prompt": "edited"}]},
        ):
            response = self.client.post(
                "/api/image/edits",
                headers=headers,
                data={"prompt": "edit", "model": "gpt-image-2", "n": "1"},
                files={"image": ("source.png", PNG_BYTES, "image/png")},
            )

        self.assertEqual(response.status_code, 200, response.text)
        item = response.json()["data"][0]
        self.assertTrue(item["gallery_id"])
        listed = self.client.get("/api/gallery/images", headers=headers)
        self.assertEqual(listed.json()["items"][0]["source"], "edit")
        self.assertEqual(self.client.get(item["content_url"], headers=headers).content, PNG_BYTES)

    def test_image_edit_rejects_non_preset_resolution_before_spending_quota(self) -> None:
        _, headers = self.create_user("bad-edit-resolution@example.com", image_quota=1)

        with patch("api.ai.openai_v1_image_edit.handle") as handle:
            response = self.client.post(
                "/api/image/edits",
                headers=headers,
                data={"prompt": "edit", "model": "gpt-image-2", "n": "1", "size": "123x456"},
                files={"image": ("source.png", PNG_BYTES, "image/png")},
            )

        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("unsupported image size", response.text)
        handle.assert_not_called()
        self.assertEqual(self.client.get("/api/auth/me", headers=headers).json()["user"]["image_quota"], 1)

    def test_failed_image_generation_does_not_write_gallery_record_or_spend_quota(self) -> None:
        _, headers = self.create_user("failed@example.com", image_quota=1)

        with patch("api.ai.openai_v1_image_generations.handle", side_effect=RuntimeError("upstream failed")):
            response = self.client.post(
                "/api/image/generations",
                headers=headers,
                json={"prompt": "draw", "model": "gpt-image-2", "n": 1},
            )

        self.assertEqual(response.status_code, 502, response.text)
        listed = self.client.get("/api/gallery/images", headers=headers)
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(listed.json()["items"], [])
        self.assertEqual(self.client.get("/api/auth/me", headers=headers).json()["user"]["image_quota"], 1)


if __name__ == "__main__":
    unittest.main()

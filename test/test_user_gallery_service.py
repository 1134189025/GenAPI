from __future__ import annotations

import base64
import importlib
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


class UserGalleryServiceTests(unittest.TestCase):
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
        self.user_module = importlib.import_module("services.user_service")
        self.gallery_module = importlib.import_module("services.gallery_service")
        self.user_service = self.user_module.user_service
        self.gallery_service = self.gallery_module.gallery_service

    def tearDown(self) -> None:
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

    def create_user(self, email: str) -> dict[str, object]:
        return self.user_service.create_user(
            email=email,
            password="UserPass123!",
            role="user",
            enabled=True,
            image_quota=1,
            image_concurrency=1,
        )

    def set_gallery_order(self, ordered_items: list[dict[str, object]], *, start_at: datetime | None = None) -> list[str]:
        base = start_at or datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        ordered_ids = [
            f"00000000-0000-0000-0000-{index:012d}"
            for index in range(len(ordered_items), 0, -1)
        ]
        with self.user_service.Session() as session:
            for item, image_id in zip(ordered_items, ordered_ids):
                row = session.get(self.user_module.UserGalleryImageModel, item["id"])
                self.assertIsNotNone(row)
                row.id = image_id
                row.created_at = base
                row.updated_at = base
            session.commit()
        return ordered_ids

    def set_gallery_created_at(self, item_id: str, created_at: datetime) -> None:
        with self.user_service.Session() as session:
            row = session.get(self.user_module.UserGalleryImageModel, item_id)
            self.assertIsNotNone(row)
            row.created_at = created_at
            row.updated_at = created_at
            session.commit()

    def test_save_user_image_writes_private_gallery_file_and_safe_metadata(self) -> None:
        user = self.create_user("artist@example.com")
        now = self.user_module.utc_now()

        item = self.gallery_service.save_user_image(
            user_id=str(user["id"]),
            image_data=PNG_BYTES,
            prompt="draw a cat",
            model="gpt-image-2",
            source="generation",
            revised_prompt="draw a small cat",
        )

        self.assertEqual(item["user_id"], user["id"])
        self.assertEqual(item["source"], "generation")
        self.assertEqual(item["share_status"], "private")
        self.assertIsNone(item["share_token"])
        self.assertEqual(item["content_url"], f"/api/gallery/images/{item['id']}/content")
        self.assertEqual(item["url"], item["content_url"])
        self.assertNotIn("storage_path", item)
        self.assertNotIn("b64_json", item)

        expires_at = self.user_module.parse_optional_datetime(item["expires_at"])
        self.assertIsNotNone(expires_at)
        self.assertGreaterEqual(expires_at, now + timedelta(days=7, seconds=-2))
        self.assertLessEqual(expires_at, now + timedelta(days=7, seconds=2))

        with self.user_service.Session() as session:
            row = session.get(self.user_module.UserGalleryImageModel, item["id"])
            self.assertIsNotNone(row)
            self.assertEqual(row.user_id, user["id"])
            self.assertFalse(Path(row.storage_path).is_absolute())
            path = self.gallery_service.resolve_storage_path(row.storage_path)

        self.assertEqual(path.read_bytes(), PNG_BYTES)
        self.assertEqual(path.resolve().relative_to((self.data_dir / "gallery_images").resolve()), Path(row.storage_path))
        public_images = self.data_dir / "images"
        self.assertFalse(any(public_images.rglob("*.png")) if public_images.exists() else False)

        listed = self.gallery_service.list_user_images(str(user["id"]))
        self.assertEqual([listed_item["id"] for listed_item in listed], [item["id"]])
        self.assertNotIn("b64_json", listed[0])
        self.assertNotIn("storage_path", listed[0])

    def test_owner_scope_is_enforced_and_delete_removes_file(self) -> None:
        owner = self.create_user("owner@example.com")
        other = self.create_user("other@example.com")
        item = self.gallery_service.save_user_image(user_id=str(owner["id"]), image_data=PNG_BYTES)
        with self.user_service.Session() as session:
            row = session.get(self.user_module.UserGalleryImageModel, item["id"])
            path = self.gallery_service.resolve_storage_path(row.storage_path)
        self.assertTrue(path.is_file())

        with self.assertRaises(self.user_module.UserServiceError) as get_error:
            self.gallery_service.get_user_image(str(other["id"]), str(item["id"]))
        self.assertEqual(get_error.exception.status_code, 404)

        with self.assertRaises(self.user_module.UserServiceError) as delete_error:
            self.gallery_service.delete_user_image(str(other["id"]), str(item["id"]))
        self.assertEqual(delete_error.exception.status_code, 404)
        self.assertTrue(path.is_file())

        self.gallery_service.delete_user_image(str(owner["id"]), str(item["id"]))

        self.assertFalse(path.exists())
        self.assertEqual(self.gallery_service.list_user_images(str(owner["id"])), [])
        with self.assertRaises(self.user_module.UserServiceError) as owner_get_error:
            self.gallery_service.get_user_image(str(owner["id"]), str(item["id"]))
        self.assertEqual(owner_get_error.exception.status_code, 404)

    def test_expired_images_are_hidden_from_list_and_detail(self) -> None:
        user = self.create_user("expired@example.com")
        expired_at = self.user_module.utc_now() - timedelta(seconds=1)

        item = self.gallery_service.save_user_image(
            user_id=str(user["id"]),
            image_data=PNG_BYTES,
            expires_at=expired_at,
        )

        self.assertEqual(self.gallery_service.list_user_images(str(user["id"])), [])
        with self.assertRaises(self.user_module.UserServiceError) as error:
            self.gallery_service.get_user_image(str(user["id"]), str(item["id"]))
        self.assertEqual(error.exception.status_code, 404)

    def test_list_user_images_page_uses_stable_cursor_for_equal_created_at_rows(self) -> None:
        user = self.create_user("stable@example.com")
        saved = [
            self.gallery_service.save_user_image(user_id=str(user["id"]), image_data=PNG_BYTES)
            for _ in range(3)
        ]
        ordered_ids = self.set_gallery_order(saved)

        first_page = self.gallery_service.list_user_images_page(str(user["id"]), limit=2)
        second_page = self.gallery_service.list_user_images_page(
            str(user["id"]),
            limit=2,
            cursor=first_page["next_cursor"],
        )

        self.assertEqual([item["id"] for item in first_page["items"]], ordered_ids[:2])
        self.assertTrue(first_page["has_more"])
        self.assertTrue(first_page["next_cursor"])
        self.assertEqual([item["id"] for item in second_page["items"]], ordered_ids[2:])
        self.assertFalse(second_page["has_more"])
        self.assertIsNone(second_page["next_cursor"])
        self.assertEqual(
            [item["id"] for item in first_page["items"] + second_page["items"]],
            ordered_ids,
        )

    def test_list_user_images_page_rejects_invalid_cursor(self) -> None:
        with self.assertRaises(self.user_module.UserServiceError) as error:
            self.gallery_service.list_user_images_page("user-id", cursor="not-base64-json")

        self.assertEqual(error.exception.status_code, 400)
        self.assertEqual(error.exception.code, "bad_request")
        self.assertEqual(error.exception.message, "invalid gallery cursor")

    def test_list_user_images_page_skips_missing_files_and_keeps_scanning(self) -> None:
        user = self.create_user("missing-page@example.com")
        saved = [
            self.gallery_service.save_user_image(user_id=str(user["id"]), image_data=PNG_BYTES)
            for _ in range(4)
        ]
        base = datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc)
        for offset, item in enumerate(saved):
            self.set_gallery_created_at(str(item["id"]), base - timedelta(seconds=offset))

        missing_id = str(saved[1]["id"])
        with self.user_service.Session() as session:
            row = session.get(self.user_module.UserGalleryImageModel, missing_id)
            self.assertIsNotNone(row)
            missing_path = self.gallery_service.resolve_storage_path(row.storage_path)
        missing_path.unlink()

        first_page = self.gallery_service.list_user_images_page(str(user["id"]), limit=2)
        second_page = self.gallery_service.list_user_images_page(
            str(user["id"]),
            limit=2,
            cursor=first_page["next_cursor"],
        )

        self.assertEqual([item["id"] for item in first_page["items"]], [saved[0]["id"], saved[2]["id"]])
        self.assertTrue(first_page["has_more"])
        self.assertTrue(first_page["next_cursor"])
        self.assertEqual([item["id"] for item in second_page["items"]], [saved[3]["id"]])
        self.assertFalse(second_page["has_more"])
        self.assertIsNone(second_page["next_cursor"])
        with self.user_service.Session() as session:
            row = session.get(self.user_module.UserGalleryImageModel, missing_id)
            self.assertEqual(row.status, "missing")

    def test_list_user_images_page_bounds_missing_file_scan_and_returns_resume_cursor(self) -> None:
        user = self.create_user("bounded-missing-page@example.com")
        saved = [
            self.gallery_service.save_user_image(user_id=str(user["id"]), image_data=PNG_BYTES)
            for _ in range(5)
        ]
        base = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)
        for offset, item in enumerate(saved):
            self.set_gallery_created_at(str(item["id"]), base - timedelta(seconds=offset))

        missing_ids = {str(item["id"]) for item in saved[:3]}
        with self.user_service.Session() as session:
            for missing_id in missing_ids:
                row = session.get(self.user_module.UserGalleryImageModel, missing_id)
                self.assertIsNotNone(row)
                self.gallery_service.resolve_storage_path(row.storage_path).unlink()

        old_max_scan_rows = self.gallery_module.GALLERY_PAGE_MAX_SCAN_ROWS
        self.gallery_module.GALLERY_PAGE_MAX_SCAN_ROWS = 3
        try:
            first_page = self.gallery_service.list_user_images_page(str(user["id"]), limit=2)
            second_page = self.gallery_service.list_user_images_page(
                str(user["id"]),
                limit=2,
                cursor=first_page["next_cursor"],
            )
        finally:
            self.gallery_module.GALLERY_PAGE_MAX_SCAN_ROWS = old_max_scan_rows

        self.assertEqual(first_page["items"], [])
        self.assertTrue(first_page["has_more"])
        self.assertTrue(first_page["next_cursor"])
        self.assertEqual([item["id"] for item in second_page["items"]], [saved[3]["id"], saved[4]["id"]])
        self.assertFalse(second_page["has_more"])
        self.assertIsNone(second_page["next_cursor"])
        with self.user_service.Session() as session:
            statuses = {
                row.id: row.status
                for row in session.query(self.user_module.UserGalleryImageModel)
                .filter(self.user_module.UserGalleryImageModel.id.in_(missing_ids))
                .all()
            }
        self.assertEqual(statuses, {missing_id: "missing" for missing_id in missing_ids})

    def test_list_user_images_page_keeps_owner_scope_across_cursor_pages(self) -> None:
        owner = self.create_user("page-owner@example.com")
        other = self.create_user("page-other@example.com")
        owner_new = self.gallery_service.save_user_image(user_id=str(owner["id"]), image_data=PNG_BYTES)
        other_middle = self.gallery_service.save_user_image(user_id=str(other["id"]), image_data=PNG_BYTES)
        owner_old = self.gallery_service.save_user_image(user_id=str(owner["id"]), image_data=PNG_BYTES)
        base = datetime(2026, 1, 3, 12, 0, tzinfo=timezone.utc)
        self.set_gallery_created_at(str(owner_new["id"]), base)
        self.set_gallery_created_at(str(other_middle["id"]), base - timedelta(seconds=1))
        self.set_gallery_created_at(str(owner_old["id"]), base - timedelta(seconds=2))

        owner_first = self.gallery_service.list_user_images_page(str(owner["id"]), limit=1)
        owner_second = self.gallery_service.list_user_images_page(
            str(owner["id"]),
            limit=1,
            cursor=owner_first["next_cursor"],
        )
        other_page = self.gallery_service.list_user_images_page(
            str(other["id"]),
            limit=10,
            cursor=owner_first["next_cursor"],
        )

        self.assertEqual([item["id"] for item in owner_first["items"]], [owner_new["id"]])
        self.assertEqual([item["id"] for item in owner_second["items"]], [owner_old["id"]])
        self.assertEqual([item["id"] for item in other_page["items"]], [other_middle["id"]])


if __name__ == "__main__":
    unittest.main()

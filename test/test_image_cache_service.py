from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

from services.image_cache_service import ImageCacheLimits, cleanup_image_cache, get_image_cache_status


def write_image(path: Path, size: int, *, age_days: int = 0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    modified_at = time.time() - age_days * 86400
    os.utime(path, (modified_at, modified_at))
    return path


class ImageCacheServiceTests(unittest.TestCase):
    def test_status_reports_current_usage_and_limits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            write_image(root / "2026" / "04" / "29" / "a.png", 10)
            write_image(root / "2026" / "04" / "29" / "b.png", 15)

            status = get_image_cache_status(
                root,
                ImageCacheLimits(retention_days=30, max_size_mb=1, auto_delete_enabled=True),
            )

            self.assertEqual(status["total_size_bytes"], 25)
            self.assertEqual(status["file_count"], 2)
            self.assertEqual(status["max_size_bytes"], 1024 * 1024)
            self.assertEqual(status["auto_delete_enabled"], True)

    def test_cleanup_removes_expired_images(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            old_file = write_image(root / "2026" / "03" / "01" / "old.png", 10, age_days=40)
            fresh_file = write_image(root / "2026" / "04" / "29" / "fresh.png", 10, age_days=1)

            result = cleanup_image_cache(
                root,
                ImageCacheLimits(retention_days=30, max_size_mb=1, auto_delete_enabled=True),
            )

            self.assertFalse(old_file.exists())
            self.assertTrue(fresh_file.exists())
            self.assertEqual(result["removed_files"], 1)
            self.assertEqual(result["removed_expired_files"], 1)
            self.assertEqual(result["removed_oversize_files"], 0)

    def test_cleanup_removes_oldest_images_until_under_size_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            oldest = write_image(root / "2026" / "04" / "27" / "oldest.png", 600, age_days=3)
            middle = write_image(root / "2026" / "04" / "28" / "middle.png", 600, age_days=2)
            newest = write_image(root / "2026" / "04" / "29" / "newest.png", 600, age_days=1)

            result = cleanup_image_cache(
                root,
                ImageCacheLimits(retention_days=30, max_size_mb=0.001, auto_delete_enabled=True),
            )

            self.assertFalse(oldest.exists())
            self.assertFalse(middle.exists())
            self.assertTrue(newest.exists())
            self.assertEqual(result["removed_expired_files"], 0)
            self.assertEqual(result["removed_oversize_files"], 2)
            self.assertLessEqual(result["total_size_bytes"], result["max_size_bytes"])

    def test_cleanup_prefers_unprotected_files_before_protected_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            old_file = write_image(root / "2026" / "04" / "28" / "old.png", 600, age_days=1)
            new_file = write_image(root / "2026" / "04" / "29" / "new.png", 300, age_days=0)

            result = cleanup_image_cache(
                root,
                ImageCacheLimits(retention_days=30, max_size_mb=0.0005, auto_delete_enabled=True),
                protected_paths={new_file},
            )

            self.assertFalse(old_file.exists())
            self.assertTrue(new_file.exists())
            self.assertEqual(result["removed_oversize_files"], 1)
            self.assertLessEqual(result["total_size_bytes"], result["max_size_bytes"])

    def test_cleanup_deletes_recent_images_when_needed_for_hard_size_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            old_file = write_image(root / "2026" / "04" / "28" / "old.png", 600, age_days=1)
            new_file = write_image(root / "2026" / "04" / "29" / "new.png", 600, age_days=0)

            result = cleanup_image_cache(
                root,
                ImageCacheLimits(retention_days=30, max_size_mb=0.0005, auto_delete_enabled=True),
            )

            self.assertFalse(old_file.exists())
            self.assertFalse(new_file.exists())
            self.assertEqual(result["removed_oversize_files"], 2)
            self.assertLessEqual(result["total_size_bytes"], result["max_size_bytes"])

    def test_cleanup_deletes_protected_file_when_needed_for_hard_size_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            new_file = write_image(root / "2026" / "04" / "29" / "new.png", 600, age_days=0)

            result = cleanup_image_cache(
                root,
                ImageCacheLimits(retention_days=30, max_size_mb=0.0005, auto_delete_enabled=True),
                protected_paths={new_file},
            )

            self.assertFalse(new_file.exists())
            self.assertEqual(result["removed_oversize_files"], 1)
            self.assertLessEqual(result["total_size_bytes"], result["max_size_bytes"])

    def test_cleanup_does_not_delete_when_auto_delete_is_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            old_file = write_image(root / "2026" / "03" / "01" / "old.png", 10, age_days=40)

            result = cleanup_image_cache(
                root,
                ImageCacheLimits(retention_days=30, max_size_mb=1, auto_delete_enabled=False),
            )

            self.assertTrue(old_file.exists())
            self.assertEqual(result["removed_files"], 0)


if __name__ == "__main__":
    unittest.main()

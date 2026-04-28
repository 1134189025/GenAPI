import unittest

from services import image_service


class VanishingRelativePath:
    def as_posix(self) -> str:
        return "2026/04/29/gone.png"


class VanishingImagePath:
    name = "gone.png"

    def is_file(self) -> bool:
        return True

    def relative_to(self, root):
        return VanishingRelativePath()

    def stat(self):
        raise FileNotFoundError("gone.png")


class FakeImagesDir:
    def rglob(self, pattern: str):
        return [VanishingImagePath()]


class FakeConfig:
    images_dir = FakeImagesDir()

    def cleanup_old_images(self) -> int:
        return 0


class ImageServiceTests(unittest.TestCase):
    def test_list_images_skips_files_removed_during_scan(self) -> None:
        old_config = image_service.config
        image_service.config = FakeConfig()
        try:
            result = image_service.list_images("https://example.test")
        finally:
            image_service.config = old_config

        self.assertEqual(result["items"], [])
        self.assertEqual(result["groups"], [])


if __name__ == "__main__":
    unittest.main()

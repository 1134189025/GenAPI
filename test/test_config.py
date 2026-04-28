import tempfile
import unittest
from pathlib import Path


class ConfigLoadingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from services import config as config_module

        cls.config_module = config_module

    def test_load_settings_ignores_directory_config_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base_dir = Path(tmp_dir)
            data_dir = base_dir / "data"
            config_dir = base_dir / "config.json"

            config_dir.mkdir()

            module = self.config_module
            old_base_dir = module.BASE_DIR
            old_data_dir = module.DATA_DIR
            old_config_file = module.CONFIG_FILE
            try:
                module.BASE_DIR = base_dir
                module.DATA_DIR = data_dir
                module.CONFIG_FILE = config_dir

                settings = module._load_settings()

                self.assertEqual(settings.refresh_account_interval_minute, 5)
            finally:
                module.BASE_DIR = old_base_dir
                module.DATA_DIR = old_data_dir
                module.CONFIG_FILE = old_config_file

    def test_default_config_file_lives_in_data_directory(self) -> None:
        module = self.config_module

        self.assertEqual(module.CONFIG_FILE, module.DATA_DIR / "config.json")

    def test_dockerfile_does_not_require_ignored_root_config_json(self) -> None:
        root_dir = Path(__file__).resolve().parents[1]
        dockerfile = (root_dir / "Dockerfile").read_text(encoding="utf-8")

        self.assertNotIn("COPY config.json", dockerfile)


if __name__ == "__main__":
    unittest.main()

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

    def test_docker_build_context_excludes_runtime_secrets(self) -> None:
        root_dir = Path(__file__).resolve().parents[1]
        dockerignore = (root_dir / ".dockerignore").read_text(encoding="utf-8").splitlines()

        self.assertIn("data", dockerignore)
        self.assertIn(".env", dockerignore)
        self.assertIn("config.json", dockerignore)

    def test_default_compose_loads_env_file_for_container_runtime(self) -> None:
        root_dir = Path(__file__).resolve().parents[1]
        compose = (root_dir / "docker-compose.yml").read_text(encoding="utf-8")

        self.assertIn("env_file:", compose)
        self.assertIn(".env", compose)

    def test_dockerfile_uses_committed_frontend_lockfile(self) -> None:
        root_dir = Path(__file__).resolve().parents[1]
        dockerfile = (root_dir / "Dockerfile").read_text(encoding="utf-8")

        self.assertIn("bun install --frozen-lockfile", dockerfile)
        self.assertNotIn("RUN npm install", dockerfile)

    def test_image_cache_settings_have_safe_defaults_and_are_normalized(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = self.config_module.ConfigStore(Path(tmp_dir) / "config.json")

            self.assertEqual(store.image_retention_days, 30)
            self.assertEqual(store.image_cache_max_size_mb, 10240)
            self.assertTrue(store.image_cache_auto_delete_enabled)
            self.assertEqual(store.get()["image_cache_max_size_mb"], 10240)
            self.assertEqual(store.get()["image_cache_auto_delete_enabled"], True)

            updated = store.update(
                {
                    "image_retention_days": "0",
                    "image_cache_max_size_mb": "0",
                    "image_cache_auto_delete_enabled": "false",
                }
            )

            self.assertEqual(updated["image_retention_days"], 1)
            self.assertEqual(updated["image_cache_max_size_mb"], 1)
            self.assertEqual(updated["image_cache_auto_delete_enabled"], False)

    def test_config_update_writes_owner_read_write_only_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "config.json"
            store = self.config_module.ConfigStore(path)

            store.update({"base_url": "https://public.example.com"})

            self.assertEqual(path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()

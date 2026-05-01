import tempfile
import unittest
import importlib
import io
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path


class ConfigLoadingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from services import config as config_module

        cls.config_module = config_module

    def setUp(self) -> None:
        self.config_module = sys.modules.get("services.config") or importlib.import_module("services.config")

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

    def test_genapi_data_dir_overrides_default_data_directory(self) -> None:
        module = self.config_module
        old_data_dir = os.environ.get("GENAPI_DATA_DIR")
        with tempfile.TemporaryDirectory() as tmp_dir:
            try:
                os.environ["GENAPI_DATA_DIR"] = str(Path(tmp_dir) / "runtime-data")

                reloaded = importlib.reload(module)

                self.assertEqual(reloaded.DATA_DIR, Path(tmp_dir) / "runtime-data")
                self.assertEqual(reloaded.CONFIG_FILE, reloaded.DATA_DIR / "config.json")
            finally:
                if old_data_dir is None:
                    os.environ.pop("GENAPI_DATA_DIR", None)
                else:
                    os.environ["GENAPI_DATA_DIR"] = old_data_dir
                self.config_module = importlib.reload(module)

    def test_pyinstaller_resource_path_is_used_for_version_file(self) -> None:
        module = self.config_module
        old_frozen = getattr(sys, "frozen", None)
        old_meipass = getattr(sys, "_MEIPASS", None)
        with tempfile.TemporaryDirectory() as tmp_dir:
            resource_dir = Path(tmp_dir)
            (resource_dir / "VERSION").write_text("0.1.6\n", encoding="utf-8")
            try:
                sys.frozen = True
                sys._MEIPASS = str(resource_dir)

                reloaded = importlib.reload(module)
                store = reloaded.ConfigStore(resource_dir / "config.json")

                self.assertEqual(reloaded.VERSION_FILE, resource_dir / "VERSION")
                self.assertEqual(store.app_version, "0.1.6")
            finally:
                if old_frozen is None:
                    try:
                        delattr(sys, "frozen")
                    except AttributeError:
                        pass
                else:
                    sys.frozen = old_frozen
                if old_meipass is None:
                    try:
                        delattr(sys, "_MEIPASS")
                    except AttributeError:
                        pass
                else:
                    sys._MEIPASS = old_meipass
                self.config_module = importlib.reload(module)

    def test_main_cli_uses_version_flag_and_host_port_env(self) -> None:
        old_host = os.environ.get("GENAPI_HOST")
        old_port = os.environ.get("GENAPI_PORT")
        try:
            os.environ["GENAPI_HOST"] = "0.0.0.0"
            os.environ["GENAPI_PORT"] = "9016"
            sys.modules.pop("main", None)
            main_module = importlib.import_module("main")

            calls: list[dict[str, object]] = []
            original_run = main_module.uvicorn.run
            main_module.uvicorn.run = lambda app, **kwargs: calls.append(kwargs)
            try:
                self.assertEqual(main_module.main([]), 0)
                self.assertEqual(calls[-1]["host"], "0.0.0.0")
                self.assertEqual(calls[-1]["port"], 9016)

                self.assertEqual(main_module.main(["--host", "127.0.0.1", "--port", "8016"]), 0)
                self.assertEqual(calls[-1]["host"], "127.0.0.1")
                self.assertEqual(calls[-1]["port"], 8016)

                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(main_module.main(["--version"]), 0)
                self.assertEqual(output.getvalue().strip(), str(main_module.app.version))
                self.assertEqual(len(calls), 2)
            finally:
                main_module.uvicorn.run = original_run
                sys.modules.pop("main", None)
        finally:
            if old_host is None:
                os.environ.pop("GENAPI_HOST", None)
            else:
                os.environ["GENAPI_HOST"] = old_host
            if old_port is None:
                os.environ.pop("GENAPI_PORT", None)
            else:
                os.environ["GENAPI_PORT"] = old_port

    def test_dockerfile_does_not_require_ignored_root_config_json(self) -> None:
        root_dir = Path(__file__).resolve().parents[1]
        dockerfile = (root_dir / "Dockerfile").read_text(encoding="utf-8")

        self.assertNotIn("COPY config.json", dockerfile)

    def test_dockerfile_defaults_to_docker_update_mode(self) -> None:
        root_dir = Path(__file__).resolve().parents[1]
        dockerfile = (root_dir / "Dockerfile").read_text(encoding="utf-8")

        self.assertIn("GENAPI_DEPLOYMENT_MODE=docker", dockerfile)
        self.assertIn("GENAPI_BUILD_TYPE=docker", dockerfile)

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

    def test_default_compose_enables_docker_web_updates(self) -> None:
        root_dir = Path(__file__).resolve().parents[1]
        compose = (root_dir / "docker-compose.yml").read_text(encoding="utf-8")

        self.assertIn("/var/run/docker.sock:/var/run/docker.sock", compose)
        self.assertIn(".:/app/deployment:ro", compose)
        self.assertIn("GENAPI_UPDATE_COMPOSE_DIR=/app/deployment", compose)
        self.assertIn("GENAPI_UPDATE_COMPOSE_FILE=docker-compose.yml", compose)
        self.assertIn("GENAPI_UPDATE_HOST_COMPOSE_DIR=${PWD}", compose)
        self.assertIn("GENAPI_UPDATE_HOST_DATA_DIR=${PWD}/data", compose)
        self.assertIn("GENAPI_UPDATE_SERVICE=app", compose)
        self.assertIn("GENAPI_UPDATE_HELPER_IMAGE=docker:28-cli", compose)

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

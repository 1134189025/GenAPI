from __future__ import annotations

from dataclasses import dataclass
import json
import os
import sys
from pathlib import Path

from services.storage.base import StorageBackend
from services.storage.base import atomic_write_text
from services.image_cache_service import ImageCacheLimits, cleanup_image_cache, get_image_cache_status

BASE_DIR = Path(__file__).resolve().parents[1]


def _resource_dir() -> Path:
    meipass = getattr(sys, "_MEIPASS", "")
    if bool(getattr(sys, "frozen", False)) and meipass:
        return Path(str(meipass)).expanduser()
    return BASE_DIR


def _configured_data_dir() -> Path:
    configured = str(os.getenv("GENAPI_DATA_DIR") or "").strip()
    return Path(configured).expanduser() if configured else BASE_DIR / "data"


RESOURCE_DIR = _resource_dir()
DATA_DIR = _configured_data_dir()
VERSION_FILE = RESOURCE_DIR / "VERSION"


@dataclass(frozen=True)
class LoadedSettings:
    refresh_account_interval_minute: int


def _configured_config_file() -> Path:
    configured = str(os.getenv("GENAPI_CONFIG_FILE") or os.getenv("CHATGPT2API_CONFIG_FILE") or "").strip()
    return Path(configured).expanduser() if configured else DATA_DIR / "config.json"


CONFIG_FILE = _configured_config_file()


def _read_json_object(path: Path, *, name: str) -> dict[str, object]:
    if not path.exists():
        return {}
    if path.is_dir():
        print(
            f"Warning: {name} at '{path}' is a directory, ignoring it and falling back to other configuration sources.",
            file=sys.stderr,
        )
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _legacy_config_file() -> Path:
    return BASE_DIR / "config.json"


def _read_effective_config(path: Path) -> dict[str, object]:
    data = _read_json_object(path, name="config.json")
    if data or path == _legacy_config_file():
        return data
    return _read_json_object(_legacy_config_file(), name="legacy config.json")


def _load_settings() -> LoadedSettings:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    raw_config = _read_effective_config(CONFIG_FILE)

    try:
        refresh_interval = int(raw_config.get("refresh_account_interval_minute", 5))
    except (TypeError, ValueError):
        refresh_interval = 5

    return LoadedSettings(
        refresh_account_interval_minute=refresh_interval,
    )


class ConfigStore:
    def __init__(self, path: Path):
        self.path = path
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.data = self._load()
        self._storage_backend: StorageBackend | None = None

    def _load(self) -> dict[str, object]:
        return _read_effective_config(self.path)

    def _save(self) -> None:
        atomic_write_text(self.path, json.dumps(self.data, ensure_ascii=False, indent=2) + "\n")

    @property
    def accounts_file(self) -> Path:
        return DATA_DIR / "accounts.json"

    @property
    def refresh_account_interval_minute(self) -> int:
        try:
            return int(self.data.get("refresh_account_interval_minute", 5))
        except (TypeError, ValueError):
            return 5

    @property
    def image_retention_days(self) -> int:
        try:
            return max(1, int(self.data.get("image_retention_days", 30)))
        except (TypeError, ValueError):
            return 30

    @property
    def image_cache_max_size_mb(self) -> int:
        try:
            return max(1, int(float(self.data.get("image_cache_max_size_mb", 10240))))
        except (TypeError, ValueError):
            return 10240

    @property
    def image_cache_auto_delete_enabled(self) -> bool:
        value = self.data.get("image_cache_auto_delete_enabled", True)
        if isinstance(value, str):
            return value.strip().lower() not in {"0", "false", "no", "off"}
        return bool(value)

    @property
    def image_cache_limits(self) -> ImageCacheLimits:
        return ImageCacheLimits(
            retention_days=self.image_retention_days,
            max_size_mb=self.image_cache_max_size_mb,
            auto_delete_enabled=self.image_cache_auto_delete_enabled,
        )

    @property
    def auto_remove_invalid_accounts(self) -> bool:
        value = self.data.get("auto_remove_invalid_accounts", False)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    @property
    def auto_remove_rate_limited_accounts(self) -> bool:
        value = self.data.get("auto_remove_rate_limited_accounts", False)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    @property
    def log_levels(self) -> list[str]:
        levels = self.data.get("log_levels")
        if not isinstance(levels, list):
            return []
        allowed = {"debug", "info", "warning", "error"}
        return [level for item in levels if (level := str(item or "").strip().lower()) in allowed]

    @property
    def images_dir(self) -> Path:
        path = DATA_DIR / "images"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def cleanup_old_images(self, protected_paths: set[Path] | None = None) -> int:
        result = cleanup_image_cache(self.images_dir, self.image_cache_limits, protected_paths=protected_paths)
        return int(result.get("removed_files") or 0)

    def get_image_cache_status(self) -> dict[str, object]:
        return get_image_cache_status(self.images_dir, self.image_cache_limits)

    @property
    def base_url(self) -> str:
        return str(
            os.getenv("GENAPI_BASE_URL")
            or os.getenv("CHATGPT2API_BASE_URL")
            or self.data.get("base_url")
            or ""
        ).strip().rstrip("/")

    @property
    def app_version(self) -> str:
        try:
            value = VERSION_FILE.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return "0.0.0"
        return value or "0.0.0"

    def get(self) -> dict[str, object]:
        data = dict(self.data)
        data["refresh_account_interval_minute"] = self.refresh_account_interval_minute
        data["image_retention_days"] = self.image_retention_days
        data["image_cache_max_size_mb"] = self.image_cache_max_size_mb
        data["image_cache_auto_delete_enabled"] = self.image_cache_auto_delete_enabled
        data["auto_remove_invalid_accounts"] = self.auto_remove_invalid_accounts
        data["auto_remove_rate_limited_accounts"] = self.auto_remove_rate_limited_accounts
        data["log_levels"] = self.log_levels
        data.pop("auth-key", None)
        return data

    def get_proxy_settings(self) -> str:
        return str(self.data.get("proxy") or "").strip()

    def update(self, data: dict[str, object]) -> dict[str, object]:
        next_data = dict(self.data)
        next_data.update(dict(data or {}))
        self.data = next_data
        self._save()
        return self.get()

    def get_storage_backend(self) -> StorageBackend:
        """获取存储后端实例（单例）"""
        if self._storage_backend is None:
            from services.storage.factory import create_storage_backend
            self._storage_backend = create_storage_backend(DATA_DIR)
        return self._storage_backend


config = ConfigStore(CONFIG_FILE)

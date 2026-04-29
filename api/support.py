from __future__ import annotations

from ipaddress import ip_address
from pathlib import Path
import sys
from threading import Event, Thread
from urllib.parse import urlsplit

from fastapi import HTTPException, Request

from services.account_service import account_service
from services.config import config
from services.user_service import user_service

BASE_DIR = Path(__file__).resolve().parents[1]


def _resolve_web_dist_dir() -> Path:
    meipass = getattr(sys, "_MEIPASS", "")
    if bool(getattr(sys, "frozen", False)) and meipass:
        return Path(str(meipass)).expanduser() / "web_dist"
    return BASE_DIR / "web_dist"


WEB_DIST_DIR = _resolve_web_dist_dir()


def extract_bearer_token(authorization: str | None) -> str:
    scheme, _, value = str(authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return ""
    return value.strip()


def require_identity(authorization: str | None) -> dict[str, object]:
    token = extract_bearer_token(authorization)
    identity = user_service.authenticate_token(token)
    if identity is None:
        raise HTTPException(status_code=401, detail={"error": "authorization is invalid"})
    return identity


def require_admin(authorization: str | None) -> dict[str, object]:
    identity = require_identity(authorization)
    if identity.get("role") != "admin":
        raise HTTPException(status_code=403, detail={"error": "admin permission required"})
    return identity


def _has_control_chars(value: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 for char in value)


def _safe_host(value: object) -> str:
    host = str(value or "").strip()
    if not host or _has_control_chars(host):
        return ""
    if any(char in host for char in "/?#@"):
        return ""
    try:
        parsed = urlsplit(f"//{host}")
        hostname = parsed.hostname
        parsed.port
    except ValueError:
        return ""
    if not parsed.netloc or parsed.username or parsed.password or not hostname:
        return ""
    normalized = hostname.rstrip(".").lower()
    if normalized == "localhost":
        return parsed.netloc
    try:
        address = ip_address(normalized)
    except ValueError:
        return ""
    if address.is_loopback or address.is_private or address.is_link_local:
        return parsed.netloc
    return ""


def _safe_base_url(value: object) -> str:
    base_url = str(value or "").strip().rstrip("/")
    if not base_url or _has_control_chars(base_url):
        return ""
    try:
        parsed = urlsplit(base_url)
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"}:
        return ""
    try:
        hostname = parsed.hostname
        parsed.port
    except ValueError:
        return ""
    if not hostname:
        return ""
    if not parsed.netloc or parsed.username or parsed.password:
        return ""
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


def resolve_image_base_url(request: Request) -> str:
    configured = _safe_base_url(config.base_url)
    if configured:
        return configured
    scheme = str(request.url.scheme or "http").strip().lower()
    if scheme not in {"http", "https"}:
        scheme = "http"
    host = _safe_host(request.headers.get("host")) or _safe_host(request.url.netloc)
    return f"{scheme}://{host}" if host else ""


def raise_image_quota_error(exc: Exception) -> None:
    message = str(exc)
    if "no available image quota" in message.lower():
        raise HTTPException(status_code=429, detail={"error": "no available image quota"}) from exc
    raise HTTPException(status_code=502, detail={"error": message}) from exc


def sanitize_cpa_pool(pool: dict | None) -> dict | None:
    if not isinstance(pool, dict):
        return None
    return {key: value for key, value in pool.items() if key != "secret_key"}


def sanitize_cpa_pools(pools: list[dict]) -> list[dict]:
    return [sanitized for pool in pools if (sanitized := sanitize_cpa_pool(pool)) is not None]


def sanitize_sub2api_server(server: dict | None) -> dict | None:
    if not isinstance(server, dict):
        return None
    sanitized = {key: value for key, value in server.items() if key not in {"password", "api_key"}}
    sanitized["has_api_key"] = bool(str(server.get("api_key") or "").strip())
    return sanitized


def sanitize_sub2api_servers(servers: list[dict]) -> list[dict]:
    return [sanitized for server in servers if (sanitized := sanitize_sub2api_server(server)) is not None]


def start_limited_account_watcher(stop_event: Event) -> Thread:
    interval_seconds = config.refresh_account_interval_minute * 60

    def worker() -> None:
        while not stop_event.is_set():
            try:
                limited_tokens = account_service.list_limited_tokens()
                if limited_tokens:
                    print(f"[account-limited-watcher] checking {len(limited_tokens)} limited accounts")
                    account_service.refresh_accounts(limited_tokens)
            except Exception as exc:
                print(f"[account-limited-watcher] fail {exc}")
            stop_event.wait(interval_seconds)

    thread = Thread(target=worker, name="limited-account-watcher", daemon=True)
    thread.start()
    return thread


def start_image_cache_watcher(stop_event: Event, interval_seconds: int = 3600) -> Thread:
    def worker() -> None:
        while not stop_event.is_set():
            try:
                removed = config.cleanup_old_images()
                if removed:
                    print(f"[image-cache-watcher] removed {removed} cached images")
            except Exception as exc:
                print(f"[image-cache-watcher] fail {exc}")
            stop_event.wait(interval_seconds)

    thread = Thread(target=worker, name="image-cache-watcher", daemon=True)
    thread.start()
    return thread


def resolve_web_asset(requested_path: str) -> Path | None:
    if not WEB_DIST_DIR.exists():
        return None
    clean_path = requested_path.strip("/")
    base_dir = WEB_DIST_DIR.resolve()
    candidates = [base_dir / "index.html"] if not clean_path else [
        base_dir / Path(clean_path),
        base_dir / clean_path / "index.html",
        base_dir / f"{clean_path}.html",
    ]
    for candidate in candidates:
        try:
            candidate.resolve().relative_to(base_dir)
        except ValueError:
            continue
        if candidate.is_file():
            return candidate
    return None

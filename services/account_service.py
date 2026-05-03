from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import base64
import hashlib
import json
from threading import Lock
from typing import Any
from datetime import datetime

from curl_cffi.requests import Session

from services.config import config
from services.log_service import (
    LOG_TYPE_ACCOUNT,
    log_service,
)
from services.proxy_service import proxy_settings
from services.storage.base import StorageBackend
from utils.helper import anonymize_token, redact_sensitive_text


class AccountService:
    ACCOUNT_TYPE_MAP = {
        "free": "Free",
        "plus": "Plus",
        "prolite": "ProLite",
        "pro_lite": "ProLite",
        "team": "Team",
        "pro": "Pro",
        "personal": "Plus",
        "business": "Team",
        "enterprise": "Team",
    }

    def __init__(self, storage_backend: StorageBackend):
        self.storage = storage_backend
        self._lock = Lock()
        self._index = 0
        self._accounts = self._load_accounts()

    @staticmethod
    def _clean_token(value: Any) -> str:
        return str(value or "").strip()

    def _clean_tokens(self, tokens: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen = set()
        for token in tokens:
            value = self._clean_token(token)
            if value and value not in seen:
                seen.add(value)
                cleaned.append(value)
        return cleaned

    def _find_account_index(self, access_token: str) -> int:
        access_token = self._clean_token(access_token)
        for index, item in enumerate(self._accounts):
            if self._clean_token(item.get("access_token")) == access_token:
                return index
        for index, item in enumerate(self._accounts):
            token = self._clean_token(item.get("access_token"))
            if token and (self._token_id(token) == access_token or anonymize_token(token) == access_token):
                return index
        return -1

    @staticmethod
    def _token_id(access_token: str) -> str:
        return hashlib.sha1(access_token.encode("utf-8")).hexdigest()[:16]

    def _resolve_token_identifier_locked(self, identifier: str) -> str:
        identifier = self._clean_token(identifier)
        if not identifier:
            return ""
        index = self._find_account_index(identifier)
        if index >= 0:
            return self._clean_token(self._accounts[index].get("access_token"))
        return identifier

    def resolve_access_tokens(self, identifiers: list[str]) -> list[str]:
        cleaned = self._clean_tokens(identifiers)
        if not cleaned:
            return []
        with self._lock:
            resolved: list[str] = []
            seen = set()
            for identifier in cleaned:
                token = self._resolve_token_identifier_locked(identifier)
                if token and token not in seen:
                    seen.add(token)
                    resolved.append(token)
            return resolved

    @staticmethod
    def _is_image_account_available(account: dict) -> bool:
        if not isinstance(account, dict):
            return False
        status = str(account.get("status") or "").strip()
        if status in {"禁用", "限流", "异常"}:
            return False
        if bool(account.get("image_quota_unknown")):
            return True
        return int(account.get("quota") or 0) > 0

    @staticmethod
    def _is_problem_account(account: dict) -> bool:
        if not isinstance(account, dict):
            return False
        status = str(account.get("status") or "").strip()
        if status in {"限流", "异常"}:
            return True
        if status == "禁用":
            return False
        if bool(account.get("image_quota_unknown")):
            return False
        try:
            quota = int(account.get("quota") or 0)
        except (TypeError, ValueError):
            quota = 0
        return status == "正常" and quota <= 0

    @staticmethod
    def _is_token_invalid_error_message(message: str) -> bool:
        text = str(message or "").lower()
        return (
            "/backend-api/me failed: http 401" in text
            or "http 401" in text and "backend-api/me" in text
            or "status=401" in text
            or "status 401" in text
            or "token_invalidated" in text
            or "token_revoked" in text
            or "authentication token has been invalidated" in text
            or "invalidated oauth token" in text
        )

    @staticmethod
    def _is_rate_limited_error_message(message: str) -> bool:
        text = str(message or "").lower()
        return (
            "http 429" in text
            or "status=429" in text
            or "status 429" in text
            or "rate limit" in text
            or "rate_limit" in text
            or "too many requests" in text
            or "usage limit" in text
            or ("image quota" in text and "limit" in text)
        )

    def _decode_access_token_payload(self, access_token: str) -> dict[str, Any]:
        parts = self._clean_token(access_token).split(".")
        if len(parts) < 2:
            return {}
        payload = parts[1]
        payload += "=" * (-len(payload) % 4)
        try:
            decoded = base64.urlsafe_b64decode(payload.encode("utf-8"))
            data = json.loads(decoded.decode("utf-8"))
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _normalize_account_type(self, value: Any) -> str | None:
        return self.ACCOUNT_TYPE_MAP.get(self._clean_token(value).lower())

    def _search_account_type(self, value: Any) -> str | None:
        if isinstance(value, dict):
            for key, item in value.items():
                key_text = self._clean_token(key).lower()
                if any(flag in key_text for flag in ("plan", "type", "subscription", "workspace", "tier")):
                    matched = self._normalize_account_type(item)
                    if matched:
                        return matched
                    matched = self._search_account_type(item)
                    if matched:
                        return matched
            return None
        if isinstance(value, list):
            for item in value:
                matched = self._search_account_type(item)
                if matched:
                    return matched
            return None
        return None

    def _detect_account_type(self, access_token: str, me_payload: Any, init_payload: Any) -> str:
        token_payload = self._decode_access_token_payload(access_token)

        auth_payload = token_payload.get("https://api.openai.com/auth")
        print("检测账户类型响应", auth_payload)
        if isinstance(auth_payload, dict):
            matched = self._normalize_account_type(auth_payload.get("chatgpt_plan_type"))
            if matched:
                return matched

        for payload in (me_payload, init_payload, token_payload):
            matched = self._search_account_type(payload)
            if matched:
                return matched

        return "Free"

    def _normalize_account(self, item: dict) -> dict | None:
        if not isinstance(item, dict):
            return None
        access_token = self._clean_token(item.get("access_token"))
        if not access_token:
            return None
        normalized = dict(item)
        normalized["access_token"] = access_token
        normalized["type"] = self._clean_token(normalized.get("type")) or "Free"
        normalized["status"] = self._clean_token(normalized.get("status")) or "正常"
        normalized["quota"] = int(normalized.get("quota") if normalized.get("quota") is not None else 0)
        if normalized["quota"] < 0:
            normalized["quota"] = 0
        normalized["image_quota_unknown"] = bool(normalized.get("image_quota_unknown"))
        normalized["email"] = self._clean_token(normalized.get("email")) or None
        normalized["user_id"] = self._clean_token(normalized.get("user_id")) or None
        limits_progress = normalized.get("limits_progress")
        normalized["limits_progress"] = limits_progress if isinstance(limits_progress, list) else []
        normalized["default_model_slug"] = self._clean_token(normalized.get("default_model_slug")) or None
        normalized["restore_at"] = self._clean_token(normalized.get("restore_at")) or None
        normalized["success"] = int(normalized.get("success") or 0)
        normalized["fail"] = int(normalized.get("fail") or 0)
        normalized["last_used_at"] = normalized.get("last_used_at")
        return normalized

    @staticmethod
    def _extract_quota_and_restore_at(limits_progress: list[Any]) -> tuple[int, str | None, bool]:
        quota = 0
        restore_at = None
        for item in limits_progress:
            if not isinstance(item, dict) or item.get("feature_name") != "image_gen":
                continue
            quota = int(item.get("remaining") or 0)
            restore_at = str(item.get("reset_after") or "").strip() or None
            return quota, restore_at, False
        return quota, restore_at, True

    def _load_accounts(self) -> list[dict]:
        accounts = self.storage.load_accounts()
        return [normalized for item in accounts if (normalized := self._normalize_account(item)) is not None]

    def _save_accounts(self) -> None:
        self.storage.save_accounts(self._accounts)

    def _reload_accounts_locked(self) -> None:
        self._accounts = self._load_accounts()
        if self._accounts:
            self._index %= len(self._accounts)
        else:
            self._index = 0

    def _delete_persisted_accounts(self, access_tokens: list[str]) -> None:
        try:
            self.storage.delete_accounts(access_tokens)
        except NotImplementedError:
            self._save_accounts()

    def _build_remote_headers(self, access_token: str) -> tuple[dict[str, str], str]:
        account = self.get_account(access_token) or {}
        user_agent = self._clean_token(account.get("user-agent") or account.get("user_agent"))
        impersonate = self._clean_token(account.get("impersonate")) or "edge101"
        headers = {
            "authorization": f"Bearer {access_token}",
            "accept": "*/*",
            "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
            "content-type": "application/json",
            "oai-language": "zh-CN",
            "origin": "https://chatgpt.com",
            "referer": "https://chatgpt.com/",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "user-agent": user_agent
                          or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                             "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "sec-ch-ua": self._clean_token(account.get("sec-ch-ua"))
                         or '"Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
            "sec-ch-ua-mobile": self._clean_token(account.get("sec-ch-ua-mobile")) or "?0",
            "sec-ch-ua-platform": self._clean_token(account.get("sec-ch-ua-platform")) or '"Windows"',
        }
        device_id = self._clean_token(account.get("oai-device-id") or account.get("oai_device_id"))
        session_id = self._clean_token(account.get("oai-session-id") or account.get("oai_session_id"))
        if device_id:
            headers["oai-device-id"] = device_id
        if session_id:
            headers["oai-session-id"] = session_id
        return headers, impersonate

    def _public_items(self, accounts: list[dict]) -> list[dict]:
        return [
            {
                "id": self._token_id(access_token),
                "access_token": anonymize_token(access_token),
                "token_ref": anonymize_token(access_token),
                "type": account.get("type") or "Free",
                "status": account.get("status") or "正常",
                "quota": account.get("quota") if account.get("quota") is not None else 0,
                "imageQuotaUnknown": bool(account.get("image_quota_unknown")),
                "email": account.get("email"),
                "user_id": account.get("user_id"),
                "limits_progress": account.get("limits_progress") or [],
                "default_model_slug": account.get("default_model_slug"),
                "restoreAt": account.get("restore_at"),
                "success": int(account.get("success") or 0),
                "fail": int(account.get("fail") or 0),
                "lastUsedAt": account.get("last_used_at"),
            }
            for account in accounts
            if (access_token := self._clean_token(account.get("access_token")))
        ]

    def list_tokens(self) -> list[str]:
        with self._lock:
            return [token for item in self._accounts if (token := self._clean_token(item.get("access_token")))]

    def _list_available_candidate_tokens(self, excluded_tokens: set[str] | None = None) -> list[str]:
        excluded = {self._clean_token(token) for token in (excluded_tokens or set()) if self._clean_token(token)}
        return [
            token
            for item in self._accounts
            if self._is_image_account_available(item)
               and (token := self._clean_token(item.get("access_token")))
               and token not in excluded
        ]

    def _pick_next_candidate_token(self, excluded_tokens: set[str] | None = None) -> str:
        with self._lock:
            tokens = self._list_available_candidate_tokens(excluded_tokens)
            if not tokens:
                raise RuntimeError("no available image quota")
            access_token = tokens[self._index % len(tokens)]
            self._index += 1
            return access_token

    def refresh_account_state(self, access_token: str) -> dict | None:
        token_ref = anonymize_token(access_token)
        try:
            remote_info = self.fetch_remote_info(access_token)
        except Exception as exc:
            message = redact_sensitive_text(str(exc), [access_token])
            print(f"[account-available] refresh token={token_ref} fail {message}")
            if self._is_token_invalid_error_message(message):
                return self.mark_invalid_token(access_token, "refresh_account_state")
            if self._is_rate_limited_error_message(message):
                return self.mark_rate_limited_token(access_token, "refresh_account_state")
            return None
        return self.update_account(access_token, remote_info)

    def get_available_access_token(self, excluded_tokens: set[str] | None = None) -> str:
        attempted_tokens: set[str] = set()
        initial_excluded = {
            self._clean_token(token)
            for token in (excluded_tokens or set())
            if self._clean_token(token)
        }
        while True:
            access_token = self._pick_next_candidate_token(excluded_tokens=attempted_tokens | initial_excluded)
            attempted_tokens.add(access_token)
            token_ref = anonymize_token(access_token)
            account = self.refresh_account_state(access_token)
            if self._is_image_account_available(account or {}):
                return access_token
            print(
                f"[account-available] skip token={token_ref} "
                f"quota={account.get('quota') if account else 'unknown'} "
                f"status={account.get('status') if account else 'unknown'}"
            )

    def get_text_access_token(self) -> str:
        with self._lock:
            for account in self._accounts:
                status = self._clean_token(account.get("status"))
                if status not in {"禁用", "异常"}:
                    return self._clean_token(account.get("access_token"))
        return ""

    def remove_invalid_token(self, access_token: str, event: str) -> bool:
        if not config.auto_remove_invalid_accounts:
            return False
        removed = self.remove_token(access_token)
        if removed:
            log_service.add(LOG_TYPE_ACCOUNT, "自动移除异常账号", {"source": event, "token": anonymize_token(access_token)})
        return removed

    def mark_invalid_token(self, access_token: str, event: str) -> dict | None:
        if self.remove_invalid_token(access_token, event):
            return None
        return self.update_account(
            access_token,
            {
                "status": "异常",
                "quota": 0,
                "image_quota_unknown": False,
            },
        )

    def mark_rate_limited_token(self, access_token: str, event: str, restore_at: str | None = None) -> dict | None:
        return self.update_account(
            access_token,
            {
                "status": "限流",
                "quota": 0,
                "image_quota_unknown": False,
                "restore_at": restore_at or None,
            },
        )

    def next_token(self) -> str:
        return self.get_available_access_token()

    def has_available_account(self) -> bool:
        with self._lock:
            return any(self._is_image_account_available(item) for item in self._accounts)

    def get_account(self, access_token: str) -> dict | None:
        access_token = self._clean_token(access_token)
        if not access_token:
            return None
        with self._lock:
            index = self._find_account_index(access_token)
            if index >= 0:
                return dict(self._accounts[index])
        return None

    def list_accounts(self) -> list[dict]:
        with self._lock:
            return self._public_items(self._accounts)

    def public_account(self, account: dict) -> dict | None:
        items = self._public_items([account])
        return items[0] if items else None

    def export_accounts(
        self,
        actor: dict[str, object] | None = None,
        *,
        request_ip: str = "",
        user_agent: str = "",
    ) -> list[dict]:
        with self._lock:
            items = [dict(account) for account in self._accounts]
            payload: dict[str, object] = {"count": len(items)}
            payload["token_refs"] = [
                anonymize_token(account.get("access_token"))
                for account in items
                if self._clean_token(account.get("access_token"))
            ]
            if request_ip:
                payload["request_ip"] = request_ip
            if user_agent:
                payload["user_agent"] = user_agent
            if actor:
                payload.update(
                    {
                        "actor_id": self._clean_token(actor.get("id")),
                        "actor_email": self._clean_token(actor.get("email")),
                        "actor_name": self._clean_token(actor.get("name")),
                        "actor_role": self._clean_token(actor.get("role")),
                    }
                )
            log_service.add(LOG_TYPE_ACCOUNT, "导出账号", payload)
            return items

    def list_limited_tokens(self) -> list[str]:
        with self._lock:
            return [
                token
                for item in self._accounts
                if item.get("status") == "限流"
                   and (token := self._clean_token(item.get("access_token")))
            ]

    def list_problem_tokens(self) -> list[str]:
        with self._lock:
            self._reload_accounts_locked()
            return [
                token
                for item in self._accounts
                if self._is_problem_account(item)
                   and (token := self._clean_token(item.get("access_token")))
            ]

    def add_accounts(self, tokens: list[str]) -> dict:
        cleaned_tokens = self._clean_tokens(tokens)
        if not cleaned_tokens:
            return {"added": 0, "skipped": 0, "items": self.list_accounts()}

        with self._lock:
            self._reload_accounts_locked()
            indexed = {self._clean_token(item.get("access_token")): dict(item) for item in self._accounts}
            added = 0
            skipped = 0
            for access_token in cleaned_tokens:
                current = indexed.get(access_token)
                if current is None:
                    added += 1
                    current = {}
                else:
                    skipped += 1
                account = self._normalize_account(
                    {
                        **current,
                        "access_token": access_token,
                        "type": str(current.get("type") or "Free"),
                    }
                )
                if account is not None:
                    indexed[access_token] = account
            self._accounts = list(indexed.values())
            self._save_accounts()
            items = self._public_items(self._accounts)
            log_service.add(LOG_TYPE_ACCOUNT, f"新增 {added} 个账号，跳过 {skipped} 个", {"added": added, "skipped": skipped})
        return {"added": added, "skipped": skipped, "items": items}

    def delete_accounts(self, tokens: list[str]) -> dict:
        requested_tokens = self._clean_tokens(tokens)
        target_set = set(requested_tokens)
        if not target_set:
            return {"removed": 0, "items": self.list_accounts()}
        with self._lock:
            target_set = {self._resolve_token_identifier_locked(token) for token in requested_tokens}
            target_set = {token for token in target_set if token}
            before = len(self._accounts)
            self._accounts = [item for item in self._accounts if
                              self._clean_token(item.get("access_token")) not in target_set]
            removed = before - len(self._accounts)
            if self._accounts:
                self._index %= len(self._accounts)
            else:
                self._index = 0
            if removed:
                self._delete_persisted_accounts(list(target_set))
                log_service.add(LOG_TYPE_ACCOUNT, f"删除 {removed} 个账号", {"removed": removed})
            items = self._public_items(self._accounts)
        return {"removed": removed, "items": items}

    def remove_token(self, access_token: str) -> bool:
        return bool(self.delete_accounts([access_token])["removed"])

    def update_account(self, access_token: str, updates: dict) -> dict | None:
        access_token = self._clean_token(access_token)
        if not access_token:
            return None
        with self._lock:
            self._reload_accounts_locked()
            index = self._find_account_index(access_token)
            if index < 0:
                return None
            stored_token = self._clean_token(self._accounts[index].get("access_token"))
            account = self._normalize_account({**self._accounts[index], **updates, "access_token": stored_token})
            if account is None:
                return None
            access_token = self._clean_token(account.get("access_token"))
            if account.get("status") == "限流" and config.auto_remove_rate_limited_accounts:
                del self._accounts[index]
                self._delete_persisted_accounts([access_token])
                log_service.add(LOG_TYPE_ACCOUNT, "自动移除限流账号", {"token": anonymize_token(access_token)})
                return None
            self._accounts[index] = account
            self._save_accounts()
            log_service.add(LOG_TYPE_ACCOUNT, "更新账号", {"token": anonymize_token(access_token), "status": account.get("status")})
            return dict(account)
        return None

    def mark_image_result(self, access_token: str, success: bool) -> dict | None:
        access_token = self._clean_token(access_token)
        if not access_token:
            return None
        with self._lock:
            self._reload_accounts_locked()
            index = self._find_account_index(access_token)
            if index < 0:
                return None
            next_item = dict(self._accounts[index])
            next_item["last_used_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            image_quota_unknown = bool(next_item.get("image_quota_unknown"))
            if success:
                next_item["success"] = int(next_item.get("success") or 0) + 1
                if not image_quota_unknown:
                    next_item["quota"] = max(0, int(next_item.get("quota") or 0) - 1)
                if not image_quota_unknown and next_item["quota"] == 0:
                    next_item["status"] = "限流"
                    next_item["restore_at"] = next_item.get("restore_at") or None
                elif next_item.get("status") == "限流":
                    next_item["status"] = "正常"
            else:
                next_item["fail"] = int(next_item.get("fail") or 0) + 1
            account = self._normalize_account(next_item)
            if account is None:
                return None
            if account.get("status") == "限流" and config.auto_remove_rate_limited_accounts:
                del self._accounts[index]
                self._delete_persisted_accounts([access_token])
                log_service.add(LOG_TYPE_ACCOUNT, "自动移除限流账号", {"token": anonymize_token(access_token)})
                return None
            self._accounts[index] = account
            self._save_accounts()
            return dict(account)
        return None

    def fetch_remote_info(self, access_token: str) -> dict[str, Any]:
        access_token = self._clean_token(access_token)
        if not access_token:
            raise ValueError("access_token is required")

        headers, impersonate = self._build_remote_headers(access_token)
        token_ref = anonymize_token(access_token)
        print(f"[account-refresh] start {token_ref}")
        session = Session(**proxy_settings.build_session_kwargs(impersonate=impersonate, verify=True))
        session.headers.update(headers)
        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                me_future = executor.submit(
                    session.get,
                    "https://chatgpt.com/backend-api/me",
                    headers={
                        "x-openai-target-path": "/backend-api/me",
                        "x-openai-target-route": "/backend-api/me",
                    },
                    timeout=20,
                )
                init_future = executor.submit(
                    session.post,
                    "https://chatgpt.com/backend-api/conversation/init",
                    json={
                        "gizmo_id": None,
                        "requested_default_model": None,
                        "conversation_id": None,
                        "timezone_offset_min": -480,
                    },
                    timeout=20,
                )

                me_response = me_future.result()
                init_response = init_future.result()

            if me_response.status_code != 200:
                raise RuntimeError(f"/backend-api/me failed: HTTP {me_response.status_code}")
            me_payload = me_response.json()

            if init_response.status_code != 200:
                raise RuntimeError(f"/backend-api/conversation/init failed: HTTP {init_response.status_code}")
            init_payload = init_response.json()

            limits_progress = init_payload.get("limits_progress")
            if not isinstance(limits_progress, list):
                limits_progress = []

            account_type = self._detect_account_type(access_token, me_payload, init_payload)
            quota, restore_at, image_quota_unknown = self._extract_quota_and_restore_at(limits_progress)
            status = "正常" if image_quota_unknown and account_type != "Free" else ("限流" if quota == 0 else "正常")

            result = {
                "email": me_payload.get("email"),
                "user_id": me_payload.get("id"),
                "type": account_type,
                "quota": quota,
                "image_quota_unknown": image_quota_unknown,
                "limits_progress": limits_progress,
                "default_model_slug": init_payload.get("default_model_slug"),
                "restore_at": restore_at,
                "status": status,
            }
            print(
                "[account-refresh] ok",
                token_ref,
                f"quota={result.get('quota')}",
                f"restore_at={result.get('restore_at')}",
            )
            return result
        finally:
            session.close()

    def refresh_accounts(self, access_tokens: list[str]) -> dict[str, Any]:
        cleaned_tokens = self.resolve_access_tokens(access_tokens)
        if not cleaned_tokens:
            return {"refreshed": 0, "errors": [], "items": self.list_accounts()}

        refreshed = 0
        errors: list[dict[str, str]] = []
        max_workers = min(10, len(cleaned_tokens))

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {executor.submit(self.fetch_remote_info, access_token): access_token for access_token in
                          cleaned_tokens}
            for future in as_completed(future_map):
                access_token = future_map[future]
                try:
                    remote_info = future.result()
                    if self.update_account(access_token, remote_info) is not None:
                        refreshed += 1
                except Exception as exc:
                    message = redact_sensitive_text(str(exc), [access_token])
                    print(f"[account-refresh] fail {anonymize_token(access_token)} {message}")
                    if self._is_token_invalid_error_message(message):
                        self.mark_invalid_token(access_token, "refresh_accounts")
                        message = "检测到封号"
                    elif self._is_rate_limited_error_message(message):
                        self.mark_rate_limited_token(access_token, "refresh_accounts")
                        message = "检测到限流"
                    errors.append({"access_token": anonymize_token(access_token), "token_ref": anonymize_token(access_token), "error": message})

        print(f"[account-refresh] done refreshed={refreshed} errors={len(errors)} workers={max_workers}")
        return {
            "refreshed": refreshed,
            "errors": errors,
            "items": self.list_accounts(),
        }


account_service = AccountService(config.get_storage_backend())

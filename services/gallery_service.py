from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any

from sqlalchemy import and_, or_

from services.config import DATA_DIR
from services.user_service import (
    UserGalleryImageModel,
    UserModel,
    UserServiceError,
    as_utc,
    clean_string,
    iso,
    user_service,
    utc_now,
)

DEFAULT_GALLERY_IMAGE_TTL_DAYS = 7
GALLERY_CURSOR_VERSION = 1
GALLERY_PAGE_DEFAULT_LIMIT = 24
GALLERY_PAGE_MAX_LIMIT = 60
GALLERY_PAGE_MAX_SCAN_ROWS = 240
GALLERY_LEGACY_LIST_MAX_LIMIT = 200


def detect_image_content_type(image_data: bytes) -> tuple[str, str]:
    if image_data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", ".jpg"
    if image_data.startswith(b"RIFF") and image_data[8:12] == b"WEBP":
        return "image/webp", ".webp"
    if image_data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif", ".gif"
    return "image/png", ".png"


class GalleryService:
    def __init__(self, service=user_service, root: Path | None = None) -> None:
        self.user_service = service
        self.root = root or DATA_DIR / "gallery_images"
        self._file_lock = Lock()

    def ensure_root(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            self.root.chmod(0o700)
        except OSError:
            pass
        return self.root

    def resolve_storage_path(self, storage_path: str) -> Path:
        relative = Path(clean_string(storage_path))
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise UserServiceError("gallery image not found", status_code=404, code="not_found")
        root = self.ensure_root().resolve()
        path = (root / relative).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise UserServiceError("gallery image not found", status_code=404, code="not_found") from exc
        return path

    @staticmethod
    def _metadata(row: UserGalleryImageModel) -> dict[str, object]:
        try:
            value = json.loads(clean_string(row.metadata_json) or "{}")
        except (TypeError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _public_metadata(metadata: dict[str, object]) -> dict[str, object]:
        public: dict[str, object] = {}
        for key in ("width", "height", "target_size", "target_width", "target_height", "target_aspect_ratio"):
            if key in metadata:
                public[key] = metadata[key]
        return public

    @staticmethod
    def content_url(image_id: str) -> str:
        return f"/api/gallery/images/{clean_string(image_id)}/content"

    def _serialize(self, row: UserGalleryImageModel) -> dict[str, object]:
        content_url = self.content_url(str(row.id))
        storage_path = clean_string(row.storage_path)
        filename = Path(storage_path).name
        metadata = self._public_metadata(self._metadata(row))
        return {
            "id": row.id,
            "user_id": row.user_id,
            "source": clean_string(row.source) or "generation",
            "source_endpoint": clean_string(row.source_endpoint),
            "status": clean_string(row.status) or "available",
            "prompt": clean_string(row.prompt),
            "revised_prompt": clean_string(row.revised_prompt),
            "model": clean_string(row.model),
            "size": clean_string(row.size),
            "filename": filename,
            "content_type": clean_string(row.content_type) or "image/png",
            "size_bytes": int(row.size_bytes or 0),
            "byte_size": int(row.size_bytes or 0),
            "share_status": clean_string(row.share_status) or "private",
            "share_token": row.share_token,
            "created_at": iso(row.created_at),
            "updated_at": iso(row.updated_at),
            "expires_at": iso(row.expires_at),
            "content_url": content_url,
            "url": content_url,
            **metadata,
        }

    @staticmethod
    def _invalid_cursor() -> None:
        raise UserServiceError("invalid gallery cursor", status_code=400, code="bad_request")

    @staticmethod
    def _clamp_page_limit(limit: int | None) -> int:
        try:
            value = GALLERY_PAGE_DEFAULT_LIMIT if limit is None else int(limit)
        except (TypeError, ValueError):
            value = GALLERY_PAGE_DEFAULT_LIMIT
        return max(1, min(value, GALLERY_PAGE_MAX_LIMIT))

    @staticmethod
    def _encode_cursor_values(created_at: datetime | None, image_id: str) -> str:
        payload = {
            "v": GALLERY_CURSOR_VERSION,
            "created_at": iso(created_at),
            "id": clean_string(image_id),
        }
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    def _encode_cursor(self, row: UserGalleryImageModel) -> str:
        return self._encode_cursor_values(row.created_at, str(row.id))

    def _decode_cursor(self, cursor: str | None) -> tuple[datetime, str] | None:
        value = clean_string(cursor)
        if not value:
            return None
        try:
            padded = value + ("=" * (-len(value) % 4))
            payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
            if not isinstance(payload, dict) or payload.get("v") != GALLERY_CURSOR_VERSION:
                self._invalid_cursor()
            image_id = clean_string(payload.get("id"))
            created_at_value = clean_string(payload.get("created_at"))
            if not image_id or not created_at_value:
                self._invalid_cursor()
            if created_at_value.endswith("Z"):
                created_at_value = f"{created_at_value[:-1]}+00:00"
            created_at = as_utc(datetime.fromisoformat(created_at_value))
            if created_at is None:
                self._invalid_cursor()
            return created_at, image_id
        except UserServiceError:
            raise
        except (binascii.Error, json.JSONDecodeError, TypeError, UnicodeDecodeError, ValueError) as exc:
            raise UserServiceError("invalid gallery cursor", status_code=400, code="bad_request") from exc

    @staticmethod
    def _apply_cursor(query, cursor_state: tuple[datetime, str] | None):
        if cursor_state is None:
            return query
        created_at, image_id = cursor_state
        return query.filter(
            or_(
                UserGalleryImageModel.created_at < created_at,
                and_(
                    UserGalleryImageModel.created_at == created_at,
                    UserGalleryImageModel.id < image_id,
                ),
            )
        )

    def save_user_image(
        self,
        *,
        user_id: str,
        image_data: bytes,
        prompt: str = "",
        revised_prompt: str = "",
        model: str = "",
        size: str = "",
        source: str = "generation",
        source_endpoint: str = "",
        usage_event_id: str = "",
        expires_at: datetime | None = None,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object]:
        clean_user_id = clean_string(user_id)
        if not clean_user_id:
            raise UserServiceError("user not found", status_code=404, code="not_found")
        if not image_data:
            raise UserServiceError("image data is empty", status_code=400, code="bad_request")

        now = utc_now()
        expires = as_utc(expires_at) or now + timedelta(days=DEFAULT_GALLERY_IMAGE_TTL_DAYS)
        content_type, extension = detect_image_content_type(image_data)
        image_id = str(uuid.uuid4())
        relative_dir = Path(now.strftime("%Y"), now.strftime("%m"))
        relative_path = relative_dir / f"{image_id}{extension}"
        storage_path = relative_path.as_posix()
        path = self.resolve_storage_path(storage_path)
        digest = hashlib.sha256(image_data).hexdigest()
        row = UserGalleryImageModel(
            id=image_id,
            user_id=clean_user_id,
            usage_event_id=clean_string(usage_event_id) or None,
            source_endpoint=clean_string(source_endpoint),
            source=clean_string(source) or "generation",
            status="available",
            prompt=clean_string(prompt),
            revised_prompt=clean_string(revised_prompt),
            model=clean_string(model),
            size=clean_string(size),
            storage_path=storage_path,
            content_type=content_type,
            size_bytes=len(image_data),
            sha256=digest,
            share_status="private",
            share_token=None,
            metadata_json=json.dumps(metadata or {}, ensure_ascii=False, separators=(",", ":")),
            created_at=now,
            updated_at=now,
            expires_at=expires,
        )

        with self._file_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = path.with_name(f".{path.name}.{uuid.uuid4()}.tmp")
            fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(fd, "wb") as file:
                    file.write(image_data)
                os.replace(tmp_path, path)
                try:
                    os.chmod(path, 0o600)
                except OSError:
                    pass
            finally:
                if tmp_path.exists():
                    tmp_path.unlink(missing_ok=True)
        try:
            with self.user_service.Session() as session:
                user = session.get(UserModel, clean_user_id)
                if user is None or not bool(user.enabled):
                    raise UserServiceError("user not found", status_code=404, code="not_found")
                session.add(row)
                session.commit()
                return self._serialize(row)
        except Exception:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            raise

    def _available_query(self, session, user_id: str, now: datetime):
        return session.query(UserGalleryImageModel).filter(
            UserGalleryImageModel.user_id == clean_string(user_id),
            UserGalleryImageModel.status == "available",
            UserGalleryImageModel.deleted_at.is_(None),
            UserGalleryImageModel.expires_at > now,
        )

    def list_user_images_page(
        self,
        user_id: str,
        *,
        limit: int = GALLERY_PAGE_DEFAULT_LIMIT,
        cursor: str | None = None,
    ) -> dict[str, object]:
        page_limit = self._clamp_page_limit(limit)
        cursor_state = self._decode_cursor(cursor)
        now = utc_now()
        chunk_size = GALLERY_PAGE_MAX_LIMIT + 1
        max_scan_rows = max(page_limit + 1, int(GALLERY_PAGE_MAX_SCAN_ROWS))
        with self.user_service.Session() as session:
            rows: list[UserGalleryImageModel] = []
            changed = False
            scan_cursor = cursor_state
            scanned_rows = 0
            exhausted = False
            last_seen: tuple[datetime, str] | None = None

            while len(rows) <= page_limit and scanned_rows < max_scan_rows:
                remaining_scan = max_scan_rows - scanned_rows
                query_limit = min(chunk_size, remaining_scan)
                query = self._apply_cursor(self._available_query(session, user_id, now), scan_cursor)
                candidates = (
                    query.order_by(UserGalleryImageModel.created_at.desc(), UserGalleryImageModel.id.desc())
                    .limit(query_limit)
                    .all()
                )
                if not candidates:
                    exhausted = True
                    break

                for row in candidates:
                    scanned_rows += 1
                    row_created_at = as_utc(row.created_at)
                    if row_created_at is None:
                        continue
                    last_seen = (row_created_at, clean_string(row.id))
                    if not self.resolve_storage_path(str(row.storage_path)).is_file():
                        row.status = "missing"
                        row.updated_at = now
                        changed = True
                        continue
                    rows.append(row)
                    if len(rows) > page_limit:
                        break

                if len(candidates) < query_limit:
                    exhausted = True
                if len(rows) > page_limit or exhausted or last_seen is None or scanned_rows >= max_scan_rows:
                    break
                scan_cursor = last_seen

            if changed:
                session.commit()

            page_rows = rows[:page_limit]
            has_more = len(rows) > page_limit or (not exhausted and last_seen is not None)
            if len(rows) > page_limit and page_rows:
                next_cursor = self._encode_cursor(page_rows[-1])
            elif has_more and last_seen is not None:
                next_cursor = self._encode_cursor_values(*last_seen)
            else:
                next_cursor = None
            return {
                "items": [self._serialize(row) for row in page_rows],
                "next_cursor": next_cursor,
                "has_more": has_more,
            }

    def list_user_images(self, user_id: str, *, limit: int = 100) -> list[dict[str, object]]:
        try:
            target = int(limit)
        except (TypeError, ValueError):
            target = 100
        target = max(1, min(target, GALLERY_LEGACY_LIST_MAX_LIMIT))
        items: list[dict[str, object]] = []
        cursor: str | None = None
        while len(items) < target:
            page = self.list_user_images_page(
                user_id,
                limit=min(GALLERY_PAGE_MAX_LIMIT, target - len(items)),
                cursor=cursor,
            )
            items.extend(page["items"])
            cursor = str(page["next_cursor"] or "")
            if not page["has_more"] or not cursor:
                break
        return items

    def get_user_image(self, user_id: str, image_id: str) -> dict[str, object]:
        now = utc_now()
        with self.user_service.Session() as session:
            row = (
                self._available_query(session, user_id, now)
                .filter(UserGalleryImageModel.id == clean_string(image_id))
                .one_or_none()
            )
            if row is None:
                raise UserServiceError("gallery image not found", status_code=404, code="not_found")
            if not self.resolve_storage_path(str(row.storage_path)).is_file():
                row.status = "missing"
                row.updated_at = now
                session.commit()
                raise UserServiceError("gallery image not found", status_code=404, code="not_found")
            return self._serialize(row)

    def get_user_image_file(self, user_id: str, image_id: str) -> tuple[dict[str, object], Path]:
        now = utc_now()
        with self.user_service.Session() as session:
            row = (
                self._available_query(session, user_id, now)
                .filter(UserGalleryImageModel.id == clean_string(image_id))
                .one_or_none()
            )
            if row is None:
                raise UserServiceError("gallery image not found", status_code=404, code="not_found")
            path = self.resolve_storage_path(str(row.storage_path))
            if not path.is_file():
                row.status = "missing"
                row.updated_at = now
                session.commit()
                raise UserServiceError("gallery image not found", status_code=404, code="not_found")
            item = self._serialize(row)
        return item, path

    def delete_user_image(self, user_id: str, image_id: str) -> None:
        now = utc_now()
        path: Path | None = None
        with self.user_service.Session() as session:
            row = (
                self._available_query(session, user_id, now)
                .filter(UserGalleryImageModel.id == clean_string(image_id))
                .one_or_none()
            )
            if row is None:
                raise UserServiceError("gallery image not found", status_code=404, code="not_found")
            path = self.resolve_storage_path(str(row.storage_path))
            row.status = "deleted"
            row.deleted_at = now
            row.updated_at = now
            session.commit()
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    def discard_user_image(self, user_id: str, image_id: str) -> None:
        path: Path | None = None
        with self.user_service.Session() as session:
            row = (
                session.query(UserGalleryImageModel)
                .filter(
                    UserGalleryImageModel.user_id == clean_string(user_id),
                    UserGalleryImageModel.id == clean_string(image_id),
                )
                .one_or_none()
            )
            if row is None:
                return
            path = self.resolve_storage_path(str(row.storage_path))
            session.delete(row)
            session.commit()
        try:
            path.unlink()
        except FileNotFoundError:
            pass


gallery_service = GalleryService()

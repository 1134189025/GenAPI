from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError
from sqlalchemy import (
    Boolean,
    case,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    delete,
    exists,
    inspect,
    text,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased, declarative_base, sessionmaker

from services.config import DATA_DIR

Base = declarative_base()

UserRole = Literal["admin", "user"]
RedeemCodeType = Literal["image_quota", "concurrency", "invitation", "membership"]

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
JWT_ALGORITHM = "HS256"
IMAGE_COST_GGB = 5
LOGIN_FAILURE_THRESHOLD = 5
LOGIN_FAILURE_INITIAL_LOCK_SECONDS = 60
LOGIN_FAILURE_MAX_LOCK_SECONDS = 900
DEFAULT_STALE_IMAGE_QUOTA_SECONDS = 21_600
DEFAULT_STALE_IMAGE_QUOTA_RECOVERY_THROTTLE_SECONDS = 60
_STALE_RECOVERY_STATE_LOCK = Lock()
_LAST_STALE_RECOVERY_AT: datetime | None = None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def iso(value: datetime | None) -> str | None:
    normalized = as_utc(value)
    return normalized.isoformat() if normalized is not None else None


def clean_string(value: object) -> str:
    return str(value or "").strip()


def normalize_email(value: object) -> str:
    return clean_string(value).lower()


def normalize_code(value: object) -> str:
    return re.sub(r"\s+", "", clean_string(value)).upper()


def json_dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def json_loads(value: str, fallback: object) -> object:
    try:
        return json.loads(value)
    except Exception:
        return fallback


class UserServiceError(Exception):
    def __init__(self, message: str, *, status_code: int = 400, code: str = "bad_request") -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code


class UserModel(Base):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(Text, nullable=False)
    role = Column(String(16), nullable=False, default="user", index=True)
    enabled = Column(Boolean, nullable=False, default=True)
    image_quota = Column(Integer, nullable=False, default=0)
    image_concurrency = Column(Integer, nullable=False, default=1)
    active_image_requests = Column(Integer, nullable=False, default=0)
    token_version = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    last_login_at = Column(DateTime(timezone=True), nullable=True)


class SchemaMigrationModel(Base):
    __tablename__ = "schema_migrations"

    key = Column(String(128), primary_key=True)
    applied_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class AuthSettingModel(Base):
    __tablename__ = "auth_settings"

    key = Column(String(128), primary_key=True)
    value = Column(Text, nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class RevokedTokenModel(Base):
    __tablename__ = "jwt_token_revocations"

    token_hash = Column(String(64), primary_key=True)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class LoginFailureLimitModel(Base):
    __tablename__ = "login_failure_limits"

    email = Column(String(255), primary_key=True)
    failed_attempts = Column(Integer, nullable=False, default=0)
    locked_until = Column(DateTime(timezone=True), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class EmailVerificationCodeModel(Base):
    __tablename__ = "email_verification_codes"

    id = Column(String(36), primary_key=True)
    email = Column(String(255), nullable=False, index=True)
    purpose = Column(String(32), nullable=False, default="register", index=True)
    code_hash = Column(String(64), nullable=False)
    attempts = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=5)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    consumed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    last_sent_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class RedeemCodeModel(Base):
    __tablename__ = "redeem_codes"

    id = Column(String(36), primary_key=True)
    code_hash = Column(String(64), unique=True, nullable=False, index=True)
    code_prefix = Column(String(8), nullable=False)
    code_suffix = Column(String(8), nullable=False)
    type = Column(String(32), nullable=False, index=True)
    value = Column(Integer, nullable=False, default=0)
    membership_plan_id = Column(String(36), ForeignKey("membership_plans.id"), nullable=True, index=True)
    metadata_json = Column("metadata", Text, nullable=False, default="")
    enabled = Column(Boolean, nullable=False, default=True)
    used_by_user_id = Column(String(36), ForeignKey("users.id"), nullable=True, index=True)
    used_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class PromoCodeModel(Base):
    __tablename__ = "promo_codes"

    id = Column(String(36), primary_key=True)
    code_hash = Column(String(64), unique=True, nullable=False, index=True)
    code_prefix = Column(String(8), nullable=False)
    code_suffix = Column(String(8), nullable=False)
    image_quota = Column(Integer, nullable=False, default=0)
    max_uses = Column(Integer, nullable=False, default=1)
    used_count = Column(Integer, nullable=False, default=0)
    enabled = Column(Boolean, nullable=False, default=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class PromoCodeUsageModel(Base):
    __tablename__ = "promo_code_usages"
    __table_args__ = (UniqueConstraint("promo_code_id", "user_id", name="uq_promo_user"),)

    id = Column(String(36), primary_key=True)
    promo_code_id = Column(String(36), ForeignKey("promo_codes.id"), nullable=False, index=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    email = Column(String(255), nullable=False)
    used_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class MembershipPlanModel(Base):
    __tablename__ = "membership_plans"

    id = Column(String(36), primary_key=True)
    name = Column(String(80), nullable=False)
    description = Column(Text, nullable=False, default="")
    duration_days = Column(Integer, nullable=False, default=1)
    period_days = Column(Integer, nullable=False, default=1)
    period_image_quota = Column(Integer, nullable=False, default=1)
    enabled = Column(Boolean, nullable=False, default=True)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class UserMembershipModel(Base):
    __tablename__ = "user_memberships"

    id = Column(String(36), primary_key=True)
    user_id = Column(String(36), ForeignKey("users.id"), unique=True, nullable=False, index=True)
    plan_id = Column(String(36), ForeignKey("membership_plans.id"), nullable=True, index=True)
    plan_name = Column(String(80), nullable=False, default="")
    status = Column(String(32), nullable=False, default="inactive", index=True)
    activated_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True, index=True)
    current_period_started_at = Column(DateTime(timezone=True), nullable=True)
    current_period_ends_at = Column(DateTime(timezone=True), nullable=True)
    member_image_quota = Column(Integer, nullable=False, default=0)
    period_image_quota = Column(Integer, nullable=False, default=0)
    duration_days = Column(Integer, nullable=False, default=0)
    period_days = Column(Integer, nullable=False, default=0)
    source_redeem_code_id = Column(String(36), ForeignKey("redeem_codes.id"), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class DailyCheckinModel(Base):
    __tablename__ = "daily_checkins"
    __table_args__ = (
        UniqueConstraint("user_id", "checkin_date", name="uq_daily_checkin_user_date"),
    )

    id = Column(String(36), primary_key=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    checkin_date = Column(Date, nullable=False)
    reward_image_quota = Column(Integer, nullable=False, default=0)
    streak_days = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class ImageUsageEventModel(Base):
    __tablename__ = "image_usage_events"

    id = Column(String(36), primary_key=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    endpoint = Column(String(128), nullable=False)
    requested_count = Column(Integer, nullable=False, default=0)
    actual_count = Column(Integer, nullable=False, default=0)
    refunded_count = Column(Integer, nullable=False, default=0)
    member_reserved_count = Column(Integer, nullable=False, default=0)
    regular_reserved_count = Column(Integer, nullable=False, default=0)
    membership_source_redeem_code_id = Column(String(36), nullable=True, index=True)
    membership_activation_key = Column(String(255), nullable=False, default="")
    member_actual_count = Column(Integer, nullable=False, default=0)
    regular_actual_count = Column(Integer, nullable=False, default=0)
    member_refunded_count = Column(Integer, nullable=False, default=0)
    regular_refunded_count = Column(Integer, nullable=False, default=0)
    requested_ggb = Column(Integer, nullable=False, default=0)
    actual_ggb = Column(Integer, nullable=False, default=0)
    refunded_ggb = Column(Integer, nullable=False, default=0)
    member_reserved_ggb = Column(Integer, nullable=False, default=0)
    regular_reserved_ggb = Column(Integer, nullable=False, default=0)
    member_actual_ggb = Column(Integer, nullable=False, default=0)
    regular_actual_ggb = Column(Integer, nullable=False, default=0)
    member_refunded_ggb = Column(Integer, nullable=False, default=0)
    regular_refunded_ggb = Column(Integer, nullable=False, default=0)
    status = Column(String(32), nullable=False, default="reserved", index=True)
    error = Column(Text, nullable=False, default="")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    settled_at = Column(DateTime(timezone=True), nullable=True)


class UserGalleryImageModel(Base):
    __tablename__ = "user_gallery_images"
    __table_args__ = (
        Index("ix_user_gallery_user_status_deleted_created_id", "user_id", "status", "deleted_at", "created_at", "id"),
        Index("ix_user_gallery_user_status_created", "user_id", "status", "created_at"),
        Index("ix_user_gallery_cleanup", "status", "expires_at", "deleted_at"),
        Index("ix_user_gallery_usage_event", "usage_event_id"),
        Index("ix_user_gallery_sha256", "sha256"),
    )

    id = Column(String(36), primary_key=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    usage_event_id = Column(String(36), ForeignKey("image_usage_events.id"), nullable=True, index=True)
    source_endpoint = Column(String(128), nullable=False, default="")
    source = Column(String(32), nullable=False, default="generation", index=True)
    status = Column(String(32), nullable=False, default="available", index=True)
    prompt = Column(Text, nullable=False, default="")
    revised_prompt = Column(Text, nullable=False, default="")
    model = Column(String(80), nullable=False, default="")
    size = Column(String(32), nullable=False, default="")
    storage_path = Column(Text, nullable=False, unique=True)
    content_type = Column(String(80), nullable=False, default="image/png")
    size_bytes = Column(Integer, nullable=False, default=0)
    sha256 = Column(String(64), nullable=False, default="")
    share_status = Column(String(32), nullable=False, default="private", index=True)
    share_token = Column(String(128), nullable=True, unique=True)
    share_requested_at = Column(DateTime(timezone=True), nullable=True)
    shared_at = Column(DateTime(timezone=True), nullable=True)
    share_review_note = Column(Text, nullable=False, default="")
    metadata_json = Column("metadata", Text, nullable=False, default="")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    deleted_at = Column(DateTime(timezone=True), nullable=True, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)


DEFAULT_SETTINGS: dict[str, object] = {
    "site_name": "Genapi",
    "registration_enabled": True,
    "email_verification_enabled": True,
    "invitation_required": False,
    "promo_codes_enabled": True,
    "email_domain_whitelist": [],
    "default_image_quota": 0,
    "default_image_concurrency": 1,
    "verify_code_ttl_seconds": 900,
    "verify_send_cooldown_seconds": 60,
    "verify_max_attempts": 5,
    "checkin_enabled": True,
    "checkin_daily_image_quota": 5,
    "checkin_streak_bonus_enabled": True,
    "checkin_streak_bonus_days": 7,
    "checkin_streak_bonus_image_quota": 15,
    "checkin_timezone": "Asia/Shanghai",
    "smtp_host": "",
    "smtp_port": 587,
    "smtp_username": "",
    "smtp_password": "",
    "smtp_from": "",
    "smtp_tls": True,
}

PUBLIC_SETTING_KEYS = {
    "site_name",
    "registration_enabled",
    "email_verification_enabled",
    "invitation_required",
    "promo_codes_enabled",
    "email_domain_whitelist",
}


@dataclass(frozen=True)
class QuotaReservation:
    event_id: str
    user_id: str
    requested_count: int
    member_reserved_count: int = 0
    regular_reserved_count: int = 0
    requested_ggb: int = 0
    member_reserved_ggb: int = 0
    regular_reserved_ggb: int = 0
    membership_source_redeem_code_id: str = ""
    membership_activation_key: str = ""
    bypass: bool = False


class UserService:
    def __init__(self, database_url: str | None = None) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.database_url = database_url or self._default_database_url()
        connect_args = {"check_same_thread": False} if self.database_url.startswith("sqlite") else {}
        self.engine = create_engine(self.database_url, pool_pre_ping=True, connect_args=connect_args)
        Base.metadata.create_all(self.engine)
        self._migrate_schema()
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self._password_hasher = PasswordHasher()
        self._quota_lock = Lock()
        self._settings_lock = Lock()
        self._admin_mutation_lock = Lock()
        self._verification_lock = Lock()
        self._login_failure_lock = Lock()
        self._secret_lock = Lock()
        self._jwt_secret_cache: str | None = None
        self._jwt_secret_path = self._configured_jwt_secret_path()
        self._ensure_defaults()
        self._recover_stale_image_quota_reservations_throttled()

    @staticmethod
    def _default_database_url() -> str:
        return (
            os.getenv("GENAPI_USER_DATABASE_URL", "").strip()
            or os.getenv("CHATGPT2API_USER_DATABASE_URL", "").strip()
            or os.getenv("USER_DATABASE_URL", "").strip()
            or f"sqlite:///{DATA_DIR / 'users.db'}"
        )

    @staticmethod
    def _configured_jwt_secret_path() -> Path:
        configured = clean_string(os.getenv("GENAPI_JWT_SECRET_FILE") or os.getenv("CHATGPT2API_JWT_SECRET_FILE"))
        return Path(configured).expanduser() if configured else DATA_DIR / "jwt_hmac_secret"

    def _migrate_schema(self) -> None:
        inspector = inspect(self.engine)
        if "users" not in inspector.get_table_names():
            return

        def add_column(table: str, name: str, column_type, suffix: str = "") -> None:
            columns = {column["name"] for column in inspector.get_columns(table)}
            if name in columns:
                return
            compiled = column_type.compile(dialect=self.engine.dialect)
            with self.engine.begin() as connection:
                connection.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {compiled} {suffix}".strip())

        user_columns = {column["name"] for column in inspector.get_columns("users")}
        if "token_version" not in user_columns:
            column_type = Integer().compile(dialect=self.engine.dialect)
            with self.engine.begin() as connection:
                connection.exec_driver_sql(
                    f"ALTER TABLE users ADD COLUMN token_version {column_type} NOT NULL DEFAULT 0"
                )
        table_names = set(inspector.get_table_names())
        if "redeem_codes" in table_names:
            add_column("redeem_codes", "membership_plan_id", String(36))
            add_column("redeem_codes", "metadata", Text(), "NOT NULL DEFAULT ''")
        if "image_usage_events" in table_names:
            for name in {
                "member_reserved_count",
                "regular_reserved_count",
                "member_actual_count",
                "regular_actual_count",
                "member_refunded_count",
                "regular_refunded_count",
                "requested_ggb",
                "actual_ggb",
                "refunded_ggb",
                "member_reserved_ggb",
                "regular_reserved_ggb",
                "member_actual_ggb",
                "regular_actual_ggb",
                "member_refunded_ggb",
                "regular_refunded_ggb",
            }:
                add_column("image_usage_events", name, Integer(), "NOT NULL DEFAULT 0")
            add_column("image_usage_events", "membership_source_redeem_code_id", String(36))
            add_column("image_usage_events", "membership_activation_key", String(255), "NOT NULL DEFAULT ''")
        if "login_failure_limits" in table_names:
            add_column("login_failure_limits", "failed_attempts", Integer(), "NOT NULL DEFAULT 0")
            add_column("login_failure_limits", "locked_until", DateTime(timezone=True))
            add_column("login_failure_limits", "created_at", DateTime(timezone=True))
            add_column("login_failure_limits", "updated_at", DateTime(timezone=True))
        if "user_gallery_images" in table_names:
            gallery_columns = {column["name"] for column in inspector.get_columns("user_gallery_images")}
            for name, column_type, suffix in [
                ("usage_event_id", String(36), ""),
                ("source_endpoint", String(128), "NOT NULL DEFAULT ''"),
                ("source", String(32), "NOT NULL DEFAULT 'generation'"),
                ("status", String(32), "NOT NULL DEFAULT 'available'"),
                ("prompt", Text(), "NOT NULL DEFAULT ''"),
                ("revised_prompt", Text(), "NOT NULL DEFAULT ''"),
                ("model", String(80), "NOT NULL DEFAULT ''"),
                ("size", String(32), "NOT NULL DEFAULT ''"),
                ("content_type", String(80), "NOT NULL DEFAULT 'image/png'"),
                ("size_bytes", Integer(), "NOT NULL DEFAULT 0"),
                ("sha256", String(64), "NOT NULL DEFAULT ''"),
                ("share_status", String(32), "NOT NULL DEFAULT 'private'"),
                ("share_token", String(128), ""),
                ("share_requested_at", DateTime(timezone=True), ""),
                ("shared_at", DateTime(timezone=True), ""),
                ("share_review_note", Text(), "NOT NULL DEFAULT ''"),
                ("metadata", Text(), "NOT NULL DEFAULT ''"),
                ("updated_at", DateTime(timezone=True), ""),
                ("deleted_at", DateTime(timezone=True), ""),
            ]:
                if name not in gallery_columns:
                    add_column("user_gallery_images", name, column_type, suffix)
            with self.engine.begin() as connection:
                connection.exec_driver_sql(
                    "CREATE INDEX IF NOT EXISTS ix_user_gallery_user_status_deleted_created_id "
                    "ON user_gallery_images (user_id, status, deleted_at, created_at, id)"
                )
                connection.exec_driver_sql(
                    "CREATE INDEX IF NOT EXISTS ix_user_gallery_user_status_created "
                    "ON user_gallery_images (user_id, status, created_at)"
                )
                connection.exec_driver_sql(
                    "CREATE INDEX IF NOT EXISTS ix_user_gallery_cleanup "
                    "ON user_gallery_images (status, expires_at, deleted_at)"
                )
                connection.exec_driver_sql(
                    "CREATE INDEX IF NOT EXISTS ix_user_gallery_usage_event "
                    "ON user_gallery_images (usage_event_id)"
                )
                connection.exec_driver_sql(
                    "CREATE INDEX IF NOT EXISTS ix_user_gallery_sha256 "
                    "ON user_gallery_images (sha256)"
                )
        self._run_ggb_v1_migration(set(inspector.get_table_names()))

    def _run_ggb_v1_migration(self, table_names: set[str]) -> None:
        migration_key = "ggb_v1"
        with self.engine.begin() as connection:
            SchemaMigrationModel.__table__.create(bind=connection, checkfirst=True)
            marker_result = connection.execute(
                text(
                    "INSERT INTO schema_migrations (key, applied_at) "
                    "VALUES (:key, :applied_at) "
                    "ON CONFLICT (key) DO NOTHING"
                ),
                {"key": migration_key, "applied_at": utc_now()},
            )
            if marker_result.rowcount != 1:
                return

            if "users" in table_names:
                connection.execute(
                    text("UPDATE users SET image_quota = COALESCE(image_quota, 0) * :cost"),
                    {"cost": IMAGE_COST_GGB},
                )
            if "user_memberships" in table_names:
                connection.execute(
                    text(
                        "UPDATE user_memberships SET "
                        "member_image_quota = COALESCE(member_image_quota, 0) * :cost, "
                        "period_image_quota = COALESCE(period_image_quota, 0) * :cost"
                    ),
                    {"cost": IMAGE_COST_GGB},
                )
            if "membership_plans" in table_names:
                connection.execute(
                    text("UPDATE membership_plans SET period_image_quota = COALESCE(period_image_quota, 0) * :cost"),
                    {"cost": IMAGE_COST_GGB},
                )
            if "daily_checkins" in table_names:
                connection.execute(
                    text("UPDATE daily_checkins SET reward_image_quota = COALESCE(reward_image_quota, 0) * :cost"),
                    {"cost": IMAGE_COST_GGB},
                )
            if "promo_codes" in table_names:
                connection.execute(
                    text("UPDATE promo_codes SET image_quota = COALESCE(image_quota, 0) * :cost"),
                    {"cost": IMAGE_COST_GGB},
                )
            if "redeem_codes" in table_names:
                connection.execute(
                    text("UPDATE redeem_codes SET value = COALESCE(value, 0) * :cost WHERE type = 'image_quota'"),
                    {"cost": IMAGE_COST_GGB},
                )
                rows = connection.execute(
                    text("SELECT id, metadata FROM redeem_codes WHERE type = 'membership' AND metadata IS NOT NULL AND metadata != ''")
                ).fetchall()
                for row in rows:
                    metadata = json_loads(row[1] or "", {})
                    if not isinstance(metadata, dict) or "period_image_quota" not in metadata:
                        continue
                    metadata["period_image_quota"] = max(0, int(metadata.get("period_image_quota") or 0)) * IMAGE_COST_GGB
                    connection.execute(
                        text("UPDATE redeem_codes SET metadata = :metadata WHERE id = :id"),
                        {"metadata": json_dumps(metadata), "id": row[0]},
                    )
            if "image_usage_events" in table_names:
                connection.execute(
                    text(
                        "UPDATE image_usage_events SET "
                        "requested_ggb = COALESCE(requested_count, 0) * :cost, "
                        "actual_ggb = COALESCE(actual_count, 0) * :cost, "
                        "refunded_ggb = COALESCE(refunded_count, 0) * :cost, "
                        "member_reserved_ggb = COALESCE(member_reserved_count, 0) * :cost, "
                        "regular_reserved_ggb = COALESCE(regular_reserved_count, 0) * :cost, "
                        "member_actual_ggb = COALESCE(member_actual_count, 0) * :cost, "
                        "regular_actual_ggb = COALESCE(regular_actual_count, 0) * :cost, "
                        "member_refunded_ggb = COALESCE(member_refunded_count, 0) * :cost, "
                        "regular_refunded_ggb = COALESCE(regular_refunded_count, 0) * :cost"
                    ),
                    {"cost": IMAGE_COST_GGB},
                )
            if "auth_settings" in table_names:
                for key in {"default_image_quota", "checkin_daily_image_quota", "checkin_streak_bonus_image_quota"}:
                    row = connection.execute(
                        text("SELECT value FROM auth_settings WHERE key = :key"),
                        {"key": key},
                    ).fetchone()
                    if row is None:
                        continue
                    raw_value = json_loads(row[0], 0)
                    try:
                        next_value = max(0, int(raw_value or 0)) * IMAGE_COST_GGB
                    except (TypeError, ValueError):
                        next_value = 0
                    connection.execute(
                        text("UPDATE auth_settings SET value = :value, updated_at = :updated_at WHERE key = :key"),
                        {"value": json_dumps(next_value), "updated_at": utc_now(), "key": key},
                    )

    @staticmethod
    def _env_int(names: tuple[str, ...], default: int) -> int:
        for name in names:
            value = clean_string(os.getenv(name))
            if not value:
                continue
            try:
                return max(0, int(value))
            except ValueError:
                continue
        return default

    @classmethod
    def stale_image_quota_seconds(cls) -> int:
        return cls._env_int(
            (
                "GENAPI_STALE_IMAGE_QUOTA_SECONDS",
                "GENAPI_STALE_IMAGE_RESERVATION_SECONDS",
                "CHATGPT2API_STALE_IMAGE_QUOTA_SECONDS",
                "CHATGPT2API_STALE_IMAGE_RESERVATION_SECONDS",
            ),
            DEFAULT_STALE_IMAGE_QUOTA_SECONDS,
        )

    @classmethod
    def stale_image_quota_recovery_throttle_seconds(cls) -> int:
        return cls._env_int(
            (
                "GENAPI_STALE_IMAGE_QUOTA_RECOVERY_THROTTLE_SECONDS",
                "GENAPI_STALE_IMAGE_RESERVATION_RECOVERY_THROTTLE_SECONDS",
                "CHATGPT2API_STALE_IMAGE_QUOTA_RECOVERY_THROTTLE_SECONDS",
                "CHATGPT2API_STALE_IMAGE_RESERVATION_RECOVERY_THROTTLE_SECONDS",
            ),
            DEFAULT_STALE_IMAGE_QUOTA_RECOVERY_THROTTLE_SECONDS,
        )

    def _recover_stale_image_quota_reservations_throttled(self) -> int:
        global _LAST_STALE_RECOVERY_AT

        now = utc_now()
        throttle_seconds = self.stale_image_quota_recovery_throttle_seconds()
        with _STALE_RECOVERY_STATE_LOCK:
            if (
                _LAST_STALE_RECOVERY_AT is not None
                and throttle_seconds > 0
                and (now - _LAST_STALE_RECOVERY_AT).total_seconds() < throttle_seconds
            ):
                return 0
            recovered = self.recover_stale_image_quota_reservations()
            _LAST_STALE_RECOVERY_AT = utc_now()
            return recovered

    def _read_jwt_secret_file(self) -> str:
        try:
            return clean_string(self._jwt_secret_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return ""

    def _write_jwt_secret_file(self, secret: str) -> None:
        value = clean_string(secret)
        if not value:
            return
        self._jwt_secret_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(str(self._jwt_secret_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(f"{value}\n")

    @staticmethod
    def _legacy_jwt_secret_from_row(row: AuthSettingModel | None) -> str:
        if row is None:
            return ""
        loaded = json_loads(row.value, row.value)
        return clean_string(loaded)

    def _jwt_secret(self) -> str:
        env_secret = clean_string(os.getenv("JWT_SECRET"))
        if env_secret:
            return env_secret
        with self._secret_lock:
            if self._jwt_secret_cache:
                return self._jwt_secret_cache
            secret = self._read_jwt_secret_file()
            if not secret:
                with self.Session() as session:
                    secret = self._legacy_jwt_secret_from_row(session.get(AuthSettingModel, "jwt_secret"))
                secret = secret or secrets.token_urlsafe(48)
                self._write_jwt_secret_file(secret)
                secret = self._read_jwt_secret_file() or secret
            self._jwt_secret_cache = secret
            return secret

    def _ensure_defaults(self) -> None:
        with self.Session() as session:
            changed = False
            for key, value in DEFAULT_SETTINGS.items():
                if session.get(AuthSettingModel, key) is None:
                    session.add(AuthSettingModel(key=key, value=json_dumps(value), updated_at=utc_now()))
                    changed = True
            legacy_jwt_secret = session.get(AuthSettingModel, "jwt_secret")
            if not clean_string(os.getenv("JWT_SECRET")):
                legacy_secret = self._legacy_jwt_secret_from_row(legacy_jwt_secret)
                if legacy_secret and not self._read_jwt_secret_file():
                    self._write_jwt_secret_file(legacy_secret)
                    self._jwt_secret_cache = self._read_jwt_secret_file() or legacy_secret
                elif not self._read_jwt_secret_file():
                    self._jwt_secret()
            if legacy_jwt_secret is not None:
                session.delete(legacy_jwt_secret)
                changed = True
            if session.query(MembershipPlanModel).count() == 0:
                defaults = [
                    ("日卡", "激活后 1 天内可用，每周期 1 天刷新 GGB。", 1, 1, 50, 10),
                    ("周卡", "激活后 7 天内可用，每周期 7 天刷新 GGB。", 7, 7, 400, 20),
                    ("月卡", "激活后 30 天内可用，每周期 30 天刷新 GGB。", 30, 30, 1800, 30),
                ]
                for name, description, duration_days, period_days, period_quota, sort_order in defaults:
                    session.add(
                        MembershipPlanModel(
                            id=str(uuid.uuid4()),
                            name=name,
                            description=description,
                            duration_days=duration_days,
                            period_days=period_days,
                            period_image_quota=period_quota,
                            enabled=True,
                            sort_order=sort_order,
                            created_at=utc_now(),
                            updated_at=utc_now(),
                        )
                    )
                changed = True
            if changed:
                session.commit()

    def _settings_from_session(self, session) -> dict[str, object]:
        settings = dict(DEFAULT_SETTINGS)
        for row in session.query(AuthSettingModel).all():
            if row.key == "jwt_secret":
                continue
            settings[row.key] = json_loads(row.value, row.value)
        return settings

    def get_settings(self) -> dict[str, object]:
        with self.Session() as session:
            return self._settings_from_session(session)

    def get_public_settings(self) -> dict[str, object]:
        settings = self.get_settings()
        return {key: settings.get(key) for key in PUBLIC_SETTING_KEYS}

    def get_admin_settings(self) -> dict[str, object]:
        settings = self.get_settings()
        settings.pop("jwt_secret", None)
        settings["has_smtp_password"] = bool(clean_string(settings.get("smtp_password")))
        settings["smtp_password"] = ""
        settings["default_ggb_balance"] = int(settings.get("default_image_quota") or 0)
        settings["checkin_daily_ggb"] = int(settings.get("checkin_daily_image_quota") or 0)
        settings["checkin_streak_bonus_ggb"] = int(settings.get("checkin_streak_bonus_image_quota") or 0)
        settings["image_cost_ggb"] = IMAGE_COST_GGB
        return settings

    def update_settings(self, updates: dict[str, object]) -> dict[str, object]:
        allowed = set(DEFAULT_SETTINGS)
        alias_keys = {
            "default_ggb_balance": "default_image_quota",
            "checkin_daily_ggb": "checkin_daily_image_quota",
            "checkin_streak_bonus_ggb": "checkin_streak_bonus_image_quota",
        }
        normalized_updates: dict[str, object] = {}
        for key, value in dict(updates or {}).items():
            normalized_updates[alias_keys.get(key, key)] = value
        with self._settings_lock, self.Session() as session:
            for key, value in normalized_updates.items():
                if key not in allowed:
                    continue
                if key == "smtp_password" and clean_string(value) == "":
                    continue
                if key == "email_domain_whitelist":
                    if isinstance(value, str):
                        value = [item.strip().lower() for item in re.split(r"[\n,]", value) if item.strip()]
                    elif isinstance(value, list):
                        value = [clean_string(item).lower() for item in value if clean_string(item)]
                    else:
                        value = []
                if key in {
                    "default_image_quota",
                    "default_image_concurrency",
                    "verify_code_ttl_seconds",
                    "verify_send_cooldown_seconds",
                    "verify_max_attempts",
                    "checkin_daily_image_quota",
                    "checkin_streak_bonus_days",
                    "checkin_streak_bonus_image_quota",
                    "smtp_port",
                }:
                    value = max(0, int(value or 0))
                    if key in {"default_image_concurrency", "verify_max_attempts", "checkin_streak_bonus_days"}:
                        value = max(1, value)
                if key in {
                    "registration_enabled",
                    "email_verification_enabled",
                    "invitation_required",
                    "promo_codes_enabled",
                    "checkin_enabled",
                    "checkin_streak_bonus_enabled",
                    "smtp_tls",
                }:
                    value = bool(value)
                if key == "checkin_timezone":
                    timezone_name = clean_string(value) or str(DEFAULT_SETTINGS["checkin_timezone"])
                    try:
                        ZoneInfo(timezone_name)
                    except (ZoneInfoNotFoundError, ValueError):
                        timezone_name = str(DEFAULT_SETTINGS["checkin_timezone"])
                    value = timezone_name
                row = session.get(AuthSettingModel, key)
                if row is None:
                    row = AuthSettingModel(key=key)
                    session.add(row)
                row.value = json_dumps(value)
                row.updated_at = utc_now()
            session.commit()
        return self.get_admin_settings()

    def has_admin(self) -> bool:
        with self.Session() as session:
            return self._enabled_admin_count(session) > 0

    def _enabled_admin_count(self, session) -> int:
        return (
            session.query(UserModel)
            .filter(UserModel.role == "admin", UserModel.enabled.is_(True))
            .count()
        )

    def _ensure_not_removing_last_enabled_admin(
        self,
        session,
        user: UserModel,
        *,
        next_role: str | None = None,
        next_enabled: bool | None = None,
    ) -> None:
        final_role = next_role if next_role is not None else str(user.role)
        final_enabled = bool(user.enabled) if next_enabled is None else bool(next_enabled)
        removes_enabled_admin = user.role == "admin" and bool(user.enabled) and not (
            final_role == "admin" and final_enabled
        )
        if removes_enabled_admin and self._enabled_admin_count(session) <= 1:
            raise UserServiceError(
                "at least one enabled admin is required",
                status_code=409,
                code="last_admin",
            )

    @staticmethod
    def _removes_enabled_admin(user: UserModel, *, next_role: str, next_enabled: bool) -> bool:
        return user.role == "admin" and bool(user.enabled) and not (
            next_role == "admin" and next_enabled
        )

    @staticmethod
    def _another_enabled_admin_exists(user_id: str):
        other_admin = aliased(UserModel)
        return exists().where(
            other_admin.id != user_id,
            other_admin.role == "admin",
            other_admin.enabled.is_(True),
        )

    @staticmethod
    def _last_admin_error() -> UserServiceError:
        return UserServiceError(
            "at least one enabled admin is required",
            status_code=409,
            code="last_admin",
        )

    def _lock_enabled_admins(self, session) -> None:
        session.query(UserModel.id).filter(
            UserModel.role == "admin",
            UserModel.enabled.is_(True),
        ).order_by(UserModel.id).with_for_update().all()

    def setup_status(self) -> dict[str, object]:
        has_admin = self.has_admin()
        return {"has_admin": has_admin, "requires_setup": not has_admin}

    def validate_email(self, email: str) -> str:
        normalized = normalize_email(email)
        if not EMAIL_PATTERN.match(normalized):
            raise UserServiceError("invalid email")
        return normalized

    @staticmethod
    def validate_password(password: str) -> str:
        value = str(password or "")
        if len(value) < 8:
            raise UserServiceError("password must be at least 8 characters")
        return value

    def hash_password(self, password: str) -> str:
        return self._password_hasher.hash(self.validate_password(password))

    def verify_password(self, stored_hash: str, password: str) -> bool:
        try:
            return self._password_hasher.verify(stored_hash, password)
        except (VerifyMismatchError, VerificationError):
            return False

    def code_hash(self, code: str) -> str:
        normalized = normalize_code(code)
        secret = self._jwt_secret()
        return hmac.new(secret.encode("utf-8"), normalized.encode("utf-8"), hashlib.sha256).hexdigest()

    @staticmethod
    def token_hash(token: str) -> str:
        return hashlib.sha256(clean_string(token).encode("utf-8")).hexdigest()

    def _serialize_membership(self, membership: UserMembershipModel | None) -> dict[str, object] | None:
        if membership is None:
            return None
        member_quota = int(membership.member_image_quota or 0)
        period_quota = int(membership.period_image_quota or 0)
        return {
            "id": membership.id,
            "plan_id": membership.plan_id,
            "plan_name": membership.plan_name,
            "status": membership.status,
            "member_image_quota": member_quota,
            "period_image_quota": period_quota,
            "member_ggb": member_quota,
            "period_ggb": period_quota,
            "member_ggb_balance": member_quota,
            "period_ggb_quota": period_quota,
            "duration_days": int(membership.duration_days or 0),
            "period_days": int(membership.period_days or 0),
            "activated_at": iso(membership.activated_at),
            "expires_at": iso(membership.expires_at),
            "current_period_started_at": iso(membership.current_period_started_at),
            "current_period_ends_at": iso(membership.current_period_ends_at),
        }

    def _legacy_membership_activation_key(self, membership: UserMembershipModel | None) -> str:
        if membership is None:
            return ""
        return "|".join(
            [
                clean_string(membership.id),
                clean_string(membership.plan_id),
                iso(membership.activated_at) or "",
                iso(membership.expires_at) or "",
            ]
        )

    def _membership_activation_key(self, membership: UserMembershipModel | None) -> str:
        legacy_key = self._legacy_membership_activation_key(membership)
        if not legacy_key:
            return ""
        return "|".join(
            [
                legacy_key,
                iso(membership.current_period_started_at) or "",
                iso(membership.current_period_ends_at) or "",
            ]
        )

    def _membership_period_matches_event(
        self,
        membership: UserMembershipModel | None,
        event_created_at: datetime | None,
    ) -> bool:
        if membership is None:
            return False
        created = as_utc(event_created_at)
        period_start = as_utc(membership.current_period_started_at)
        period_end = as_utc(membership.current_period_ends_at)
        if created is None or period_start is None:
            return False
        if period_end is not None:
            return period_start <= created < period_end
        return period_start <= created

    def _membership_refund_matches(
        self,
        membership: UserMembershipModel | None,
        *,
        activation_key: str,
        source_redeem_code_id: str,
        event_created_at: datetime | None,
    ) -> bool:
        if membership is None:
            return False
        event_activation_key = clean_string(activation_key)
        if event_activation_key and self._membership_activation_key(membership) == event_activation_key:
            return True
        if event_activation_key and self._legacy_membership_activation_key(membership) == event_activation_key:
            return self._membership_period_matches_event(membership, event_created_at)
        current_source = clean_string(membership.source_redeem_code_id)
        event_source = clean_string(source_redeem_code_id)
        if not event_activation_key and event_source and current_source == event_source:
            return self._membership_period_matches_event(membership, event_created_at)
        return False

    def _refresh_user_membership(self, session, user_id: str, now: datetime | None = None) -> UserMembershipModel | None:
        current = now or utc_now()
        membership = session.query(UserMembershipModel).filter(UserMembershipModel.user_id == clean_string(user_id)).one_or_none()
        if membership is None:
            return None
        expires_at = as_utc(membership.expires_at)
        if expires_at is None or expires_at <= current:
            if membership.status != "expired" or int(membership.member_image_quota or 0) != 0:
                membership.status = "expired"
                membership.member_image_quota = 0
                membership.updated_at = current
                session.flush()
            return membership
        period_days = max(1, int(membership.period_days or 1))
        period_quota = max(0, int(membership.period_image_quota or 0))
        period_start = as_utc(membership.current_period_started_at) or as_utc(membership.activated_at) or current
        period_end = as_utc(membership.current_period_ends_at) or min(period_start + timedelta(days=period_days), expires_at)
        changed = False
        while period_end <= current and period_end < expires_at:
            period_start = period_end
            period_end = min(period_start + timedelta(days=period_days), expires_at)
            changed = True
        if changed or membership.status != "active":
            membership.status = "active"
            membership.current_period_started_at = period_start
            membership.current_period_ends_at = period_end
            membership.member_image_quota = period_quota
            membership.updated_at = current
            session.flush()
        return membership

    def _serialize_user(self, user: UserModel, membership: UserMembershipModel | None = None) -> dict[str, object]:
        membership_payload = self._serialize_membership(membership)
        member_quota = int(membership.member_image_quota or 0) if membership is not None and membership.status == "active" else 0
        regular_quota = int(user.image_quota or 0)
        return {
            "id": user.id,
            "email": user.email,
            "name": user.email,
            "role": user.role,
            "enabled": bool(user.enabled),
            "image_quota": regular_quota,
            "member_image_quota": member_quota,
            "total_image_quota": regular_quota + member_quota,
            "ggb": regular_quota,
            "member_ggb": member_quota,
            "total_ggb": regular_quota + member_quota,
            "regular_ggb_balance": regular_quota,
            "member_ggb_balance": member_quota,
            "total_ggb_balance": regular_quota + member_quota,
            "image_cost_ggb": IMAGE_COST_GGB,
            "membership": membership_payload,
            "membership_status": str(membership.status) if membership is not None else "inactive",
            "membership_plan_id": membership.plan_id if membership is not None else None,
            "membership_plan_name": membership.plan_name if membership is not None else "",
            "membership_expires_at": iso(membership.expires_at) if membership is not None else None,
            "membership_period_ends_at": iso(membership.current_period_ends_at) if membership is not None else None,
            "image_concurrency": int(user.image_concurrency or 0),
            "active_image_requests": int(user.active_image_requests or 0),
            "created_at": iso(user.created_at),
            "updated_at": iso(user.updated_at),
            "last_login_at": iso(user.last_login_at),
        }

    def create_token(self, user: UserModel | dict[str, object]) -> str:
        user_id = user.id if isinstance(user, UserModel) else clean_string(user.get("id"))
        token_version = int(
            (user.token_version if isinstance(user, UserModel) else user.get("token_version", 0))
            or 0
        )
        now = utc_now()
        payload = {
            "sub": user_id,
            "ver": token_version,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(days=30)).timestamp()),
        }
        secret = self._jwt_secret()
        return jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)

    def authenticate_token(self, token: str) -> dict[str, object] | None:
        candidate = clean_string(token)
        if not candidate:
            return None
        try:
            payload = jwt.decode(candidate, self._jwt_secret(), algorithms=[JWT_ALGORITHM])
        except jwt.PyJWTError:
            return None
        user_id = clean_string(payload.get("sub"))
        if not user_id:
            return None
        token_version = int(payload.get("ver") or 0)
        candidate_hash = self.token_hash(candidate)
        now = utc_now()
        with self.Session() as session:
            revoked = session.get(RevokedTokenModel, candidate_hash)
            if revoked is not None and as_utc(revoked.expires_at) > now:
                return None
            user = session.get(UserModel, user_id)
            if user is None or not bool(user.enabled):
                return None
            if int(user.token_version or 0) != token_version:
                return None
            membership = self._refresh_user_membership(session, user.id, now)
            session.commit()
            return self._serialize_user(user, membership)

    def revoke_token(self, token: str) -> None:
        candidate = clean_string(token)
        if not candidate:
            return
        try:
            payload = jwt.decode(candidate, self._jwt_secret(), algorithms=[JWT_ALGORITHM])
        except jwt.PyJWTError:
            return
        expires_at = datetime.fromtimestamp(int(payload.get("exp") or 0), timezone.utc)
        now = utc_now()
        if expires_at <= now:
            return
        with self.Session() as session:
            session.query(RevokedTokenModel).filter(RevokedTokenModel.expires_at <= now).delete(
                synchronize_session=False
            )
            session.merge(
                RevokedTokenModel(
                    token_hash=self.token_hash(candidate),
                    expires_at=expires_at,
                    created_at=now,
                )
            )
            session.commit()

    def create_setup_admin(self, email: str, password: str) -> dict[str, object]:
        with self._settings_lock, self.Session() as session:
            if self._enabled_admin_count(session) > 0:
                raise UserServiceError("setup already completed", status_code=409, code="setup_completed")
            user = UserModel(
                id=str(uuid.uuid4()),
                email=self.validate_email(email),
                password_hash=self.hash_password(password),
                role="admin",
                enabled=True,
                image_quota=0,
                image_concurrency=0,
                created_at=utc_now(),
                updated_at=utc_now(),
            )
            session.add(user)
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise UserServiceError("email already exists", status_code=409, code="duplicate_email") from exc
            return {"user": self._serialize_user(user), "token": self.create_token(user)}

    def login(self, email: str, password: str) -> dict[str, object]:
        normalized_email = normalize_email(email)
        with self.Session() as session:
            now = utc_now()
            user = session.query(UserModel).filter(UserModel.email == normalized_email).one_or_none()
            if user is None:
                raise UserServiceError("invalid email or password", status_code=401, code="invalid_credentials")
            self._raise_if_login_limited(session, normalized_email, now)
            if not self.verify_password(user.password_hash, password):
                with self._login_failure_lock:
                    self._record_login_failure(session, normalized_email, now)
                    session.commit()
                raise UserServiceError("invalid email or password", status_code=401, code="invalid_credentials")
            if not bool(user.enabled):
                raise UserServiceError("user is disabled", status_code=403, code="user_disabled")
            with self._login_failure_lock:
                self._clear_login_failures(session, normalized_email)
                user.last_login_at = now
                user.updated_at = now
                membership = self._refresh_user_membership(session, user.id, user.updated_at)
                session.commit()
            return {"user": self._serialize_user(user, membership), "token": self.create_token(user)}

    @staticmethod
    def _login_failure_lock_seconds(failed_attempts: int) -> int:
        attempts_over_threshold = max(0, failed_attempts - LOGIN_FAILURE_THRESHOLD)
        return min(
            LOGIN_FAILURE_MAX_LOCK_SECONDS,
            LOGIN_FAILURE_INITIAL_LOCK_SECONDS * (2 ** attempts_over_threshold),
        )

    def _raise_if_login_limited(self, session, email: str, now: datetime) -> None:
        if not email:
            return
        row = session.get(LoginFailureLimitModel, email)
        if row is None:
            return
        locked_until = as_utc(row.locked_until)
        if locked_until is not None and locked_until > now:
            retry_after = max(1, int((locked_until - now).total_seconds()))
            raise UserServiceError(
                f"too many login failures; retry after {retry_after} seconds",
                status_code=429,
                code="login_rate_limited",
            )

    def _record_login_failure(self, session, email: str, now: datetime) -> None:
        if not email:
            return
        row = session.get(LoginFailureLimitModel, email)
        if row is None:
            row = LoginFailureLimitModel(email=email, failed_attempts=0, created_at=now)
            session.add(row)
        row.failed_attempts = int(row.failed_attempts or 0) + 1
        row.locked_until = (
            now + timedelta(seconds=self._login_failure_lock_seconds(int(row.failed_attempts or 0)))
            if int(row.failed_attempts or 0) >= LOGIN_FAILURE_THRESHOLD
            else None
        )
        row.updated_at = now

    def _clear_login_failures(self, session, email: str) -> None:
        if not email:
            return
        row = session.get(LoginFailureLimitModel, email)
        if row is not None:
            session.delete(row)

    def get_user(self, user_id: str) -> dict[str, object] | None:
        with self.Session() as session:
            user = session.get(UserModel, clean_string(user_id))
            if user is None:
                return None
            membership = self._refresh_user_membership(session, user.id)
            session.commit()
            return self._serialize_user(user, membership)

    @staticmethod
    def _setting_int(settings: dict[str, object], key: str, default: int, *, minimum: int = 0) -> int:
        try:
            value = int(settings.get(key, default) or 0)
        except (TypeError, ValueError):
            value = default
        return max(minimum, value)

    @staticmethod
    def _payload_int(
        payload: dict[str, object],
        keys: tuple[str, ...],
        default: int = 0,
        *,
        minimum: int = 0,
    ) -> int:
        for key in keys:
            if key not in payload:
                continue
            try:
                return max(minimum, int(payload.get(key) or 0))
            except (TypeError, ValueError):
                return max(minimum, default)
        return max(minimum, default)

    @staticmethod
    def _checkin_timezone_name(settings: dict[str, object]) -> str:
        timezone_name = clean_string(settings.get("checkin_timezone")) or str(DEFAULT_SETTINGS["checkin_timezone"])
        try:
            ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError):
            return str(DEFAULT_SETTINGS["checkin_timezone"])
        return timezone_name

    @staticmethod
    def _checkin_timezone(settings: dict[str, object]) -> ZoneInfo:
        return ZoneInfo(UserService._checkin_timezone_name(settings))

    def _checkin_date(self, settings: dict[str, object], now: datetime | None = None) -> date:
        current = as_utc(now or utc_now()) or utc_now()
        return current.astimezone(self._checkin_timezone(settings)).date()

    def _checkin_reward_for_streak(self, settings: dict[str, object], streak_days: int) -> int:
        reward = self._setting_int(settings, "checkin_daily_image_quota", 1)
        if bool(settings.get("checkin_streak_bonus_enabled", True)):
            bonus_days = self._setting_int(settings, "checkin_streak_bonus_days", 7, minimum=1)
            if streak_days > 0 and streak_days % bonus_days == 0:
                reward += self._setting_int(settings, "checkin_streak_bonus_image_quota", 3)
        return reward

    @staticmethod
    def _date_iso(value: date | None) -> str | None:
        return value.isoformat() if value is not None else None

    def _latest_checkin(self, session, user_id: str) -> DailyCheckinModel | None:
        return (
            session.query(DailyCheckinModel)
            .filter(DailyCheckinModel.user_id == clean_string(user_id))
            .order_by(DailyCheckinModel.checkin_date.desc(), DailyCheckinModel.created_at.desc())
            .first()
        )

    def _checkin_status_payload(
        self,
        *,
        settings: dict[str, object],
        today: date,
        today_row: DailyCheckinModel | None,
        latest_row: DailyCheckinModel | None,
    ) -> dict[str, object]:
        checked_in_today = today_row is not None
        last_row = today_row or latest_row
        yesterday = today - timedelta(days=1)
        if today_row is not None:
            streak_days = int(today_row.streak_days or 0)
        elif latest_row is not None and latest_row.checkin_date == yesterday:
            streak_days = int(latest_row.streak_days or 0)
        else:
            streak_days = 0
        if checked_in_today:
            reward = int(today_row.reward_image_quota or 0) if today_row is not None else 0
        else:
            next_streak = streak_days + 1 if latest_row is not None and latest_row.checkin_date == yesterday else 1
            reward = self._checkin_reward_for_streak(settings, next_streak)
        return {
            "checkin_enabled": bool(settings.get("checkin_enabled", True)),
            "can_checkin": bool(settings.get("checkin_enabled", True)) and not checked_in_today,
            "checkin_date": today.isoformat(),
            "checked_in_today": checked_in_today,
            "already_checked_in": checked_in_today,
            "streak_days": streak_days,
            "last_checkin_date": self._date_iso(last_row.checkin_date if last_row is not None else None),
            "reward_image_quota": reward,
            "reward_ggb": reward,
            "daily_image_quota": self._setting_int(settings, "checkin_daily_image_quota", 1),
            "daily_ggb": self._setting_int(settings, "checkin_daily_image_quota", 1),
            "streak_bonus_enabled": bool(settings.get("checkin_streak_bonus_enabled", True)),
            "streak_bonus_days": self._setting_int(settings, "checkin_streak_bonus_days", 7, minimum=1),
            "streak_bonus_image_quota": self._setting_int(settings, "checkin_streak_bonus_image_quota", 3),
            "streak_bonus_ggb": self._setting_int(settings, "checkin_streak_bonus_image_quota", 3),
            "timezone": self._checkin_timezone_name(settings),
        }

    def get_checkin_status(self, user_id: str) -> dict[str, object]:
        normalized_user_id = clean_string(user_id)
        with self.Session() as session:
            user = session.get(UserModel, normalized_user_id)
            if user is None or not bool(user.enabled):
                raise UserServiceError("user not found", status_code=404, code="not_found")
            settings = self._settings_from_session(session)
            today = self._checkin_date(settings)
            today_row = (
                session.query(DailyCheckinModel)
                .filter(
                    DailyCheckinModel.user_id == normalized_user_id,
                    DailyCheckinModel.checkin_date == today,
                )
                .one_or_none()
            )
            latest_row = today_row or self._latest_checkin(session, normalized_user_id)
            return self._checkin_status_payload(
                settings=settings,
                today=today,
                today_row=today_row,
                latest_row=latest_row,
            )

    def _already_checked_in_response(
        self,
        session,
        *,
        user_id: str,
        today: date,
        row: DailyCheckinModel,
    ) -> dict[str, object]:
        user = session.get(UserModel, user_id)
        if user is None or not bool(user.enabled):
            raise UserServiceError("user not found", status_code=404, code="not_found")
        membership = self._refresh_user_membership(session, user.id)
        session.commit()
        return {
            "ok": True,
            "already_checked_in": True,
            "checkin_date": today.isoformat(),
            "reward_image_quota": 0,
            "reward_ggb": 0,
            "streak_days": int(row.streak_days or 0),
            "user": self._serialize_user(user, membership),
        }

    def checkin(self, user_id: str) -> dict[str, object]:
        normalized_user_id = clean_string(user_id)
        now = utc_now()
        with self._quota_lock, self.Session() as session:
            settings = self._settings_from_session(session)
            if not bool(settings.get("checkin_enabled", True)):
                raise UserServiceError("check-in is disabled", status_code=403, code="checkin_disabled")
            today = self._checkin_date(settings, now)
            user = session.get(UserModel, normalized_user_id)
            if user is None or not bool(user.enabled):
                raise UserServiceError("user not found", status_code=404, code="not_found")
            today_row = (
                session.query(DailyCheckinModel)
                .filter(
                    DailyCheckinModel.user_id == normalized_user_id,
                    DailyCheckinModel.checkin_date == today,
                )
                .one_or_none()
            )
            if today_row is not None:
                return self._already_checked_in_response(
                    session,
                    user_id=normalized_user_id,
                    today=today,
                    row=today_row,
                )
            previous = (
                session.query(DailyCheckinModel)
                .filter(
                    DailyCheckinModel.user_id == normalized_user_id,
                    DailyCheckinModel.checkin_date < today,
                )
                .order_by(DailyCheckinModel.checkin_date.desc(), DailyCheckinModel.created_at.desc())
                .first()
            )
            yesterday = today - timedelta(days=1)
            streak_days = int(previous.streak_days or 0) + 1 if previous is not None and previous.checkin_date == yesterday else 1
            reward = self._checkin_reward_for_streak(settings, streak_days)
            row = DailyCheckinModel(
                id=str(uuid.uuid4()),
                user_id=normalized_user_id,
                checkin_date=today,
                reward_image_quota=reward,
                streak_days=streak_days,
                created_at=now,
            )
            session.add(row)
            try:
                session.flush()
                if reward:
                    session.execute(
                        update(UserModel)
                        .where(UserModel.id == normalized_user_id, UserModel.enabled.is_(True))
                        .values(
                            image_quota=UserModel.image_quota + reward,
                            updated_at=now,
                        )
                    )
                session.commit()
            except IntegrityError:
                session.rollback()
                existing = (
                    session.query(DailyCheckinModel)
                    .filter(
                        DailyCheckinModel.user_id == normalized_user_id,
                        DailyCheckinModel.checkin_date == today,
                    )
                    .one()
                )
                return self._already_checked_in_response(
                    session,
                    user_id=normalized_user_id,
                    today=today,
                    row=existing,
                )
            user = session.get(UserModel, normalized_user_id)
            if user is None:
                raise UserServiceError("user not found", status_code=404, code="not_found")
            membership = self._refresh_user_membership(session, user.id, now)
            session.commit()
            return {
                "ok": True,
                "already_checked_in": False,
                "checkin_date": today.isoformat(),
                "reward_image_quota": reward,
                "reward_ggb": reward,
                "streak_days": streak_days,
                "user": self._serialize_user(user, membership),
            }

    def list_users(self, query: str = "") -> list[dict[str, object]]:
        normalized_query = normalize_email(query)
        with self.Session() as session:
            q = session.query(UserModel).order_by(UserModel.created_at.desc())
            if normalized_query:
                q = q.filter(UserModel.email.contains(normalized_query))
            users = q.all()
            memberships = {row.user_id: row for row in session.query(UserMembershipModel).all()}
            for user in users:
                if user.id in memberships:
                    self._refresh_user_membership(session, user.id)
            session.commit()
            return [self._serialize_user(user, memberships.get(user.id)) for user in users]

    def create_user(
        self,
        *,
        email: str,
        password: str,
        role: UserRole = "user",
        enabled: bool = True,
        image_quota: int = 0,
        image_concurrency: int = 1,
    ) -> dict[str, object]:
        if role not in {"admin", "user"}:
            raise UserServiceError("invalid role")
        user = UserModel(
            id=str(uuid.uuid4()),
            email=self.validate_email(email),
            password_hash=self.hash_password(password),
            role=role,
            enabled=bool(enabled),
            image_quota=max(0, int(image_quota or 0)),
            image_concurrency=max(1, int(image_concurrency or 1)) if role == "user" else 0,
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        with self.Session() as session:
            session.add(user)
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise UserServiceError("email already exists", status_code=409, code="duplicate_email") from exc
            return self._serialize_user(user)

    def update_user(self, user_id: str, updates: dict[str, object]) -> dict[str, object]:
        with self._admin_mutation_lock, self.Session() as session:
            updates = dict(updates or {})
            if any(key in updates for key in {"role", "enabled"}):
                self._lock_enabled_admins(session)
            user = session.get(UserModel, clean_string(user_id))
            if user is None:
                raise UserServiceError("user not found", status_code=404, code="not_found")
            next_role = str(updates.get("role")) if updates.get("role") in {"admin", "user"} else str(user.role)
            next_enabled = bool(updates.get("enabled")) if "enabled" in updates else bool(user.enabled)
            values: dict[str, object] = {}
            next_token_version = int(user.token_version or 0)
            if "email" in updates:
                values["email"] = self.validate_email(str(updates.get("email") or ""))
            if "password" in updates and clean_string(updates.get("password")):
                values["password_hash"] = self.hash_password(str(updates.get("password") or ""))
                next_token_version += 1
            if "role" in updates and updates.get("role") in {"admin", "user"}:
                values["role"] = next_role
                if next_role != str(user.role):
                    next_token_version += 1
            if "enabled" in updates:
                values["enabled"] = next_enabled
                if bool(user.enabled) and not next_enabled:
                    next_token_version += 1
            quota_keys = ("image_quota", "ggb", "ggb_balance", "regular_ggb", "regular_ggb_balance")
            if any(key in updates for key in quota_keys):
                values["image_quota"] = self._payload_int(updates, quota_keys)
            if "image_concurrency" in updates:
                values["image_concurrency"] = max(1, int(updates.get("image_concurrency") or 1)) if next_role == "user" else 0
            elif str(user.role) != "user" and next_role == "user" and int(user.image_concurrency or 0) < 1:
                values["image_concurrency"] = 1
            if next_token_version != int(user.token_version or 0):
                values["token_version"] = next_token_version
            values["updated_at"] = utc_now()
            try:
                if self._removes_enabled_admin(user, next_role=next_role, next_enabled=next_enabled):
                    result = session.execute(
                        update(UserModel)
                        .where(
                            UserModel.id == user.id,
                            UserModel.role == "admin",
                            UserModel.enabled.is_(True),
                            self._another_enabled_admin_exists(user.id),
                        )
                        .values(**values)
                    )
                    if result.rowcount != 1:
                        session.rollback()
                        raise self._last_admin_error()
                    session.commit()
                    refreshed = session.get(UserModel, user.id)
                    if refreshed is None:
                        raise UserServiceError("user not found", status_code=404, code="not_found")
                    return self._serialize_user(refreshed)
                for key, value in values.items():
                    setattr(user, key, value)
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise UserServiceError("email already exists", status_code=409, code="duplicate_email") from exc
            return self._serialize_user(user)

    def _normalize_membership_plan_payload(
        self,
        payload: dict[str, object],
        *,
        partial: bool = False,
    ) -> dict[str, object]:
        payload = dict(payload or {})
        if "period_image_quota" not in payload:
            for alias in ("period_ggb_quota", "period_ggb"):
                if alias in payload:
                    payload["period_image_quota"] = payload[alias]
                    break
        values: dict[str, object] = {}
        if "name" in payload or not partial:
            name = clean_string(payload.get("name"))
            if not name:
                raise UserServiceError("membership plan name is required")
            if len(name) > 80:
                raise UserServiceError("membership plan name is too long")
            values["name"] = name
        if "description" in payload or not partial:
            values["description"] = clean_string(payload.get("description"))
        if "duration_days" in payload or not partial:
            duration_days = max(1, int(payload.get("duration_days") or 1))
            values["duration_days"] = duration_days
        if "period_days" in payload or not partial:
            period_days = max(1, int(payload.get("period_days") or 1))
            values["period_days"] = period_days
        if "period_image_quota" in payload or not partial:
            values["period_image_quota"] = max(0, int(payload.get("period_image_quota") or 0))
        if "enabled" in payload or not partial:
            values["enabled"] = bool(payload.get("enabled", True))
        if "sort_order" in payload or not partial:
            values["sort_order"] = int(payload.get("sort_order") or 0)
        duration = int(values.get("duration_days", payload.get("duration_days") or 1) or 1)
        period = int(values.get("period_days", payload.get("period_days") or 1) or 1)
        if period > duration:
            raise UserServiceError("period days cannot exceed duration days")
        return values

    def _serialize_membership_plan(self, row: MembershipPlanModel) -> dict[str, object]:
        return {
            "id": row.id,
            "name": row.name,
            "description": row.description,
            "duration_days": int(row.duration_days or 0),
            "period_days": int(row.period_days or 0),
            "period_image_quota": int(row.period_image_quota or 0),
            "period_ggb": int(row.period_image_quota or 0),
            "period_ggb_quota": int(row.period_image_quota or 0),
            "enabled": bool(row.enabled),
            "sort_order": int(row.sort_order or 0),
            "created_at": iso(row.created_at),
            "updated_at": iso(row.updated_at),
        }

    def list_membership_plans(self, *, public_only: bool = False) -> list[dict[str, object]]:
        with self.Session() as session:
            query = session.query(MembershipPlanModel)
            if public_only:
                query = query.filter(MembershipPlanModel.enabled.is_(True))
            rows = query.order_by(MembershipPlanModel.sort_order.asc(), MembershipPlanModel.created_at.asc()).all()
            return [self._serialize_membership_plan(row) for row in rows]

    def get_user_membership(self, user_id: str) -> dict[str, object]:
        with self.Session() as session:
            user = session.get(UserModel, clean_string(user_id))
            if user is None or not bool(user.enabled):
                raise UserServiceError("user not found", status_code=404, code="not_found")
            membership = self._refresh_user_membership(session, user.id)
            session.commit()
            return {
                "membership": self._serialize_membership(membership),
                "user": self._serialize_user(user, membership),
            }

    def create_membership_plan(self, payload: dict[str, object]) -> dict[str, object]:
        values = self._normalize_membership_plan_payload(payload)
        now = utc_now()
        row = MembershipPlanModel(
            id=str(uuid.uuid4()),
            created_at=now,
            updated_at=now,
            **values,
        )
        with self.Session() as session:
            session.add(row)
            session.commit()
            return self._serialize_membership_plan(row)

    def update_membership_plan(self, plan_id: str, updates: dict[str, object]) -> dict[str, object]:
        with self.Session() as session:
            row = session.get(MembershipPlanModel, clean_string(plan_id))
            if row is None:
                raise UserServiceError("membership plan not found", status_code=404, code="not_found")
            payload = {
                "duration_days": int(row.duration_days or 1),
                "period_days": int(row.period_days or 1),
                **dict(updates or {}),
            }
            values = self._normalize_membership_plan_payload(payload, partial=True)
            for key, value in values.items():
                setattr(row, key, value)
            row.updated_at = utc_now()
            session.commit()
            return self._serialize_membership_plan(row)

    def delete_membership_plan(self, plan_id: str) -> None:
        with self.Session() as session:
            row = session.get(MembershipPlanModel, clean_string(plan_id))
            if row is None:
                raise UserServiceError("membership plan not found", status_code=404, code="not_found")
            used_by_code = session.query(RedeemCodeModel).filter(RedeemCodeModel.membership_plan_id == row.id).count()
            used_by_membership = session.query(UserMembershipModel).filter(UserMembershipModel.plan_id == row.id).count()
            if used_by_code or used_by_membership:
                row.enabled = False
                row.updated_at = utc_now()
            else:
                session.delete(row)
            session.commit()

    def delete_user(self, user_id: str) -> None:
        with self._admin_mutation_lock, self.Session() as session:
            self._lock_enabled_admins(session)
            user = session.get(UserModel, clean_string(user_id))
            if user is None:
                raise UserServiceError("user not found", status_code=404, code="not_found")
            has_history = (
                session.query(ImageUsageEventModel).filter(ImageUsageEventModel.user_id == user.id).count()
                or session.query(UserGalleryImageModel).filter(UserGalleryImageModel.user_id == user.id).count()
                or session.query(DailyCheckinModel).filter(DailyCheckinModel.user_id == user.id).count()
                or session.query(RedeemCodeModel).filter(RedeemCodeModel.used_by_user_id == user.id).count()
                or session.query(PromoCodeUsageModel).filter(PromoCodeUsageModel.user_id == user.id).count()
            )
            if self._removes_enabled_admin(user, next_role=str(user.role), next_enabled=False):
                if has_history:
                    result = session.execute(
                        update(UserModel)
                        .where(
                            UserModel.id == user.id,
                            UserModel.role == "admin",
                            UserModel.enabled.is_(True),
                            self._another_enabled_admin_exists(user.id),
                        )
                        .values(
                            enabled=False,
                            token_version=UserModel.token_version + 1,
                            updated_at=utc_now(),
                        )
                    )
                else:
                    result = session.execute(
                        delete(UserModel).where(
                            UserModel.id == user.id,
                            UserModel.role == "admin",
                            UserModel.enabled.is_(True),
                            self._another_enabled_admin_exists(user.id),
                        )
                    )
                if result.rowcount != 1:
                    session.rollback()
                    raise self._last_admin_error()
            elif has_history:
                user.enabled = False
                user.token_version = int(user.token_version or 0) + 1
                user.updated_at = utc_now()
            else:
                session.delete(user)
            session.commit()

    def _check_email_domain(self, email: str, settings: dict[str, object]) -> None:
        whitelist = settings.get("email_domain_whitelist")
        if not isinstance(whitelist, list) or not whitelist:
            return
        domain = email.rsplit("@", 1)[-1]
        allowed = {clean_string(item).lower() for item in whitelist if clean_string(item)}
        if domain not in allowed:
            raise UserServiceError("email domain is not allowed")

    def create_email_verification_code(self, email: str, purpose: str = "register") -> str:
        normalized_email = self.validate_email(email)
        normalized_purpose = clean_string(purpose) or "register"
        now = utc_now()
        with self._verification_lock, self.Session() as session:
            settings = self._settings_from_session(session)
            if normalized_purpose == "register":
                if not bool(settings.get("registration_enabled", True)):
                    raise UserServiceError(
                        "registration is disabled",
                        status_code=403,
                        code="registration_disabled",
                    )
                self._check_email_domain(normalized_email, settings)
                if session.query(UserModel).filter(UserModel.email == normalized_email).count() > 0:
                    raise UserServiceError("email already exists", status_code=409, code="duplicate_email")
            cooldown = int(settings.get("verify_send_cooldown_seconds") or 60)
            latest = (
                session.query(EmailVerificationCodeModel)
                .filter(
                    EmailVerificationCodeModel.email == normalized_email,
                )
                .order_by(EmailVerificationCodeModel.created_at.desc())
                .first()
            )
            if latest is not None and (now - as_utc(latest.last_sent_at)).total_seconds() < cooldown:
                raise UserServiceError("verification code sent too frequently", status_code=429, code="send_cooldown")
            code = f"{secrets.randbelow(1_000_000):06d}"
            row = EmailVerificationCodeModel(
                id=str(uuid.uuid4()),
                email=normalized_email,
                purpose=normalized_purpose,
                code_hash=self.code_hash(code),
                attempts=0,
                max_attempts=int(settings.get("verify_max_attempts") or 5),
                expires_at=now + timedelta(seconds=int(settings.get("verify_code_ttl_seconds") or 900)),
                created_at=now,
                last_sent_at=now,
            )
            session.add(row)
            session.commit()
            return code

    def _valid_email_verification_code(
        self,
        session,
        email: str,
        code: str,
        purpose: str = "register",
    ) -> EmailVerificationCodeModel:
        normalized_email = self.validate_email(email)
        normalized_purpose = clean_string(purpose) or "register"
        now = utc_now()
        row = (
            session.query(EmailVerificationCodeModel)
            .filter(
                EmailVerificationCodeModel.email == normalized_email,
                EmailVerificationCodeModel.purpose == normalized_purpose,
                EmailVerificationCodeModel.consumed_at.is_(None),
            )
            .order_by(EmailVerificationCodeModel.created_at.desc())
            .first()
        )
        if row is None:
            raise UserServiceError("verification code is invalid")
        if as_utc(row.expires_at) < now:
            raise UserServiceError("verification code expired")
        if row.attempts >= row.max_attempts:
            raise UserServiceError("verification code attempts exceeded")
        if not hmac.compare_digest(row.code_hash, self.code_hash(code)):
            result = session.execute(
                update(EmailVerificationCodeModel)
                .where(
                    EmailVerificationCodeModel.id == row.id,
                    EmailVerificationCodeModel.consumed_at.is_(None),
                    EmailVerificationCodeModel.attempts < EmailVerificationCodeModel.max_attempts,
                )
                .values(attempts=EmailVerificationCodeModel.attempts + 1)
            )
            if result.rowcount != 1:
                session.rollback()
                raise UserServiceError("verification code attempts exceeded")
            session.commit()
            raise UserServiceError("verification code is invalid")
        return row

    def _consume_email_verification_row(
        self,
        session,
        row: EmailVerificationCodeModel,
        consumed_at: datetime,
    ) -> None:
        result = session.execute(
            update(EmailVerificationCodeModel)
            .where(
                EmailVerificationCodeModel.id == row.id,
                EmailVerificationCodeModel.consumed_at.is_(None),
            )
            .values(consumed_at=consumed_at)
        )
        if result.rowcount != 1:
            session.rollback()
            raise UserServiceError("verification code is invalid")

    def consume_email_verification_code(self, email: str, code: str, purpose: str = "register") -> None:
        now = utc_now()
        with self.Session() as session:
            row = self._valid_email_verification_code(session, email, code, purpose)
            self._consume_email_verification_row(session, row, now)
            session.commit()

    def discard_email_verification_code(self, email: str, code: str, purpose: str = "register") -> None:
        try:
            normalized_email = self.validate_email(email)
        except UserServiceError:
            return
        normalized_purpose = clean_string(purpose) or "register"
        code_hash = self.code_hash(code)
        with self.Session() as session:
            row = (
                session.query(EmailVerificationCodeModel)
                .filter(
                    EmailVerificationCodeModel.email == normalized_email,
                    EmailVerificationCodeModel.purpose == normalized_purpose,
                    EmailVerificationCodeModel.code_hash == code_hash,
                    EmailVerificationCodeModel.consumed_at.is_(None),
                )
                .order_by(EmailVerificationCodeModel.created_at.desc())
                .first()
            )
            if row is not None:
                session.delete(row)
                session.commit()

    def register(
        self,
        *,
        email: str,
        password: str,
        verification_code: str = "",
        invitation_code: str = "",
        promo_code: str = "",
    ) -> dict[str, object]:
        normalized_email = self.validate_email(email)
        self.validate_password(password)
        with self._settings_lock, self.Session() as session:
            settings = self._settings_from_session(session)
            if not bool(settings.get("registration_enabled", True)):
                raise UserServiceError("registration is disabled", status_code=403, code="registration_disabled")
            self._check_email_domain(normalized_email, settings)
            if session.query(UserModel).filter(UserModel.email == normalized_email).count() > 0:
                raise UserServiceError("email already exists", status_code=409, code="duplicate_email")

            verification = None
            if bool(settings.get("email_verification_enabled", True)):
                verification = self._valid_email_verification_code(
                    session,
                    normalized_email,
                    verification_code,
                    "register",
                )

            invitation = None
            if bool(settings.get("invitation_required", False)):
                invitation = self._find_valid_redeem_code(session, invitation_code, expected_type="invitation")

            promo = None
            if clean_string(promo_code):
                if not bool(settings.get("promo_codes_enabled", True)):
                    raise UserServiceError("promo code is disabled")
                promo = self._find_valid_promo_code(session, promo_code)

            image_quota = max(0, int(settings.get("default_image_quota") or 0))
            if promo is not None:
                image_quota += max(0, int(promo.image_quota or 0))
            user = UserModel(
                id=str(uuid.uuid4()),
                email=normalized_email,
                password_hash=self.hash_password(password),
                role="user",
                enabled=True,
                image_quota=image_quota,
                image_concurrency=max(1, int(settings.get("default_image_concurrency") or 1)),
                created_at=utc_now(),
                updated_at=utc_now(),
            )
            session.add(user)
            now = utc_now()
            if verification is not None:
                self._consume_email_verification_row(session, verification, now)
            if invitation is not None:
                result = session.execute(
                    update(RedeemCodeModel)
                    .where(
                        RedeemCodeModel.id == invitation.id,
                        RedeemCodeModel.enabled.is_(True),
                        RedeemCodeModel.used_at.is_(None),
                    )
                    .values(used_by_user_id=user.id, used_at=now)
                )
                if result.rowcount != 1:
                    raise UserServiceError("redeem code has already been used")
            if promo is not None:
                result = session.execute(
                    update(PromoCodeModel)
                    .where(
                        PromoCodeModel.id == promo.id,
                        PromoCodeModel.enabled.is_(True),
                        PromoCodeModel.used_count < PromoCodeModel.max_uses,
                    )
                    .values(used_count=PromoCodeModel.used_count + 1)
                )
                if result.rowcount != 1:
                    raise UserServiceError("promo code usage limit reached")
                session.add(
                    PromoCodeUsageModel(
                        id=str(uuid.uuid4()),
                        promo_code_id=promo.id,
                        user_id=user.id,
                        email=normalized_email,
                        used_at=now,
                    )
                )
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise UserServiceError("email already exists", status_code=409, code="duplicate_email") from exc
            return {"user": self._serialize_user(user), "token": self.create_token(user)}

    def generate_redeem_codes(
        self,
        *,
        type: RedeemCodeType,
        value: int = 0,
        count: int = 1,
        expires_at: datetime | None = None,
        membership_plan_id: str | None = None,
    ) -> list[dict[str, object]]:
        if type not in {"image_quota", "concurrency", "invitation", "membership"}:
            raise UserServiceError("invalid redeem code type")
        normalized_count = max(1, min(500, int(count or 1)))
        normalized_value = 0 if type in {"invitation", "membership"} else max(1, int(value or 1))
        prefix_map = {"image_quota": "IMG", "concurrency": "CON", "invitation": "INV", "membership": "MEM"}
        created: list[dict[str, object]] = []
        with self.Session() as session:
            plan_snapshot = ""
            normalized_plan_id: str | None = None
            if type == "membership":
                normalized_plan_id = clean_string(membership_plan_id)
                if not normalized_plan_id:
                    raise UserServiceError("membership plan is required")
                plan = session.get(MembershipPlanModel, normalized_plan_id)
                if plan is None:
                    raise UserServiceError("membership plan not found", status_code=404, code="not_found")
                if not bool(plan.enabled):
                    raise UserServiceError("membership plan is disabled")
                plan_snapshot = json_dumps(self._serialize_membership_plan(plan))
            for _ in range(normalized_count):
                code = f"{prefix_map[type]}-{secrets.token_urlsafe(9).replace('-', '').replace('_', '').upper()[:12]}"
                row = RedeemCodeModel(
                    id=str(uuid.uuid4()),
                    code_hash=self.code_hash(code),
                    code_prefix=code[:4],
                    code_suffix=code[-4:],
                    type=type,
                    value=normalized_value,
                    membership_plan_id=normalized_plan_id,
                    metadata_json=plan_snapshot,
                    enabled=True,
                    expires_at=expires_at,
                    created_at=utc_now(),
                )
                session.add(row)
                created.append({"code": code, **self._serialize_redeem_code(row)})
            session.commit()
        return created

    def _serialize_redeem_code(self, row: RedeemCodeModel) -> dict[str, object]:
        return {
            "id": row.id,
            "code_preview": f"{row.code_prefix}...{row.code_suffix}",
            "type": row.type,
            "value": int(row.value or 0),
            "ggb": int(row.value or 0) if row.type == "image_quota" else 0,
            "ggb_value": int(row.value or 0) if row.type == "image_quota" else 0,
            "membership_plan_id": row.membership_plan_id,
            "membership_plan": json_loads(row.metadata_json or "", {}) if row.type == "membership" else None,
            "enabled": bool(row.enabled),
            "used": bool(row.used_at),
            "used_by_user_id": row.used_by_user_id,
            "used_at": iso(row.used_at),
            "expires_at": iso(row.expires_at),
            "created_at": iso(row.created_at),
        }

    def list_redeem_codes(self) -> list[dict[str, object]]:
        with self.Session() as session:
            return [
                self._serialize_redeem_code(row)
                for row in session.query(RedeemCodeModel).order_by(RedeemCodeModel.created_at.desc()).all()
            ]

    def update_redeem_code(self, code_id: str, updates: dict[str, object]) -> dict[str, object]:
        with self.Session() as session:
            row = session.get(RedeemCodeModel, clean_string(code_id))
            if row is None:
                raise UserServiceError("redeem code not found", status_code=404, code="not_found")
            if "enabled" in updates:
                row.enabled = bool(updates.get("enabled"))
            if "expires_at" in updates:
                row.expires_at = parse_optional_datetime(updates.get("expires_at"))
            session.commit()
            return self._serialize_redeem_code(row)

    def delete_redeem_code(self, code_id: str) -> None:
        with self.Session() as session:
            row = session.get(RedeemCodeModel, clean_string(code_id))
            if row is None:
                raise UserServiceError("redeem code not found", status_code=404, code="not_found")
            session.query(UserMembershipModel).filter(
                UserMembershipModel.source_redeem_code_id == row.id
            ).update(
                {UserMembershipModel.source_redeem_code_id: None},
                synchronize_session=False,
            )
            session.delete(row)
            session.commit()

    def _find_valid_redeem_code(
        self,
        session,
        code: str,
        *,
        expected_type: RedeemCodeType | None = None,
    ) -> RedeemCodeModel:
        normalized = normalize_code(code)
        if not normalized:
            raise UserServiceError("redeem code is required")
        row = session.query(RedeemCodeModel).filter(RedeemCodeModel.code_hash == self.code_hash(normalized)).one_or_none()
        if row is None:
            raise UserServiceError("redeem code is invalid")
        if expected_type is not None and row.type != expected_type:
            raise UserServiceError("redeem code type is invalid")
        if not bool(row.enabled):
            raise UserServiceError("redeem code is disabled")
        if row.used_at is not None:
            raise UserServiceError("redeem code has already been used")
        if row.expires_at is not None and as_utc(row.expires_at) < utc_now():
            raise UserServiceError("redeem code expired")
        return row

    def redeem(self, user_id: str, code: str) -> dict[str, object]:
        with self.Session() as session:
            user = session.get(UserModel, clean_string(user_id))
            if user is None or not bool(user.enabled):
                raise UserServiceError("user not found", status_code=404, code="not_found")
            row = self._find_valid_redeem_code(session, code)
            if row.type == "invitation":
                raise UserServiceError("invitation code can only be used during registration")
            now = utc_now()
            result = session.execute(
                update(RedeemCodeModel)
                .where(
                    RedeemCodeModel.id == row.id,
                    RedeemCodeModel.enabled.is_(True),
                    RedeemCodeModel.used_at.is_(None),
                )
                .values(used_by_user_id=user.id, used_at=now)
            )
            if result.rowcount != 1:
                session.rollback()
                raise UserServiceError("redeem code has already been used")
            if row.type == "image_quota":
                session.execute(
                    update(UserModel)
                    .where(UserModel.id == user.id, UserModel.enabled.is_(True))
                    .values(
                        image_quota=UserModel.image_quota + max(1, int(row.value or 1)),
                        updated_at=now,
                    )
                )
            elif row.type == "concurrency":
                session.execute(
                    update(UserModel)
                    .where(UserModel.id == user.id, UserModel.enabled.is_(True))
                    .values(
                        image_concurrency=UserModel.image_concurrency + max(1, int(row.value or 1)),
                        updated_at=now,
                    )
                )
            elif row.type == "membership":
                snapshot = json_loads(row.metadata_json or "", {})
                if not isinstance(snapshot, dict) or not snapshot:
                    plan = session.get(MembershipPlanModel, clean_string(row.membership_plan_id))
                    if plan is None:
                        raise UserServiceError("membership plan not found", status_code=404, code="not_found")
                    snapshot = self._serialize_membership_plan(plan)
                plan_name = clean_string(snapshot.get("name")) or "会员"
                duration_days = max(1, int(snapshot.get("duration_days") or 1))
                period_days = max(1, int(snapshot.get("period_days") or 1))
                period_image_quota = max(0, int(snapshot.get("period_image_quota") or 0))
                expires_at = now + timedelta(days=duration_days)
                period_ends_at = min(now + timedelta(days=period_days), expires_at)
                membership = (
                    session.query(UserMembershipModel)
                    .filter(UserMembershipModel.user_id == user.id)
                    .one_or_none()
                )
                if membership is None:
                    membership = UserMembershipModel(id=str(uuid.uuid4()), user_id=user.id, created_at=now)
                    session.add(membership)
                membership.plan_id = clean_string(row.membership_plan_id) or clean_string(snapshot.get("id")) or None
                membership.plan_name = plan_name
                membership.status = "active"
                membership.activated_at = now
                membership.expires_at = expires_at
                membership.current_period_started_at = now
                membership.current_period_ends_at = period_ends_at
                membership.member_image_quota = period_image_quota
                membership.period_image_quota = period_image_quota
                membership.duration_days = duration_days
                membership.period_days = period_days
                membership.source_redeem_code_id = row.id
                membership.updated_at = now
            session.commit()
            session.refresh(row)
            session.refresh(user)
            membership = self._refresh_user_membership(session, user.id, now)
            session.commit()
            return {"redeem": self._serialize_redeem_code(row), "user": self._serialize_user(user, membership)}

    def redeem_history(self, user_id: str) -> list[dict[str, object]]:
        with self.Session() as session:
            rows = (
                session.query(RedeemCodeModel)
                .filter(RedeemCodeModel.used_by_user_id == clean_string(user_id))
                .order_by(RedeemCodeModel.used_at.desc())
                .all()
            )
            return [self._serialize_redeem_code(row) for row in rows]

    def _serialize_promo_code(self, row: PromoCodeModel) -> dict[str, object]:
        return {
            "id": row.id,
            "code_preview": f"{row.code_prefix}...{row.code_suffix}",
            "image_quota": int(row.image_quota or 0),
            "ggb_amount": int(row.image_quota or 0),
            "max_uses": int(row.max_uses or 0),
            "used_count": int(row.used_count or 0),
            "enabled": bool(row.enabled),
            "expires_at": iso(row.expires_at),
            "created_at": iso(row.created_at),
        }

    def create_promo_code(
        self,
        *,
        code: str,
        image_quota: int,
        max_uses: int,
        expires_at: datetime | None = None,
        enabled: bool = True,
    ) -> dict[str, object]:
        normalized = normalize_code(code)
        if not normalized:
            raise UserServiceError("promo code is required")
        row = PromoCodeModel(
            id=str(uuid.uuid4()),
            code_hash=self.code_hash(normalized),
            code_prefix=normalized[:4],
            code_suffix=normalized[-4:],
            image_quota=max(0, int(image_quota or 0)),
            max_uses=max(1, int(max_uses or 1)),
            used_count=0,
            enabled=bool(enabled),
            expires_at=expires_at,
            created_at=utc_now(),
        )
        with self.Session() as session:
            session.add(row)
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise UserServiceError("promo code already exists", status_code=409, code="duplicate_code") from exc
            return self._serialize_promo_code(row)

    def _find_valid_promo_code(self, session, code: str) -> PromoCodeModel:
        normalized = normalize_code(code)
        if not normalized:
            raise UserServiceError("promo code is required")
        row = session.query(PromoCodeModel).filter(PromoCodeModel.code_hash == self.code_hash(normalized)).one_or_none()
        if row is None:
            raise UserServiceError("promo code is invalid")
        if not bool(row.enabled):
            raise UserServiceError("promo code is disabled")
        if row.expires_at is not None and as_utc(row.expires_at) < utc_now():
            raise UserServiceError("promo code expired")
        if int(row.used_count or 0) >= int(row.max_uses or 0):
            raise UserServiceError("promo code usage limit reached")
        return row

    def list_promo_codes(self) -> list[dict[str, object]]:
        with self.Session() as session:
            return [
                self._serialize_promo_code(row)
                for row in session.query(PromoCodeModel).order_by(PromoCodeModel.created_at.desc()).all()
            ]

    def update_promo_code(self, code_id: str, updates: dict[str, object]) -> dict[str, object]:
        with self.Session() as session:
            row = session.get(PromoCodeModel, clean_string(code_id))
            if row is None:
                raise UserServiceError("promo code not found", status_code=404, code="not_found")
            updates = dict(updates or {})
            quota_keys = ("image_quota", "ggb", "ggb_amount", "ggb_value")
            if any(key in updates for key in quota_keys):
                row.image_quota = self._payload_int(updates, quota_keys)
            if "max_uses" in updates:
                row.max_uses = max(1, int(updates.get("max_uses") or 1))
            if "enabled" in updates:
                row.enabled = bool(updates.get("enabled"))
            if "expires_at" in updates:
                row.expires_at = parse_optional_datetime(updates.get("expires_at"))
            session.commit()
            return self._serialize_promo_code(row)

    def delete_promo_code(self, code_id: str) -> None:
        with self.Session() as session:
            row = session.get(PromoCodeModel, clean_string(code_id))
            if row is None:
                raise UserServiceError("promo code not found", status_code=404, code="not_found")
            session.delete(row)
            session.commit()

    def reserve_image_quota(self, identity: dict[str, object], requested_count: int, endpoint: str) -> QuotaReservation:
        if identity.get("role") == "admin":
            return QuotaReservation(event_id="", user_id=clean_string(identity.get("id")), requested_count=requested_count, bypass=True)
        user_id = clean_string(identity.get("id"))
        image_count = max(1, int(requested_count or 1))
        amount = image_count * IMAGE_COST_GGB
        self._recover_stale_image_quota_reservations_throttled()
        with self._quota_lock, self.Session() as session:
            now = utc_now()
            user = (
                session.query(UserModel)
                .filter(UserModel.id == user_id, UserModel.enabled.is_(True))
                .with_for_update()
                .one_or_none()
            )
            if user is None:
                raise UserServiceError("user not found", status_code=401, code="invalid_token")
            if int(user.active_image_requests or 0) >= int(user.image_concurrency or 1):
                raise UserServiceError("image concurrency limit exceeded", status_code=429, code="rate_limit_exceeded")
            membership = self._refresh_user_membership(session, user_id, now)
            member_available = (
                int(membership.member_image_quota or 0)
                if membership is not None and membership.status == "active"
                else 0
            )
            regular_available = int(user.image_quota or 0)
            if member_available + regular_available < amount:
                raise UserServiceError("insufficient GGB balance", status_code=429, code="insufficient_quota")
            member_to_use = min(member_available, amount)
            regular_to_use = amount - member_to_use
            membership_source_redeem_code_id = (
                clean_string(membership.source_redeem_code_id)
                if membership is not None and member_to_use
                else ""
            )
            membership_activation_key = self._membership_activation_key(membership) if member_to_use else ""
            if membership is not None and member_to_use:
                membership.member_image_quota = max(0, int(membership.member_image_quota or 0) - member_to_use)
                membership.updated_at = now
            user.image_quota = max(0, int(user.image_quota or 0) - regular_to_use)
            user.active_image_requests = int(user.active_image_requests or 0) + 1
            user.updated_at = now
            event = ImageUsageEventModel(
                id=str(uuid.uuid4()),
                user_id=user_id,
                endpoint=endpoint,
                requested_count=image_count,
                member_reserved_count=member_to_use // IMAGE_COST_GGB,
                regular_reserved_count=regular_to_use // IMAGE_COST_GGB,
                requested_ggb=amount,
                member_reserved_ggb=member_to_use,
                regular_reserved_ggb=regular_to_use,
                membership_source_redeem_code_id=membership_source_redeem_code_id or None,
                membership_activation_key=membership_activation_key,
                status="reserved",
                created_at=now,
            )
            session.add(event)
            session.commit()
            return QuotaReservation(
                event_id=event.id,
                user_id=user_id,
                requested_count=image_count,
                member_reserved_count=member_to_use // IMAGE_COST_GGB,
                regular_reserved_count=regular_to_use // IMAGE_COST_GGB,
                requested_ggb=amount,
                member_reserved_ggb=member_to_use,
                regular_reserved_ggb=regular_to_use,
                membership_source_redeem_code_id=membership_source_redeem_code_id,
                membership_activation_key=membership_activation_key,
            )

    def settle_image_quota(
        self,
        reservation: QuotaReservation | None,
        *,
        success: bool,
        actual_count: int = 0,
        error: str = "",
    ) -> None:
        if reservation is None or reservation.bypass:
            return
        now = utc_now()
        with self._quota_lock, self.Session() as session:
            actual = max(0, min(int(actual_count or 0), int(reservation.requested_count or 0))) if success else 0
            actual_ggb = actual * IMAGE_COST_GGB
            requested_ggb = max(0, int(reservation.requested_ggb or 0))
            member_reserved = max(0, int(reservation.member_reserved_count or 0))
            regular_reserved = max(0, int(reservation.regular_reserved_count or 0))
            member_reserved_ggb = max(0, int(reservation.member_reserved_ggb or 0))
            regular_reserved_ggb = max(0, int(reservation.regular_reserved_ggb or 0))
            membership_source_redeem_code_id = clean_string(reservation.membership_source_redeem_code_id)
            membership_activation_key = clean_string(reservation.membership_activation_key)
            event_created_at: datetime | None = None
            event = session.get(ImageUsageEventModel, reservation.event_id)
            if event is not None:
                event_created_at = event.created_at
                if requested_ggb <= 0:
                    requested_ggb = max(0, int(event.requested_ggb or 0))
                if member_reserved_ggb + regular_reserved_ggb <= 0:
                    member_reserved_ggb = max(0, int(event.member_reserved_ggb or 0))
                    regular_reserved_ggb = max(0, int(event.regular_reserved_ggb or 0))
                if member_reserved + regular_reserved <= 0:
                    member_reserved = max(0, int(event.member_reserved_count or 0))
                    regular_reserved = max(0, int(event.regular_reserved_count or 0))
                membership_source_redeem_code_id = clean_string(event.membership_source_redeem_code_id)
                membership_activation_key = clean_string(event.membership_activation_key)
            if requested_ggb <= 0:
                requested_ggb = max(0, int(reservation.requested_count or 0)) * IMAGE_COST_GGB
            if member_reserved_ggb + regular_reserved_ggb <= 0:
                member_reserved_ggb = member_reserved * IMAGE_COST_GGB
                regular_reserved_ggb = regular_reserved * IMAGE_COST_GGB
            if member_reserved + regular_reserved <= 0:
                regular_reserved = max(0, int(reservation.requested_count or 0))
            if member_reserved_ggb + regular_reserved_ggb <= 0:
                regular_reserved_ggb = max(0, int(reservation.requested_count or 0)) * IMAGE_COST_GGB
            member_actual_ggb = min(member_reserved_ggb, actual_ggb)
            regular_actual_ggb = min(regular_reserved_ggb, max(0, actual_ggb - member_actual_ggb))
            member_refund_ggb = max(0, member_reserved_ggb - member_actual_ggb)
            regular_refund_ggb = max(0, regular_reserved_ggb - regular_actual_ggb)
            refund_ggb = member_refund_ggb + regular_refund_ggb
            member_actual = member_actual_ggb // IMAGE_COST_GGB
            regular_actual = regular_actual_ggb // IMAGE_COST_GGB
            member_refund = member_refund_ggb // IMAGE_COST_GGB
            regular_refund = regular_refund_ggb // IMAGE_COST_GGB
            refund = max(0, int(reservation.requested_count or 0) - actual)
            event_result = session.execute(
                update(ImageUsageEventModel)
                .where(
                    ImageUsageEventModel.id == reservation.event_id,
                    ImageUsageEventModel.user_id == reservation.user_id,
                    ImageUsageEventModel.status == "reserved",
                )
                .values(
                    actual_count=actual,
                    refunded_count=refund,
                    member_actual_count=member_actual,
                    regular_actual_count=regular_actual,
                    member_refunded_count=member_refund,
                    regular_refunded_count=regular_refund,
                    actual_ggb=actual_ggb,
                    refunded_ggb=refund_ggb,
                    member_actual_ggb=member_actual_ggb,
                    regular_actual_ggb=regular_actual_ggb,
                    member_refunded_ggb=member_refund_ggb,
                    regular_refunded_ggb=regular_refund_ggb,
                    status="success" if success else "failed",
                    error=clean_string(error),
                    settled_at=now,
                )
            )
            if event_result.rowcount != 1:
                session.rollback()
                return

            if member_refund_ggb:
                membership = (
                    session.query(UserMembershipModel)
                    .filter(UserMembershipModel.user_id == reservation.user_id)
                    .one_or_none()
                )
                if membership is not None:
                    if self._membership_refund_matches(
                        membership,
                        activation_key=membership_activation_key,
                        source_redeem_code_id=membership_source_redeem_code_id,
                        event_created_at=event_created_at,
                    ):
                        membership.member_image_quota = int(membership.member_image_quota or 0) + member_refund_ggb
                        membership.updated_at = now
            user_result = session.execute(
                update(UserModel)
                .where(UserModel.id == reservation.user_id)
                .values(
                    image_quota=UserModel.image_quota + regular_refund_ggb,
                    active_image_requests=case(
                        (UserModel.active_image_requests > 0, UserModel.active_image_requests - 1),
                        else_=0,
                    ),
                    updated_at=now,
                )
            )
            if user_result.rowcount != 1:
                session.rollback()
                return
            session.commit()

    def recover_stale_image_quota_reservations(self, *, stale_after_seconds: int | None = None) -> int:
        stale_seconds = self.stale_image_quota_seconds() if stale_after_seconds is None else int(stale_after_seconds)
        cutoff = utc_now() - timedelta(seconds=max(0, stale_seconds))
        recovered = 0
        with self._quota_lock, self.Session() as session:
            events = (
                session.query(ImageUsageEventModel)
                .filter(
                    ImageUsageEventModel.status == "reserved",
                    ImageUsageEventModel.created_at <= cutoff,
                )
                .order_by(ImageUsageEventModel.created_at, ImageUsageEventModel.id)
                .all()
            )
            for event in events:
                member_reserved = max(0, int(event.member_reserved_count or 0))
                regular_reserved = max(0, int(event.regular_reserved_count or 0))
                member_reserved_ggb = max(0, int(event.member_reserved_ggb or 0))
                regular_reserved_ggb = max(0, int(event.regular_reserved_ggb or 0))
                if member_reserved_ggb + regular_reserved_ggb <= 0:
                    member_reserved_ggb = member_reserved * IMAGE_COST_GGB
                    regular_reserved_ggb = regular_reserved * IMAGE_COST_GGB
                if member_reserved + regular_reserved <= 0:
                    regular_reserved = max(0, int(event.requested_count or 0))
                if member_reserved_ggb + regular_reserved_ggb <= 0:
                    regular_reserved_ggb = regular_reserved * IMAGE_COST_GGB
                refund = max(0, int(event.requested_count or 0)) or (member_reserved + regular_reserved)
                refund_ggb = member_reserved_ggb + regular_reserved_ggb
                now = utc_now()
                event_result = session.execute(
                    update(ImageUsageEventModel)
                    .where(
                        ImageUsageEventModel.id == event.id,
                        ImageUsageEventModel.status == "reserved",
                    )
                    .values(
                        actual_count=0,
                        refunded_count=refund,
                        member_actual_count=0,
                        regular_actual_count=0,
                        member_refunded_count=member_reserved,
                        regular_refunded_count=regular_reserved,
                        actual_ggb=0,
                        refunded_ggb=refund_ggb,
                        member_actual_ggb=0,
                        regular_actual_ggb=0,
                        member_refunded_ggb=member_reserved_ggb,
                        regular_refunded_ggb=regular_reserved_ggb,
                        status="recovered",
                        error="recovered stale reserved GGB",
                        settled_at=now,
                    )
                )
                if event_result.rowcount != 1:
                    continue

                if member_reserved_ggb:
                    membership = (
                        session.query(UserMembershipModel)
                        .filter(UserMembershipModel.user_id == event.user_id)
                        .one_or_none()
                    )
                    if membership is not None:
                        if self._membership_refund_matches(
                            membership,
                            activation_key=clean_string(event.membership_activation_key),
                            source_redeem_code_id=clean_string(event.membership_source_redeem_code_id),
                            event_created_at=event.created_at,
                        ):
                            membership.member_image_quota = int(membership.member_image_quota or 0) + member_reserved_ggb
                            membership.updated_at = now

                user_result = session.execute(
                    update(UserModel)
                    .where(UserModel.id == event.user_id)
                    .values(
                        image_quota=UserModel.image_quota + regular_reserved_ggb,
                        active_image_requests=case(
                            (UserModel.active_image_requests > 0, UserModel.active_image_requests - 1),
                            else_=0,
                        ),
                        updated_at=now,
                    )
                )
                if user_result.rowcount == 1:
                    recovered += 1
            session.commit()
        return recovered


def parse_optional_datetime(value: object) -> datetime | None:
    text = clean_string(value)
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise UserServiceError("invalid datetime") from exc
    return as_utc(parsed)


user_service = UserService()

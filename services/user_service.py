from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError
from sqlalchemy import (
    Boolean,
    case,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    delete,
    exists,
    inspect,
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
    status = Column(String(32), nullable=False, default="reserved", index=True)
    error = Column(Text, nullable=False, default="")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    settled_at = Column(DateTime(timezone=True), nullable=True)


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
        self._secret_lock = Lock()
        self._jwt_secret_cache: str | None = None
        self._jwt_secret_path = self._configured_jwt_secret_path()
        self._ensure_defaults()

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
            }:
                add_column("image_usage_events", name, Integer(), "NOT NULL DEFAULT 0")
            add_column("image_usage_events", "membership_source_redeem_code_id", String(36))
            add_column("image_usage_events", "membership_activation_key", String(255), "NOT NULL DEFAULT ''")

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
                    ("日卡", "激活后 1 天内可用，每周期 1 天刷新额度。", 1, 1, 10, 10),
                    ("周卡", "激活后 7 天内可用，每周期 7 天刷新额度。", 7, 7, 80, 20),
                    ("月卡", "激活后 30 天内可用，每周期 30 天刷新额度。", 30, 30, 360, 30),
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
        return settings

    def update_settings(self, updates: dict[str, object]) -> dict[str, object]:
        allowed = set(DEFAULT_SETTINGS)
        with self._settings_lock, self.Session() as session:
            for key, value in dict(updates or {}).items():
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
                    "smtp_port",
                }:
                    value = max(0, int(value or 0))
                    if key in {"default_image_concurrency", "verify_max_attempts"}:
                        value = max(1, value)
                if key in {
                    "registration_enabled",
                    "email_verification_enabled",
                    "invitation_required",
                    "promo_codes_enabled",
                    "smtp_tls",
                }:
                    value = bool(value)
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
        return {
            "id": membership.id,
            "plan_id": membership.plan_id,
            "plan_name": membership.plan_name,
            "status": membership.status,
            "member_image_quota": int(membership.member_image_quota or 0),
            "period_image_quota": int(membership.period_image_quota or 0),
            "duration_days": int(membership.duration_days or 0),
            "period_days": int(membership.period_days or 0),
            "activated_at": iso(membership.activated_at),
            "expires_at": iso(membership.expires_at),
            "current_period_started_at": iso(membership.current_period_started_at),
            "current_period_ends_at": iso(membership.current_period_ends_at),
        }

    def _membership_activation_key(self, membership: UserMembershipModel | None) -> str:
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
            user = session.query(UserModel).filter(UserModel.email == normalized_email).one_or_none()
            if user is None or not self.verify_password(user.password_hash, password):
                raise UserServiceError("invalid email or password", status_code=401, code="invalid_credentials")
            if not bool(user.enabled):
                raise UserServiceError("user is disabled", status_code=403, code="user_disabled")
            user.last_login_at = utc_now()
            user.updated_at = utc_now()
            membership = self._refresh_user_membership(session, user.id, user.updated_at)
            session.commit()
            return {"user": self._serialize_user(user, membership), "token": self.create_token(user)}

    def get_user(self, user_id: str) -> dict[str, object] | None:
        with self.Session() as session:
            user = session.get(UserModel, clean_string(user_id))
            if user is None:
                return None
            membership = self._refresh_user_membership(session, user.id)
            session.commit()
            return self._serialize_user(user, membership)

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
            if "enabled" in updates:
                values["enabled"] = next_enabled
                if bool(user.enabled) and not next_enabled:
                    next_token_version += 1
            if "image_quota" in updates:
                values["image_quota"] = max(0, int(updates.get("image_quota") or 0))
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
            if "image_quota" in updates:
                row.image_quota = max(0, int(updates.get("image_quota") or 0))
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
        amount = max(1, int(requested_count or 1))
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
                raise UserServiceError("insufficient image quota", status_code=429, code="insufficient_quota")
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
                requested_count=amount,
                member_reserved_count=member_to_use,
                regular_reserved_count=regular_to_use,
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
                requested_count=amount,
                member_reserved_count=member_to_use,
                regular_reserved_count=regular_to_use,
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
            member_reserved = max(0, int(reservation.member_reserved_count or 0))
            regular_reserved = max(0, int(reservation.regular_reserved_count or 0))
            membership_source_redeem_code_id = clean_string(reservation.membership_source_redeem_code_id)
            membership_activation_key = clean_string(reservation.membership_activation_key)
            if member_reserved + regular_reserved <= 0:
                event = session.get(ImageUsageEventModel, reservation.event_id)
                if event is not None:
                    member_reserved = max(0, int(event.member_reserved_count or 0))
                    regular_reserved = max(0, int(event.regular_reserved_count or 0))
                    membership_source_redeem_code_id = clean_string(event.membership_source_redeem_code_id)
                    membership_activation_key = clean_string(event.membership_activation_key)
            if member_reserved + regular_reserved <= 0:
                regular_reserved = max(0, int(reservation.requested_count or 0))
            member_actual = min(member_reserved, actual)
            regular_actual = min(regular_reserved, max(0, actual - member_actual))
            member_refund = max(0, member_reserved - member_actual)
            regular_refund = max(0, regular_reserved - regular_actual)
            refund = member_refund + regular_refund
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
                    status="success" if success else "failed",
                    error=clean_string(error),
                    settled_at=now,
                )
            )
            if event_result.rowcount != 1:
                session.rollback()
                return

            if member_refund:
                membership = (
                    session.query(UserMembershipModel)
                    .filter(UserMembershipModel.user_id == reservation.user_id)
                    .one_or_none()
                )
                if membership is not None:
                    current_source = clean_string(membership.source_redeem_code_id)
                    current_activation_key = self._membership_activation_key(membership)
                    same_activation = bool(membership_activation_key) and current_activation_key == membership_activation_key
                    legacy_same_source = (
                        not membership_activation_key
                        and bool(membership_source_redeem_code_id)
                        and current_source == membership_source_redeem_code_id
                    )
                    if same_activation or legacy_same_source:
                        membership.member_image_quota = int(membership.member_image_quota or 0) + member_refund
                        membership.updated_at = now
            user_result = session.execute(
                update(UserModel)
                .where(UserModel.id == reservation.user_id)
                .values(
                    image_quota=UserModel.image_quota + regular_refund,
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

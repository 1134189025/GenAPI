from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field

from api.support import extract_bearer_token, require_admin, require_identity
from services.email_service import email_service
from services.user_service import UserServiceError, parse_optional_datetime, user_service


class SetupAdminRequest(BaseModel):
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


class SendVerifyCodeRequest(BaseModel):
    email: str
    purpose: str = "register"


class RegisterRequest(BaseModel):
    email: str
    password: str
    verification_code: str = ""
    invitation_code: str = ""
    promo_code: str = ""


class AuthSettingsUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="allow")


class AdminUserCreateRequest(BaseModel):
    email: str
    password: str
    role: str = "user"
    enabled: bool = True
    image_quota: int = 0
    image_concurrency: int = 1


class AdminUserUpdateRequest(BaseModel):
    email: str | None = None
    password: str | None = None
    role: str | None = None
    enabled: bool | None = None
    image_quota: int | None = None
    image_concurrency: int | None = None


class RedeemCodeGenerateRequest(BaseModel):
    type: str
    value: int = 0
    membership_plan_id: str = ""
    count: int = Field(default=1, ge=1, le=500)
    expires_at: str | None = None


class RedeemCodeUpdateRequest(BaseModel):
    enabled: bool | None = None
    expires_at: str | None = None


class RedeemRequest(BaseModel):
    code: str


class MembershipPlanCreateRequest(BaseModel):
    name: str
    description: str = ""
    duration_days: int = 1
    period_days: int = 1
    period_image_quota: int = 0
    enabled: bool = True
    sort_order: int = 0


class MembershipPlanUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    duration_days: int | None = None
    period_days: int | None = None
    period_image_quota: int | None = None
    enabled: bool | None = None
    sort_order: int | None = None


class PromoCodeCreateRequest(BaseModel):
    code: str
    image_quota: int = 0
    max_uses: int = 1
    expires_at: str | None = None
    enabled: bool = True


class PromoCodeUpdateRequest(BaseModel):
    image_quota: int | None = None
    max_uses: int | None = None
    expires_at: str | None = None
    enabled: bool | None = None


def raise_user_error(exc: UserServiceError) -> None:
    raise HTTPException(
        status_code=exc.status_code,
        detail={"error": {"message": exc.message, "code": exc.code}},
    ) from exc


def auth_payload(result: dict[str, object], app_version: str) -> dict[str, object]:
    user = result["user"]
    assert isinstance(user, dict)
    return {
        "ok": True,
        "version": app_version,
        "token": result["token"],
        "user": user,
        "role": user.get("role"),
        "subject_id": user.get("id"),
        "name": user.get("email"),
    }


def reject_self_admin_status_change(
    *,
    actor: dict[str, object],
    target_user_id: str,
    updates: dict[str, object] | None = None,
    delete_self: bool = False,
) -> None:
    if str(actor.get("id") or "") != str(target_user_id):
        return
    payload = dict(updates or {})
    if delete_self:
        raise UserServiceError("admin cannot delete self", status_code=400, code="self_admin_mutation")
    if payload.get("enabled") is False:
        raise UserServiceError("admin cannot disable self", status_code=400, code="self_admin_mutation")
    if payload.get("role") == "user":
        raise UserServiceError("admin cannot demote self", status_code=400, code="self_admin_mutation")


def create_router(app_version: str) -> APIRouter:
    router = APIRouter()

    @router.get("/api/setup/status")
    async def setup_status():
        return user_service.setup_status()

    @router.post("/api/setup/admin")
    async def setup_admin(body: SetupAdminRequest):
        try:
            return auth_payload(user_service.create_setup_admin(body.email, body.password), app_version)
        except UserServiceError as exc:
            raise_user_error(exc)

    @router.get("/api/public/settings")
    async def public_settings():
        return {"settings": user_service.get_public_settings()}

    @router.post("/api/auth/send-verify-code")
    async def send_verify_code(body: SendVerifyCodeRequest):
        try:
            code = user_service.create_email_verification_code(body.email, "register")
            try:
                await run_in_threadpool(email_service.send_verification_code, body.email, code)
            except UserServiceError:
                user_service.discard_email_verification_code(body.email, code, "register")
                raise
            return {"ok": True}
        except UserServiceError as exc:
            raise_user_error(exc)

    @router.post("/api/auth/register")
    async def register(body: RegisterRequest):
        try:
            result = await run_in_threadpool(
                user_service.register,
                email=body.email,
                password=body.password,
                verification_code=body.verification_code,
                invitation_code=body.invitation_code,
                promo_code=body.promo_code,
            )
            return auth_payload(result, app_version)
        except UserServiceError as exc:
            raise_user_error(exc)

    @router.post("/api/auth/login")
    async def login(body: LoginRequest):
        try:
            result = await run_in_threadpool(user_service.login, body.email, body.password)
            return auth_payload(result, app_version)
        except UserServiceError as exc:
            raise_user_error(exc)

    @router.get("/api/auth/me")
    async def me(authorization: str | None = Header(default=None)):
        return {"user": require_identity(authorization)}

    @router.get("/api/checkin/status")
    async def checkin_status(authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        try:
            return await run_in_threadpool(user_service.get_checkin_status, str(identity.get("id") or ""))
        except UserServiceError as exc:
            raise_user_error(exc)

    @router.post("/api/checkin")
    async def checkin(authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        try:
            return await run_in_threadpool(user_service.checkin, str(identity.get("id") or ""))
        except UserServiceError as exc:
            raise_user_error(exc)

    @router.post("/api/auth/logout")
    async def logout(authorization: str | None = Header(default=None)):
        token = extract_bearer_token(authorization)
        require_identity(authorization)
        user_service.revoke_token(token)
        return {"ok": True}

    @router.get("/api/membership/plans")
    async def public_membership_plans():
        return {"items": user_service.list_membership_plans(public_only=True)}

    @router.get("/api/membership/me")
    async def my_membership(authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        try:
            return user_service.get_user_membership(str(identity.get("id") or ""))
        except UserServiceError as exc:
            raise_user_error(exc)

    @router.get("/api/admin/auth-settings")
    async def get_auth_settings(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"settings": user_service.get_admin_settings()}

    @router.patch("/api/admin/auth-settings")
    async def update_auth_settings(body: AuthSettingsUpdateRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return {"settings": user_service.update_settings(body.model_dump(mode="python"))}
        except UserServiceError as exc:
            raise_user_error(exc)

    @router.get("/api/admin/users")
    async def list_users(query: str = "", authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"items": user_service.list_users(query)}

    @router.post("/api/admin/users")
    async def create_user(body: AdminUserCreateRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            item = user_service.create_user(
                email=body.email,
                password=body.password,
                role="admin" if body.role == "admin" else "user",
                enabled=body.enabled,
                image_quota=body.image_quota,
                image_concurrency=body.image_concurrency,
            )
            return {"item": item, "items": user_service.list_users()}
        except UserServiceError as exc:
            raise_user_error(exc)

    @router.patch("/api/admin/users/{user_id}")
    async def update_user(user_id: str, body: AdminUserUpdateRequest, authorization: str | None = Header(default=None)):
        identity = require_admin(authorization)
        try:
            updates = body.model_dump(mode="python", exclude_unset=True)
            reject_self_admin_status_change(actor=identity, target_user_id=user_id, updates=updates)
            item = user_service.update_user(user_id, updates)
            return {"item": item, "items": user_service.list_users()}
        except UserServiceError as exc:
            raise_user_error(exc)

    @router.delete("/api/admin/users/{user_id}")
    async def delete_user(user_id: str, authorization: str | None = Header(default=None)):
        identity = require_admin(authorization)
        try:
            reject_self_admin_status_change(actor=identity, target_user_id=user_id, delete_self=True)
            user_service.delete_user(user_id)
            return {"items": user_service.list_users()}
        except UserServiceError as exc:
            raise_user_error(exc)

    @router.get("/api/admin/membership-plans")
    async def admin_membership_plans(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"items": user_service.list_membership_plans()}

    @router.post("/api/admin/membership-plans")
    async def create_membership_plan(
        body: MembershipPlanCreateRequest,
        authorization: str | None = Header(default=None),
    ):
        require_admin(authorization)
        try:
            item = user_service.create_membership_plan(body.model_dump(mode="python"))
            return {"item": item, "items": user_service.list_membership_plans()}
        except UserServiceError as exc:
            raise_user_error(exc)

    @router.patch("/api/admin/membership-plans/{plan_id}")
    async def update_membership_plan(
        plan_id: str,
        body: MembershipPlanUpdateRequest,
        authorization: str | None = Header(default=None),
    ):
        require_admin(authorization)
        try:
            item = user_service.update_membership_plan(plan_id, body.model_dump(mode="python", exclude_unset=True))
            return {"item": item, "items": user_service.list_membership_plans()}
        except UserServiceError as exc:
            raise_user_error(exc)

    @router.delete("/api/admin/membership-plans/{plan_id}")
    async def delete_membership_plan(plan_id: str, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            user_service.delete_membership_plan(plan_id)
            return {"items": user_service.list_membership_plans()}
        except UserServiceError as exc:
            raise_user_error(exc)

    @router.post("/api/admin/redeem-codes/generate")
    async def generate_redeem_codes(body: RedeemCodeGenerateRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            codes = user_service.generate_redeem_codes(
                type=body.type,  # type: ignore[arg-type]
                value=body.value,
                count=body.count,
                expires_at=parse_optional_datetime(body.expires_at),
                membership_plan_id=body.membership_plan_id,
            )
            return {"codes": codes, "items": user_service.list_redeem_codes()}
        except UserServiceError as exc:
            raise_user_error(exc)

    @router.get("/api/admin/redeem-codes")
    async def list_redeem_codes(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"items": user_service.list_redeem_codes()}

    @router.patch("/api/admin/redeem-codes/{code_id}")
    async def update_redeem_code(
        code_id: str,
        body: RedeemCodeUpdateRequest,
        authorization: str | None = Header(default=None),
    ):
        require_admin(authorization)
        try:
            item = user_service.update_redeem_code(code_id, body.model_dump(mode="python", exclude_unset=True))
            return {"item": item, "items": user_service.list_redeem_codes()}
        except UserServiceError as exc:
            raise_user_error(exc)

    @router.delete("/api/admin/redeem-codes/{code_id}")
    async def delete_redeem_code(code_id: str, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            user_service.delete_redeem_code(code_id)
            return {"items": user_service.list_redeem_codes()}
        except UserServiceError as exc:
            raise_user_error(exc)

    @router.post("/api/redeem")
    async def redeem(body: RedeemRequest, authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        try:
            return user_service.redeem(str(identity.get("id") or ""), body.code)
        except UserServiceError as exc:
            raise_user_error(exc)

    @router.get("/api/redeem/history")
    async def redeem_history(authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        return {"items": user_service.redeem_history(str(identity.get("id") or ""))}

    @router.get("/api/admin/promo-codes")
    async def list_promo_codes(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"items": user_service.list_promo_codes()}

    @router.post("/api/admin/promo-codes")
    async def create_promo_code(body: PromoCodeCreateRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            item = user_service.create_promo_code(
                code=body.code,
                image_quota=body.image_quota,
                max_uses=body.max_uses,
                expires_at=parse_optional_datetime(body.expires_at),
                enabled=body.enabled,
            )
            return {"item": item, "items": user_service.list_promo_codes()}
        except UserServiceError as exc:
            raise_user_error(exc)

    @router.patch("/api/admin/promo-codes/{code_id}")
    async def update_promo_code(
        code_id: str,
        body: PromoCodeUpdateRequest,
        authorization: str | None = Header(default=None),
    ):
        require_admin(authorization)
        try:
            item = user_service.update_promo_code(code_id, body.model_dump(mode="python", exclude_unset=True))
            return {"item": item, "items": user_service.list_promo_codes()}
        except UserServiceError as exc:
            raise_user_error(exc)

    @router.delete("/api/admin/promo-codes/{code_id}")
    async def delete_promo_code(code_id: str, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            user_service.delete_promo_code(code_id)
            return {"items": user_service.list_promo_codes()}
        except UserServiceError as exc:
            raise_user_error(exc)

    return router

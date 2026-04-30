from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse

from api.support import require_identity
from services.gallery_service import gallery_service
from services.user_service import UserServiceError


def raise_gallery_error(exc: UserServiceError) -> None:
    raise HTTPException(
        status_code=exc.status_code,
        detail={"error": {"message": exc.message, "code": exc.code}},
    ) from exc


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/gallery/images")
    async def list_gallery_images(
        limit: int = Query(default=24, ge=1, le=60),
        cursor: str | None = Query(default=None),
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        try:
            return await run_in_threadpool(
                gallery_service.list_user_images_page,
                str(identity.get("id") or ""),
                limit=limit,
                cursor=cursor,
            )
        except UserServiceError as exc:
            raise_gallery_error(exc)

    @router.get("/api/gallery/images/{image_id}")
    async def get_gallery_image(image_id: str, authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        try:
            item = await run_in_threadpool(gallery_service.get_user_image, str(identity.get("id") or ""), image_id)
            return {"item": item}
        except UserServiceError as exc:
            raise_gallery_error(exc)

    @router.get("/api/gallery/images/{image_id}/content")
    async def get_gallery_image_content(image_id: str, authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        try:
            item, path = await run_in_threadpool(
                gallery_service.get_user_image_file,
                str(identity.get("id") or ""),
                image_id,
            )
            return FileResponse(
                path,
                media_type=str(item.get("content_type") or "image/png"),
                filename=str(item.get("filename") or f"{image_id}.png"),
            )
        except UserServiceError as exc:
            raise_gallery_error(exc)

    @router.delete("/api/gallery/images/{image_id}")
    async def delete_gallery_image(image_id: str, authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        try:
            await run_in_threadpool(gallery_service.delete_user_image, str(identity.get("id") or ""), image_id)
            return {"ok": True}
        except UserServiceError as exc:
            raise_gallery_error(exc)

    return router

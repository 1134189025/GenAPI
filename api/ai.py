from __future__ import annotations

from fastapi import APIRouter, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from api.support import require_identity, resolve_image_base_url
from services.log_service import LoggedCall
from services.quota_service import openai_quota_error, reserve_image_quota
from services.user_service import UserServiceError
from services.protocol import (
    openai_v1_image_edit,
    openai_v1_image_generations,
)


class ImageGenerationRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    model: str = "gpt-image-2"
    n: int = Field(default=1, ge=1, le=4)
    size: str | None = None
    response_format: str = "b64_json"
    history_disabled: bool = True
    stream: bool | None = None


def _reserve_or_error(identity: dict[str, object], count: int, endpoint: str):
    try:
        return reserve_image_quota(identity, count, endpoint)
    except UserServiceError as exc:
        if exc.status_code == 429:
            return JSONResponse(status_code=429, content=openai_quota_error(exc))
        raise HTTPException(status_code=exc.status_code, detail={"error": exc.message}) from exc


def create_router() -> APIRouter:
    router = APIRouter()

    @router.post("/api/image/generations")
    async def generate_images(
            body: ImageGenerationRequest,
            request: Request,
            authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        payload = body.model_dump(mode="python")
        payload["base_url"] = resolve_image_base_url(request)
        quota_reservation = _reserve_or_error(identity, body.n, "/api/image/generations")
        if isinstance(quota_reservation, JSONResponse):
            return quota_reservation
        call = LoggedCall(identity, "/api/image/generations", body.model, "文生图")
        return await call.run(openai_v1_image_generations.handle, payload, quota_reservation)

    @router.post("/api/image/edits")
    async def edit_images(
            request: Request,
            authorization: str | None = Header(default=None),
            image: list[UploadFile] | None = File(default=None),
            image_list: list[UploadFile] | None = File(default=None, alias="image[]"),
            prompt: str = Form(...),
            model: str = Form(default="gpt-image-2"),
            n: int = Form(default=1),
            size: str | None = Form(default=None),
            response_format: str = Form(default="b64_json"),
            stream: bool | None = Form(default=None),
    ):
        identity = require_identity(authorization)
        if n < 1 or n > 4:
            raise HTTPException(status_code=400, detail={"error": "n must be between 1 and 4"})
        uploads = [*(image or []), *(image_list or [])]
        if not uploads:
            raise HTTPException(status_code=400, detail={"error": "image file is required"})
        images: list[tuple[bytes, str, str]] = []
        for upload in uploads:
            image_data = await upload.read()
            if not image_data:
                raise HTTPException(status_code=400, detail={"error": "image file is empty"})
            images.append((image_data, upload.filename or "image.png", upload.content_type or "image/png"))
        payload = {
            "prompt": prompt,
            "images": images,
            "model": model,
            "n": n,
            "size": size,
            "response_format": response_format,
            "stream": stream,
            "base_url": resolve_image_base_url(request),
        }
        quota_reservation = _reserve_or_error(identity, n, "/api/image/edits")
        if isinstance(quota_reservation, JSONResponse):
            return quota_reservation
        call = LoggedCall(identity, "/api/image/edits", model, "图生图")
        return await call.run(openai_v1_image_edit.handle, payload, quota_reservation)

    return router

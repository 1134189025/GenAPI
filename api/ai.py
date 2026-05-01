from __future__ import annotations

import base64
from collections.abc import Iterable
from io import BytesIO

from fastapi import APIRouter, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from PIL import Image
from pydantic import BaseModel, Field

from api.support import require_identity, resolve_image_base_url
from services.gallery_service import gallery_service
from services.log_service import LoggedCall
from services.quota_service import openai_quota_error, reserve_image_quota, settle_image_quota
from services.user_service import UserServiceError
from services.protocol import (
    openai_v1_image_edit,
    openai_v1_image_generations,
)
from services.protocol.conversation import image_size_metadata, validate_image_size

IMAGE_EDIT_MAX_FILES = 4
IMAGE_EDIT_MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024
IMAGE_EDIT_MAX_TOTAL_SIZE_BYTES = 80 * 1024 * 1024
IMAGE_EDIT_MAX_MULTIPART_OVERHEAD_BYTES = 1024 * 1024


def image_edit_max_request_size_bytes() -> int:
    return IMAGE_EDIT_MAX_TOTAL_SIZE_BYTES + IMAGE_EDIT_MAX_MULTIPART_OVERHEAD_BYTES


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


def _validated_image_size(size: str | None) -> str:
    try:
        return validate_image_size(size)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc


def _image_dimensions(image_data: bytes) -> tuple[int | None, int | None]:
    try:
        with Image.open(BytesIO(image_data)) as image:
            width, height = image.size
    except Exception:
        return None, None
    return int(width), int(height)


def _attach_gallery_images(
        result: object,
        *,
        identity: dict[str, object],
        quota_reservation,
        endpoint: str,
        source: str,
        prompt: str,
        model: str,
        size: str | None,
        response_format: str,
) -> object:
    if isinstance(result, Iterable) and not isinstance(result, (dict, str, bytes, bytearray)):
        return (
            _attach_gallery_images(
                item,
                identity=identity,
                quota_reservation=quota_reservation,
                endpoint=endpoint,
                source=source,
                prompt=prompt,
                model=model,
                size=size,
                response_format=response_format,
            )
            for item in result
        )
    if not isinstance(result, dict):
        return result
    data = result.get("data")
    if not isinstance(data, list):
        return result
    user_id = str(identity.get("id") or "").strip()
    if not user_id:
        return result
    usage_event_id = getattr(quota_reservation, "event_id", "") or None
    created_gallery_ids: list[str] = []
    try:
        for item in data:
            if not isinstance(item, dict):
                continue
            if item.get("gallery_id"):
                if response_format != "b64_json":
                    item.pop("b64_json", None)
                continue
            b64_json = str(item.get("b64_json") or "").strip()
            if not b64_json:
                continue
            try:
                image_data = base64.b64decode(b64_json)
            except Exception as exc:
                raise HTTPException(status_code=502, detail={"error": "invalid generated image data"}) from exc
            width, height = _image_dimensions(image_data)
            metadata = {
                **image_size_metadata(size),
                "width": width,
                "height": height,
            }
            gallery_item = gallery_service.save_user_image(
                user_id=user_id,
                image_data=image_data,
                prompt=prompt,
                revised_prompt=str(item.get("revised_prompt") or ""),
                model=model,
                size=size or "",
                source=source,
                source_endpoint=endpoint,
                usage_event_id=usage_event_id,
                metadata=metadata,
            )
            gallery_id = str(gallery_item["id"])
            created_gallery_ids.append(gallery_id)
            item.update(metadata)
            item["gallery_id"] = gallery_id
            item["content_url"] = gallery_item["content_url"]
            item["url"] = gallery_item["content_url"]
            item["expires_at"] = gallery_item["expires_at"]
            if response_format != "b64_json":
                item.pop("b64_json", None)
    except Exception:
        for gallery_id in created_gallery_ids:
            try:
                gallery_service.discard_user_image(user_id, gallery_id)
            except Exception:
                pass
        raise
    return result


async def _read_limited_uploads(uploads: list[UploadFile]) -> list[tuple[bytes, str, str]]:
    if len(uploads) > IMAGE_EDIT_MAX_FILES:
        raise HTTPException(status_code=400, detail={"error": "image count must be between 1 and 4"})
    images: list[tuple[bytes, str, str]] = []
    total_size = 0
    chunk_size = 1024 * 1024
    for upload in uploads:
        chunks: list[bytes] = []
        file_size = 0
        while True:
            chunk = await upload.read(chunk_size)
            if not chunk:
                break
            file_size += len(chunk)
            total_size += len(chunk)
            if file_size > IMAGE_EDIT_MAX_FILE_SIZE_BYTES:
                raise HTTPException(status_code=413, detail={"error": "image file is too large"})
            if total_size > IMAGE_EDIT_MAX_TOTAL_SIZE_BYTES:
                raise HTTPException(status_code=413, detail={"error": "image files are too large"})
            chunks.append(chunk)
        if file_size == 0:
            raise HTTPException(status_code=400, detail={"error": "image file is empty"})
        images.append((b"".join(chunks), upload.filename or "image.png", upload.content_type or "image/png"))
    return images


def create_router() -> APIRouter:
    router = APIRouter()

    @router.post("/api/image/generations")
    async def generate_images(
            body: ImageGenerationRequest,
            request: Request,
            authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        size = _validated_image_size(body.size)
        payload = body.model_dump(mode="python")
        payload["size"] = size or None
        payload["base_url"] = resolve_image_base_url(request)
        payload["save_public_images"] = False
        quota_reservation = _reserve_or_error(identity, body.n, "/api/image/generations")
        if isinstance(quota_reservation, JSONResponse):
            return quota_reservation
        call = LoggedCall(identity, "/api/image/generations", body.model, "文生图")

        def handle_with_gallery(call_payload):
            return _attach_gallery_images(
                openai_v1_image_generations.handle(call_payload),
                identity=identity,
                quota_reservation=quota_reservation,
                endpoint="/api/image/generations",
                source="generation",
                prompt=body.prompt,
                model=body.model,
                size=size,
                response_format=body.response_format,
            )

        return await call.run(handle_with_gallery, payload, quota_reservation)

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
        normalized_size = _validated_image_size(size)
        quota_reservation = _reserve_or_error(identity, n, "/api/image/edits")
        if isinstance(quota_reservation, JSONResponse):
            return quota_reservation
        try:
            images = await _read_limited_uploads(uploads)
        except HTTPException as exc:
            settle_image_quota(quota_reservation, success=False, error=str(exc.detail))
            raise
        except Exception as exc:
            settle_image_quota(quota_reservation, success=False, error=str(exc))
            raise
        payload = {
            "prompt": prompt,
            "images": images,
            "model": model,
            "n": n,
            "size": normalized_size or None,
            "response_format": response_format,
            "stream": stream,
            "base_url": resolve_image_base_url(request),
            "save_public_images": False,
        }
        call = LoggedCall(identity, "/api/image/edits", model, "图生图")

        def handle_with_gallery(call_payload):
            return _attach_gallery_images(
                openai_v1_image_edit.handle(call_payload),
                identity=identity,
                quota_reservation=quota_reservation,
                endpoint="/api/image/edits",
                source="edit",
                prompt=prompt,
                model=model,
                size=normalized_size,
                response_format=response_format,
            )

        return await call.run(handle_with_gallery, payload, quota_reservation)

    return router

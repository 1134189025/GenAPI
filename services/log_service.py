from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, StreamingResponse

from services.config import DATA_DIR
from utils.helper import anthropic_sse_stream, redact_sensitive_text, sse_json_stream

LOG_TYPE_CALL = "call"
LOG_TYPE_ACCOUNT = "account"


class LogService:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def add(self, type: str, summary: str = "", detail: dict[str, Any] | None = None, **data: Any) -> None:
        item = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "type": type,
            "summary": summary,
            "detail": detail or data,
        }
        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")

    def _iter_lines_reverse(self, block_size: int = 64 * 1024):
        with self.path.open("rb") as file:
            file.seek(0, 2)
            position = file.tell()
            buffer = b""
            while position > 0:
                read_size = min(block_size, position)
                position -= read_size
                file.seek(position)
                lines = (file.read(read_size) + buffer).split(b"\n")
                buffer = lines[0]
                for line in reversed(lines[1:]):
                    if line:
                        yield line.decode("utf-8", errors="replace")
            if buffer:
                yield buffer.decode("utf-8", errors="replace")

    def list(self, type: str = "", start_date: str = "", end_date: str = "", limit: int = 200) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        items: list[dict[str, Any]] = []
        for line in self._iter_lines_reverse():
            try:
                item = json.loads(line)
            except Exception:
                continue
            t = str(item.get("time") or "")
            day = t[:10]
            if type and item.get("type") != type:
                continue
            if start_date and day < start_date:
                continue
            if end_date and day > end_date:
                continue
            items.append(item)
            if len(items) >= limit:
                break
        return items


log_service = LogService(DATA_DIR / "logs.jsonl")


def _collect_urls(value: object) -> list[str]:
    urls: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "url" and isinstance(item, str):
                urls.append(item)
            elif key == "urls" and isinstance(item, list):
                urls.extend(str(url) for url in item if isinstance(url, str))
            else:
                urls.extend(_collect_urls(item))
    elif isinstance(value, list):
        for item in value:
            urls.extend(_collect_urls(item))
    return urls


def _count_delivered_images(value: object) -> int:
    if isinstance(value, dict):
        data = value.get("data")
        if isinstance(data, list):
            return sum(
                1
                for item in data
                if isinstance(item, dict) and (item.get("b64_json") or item.get("url"))
            )
        if value.get("b64_json"):
            return 1
        return 0
    if isinstance(value, list):
        return sum(_count_delivered_images(item) for item in value)
    return 0


def _image_error_response(exc: Exception) -> JSONResponse:
    message = redact_sensitive_text(str(exc))
    if "no available image quota" in message.lower():
        return JSONResponse(
            status_code=429,
            content={
                "error": {
                    "message": "no available image quota",
                    "type": "insufficient_quota",
                    "param": None,
                    "code": "insufficient_quota",
                }
            },
        )
    if hasattr(exc, "status_code"):
        return JSONResponse(
            status_code=int(exc.status_code),
            content={
                "error": {
                    "message": message,
                    "type": getattr(exc, "error_type", "server_error"),
                    "param": getattr(exc, "param", None),
                    "code": getattr(exc, "code", "upstream_error"),
                }
            },
        )
    return JSONResponse(
        status_code=502,
        content={
            "error": {
                "message": message,
                "type": "server_error",
                "param": None,
                "code": "upstream_error",
            }
        },
    )


def _next_item(items):
    try:
        return True, next(items)
    except StopIteration:
        return False, None


def _close_iterator(items) -> None:
    close = getattr(items, "close", None)
    if callable(close):
        close()


def _prepend_item(first, items):
    try:
        yield first
        yield from items
    finally:
        _close_iterator(items)


_NO_PENDING_ITEM = object()


def _is_sse_data_chunk(chunk: object) -> bool:
    if isinstance(chunk, str):
        return chunk.startswith("data:")
    if isinstance(chunk, bytes | memoryview):
        return bytes(chunk).startswith(b"data:")
    return False


class _QuotaTrackedStream:
    def __init__(self, call: "LoggedCall", items, quota_reservation=None):
        self.call = call
        self.items = iter(items)
        self.quota_reservation = quota_reservation
        self.urls: list[str] = []
        self.actual_count = 0
        self.closed = False
        self.pending_item: object = _NO_PENDING_ITEM

    def __iter__(self):
        return self

    def __next__(self):
        if self.closed:
            raise StopIteration
        self.mark_pending_delivered()
        try:
            item = next(self.items)
        except StopIteration:
            self.close()
            raise
        except asyncio.CancelledError as exc:
            self._fail(exc, safe_error="image request cancelled", suffix="流式调用取消", status="cancelled")
            raise
        except Exception as exc:
            self._fail(exc)
            raise
        self.pending_item = item
        return item

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            _close_iterator(self.items)
        finally:
            self._settle_success()

    def mark_pending_delivered(self) -> None:
        if self.pending_item is _NO_PENDING_ITEM:
            return
        item = self.pending_item
        self.pending_item = _NO_PENDING_ITEM
        self.urls.extend(_collect_urls(item))
        self.actual_count += _count_delivered_images(item)

    def _settle_success(self) -> None:
        from services.quota_service import settle_image_quota

        settle_image_quota(self.quota_reservation, success=True, actual_count=self.actual_count)
        self.call.log("流式调用结束", urls=self.urls)

    def _fail(
        self,
        exc: BaseException,
        *,
        safe_error: str | None = None,
        suffix: str = "流式调用失败",
        status: str = "failed",
    ) -> None:
        from services.quota_service import settle_image_quota

        if self.closed:
            return
        self.closed = True
        safe_message = safe_error or redact_sensitive_text(str(exc))
        settle_image_quota(
            self.quota_reservation,
            success=self.actual_count > 0,
            actual_count=self.actual_count,
            error=safe_message,
        )
        self.call.log(suffix, status=status, error=safe_message, urls=self.urls)


class _QuotaTrackedStreamingResponse(StreamingResponse):
    def __init__(self, sender, tracked_items: _QuotaTrackedStream, *, media_type: str):
        self._tracked_items = tracked_items
        self._sse_items = sender(tracked_items)
        super().__init__(self._sse_items, media_type=media_type)

    async def stream_response(self, send) -> None:
        await send({"type": "http.response.start", "status": self.status_code, "headers": self.raw_headers})
        try:
            async for chunk in self.body_iterator:
                if not isinstance(chunk, bytes | memoryview):
                    chunk = chunk.encode(self.charset)
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
                if _is_sse_data_chunk(chunk):
                    self._tracked_items.mark_pending_delivered()

            await send({"type": "http.response.body", "body": b"", "more_body": False})
        finally:
            _close_iterator(self._sse_items)
            _close_iterator(self._tracked_items)


@dataclass
class LoggedCall:
    identity: dict[str, object]
    endpoint: str
    model: str
    summary: str
    started: float = field(default_factory=time.time)

    async def run(self, handler, *args, sse: str = "openai"):
        from services.protocol.conversation import ImageGenerationError
        from services.quota_service import settle_image_quota

        quota_reservation = None
        if args and hasattr(args[-1], "requested_count") and hasattr(args[-1], "bypass"):
            *args, quota_reservation = args
        try:
            result = await run_in_threadpool(handler, *args)
        except asyncio.CancelledError:
            safe_error = "image request cancelled"
            settle_image_quota(quota_reservation, success=False, error=safe_error)
            self.log("调用取消", status="cancelled", error=safe_error)
            raise
        except ImageGenerationError as exc:
            safe_error = redact_sensitive_text(str(exc))
            settle_image_quota(quota_reservation, success=False, error=safe_error)
            self.log("调用失败", status="failed", error=safe_error)
            return _image_error_response(exc)
        except HTTPException as exc:
            safe_error = redact_sensitive_text(str(exc.detail))
            settle_image_quota(quota_reservation, success=False, error=safe_error)
            self.log("调用失败", status="failed", error=safe_error)
            raise
        except Exception as exc:
            safe_error = redact_sensitive_text(str(exc))
            settle_image_quota(quota_reservation, success=False, error=safe_error)
            self.log("调用失败", status="failed", error=safe_error)
            raise HTTPException(status_code=502, detail={"error": safe_error}) from exc

        if isinstance(result, dict):
            settle_image_quota(quota_reservation, success=True, actual_count=_count_delivered_images(result))
            self.log("调用完成", result)
            return result

        sender = anthropic_sse_stream if sse == "anthropic" else sse_json_stream
        try:
            has_first, first = await run_in_threadpool(_next_item, result)
        except asyncio.CancelledError:
            safe_error = "image request cancelled"
            settle_image_quota(quota_reservation, success=False, error=safe_error)
            self.log("流式调用取消", status="cancelled", error=safe_error)
            raise
        except ImageGenerationError as exc:
            safe_error = redact_sensitive_text(str(exc))
            settle_image_quota(quota_reservation, success=False, error=safe_error)
            self.log("调用失败", status="failed", error=safe_error)
            return _image_error_response(exc)
        except HTTPException as exc:
            safe_error = redact_sensitive_text(str(exc.detail))
            settle_image_quota(quota_reservation, success=False, error=safe_error)
            self.log("调用失败", status="failed", error=safe_error)
            raise
        except Exception as exc:
            safe_error = redact_sensitive_text(str(exc))
            settle_image_quota(quota_reservation, success=False, error=safe_error)
            self.log("调用失败", status="failed", error=safe_error)
            raise HTTPException(status_code=502, detail={"error": safe_error}) from exc
        if not has_first:
            settle_image_quota(quota_reservation, success=True, actual_count=0)
            self.log("流式调用结束")
            return StreamingResponse(sender(()), media_type="text/event-stream")
        tracked_items = self.stream(_prepend_item(first, result), quota_reservation=quota_reservation)
        return _QuotaTrackedStreamingResponse(sender, tracked_items, media_type="text/event-stream")

    def sse_stream(self, sender, tracked_items):
        sse_items = sender(tracked_items)
        try:
            for chunk in sse_items:
                yield chunk
        finally:
            _close_iterator(sse_items)
            _close_iterator(tracked_items)

    def stream(self, items, quota_reservation=None):
        return _QuotaTrackedStream(self, items, quota_reservation=quota_reservation)

    def log(self, suffix: str, result: object = None, status: str = "success", error: str = "",
            urls: list[str] | None = None) -> None:
        detail = {
            "key_id": self.identity.get("id"),
            "key_name": self.identity.get("name"),
            "role": self.identity.get("role"),
            "endpoint": self.endpoint,
            "model": self.model,
            "started_at": datetime.fromtimestamp(self.started).strftime("%Y-%m-%d %H:%M:%S"),
            "ended_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "duration_ms": int((time.time() - self.started) * 1000),
            "status": status,
        }
        if error:
            detail["error"] = error
        collected_urls = [*(urls or []), *_collect_urls(result)]
        if collected_urls:
            detail["urls"] = list(dict.fromkeys(collected_urls))
        log_service.add(LOG_TYPE_CALL, f"{self.summary}{suffix}", detail)

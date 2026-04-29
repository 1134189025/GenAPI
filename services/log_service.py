from __future__ import annotations

import json
import itertools
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
        return StreamingResponse(
            sender(self.stream(itertools.chain([first], result), quota_reservation=quota_reservation)),
            media_type="text/event-stream",
        )

    def stream(self, items, quota_reservation=None):
        from services.quota_service import settle_image_quota

        urls: list[str] = []
        actual_count = 0
        failed = False
        try:
            for item in items:
                urls.extend(_collect_urls(item))
                actual_count += _count_delivered_images(item)
                yield item
        except Exception as exc:
            failed = True
            safe_error = redact_sensitive_text(str(exc))
            # Failed streams that already delivered images should charge those images and refund the rest.
            settle_image_quota(
                quota_reservation,
                success=actual_count > 0,
                actual_count=actual_count,
                error=safe_error,
            )
            self.log("流式调用失败", status="failed", error=safe_error, urls=urls)
            raise
        finally:
            if not failed:
                settle_image_quota(quota_reservation, success=True, actual_count=actual_count)
                self.log("流式调用结束", urls=urls)

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

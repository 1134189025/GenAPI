from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

import tiktoken

from services.account_service import account_service
from services.config import config
from services.image_cache_service import locked_image_cache
from services.openai_backend_api import OpenAIBackendAPI
from utils.helper import IMAGE_MODELS, anonymize_token, redact_sensitive_text
from utils.log import logger


class ImageGenerationError(Exception):
    def __init__(
        self,
        message: str,
        status_code: int = 502,
        error_type: str = "server_error",
        code: str | None = "upstream_error",
        param: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_type = error_type
        self.code = code
        self.param = param

    def to_openai_error(self) -> dict[str, Any]:
        return {
            "error": {
                "message": str(self),
                "type": self.error_type,
                "param": self.param,
                "code": self.code,
            }
        }


def is_token_invalid_error(message: str) -> bool:
    text = str(message or "").lower()
    return (
        "http 401" in text
        or "status=401" in text
        or "status 401" in text
        or "token_invalidated" in text
        or "token_revoked" in text
        or "authentication token has been invalidated" in text
        or "invalidated oauth token" in text
    )


def is_rate_limited_error(message: str) -> bool:
    text = str(message or "").lower()
    return (
        "http 429" in text
        or "status=429" in text
        or "status 429" in text
        or "rate limit" in text
        or "rate_limit" in text
        or "too many requests" in text
        or "usage limit" in text
        or "no available image quota" in text
        or "image quota" in text and "limit" in text
    )


def encode_images(images: Iterable[tuple[bytes, str, str]]) -> list[str]:
    return [base64.b64encode(data).decode("ascii") for data, _, _ in images if data]


def save_image_bytes(image_data: bytes, base_url: str | None = None) -> str:
    config.cleanup_old_images()
    file_hash = hashlib.md5(image_data).hexdigest()
    filename = f"{int(time.time())}_{file_hash}.png"
    relative_dir = Path(time.strftime("%Y"), time.strftime("%m"), time.strftime("%d"))
    file_path = config.images_dir / relative_dir / filename
    with locked_image_cache():
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(image_data)
        config.cleanup_old_images(protected_paths={file_path})
    return f"{(base_url or config.base_url)}/images/{relative_dir.as_posix()}/{filename}"


def message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and str(item.get("type") or "") in {"text", "input_text", "output_text"}:
                parts.append(str(item.get("text") or ""))
        return "".join(parts)
    return ""


def normalize_messages(messages: object, system: Any = None) -> list[dict[str, Any]]:
    normalized = []
    system_text = message_text(system)
    if system_text:
        normalized.append({"role": "system", "content": system_text})
    if isinstance(messages, list):
        for message in messages:
            if isinstance(message, dict):
                normalized.append({"role": message.get("role", "user"), "content": message_text(message.get("content", ""))})
    return normalized


def assistant_history_text(messages: list[dict[str, Any]]) -> str:
    return "".join(str(item.get("content") or "") for item in messages if item.get("role") == "assistant")


def assistant_history_messages(messages: list[dict[str, Any]]) -> list[str]:
    return [str(item.get("content") or "") for item in messages if item.get("role") == "assistant" and item.get("content")]


IMAGE_RESOLUTION_PRESETS: dict[str, dict[str, Any]] = {
    "1024x1024": {
        "target_width": 1024,
        "target_height": 1024,
        "target_aspect_ratio": "1:1",
        "prompt_hint": "目标输出分辨率为 1024x1024，1:1 正方形构图，主体居中，适合正方形画幅。",
    },
    "1536x864": {
        "target_width": 1536,
        "target_height": 864,
        "target_aspect_ratio": "16:9",
        "prompt_hint": "目标输出分辨率为 1536x864，16:9 横版构图，适合宽画幅展示。",
    },
    "864x1536": {
        "target_width": 864,
        "target_height": 1536,
        "target_aspect_ratio": "9:16",
        "prompt_hint": "目标输出分辨率为 864x1536，9:16 竖版构图，适合竖版画幅展示。",
    },
    "1280x960": {
        "target_width": 1280,
        "target_height": 960,
        "target_aspect_ratio": "4:3",
        "prompt_hint": "目标输出分辨率为 1280x960，4:3 横版构图，兼顾宽度与高度，适合展示画面细节。",
    },
    "960x1280": {
        "target_width": 960,
        "target_height": 1280,
        "target_aspect_ratio": "3:4",
        "prompt_hint": "目标输出分辨率为 960x1280，3:4 竖版构图，适合人物肖像或竖向场景。",
    },
    "1920x1080": {
        "target_width": 1920,
        "target_height": 1080,
        "target_aspect_ratio": "16:9",
        "prompt_hint": "目标输出分辨率为 1920x1080，1080P 16:9 横版构图，适合高清横幅展示。",
    },
    "1080x1920": {
        "target_width": 1080,
        "target_height": 1920,
        "target_aspect_ratio": "9:16",
        "prompt_hint": "目标输出分辨率为 1080x1920，1080P 9:16 竖版构图，适合手机竖屏展示。",
    },
    "2560x1440": {
        "target_width": 2560,
        "target_height": 1440,
        "target_aspect_ratio": "16:9",
        "prompt_hint": "目标输出分辨率为 2560x1440，2K QHD 16:9 横版构图，适合高细节横屏展示。",
    },
    "1440x2560": {
        "target_width": 1440,
        "target_height": 2560,
        "target_aspect_ratio": "9:16",
        "prompt_hint": "目标输出分辨率为 1440x2560，2K QHD 9:16 竖版构图，适合高细节手机竖屏展示。",
    },
    "3840x2160": {
        "target_width": 3840,
        "target_height": 2160,
        "target_aspect_ratio": "16:9",
        "prompt_hint": "目标输出分辨率为 3840x2160，4K UHD 16:9 横版构图，适合超高清横屏展示。",
    },
    "2160x3840": {
        "target_width": 2160,
        "target_height": 3840,
        "target_aspect_ratio": "9:16",
        "prompt_hint": "目标输出分辨率为 2160x3840，4K UHD 9:16 竖版构图，适合超高清手机竖屏展示。",
    },
}

LEGACY_IMAGE_ASPECT_RATIOS: dict[str, str] = {
    "1:1": "输出为 1:1 正方形构图，主体居中，适合正方形画幅。",
    "16:9": "输出为 16:9 横屏构图，适合宽画幅展示。",
    "9:16": "输出为 9:16 竖屏构图，适合竖版画幅展示。",
    "4:3": "输出为 4:3 比例，兼顾宽度与高度，适合展示画面细节。",
    "3:4": "输出为 3:4 比例，纵向构图，适合人物肖像或竖向场景。",
}


def normalize_image_size(size: str | None) -> str:
    value = str(size or "").strip().lower().replace("×", "x")
    return re.sub(r"\s+", "", value)


def validate_image_size(size: str | None) -> str:
    normalized = normalize_image_size(size)
    if not normalized:
        return ""
    if normalized in IMAGE_RESOLUTION_PRESETS or normalized in LEGACY_IMAGE_ASPECT_RATIOS:
        return normalized
    supported = [*IMAGE_RESOLUTION_PRESETS.keys(), *LEGACY_IMAGE_ASPECT_RATIOS.keys()]
    raise ValueError(f"unsupported image size: {size}. supported sizes: {', '.join(supported)}")


def image_size_metadata(size: str | None) -> dict[str, object]:
    normalized = validate_image_size(size)
    if not normalized:
        return {}
    if normalized in IMAGE_RESOLUTION_PRESETS:
        preset = IMAGE_RESOLUTION_PRESETS[normalized]
        return {
            "target_size": normalized,
            "target_width": preset["target_width"],
            "target_height": preset["target_height"],
            "target_aspect_ratio": preset["target_aspect_ratio"],
        }
    return {
        "target_size": normalized,
        "target_width": None,
        "target_height": None,
        "target_aspect_ratio": normalized,
    }


def build_image_prompt(prompt: str, size: str | None) -> str:
    normalized = validate_image_size(size)
    if not normalized:
        return prompt
    if normalized in IMAGE_RESOLUTION_PRESETS:
        hint = str(IMAGE_RESOLUTION_PRESETS[normalized]["prompt_hint"])
    else:
        hint = LEGACY_IMAGE_ASPECT_RATIOS[normalized]
    return f"{prompt.strip()}\n\n{hint}"


def encoding_for_model(model: str):
    try:
        return tiktoken.encoding_for_model(model)
    except KeyError:
        try:
            return tiktoken.get_encoding("o200k_base")
        except KeyError:
            return tiktoken.get_encoding("cl100k_base")


def count_message_tokens(messages: list[dict[str, Any]], model: str) -> int:
    encoding = encoding_for_model(model)
    total = 0
    for message in messages:
        total += 3
        for key, value in message.items():
            if not isinstance(value, str):
                continue
            total += len(encoding.encode(value))
            if key == "name":
                total += 1
    return total + 3


def count_text_tokens(text: str, model: str) -> int:
    return len(encoding_for_model(model).encode(text))


def format_image_result(
    items: list[dict[str, Any]],
    prompt: str,
    response_format: str,
    base_url: str | None = None,
    created: int | None = None,
    message: str = "",
    save_public_images: bool = True,
) -> dict[str, Any]:
    data: list[dict[str, Any]] = []
    for item in items:
        b64_json = str(item.get("b64_json") or "").strip()
        if not b64_json:
            continue
        revised_prompt = str(item.get("revised_prompt") or prompt).strip() or prompt
        image_data = base64.b64decode(b64_json)
        if response_format == "b64_json":
            result_item = {
                "b64_json": b64_json,
                "revised_prompt": revised_prompt,
            }
            if save_public_images:
                result_item["url"] = save_image_bytes(image_data, base_url)
            data.append(result_item)
        else:
            result_item = {"revised_prompt": revised_prompt}
            if save_public_images:
                result_item["url"] = save_image_bytes(image_data, base_url)
            else:
                result_item["b64_json"] = b64_json
            data.append(result_item)
    result: dict[str, Any] = {"created": created or int(time.time()), "data": data}
    if message and not data:
        result["message"] = message
    return result


@dataclass
class ConversationRequest:
    model: str = "auto"
    prompt: str = ""
    messages: list[dict[str, Any]] | None = None
    images: list[str] | None = None
    n: int = 1
    size: str | None = None
    response_format: str = "b64_json"
    base_url: str | None = None
    message_as_error: bool = False
    save_public_images: bool = True


@dataclass
class ConversationState:
    text: str = ""
    conversation_id: str = ""
    file_ids: list[str] = field(default_factory=list)
    sediment_ids: list[str] = field(default_factory=list)
    blocked: bool = False
    tool_invoked: bool | None = None
    turn_use_case: str = ""


@dataclass
class ImageOutput:
    kind: str
    model: str
    index: int
    total: int
    created: int = field(default_factory=lambda: int(time.time()))
    text: str = ""
    upstream_event_type: str = ""
    data: list[dict[str, Any]] = field(default_factory=list)

    def to_chunk(self) -> dict[str, Any]:
        chunk: dict[str, Any] = {
            "object": "image.generation.chunk",
            "created": self.created,
            "model": self.model,
            "index": self.index,
            "total": self.total,
            "progress_text": self.text,
            "upstream_event_type": self.upstream_event_type,
            "data": [],
        }
        if self.kind == "message":
            chunk.update({
                "object": "image.generation.message",
                "message": self.text,
            })
            chunk.pop("progress_text", None)
            chunk.pop("upstream_event_type", None)
        elif self.kind == "result":
            chunk.update({
                "object": "image.generation.result",
                "data": self.data,
            })
            chunk.pop("progress_text", None)
            chunk.pop("upstream_event_type", None)
        return chunk


def assistant_message_text(message: dict[str, Any]) -> str:
    content = message.get("content") or {}
    parts = content.get("parts") or []
    if not isinstance(parts, list):
        return ""
    return "".join(part for part in parts if isinstance(part, str))


def strip_history(text: str, history_text: str = "") -> str:
    text = str(text or "")
    history_text = str(history_text or "")
    while history_text and text.startswith(history_text):
        text = text[len(history_text):]
    return text


def assistant_text(event: dict[str, Any], current_text: str = "", history_text: str = "") -> str:
    for candidate in (event, event.get("v")):
        if not isinstance(candidate, dict):
            continue
        message = candidate.get("message")
        if not isinstance(message, dict):
            continue
        role = str((message.get("author") or {}).get("role") or "").strip().lower()
        if role != "assistant":
            continue
        text = assistant_message_text(message)
        if text:
            return strip_history(text, history_text)
    return apply_text_patch(event, current_text, history_text)


def event_assistant_text(event: dict[str, Any], history_text: str = "") -> str:
    for candidate in (event, event.get("v")):
        if not isinstance(candidate, dict):
            continue
        message = candidate.get("message")
        if isinstance(message, dict) and (message.get("author") or {}).get("role") == "assistant":
            return strip_history(assistant_message_text(message), history_text)
    return ""


def apply_text_patch(event: dict[str, Any], current_text: str = "", history_text: str = "") -> str:
    if event.get("p") == "/message/content/parts/0":
        return apply_patch_op(event, current_text, history_text)

    operations = event.get("v")
    if isinstance(operations, str) and current_text and not event.get("p") and not event.get("o"):
        return current_text + operations

    if event.get("o") == "patch" and isinstance(operations, list):
        text = current_text
        for item in operations:
            if isinstance(item, dict):
                text = apply_text_patch(item, text, history_text)
        return text

    if not isinstance(operations, list):
        return current_text

    text = current_text
    for item in operations:
        if isinstance(item, dict):
            text = apply_text_patch(item, text, history_text)
    return text


def apply_patch_op(operation: dict[str, Any], current_text: str, history_text: str = "") -> str:
    op = operation.get("o")
    value = str(operation.get("v") or "")
    if op == "append":
        return current_text + value
    if op == "replace":
        return strip_history(value, history_text)
    return current_text


def add_unique(values: list[str], candidates: list[str]) -> None:
    for candidate in candidates:
        if candidate and candidate not in values:
            values.append(candidate)


def extract_conversation_ids(payload: str) -> tuple[str, list[str], list[str]]:
    conversation_match = re.search(r'"conversation_id"\s*:\s*"([^"]+)"', payload)
    conversation_id = conversation_match.group(1) if conversation_match else ""
    file_ids = re.findall(r"(file[-_][A-Za-z0-9]+)", payload)
    sediment_ids = re.findall(r"sediment://([A-Za-z0-9_-]+)", payload)
    return conversation_id, file_ids, sediment_ids


def is_image_tool_event(event: dict[str, Any]) -> bool:
    value = event.get("v")
    message = event.get("message") or (value.get("message") if isinstance(value, dict) else None)
    if not isinstance(message, dict):
        return False
    metadata = message.get("metadata") or {}
    author = message.get("author") or {}
    return author.get("role") == "tool" and metadata.get("async_task_type") == "image_gen"


def update_conversation_state(state: ConversationState, payload: str, event: dict[str, Any] | None = None) -> None:
    conversation_id, file_ids, sediment_ids = extract_conversation_ids(payload)
    if conversation_id and not state.conversation_id:
        state.conversation_id = conversation_id
    if isinstance(event, dict) and is_image_tool_event(event):
        add_unique(state.file_ids, file_ids)
        add_unique(state.sediment_ids, sediment_ids)
    if not isinstance(event, dict):
        return
    state.conversation_id = str(event.get("conversation_id") or state.conversation_id)
    value = event.get("v")
    if isinstance(value, dict):
        state.conversation_id = str(value.get("conversation_id") or state.conversation_id)
    if event.get("type") == "moderation":
        moderation = event.get("moderation_response")
        if isinstance(moderation, dict) and moderation.get("blocked") is True:
            state.blocked = True
    if event.get("type") == "server_ste_metadata":
        metadata = event.get("metadata")
        if isinstance(metadata, dict):
            if isinstance(metadata.get("tool_invoked"), bool):
                state.tool_invoked = metadata["tool_invoked"]
            state.turn_use_case = str(metadata.get("turn_use_case") or state.turn_use_case)


def conversation_base_event(event_type: str, state: ConversationState, **extra: Any) -> dict[str, Any]:
    return {
        "type": event_type,
        "text": state.text,
        "conversation_id": state.conversation_id,
        "file_ids": list(state.file_ids),
        "sediment_ids": list(state.sediment_ids),
        "blocked": state.blocked,
        "tool_invoked": state.tool_invoked,
        "turn_use_case": state.turn_use_case,
        **extra,
    }


def iter_conversation_payloads(payloads: Iterator[str], history_text: str = "",
                               history_messages: list[str] | None = None) -> Iterator[dict[str, Any]]:
    state = ConversationState()
    history_messages = history_messages or []
    history_index = 0
    for payload in payloads:
        # print(f"[upstream_sse] {payload}", flush=True)
        if not payload:
            continue
        if payload == "[DONE]":
            yield conversation_base_event("conversation.done", state, done=True)
            break
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            update_conversation_state(state, payload)
            yield conversation_base_event("conversation.raw", state, payload=payload)
            continue
        if not isinstance(event, dict):
            yield conversation_base_event("conversation.event", state, raw=event)
            continue
        update_conversation_state(state, payload, event)
        if history_index < len(history_messages) and event_assistant_text(event, history_text) == history_messages[history_index]:
            history_index += 1
            state.text = ""
            continue
        next_text = assistant_text(event, state.text, history_text)
        if next_text != state.text:
            delta = next_text[len(state.text):] if next_text.startswith(state.text) else next_text
            state.text = next_text
            yield conversation_base_event("conversation.delta", state, raw=event, delta=delta)
            continue
        yield conversation_base_event("conversation.event", state, raw=event)


def conversation_events(
    backend: OpenAIBackendAPI,
    messages: list[dict[str, Any]] | None = None,
    model: str = "auto",
    prompt: str = "",
    images: list[str] | None = None,
    size: str | None = None,
) -> Iterator[dict[str, Any]]:
    normalized = normalize_messages(messages or ([{"role": "user", "content": prompt}] if prompt else []))
    image_model = str(model or "").strip() in IMAGE_MODELS
    history_text = "" if image_model else assistant_history_text(normalized)
    history_messages = [] if image_model else assistant_history_messages(normalized)
    final_prompt = build_image_prompt(prompt, size) if image_model else prompt
    payloads = backend.stream_conversation(
        messages=normalized,
        model=model,
        prompt=final_prompt,
        images=images if image_model else None,
        system_hints=["picture_v2"] if image_model else None,
    )
    yield from iter_conversation_payloads(payloads, history_text, history_messages)


def text_backend() -> OpenAIBackendAPI:
    return OpenAIBackendAPI(access_token=account_service.get_text_access_token())


def stream_text_deltas(backend: OpenAIBackendAPI, request: ConversationRequest) -> Iterator[str]:
    for event in conversation_events(backend, messages=request.messages, model=request.model, prompt=request.prompt):
        if event.get("type") != "conversation.delta":
            continue
        delta = str(event.get("delta") or "")
        if delta:
            yield delta


def collect_text(backend: OpenAIBackendAPI, request: ConversationRequest) -> str:
    return "".join(stream_text_deltas(backend, request))


def stream_image_outputs(
        backend: OpenAIBackendAPI,
        request: ConversationRequest,
        index: int = 1,
        total: int = 1,
) -> Iterator[ImageOutput]:
    last: dict[str, Any] = {}
    for event in conversation_events(
            backend,
            prompt=request.prompt,
            model=request.model,
            images=request.images or [],
            size=request.size,
    ):
        last = event
        if event.get("type") == "conversation.delta":
            yield ImageOutput(
                kind="progress",
                model=request.model,
                index=index,
                total=total,
                text=str(event.get("delta") or ""),
                upstream_event_type="conversation.delta",
            )
            continue
        if event.get("type") == "conversation.event":
            raw = event.get("raw")
            raw_type = str(raw.get("type") or "") if isinstance(raw, dict) else ""
            yield ImageOutput(
                kind="progress",
                model=request.model,
                index=index,
                total=total,
                upstream_event_type=raw_type,
            )

    conversation_id = str(last.get("conversation_id") or "")
    file_ids = [str(item) for item in last.get("file_ids") or []]
    sediment_ids = [str(item) for item in last.get("sediment_ids") or []]
    message = str(last.get("text") or "").strip()
    is_text_response = last.get("tool_invoked") is False or last.get("turn_use_case") == "text"
    logger.info({
        "event": "image_stream_resolve_start",
        "conversation_id": conversation_id,
        "file_ids": file_ids,
        "sediment_ids": sediment_ids,
        "tool_invoked": last.get("tool_invoked"),
        "turn_use_case": last.get("turn_use_case"),
    })
    if message and not file_ids and not sediment_ids and (last.get("blocked") or is_text_response):
        yield ImageOutput(kind="message", model=request.model, index=index, total=total, text=message)
        return

    image_urls = backend.resolve_conversation_image_urls(conversation_id, file_ids, sediment_ids)
    if image_urls:
        image_items = [
            {"b64_json": base64.b64encode(image_data).decode("ascii")}
            for image_data in backend.download_image_bytes(image_urls)
        ]
        data = format_image_result(
            image_items,
            request.prompt,
            request.response_format,
            request.base_url,
            int(time.time()),
            save_public_images=request.save_public_images,
        )["data"]
        if data:
            yield ImageOutput(kind="result", model=request.model, index=index, total=total, data=data)
        return

    if message:
        yield ImageOutput(kind="message", model=request.model, index=index, total=total, text=message)


def stream_image_outputs_with_pool(request: ConversationRequest) -> Iterator[ImageOutput]:
    if str(request.model or "").strip() not in IMAGE_MODELS:
        raise ImageGenerationError("unsupported image model,supported models: " + ", ".join(IMAGE_MODELS))
    try:
        request.size = validate_image_size(request.size) or None
    except ValueError as exc:
        raise ImageGenerationError(
            str(exc),
            status_code=400,
            error_type="invalid_request_error",
            code="invalid_image_size",
            param="size",
        ) from exc

    emitted = False
    last_error = ""
    for index in range(1, request.n + 1):
        attempted_tokens: set[str] = set()
        while True:
            try:
                token = account_service.get_available_access_token(excluded_tokens=attempted_tokens)
            except RuntimeError as exc:
                if emitted:
                    return
                raise ImageGenerationError(str(exc) or "image generation failed") from exc
            if token in attempted_tokens:
                if emitted:
                    return
                raise ImageGenerationError(last_error or "image generation failed")
            attempted_tokens.add(token)

            emitted_for_token = False
            returned_message = False
            returned_result = False
            try:
                backend = OpenAIBackendAPI(access_token=token)
                for output in stream_image_outputs(backend, request, index, request.n):
                    if output.kind == "message" and request.message_as_error:
                        raise ImageGenerationError(
                            output.text or "Image generation was rejected by upstream policy.",
                            status_code=400,
                            error_type="invalid_request_error",
                            code="content_policy_violation",
                        )
                    emitted = True
                    emitted_for_token = True
                    returned_message = output.kind == "message"
                    returned_result = returned_result or output.kind == "result"
                    yield output
                if returned_message or not returned_result:
                    account_service.mark_image_result(token, False)
                    return
                account_service.mark_image_result(token, True)
                break
            except ImageGenerationError:
                account_service.mark_image_result(token, False)
                raise
            except Exception as exc:
                account_service.mark_image_result(token, False)
                last_error = str(exc)
                logger.warning({
                    "event": "image_stream_fail",
                    "token_ref": anonymize_token(token),
                    "error": redact_sensitive_text(last_error, [token]),
                })
                if is_token_invalid_error(last_error):
                    account_service.mark_invalid_token(token, "image_stream")
                    continue
                if is_rate_limited_error(last_error):
                    account_service.mark_rate_limited_token(token, "image_stream")
                    continue
                raise ImageGenerationError(redact_sensitive_text(last_error, [token]) or "image generation failed") from exc

    if not emitted:
        raise ImageGenerationError(last_error or "image generation failed")


def stream_image_chunks(outputs: Iterable[ImageOutput]) -> Iterator[dict[str, Any]]:
    for output in outputs:
        yield output.to_chunk()


def collect_image_outputs(outputs: Iterable[ImageOutput]) -> dict[str, Any]:
    created = None
    data: list[dict[str, Any]] = []
    message = ""
    progress_parts: list[str] = []
    for output in outputs:
        created = created or output.created
        if output.kind == "progress" and output.text:
            progress_parts.append(output.text)
        elif output.kind == "message":
            message = output.text
        elif output.kind == "result":
            data.extend(output.data)

    result: dict[str, Any] = {"created": created or int(time.time()), "data": data}
    if not data:
        text = message or "".join(progress_parts).strip()
        if text:
            result["message"] = text
    return result

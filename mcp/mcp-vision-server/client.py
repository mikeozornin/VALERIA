"""Qwen chat completions client."""
import asyncio
from typing import Any

import httpx
from pydantic import BaseModel

from config import Settings
from images import VisionError


class VisionResult(BaseModel):
    answer: str
    model: str
    usage: dict[str, Any] | None = None
    finish_reason: str | None = None
    truncated: bool = False


async def ask_model(settings: Settings, client: httpx.AsyncClient,
                    prompt: str, data_url: str) -> VisionResult:
    payload = {
        "model": settings.model,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]}],
        "stream": False,
        "max_tokens": settings.max_tokens,
        "chat_template_kwargs": {"enable_thinking": settings.enable_thinking},
    }
    # The local gateway forwards compressed bytes but drops Content-Encoding.
    # Request uncompressed JSON so it also counts usage correctly.
    headers = {"Accept-Encoding": "identity"}
    if settings.api_key:
        headers["Authorization"] = f"Bearer {settings.api_key}"
    try:
        # httpx read timeout is an inactivity timeout; asyncio bounds the entire call.
        async with asyncio.timeout(settings.timeout_seconds):
            response = await client.post(
                f"{settings.base_url}/chat/completions", json=payload, headers=headers,
                timeout=httpx.Timeout(settings.timeout_seconds, connect=min(30, settings.timeout_seconds)),
            )
            response.raise_for_status()
    except (TimeoutError, httpx.TimeoutException):
        raise VisionError(f"Модель не ответила за {settings.timeout_seconds:g} секунд.") from None
    except httpx.HTTPStatusError as exc:
        # Do not expose upstream bodies: they can contain echoed prompts or credentials.
        status = exc.response.status_code
        detail = {
            400: "API отклонил параметры запроса или изображение",
            401: "проверьте VISION_API_KEY", 403: "нет доступа к модели",
            404: "проверьте VISION_BASE_URL и VISION_MODEL",
            413: "API отклонил размер изображения", 429: "превышен лимит запросов API",
        }.get(status, "ошибка сервиса модели")
        raise VisionError(f"API вернул HTTP {status}: {detail}.") from None
    except httpx.RequestError:
        raise VisionError("Не удалось связаться с API модели; проверьте адрес и доступность сервиса.") from None
    try:
        body = response.json()
        choice = body["choices"][0]
        answer = choice["message"]["content"]
        reason = choice.get("finish_reason")
        if not isinstance(answer, str) or not answer.strip():
            suffix = " Увеличьте VISION_MAX_TOKENS." if reason == "length" else ""
            raise VisionError("Модель вернула пустой текстовый ответ." + suffix)
        return VisionResult(
            answer=answer, model=body.get("model") or settings.model,
            usage=body.get("usage"), finish_reason=reason, truncated=reason == "length",
        )
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        raise VisionError("API вернул некорректный формат ответа.") from None

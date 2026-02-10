"""Local stdio MCP server for image questions and OCR using Qwen."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
import logging
import sys

import httpx
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from client import VisionResult, ask_model
from config import Settings
from images import VisionError, image_data_url


@dataclass
class Runtime:
    settings: Settings
    client: httpx.AsyncClient


@asynccontextmanager
async def lifespan(server: FastMCP):
    settings = Settings.from_env()
    async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as client:
        yield Runtime(settings, client)


mcp = FastMCP("vision", lifespan=lifespan, log_level="WARNING")


@mcp.tool()
async def analyze_image(
    prompt: str,
    ctx: Context,
    image_path: str | None = None,
    image_base64: str | None = None,
    mime_type: str | None = None,
) -> VisionResult:
    """Распознать изображение, прочитать текст (OCR) или ответить на вопрос через Qwen.

    prompt: вопрос или инструкция, например «Перепиши весь видимый текст дословно».
    Передайте ровно один источник: image_path — абсолютный путь к доступному серверу
    файлу (предпочтительно), либо image_base64 — готовый base64 или data URL.
    Не генерируйте base64 самостоятельно из описания картинки. Вложение чата
    автоматически не передаётся: нужен доступный файл или его настоящие байты.
    Поддерживаются статичные PNG/JPEG/WebP. mime_type необязателен: формат определяется
    по содержимому. Возвращает answer, model, usage, finish_reason и truncated.
    Для буквальной транскрипции явно попросите не добавлять пояснения.
    """
    if not prompt.strip():
        raise ToolError("prompt не может быть пустым.")
    runtime: Runtime = ctx.request_context.lifespan_context
    try:
        data_url = await asyncio.to_thread(
            image_data_url, runtime.settings, image_path, image_base64, mime_type,
        )
        return await ask_model(runtime.settings, runtime.client, prompt, data_url)
    except VisionError as exc:
        raise ToolError(str(exc)) from None


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    mcp.run(transport="stdio")

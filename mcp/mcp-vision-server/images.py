"""Bounded local-file/base64 image input. Images are never downloaded or persisted."""
import base64
import binascii
from io import BytesIO
from pathlib import Path
import warnings

from PIL import Image, UnidentifiedImageError

from config import Settings


class VisionError(Exception):
    """A safe, user-facing error without input contents or credentials."""


MIME_TYPES = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}


def image_data_url(
    settings: Settings,
    image_path: str | None = None,
    image_base64: str | None = None,
    mime_type: str | None = None,
) -> str:
    if (image_path is None) == (image_base64 is None):
        raise VisionError("Укажите ровно один источник: image_path или image_base64.")
    if image_path is not None:
        if not image_path or not Path(image_path).expanduser().is_absolute():
            raise VisionError("image_path должен быть абсолютным путём к файлу.")
        try:
            path = Path(image_path).expanduser().resolve(strict=True)
            if not any(path.is_relative_to(root) for root in settings.allowed_roots):
                raise VisionError("Файл находится вне каталогов VISION_ALLOWED_ROOTS.")
            if not path.is_file():
                raise VisionError("image_path должен указывать на обычный файл.")
            with path.open("rb") as source:
                raw = source.read(settings.max_image_bytes + 1)
        except (OSError, RuntimeError, ValueError):
            raise VisionError("Не удалось прочитать изображение: проверьте путь и права доступа.") from None
    else:
        encoded = image_base64 or ""
        # Check the encoded length before decoding to avoid a large allocation.
        if len(encoded) > 4 * ((settings.max_image_bytes + 2) // 3) + 64:
            raise VisionError("Изображение превышает VISION_MAX_IMAGE_MB.")
        if encoded.startswith("data:"):
            header, separator, encoded = encoded.partition(",")
            if not separator or not header.endswith(";base64"):
                raise VisionError("Ожидается data URL с кодированием base64.")
            declared_mime = header[5:-7]
            if mime_type and mime_type != declared_mime:
                raise VisionError("mime_type не совпадает с MIME-типом data URL.")
            mime_type = declared_mime
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise VisionError("Некорректное содержимое image_base64.") from None
    if not raw:
        raise VisionError("Изображение пустое.")
    if len(raw) > settings.max_image_bytes:
        raise VisionError("Изображение превышает VISION_MAX_IMAGE_MB.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw)) as img:
                actual_mime = MIME_TYPES.get(img.format)
                if actual_mime is None:
                    raise VisionError("Поддерживаются только PNG, JPEG и WebP.")
                if img.width * img.height > settings.max_image_pixels:
                    raise VisionError("Изображение превышает VISION_MAX_IMAGE_PIXELS.")
                if getattr(img, "n_frames", 1) != 1:
                    raise VisionError("Передайте статичное изображение без анимации.")
                img.verify()
            # verify() alone does not decode JPEG pixels or catch every truncated file.
            with Image.open(BytesIO(raw)) as img:
                img.load()
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError,
            Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise VisionError("Повреждённое изображение или недопустимый размер изображения.") from None
    if mime_type is not None and mime_type != actual_mime:
        raise VisionError("mime_type не соответствует фактическому формату изображения.")
    return f"data:{actual_mime};base64,{base64.b64encode(raw).decode('ascii')}"

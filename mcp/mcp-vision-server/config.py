"""Configuration from the process environment; no credentials are stored on disk."""
from dataclasses import dataclass
import math
import os
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    base_url: str = "http://localhost:8080/v1"
    model: str = "Qwen/Qwen35-122B-A10B-FP8"
    timeout_seconds: float = 6000
    max_image_bytes: int = 20 * 1024 * 1024
    max_image_pixels: int = 40_000_000
    max_tokens: int = 4096
    allowed_roots: tuple[Path, ...] = (Path.home(),)
    api_key: str = ""
    enable_thinking: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        timeout = float(os.environ.get("VISION_TIMEOUT_SECONDS", "6000"))
        max_mb = int(os.environ.get("VISION_MAX_IMAGE_MB", "20"))
        pixels = int(os.environ.get("VISION_MAX_IMAGE_PIXELS", "40000000"))
        tokens = int(os.environ.get("VISION_MAX_TOKENS", "4096"))
        if not math.isfinite(timeout) or min(timeout, max_mb, pixels, tokens) <= 0:
            raise ValueError("VISION: лимиты и таймаут должны быть положительными числами.")
        roots = tuple(
            Path(value).expanduser().resolve()
            for value in os.environ.get("VISION_ALLOWED_ROOTS", str(Path.home())).split(os.pathsep)
            if value.strip()
        )
        if not roots:
            raise ValueError("VISION_ALLOWED_ROOTS должен содержать хотя бы один каталог.")
        base_url = os.environ.get("VISION_BASE_URL", cls.base_url).rstrip("/")
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("VISION_BASE_URL должен быть HTTP(S)-адресом API.")
        thinking = os.environ.get("VISION_ENABLE_THINKING", "false").lower()
        if thinking not in ("true", "false"):
            raise ValueError("VISION_ENABLE_THINKING должен быть true или false.")
        model = os.environ.get("VISION_MODEL", cls.model).strip()
        if not model:
            raise ValueError("VISION_MODEL не может быть пустым.")
        return cls(
            base_url=base_url, model=model, timeout_seconds=timeout,
            max_image_bytes=max_mb * 1024 * 1024, max_image_pixels=pixels,
            max_tokens=tokens, allowed_roots=roots,
            api_key=os.environ.get("VISION_API_KEY", ""),
            enable_thinking=thinking == "true",
        )

import asyncio
import base64
from dataclasses import replace
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
from PIL import Image

from client import ask_model
from config import Settings
from images import VisionError, image_data_url


def picture(fmt="PNG"):
    out = BytesIO()
    Image.new("RGB", (32, 24), "red").save(out, format=fmt)
    return out.getvalue()


class ImageTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings()

    def test_supported_formats_and_data_url(self):
        for fmt, mime in (("PNG", "image/png"), ("JPEG", "image/jpeg"), ("WEBP", "image/webp")):
            with self.subTest(fmt=fmt):
                encoded = base64.b64encode(picture(fmt)).decode()
                url = image_data_url(self.settings, image_base64=encoded)
                self.assertEqual(url, f"data:{mime};base64,{encoded}")
                self.assertEqual(image_data_url(self.settings, image_base64=url), url)

    def test_file_access_and_symlink_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            allowed = root / "allowed"
            allowed.mkdir()
            path = allowed / "image.png"
            path.write_bytes(picture())
            settings = replace(self.settings, allowed_roots=(allowed,))
            self.assertTrue(image_data_url(settings, image_path=str(path)).startswith("data:image/png;"))
            outside = root / "outside.png"
            outside.write_bytes(picture())
            link = allowed / "link.png"
            link.symlink_to(outside)
            for forbidden in (outside, link):
                with self.assertRaisesRegex(VisionError, "вне каталогов"):
                    image_data_url(settings, image_path=str(forbidden))
            with self.assertRaisesRegex(VisionError, "прочитать"):
                image_data_url(settings, image_path=str(allowed / "missing.png"))

    def test_invalid_input_and_mime(self):
        for args in ({}, {"image_path": "/a", "image_base64": "a"},
                     {"image_path": "relative.png"}, {"image_base64": "%%%"},
                     {"image_base64": ""}, {"image_base64": "data:image/png,hello"},
                     {"image_base64": base64.b64encode(b"not a picture").decode()},
                     {"image_base64": base64.b64encode(picture()).decode(), "mime_type": "image/jpeg"}):
            with self.subTest(args=list(args)), self.assertRaises(VisionError):
                image_data_url(self.settings, **args)

    def test_size_pixels_corruption_and_unsupported_format(self):
        for settings, raw in (
            (replace(self.settings, max_image_bytes=10), picture()),
            (replace(self.settings, max_image_pixels=100), picture()),
            (self.settings, picture()[:-15]),
            (self.settings, picture("GIF")),
        ):
            with self.subTest(bytes=len(raw)), self.assertRaises(VisionError):
                image_data_url(settings, image_base64=base64.b64encode(raw).decode())

    def test_environment_defaults_and_overrides(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(Settings.from_env().timeout_seconds, 6000)
        with patch.dict("os.environ", {"VISION_TIMEOUT_SECONDS": "6000", "VISION_MAX_TOKENS": "512"}, clear=True):
            settings = Settings.from_env()
            self.assertEqual(settings.timeout_seconds, 6000)
            self.assertEqual(settings.max_tokens, 512)
        for invalid in ("0", "-1", "nan", "inf"):
            with patch.dict("os.environ", {"VISION_TIMEOUT_SECONDS": invalid}, clear=True):
                with self.assertRaises(ValueError):
                    Settings.from_env()


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def invoke(self, handler, settings=None):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await ask_model(settings or Settings(), client, "Что изображено?", "data:image/png;base64,AAAA")

    async def test_request_and_response(self):
        def handler(request):
            self.assertEqual(str(request.url), "http://localhost:8080/v1/chat/completions")
            self.assertEqual(request.headers["Authorization"], "Bearer test-secret")
            self.assertEqual(request.headers["Accept-Encoding"], "identity")
            body = json.loads(request.content)
            self.assertEqual(body["model"], "Qwen/Qwen35-122B-A10B-FP8")
            self.assertEqual(body["messages"][0]["content"][1]["image_url"]["url"], "data:image/png;base64,AAAA")
            self.assertEqual(body["messages"][0]["content"][0]["text"], "Что изображено?")
            self.assertEqual(body["chat_template_kwargs"], {"enable_thinking": False})
            self.assertEqual(request.extensions["timeout"]["read"], 6000)
            return httpx.Response(200, json={"choices": [{"message": {"content": "Красный круг"}, "finish_reason": "length"}], "usage": {"total_tokens": 7}})
        result = await self.invoke(handler, replace(Settings(), api_key="test-secret"))
        self.assertEqual(result.answer, "Красный круг")
        self.assertTrue(result.truncated)
        self.assertEqual(result.usage, {"total_tokens": 7})

    async def test_upstream_errors_do_not_leak_body(self):
        for status in (400, 401, 403, 404, 413, 429, 500, 502):
            with self.subTest(status=status), self.assertRaises(VisionError) as caught:
                await self.invoke(lambda request: httpx.Response(status, text="secret prompt and key"))
            self.assertIn(str(status), str(caught.exception))
            self.assertNotIn("secret", str(caught.exception))

    async def test_invalid_and_empty_responses(self):
        for body in ({}, {"choices": []}, {"choices": [{"message": {"content": None}}]},
                     {"choices": [{"message": {"content": " "}, "finish_reason": "length"}]}):
            with self.subTest(body=body), self.assertRaises(VisionError):
                await self.invoke(lambda request: httpx.Response(200, json=body))
        with self.assertRaisesRegex(VisionError, "формат"):
            await self.invoke(lambda request: httpx.Response(200, text="not JSON"))

    async def test_connection_and_read_timeout(self):
        for error, expected in ((httpx.ConnectError, "связаться"), (httpx.ReadTimeout, "6000")):
            def handler(request):
                raise error("secret upstream detail", request=request)
            with self.assertRaisesRegex(VisionError, expected):
                await self.invoke(handler)

    async def test_total_deadline_and_cancellation(self):
        async def slow_handler(request):
            await asyncio.sleep(10)
            return httpx.Response(200)
        with self.assertRaisesRegex(VisionError, "не ответила"):
            await self.invoke(slow_handler, replace(Settings(), timeout_seconds=0.02))
        task = asyncio.create_task(self.invoke(slow_handler))
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task


if __name__ == "__main__":
    unittest.main()

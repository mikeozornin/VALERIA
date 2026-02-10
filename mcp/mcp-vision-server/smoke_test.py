"""Exercise the real stdio MCP protocol; --live also sends a fixture to Qwen."""
import argparse
import asyncio
from datetime import timedelta
import json
import os
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parent


def make_fixture() -> Path:
    target = ROOT / "data" / "smoke.png"
    target.parent.mkdir(exist_ok=True)
    img = Image.new("RGB", (640, 240), "white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype("DejaVuSans.ttf", 48)
    draw.text((24, 16), "VISION 4827", fill="black", font=font)
    draw.ellipse((40, 100, 145, 205), fill="red")
    draw.rectangle((220, 100, 325, 205), fill="blue")
    img.save(target)
    return target


async def main(live: bool):
    params = StdioServerParameters(
        command=sys.executable, args=[str(ROOT / "server.py")], cwd=str(ROOT),
        env=dict(os.environ),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=6060)) as session:
            await session.initialize()
            tools = await session.list_tools()
            assert [tool.name for tool in tools.tools] == ["analyze_image"]
            assert "ctx" not in tools.tools[0].inputSchema["properties"]
            assert tools.tools[0].outputSchema
            for args in ({"prompt": "Что на картинке?"},
                         {"prompt": " ", "image_base64": "bad"},
                         {"prompt": "Что на картинке?", "image_path": str(ROOT / "data" / "missing.png")}):
                result = await session.call_tool("analyze_image", args)
                assert result.isError, result
            print("PASS: MCP initialize, tools/list, schema, validation errors", flush=True)
            if live:
                result = await session.call_tool("analyze_image", {
                    "prompt": "Перепиши текст вверху изображения дословно и кратко назови две цветные фигуры. Ответь по-русски.",
                    "image_path": str(make_fixture()),
                })
                assert not result.isError, result
                body = result.structuredContent
                assert body and "4827" in body["answer"], result
                assert not body["truncated"], result
                print(json.dumps(body, ensure_ascii=False, indent=2), flush=True)
                print("PASS: live Qwen image + prompt through stdio MCP", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="Send a generated test image to the configured API")
    asyncio.run(main(parser.parse_args().live))

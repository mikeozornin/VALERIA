from __future__ import annotations

import argparse
import os

import uvicorn
from dotenv import load_dotenv

from .config import Settings


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="OpenCode Web server")
    parser.add_argument("--host", help="Override APP_HOST")
    parser.add_argument("--port", type=int, help="Override APP_PORT")
    args = parser.parse_args()
    if args.host:
        os.environ["APP_HOST"] = args.host
    if args.port:
        os.environ["APP_PORT"] = str(args.port)
    settings = Settings.from_env()
    uvicorn.run(
        "opencode_web.app:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        # SSE connections otherwise prevent lifespan cleanup from being reached.
        timeout_graceful_shutdown=10,
    )


if __name__ == "__main__":
    main()

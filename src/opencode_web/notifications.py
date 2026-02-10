from __future__ import annotations

import asyncio
import logging
import ssl
from contextlib import suppress
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from .config import Settings
from .database import Database

logger = logging.getLogger(__name__)


class MattermostNotificationError(RuntimeError):
    def __init__(
        self, code: str, *, permanent: bool = False, retry_after_seconds: float = 0
    ) -> None:
        super().__init__(code)
        self.code = code
        self.permanent = permanent
        self.retry_after_seconds = retry_after_seconds


class MattermostNotifier:
    def __init__(self, settings: Settings):
        token_file = settings.mattermost_token_file
        if not token_file:
            raise ValueError("MATTERMOST_TOKEN_FILE is not configured")
        token = self._read_token(token_file)
        verify: ssl.SSLContext | bool = True
        if settings.mattermost_ca_bundle:
            verify = ssl.create_default_context(cafile=str(settings.mattermost_ca_bundle))
        self.timeout = settings.mattermost_timeout_seconds
        self.client = httpx.AsyncClient(
            base_url=settings.mattermost_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(self.timeout, connect=min(15, self.timeout)),
            follow_redirects=False,
            trust_env=False,
            verify=verify,
        )
        self._bot_id: str | None = None

    @staticmethod
    def _read_token(path: Path) -> str:
        try:
            token = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ValueError("Mattermost token file cannot be read") from exc
        if not token:
            raise ValueError("Mattermost token file is empty")
        return token

    async def close(self) -> None:
        await self.client.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = await self.client.request(method, path, **kwargs)
        except (httpx.RequestError, TimeoutError) as exc:
            raise MattermostNotificationError("network_error") from exc
        if response.status_code >= 400:
            retry_after = 0.0
            if response.status_code == 429:
                with suppress(ValueError):
                    retry_after = float(response.headers.get("Retry-After", "0"))
            permanent = response.status_code in {400, 401, 403, 404}
            raise MattermostNotificationError(
                f"http_{response.status_code}",
                permanent=permanent,
                retry_after_seconds=retry_after,
            )
        try:
            return response.json()
        except ValueError as exc:
            raise MattermostNotificationError("invalid_response") from exc

    async def bot_id(self) -> str:
        if self._bot_id:
            return self._bot_id
        data = await self._request("GET", "/api/v4/users/me")
        bot_id = data.get("id")
        if not isinstance(bot_id, str) or not bot_id:
            raise MattermostNotificationError("missing_bot_id", permanent=True)
        self._bot_id = bot_id
        return bot_id

    async def user_by_username(self, username: str) -> dict[str, str]:
        data = await self._request(
            "GET", f"/api/v4/users/username/{quote(username, safe='')}"
        )
        user_id = data.get("id")
        canonical_username = data.get("username")
        if not isinstance(user_id, str) or not user_id:
            raise MattermostNotificationError("missing_user_id", permanent=True)
        if not isinstance(canonical_username, str) or not canonical_username:
            raise MattermostNotificationError("missing_username", permanent=True)
        return {"id": user_id, "username": canonical_username}

    async def send(self, notification: dict[str, Any], base_url: str) -> str:
        channel = await self._request(
            "POST",
            "/api/v4/channels/direct",
            json=[await self.bot_id(), notification["mattermost_user_id"]],
        )
        channel_id = channel.get("id")
        if not isinstance(channel_id, str) or not channel_id:
            raise MattermostNotificationError("missing_channel_id", permanent=True)
        if notification["kind"] == "task_completed":
            heading = f"✅ Таска «{notification['title']}» завершена."
            label = "Открыть результат в Valeria"
        else:
            heading = f"💬 Ответ на консультацию «{notification['title']}» готов."
            label = "Открыть консультацию в Valeria"
        link = base_url.rstrip("/") + notification["link_path"]
        post = await self._request(
            "POST",
            "/api/v4/posts",
            json={
                "channel_id": channel_id,
                "message": f"{heading}\n[{label}]({link})",
                # Mattermost clients use this field to identify retries of one post.
                "pending_post_id": f"valeria-notification:{notification['id']}",
                "props": {"valeria_notification_id": notification["id"]},
            },
        )
        post_id = post.get("id")
        if not isinstance(post_id, str) or not post_id:
            raise MattermostNotificationError("missing_post_id")
        return post_id


async def resolve_mattermost_username(settings: Settings, username: str) -> dict[str, str]:
    notifier = MattermostNotifier(settings)
    try:
        return await notifier.user_by_username(username)
    finally:
        await notifier.close()


class NotificationDispatcher:
    def __init__(self, database: Database, settings: Settings):
        self.database = database
        self.settings = settings
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._stopping = False
        self._notifier: MattermostNotifier | None = None

    @property
    def enabled(self) -> bool:
        return self.settings.mattermost_token_file is not None

    def start(self) -> None:
        if not self.enabled or self._task:
            return
        self._task = asyncio.create_task(self._run(), name="notification-dispatcher")

    def wake(self) -> None:
        self._wake.set()

    async def stop(self) -> None:
        self._stopping = True
        self._wake.set()
        if self._task:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
        if self._notifier:
            await self._notifier.close()

    async def _run(self) -> None:
        try:
            self._notifier = MattermostNotifier(self.settings)
        except (OSError, ValueError):
            logger.exception("Mattermost notifications are disabled by invalid configuration")
            return
        while not self._stopping:
            notification = self.database.claim_notification()
            if not notification:
                self._wake.clear()
                with suppress(TimeoutError):
                    await asyncio.wait_for(self._wake.wait(), timeout=2)
                continue
            try:
                post_id = await self._notifier.send(notification, self.settings.base_url)
            except MattermostNotificationError as exc:
                self.database.mark_notification_failed(
                    notification["id"],
                    exc.code,
                    permanent=exc.permanent,
                    retry_after_seconds=exc.retry_after_seconds,
                    max_attempts=self.settings.mattermost_notification_max_attempts,
                )
                logger.warning(
                    "Mattermost notification delivery failed",
                    extra={"notification_id": notification["id"], "error_code": exc.code},
                )
            except Exception:
                self.database.mark_notification_failed(
                    notification["id"],
                    "internal_error",
                    permanent=False,
                    max_attempts=self.settings.mattermost_notification_max_attempts,
                )
                logger.exception(
                    "Unexpected Mattermost notification error",
                    extra={"notification_id": notification["id"]},
                )
            else:
                self.database.mark_notification_sent(notification["id"], post_id)

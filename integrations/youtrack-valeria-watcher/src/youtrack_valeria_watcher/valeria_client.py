from __future__ import annotations

from types import TracebackType
from typing import Any

import httpx

from .config import Settings
from .errors import ExternalServiceError
from .models import TaskRun

VALID_STATUSES = frozenset(
    {"queued", "dispatching", "running", "cancelling", "completed", "failed", "cancelled"}
)


class ValeriaClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        verify: bool | str = str(settings.ca_bundle) if settings.ca_bundle else True
        self.client = httpx.AsyncClient(
            base_url=settings.valeria_base_url,
            timeout=settings.request_timeout_seconds,
            verify=verify,
            follow_redirects=False,
        )
        self.csrf_token: str | None = None

    async def __aenter__(self) -> ValeriaClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.client.aclose()

    async def _login(self) -> None:
        try:
            response = await self.client.post(
                "/api/auth/login",
                json={
                    "login": self.settings.valeria_login,
                    "password": self.settings.valeria_password,
                },
            )
        except httpx.HTTPError as exc:
            raise ExternalServiceError("Cannot connect to Valeria for login") from exc
        if response.status_code != 200:
            raise ExternalServiceError(f"Valeria login failed with HTTP {response.status_code}")
        try:
            token = response.json()["csrf_token"]
        except (ValueError, KeyError, TypeError) as exc:
            raise ExternalServiceError("Valeria login returned an invalid response") from exc
        if not isinstance(token, str) or not token:
            raise ExternalServiceError("Valeria login returned an invalid CSRF token")
        self.csrf_token = token

    async def _request(
        self, method: str, path: str, *, headers: dict[str, str] | None = None, **kwargs: Any
    ) -> httpx.Response:
        if self.csrf_token is None:
            await self._login()
        combined = {"X-CSRF-Token": self.csrf_token or ""}
        combined.update(headers or {})
        try:
            response = await self.client.request(method, path, headers=combined, **kwargs)
        except httpx.HTTPError as exc:
            raise ExternalServiceError("Cannot connect to Valeria") from exc
        if response.status_code == 401:
            self.csrf_token = None
            await self._login()
            combined["X-CSRF-Token"] = self.csrf_token or ""
            try:
                response = await self.client.request(method, path, headers=combined, **kwargs)
            except httpx.HTTPError as exc:
                raise ExternalServiceError("Cannot reconnect to Valeria") from exc
        return response

    async def submit(self, issue_url: str, comment_id: str) -> TaskRun:
        response = await self._request(
            "POST",
            f"/api/tasks/{self.settings.valeria_task_id}/runs",
            headers={"Idempotency-Key": f"youtrack-comment:{comment_id}"},
            json={"user_input": issue_url},
        )
        if response.status_code != 202:
            raise ExternalServiceError(
                f"Valeria rejected task submission with HTTP {response.status_code}"
            )
        return _parse_run(response)

    async def get_run(self, run_id: str) -> TaskRun:
        response = await self._request("GET", f"/api/task-runs/{run_id}")
        if response.status_code != 200:
            raise ExternalServiceError(f"Valeria run status failed with HTTP {response.status_code}")
        return _parse_run(response)


def _parse_run(response: httpx.Response) -> TaskRun:
    try:
        payload = response.json()
        run_id = payload["id"]
        status = payload["status"]
    except (ValueError, KeyError, TypeError) as exc:
        raise ExternalServiceError("Valeria returned an invalid task run") from exc
    if not isinstance(run_id, str) or not run_id or status not in VALID_STATUSES:
        raise ExternalServiceError("Valeria returned an invalid task run")
    return TaskRun(run_id=run_id, status=status)

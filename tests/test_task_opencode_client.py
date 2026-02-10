from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest

from opencode_web.opencode_client import (
    OpenCodeClient,
    OpenCodeError,
    OpenCodeInstance,
    OpenCodeManager,
    OpenCodeReadTimeout,
)


async def test_client_allows_two_hours_for_response_body() -> None:
    client = OpenCodeClient("http://opencode.test", "", "")
    try:
        assert client._client.timeout.connect == 5
        assert client._client.timeout.read == 2 * 60 * 60
        assert client._client.timeout.write == 30
        assert client._client.timeout.pool == 30
    finally:
        await client.close()


async def test_read_timeout_has_a_distinct_error_type() -> None:
    def transport(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow OpenCode response", request=request)

    client = OpenCodeClient("http://opencode.test", "", "")
    await client._client.aclose()
    client._client = httpx.AsyncClient(
        base_url="http://opencode.test",
        transport=httpx.MockTransport(transport),
    )
    try:
        with pytest.raises(OpenCodeReadTimeout, match="GET /session/status"):
            await client.session_statuses()
    finally:
        await client.close()


async def test_startup_probe_cannot_outlive_manager_deadline(settings, workspace) -> None:
    manager = OpenCodeManager(replace(settings, opencode_start_timeout=0.05))

    async def stalled_health() -> dict[str, object]:
        await asyncio.Event().wait()
        return {}

    client = SimpleNamespace(health=stalled_health)
    process = SimpleNamespace(returncode=None)
    instance = OpenCodeInstance(workspace, 4100, process, client)

    with pytest.raises(OpenCodeError, match="Timed out while starting OpenCode"):
        async with asyncio.timeout(0.5):
            await manager._start_instance(instance)


async def test_question_api_uses_original_labels_and_array_per_question() -> None:
    requests: list[httpx.Request] = []

    def transport(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=[{"id": "q-1", "sessionID": "s-1"}])
        return httpx.Response(200, json=True)

    client = OpenCodeClient("http://opencode.test", "", "")
    await client._client.aclose()
    client._client = httpx.AsyncClient(
        base_url="http://opencode.test",
        transport=httpx.MockTransport(transport),
    )
    try:
        assert await client.questions() == [{"id": "q-1", "sessionID": "s-1"}]
        assert await client.respond_question("q-1", [["A (Recommended)"], ["B", "C"]])
    finally:
        await client.close()

    assert [(item.method, item.url.path) for item in requests] == [
        ("GET", "/question"),
        ("POST", "/question/q-1/reply"),
    ]
    assert requests[1].content == b'{"answers":[["A (Recommended)"],["B","C"]]}'


async def test_invalid_question_payload_is_not_treated_as_an_empty_queue() -> None:
    client = OpenCodeClient("http://opencode.test", "", "")
    await client._client.aclose()
    client._client = httpx.AsyncClient(
        base_url="http://opencode.test",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})),
    )
    try:
        with pytest.raises(OpenCodeError, match="invalid question requests"):
            await client.questions()
    finally:
        await client.close()

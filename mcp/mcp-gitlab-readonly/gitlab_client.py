"""Small HTTPS client for the GitLab REST API."""

from __future__ import annotations

import base64
import binascii
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote, urljoin, urlsplit

import httpx

DEFAULT_GITLAB_URL = "https://gitlab.com"
DEFAULT_CLONE_ROOT = Path.home() / "gitlab-readonly"
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_CLONE_TIMEOUT_SECONDS = 600
DEFAULT_MAX_FILE_BYTES = 1_000_000
DEFAULT_MAX_TRACE_CHARS = 200_000
DEFAULT_TRACE_OUTPUT_DIRNAME = "gitlab-traces"
DEFAULT_MAX_ARTIFACT_BYTES = 100_000_000
MAX_ARTIFACT_REDIRECTS = 3


class GitLabError(RuntimeError):
    """Safe error returned to MCP clients without exposing credentials."""


GitLabReadOnlyError = GitLabError


def _artifact_redirect_hosts(value: str) -> tuple[str, ...]:
    result: list[str] = []
    for raw_host in value.split(","):
        host = raw_host.strip().lower().rstrip(".")
        if not host:
            continue
        parsed = urlsplit(f"//{host}")
        if (
            parsed.hostname != host
            or parsed.username
            or parsed.password
            or parsed.port is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
            or any(char.isspace() for char in host)
        ):
            raise GitLabReadOnlyError(
                "GITLAB_ARTIFACT_REDIRECT_HOSTS must contain comma-separated hostnames"
            )
        if host not in result:
            result.append(host)
    return tuple(result)


@dataclass(frozen=True)
class GitLabSettings:
    base_url: str
    token: str
    clone_root: Path
    timeout_seconds: float
    clone_timeout_seconds: int
    max_file_bytes: int
    max_trace_chars: int
    ca_bundle: str | None
    trace_output_dir: Path | None = None
    trust_env_proxy: bool = False
    max_artifact_bytes: int = DEFAULT_MAX_ARTIFACT_BYTES
    artifact_redirect_hosts: tuple[str, ...] = ()

    @classmethod
    def from_env(cls, require_token: bool = True) -> GitLabSettings:
        base_url = os.getenv("GITLAB_URL", DEFAULT_GITLAB_URL).rstrip("/")
        parsed = urlsplit(base_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise GitLabReadOnlyError("GITLAB_URL must be a credential-free HTTPS URL")

        token = (os.getenv("GITLAB_TOKEN") or os.getenv("GITLAB_READ_TOKEN", "")).strip()
        if require_token and not token:
            raise GitLabReadOnlyError(
                "GITLAB_TOKEN is not set (GITLAB_READ_TOKEN is accepted for compatibility)."
            )

        clone_root = Path(os.getenv("GITLAB_CLONE_ROOT", str(DEFAULT_CLONE_ROOT))).expanduser().resolve()
        return cls(
            base_url=base_url,
            token=token,
            clone_root=clone_root,
            timeout_seconds=float(os.getenv("GITLAB_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS))),
            clone_timeout_seconds=int(
                os.getenv("GITLAB_CLONE_TIMEOUT_SECONDS", str(DEFAULT_CLONE_TIMEOUT_SECONDS))
            ),
            max_file_bytes=int(os.getenv("GITLAB_MAX_FILE_BYTES", str(DEFAULT_MAX_FILE_BYTES))),
            max_trace_chars=int(os.getenv("GITLAB_MAX_TRACE_CHARS", str(DEFAULT_MAX_TRACE_CHARS))),
            ca_bundle=os.getenv("GITLAB_CA_BUNDLE") or None,
            trace_output_dir=Path(
                os.getenv("GITLAB_TRACE_OUTPUT_DIR", str(clone_root / DEFAULT_TRACE_OUTPUT_DIRNAME))
            ).expanduser().resolve(),
            trust_env_proxy=os.getenv("GITLAB_TRUST_ENV_PROXY", "false").lower()
            in ("1", "true", "yes"),
            max_artifact_bytes=int(
                os.getenv("GITLAB_MAX_ARTIFACT_BYTES", str(DEFAULT_MAX_ARTIFACT_BYTES))
            ),
            artifact_redirect_hosts=_artifact_redirect_hosts(
                os.getenv("GITLAB_ARTIFACT_REDIRECT_HOSTS", "")
            ),
        )


@dataclass(frozen=True)
class GitLabResponse:
    data: Any
    headers: dict[str, str]


def encode_path_component(value: str | int) -> str:
    """Encode a project path, file path, ref, or other single URL component."""
    return quote(str(value), safe="")


def pagination(data: Any, headers: dict[str, str], page: int, per_page: int) -> dict[str, Any]:
    return {
        "items": data,
        "pagination": {
            "page": int(headers.get("x-page") or page),
            "per_page": int(headers.get("x-per-page") or per_page),
            "next_page": int(headers["x-next-page"]) if headers.get("x-next-page") else None,
            "previous_page": int(headers["x-prev-page"]) if headers.get("x-prev-page") else None,
            "total": int(headers["x-total"]) if headers.get("x-total") else None,
            "total_pages": int(headers["x-total-pages"]) if headers.get("x-total-pages") else None,
        },
    }


class GitLabClient:
    """GitLab REST API client."""

    def __init__(self, settings: GitLabSettings):
        self.settings = settings

    def _url(self, path: str) -> str:
        clean_path = path.lstrip("/")
        if not clean_path or "://" in clean_path or clean_path.startswith("../"):
            raise GitLabReadOnlyError("Invalid GitLab API path")
        return f"{self.settings.base_url}/api/v4/{clean_path}"

    def request(
        self,
        method: Literal["GET", "POST", "PUT", "DELETE"],
        path: str,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        raw: bool = False,
    ) -> GitLabResponse:
        """Perform a bounded GitLab API request without redirects."""
        headers = {"PRIVATE-TOKEN": self.settings.token, "Accept": "application/json"}
        verify: bool | str = self.settings.ca_bundle or True
        safe_params = {key: value for key, value in (params or {}).items() if value is not None}

        try:
            with httpx.Client(
                timeout=self.settings.timeout_seconds,
                verify=verify,
                follow_redirects=False,
                trust_env=self.settings.trust_env_proxy,
            ) as session:
                response = session.request(
                    method,
                    self._url(path),
                    headers=headers,
                    params=safe_params,
                    json=json,
                )
        except httpx.HTTPError as exc:
            raise GitLabReadOnlyError(f"GitLab request failed: {exc}") from exc

        if response.is_redirect:
            raise GitLabReadOnlyError(
                f"GitLab returned an unexpected redirect ({response.status_code}); redirects are disabled"
            )
        if response.status_code >= 400:
            message = response.text[:1000].replace(self.settings.token, "<redacted>")
            raise GitLabReadOnlyError(f"GitLab API returned {response.status_code}: {message}")

        data: Any
        if response.status_code == 204 or not response.content:
            data = None
        elif raw:
            data = response.text
        else:
            try:
                data = response.json()
            except ValueError as exc:
                raise GitLabReadOnlyError("GitLab returned invalid JSON") from exc
        return GitLabResponse(data=data, headers={key.lower(): value for key, value in response.headers.items()})

    def get(self, path: str, params: dict[str, Any] | None = None, raw: bool = False) -> GitLabResponse:
        return self.request("GET", path, params=params, raw=raw)

    def post(self, path: str, json: dict[str, Any] | None = None) -> GitLabResponse:
        payload = {key: value for key, value in (json or {}).items() if value is not None}
        return self.request("POST", path, json=payload)

    def put(self, path: str, json: dict[str, Any] | None = None) -> GitLabResponse:
        payload = {key: value for key, value in (json or {}).items() if value is not None}
        return self.request("PUT", path, json=payload)

    def delete(self, path: str) -> GitLabResponse:
        return self.request("DELETE", path)

    def download_file(
        self,
        path: str,
        output: Path,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Stream a binary download (job artifacts) to output with a size cap.

        The cap is enforced via Content-Length when present and while streaming;
        a partial file is removed when any limit or error is hit. HTTPS redirects
        are followed only to the GitLab host or an explicitly allowed artifact
        host, and the GitLab token is never forwarded after the first request.
        """
        url = self._url(path)
        headers = {"PRIVATE-TOKEN": self.settings.token, "Accept": "application/octet-stream"}
        verify: bool | str = self.settings.ca_bundle or True
        safe_params = {key: value for key, value in (params or {}).items() if value is not None}
        limit = self.settings.max_artifact_bytes
        request_url = url
        request_headers = headers
        request_params: dict[str, Any] | None = safe_params
        redirects_followed = 0
        try:
            with httpx.Client(
                timeout=self.settings.timeout_seconds,
                verify=verify,
                follow_redirects=False,
                trust_env=self.settings.trust_env_proxy,
            ) as session:
                while True:
                    with session.stream(
                        "GET",
                        request_url,
                        headers=request_headers,
                        params=request_params,
                    ) as response:
                        if response.is_redirect:
                            if redirects_followed >= MAX_ARTIFACT_REDIRECTS:
                                raise GitLabReadOnlyError(
                                    f"GitLab artifact download exceeded {MAX_ARTIFACT_REDIRECTS} "
                                    "redirects"
                                )
                            location = response.headers.get("location")
                            request_url = self._validated_artifact_redirect(request_url, location)
                            request_headers = {"Accept": "application/octet-stream"}
                            request_params = None
                            redirects_followed += 1
                            continue
                        if response.status_code >= 400:
                            response.read()
                            message = response.text[:1000].replace(
                                self.settings.token, "<redacted>"
                            )
                            raise GitLabReadOnlyError(
                                f"GitLab API returned {response.status_code}: {message}"
                            )
                        length_header = response.headers.get("content-length")
                        if length_header and int(length_header) > limit:
                            raise GitLabReadOnlyError(
                                f"Artifact is {int(length_header)} bytes; limit is {limit}. "
                                "Raise GITLAB_MAX_ARTIFACT_BYTES if this artifact is expected."
                            )
                        output.parent.mkdir(parents=True, exist_ok=True)
                        size = 0
                        try:
                            with output.open("wb") as handle:
                                for chunk in response.iter_bytes():
                                    size += len(chunk)
                                    if size > limit:
                                        raise GitLabReadOnlyError(
                                            f"Artifact exceeds {limit} bytes; partial download removed"
                                        )
                                    handle.write(chunk)
                        except BaseException:
                            output.unlink(missing_ok=True)
                            raise
                        return {
                            "size": size,
                            "content_type": response.headers.get("content-type"),
                            "redirects_followed": redirects_followed,
                        }
        except httpx.HTTPError as exc:
            output.unlink(missing_ok=True)
            raise GitLabReadOnlyError(f"GitLab request failed: {exc}") from exc

    def _validated_artifact_redirect(self, source_url: str, location: str | None) -> str:
        if not location:
            raise GitLabReadOnlyError("GitLab artifact redirect has no Location header")
        target = urlsplit(urljoin(source_url, location))
        try:
            target_port = target.port
        except ValueError as exc:
            raise GitLabReadOnlyError("GitLab artifact redirect URL is invalid") from exc
        if (
            target.scheme != "https"
            or not target.hostname
            or target.username
            or target.password
            or target.fragment
            or target_port not in (None, 443)
        ):
            raise GitLabReadOnlyError("GitLab artifact redirect must be a credential-free HTTPS URL")

        target_host = target.hostname.lower().rstrip(".")
        gitlab_host = (urlsplit(self.settings.base_url).hostname or "").lower().rstrip(".")
        allowed_hosts = {gitlab_host, *self.settings.artifact_redirect_hosts}
        if target_host not in allowed_hosts:
            raise GitLabReadOnlyError(
                f"GitLab artifact redirect host '{target_host}' is not allowed; add it to "
                "GITLAB_ARTIFACT_REDIRECT_HOSTS after verification"
            )
        return target.geturl()

    def get_paginated(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        page: int = 1,
        per_page: int = 50,
    ) -> dict[str, Any]:
        if page < 1:
            raise GitLabReadOnlyError("page must be at least 1")
        if per_page < 1 or per_page > 100:
            raise GitLabReadOnlyError("per_page must be between 1 and 100")
        query = dict(params or {})
        query.update({"page": page, "per_page": per_page})
        response = self.get(path, query)
        return pagination(response.data, response.headers, page, per_page)

    def get_file(
        self,
        project: str | int,
        file_path: str,
        ref: str,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> dict[str, Any]:
        response = self.get(
            f"projects/{encode_path_component(project)}/repository/files/{encode_path_component(file_path)}",
            {"ref": ref},
        ).data
        size = int(response.get("size", 0))
        if size > self.settings.max_file_bytes:
            raise GitLabReadOnlyError(
                f"File is {size} bytes; limit is {self.settings.max_file_bytes}. "
                "Use repository metadata or clone the repository instead."
            )

        try:
            decoded = base64.b64decode(response["content"], validate=True)
        except (KeyError, ValueError, binascii.Error) as exc:
            raise GitLabReadOnlyError("GitLab returned invalid file content") from exc

        result = {key: value for key, value in response.items() if key != "content"}
        try:
            text = decoded.decode("utf-8")
        except UnicodeDecodeError:
            result.update({"content": response["content"], "content_encoding": "base64", "binary": True})
            return result

        lines = text.splitlines()
        first = start_line or 1
        last = end_line or len(lines)
        if not lines and start_line is None and end_line is None:
            result.update(
                {
                    "content": "",
                    "content_encoding": "utf-8",
                    "binary": False,
                    "line_range": {"start": 0, "end": 0, "total": 0},
                }
            )
            return result
        if first < 1 or last < first:
            raise GitLabReadOnlyError("Invalid line range")
        result.update(
            {
                "content": "\n".join(lines[first - 1 : last]),
                "content_encoding": "utf-8",
                "binary": False,
                "line_range": {"start": first, "end": min(last, len(lines)), "total": len(lines)},
            }
        )
        return result


# Compatibility aliases for callers of the previous read-only MCP.
GitLabReadOnlyClient = GitLabClient

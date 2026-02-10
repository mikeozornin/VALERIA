"""GitLab MCP with HTTPS REST access and SSH-only Git network operations."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import sys
import time
import zipfile
from collections import deque
from datetime import UTC, datetime
from functools import partial, wraps
from pathlib import Path
from typing import Any, Callable, Literal, TypeVar

import anyio
from git_ssh_client import GitLabSSHClient, GitLabSSHSettings
from gitlab_client import GitLabClient, GitLabError, GitLabSettings, encode_path_component
from mcp.server.fastmcp import FastMCP
from trace_selection import select_trace, validate_selection


DEFAULT_MCP_MAX_CONCURRENCY = 8
DEFAULT_MCP_MAX_GIT_CONCURRENCY = 2
_GIT_TOOL_NAMES = frozenset(
    {
        "gitlab_clone_repository",
        "gitlab_configure_ssh_remote",
        "gitlab_repository_status",
        "gitlab_repository_diff",
        "gitlab_switch_branch",
        "gitlab_stage_paths",
        "gitlab_unstage_paths",
        "gitlab_commit",
        "gitlab_fetch",
        "gitlab_pull",
        "gitlab_push",
        "gitlab_commit_and_push",
    }
)
_Result = TypeVar("_Result")


def _positive_int_env(name: str, default: int) -> int:
    raw_value = os.getenv(name, str(default))
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if value < 1:
        raise RuntimeError(f"{name} must be positive")
    return value


def _tool_logger() -> logging.Logger:
    logger = logging.getLogger("gitlab-mcp.tools")
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(
            logging.Formatter("%(asctime)s level=%(levelname)s logger=%(name)s %(message)s")
        )
        logger.addHandler(handler)
        logger.propagate = False
    logger.setLevel(os.getenv("GITLAB_MCP_LOG_LEVEL", "INFO").upper())
    return logger


class NonBlockingFastMCP(FastMCP):
    """Register synchronous tools without blocking the MCP event loop."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._tool_limiters: dict[str, anyio.CapacityLimiter] = {}

    def _tool_limiter(self, group: str) -> anyio.CapacityLimiter:
        limiter = self._tool_limiters.get(group)
        if limiter is None:
            if group == "git":
                capacity = _positive_int_env(
                    "GITLAB_MCP_MAX_GIT_CONCURRENCY", DEFAULT_MCP_MAX_GIT_CONCURRENCY
                )
            else:
                capacity = _positive_int_env(
                    "GITLAB_MCP_MAX_CONCURRENCY", DEFAULT_MCP_MAX_CONCURRENCY
                )
            limiter = anyio.CapacityLimiter(capacity)
            self._tool_limiters[group] = limiter
        return limiter

    def tool(self, *tool_args: Any, **tool_kwargs: Any) -> Callable[..., Any]:
        register = super().tool(*tool_args, **tool_kwargs)

        def decorator(function: Callable[..., _Result]) -> Callable[..., _Result]:
            group = "git" if function.__name__ in _GIT_TOOL_NAMES else "default"

            @wraps(function)
            async def run_nonblocking(*args: Any, **kwargs: Any) -> _Result:
                started = time.monotonic()
                outcome = "cancelled"
                logger = _tool_logger()
                logger.info("tool=%s group=%s event=start", function.__name__, group)
                try:
                    result = await anyio.to_thread.run_sync(
                        partial(function, *args, **kwargs),
                        abandon_on_cancel=False,
                        limiter=self._tool_limiter(group),
                    )
                except Exception:
                    outcome = "error"
                    logger.exception("tool=%s group=%s event=error", function.__name__, group)
                    raise
                else:
                    outcome = "ok"
                    return result
                finally:
                    logger.info(
                        "tool=%s group=%s event=finish outcome=%s duration_seconds=%.3f",
                        function.__name__,
                        group,
                        outcome,
                        time.monotonic() - started,
                    )

            register(run_nonblocking)
            return function

        return decorator


mcp = NonBlockingFastMCP("gitlab")


def client() -> GitLabClient:
    return GitLabClient(GitLabSettings.from_env())


def git_client() -> GitLabSSHClient:
    api_settings = GitLabSettings.from_env(require_token=False)
    settings = GitLabSSHSettings.from_env(api_settings.clone_root, api_settings.base_url)
    return GitLabSSHClient(settings)


@mcp.tool()
def gitlab_connection_info() -> dict[str, Any]:
    """Show safe REST and SSH configuration details. Never returns credentials."""
    settings = GitLabSettings.from_env(require_token=False)
    ssh = GitLabSSHSettings.from_env(settings.clone_root, settings.base_url)
    return {
        "base_url": settings.base_url,
        "token_configured": bool(settings.token),
        "repository_roots": [str(path) for path in ssh.repository_roots],
        "allow_absolute_paths": ssh.allow_absolute_paths,
        "rest_transport": "HTTPS GitLab REST API",
        "git_transport": "SSH only",
        "ssh_host": ssh.ssh_host,
        "ssh_user": ssh.ssh_user,
        "ssh_port": ssh.ssh_port or 22,
        "ssh_agent_configured": bool(os.getenv("SSH_AUTH_SOCK")),
        "ssh_identity_file_configured": ssh.ssh_identity_file is not None,
        "token_scope_for_reads": "read_api",
        "token_scope_for_rest_writes": "api",
        "trace_output_dir": str(settings.trace_output_dir or settings.clone_root / "gitlab-traces"),
        "artifact_output_dir": str(_artifact_output_dir(settings)),
        "max_artifact_bytes": settings.max_artifact_bytes,
    }


@mcp.tool()
def gitlab_get_current_user() -> dict[str, Any]:
    """Get the profile associated with the configured GitLab token."""
    return client().get("user").data


@mcp.tool()
def gitlab_list_projects(
    search: str | None = None,
    membership: bool = True,
    archived: bool | None = False,
    order_by: Literal["id", "name", "path", "created_at", "updated_at", "last_activity_at"] = "last_activity_at",
    sort: Literal["asc", "desc"] = "desc",
    page: int = 1,
    per_page: int = 50,
) -> dict[str, Any]:
    """List projects visible to the token, optionally filtering by name/path."""
    return client().get_paginated(
        "projects",
        {
            "search": search,
            "membership": str(membership).lower(),
            "archived": str(archived).lower() if archived is not None else None,
            "order_by": order_by,
            "sort": sort,
            "simple": "true",
        },
        page,
        per_page,
    )


@mcp.tool()
def gitlab_get_project(project: str) -> dict[str, Any]:
    """Get project metadata by numeric ID or namespace/path."""
    return client().get(f"projects/{encode_path_component(project)}").data


@mcp.tool()
def gitlab_list_repository_tree(
    project: str,
    path: str | None = None,
    ref: str | None = None,
    recursive: bool = False,
    page: int = 1,
    per_page: int = 100,
) -> dict[str, Any]:
    """List files and directories in a repository tree."""
    return client().get_paginated(
        f"projects/{encode_path_component(project)}/repository/tree",
        {"path": path, "ref": ref, "recursive": str(recursive).lower()},
        page,
        per_page,
    )


@mcp.tool()
def gitlab_get_file(
    project: str,
    file_path: str,
    ref: str = "HEAD",
    start_line: int | None = None,
    end_line: int | None = None,
) -> dict[str, Any]:
    """Read a repository file, optionally selecting an inclusive line range."""
    return client().get_file(project, file_path, ref, start_line, end_line)


@mcp.tool()
def gitlab_list_commits(
    project: str,
    ref_name: str | None = None,
    path: str | None = None,
    since: str | None = None,
    until: str | None = None,
    search: str | None = None,
    page: int = 1,
    per_page: int = 50,
) -> dict[str, Any]:
    """List repository commits without creating or modifying commits."""
    return client().get_paginated(
        f"projects/{encode_path_component(project)}/repository/commits",
        {"ref_name": ref_name, "path": path, "since": since, "until": until, "search": search},
        page,
        per_page,
    )


@mcp.tool()
def gitlab_get_commit(project: str, sha: str, include_diff: bool = False) -> dict[str, Any]:
    """Get a commit and optionally its read-only diff."""
    gitlab = client()
    project_id = encode_path_component(project)
    commit_id = encode_path_component(sha)
    result = gitlab.get(f"projects/{project_id}/repository/commits/{commit_id}").data
    if include_diff:
        result["diff"] = gitlab.get(f"projects/{project_id}/repository/commits/{commit_id}/diff").data
    return result


@mcp.tool()
def gitlab_compare_refs(project: str, from_ref: str, to_ref: str, straight: bool = False) -> dict[str, Any]:
    """Compare two branches, tags, or commit SHAs."""
    return client().get(
        f"projects/{encode_path_component(project)}/repository/compare",
        {"from": from_ref, "to": to_ref, "straight": str(straight).lower()},
    ).data


@mcp.tool()
def gitlab_clone_repository(
    project: str,
    destination: str | None = None,
    ref: str | None = None,
    depth: int = 0,
) -> dict[str, Any]:
    """Clone over SSH into an exact absolute path or a relative path under the first configured root."""
    return git_client().clone_repository(project, destination, ref, depth)


@mcp.tool()
def gitlab_configure_ssh_remote(
    repository_path: str,
    project: str,
    remote: str = "origin",
) -> dict[str, Any]:
    """Set fetch and push URLs from an approved GitLab namespace/project SSH route."""
    return git_client().configure_ssh_remote(repository_path, project, remote)


@mcp.tool()
def gitlab_repository_status(repository_path: str) -> dict[str, Any]:
    """Show branch, staged, unstaged, and untracked changes in a local repository."""
    return git_client().status(repository_path)


@mcp.tool()
def gitlab_repository_diff(
    repository_path: str,
    staged: bool = False,
    paths: list[str] | None = None,
) -> dict[str, Any]:
    """Show a local diff, optionally restricted to staged changes or selected paths."""
    return git_client().diff(repository_path, staged, paths)


@mcp.tool()
def gitlab_switch_branch(
    repository_path: str,
    branch: str,
    create: bool = False,
    start_point: str | None = None,
) -> dict[str, Any]:
    """Switch branches, or create a branch from an optional start point."""
    return git_client().switch_branch(repository_path, branch, create, start_point)


@mcp.tool()
def gitlab_stage_paths(repository_path: str, paths: list[str]) -> dict[str, Any]:
    """Stage selected repository-relative paths, including deletions."""
    return git_client().stage(repository_path, paths)


@mcp.tool()
def gitlab_unstage_paths(repository_path: str, paths: list[str]) -> dict[str, Any]:
    """Remove selected repository-relative paths from the staging area."""
    return git_client().unstage(repository_path, paths)


@mcp.tool()
def gitlab_commit(
    repository_path: str,
    message: str,
    paths: list[str] | None = None,
) -> dict[str, Any]:
    """Create a local commit from staged changes or explicit paths. Repository hooks are disabled."""
    return git_client().commit(repository_path, message, paths)


@mcp.tool()
def gitlab_fetch(
    repository_path: str,
    remote: str = "origin",
    prune: bool = True,
    tags: bool = True,
) -> dict[str, Any]:
    """Fetch from a remote after verifying that its fetch URL uses approved SSH."""
    return git_client().fetch(repository_path, remote, prune, tags)


@mcp.tool()
def gitlab_pull(repository_path: str, remote: str = "origin") -> dict[str, Any]:
    """Fast-forward the current branch from an approved SSH remote."""
    return git_client().pull(repository_path, remote)


@mcp.tool()
def gitlab_push(
    repository_path: str,
    remote: str = "origin",
    branch: str | None = None,
) -> dict[str, Any]:
    """Push HEAD over approved SSH without force to the current or selected branch."""
    return git_client().push(repository_path, remote, branch)


@mcp.tool()
def gitlab_commit_and_push(
    repository_path: str,
    message: str,
    paths: list[str] | None = None,
    remote: str = "origin",
    branch: str | None = None,
) -> dict[str, Any]:
    """Create a local commit and push it over approved SSH without force."""
    return git_client().commit_and_push(repository_path, message, paths, remote, branch)


@mcp.tool()
def gitlab_list_merge_requests(
    project: str,
    state: Literal["opened", "closed", "locked", "merged", "all"] = "opened",
    search: str | None = None,
    source_branch: str | None = None,
    target_branch: str | None = None,
    labels: str | None = None,
    author_username: str | None = None,
    assignee_username: str | None = None,
    page: int = 1,
    per_page: int = 50,
) -> dict[str, Any]:
    """List merge requests in a project."""
    return client().get_paginated(
        f"projects/{encode_path_component(project)}/merge_requests",
        {
            "state": state,
            "search": search,
            "source_branch": source_branch,
            "target_branch": target_branch,
            "labels": labels,
            "author_username": author_username,
            "assignee_username": assignee_username,
            "scope": "all",
        },
        page,
        per_page,
    )


@mcp.tool()
def gitlab_get_merge_request(project: str, merge_request_iid: int) -> dict[str, Any]:
    """Get full metadata for one merge request."""
    return client().get(
        f"projects/{encode_path_component(project)}/merge_requests/{merge_request_iid}"
    ).data


@mcp.tool()
def gitlab_get_merge_request_diffs(
    project: str,
    merge_request_iid: int,
    unidiff: bool = True,
    page: int = 1,
    per_page: int = 50,
) -> dict[str, Any]:
    """List changed files and diffs for a merge request."""
    return client().get_paginated(
        f"projects/{encode_path_component(project)}/merge_requests/{merge_request_iid}/diffs",
        {"unidiff": str(unidiff).lower()},
        page,
        per_page,
    )


@mcp.tool()
def gitlab_get_merge_request_notes(
    project: str,
    merge_request_iid: int,
    sort: Literal["asc", "desc"] = "asc",
    order_by: Literal["created_at", "updated_at"] = "created_at",
    page: int = 1,
    per_page: int = 100,
) -> dict[str, Any]:
    """Read comments and system notes for a merge request."""
    return client().get_paginated(
        f"projects/{encode_path_component(project)}/merge_requests/{merge_request_iid}/notes",
        {"sort": sort, "order_by": order_by},
        page,
        per_page,
    )


@mcp.tool()
def gitlab_create_merge_request(
    project: str,
    source_branch: str,
    target_branch: str,
    title: str,
    description: str | None = None,
    draft: bool = False,
    remove_source_branch: bool = False,
    squash: bool | None = None,
    labels: str | None = None,
) -> dict[str, Any]:
    """Create a merge request through the GitLab REST API."""
    mr_title = f"Draft: {title}" if draft and not title.lower().startswith("draft:") else title
    return client().post(
        f"projects/{encode_path_component(project)}/merge_requests",
        {
            "source_branch": source_branch,
            "target_branch": target_branch,
            "title": mr_title,
            "description": description,
            "remove_source_branch": remove_source_branch,
            "squash": squash,
            "labels": labels,
        },
    ).data


@mcp.tool()
def gitlab_update_merge_request(
    project: str,
    merge_request_iid: int,
    title: str | None = None,
    description: str | None = None,
    target_branch: str | None = None,
    state_event: Literal["close", "reopen"] | None = None,
    labels: str | None = None,
    remove_source_branch: bool | None = None,
    squash: bool | None = None,
) -> dict[str, Any]:
    """Update, close, or reopen a merge request through the GitLab REST API."""
    values = {
        "title": title,
        "description": description,
        "target_branch": target_branch,
        "state_event": state_event,
        "labels": labels,
        "remove_source_branch": remove_source_branch,
        "squash": squash,
    }
    payload = {key: value for key, value in values.items() if value is not None}
    if not payload:
        raise ValueError("At least one merge request field must be provided")
    return client().put(
        f"projects/{encode_path_component(project)}/merge_requests/{merge_request_iid}", payload
    ).data


@mcp.tool()
def gitlab_add_merge_request_note(
    project: str,
    merge_request_iid: int,
    body: str,
    internal: bool = False,
) -> dict[str, Any]:
    """Add a general comment to a merge request."""
    return client().post(
        f"projects/{encode_path_component(project)}/merge_requests/{merge_request_iid}/notes",
        {"body": body, "internal": internal},
    ).data


@mcp.tool()
def gitlab_merge_merge_request(
    project: str,
    merge_request_iid: int,
    expected_head_sha: str,
    squash: bool | None = None,
    should_remove_source_branch: bool | None = None,
    merge_commit_message: str | None = None,
) -> dict[str, Any]:
    """Merge an MR only if its current head matches expected_head_sha."""
    return client().put(
        f"projects/{encode_path_component(project)}/merge_requests/{merge_request_iid}/merge",
        {
            "sha": expected_head_sha,
            "squash": squash,
            "should_remove_source_branch": should_remove_source_branch,
            "merge_commit_message": merge_commit_message,
        },
    ).data


@mcp.tool()
def gitlab_list_issues(
    project: str,
    state: Literal["opened", "closed", "all"] = "opened",
    search: str | None = None,
    labels: str | None = None,
    author_username: str | None = None,
    assignee_username: str | None = None,
    page: int = 1,
    per_page: int = 50,
) -> dict[str, Any]:
    """List issues in a project."""
    return client().get_paginated(
        f"projects/{encode_path_component(project)}/issues",
        {
            "state": state,
            "search": search,
            "labels": labels,
            "author_username": author_username,
            "assignee_username": assignee_username,
            "scope": "all",
        },
        page,
        per_page,
    )


@mcp.tool()
def gitlab_get_issue(project: str, issue_iid: int) -> dict[str, Any]:
    """Get one project issue by IID."""
    return client().get(f"projects/{encode_path_component(project)}/issues/{issue_iid}").data


@mcp.tool()
def gitlab_get_issue_notes(
    project: str,
    issue_iid: int,
    sort: Literal["asc", "desc"] = "asc",
    order_by: Literal["created_at", "updated_at"] = "created_at",
    page: int = 1,
    per_page: int = 100,
) -> dict[str, Any]:
    """Read comments and system notes for an issue."""
    return client().get_paginated(
        f"projects/{encode_path_component(project)}/issues/{issue_iid}/notes",
        {"sort": sort, "order_by": order_by},
        page,
        per_page,
    )


@mcp.tool()
def gitlab_create_issue(
    project: str,
    title: str,
    description: str | None = None,
    labels: str | None = None,
    confidential: bool = False,
    issue_type: Literal["issue", "incident", "test_case", "task"] = "issue",
    assignee_ids: list[int] | None = None,
) -> dict[str, Any]:
    """Create a project issue through the GitLab REST API."""
    return client().post(
        f"projects/{encode_path_component(project)}/issues",
        {
            "title": title,
            "description": description,
            "labels": labels,
            "confidential": confidential,
            "issue_type": issue_type,
            "assignee_ids": assignee_ids,
        },
    ).data


@mcp.tool()
def gitlab_update_issue(
    project: str,
    issue_iid: int,
    title: str | None = None,
    description: str | None = None,
    labels: str | None = None,
    state_event: Literal["close", "reopen"] | None = None,
    confidential: bool | None = None,
    assignee_ids: list[int] | None = None,
) -> dict[str, Any]:
    """Update, close, or reopen a project issue."""
    values = {
        "title": title,
        "description": description,
        "labels": labels,
        "state_event": state_event,
        "confidential": confidential,
        "assignee_ids": assignee_ids,
    }
    payload = {key: value for key, value in values.items() if value is not None}
    if not payload:
        raise ValueError("At least one issue field must be provided")
    return client().put(
        f"projects/{encode_path_component(project)}/issues/{issue_iid}", payload
    ).data


@mcp.tool()
def gitlab_add_issue_note(
    project: str,
    issue_iid: int,
    body: str,
    internal: bool = False,
) -> dict[str, Any]:
    """Add a comment to a project issue."""
    return client().post(
        f"projects/{encode_path_component(project)}/issues/{issue_iid}/notes",
        {"body": body, "internal": internal},
    ).data


@mcp.tool()
def gitlab_list_pipelines(
    project: str,
    status: str | None = None,
    ref: str | None = None,
    username: str | None = None,
    source: str | None = None,
    page: int = 1,
    per_page: int = 50,
    sha: str | None = None,
) -> dict[str, Any]:
    """List project pipelines, optionally filtering by an exact commit SHA."""
    return client().get_paginated(
        f"projects/{encode_path_component(project)}/pipelines",
        {
            "status": status,
            "ref": ref,
            "username": username,
            "source": source,
            "sha": sha,
            "order_by": "id",
            "sort": "desc",
        },
        page,
        per_page,
    )


@mcp.tool()
def gitlab_get_pipeline(project: str, pipeline_id: int) -> dict[str, Any]:
    """Get details for one pipeline."""
    return client().get(
        f"projects/{encode_path_component(project)}/pipelines/{pipeline_id}"
    ).data


@mcp.tool()
def gitlab_list_pipeline_jobs(
    project: str,
    pipeline_id: int,
    scope: list[str] | None = None,
    include_retried: bool = False,
    page: int = 1,
    per_page: int = 100,
    compact: bool = False,
) -> dict[str, Any]:
    """List pipeline jobs. compact=true returns one row per job, duration in seconds.

    Full metadata is returned by default. Both modes preserve filters and pagination.
    """
    result = client().get_paginated(
        f"projects/{encode_path_component(project)}/pipelines/{pipeline_id}/jobs",
        {"scope[]": scope, "include_retried": str(include_retried).lower()},
        page,
        per_page,
    )
    if not compact:
        return result
    return {
        "columns": ["job_id", "status", "duration_seconds", "name"],
        "items": [
            f"{job['id']} | {job['status']} | "
            f"{job['duration'] if job.get('duration') is not None else '—'} | "
            f"{json.dumps(job['name'], ensure_ascii=False)}"
            for job in result["items"]
        ],
        "pagination": result["pagination"],
    }


@mcp.tool()
def gitlab_list_pipeline_bridges(
    project: str,
    pipeline_id: int,
    scope: list[str] | None = None,
    page: int = 1,
    per_page: int = 100,
    compact: bool = False,
) -> dict[str, Any]:
    """List pipeline trigger jobs (bridges) and their downstream pipelines.

    Pipeline jobs and trigger jobs are separate GitLab API resources. Call this
    alongside gitlab_list_pipeline_jobs when traversing a complete pipeline graph.
    compact=true returns one row per bridge with the downstream pipeline identity.
    """
    result = client().get_paginated(
        f"projects/{encode_path_component(project)}/pipelines/{pipeline_id}/trigger_jobs",
        {"scope[]": scope},
        page,
        per_page,
    )
    if not compact:
        return result

    rows: list[str] = []
    for bridge in result["items"]:
        downstream = bridge.get("downstream_pipeline") or {}
        rows.append(
            f"{bridge.get('id', '—')} | {bridge.get('status') or '—'} | "
            f"{json.dumps(bridge.get('name'), ensure_ascii=False)} | "
            f"{bridge.get('allow_failure', '—')} | {downstream.get('id', '—')} | "
            f"{downstream.get('project_id', '—')} | {downstream.get('status') or '—'} | "
            f"{json.dumps(downstream.get('web_url'), ensure_ascii=False)}"
        )
    return {
        "columns": [
            "bridge_id",
            "status",
            "name",
            "allow_failure",
            "downstream_pipeline_id",
            "downstream_project_id",
            "downstream_status",
            "downstream_web_url",
        ],
        "items": rows,
        "pagination": result["pagination"],
    }


PIPELINE_GRAPH_PIPELINE_FIELDS = (
    "id",
    "iid",
    "project_id",
    "status",
    "source",
    "ref",
    "sha",
    "name",
    "web_url",
    "created_at",
    "updated_at",
    "started_at",
    "finished_at",
    "duration",
    "queued_duration",
    "archived",
    "yaml_errors",
)
PIPELINE_GRAPH_JOB_FIELDS = (
    "name",
    "stage",
    "status",
    "allow_failure",
    "retried",
    "failure_reason",
    "when",
    "manual",
    "web_url",
    "created_at",
    "started_at",
    "finished_at",
    "duration",
    "queued_duration",
    "artifacts",
    "artifacts_file",
    "artifacts_expire_at",
)
PIPELINE_GRAPH_BRIDGE_FIELDS = (
    "name",
    "stage",
    "status",
    "allow_failure",
    "failure_reason",
    "web_url",
    "created_at",
    "started_at",
    "finished_at",
    "duration",
    "queued_duration",
)
NONTERMINAL_PIPELINE_STATUSES = {
    "created",
    "waiting_for_resource",
    "preparing",
    "pending",
    "running",
    "canceling",
    "scheduled",
    "manual",
}


def _selected_fields(data: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: data[field] for field in fields if field in data}


def _collect_graph_pages(
    gitlab: GitLabClient,
    path: str,
    params: dict[str, Any],
    max_pages: int,
) -> tuple[list[dict[str, Any]], dict[str, Any], str | None]:
    """Collect a bounded paginated resource while retaining partial progress."""
    items: list[dict[str, Any]] = []
    pages_read = 0
    page = 1
    last_page: int | None = None
    while True:
        try:
            result = gitlab.get_paginated(path, params, page=page, per_page=100)
        except GitLabError as exc:
            return (
                items,
                {
                    "complete": False,
                    "pages_read": pages_read,
                    "last_page": last_page,
                    "items_read": len(items),
                    "limit_hit": False,
                },
                str(exc),
            )

        page_items = result.get("items") or []
        items.extend(page_items)
        pages_read += 1
        last_page = page
        next_page = (result.get("pagination") or {}).get("next_page")
        if next_page is None:
            return (
                items,
                {
                    "complete": True,
                    "pages_read": pages_read,
                    "last_page": last_page,
                    "items_read": len(items),
                    "limit_hit": False,
                },
                None,
            )
        if not isinstance(next_page, int) or next_page <= page:
            return (
                items,
                {
                    "complete": False,
                    "pages_read": pages_read,
                    "last_page": last_page,
                    "items_read": len(items),
                    "limit_hit": False,
                },
                "GitLab returned non-advancing pagination",
            )
        if pages_read >= max_pages:
            return (
                items,
                {
                    "complete": False,
                    "pages_read": pages_read,
                    "last_page": last_page,
                    "items_read": len(items),
                    "limit_hit": True,
                },
                None,
            )
        page = next_page


def _graph_error(
    project_id: str,
    pipeline_id: int,
    resource: str,
    error: str,
) -> dict[str, Any]:
    return {
        "project_id": project_id,
        "pipeline_id": pipeline_id,
        "resource": resource,
        "error": error,
    }


@mcp.tool()
def gitlab_get_pipeline_graph(
    project: str,
    pipeline_id: int,
    include_retried: bool = True,
    max_depth: int = 20,
    max_pipelines: int = 200,
    max_pages_per_collection: int = 100,
    refresh_statuses: bool = True,
) -> dict[str, Any]:
    """Collect a normalized pipeline/downstream graph with explicit coverage.

    All pages of ordinary jobs and trigger jobs are read for every discovered
    pipeline. Bridge downstream_pipeline references are the only graph edges;
    equal SHAs or URLs never create an inferred edge. Job artifact metadata is
    retained, but traces and artifact contents are not downloaded.

    coverage.complete reports exhaustive traversal within the configured limits.
    snapshot.stable separately reports whether the graph could still be changing.
    """
    if not project.strip():
        raise ValueError("project must not be empty")
    if pipeline_id < 1:
        raise ValueError("pipeline_id must be positive")
    if max_depth < 0:
        raise ValueError("max_depth must be non-negative")
    if max_pipelines < 1:
        raise ValueError("max_pipelines must be positive")
    if max_pages_per_collection < 1:
        raise ValueError("max_pages_per_collection must be positive")

    gitlab = client()
    requested_project = project.strip()
    root_data = gitlab.get(
        f"projects/{encode_path_component(requested_project)}/pipelines/{pipeline_id}"
    ).data
    root_project_id = str(root_data.get("project_id", requested_project))
    root_pipeline_id = int(root_data.get("id", pipeline_id))
    root_key = (root_project_id, root_pipeline_id)

    queue: deque[tuple[str, int, int]] = deque([(root_project_id, root_pipeline_id, 0)])
    discovered = {root_key}
    prefetched = {root_key: root_data}
    visited: set[tuple[str, int]] = set()
    pipeline_order: list[tuple[str, int]] = []
    pipeline_data: dict[tuple[str, int], dict[str, Any]] = {}
    pipeline_depths: dict[tuple[str, int], int] = {}
    jobs: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    node_coverage: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    limits_hit: list[dict[str, Any]] = []

    while queue:
        project_ref, current_pipeline_id, depth = queue.popleft()
        key = (project_ref, current_pipeline_id)
        if key in visited:
            continue
        visited.add(key)
        pipeline_order.append(key)
        pipeline_depths[key] = depth
        node_result: dict[str, Any] = {
            "project_id": project_ref,
            "pipeline_id": current_pipeline_id,
            "depth": depth,
        }

        current_data = prefetched.pop(key, None)
        if current_data is None:
            try:
                current_data = gitlab.get(
                    f"projects/{encode_path_component(project_ref)}/pipelines/{current_pipeline_id}"
                ).data
            except GitLabError as exc:
                message = str(exc)
                errors.append(_graph_error(project_ref, current_pipeline_id, "pipeline", message))
                node_result.update(
                    {
                        "pipeline": {"complete": False},
                        "jobs": {"complete": False, "not_attempted": True},
                        "bridges": {"complete": False, "not_attempted": True},
                    }
                )
                node_coverage.append(node_result)
                continue

        current_data = dict(current_data)
        current_data.setdefault("id", current_pipeline_id)
        current_data.setdefault("project_id", project_ref)
        pipeline_data[key] = current_data
        node_result["pipeline"] = {"complete": True}

        project_path = encode_path_component(project_ref)
        current_jobs, jobs_coverage, jobs_error = _collect_graph_pages(
            gitlab,
            f"projects/{project_path}/pipelines/{current_pipeline_id}/jobs",
            {"include_retried": str(include_retried).lower()},
            max_pages_per_collection,
        )
        node_result["jobs"] = jobs_coverage
        for job in current_jobs:
            normalized_job = {
                "project_id": project_ref,
                "pipeline_id": current_pipeline_id,
                "job_id": job.get("id"),
            }
            normalized_job.update(_selected_fields(job, PIPELINE_GRAPH_JOB_FIELDS))
            jobs.append(normalized_job)
        if jobs_error:
            errors.append(_graph_error(project_ref, current_pipeline_id, "jobs", jobs_error))
        if jobs_coverage["limit_hit"]:
            limits_hit.append(
                {
                    "kind": "max_pages_per_collection",
                    "project_id": project_ref,
                    "pipeline_id": current_pipeline_id,
                    "resource": "jobs",
                    "value": max_pages_per_collection,
                }
            )

        current_bridges, bridges_coverage, bridges_error = _collect_graph_pages(
            gitlab,
            f"projects/{project_path}/pipelines/{current_pipeline_id}/trigger_jobs",
            {},
            max_pages_per_collection,
        )
        node_result["bridges"] = bridges_coverage
        if bridges_error:
            errors.append(_graph_error(project_ref, current_pipeline_id, "bridges", bridges_error))
        if bridges_coverage["limit_hit"]:
            limits_hit.append(
                {
                    "kind": "max_pages_per_collection",
                    "project_id": project_ref,
                    "pipeline_id": current_pipeline_id,
                    "resource": "bridges",
                    "value": max_pages_per_collection,
                }
            )

        for bridge in current_bridges:
            downstream = bridge.get("downstream_pipeline") or {}
            downstream_project_id = downstream.get("project_id")
            downstream_pipeline_id = downstream.get("id")
            target = None
            if downstream_project_id is not None or downstream_pipeline_id is not None:
                target = {
                    "project_id": (
                        str(downstream_project_id) if downstream_project_id is not None else None
                    ),
                    "pipeline_id": downstream_pipeline_id,
                }
            edge = {
                "bridge_id": bridge.get("id"),
                "from": {
                    "project_id": project_ref,
                    "pipeline_id": current_pipeline_id,
                },
                "to": target,
                "downstream_status": downstream.get("status"),
                "downstream_web_url": downstream.get("web_url"),
            }
            edge.update(_selected_fields(bridge, PIPELINE_GRAPH_BRIDGE_FIELDS))
            edges.append(edge)

            if downstream_pipeline_id is None:
                continue
            if downstream_project_id is None:
                errors.append(
                    _graph_error(
                        project_ref,
                        current_pipeline_id,
                        "bridge",
                        f"Bridge {bridge.get('id')} has downstream pipeline "
                        f"{downstream_pipeline_id} without project_id",
                    )
                )
                continue

            child_key = (str(downstream_project_id), int(downstream_pipeline_id))
            if child_key in discovered:
                continue
            if depth >= max_depth:
                limits_hit.append(
                    {
                        "kind": "max_depth",
                        "from": {
                            "project_id": project_ref,
                            "pipeline_id": current_pipeline_id,
                        },
                        "to": {
                            "project_id": child_key[0],
                            "pipeline_id": child_key[1],
                        },
                        "value": max_depth,
                    }
                )
                continue
            if len(discovered) >= max_pipelines:
                limits_hit.append(
                    {
                        "kind": "max_pipelines",
                        "from": {
                            "project_id": project_ref,
                            "pipeline_id": current_pipeline_id,
                        },
                        "to": {
                            "project_id": child_key[0],
                            "pipeline_id": child_key[1],
                        },
                        "value": max_pipelines,
                    }
                )
                continue
            discovered.add(child_key)
            queue.append((child_key[0], child_key[1], depth + 1))

        node_coverage.append(node_result)

    changed_during_collection: list[dict[str, Any]] = []
    if refresh_statuses:
        for project_ref, current_pipeline_id in pipeline_order:
            key = (project_ref, current_pipeline_id)
            if key not in pipeline_data:
                continue
            before = pipeline_data[key]
            try:
                after = gitlab.get(
                    f"projects/{encode_path_component(project_ref)}/pipelines/{current_pipeline_id}"
                ).data
            except GitLabError as exc:
                errors.append(
                    _graph_error(
                        project_ref,
                        current_pipeline_id,
                        "pipeline_refresh",
                        str(exc),
                    )
                )
                continue
            if before.get("status") != after.get("status") or before.get("updated_at") != after.get(
                "updated_at"
            ):
                changed_during_collection.append(
                    {
                        "project_id": project_ref,
                        "pipeline_id": current_pipeline_id,
                        "status_before": before.get("status"),
                        "status_after": after.get("status"),
                        "updated_at_before": before.get("updated_at"),
                        "updated_at_after": after.get("updated_at"),
                    }
                )
            after = dict(after)
            after.setdefault("id", current_pipeline_id)
            after.setdefault("project_id", project_ref)
            pipeline_data[key] = after

    normalized_pipelines = []
    for key in pipeline_order:
        if key not in pipeline_data:
            continue
        normalized = _selected_fields(pipeline_data[key], PIPELINE_GRAPH_PIPELINE_FIELDS)
        normalized["project_id"] = str(normalized.get("project_id", key[0]))
        normalized["depth"] = pipeline_depths[key]
        normalized_pipelines.append(normalized)

    active_pipelines = [
        {
            "project_id": str(item.get("project_id")),
            "pipeline_id": item.get("id"),
            "status": item.get("status"),
        }
        for item in normalized_pipelines
        if item.get("status") in NONTERMINAL_PIPELINE_STATUSES
    ]
    complete = not errors and not limits_hit and len(visited) == len(discovered)
    return {
        "root": {
            "project_id": root_project_id,
            "pipeline_id": root_pipeline_id,
        },
        "pipelines": normalized_pipelines,
        "jobs": jobs,
        "edges": edges,
        "coverage": {
            "complete": complete,
            "parameters": {
                "include_retried": include_retried,
                "max_depth": max_depth,
                "max_pipelines": max_pipelines,
                "max_pages_per_collection": max_pages_per_collection,
            },
            "pipelines_discovered": len(discovered),
            "pipelines_visited": len(visited),
            "jobs_collected": len(jobs),
            "bridges_collected": len(edges),
            "nodes": node_coverage,
            "errors": errors,
            "limits_hit": limits_hit,
        },
        "snapshot": {
            "atomic": False,
            "statuses_refreshed": refresh_statuses,
            "stable": (
                complete
                and refresh_statuses
                and not changed_during_collection
                and not active_pipelines
            ),
            "changed_during_collection": changed_during_collection,
            "active_pipelines": active_pipelines,
        },
    }


def _save_job_trace(settings: GitLabSettings, project: str, job_id: int, text: str) -> Path:
    output_dir = settings.trace_output_dir or settings.clone_root / "gitlab-traces"
    output_dir.mkdir(parents=True, exist_ok=True)

    project_slug = re.sub(r"[^A-Za-z0-9._-]+", "_", str(project)).strip("._-") or "project"
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:12]
    trace_file = output_dir / f"{project_slug}-job-{job_id}-{timestamp}-{digest}.log"
    trace_file.write_text(text, encoding="utf-8")
    return trace_file


@mcp.tool()
def gitlab_get_job_trace(
    project: str,
    job_id: int,
    offset: int | None = None,
    limit: int | None = None,
    pattern: str | None = None,
    context_before: int = 0,
    context_after: int = 0,
) -> dict[str, Any]:
    """Save the full log; return a bounded preview or numbered line selection.

    offset is 0-based in lines; negative offsets read from the end (e.g. -100).
    limit is a positive line count. pattern is a case-sensitive literal substring,
    with optional context_before/after. Overlapping search blocks are merged;
    offset/limit apply AFTER searching. Selection lines have original 1-based
    numbers. No options preserves the legacy character preview. The full trace
    is downloaded in all modes. GITLAB_MAX_TRACE_CHARS still caps the response.
    """
    validate_selection(limit, pattern, context_before, context_after)
    gitlab = client()
    text = gitlab.get(
        f"projects/{encode_path_component(project)}/jobs/{job_id}/trace",
        raw=True,
    ).data or ""
    trace_file = _save_job_trace(gitlab.settings, project, job_id, text)
    max_chars = gitlab.settings.max_trace_chars
    result = {
        "job_id": job_id,
        "trace": text[:max_chars],
        "truncated": len(text) > max_chars,
        "characters": len(text),
        "returned_characters": min(len(text), max_chars),
        "trace_file": str(trace_file),
        "trace_file_bytes": trace_file.stat().st_size,
        "full_trace_available_at": f"Full trace saved on the MCP host at {trace_file}",
    }
    if offset is not None or limit is not None or pattern is not None:
        result.update(select_trace(
            text, max_chars, offset, limit, pattern, context_before, context_after
        ))
    return result


def _validate_relative_path(value: str | None, name: str) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        raise ValueError(f"{name} must be a non-empty relative path")
    if cleaned.startswith("/") or cleaned.endswith("/"):
        raise ValueError(f"{name} must be a relative path without leading/trailing '/'")
    if "\\" in cleaned:
        raise ValueError(f"{name} must use '/' as the separator")
    parts = [part for part in cleaned.split("/") if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        raise ValueError(f"{name} must be relative without '..' components")
    return "/".join(parts)


def _artifact_output_dir(settings: GitLabSettings) -> Path:
    base = settings.trace_output_dir or settings.clone_root / "gitlab-traces"
    return base.parent / "gitlab-artifacts"


def _looks_binary(data: bytes) -> bool:
    return b"\x00" in data[:8192]


def _extract_zip_safely(archive: Path, extracted_dir: Path) -> list[dict[str, Any]]:
    root = extracted_dir.resolve()
    extracted_dir.mkdir(parents=True, exist_ok=True)
    files: list[dict[str, Any]] = []
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            if member.is_dir():
                continue
            target = (extracted_dir / member.filename).resolve()
            if not target.is_relative_to(root):
                raise GitLabError(
                    f"Archive member escapes the extraction directory: {member.filename}"
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(member) as source, target.open("wb") as sink:
                shutil.copyfileobj(source, sink)
            files.append({"path": member.filename, "size": member.file_size})
    files.sort(key=lambda item: item["path"])
    return files


def _read_text_file(
    path: Path,
    max_chars: int,
    offset: int | None,
    limit: int | None,
    pattern: str | None,
    context_before: int,
    context_after: int,
) -> dict[str, Any]:
    data = path.read_bytes()
    if _looks_binary(data):
        return {"binary": True, "bytes": len(data), "file": str(path)}
    text = data.decode("utf-8", errors="replace")
    selection = select_trace(text, max_chars, offset, limit, pattern, context_before, context_after)
    content = selection.pop("trace")
    return {
        "binary": False,
        "content": content,
        "characters": len(text),
        **selection,
    }


@mcp.tool()
def gitlab_get_job_artifact(
    project: str,
    job_id: int,
    artifact_path: str,
    inner_path: str | None = None,
    offset: int | None = None,
    limit: int | None = None,
    pattern: str | None = None,
    context_before: int = 0,
    context_after: int = 0,
) -> dict[str, Any]:
    """Download one job artifact; for zip archives, list or read inner text files.

    artifact_path is the job archive filename (e.g. install_logs.zip), or a
    path to a single file inside that archive. The archive filename is resolved
    from job metadata and downloaded via the whole-archive endpoint.
    Without inner_path a zip
    archive is extracted on the MCP host and its files are listed with sizes.
    With inner_path the file inside the archive is read as text with the same
    offset/limit/pattern semantics as gitlab_get_job_trace (pattern is a literal
    substring). A non-zip text artifact is read directly with the same options.
    Repeated calls re-download the artifact. Artifacts expire on the GitLab
    side (check artifacts_expire_at in the job metadata). Read-only.
    """
    validate_selection(limit, pattern, context_before, context_after)
    if job_id < 1:
        raise ValueError("job_id must be positive")
    artifact = _validate_relative_path(artifact_path, "artifact_path")
    inner = _validate_relative_path(inner_path, "inner_path") if inner_path else None

    gitlab = client()
    settings = gitlab.settings
    output_dir = _artifact_output_dir(settings)
    output_dir.mkdir(parents=True, exist_ok=True)

    project_slug = re.sub(r"[^A-Za-z0-9._-]+", "_", str(project)).strip("._-") or "project"
    artifact_slug = re.sub(r"[^A-Za-z0-9._-]+", "_", artifact)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    artifact_file = output_dir / f"{project_slug}-job-{job_id}-artifact-{artifact_slug}-{timestamp}"

    job_path = f"projects/{encode_path_component(project)}/jobs/{job_id}"
    job = gitlab.get(job_path).data
    archive_name = (job.get("artifacts_file") or {}).get("filename")
    if not archive_name:
        archive_name = next(
            (item.get("filename") for item in job.get("artifacts", [])
             if item.get("file_type") == "archive"),
            None,
        )
    api_path = f"{job_path}/artifacts"
    if artifact != archive_name:
        api_path += "/" + "/".join(
            encode_path_component(part) for part in artifact.split("/")
        )
    download = gitlab.download_file(api_path, artifact_file)
    result: dict[str, Any] = {
        "job_id": job_id,
        "artifact_path": artifact,
        "artifact_file": str(artifact_file),
        "artifact_bytes": download["size"],
        "content_type": download.get("content_type"),
        "artifact_redirects_followed": download.get("redirects_followed", 0),
    }

    if zipfile.is_zipfile(artifact_file):
        extracted_dir = artifact_file.parent / f"{artifact_file.name}.extracted"
        files = _extract_zip_safely(artifact_file, extracted_dir)
        result.update(
            {
                "is_zip": True,
                "extracted_dir": str(extracted_dir),
                "files": files,
                "file_count": len(files),
                "artifact_files_available_at": (
                    f"Extracted files are saved on the MCP host at {extracted_dir}"
                ),
            }
        )
        if inner:
            target = extracted_dir.joinpath(*inner.split("/"))
            if not target.is_file():
                result["inner_path"] = inner
                result["inner_path_error"] = (
                    f"inner_path '{inner}' not found in the archive; use the files list. "
                    f"Available: {', '.join(item['path'] for item in files[:20])}"
                )
                return result
            result["inner_path"] = inner
            result.update(
                _read_text_file(
                    target,
                    settings.max_trace_chars,
                    offset,
                    limit,
                    pattern,
                    context_before,
                    context_after,
                )
            )
        return result

    if inner:
        raise GitLabError("inner_path is only supported for zip artifacts")
    result["is_zip"] = False
    result.update(
        _read_text_file(
            artifact_file,
            settings.max_trace_chars,
            offset,
            limit,
            pattern,
            context_before,
            context_after,
        )
    )
    return result


@mcp.tool()
def gitlab_run_pipeline(project: str, ref: str) -> dict[str, Any]:
    """Create a pipeline on a branch/tag (write; api scope required).

    Never automatically retried. After a timeout, check pipelines before repeating.
    """
    if not ref.strip():
        raise ValueError("ref must be a non-empty branch or tag")
    data = client().post(f"projects/{encode_path_component(project)}/pipeline", {"ref": ref}).data
    return {
        "pipeline_id": data["id"],
        "status": data.get("status"),
        "ref": data.get("ref"),
        "web_url": data.get("web_url"),
    }


@mcp.tool()
def gitlab_retry_job(project: str, job_id: int) -> dict[str, Any]:
    """Retry one job (write; api scope required); returns the NEW job ID.

    Never automatically retried. After a timeout, inspect jobs before repeating.
    """
    if job_id < 1:
        raise ValueError("job_id must be positive")
    data = client().post(f"projects/{encode_path_component(project)}/jobs/{job_id}/retry").data
    return {
        "job_id": data["id"],
        "status": data.get("status"),
        "web_url": data.get("web_url"),
    }


@mcp.tool()
def gitlab_list_protected_branches(project: str) -> dict[str, Any]:
    """Read ALL protected branch rules, including wildcard names and access levels.

    Rules alone do not determine the cache suffix: check project CI settings and
    the pipeline context as well. All API pages are fetched automatically.
    """
    gitlab = client()
    items = []
    page = 1
    fields = (
        "name", "push_access_levels", "merge_access_levels", "unprotect_access_levels",
        "allow_force_push", "code_owner_approval_required", "inherited",
    )
    while True:
        result = gitlab.get_paginated(
            f"projects/{encode_path_component(project)}/protected_branches", page=page, per_page=100
        )
        items.extend({key: row[key] for key in fields if key in row} for row in result["items"])
        next_page = result["pagination"]["next_page"]
        if next_page is None:
            break
        if next_page <= page:
            raise GitLabError("GitLab returned non-advancing protected branch pagination")
        page = next_page
    return {"items": items, "total": len(items)}


CI_APPLICATION_SETTINGS = (
    "max_artifacts_size",
    "default_artifacts_expire_in",
    "keep_latest_artifact",
    "max_artifacts_content_include_size",
    "archive_builds_in_human_readable",
    "ci_max_total_yaml_size_bytes",
    "ci_max_includes",
)


@mcp.tool()
def gitlab_get_application_settings() -> dict[str, Any]:
    """Read an allowlisted CI/CD subset of instance settings (administrator required).

    Missing fields mean unavailable, NOT unlimited. Runner/object-storage cache
    upload limits and plan-specific artifact limits are outside this endpoint.
    """
    try:
        data = client().get("application/settings").data
    except GitLabError as exc:
        raise GitLabError(
            f"Cannot read application settings (administrator API access required): {exc}"
        ) from exc
    return {
        "settings": {key: data[key] for key in CI_APPLICATION_SETTINGS if key in data},
        "unavailable_fields": [key for key in CI_APPLICATION_SETTINGS if key not in data],
        "notes": [
            "max_artifacts_size is in MB; max_artifacts_content_include_size and "
            "ci_max_total_yaml_size_bytes are in bytes.",
            "Unavailable fields do not imply unlimited values.",
            "Runner/storage cache upload limits and plan-specific artifact limits "
            "are not exposed by this endpoint.",
        ],
    }


@mcp.tool()
def gitlab_retry_pipeline(project: str, pipeline_id: int) -> dict[str, Any]:
    """Retry failed or canceled jobs in a pipeline."""
    return client().post(
        f"projects/{encode_path_component(project)}/pipelines/{pipeline_id}/retry"
    ).data


@mcp.tool()
def gitlab_cancel_pipeline(project: str, pipeline_id: int) -> dict[str, Any]:
    """Cancel all cancelable jobs in a pipeline."""
    return client().post(
        f"projects/{encode_path_component(project)}/pipelines/{pipeline_id}/cancel"
    ).data


if __name__ == "__main__":
    mcp.run()

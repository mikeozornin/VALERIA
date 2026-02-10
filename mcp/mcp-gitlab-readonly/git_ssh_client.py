"""Safe local Git operations whose network transport is restricted to SSH."""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from urllib.parse import unquote, urlsplit

from gitlab_client import GitLabError


DEFAULT_GIT_TIMEOUT_SECONDS = 120
DEFAULT_MAX_GIT_OUTPUT_CHARS = 200_000
DEFAULT_SSH_CONNECT_TIMEOUT_SECONDS = 10
DEFAULT_SSH_CONNECTION_ATTEMPTS = 1
DEFAULT_SSH_SERVER_ALIVE_INTERVAL_SECONDS = 15
DEFAULT_SSH_SERVER_ALIVE_COUNT_MAX = 2
_SCP_SSH_URL = re.compile(
    r"^(?P<user>[A-Za-z0-9._-]+)@(?P<host>[A-Za-z0-9._-]+):(?P<path>[^\x00]+)$"
)


class GitLabGitError(GitLabError):
    """A safe error from a local Git or SSH operation."""


@dataclass(frozen=True)
class GitLabSSHSettings:
    repository_roots: tuple[Path, ...]
    allow_absolute_paths: bool
    ssh_host: str
    ssh_user: str
    ssh_port: int | None
    ssh_identity_file: Path | None
    ssh_known_hosts_file: Path | None
    timeout_seconds: int
    max_output_chars: int
    ssh_connect_timeout_seconds: int = DEFAULT_SSH_CONNECT_TIMEOUT_SECONDS
    ssh_connection_attempts: int = DEFAULT_SSH_CONNECTION_ATTEMPTS
    ssh_server_alive_interval_seconds: int = DEFAULT_SSH_SERVER_ALIVE_INTERVAL_SECONDS
    ssh_server_alive_count_max: int = DEFAULT_SSH_SERVER_ALIVE_COUNT_MAX

    @classmethod
    def from_env(cls, clone_root: Path, gitlab_base_url: str) -> "GitLabSSHSettings":
        roots_value = os.getenv("GITLAB_REPOSITORY_ROOTS", str(clone_root))
        roots = tuple(
            Path(value.strip()).expanduser().resolve()
            for value in roots_value.split(os.pathsep)
            if value.strip()
        )
        if not roots:
            raise GitLabGitError("GITLAB_REPOSITORY_ROOTS must contain at least one path")

        api_host = urlsplit(gitlab_base_url).hostname
        ssh_host = os.getenv("GITLAB_SSH_HOST", api_host or "").strip().lower()
        if not ssh_host or any(char.isspace() for char in ssh_host):
            raise GitLabGitError("GITLAB_SSH_HOST is invalid")

        ssh_user = os.getenv("GITLAB_SSH_USER", "git").strip()
        if not re.fullmatch(r"[A-Za-z0-9._-]+", ssh_user):
            raise GitLabGitError("GITLAB_SSH_USER is invalid")

        port_value = os.getenv("GITLAB_SSH_PORT", "").strip()
        ssh_port = int(port_value) if port_value else None
        if ssh_port is not None and not 1 <= ssh_port <= 65535:
            raise GitLabGitError("GITLAB_SSH_PORT must be between 1 and 65535")

        identity_value = os.getenv("GITLAB_SSH_IDENTITY_FILE", "").strip()
        identity = Path(identity_value).expanduser().resolve() if identity_value else None
        if identity is not None and not identity.is_file():
            raise GitLabGitError("GITLAB_SSH_IDENTITY_FILE does not exist or is not a file")

        known_hosts_value = os.getenv("GITLAB_SSH_KNOWN_HOSTS_FILE", "").strip()
        known_hosts = Path(known_hosts_value).expanduser().resolve() if known_hosts_value else None
        if known_hosts is not None and not known_hosts.is_file():
            raise GitLabGitError("GITLAB_SSH_KNOWN_HOSTS_FILE does not exist or is not a file")

        timeout_seconds = int(
            os.getenv("GITLAB_GIT_TIMEOUT_SECONDS", str(DEFAULT_GIT_TIMEOUT_SECONDS))
        )
        max_output_chars = int(
            os.getenv("GITLAB_MAX_GIT_OUTPUT_CHARS", str(DEFAULT_MAX_GIT_OUTPUT_CHARS))
        )
        ssh_connect_timeout_seconds = int(
            os.getenv(
                "GITLAB_SSH_CONNECT_TIMEOUT_SECONDS",
                str(DEFAULT_SSH_CONNECT_TIMEOUT_SECONDS),
            )
        )
        ssh_connection_attempts = int(
            os.getenv("GITLAB_SSH_CONNECTION_ATTEMPTS", str(DEFAULT_SSH_CONNECTION_ATTEMPTS))
        )
        ssh_server_alive_interval_seconds = int(
            os.getenv(
                "GITLAB_SSH_SERVER_ALIVE_INTERVAL_SECONDS",
                str(DEFAULT_SSH_SERVER_ALIVE_INTERVAL_SECONDS),
            )
        )
        ssh_server_alive_count_max = int(
            os.getenv(
                "GITLAB_SSH_SERVER_ALIVE_COUNT_MAX",
                str(DEFAULT_SSH_SERVER_ALIVE_COUNT_MAX),
            )
        )
        if timeout_seconds < 1:
            raise GitLabGitError("GITLAB_GIT_TIMEOUT_SECONDS must be positive")
        if max_output_chars < 1000:
            raise GitLabGitError("GITLAB_MAX_GIT_OUTPUT_CHARS must be at least 1000")
        if ssh_connect_timeout_seconds < 1:
            raise GitLabGitError("GITLAB_SSH_CONNECT_TIMEOUT_SECONDS must be positive")
        if ssh_connection_attempts < 1:
            raise GitLabGitError("GITLAB_SSH_CONNECTION_ATTEMPTS must be positive")
        if ssh_server_alive_interval_seconds < 1:
            raise GitLabGitError("GITLAB_SSH_SERVER_ALIVE_INTERVAL_SECONDS must be positive")
        if ssh_server_alive_count_max < 1:
            raise GitLabGitError("GITLAB_SSH_SERVER_ALIVE_COUNT_MAX must be positive")

        return cls(
            repository_roots=roots,
            allow_absolute_paths=os.getenv("GITLAB_ALLOW_ABSOLUTE_PATHS", "true").lower()
            in ("1", "true", "yes"),
            ssh_host=ssh_host,
            ssh_user=ssh_user,
            ssh_port=ssh_port,
            ssh_identity_file=identity,
            ssh_known_hosts_file=known_hosts,
            timeout_seconds=timeout_seconds,
            max_output_chars=max_output_chars,
            ssh_connect_timeout_seconds=ssh_connect_timeout_seconds,
            ssh_connection_attempts=ssh_connection_attempts,
            ssh_server_alive_interval_seconds=ssh_server_alive_interval_seconds,
            ssh_server_alive_count_max=ssh_server_alive_count_max,
        )


def _is_inside(path: Path, roots: Iterable[Path]) -> bool:
    return any(path == root or root in path.parents for root in roots)


def resolve_repository_path(
    roots: tuple[Path, ...], repository_path: str, allow_absolute_paths: bool = True
) -> Path:
    requested = Path(repository_path).expanduser()
    if requested.is_absolute():
        target = requested.resolve()
        if target == Path(target.anchor):
            raise GitLabGitError("repository_path cannot be a filesystem root")
        if not allow_absolute_paths and not _is_inside(target, roots):
            raise GitLabGitError("absolute repository_path is outside GITLAB_REPOSITORY_ROOTS")
        return target

    target = (roots[0] / requested).resolve()
    if not _is_inside(target, roots):
        raise GitLabGitError("repository_path is outside GITLAB_REPOSITORY_ROOTS")
    return target


def resolve_clone_destination(
    roots: tuple[Path, ...],
    project_path: str,
    destination: str | None,
    allow_absolute_paths: bool = True,
) -> Path:
    requested = Path(destination).expanduser() if destination else Path(*PurePosixPath(project_path).parts)
    if requested.is_absolute():
        target = requested.resolve()
        if target == Path(target.anchor):
            raise GitLabGitError("clone destination cannot be a filesystem root")
        if not allow_absolute_paths and not _is_inside(target, roots):
            raise GitLabGitError("absolute clone destination is outside GITLAB_REPOSITORY_ROOTS")
        return target

    target = (roots[0] / requested).resolve()
    if not _is_inside(target, roots) or target in roots:
        raise GitLabGitError("clone destination must be below one of GITLAB_REPOSITORY_ROOTS")
    return target


def validate_relative_paths(paths: list[str]) -> list[str]:
    if not paths:
        raise GitLabGitError("paths must contain at least one repository-relative path")
    result: list[str] = []
    for raw_path in paths:
        path = PurePosixPath(raw_path)
        if (
            not raw_path
            or raw_path.startswith(":")
            or path.is_absolute()
            or ".." in path.parts
            or ".git" in path.parts
            or "\x00" in raw_path
        ):
            raise GitLabGitError(f"Invalid repository-relative path: {raw_path!r}")
        result.append(str(path))
    return result


def validate_project_path(project: str) -> str:
    normalized = project.removesuffix(".git").strip("/")
    parts = PurePosixPath(normalized).parts
    if (
        len(parts) < 2
        or any(not re.fullmatch(r"[A-Za-z0-9._-]+", part) for part in parts)
        or any(part in (".", "..") for part in parts)
    ):
        raise GitLabGitError("project must be a namespace/project path, not a numeric ID or URL")
    return "/".join(parts)


def parse_ssh_repository_url(url: str) -> tuple[str, str, int | None, str]:
    """Return user, host, port, and namespace/project from an SSH Git URL."""
    match = _SCP_SSH_URL.fullmatch(url)
    if match:
        user = match.group("user")
        host = match.group("host").lower()
        port = None
        repository = match.group("path")
    else:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "ssh"
            or not parsed.username
            or not parsed.hostname
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise GitLabGitError("Git remote must use an SSH URL")
        user = unquote(parsed.username)
        host = parsed.hostname.lower()
        port = parsed.port
        repository = unquote(parsed.path).lstrip("/")

    normalized = repository.removesuffix(".git").strip("/")
    parts = PurePosixPath(normalized).parts
    if not normalized or ".." in parts or normalized.startswith("-"):
        raise GitLabGitError("SSH Git remote contains an invalid repository path")
    return user, host, port, normalized


class GitLabSSHClient:
    """Operate on local repositories while allowing network Git only over SSH."""

    def __init__(self, settings: GitLabSSHSettings):
        self.settings = settings

    def _ssh_url_for_project(self, project: str) -> tuple[str, str]:
        project_path = validate_project_path(project)
        if self.settings.ssh_port in (None, 22):
            url = f"{self.settings.ssh_user}@{self.settings.ssh_host}:{project_path}.git"
        else:
            url = (
                f"ssh://{self.settings.ssh_user}@{self.settings.ssh_host}:"
                f"{self.settings.ssh_port}/{project_path}.git"
            )
        self._validate_ssh_url(url, project_path)
        return project_path, url

    def _environment(self) -> dict[str, str]:
        environment = os.environ.copy()
        for name in (
            "GIT_DIR",
            "GIT_WORK_TREE",
            "GIT_INDEX_FILE",
            "GIT_OBJECT_DIRECTORY",
            "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        ):
            environment.pop(name, None)
        ssh_command = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"ConnectTimeout={self.settings.ssh_connect_timeout_seconds}",
            "-o",
            f"ConnectionAttempts={self.settings.ssh_connection_attempts}",
            "-o",
            f"ServerAliveInterval={self.settings.ssh_server_alive_interval_seconds}",
            "-o",
            f"ServerAliveCountMax={self.settings.ssh_server_alive_count_max}",
        ]
        if self.settings.ssh_port is not None:
            ssh_command.extend(["-p", str(self.settings.ssh_port)])
        if self.settings.ssh_identity_file is not None:
            ssh_command.extend(["-i", str(self.settings.ssh_identity_file), "-o", "IdentitiesOnly=yes"])
        if self.settings.ssh_known_hosts_file is not None:
            ssh_command.extend(
                ["-o", f"UserKnownHostsFile={self.settings.ssh_known_hosts_file}"]
            )
        environment.update(
            {
                "GIT_SSH_COMMAND": shlex.join(ssh_command),
                "GIT_SSH_VARIANT": "ssh",
                "GIT_TERMINAL_PROMPT": "0",
                "LC_ALL": "C",
            }
        )
        return environment

    def _run(
        self,
        arguments: list[str],
        repository: Path | None = None,
        allowed_returncodes: tuple[int, ...] = (0,),
    ) -> subprocess.CompletedProcess[str]:
        if not shutil.which("git"):
            raise GitLabGitError("git executable was not found")
        command = [
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "credential.helper=",
        ]
        if repository is not None:
            command.extend(["-C", str(repository)])
        command.extend(arguments)
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.settings.timeout_seconds,
                env=self._environment(),
            )
        except subprocess.TimeoutExpired as exc:
            raise GitLabGitError("git operation timed out") from exc
        if completed.returncode not in allowed_returncodes:
            detail = (completed.stderr or completed.stdout)[-4000:]
            raise GitLabGitError(f"git command failed ({completed.returncode}): {detail.strip()}")
        return completed

    def _output(self, completed: subprocess.CompletedProcess[str]) -> dict[str, Any]:
        output = completed.stdout + completed.stderr
        limit = self.settings.max_output_chars
        return {
            "output": output[:limit],
            "truncated": len(output) > limit,
            "returncode": completed.returncode,
        }

    def _validate_ssh_url(self, url: str, expected_project: str | None = None) -> str:
        user, host, port, project = parse_ssh_repository_url(url)
        if user != self.settings.ssh_user or host != self.settings.ssh_host:
            raise GitLabGitError(
                f"SSH remote must use {self.settings.ssh_user}@{self.settings.ssh_host}"
            )
        expected_port = self.settings.ssh_port or 22
        if port is not None and port != expected_port:
            raise GitLabGitError("SSH remote uses an unexpected port")
        if expected_project is not None and project != expected_project.removesuffix(".git").strip("/"):
            raise GitLabGitError("SSH URL contains an unexpected project path")
        return project

    def _repository(self, repository_path: str) -> Path:
        repository = resolve_repository_path(
            self.settings.repository_roots,
            repository_path,
            self.settings.allow_absolute_paths,
        )
        if not repository.is_dir():
            raise GitLabGitError(f"Repository directory does not exist: {repository}")
        result = self._run(["rev-parse", "--show-toplevel"], repository)
        top_level = Path(result.stdout.strip()).resolve()
        if top_level != repository:
            raise GitLabGitError("repository_path must point to the Git worktree root")
        unsafe = self._run(
            [
                "config",
                "--local",
                "--get-regexp",
                (
                    r"^(core\.(hooksPath|fsmonitor|sshCommand)|"
                    r"gpg(\.[^.]+)?\.program|"
                    r"diff\..*\.command|filter\..*\.(clean|smudge|process)|"
                    r"merge\..*\.driver|remote\..*\.(uploadpack|receivepack))$"
                ),
            ],
            repository,
            (0, 1),
        )
        if unsafe.returncode == 0:
            raise GitLabGitError("Repository contains local Git configuration that can execute commands")
        return repository

    def _current_branch(self, repository: Path) -> str:
        result = self._run(["symbolic-ref", "--quiet", "--short", "HEAD"], repository)
        branch = result.stdout.strip()
        self._validate_branch(branch)
        return branch

    def _validate_branch(self, branch: str) -> None:
        if not branch or branch.startswith("-") or "\x00" in branch:
            raise GitLabGitError("Invalid branch name")
        self._run(["check-ref-format", "--branch", branch])

    def _validate_remote(self, repository: Path, remote: str, push: bool = False) -> dict[str, str]:
        if not re.fullmatch(r"[A-Za-z0-9._-]+", remote):
            raise GitLabGitError("Invalid remote name")
        arguments = ["remote", "get-url"]
        if push:
            arguments.append("--push")
        arguments.append("--all")
        arguments.append(remote)
        urls = self._run(arguments, repository).stdout.splitlines()
        if not urls:
            raise GitLabGitError("Git remote has no configured URL")
        projects = {self._validate_ssh_url(url) for url in urls}
        if len(projects) != 1:
            raise GitLabGitError("All Git remote URLs must point to the same project")
        return {"remote": remote, "url": urls[0], "project": projects.pop()}

    def clone_repository(
        self,
        project: str,
        destination: str | None = None,
        ref: str | None = None,
        depth: int = 0,
    ) -> dict[str, Any]:
        if depth < 0 or depth > 1000:
            raise GitLabGitError("depth must be between 0 (full clone) and 1000")
        project_path, repository_url = self._ssh_url_for_project(project)
        target = resolve_clone_destination(
            self.settings.repository_roots,
            project_path,
            destination,
            self.settings.allow_absolute_paths,
        )
        if target.exists():
            raise GitLabGitError(f"Clone destination already exists: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)

        arguments = ["clone", "--origin", "origin"]
        if depth:
            arguments.extend(["--depth", str(depth)])
        if ref:
            if ref.startswith("-") or "\x00" in ref:
                raise GitLabGitError("Invalid ref")
            arguments.extend(["--branch", ref, "--single-branch"])
        arguments.extend(["--", repository_url, str(target)])
        try:
            completed = self._run(arguments)
        except GitLabGitError:
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            raise
        return {
            "project": project_path,
            "destination": str(target),
            "ref": ref,
            "depth": depth,
            "remote": repository_url,
            "git_transport": "ssh",
            **self._output(completed),
        }

    def configure_ssh_remote(
        self, repository_path: str, project: str, remote: str = "origin"
    ) -> dict[str, Any]:
        repository = self._repository(repository_path)
        if not re.fullmatch(r"[A-Za-z0-9._-]+", remote):
            raise GitLabGitError("Invalid remote name")
        project_path, repository_url = self._ssh_url_for_project(project)
        remotes = self._run(["remote"], repository).stdout.splitlines()
        if remote in remotes:
            self._run(
                ["config", "--local", "--unset-all", f"remote.{remote}.url"],
                repository,
                (0, 5),
            )
            self._run(
                ["config", "--local", "--add", f"remote.{remote}.url", repository_url],
                repository,
            )
            self._run(
                ["config", "--local", "--unset-all", f"remote.{remote}.pushurl"],
                repository,
                (0, 5),
            )
            self._run(
                ["config", "--local", "--add", f"remote.{remote}.pushurl", repository_url],
                repository,
            )
        else:
            self._run(["remote", "add", remote, repository_url], repository)
        return {
            "repository_path": str(repository),
            "project": project_path,
            "remote": remote,
            "fetch_url": repository_url,
            "push_url": repository_url,
            "git_transport": "ssh",
        }

    def status(self, repository_path: str) -> dict[str, Any]:
        repository = self._repository(repository_path)
        completed = self._run(["status", "--short", "--branch", "--untracked-files=all"], repository)
        return {"repository_path": str(repository), **self._output(completed)}

    def diff(
        self, repository_path: str, staged: bool = False, paths: list[str] | None = None
    ) -> dict[str, Any]:
        repository = self._repository(repository_path)
        arguments = ["diff", "--no-ext-diff", "--no-textconv", "--unified=3"]
        if staged:
            arguments.append("--cached")
        if paths:
            arguments.extend(["--", *validate_relative_paths(paths)])
        completed = self._run(arguments, repository)
        return {"repository_path": str(repository), "staged": staged, **self._output(completed)}

    def switch_branch(
        self,
        repository_path: str,
        branch: str,
        create: bool = False,
        start_point: str | None = None,
    ) -> dict[str, Any]:
        repository = self._repository(repository_path)
        self._validate_branch(branch)
        arguments = ["switch"]
        if create:
            arguments.extend(["-c", branch])
            if start_point:
                if start_point.startswith("-") or "\x00" in start_point:
                    raise GitLabGitError("Invalid start_point")
                arguments.append(start_point)
        else:
            if start_point is not None:
                raise GitLabGitError("start_point is only valid when create=true")
            arguments.append(branch)
        completed = self._run(arguments, repository)
        return {
            "repository_path": str(repository),
            "branch": branch,
            "created": create,
            **self._output(completed),
        }

    def stage(self, repository_path: str, paths: list[str]) -> dict[str, Any]:
        repository = self._repository(repository_path)
        safe_paths = validate_relative_paths(paths)
        self._run(["add", "-A", "--", *safe_paths], repository)
        staged = self._run(["diff", "--cached", "--name-status"], repository)
        return {"repository_path": str(repository), **self._output(staged)}

    def unstage(self, repository_path: str, paths: list[str]) -> dict[str, Any]:
        repository = self._repository(repository_path)
        safe_paths = validate_relative_paths(paths)
        self._run(["restore", "--staged", "--", *safe_paths], repository)
        staged = self._run(["diff", "--cached", "--name-status"], repository)
        return {"repository_path": str(repository), **self._output(staged)}

    def commit(
        self, repository_path: str, message: str, paths: list[str] | None = None
    ) -> dict[str, Any]:
        repository = self._repository(repository_path)
        clean_message = message.strip()
        if not clean_message or len(clean_message) > 10_000 or "\x00" in clean_message:
            raise GitLabGitError("Commit message must contain between 1 and 10000 characters")

        if paths:
            already_staged = self._run(["diff", "--cached", "--quiet"], repository, (0, 1))
            if already_staged.returncode == 1:
                raise GitLabGitError(
                    "The repository already has staged changes; commit or unstage them before using paths"
                )
            self._run(["add", "-A", "--", *validate_relative_paths(paths)], repository)

        staged = self._run(["diff", "--cached", "--quiet"], repository, (0, 1))
        if staged.returncode == 0:
            raise GitLabGitError("There are no staged changes to commit")

        completed = self._run(["commit", "-m", clean_message], repository)
        commit_sha = self._run(["rev-parse", "HEAD"], repository).stdout.strip()
        summary = self._run(["show", "--stat", "--oneline", "--decorate", "--no-renames", "HEAD"], repository)
        return {
            "repository_path": str(repository),
            "commit": commit_sha,
            "summary": summary.stdout[: self.settings.max_output_chars],
            **self._output(completed),
        }

    def fetch(
        self, repository_path: str, remote: str = "origin", prune: bool = True, tags: bool = True
    ) -> dict[str, Any]:
        repository = self._repository(repository_path)
        remote_info = self._validate_remote(repository, remote)
        arguments = ["fetch"]
        if prune:
            arguments.append("--prune")
        arguments.append("--tags" if tags else "--no-tags")
        arguments.extend(["--", remote])
        completed = self._run(arguments, repository)
        return {
            "repository_path": str(repository),
            **remote_info,
            "git_transport": "ssh",
            **self._output(completed),
        }

    def pull(self, repository_path: str, remote: str = "origin") -> dict[str, Any]:
        repository = self._repository(repository_path)
        remote_info = self._validate_remote(repository, remote)
        branch = self._current_branch(repository)
        completed = self._run(["pull", "--ff-only", "--", remote, branch], repository)
        return {
            "repository_path": str(repository),
            "branch": branch,
            **remote_info,
            "git_transport": "ssh",
            **self._output(completed),
        }

    def push(
        self, repository_path: str, remote: str = "origin", branch: str | None = None
    ) -> dict[str, Any]:
        repository = self._repository(repository_path)
        remote_info = self._validate_remote(repository, remote, push=True)
        current_branch = self._current_branch(repository)
        target_branch = branch or current_branch
        self._validate_branch(target_branch)
        completed = self._run(
            [
                "push",
                "--porcelain",
                "--set-upstream",
                "--",
                remote,
                f"HEAD:refs/heads/{target_branch}",
            ],
            repository,
        )
        return {
            "repository_path": str(repository),
            "source_branch": current_branch,
            "target_branch": target_branch,
            **remote_info,
            "git_transport": "ssh",
            "force": False,
            **self._output(completed),
        }

    def commit_and_push(
        self,
        repository_path: str,
        message: str,
        paths: list[str] | None = None,
        remote: str = "origin",
        branch: str | None = None,
    ) -> dict[str, Any]:
        commit_result = self.commit(repository_path, message, paths)
        try:
            push_result = self.push(repository_path, remote, branch)
        except GitLabGitError as exc:
            raise GitLabGitError(
                f"Commit {commit_result['commit']} was created locally, but push failed: {exc}"
            ) from exc
        return {"commit_result": commit_result, "push_result": push_result}

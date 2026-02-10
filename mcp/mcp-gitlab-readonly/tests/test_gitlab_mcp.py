import base64
import inspect
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from git_ssh_client import (
    GitLabGitError,
    GitLabSSHClient,
    GitLabSSHSettings,
    parse_ssh_repository_url,
    resolve_clone_destination,
    resolve_repository_path,
    validate_project_path,
    validate_relative_paths,
)
from gitlab_client import GitLabClient, GitLabError, GitLabSettings, encode_path_component


class Response:
    def __init__(self, data):
        self.data = data


class GitLabMCPTests(unittest.TestCase):
    def api_settings(self, clone_root: Path) -> GitLabSettings:
        return GitLabSettings(
            base_url="https://gitlab.example.com",
            token="test-token",
            clone_root=clone_root,
            timeout_seconds=1,
            clone_timeout_seconds=1,
            max_file_bytes=1000,
            max_trace_chars=1000,
            ca_bundle=None,
        )

    def ssh_settings(self, root: Path) -> GitLabSSHSettings:
        return GitLabSSHSettings(
            repository_roots=(root.resolve(),),
            allow_absolute_paths=True,
            ssh_host="gitlab.example.com",
            ssh_user="git",
            ssh_port=None,
            ssh_identity_file=None,
            ssh_known_hosts_file=None,
            timeout_seconds=10,
            max_output_chars=10000,
        )

    def git_client(self, root: Path) -> GitLabSSHClient:
        return GitLabSSHClient(self.ssh_settings(root))

    def init_repository(self, root: Path) -> Path:
        repository = root / "repo"
        subprocess.run(["git", "init", "-q", "-b", "main", str(repository)], check=True)
        subprocess.run(["git", "-C", str(repository), "config", "user.name", "Test User"], check=True)
        subprocess.run(
            ["git", "-C", str(repository), "config", "user.email", "test@example.com"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(repository), "config", "commit.gpgSign", "false"],
            check=True,
        )
        return repository

    def test_project_paths_are_encoded_as_one_component(self):
        self.assertEqual(encode_path_component("group/project"), "group%2Fproject")

    def test_ssh_url_formats_are_supported(self):
        expected = ("git", "gitlab.example.com", None, "group/project")
        self.assertEqual(
            parse_ssh_repository_url("git@gitlab.example.com:group/project.git"), expected
        )
        self.assertEqual(
            parse_ssh_repository_url("ssh://git@gitlab.example.com/group/project.git"), expected
        )

    def test_https_git_remote_is_rejected(self):
        with self.assertRaises(GitLabGitError):
            parse_ssh_repository_url("https://gitlab.example.com/group/project.git")

    def test_repository_path_must_stay_in_allowed_roots(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "allowed"
            root.mkdir()
            self.assertEqual(resolve_repository_path((root,), "team/repo"), root / "team/repo")
            with self.assertRaises(GitLabGitError):
                resolve_repository_path((root,), "../outside")

    def test_clone_destination_accepts_exact_absolute_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "allowed"
            root.mkdir()
            self.assertEqual(
                resolve_clone_destination((root,), "group/project", None), root / "group/project"
            )
            outside = Path(directory) / "outside"
            self.assertEqual(
                resolve_clone_destination((root,), "group/project", str(outside)), outside
            )

    def test_absolute_clone_destination_can_be_restricted_by_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "allowed"
            root.mkdir()
            with self.assertRaises(GitLabGitError):
                resolve_clone_destination(
                    (root,),
                    "group/project",
                    str(Path(directory) / "outside"),
                    allow_absolute_paths=False,
                )

    def test_pathspec_magic_and_git_directory_are_rejected(self):
        for value in ("../secret", "/absolute", ":(glob)**", ".git/config"):
            with self.subTest(value=value), self.assertRaises(GitLabGitError):
                validate_relative_paths([value])

    def test_project_must_be_namespace_path(self):
        self.assertEqual(validate_project_path("group/team/project.git"), "group/team/project")
        for value in ("123", "https://gitlab.example.com/group/project", "../project"):
            with self.subTest(value=value), self.assertRaises(GitLabGitError):
                validate_project_path(value)

    def test_clone_uses_gitlab_ssh_url(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gitlab = self.git_client(root)
            gitlab._run = Mock(return_value=subprocess.CompletedProcess([], 0, "cloned\n", ""))
            result = gitlab.clone_repository("group/project", "project")
            command = gitlab._run.call_args.args[0]
            self.assertIn("git@gitlab.example.com:group/project.git", command)
            self.assertNotIn("https://gitlab.example.com/group/project.git", command)
            self.assertEqual(result["git_transport"], "ssh")

    def test_fetch_rejects_repository_with_https_remote_before_network(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = self.init_repository(root)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repository),
                    "remote",
                    "add",
                    "origin",
                    "https://gitlab.example.com/group/project.git",
                ],
                check=True,
            )
            with self.assertRaisesRegex(GitLabGitError, "SSH URL"):
                self.git_client(root).fetch(str(repository))

    def test_configure_remote_sets_fetch_and_push_to_ssh(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = self.init_repository(root)
            subprocess.run(
                ["git", "-C", str(repository), "remote", "add", "origin", "https://example.test/a"],
                check=True,
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repository),
                    "config",
                    "--add",
                    "remote.origin.pushurl",
                    "https://example.test/b",
                ],
                check=True,
            )
            gitlab = self.git_client(root)
            gitlab.configure_ssh_remote(str(repository), "group/project")
            fetch_url = subprocess.run(
                ["git", "-C", str(repository), "remote", "get-url", "--all", "origin"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            push_url = subprocess.run(
                ["git", "-C", str(repository), "remote", "get-url", "--push", "--all", "origin"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            self.assertEqual(fetch_url, "git@gitlab.example.com:group/project.git")
            self.assertEqual(push_url, fetch_url)

    def test_push_rejects_any_secondary_https_push_url(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = self.init_repository(root)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repository),
                    "remote",
                    "add",
                    "origin",
                    "git@gitlab.example.com:group/project.git",
                ],
                check=True,
            )
            for url in (
                "git@gitlab.example.com:group/project.git",
                "https://gitlab.example.com/group/project.git",
            ):
                subprocess.run(
                    [
                        "git",
                        "-C",
                        str(repository),
                        "config",
                        "--add",
                        "remote.origin.pushurl",
                        url,
                    ],
                    check=True,
                )
            with self.assertRaisesRegex(GitLabGitError, "SSH URL"):
                self.git_client(root).push(str(repository))

    def test_commit_disables_repository_hooks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = self.init_repository(root)
            marker = root / "hook-ran"
            hooks = repository / ".git" / "hooks"
            hook = hooks / "pre-commit"
            hook.write_text(f"#!/bin/sh\ntouch '{marker}'\n", encoding="utf-8")
            hook.chmod(0o700)
            (repository / "file.txt").write_text("content\n", encoding="utf-8")

            result = self.git_client(root).commit(
                str(repository), "Initial commit", paths=["file.txt"]
            )
            self.assertEqual(len(result["commit"]), 40)
            self.assertFalse(marker.exists())

    def test_commit_inherits_git_signing_configuration(self):
        if shutil.which("ssh-keygen") is None:
            self.skipTest("ssh-keygen is required for the signing test")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = self.init_repository(root)
            signing_key = root / "signing-key"
            subprocess.run(
                ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(signing_key)],
                check=True,
            )
            for name, value in (
                ("commit.gpgSign", "true"),
                ("gpg.format", "ssh"),
                ("user.signingKey", str(signing_key)),
            ):
                subprocess.run(
                    ["git", "-C", str(repository), "config", name, value],
                    check=True,
                )
            (repository / "file.txt").write_text("signed content\n", encoding="utf-8")

            result = self.git_client(root).commit(
                str(repository), "Signed commit", paths=["file.txt"]
            )

            commit = subprocess.run(
                ["git", "-C", str(repository), "cat-file", "commit", result["commit"]],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            self.assertIn("gpgsig -----BEGIN SSH SIGNATURE-----", commit)

    def test_repository_rejects_local_signing_program(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = self.init_repository(root)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repository),
                    "config",
                    "gpg.x509.program",
                    "/tmp/untrusted-signer",
                ],
                check=True,
            )

            with self.assertRaisesRegex(GitLabGitError, "execute commands"):
                self.git_client(root).status(str(repository))

    def test_empty_text_file_is_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            client = GitLabClient(self.api_settings(Path(directory)))

            class FileResponse:
                data = {"content": base64.b64encode(b"").decode(), "size": 0}

            client.get = lambda *args, **kwargs: FileResponse()
            result = client.get_file("group/project", "empty.txt", "HEAD")
            self.assertEqual(result["content"], "")
            self.assertEqual(result["line_range"], {"start": 0, "end": 0, "total": 0})

    def test_rest_writes_use_https_api_and_json(self):
        with tempfile.TemporaryDirectory() as directory:
            client = GitLabClient(self.api_settings(Path(directory)))
            response = Mock(
                status_code=201,
                content=b'{"iid":1}',
                is_redirect=False,
                headers={},
            )
            response.json.return_value = {"iid": 1}
            session = MagicMock()
            session.__enter__.return_value = session
            session.request.return_value = response
            with patch("gitlab_client.httpx.Client", return_value=session) as client_factory:
                result = client.post("projects/1/issues", {"title": "Test", "description": None})
            self.assertEqual(result.data, {"iid": 1})
            request = session.request
            self.assertEqual(request.call_args.args[0], "POST")
            self.assertEqual(request.call_args.kwargs["json"], {"title": "Test"})
            self.assertTrue(request.call_args.args[1].startswith("https://gitlab.example.com/api/v4/"))
            self.assertFalse(client_factory.call_args.kwargs["trust_env"])

    def test_server_exports_git_workflow_tools(self):
        from server import gitlab_connection_info, mcp

        tools = set(mcp._tool_manager._tools)
        required = {
            "gitlab_clone_repository",
            "gitlab_fetch",
            "gitlab_pull",
            "gitlab_commit",
            "gitlab_push",
            "gitlab_commit_and_push",
            "gitlab_create_merge_request",
            "gitlab_create_issue",
        }
        self.assertTrue(required <= tools)
        self.assertFalse(inspect.iscoroutinefunction(gitlab_connection_info))
        self.assertTrue(mcp._tool_manager._tools["gitlab_connection_info"].is_async)

    def test_registered_sync_tool_runs_in_worker_thread(self):
        from server import NonBlockingFastMCP

        test_mcp = NonBlockingFastMCP("thread-test")
        caller_thread = threading.get_ident()

        @test_mcp.tool()
        def worker_thread_id() -> int:
            return threading.get_ident()

        registered = test_mcp._tool_manager._tools["worker_thread_id"]

        async def call_tool() -> int:
            return await registered.fn()

        import anyio

        self.assertNotEqual(anyio.run(call_tool), caller_thread)

    def test_long_git_tool_does_not_block_other_tools(self):
        import anyio

        from server import NonBlockingFastMCP

        test_mcp = NonBlockingFastMCP("concurrency-test")
        git_started = threading.Event()
        release_git = threading.Event()

        @test_mcp.tool()
        def gitlab_fetch() -> str:
            git_started.set()
            release_git.wait(timeout=1)
            return "git-finished"

        @test_mcp.tool()
        def connection_info() -> str:
            return "responsive"

        git_tool = test_mcp._tool_manager._tools["gitlab_fetch"]
        info_tool = test_mcp._tool_manager._tools["connection_info"]

        async def call_tools() -> str:
            try:
                async with anyio.create_task_group() as task_group:
                    task_group.start_soon(git_tool.fn)
                    for _ in range(100):
                        if git_started.is_set():
                            break
                        await anyio.sleep(0.001)
                    else:
                        raise AssertionError("Git worker did not start")
                    with anyio.fail_after(0.2):
                        result = await info_tool.fn()
                    release_git.set()
                    return result
            finally:
                release_git.set()

        self.assertEqual(anyio.run(call_tools), "responsive")

    def test_ssh_environment_has_bounded_connection_options(self):
        with tempfile.TemporaryDirectory() as directory:
            environment = self.git_client(Path(directory))._environment()

        ssh_command = environment["GIT_SSH_COMMAND"]
        self.assertIn("ConnectTimeout=10", ssh_command)
        self.assertIn("ConnectionAttempts=1", ssh_command)
        self.assertIn("ServerAliveInterval=15", ssh_command)
        self.assertIn("ServerAliveCountMax=2", ssh_command)

    def test_list_pipelines_filters_by_exact_commit_sha(self):
        from server import gitlab_list_pipelines

        gitlab = Mock()
        gitlab.get_paginated.return_value = {"items": []}
        commit_sha = "0123456789abcdef0123456789abcdef01234567"
        with patch("server.client", return_value=gitlab):
            result = gitlab_list_pipelines("group/project", sha=commit_sha)

        self.assertEqual(result, {"items": []})
        gitlab.get_paginated.assert_called_once_with(
            "projects/group%2Fproject/pipelines",
            {
                "status": None,
                "ref": None,
                "username": None,
                "source": None,
                "sha": commit_sha,
                "order_by": "id",
                "sort": "desc",
            },
            1,
            50,
        )

    def test_get_job_trace_saves_full_trace_and_returns_preview(self):
        from server import gitlab_get_job_trace

        with tempfile.TemporaryDirectory() as directory:
            base_settings = self.api_settings(Path(directory) / "clone-root")
            trace_dir = Path(directory) / "traces"
            settings = GitLabSettings(
                base_url=base_settings.base_url,
                token=base_settings.token,
                clone_root=base_settings.clone_root,
                timeout_seconds=base_settings.timeout_seconds,
                clone_timeout_seconds=base_settings.clone_timeout_seconds,
                max_file_bytes=base_settings.max_file_bytes,
                max_trace_chars=5,
                ca_bundle=base_settings.ca_bundle,
                trace_output_dir=trace_dir,
            )
            gitlab = Mock()
            gitlab.settings = settings
            gitlab.get.return_value = Response("0123456789")

            with patch("server.client", return_value=gitlab):
                result = gitlab_get_job_trace("group/project", 123)

            trace_file = Path(result["trace_file"])
            self.assertEqual(result["trace"], "01234")
            self.assertTrue(result["truncated"])
            self.assertEqual(result["characters"], 10)
            self.assertEqual(result["returned_characters"], 5)
            self.assertEqual(trace_file.read_text(encoding="utf-8"), "0123456789")
            self.assertEqual(trace_file.parent, trace_dir)
            self.assertIn(str(trace_file), result["full_trace_available_at"])

    def test_token_compatibility_environment_variable(self):
        old_token = os.environ.pop("GITLAB_TOKEN", None)
        old_read_token = os.environ.pop("GITLAB_READ_TOKEN", None)
        try:
            os.environ["GITLAB_READ_TOKEN"] = "legacy-token"
            self.assertEqual(GitLabSettings.from_env().token, "legacy-token")
        finally:
            os.environ.pop("GITLAB_READ_TOKEN", None)
            if old_token is not None:
                os.environ["GITLAB_TOKEN"] = old_token
            if old_read_token is not None:
                os.environ["GITLAB_READ_TOKEN"] = old_read_token

    def test_gitlab_com_is_the_default_instance(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = GitLabSettings.from_env(require_token=False)
            ssh_settings = GitLabSSHSettings.from_env(
                settings.clone_root, settings.base_url
            )

        self.assertEqual(settings.base_url, "https://gitlab.com")
        self.assertEqual(ssh_settings.ssh_host, "gitlab.com")

    def test_self_managed_instance_is_configurable(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "GITLAB_URL": "https://code.example.net",
                "GITLAB_CLONE_ROOT": directory,
            },
            clear=True,
        ):
            settings = GitLabSettings.from_env(require_token=False)
            ssh_settings = GitLabSSHSettings.from_env(
                settings.clone_root, settings.base_url
            )

        self.assertEqual(settings.base_url, "https://code.example.net")
        self.assertEqual(ssh_settings.ssh_host, "code.example.net")

    def test_artifact_redirect_host_allowlist_environment_variable(self):
        with patch.dict(
            os.environ,
            {
                "GITLAB_TOKEN": "test-token",
                "GITLAB_ARTIFACT_REDIRECT_HOSTS": (
                    " storage.example.net,artifacts.example.com,storage.example.net "
                ),
            },
        ):
            settings = GitLabSettings.from_env()
        self.assertEqual(
            settings.artifact_redirect_hosts,
            ("storage.example.net", "artifacts.example.com"),
        )

    def test_artifact_redirect_host_allowlist_rejects_urls(self):
        with (
            patch.dict(
                os.environ,
                {
                    "GITLAB_TOKEN": "test-token",
                    "GITLAB_ARTIFACT_REDIRECT_HOSTS": "https://artifacts.example.com/path",
                },
            ),
            self.assertRaises(GitLabError),
        ):
            GitLabSettings.from_env()


if __name__ == "__main__":
    unittest.main()

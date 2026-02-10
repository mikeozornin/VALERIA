import asyncio
import io
import json
import tempfile
import unittest
import zipfile
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import httpx
import test_gitlab_mcp as fixtures
from gitlab_client import GitLabClient, GitLabError, GitLabResponse
from server import (
    gitlab_get_application_settings,
    gitlab_get_job_artifact,
    gitlab_get_job_trace,
    gitlab_get_pipeline_graph,
    gitlab_list_pipeline_bridges,
    gitlab_list_pipeline_jobs,
    gitlab_list_protected_branches,
    gitlab_retry_job,
    gitlab_run_pipeline,
    mcp,
)
from trace_selection import select_trace


class CIToolsTests(unittest.TestCase):
    def test_write_routes_and_minimal_results(self):
        api = Mock()
        api.post.return_value = GitLabResponse(
            {
                "id": 42,
                "status": "pending",
                "ref": "feature/a",
                "web_url": "url",
                "user": {"private": "irrelevant"},
            },
            {},
        )
        with patch("server.client", return_value=api):
            self.assertEqual(
                gitlab_run_pipeline("group/a b", "feature/a"),
                {
                    "pipeline_id": 42,
                    "status": "pending",
                    "ref": "feature/a",
                    "web_url": "url",
                },
            )
            api.post.assert_called_once_with(
                "projects/group%2Fa%20b/pipeline", {"ref": "feature/a"}
            )
            api.reset_mock()
            self.assertEqual(
                gitlab_retry_job("group/a b", 12),
                {
                    "job_id": 42,
                    "status": "pending",
                    "web_url": "url",
                },
            )
            api.post.assert_called_once_with("projects/group%2Fa%20b/jobs/12/retry")

    def test_invalid_write_arguments_do_not_call_api(self):
        with patch("server.client") as factory:
            with self.assertRaises(ValueError):
                gitlab_run_pipeline("project", "  ")
            with self.assertRaises(ValueError):
                gitlab_retry_job("project", 0)
            factory.assert_not_called()

    def test_post_timeout_is_not_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            api = GitLabClient(fixtures.GitLabMCPTests().api_settings(Path(directory)))
            for tool, args in ((gitlab_run_pipeline, ("1", "main")), (gitlab_retry_job, ("1", 42))):
                with (
                    self.subTest(tool=tool.__name__),
                    patch("gitlab_client.httpx.Client") as factory,
                ):
                    request = factory.return_value.__enter__.return_value.request
                    request.side_effect = httpx.ReadTimeout("timed out")
                    with patch("server.client", return_value=api), self.assertRaises(GitLabError):
                        tool(*args)
                    request.assert_called_once()

    def test_api_errors_keep_status_and_redact_token(self):
        with tempfile.TemporaryDirectory() as directory:
            api = GitLabClient(fixtures.GitLabMCPTests().api_settings(Path(directory)))
            for status in (400, 401, 403, 404, 429):
                with self.subTest(status=status), patch("gitlab_client.httpx.Client") as factory:
                    request = factory.return_value.__enter__.return_value.request
                    request.return_value = httpx.Response(status, text="error test-token")
                    with (
                        patch("server.client", return_value=api),
                        self.assertRaises(GitLabError) as error,
                    ):
                        gitlab_get_application_settings()
                    self.assertIn(str(status), str(error.exception))
                    self.assertIn("administrator", str(error.exception))
                    self.assertNotIn("test-token", str(error.exception))
                    request.assert_called_once()

    def test_full_and_compact_jobs_preserve_pagination_and_filters(self):
        original = {
            "items": [
                {
                    "id": 1,
                    "status": "success",
                    "duration": 0,
                    "name": "build",
                    "commit": {"huge": "x"},
                },
                {
                    "id": 2,
                    "status": "running",
                    "duration": None,
                    "name": "test\nnext",
                    "runner": {},
                },
            ],
            "pagination": {"page": 2, "next_page": 3},
        }
        api = Mock()
        api.get_paginated.return_value = original
        with patch("server.client", return_value=api):
            self.assertEqual(gitlab_list_pipeline_jobs("team/repo", 7), original)
            result = gitlab_list_pipeline_jobs("team/repo", 7, ["running"], True, 2, 20, True)
        api.get_paginated.assert_called_with(
            "projects/team%2Frepo/pipelines/7/jobs",
            {"scope[]": ["running"], "include_retried": "true"},
            2,
            20,
        )
        self.assertEqual(
            result["items"], ['1 | success | 0 | "build"', '2 | running | — | "test\\nnext"']
        )
        self.assertEqual(result["pagination"], original["pagination"])
        self.assertNotIn("commit", json.dumps(result))
        self.assertIn("commit", original["items"][0])

    def test_empty_compact_page(self):
        api = Mock()
        api.get_paginated.return_value = {"items": [], "pagination": {"next_page": None}}
        with patch("server.client", return_value=api):
            self.assertEqual(gitlab_list_pipeline_jobs("1", 1, compact=True)["items"], [])

    def test_full_and_compact_bridges_preserve_pagination_and_filters(self):
        original = {
            "items": [
                {
                    "id": 11,
                    "status": "failed",
                    "name": "upload_logs",
                    "allow_failure": True,
                    "commit": {"huge": "x"},
                    "downstream_pipeline": {
                        "id": 67890,
                        "project_id": 123,
                        "status": "failed",
                        "web_url": "https://gitlab.example/team/tools/-/pipelines/67890",
                    },
                },
                {
                    "id": 12,
                    "status": "skipped",
                    "name": "tests\nchild",
                    "allow_failure": False,
                    "downstream_pipeline": None,
                },
            ],
            "pagination": {"page": 2, "next_page": 3},
        }
        api = Mock()
        api.get_paginated.return_value = original
        with patch("server.client", return_value=api):
            self.assertEqual(gitlab_list_pipeline_bridges("team/repo", 7), original)
            result = gitlab_list_pipeline_bridges(
                "team/repo", 7, ["failed"], 2, 20, True
            )
        api.get_paginated.assert_called_with(
            "projects/team%2Frepo/pipelines/7/trigger_jobs",
            {"scope[]": ["failed"]},
            2,
            20,
        )
        self.assertEqual(
            result["columns"],
            [
                "bridge_id",
                "status",
                "name",
                "allow_failure",
                "downstream_pipeline_id",
                "downstream_project_id",
                "downstream_status",
                "downstream_web_url",
            ],
        )
        self.assertEqual(
            result["items"],
            [
                "11 | failed | \"upload_logs\" | True | 67890 | 123 | failed | "
                "\"https://gitlab.example/team/tools/-/pipelines/67890\"",
                "12 | skipped | \"tests\\nchild\" | False | — | — | — | null",
            ],
        )
        self.assertEqual(result["pagination"], original["pagination"])
        self.assertNotIn("commit", json.dumps(result))

    def test_empty_compact_bridges_page(self):
        api = Mock()
        api.get_paginated.return_value = {"items": [], "pagination": {"next_page": None}}
        with patch("server.client", return_value=api):
            self.assertEqual(gitlab_list_pipeline_bridges("1", 1, compact=True)["items"], [])

    def test_pipeline_graph_collects_all_pages_downstreams_retries_and_cycles(self):
        api = Mock()

        def get(path, *args, **kwargs):
            if path in (
                "projects/team%2Frepo/pipelines/7",
                "projects/10/pipelines/7",
            ):
                return GitLabResponse(
                    {
                        "id": 7,
                        "project_id": 10,
                        "status": "failed",
                        "sha": "root-sha",
                        "updated_at": "2026-10-15T10:00:00Z",
                    },
                    {},
                )
            if path == "projects/20/pipelines/8":
                return GitLabResponse(
                    {
                        "id": 8,
                        "project_id": 20,
                        "status": "success",
                        "sha": "child-sha",
                        "updated_at": "2026-10-15T10:01:00Z",
                    },
                    {},
                )
            raise AssertionError(f"Unexpected GET {path}")

        def get_paginated(path, params, page=1, per_page=100):
            self.assertEqual(per_page, 100)
            if path == "projects/10/pipelines/7/jobs":
                self.assertEqual(params, {"include_retried": "true"})
                if page == 1:
                    return {
                        "items": [
                            {
                                "id": 101,
                                "name": "test",
                                "status": "failed",
                                "allow_failure": False,
                                "retried": True,
                                "artifacts_expire_at": "2026-10-22T10:00:00Z",
                            }
                        ],
                        "pagination": {"next_page": 2},
                    }
                if page == 2:
                    return {
                        "items": [
                            {
                                "id": 102,
                                "name": "test",
                                "status": "success",
                                "allow_failure": False,
                                "retried": False,
                            }
                        ],
                        "pagination": {"next_page": None},
                    }
            if path == "projects/10/pipelines/7/trigger_jobs":
                self.assertEqual(params, {})
                if page == 1:
                    return {
                        "items": [
                            {
                                "id": 201,
                                "name": "optional",
                                "status": "failed",
                                "allow_failure": True,
                                "downstream_pipeline": None,
                            }
                        ],
                        "pagination": {"next_page": 2},
                    }
                if page == 2:
                    return {
                        "items": [
                            {
                                "id": 202,
                                "name": "child",
                                "status": "success",
                                "allow_failure": False,
                                "downstream_pipeline": {
                                    "id": 8,
                                    "project_id": 20,
                                    "status": "success",
                                    "web_url": "https://gitlab.example/pipelines/8",
                                },
                            }
                        ],
                        "pagination": {"next_page": None},
                    }
            if path == "projects/20/pipelines/8/jobs":
                return {
                    "items": [
                        {
                            "id": 301,
                            "name": "deploy",
                            "status": "success",
                            "allow_failure": False,
                        }
                    ],
                    "pagination": {"next_page": None},
                }
            if path == "projects/20/pipelines/8/trigger_jobs":
                return {
                    "items": [
                        {
                            "id": 302,
                            "name": "back-to-root",
                            "status": "success",
                            "allow_failure": False,
                            "downstream_pipeline": {
                                "id": 7,
                                "project_id": 10,
                                "status": "failed",
                            },
                        }
                    ],
                    "pagination": {"next_page": None},
                }
            raise AssertionError(f"Unexpected paginated GET {path}, page={page}")

        api.get.side_effect = get
        api.get_paginated.side_effect = get_paginated
        with patch("server.client", return_value=api):
            result = gitlab_get_pipeline_graph("team/repo", 7)

        self.assertTrue(result["coverage"]["complete"])
        self.assertEqual(result["coverage"]["pipelines_visited"], 2)
        self.assertEqual(result["coverage"]["jobs_collected"], 3)
        self.assertEqual(result["coverage"]["bridges_collected"], 3)
        self.assertTrue(result["coverage"]["parameters"]["include_retried"])
        self.assertEqual([item["id"] for item in result["pipelines"]], [7, 8])
        self.assertEqual([item["job_id"] for item in result["jobs"]], [101, 102, 301])
        self.assertTrue(result["jobs"][0]["retried"])
        self.assertEqual(result["jobs"][0]["artifacts_expire_at"], "2026-10-22T10:00:00Z")
        self.assertTrue(result["edges"][0]["allow_failure"])
        self.assertIsNone(result["edges"][0]["to"])
        self.assertEqual(result["edges"][1]["to"], {"project_id": "20", "pipeline_id": 8})
        self.assertTrue(result["snapshot"]["stable"])
        root_node = result["coverage"]["nodes"][0]
        self.assertEqual(root_node["jobs"]["pages_read"], 2)
        self.assertEqual(root_node["bridges"]["pages_read"], 2)

    def test_pipeline_graph_retains_edges_and_reports_partial_downstream_errors(self):
        api = Mock()
        root = {
            "id": 7,
            "project_id": 10,
            "status": "success",
            "updated_at": "2026-10-15T10:00:00Z",
        }

        def get(path, *args, **kwargs):
            if path in (
                "projects/team%2Frepo/pipelines/7",
                "projects/10/pipelines/7",
            ):
                return GitLabResponse(root, {})
            if path == "projects/20/pipelines/8":
                raise GitLabError("GitLab API returned 403: hidden")
            raise AssertionError(f"Unexpected GET {path}")

        def get_paginated(path, params, page=1, per_page=100):
            if path == "projects/10/pipelines/7/jobs":
                return {"items": [], "pagination": {"next_page": None}}
            if path == "projects/10/pipelines/7/trigger_jobs":
                return {
                    "items": [
                        {
                            "id": 202,
                            "allow_failure": False,
                            "downstream_pipeline": {"id": 8, "project_id": 20},
                        }
                    ],
                    "pagination": {"next_page": None},
                }
            raise AssertionError(f"Unexpected paginated GET {path}")

        api.get.side_effect = get
        api.get_paginated.side_effect = get_paginated
        with patch("server.client", return_value=api):
            result = gitlab_get_pipeline_graph("team/repo", 7)

        self.assertFalse(result["coverage"]["complete"])
        self.assertEqual(result["coverage"]["pipelines_visited"], 2)
        self.assertEqual(len(result["edges"]), 1)
        self.assertEqual(result["coverage"]["errors"][0]["resource"], "pipeline")
        self.assertIn("403", result["coverage"]["errors"][0]["error"])
        self.assertTrue(result["coverage"]["nodes"][1]["jobs"]["not_attempted"])
        self.assertFalse(result["snapshot"]["stable"])

    def test_pipeline_graph_limits_are_explicit_and_make_coverage_incomplete(self):
        api = Mock()
        pipeline = {"id": 7, "project_id": 10, "status": "success"}
        api.get.return_value = GitLabResponse(pipeline, {})

        def get_paginated(path, params, page=1, per_page=100):
            if path.endswith("/jobs"):
                return {
                    "items": [{"id": page, "name": "job", "status": "success"}],
                    "pagination": {"next_page": page + 1},
                }
            return {
                "items": [
                    {
                        "id": 2,
                        "downstream_pipeline": {"id": 8, "project_id": 20},
                    }
                ],
                "pagination": {"next_page": None},
            }

        api.get_paginated.side_effect = get_paginated
        with patch("server.client", return_value=api):
            result = gitlab_get_pipeline_graph(
                "team/repo", 7, max_depth=0, max_pages_per_collection=1
            )

        self.assertFalse(result["coverage"]["complete"])
        self.assertEqual(
            {item["kind"] for item in result["coverage"]["limits_hit"]},
            {"max_depth", "max_pages_per_collection"},
        )
        self.assertFalse(result["coverage"]["nodes"][0]["jobs"]["complete"])

    def test_pipeline_graph_marks_changed_active_snapshot(self):
        api = Mock()
        api.get.side_effect = [
            GitLabResponse(
                {
                    "id": 7,
                    "project_id": 10,
                    "status": "running",
                    "updated_at": "before",
                },
                {},
            ),
            GitLabResponse(
                {
                    "id": 7,
                    "project_id": 10,
                    "status": "success",
                    "updated_at": "after",
                },
                {},
            ),
        ]
        api.get_paginated.return_value = {
            "items": [],
            "pagination": {"next_page": None},
        }
        with patch("server.client", return_value=api):
            result = gitlab_get_pipeline_graph("team/repo", 7)

        self.assertFalse(result["snapshot"]["stable"])
        self.assertEqual(
            result["snapshot"]["changed_during_collection"][0]["status_after"],
            "success",
        )
        self.assertEqual(result["pipelines"][0]["status"], "success")

    def test_pipeline_graph_validates_limits_before_calling_api(self):
        invalid = (
            {"project": " ", "pipeline_id": 1},
            {"project": "1", "pipeline_id": 0},
            {"project": "1", "pipeline_id": 1, "max_depth": -1},
            {"project": "1", "pipeline_id": 1, "max_pipelines": 0},
            {"project": "1", "pipeline_id": 1, "max_pages_per_collection": 0},
        )
        with patch("server.client") as factory:
            for kwargs in invalid:
                with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                    gitlab_get_pipeline_graph(**kwargs)
            factory.assert_not_called()

    def test_all_protected_branch_pages_and_wildcards(self):
        api = Mock()
        rules = [
            {
                "name": "release/*",
                "allow_force_push": False,
                "push_access_levels": [{"access_level": 40}],
            },
            {"name": "main", "inherited": True},
        ]
        api.get_paginated.side_effect = [
            {"items": [rules[0]], "pagination": {"next_page": 2}},
            {"items": [rules[1]], "pagination": {"next_page": None}},
        ]
        with patch("server.client", return_value=api):
            self.assertEqual(
                gitlab_list_protected_branches("team/repo"), {"items": rules, "total": 2}
            )
        self.assertEqual(api.get_paginated.call_count, 2)
        api.get_paginated.assert_called_with(
            "projects/team%2Frepo/protected_branches", page=2, per_page=100
        )

    def test_protected_branch_pagination_does_not_loop(self):
        api = Mock()
        api.get_paginated.return_value = {"items": [], "pagination": {"next_page": 1}}
        with patch("server.client", return_value=api), self.assertRaises(GitLabError):
            gitlab_list_protected_branches("1")

    def test_application_settings_do_not_leak_other_fields(self):
        api = Mock()
        api.get.return_value = GitLabResponse(
            {
                "max_artifacts_size": 0,
                "keep_latest_artifact": False,
                "default_artifacts_expire_in": None,
                "runners_registration_token": "secret",
                "ci_secret_future_field": "secret",
                "mailgun_signing_key": "secret",
            },
            {},
        )
        with patch("server.client", return_value=api):
            result = gitlab_get_application_settings()
        self.assertEqual(
            result["settings"],
            {
                "max_artifacts_size": 0,
                "keep_latest_artifact": False,
                "default_artifacts_expire_in": None,
            },
        )
        self.assertNotIn("secret", json.dumps(result))
        self.assertIn("ci_max_includes", result["unavailable_fields"])

    def test_trace_selection_saves_full_file(self):
        with tempfile.TemporaryDirectory() as directory:
            api = Mock()
            api.settings = replace(
                fixtures.GitLabMCPTests().api_settings(Path(directory)), max_trace_chars=100
            )
            api.get.return_value = GitLabResponse(
                "setup\nccache --show-stats\nhit 90%\nerror\n", {}
            )
            with patch("server.client", return_value=api):
                result = gitlab_get_job_trace(
                    "team/repo", 7, pattern="ccache --show-stats", context_after=1
                )
            self.assertEqual(result["trace"], "2: ccache --show-stats\n3: hit 90%")
            self.assertFalse(result["truncated"])
            self.assertEqual(Path(result["trace_file"]).read_text(), api.get.return_value.data)
            api.get.assert_called_once_with("projects/team%2Frepo/jobs/7/trace", raw=True)

    def test_invalid_selection_fails_before_downloading(self):
        for kwargs in (
            {"limit": 0},
            {"limit": -1},
            {"pattern": ""},
            {"context_after": 1},
            {"pattern": "x", "context_before": -1},
        ):
            with self.subTest(kwargs=kwargs), patch("server.client") as factory:
                with self.assertRaises(ValueError):
                    gitlab_get_job_trace("1", 7, **kwargs)
                factory.assert_not_called()

    def test_empty_http_trace_is_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            api = Mock()
            api.settings = fixtures.GitLabMCPTests().api_settings(Path(directory))
            api.get.return_value = GitLabResponse(None, {})
            with patch("server.client", return_value=api):
                result = gitlab_get_job_trace("1", 7, offset=-10, limit=10)
            self.assertEqual(result["trace"], "")
            self.assertFalse(result["truncated"])
            self.assertEqual(result["trace_file_bytes"], 0)

    def test_mcp_catalog_and_serialized_compact_call(self):
        async def check():
            catalog = {tool.name: tool for tool in await mcp.list_tools()}
            for name in (
                "gitlab_run_pipeline",
                "gitlab_retry_job",
                "gitlab_get_pipeline_graph",
                "gitlab_list_pipeline_bridges",
                "gitlab_list_protected_branches",
                "gitlab_get_application_settings",
                "gitlab_get_job_artifact",
            ):
                self.assertIn(name, catalog)
            self.assertFalse(
                catalog["gitlab_list_pipeline_jobs"].inputSchema["properties"]["compact"]["default"]
            )
            self.assertFalse(
                catalog["gitlab_list_pipeline_bridges"]
                .inputSchema["properties"]["compact"]["default"]
            )
            graph_schema = catalog["gitlab_get_pipeline_graph"].inputSchema["properties"]
            self.assertTrue(graph_schema["include_retried"]["default"])
            self.assertEqual(graph_schema["max_depth"]["default"], 20)
            self.assertEqual(graph_schema["max_pipelines"]["default"], 200)
            self.assertIn("offset", catalog["gitlab_get_job_trace"].inputSchema["properties"])
            api = Mock()
            api.get_paginated.return_value = {"items": [], "pagination": {"next_page": None}}
            with patch("server.client", return_value=api):
                result = await mcp.call_tool(
                    "gitlab_list_pipeline_jobs", {"project": "1", "pipeline_id": 1, "compact": True}
                )
            self.assertIn("columns", str(result))

        asyncio.run(check())


class ArtifactToolTests(unittest.TestCase):
    @staticmethod
    def zip_bytes() -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as bundle:
            bundle.writestr("deploy_single_manifest.log", "line1\nError 403 Forbidden\nline3\n")
            bundle.writestr("installReports/report.html", "<html>403</html>")
        return buffer.getvalue()

    def make_api(self, directory: str) -> Mock:
        api = Mock()
        api.get.return_value = GitLabResponse(
            {"artifacts_file": {"filename": "install_logs.zip"}}, {}
        )
        api.settings = replace(
            fixtures.GitLabMCPTests().api_settings(Path(directory)), max_trace_chars=200
        )
        return api

    def install_download(self, api: Mock, payload: bytes, content_type: str = "application/zip"):
        def fake_download(path, output, params=None):
            output.write_bytes(payload)
            return {"size": len(payload), "content_type": content_type}

        api.download_file.side_effect = fake_download

    def test_zip_artifact_lists_and_reads_inner_file(self):
        payload = self.zip_bytes()
        with tempfile.TemporaryDirectory() as directory:
            api = self.make_api(directory)
            self.install_download(api, payload)
            with patch("server.client", return_value=api):
                listing = gitlab_get_job_artifact("team/repo", 7, "install_logs.zip")
                inner = gitlab_get_job_artifact(
                    "team/repo",
                    7,
                    "install_logs.zip",
                    inner_path="deploy_single_manifest.log",
                    pattern="Error",
                    context_after=1,
                )
            self.assertTrue(listing["is_zip"])
            self.assertEqual(listing["artifact_bytes"], len(payload))
            self.assertEqual(
                {item["path"] for item in listing["files"]},
                {"deploy_single_manifest.log", "installReports/report.html"},
            )
            self.assertEqual(
                Path(listing["extracted_dir"], "installReports", "report.html").read_text(),
                "<html>403</html>",
            )
            self.assertEqual(
                api.download_file.call_args.args[0],
                "projects/team%2Frepo/jobs/7/artifacts",
            )
            self.assertEqual(api.download_file.call_count, 2)  # every call re-downloads
            self.assertFalse(inner["binary"])
            self.assertEqual(inner["content"], "2: Error 403 Forbidden\n3: line3")
            self.assertEqual(inner["selection"]["matched_lines"], 1)
            self.assertEqual(inner["characters"], len("line1\nError 403 Forbidden\nline3\n"))

    def test_archive_filename_from_artifacts_list_uses_whole_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            api = self.make_api(directory)
            api.get.return_value = GitLabResponse(
                {"artifacts": [{"file_type": "archive", "filename": "install_logs.zip"}]}, {}
            )
            self.install_download(api, self.zip_bytes())
            with patch("server.client", return_value=api):
                result = gitlab_get_job_artifact("team/repo", 7, "install_logs.zip")
            self.assertTrue(result["is_zip"])
            self.assertEqual(
                api.download_file.call_args.args[0],
                "projects/team%2Frepo/jobs/7/artifacts",
            )

    def test_single_file_path_uses_member_endpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            api = self.make_api(directory)
            self.install_download(api, b"Error 403\n", content_type="text/plain")
            with patch("server.client", return_value=api):
                result = gitlab_get_job_artifact("team/repo", 7, "logs/deploy report.log")
            self.assertEqual(result["content"], "1: Error 403")
            self.assertEqual(
                api.download_file.call_args.args[0],
                "projects/team%2Frepo/jobs/7/artifacts/logs/deploy%20report.log",
            )

    def test_inner_path_not_found_returns_file_list(self):
        with tempfile.TemporaryDirectory() as directory:
            api = self.make_api(directory)
            self.install_download(api, self.zip_bytes())
            with patch("server.client", return_value=api):
                result = gitlab_get_job_artifact(
                    "team/repo", 7, "install_logs.zip", inner_path="missing.txt"
                )
        self.assertEqual(result["file_count"], 2)
        self.assertIn("missing.txt", result["inner_path_error"])
        self.assertIn("deploy_single_manifest.log", result["inner_path_error"])
        self.assertNotIn("content", result)

    def test_non_zip_text_artifact_reads_and_rejects_inner_path(self):
        with tempfile.TemporaryDirectory() as directory:
            api = self.make_api(directory)
            self.install_download(api, b"alpha\nbeta\n", content_type="text/plain")
            with patch("server.client", return_value=api):
                result = gitlab_get_job_artifact("team/repo", 7, "job.log", pattern="beta")
                with self.assertRaises(GitLabError):
                    gitlab_get_job_artifact("team/repo", 7, "job.log", inner_path="x.txt")
        self.assertFalse(result["is_zip"])
        self.assertFalse(result["binary"])
        self.assertEqual(result["content"], "2: beta")

    def test_binary_inner_file_is_reported_without_text(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as bundle:
            bundle.writestr("blob.bin", b"bin\x00ary")
        with tempfile.TemporaryDirectory() as directory:
            api = self.make_api(directory)
            self.install_download(api, buffer.getvalue())
            with patch("server.client", return_value=api):
                result = gitlab_get_job_artifact(
                    "team/repo", 7, "install_logs.zip", inner_path="blob.bin"
                )
        self.assertTrue(result["binary"])
        self.assertNotIn("content", result)

    def test_invalid_artifact_arguments_do_not_call_api(self):
        for kwargs in (
            {"job_id": 0, "artifact_path": "install_logs.zip"},
            {"job_id": -1, "artifact_path": "install_logs.zip"},
            {"job_id": 7, "artifact_path": "  "},
            {"job_id": 7, "artifact_path": ".."},
            {"job_id": 7, "artifact_path": "/etc/passwd"},
            {"job_id": 7, "artifact_path": "logs/"},
            {"job_id": 7, "artifact_path": "a\\b"},
            {"job_id": 7, "artifact_path": "install_logs.zip", "inner_path": "../evil"},
            {"job_id": 7, "artifact_path": "install_logs.zip", "limit": 0},
            {"job_id": 7, "artifact_path": "install_logs.zip", "context_after": 1},
        ):
            with self.subTest(kwargs=kwargs), patch("server.client") as factory:
                with self.assertRaises(ValueError):
                    gitlab_get_job_artifact("1", **kwargs)
                factory.assert_not_called()

    def test_zip_slip_member_is_rejected(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as bundle:
            bundle.writestr("../evil.txt", "payload")
        with tempfile.TemporaryDirectory() as directory:
            api = self.make_api(directory)
            self.install_download(api, buffer.getvalue())
            with (
                patch("server.client", return_value=api),
                self.assertRaises(GitLabError) as error,
            ):
                gitlab_get_job_artifact("team/repo", 7, "install_logs.zip")
            self.assertIn("escapes", str(error.exception))
            artifacts_dir = Path(directory) / "gitlab-artifacts"
            self.assertFalse((artifacts_dir / "evil.txt").exists())


class DownloadFileTests(unittest.TestCase):
    def settings(self, directory: str, max_artifact_bytes: int = 100):
        return replace(
            fixtures.GitLabMCPTests().api_settings(Path(directory)),
            max_artifact_bytes=max_artifact_bytes,
        )

    @contextmanager
    def mocked_stream(self):
        with patch("gitlab_client.httpx.Client") as factory:
            response = (
                factory.return_value.__enter__.return_value.stream.return_value.__enter__.return_value
            )
            response.is_redirect = False
            response.status_code = 200
            response.headers = {}
            yield response

    @staticmethod
    def stream_context(response):
        manager = MagicMock()
        manager.__enter__.return_value = response
        manager.__exit__.return_value = False
        return manager

    def test_streams_to_file_and_returns_size(self):
        with tempfile.TemporaryDirectory() as directory:
            api = GitLabClient(self.settings(directory))
            output = Path(directory) / "artifacts" / "install_logs.zip"
            with self.mocked_stream() as response:
                response.iter_bytes.return_value = iter([b"12345", b"67890"])
                result = api.download_file("projects/1/jobs/2/artifacts/install_logs.zip", output)
            self.assertEqual(result["size"], 10)
            self.assertEqual(result["redirects_followed"], 0)
            self.assertEqual(output.read_bytes(), b"1234567890")

    def test_rejects_oversized_content_length_without_download(self):
        with tempfile.TemporaryDirectory() as directory:
            api = GitLabClient(self.settings(directory, max_artifact_bytes=100))
            output = Path(directory) / "out.bin"
            with self.mocked_stream() as response, self.assertRaises(GitLabError) as error:
                response.headers = {"content-length": "101"}
                api.download_file("x", output)
        self.assertIn("limit is 100", str(error.exception))
        self.assertFalse(output.exists())

    def test_aborts_stream_over_cap_and_removes_partial_file(self):
        with tempfile.TemporaryDirectory() as directory:
            api = GitLabClient(self.settings(directory, max_artifact_bytes=8))
            output = Path(directory) / "out.bin"
            with self.mocked_stream() as response, self.assertRaises(GitLabError) as error:
                response.iter_bytes.return_value = iter([b"12345", b"67890"])
                api.download_file("x", output)
        self.assertIn("exceeds 8 bytes", str(error.exception))
        self.assertFalse(output.exists())

    def test_error_status_redacts_token_and_cleans_partial_file(self):
        with tempfile.TemporaryDirectory() as directory:
            api = GitLabClient(self.settings(directory))
            output = Path(directory) / "out.bin"
            with self.mocked_stream() as response, self.assertRaises(GitLabError) as error:
                response.status_code = 404
                response.text = "artifact not found test-token"
                api.download_file("x", output)
        self.assertIn("404", str(error.exception))
        self.assertNotIn("test-token", str(error.exception))
        self.assertFalse(output.exists())

    def test_follows_allowed_https_redirect_without_forwarding_token(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = replace(
                self.settings(directory),
                artifact_redirect_hosts=("artifacts.example.com",),
            )
            api = GitLabClient(settings)
            output = Path(directory) / "out.bin"
            redirect = Mock(
                is_redirect=True,
                status_code=302,
                headers={
                    "location": "https://artifacts.example.com/object?signature=secret"
                },
            )
            artifact = Mock(
                is_redirect=False,
                status_code=200,
                headers={"content-type": "application/zip"},
            )
            artifact.iter_bytes.return_value = iter([b"payload"])
            with patch("gitlab_client.httpx.Client") as factory:
                stream = factory.return_value.__enter__.return_value.stream
                stream.side_effect = [
                    self.stream_context(redirect),
                    self.stream_context(artifact),
                ]
                result = api.download_file("projects/1/jobs/2/artifacts", output)

            self.assertEqual(result["redirects_followed"], 1)
            self.assertEqual(output.read_bytes(), b"payload")
            self.assertIn("PRIVATE-TOKEN", stream.call_args_list[0].kwargs["headers"])
            self.assertNotIn("PRIVATE-TOKEN", stream.call_args_list[1].kwargs["headers"])
            self.assertIsNone(stream.call_args_list[1].kwargs["params"])

    def test_rejects_unlisted_redirect_without_exposing_signed_url(self):
        with tempfile.TemporaryDirectory() as directory:
            api = GitLabClient(self.settings(directory))
            output = Path(directory) / "out.bin"
            redirect = Mock(
                is_redirect=True,
                status_code=302,
                headers={
                    "location": "https://untrusted.example/object?signature=do-not-log"
                },
            )
            with patch("gitlab_client.httpx.Client") as factory:
                stream = factory.return_value.__enter__.return_value.stream
                stream.return_value = self.stream_context(redirect)
                with self.assertRaises(GitLabError) as error:
                    api.download_file("projects/1/jobs/2/artifacts", output)

            self.assertIn("untrusted.example", str(error.exception))
            self.assertNotIn("do-not-log", str(error.exception))
            self.assertEqual(stream.call_count, 1)
            self.assertFalse(output.exists())

    def test_rejects_non_https_redirect_even_when_host_is_allowed(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = replace(
                self.settings(directory),
                artifact_redirect_hosts=("artifacts.example.com",),
            )
            api = GitLabClient(settings)
            output = Path(directory) / "out.bin"
            redirect = Mock(
                is_redirect=True,
                status_code=302,
                headers={"location": "http://artifacts.example.com/object"},
            )
            with patch("gitlab_client.httpx.Client") as factory:
                stream = factory.return_value.__enter__.return_value.stream
                stream.return_value = self.stream_context(redirect)
                with self.assertRaises(GitLabError) as error:
                    api.download_file("projects/1/jobs/2/artifacts", output)

            self.assertIn("HTTPS", str(error.exception))
            self.assertEqual(stream.call_count, 1)
            self.assertFalse(output.exists())

    def test_limits_allowed_redirect_chain(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = replace(
                self.settings(directory),
                artifact_redirect_hosts=("artifacts.example.com",),
            )
            api = GitLabClient(settings)
            output = Path(directory) / "out.bin"
            redirects = [
                Mock(
                    is_redirect=True,
                    status_code=302,
                    headers={"location": f"https://artifacts.example.com/hop-{index}"},
                )
                for index in range(4)
            ]
            with patch("gitlab_client.httpx.Client") as factory:
                stream = factory.return_value.__enter__.return_value.stream
                stream.side_effect = [self.stream_context(response) for response in redirects]
                with self.assertRaises(GitLabError) as error:
                    api.download_file("projects/1/jobs/2/artifacts", output)

            self.assertIn("exceeded 3 redirects", str(error.exception))
            self.assertEqual(stream.call_count, 4)
            self.assertFalse(output.exists())



    def select(self, text, **kwargs):
        defaults = dict(
            max_chars=1000, offset=None, limit=None, pattern=None, context_before=0, context_after=0
        )
        return select_trace(text, **(defaults | kwargs))

    def test_tail_offset_and_line_limit(self):
        result = self.select("a\nb\nc\nd\n", offset=-2, limit=1)
        self.assertEqual(result["trace"], "3: c")
        self.assertTrue(result["truncated"])
        self.assertEqual(result["selection"]["next_offset"], 3)

    def test_search_merges_context_then_applies_offset(self):
        text = "a\nhit\nc\nhit\ne\nf\ng\nhit\ni"
        result = self.select(
            text, pattern="hit", context_before=1, context_after=1, offset=4, limit=3
        )
        self.assertEqual(result["trace"], "5: e\n7: g\n8: hit")
        self.assertEqual(result["selection"]["matched_lines"], 3)
        self.assertEqual(result["selection"]["selected_lines"], 8)
        self.assertEqual(result["selection"]["next_offset"], 7)

    def test_literal_case_sensitive_search_and_no_match(self):
        self.assertEqual(self.select("axb\na.b\nA.B", pattern="a.b")["trace"], "2: a.b")
        result = self.select("text", pattern="missing")
        self.assertEqual(result["trace"], "")
        self.assertFalse(result["truncated"])

    def test_empty_trace_and_outside_offsets(self):
        self.assertEqual(self.select("")["selection"]["total_lines"], 0)
        self.assertEqual(self.select("a\nb", offset=-20)["trace"], "1: a\n2: b")
        self.assertEqual(self.select("a\nb", offset=20)["trace"], "")

    def test_character_cap_and_long_first_line(self):
        result = self.select("abc\ndef", max_chars=8)
        self.assertEqual(result["trace"], "1: abc")
        self.assertTrue(result["selection"]["character_limit_reached"])
        self.assertEqual(result["selection"]["next_offset"], 1)
        result = self.select("x" * 100, max_chars=10)
        self.assertEqual(len(result["trace"]), 10)
        self.assertTrue(result["truncated"])
        self.assertTrue(result["selection"]["last_line_truncated"])

    def test_unicode_crlf_and_progress_are_not_extra_lines(self):
        result = self.select("привет\r\n0%\r100%\n\n", offset=1)
        self.assertEqual(result["selection"]["total_lines"], 3)
        self.assertEqual(result["trace"], "2: 0%\r100%\n3: ")


if __name__ == "__main__":
    unittest.main()

import asyncio
import json
import time
import types
import unittest
from unittest import mock

from torque import external_tickets as ext


class DummyAdapter(ext.ExternalTicketAdapter):
    name = "dummy"

    def import_ticket(self, ref: str, **_kwargs):
        return ext.ImportedTicket(
            title=f"Imported {ref}",
            description="Imported description",
            provider=self.name,
            external_id="DUMMY-1",
            external_url="https://example.test/tickets/1",
        )

    def push_status(self, task, *, status: str = "", note: str = "") -> str:
        return f"{task.task}:{status}:{note}"

    def post_comment(self, _task, *, comment: str) -> str:
        return comment


class ExternalTicketTests(unittest.TestCase):
    def setUp(self):
        ext.register_adapter("dummy", DummyAdapter())

    def test_normalize_link_infers_github_from_url(self):
        link = ext.normalize_link(
            ref="https://github.com/openai/example/issues/42"
        )
        self.assertEqual(link["provider"], "github")
        self.assertEqual(link["external_id"], "openai/example#42")
        self.assertEqual(
            link["external_url"],
            "https://github.com/openai/example/issues/42",
        )

    def test_import_ticket_routes_through_registered_adapter(self):
        imported = ext.import_ticket("dummy:123", provider="dummy")
        self.assertEqual(imported.title, "Imported dummy:123")
        self.assertEqual(imported.provider, "dummy")
        self.assertEqual(imported.external_id, "DUMMY-1")

    def test_push_status_and_comment_require_linked_provider(self):
        task = types.SimpleNamespace(
            task="Ship release",
            lane="In Progress",
            status="",
            provider="dummy",
            external_id="DUMMY-1",
            external_url="https://example.test/tickets/1",
        )
        pushed = ext.push_ticket_status(
            task, status="In Progress", note="Running final checks"
        )
        posted = ext.post_ticket_comment(task, comment="Done")
        self.assertEqual(
            pushed,
            "Ship release:In Progress:Running final checks",
        )
        self.assertEqual(posted, "Done")

    def test_completion_comment_is_concise(self):
        comment = ext.build_completion_comment(
            "Fix flaky tests",
            "Stabilized the retry path and updated assertions.",
        )
        self.assertIn("Torque completed: Fix flaky tests", comment)
        self.assertIn("Summary:", comment)

    def test_github_import_uses_supported_issue_view_json_fields(self):
        calls = []

        def fake_run(cmd, check, capture_output, text, timeout):
            calls.append(cmd)
            self.assertEqual(timeout, ext._GH_TIMEOUT_SECONDS)
            self.assertEqual(cmd[:3], ["gh", "issue", "view"])
            fields = cmd[cmd.index("--json") + 1]
            self.assertNotIn("repository", fields.split(","))
            return types.SimpleNamespace(
                stdout=json.dumps({
                    "number": 42,
                    "title": "Imported",
                    "body": "Body",
                    "url": "https://github.com/openai/example/issues/42",
                }),
                stderr="",
            )

        with mock.patch.object(ext.subprocess, "run", side_effect=fake_run):
            imported = ext.GitHubExternalTicketAdapter().import_ticket(
                "https://github.com/openai/example/issues/42"
            )

        self.assertEqual(imported.provider, "github")
        self.assertEqual(imported.external_id, "openai/example#42")
        self.assertEqual(imported.external_url, "https://github.com/openai/example/issues/42")
        self.assertEqual(imported.title, "Imported")
        self.assertEqual(len(calls), 1)

    def test_github_cli_missing_error_is_preserved(self):
        with mock.patch.object(
                ext.subprocess, "run", side_effect=FileNotFoundError):
            with self.assertRaisesRegex(
                    ext.ExternalTicketError,
                    "GitHub CLI 'gh' is required for GitHub ticket sync"):
                ext.GitHubExternalTicketAdapter()._run_gh(["api", "rate_limit"])

    def test_github_cli_failure_preserves_stderr(self):
        failure = ext.subprocess.CalledProcessError(
            1, ["gh", "api"], output="fallback stdout", stderr="auth required"
        )
        with mock.patch.object(ext.subprocess, "run", side_effect=failure):
            with self.assertRaisesRegex(ext.ExternalTicketError, "auth required"):
                ext.GitHubExternalTicketAdapter()._run_gh(["api", "rate_limit"])

    def test_github_cli_failure_preserves_stdout_when_stderr_is_empty(self):
        failure = ext.subprocess.CalledProcessError(
            1, ["gh", "api"], output="fallback stdout", stderr=""
        )
        with mock.patch.object(ext.subprocess, "run", side_effect=failure):
            with self.assertRaisesRegex(ext.ExternalTicketError, "fallback stdout"):
                ext.GitHubExternalTicketAdapter()._run_gh(["api", "rate_limit"])

    def test_github_cli_timeout_is_bounded_and_reported(self):
        timeout = ext.subprocess.TimeoutExpired(
            ["gh", "api", "rate_limit"], ext._GH_TIMEOUT_SECONDS
        )
        with mock.patch.object(ext.subprocess, "run", side_effect=timeout) as run:
            with self.assertRaisesRegex(
                    ext.ExternalTicketError,
                    "GitHub CLI command timed out after 30 seconds"):
                ext.GitHubExternalTicketAdapter()._run_gh(["api", "rate_limit"])
        self.assertEqual(run.call_args.kwargs["timeout"], ext._GH_TIMEOUT_SECONDS)

    def test_async_ticket_operation_does_not_block_event_loop(self):
        adapter = ext.GitHubExternalTicketAdapter()
        progress_at = None

        def slow_gh(*_args, **_kwargs):
            time.sleep(0.15)
            return types.SimpleNamespace(stdout="ok", stderr="")

        async def verify():
            nonlocal progress_at

            async def mark_progress():
                nonlocal progress_at
                await asyncio.sleep(0.01)
                progress_at = time.monotonic()

            with mock.patch.object(ext.subprocess, "run", side_effect=slow_gh):
                started_at = time.monotonic()
                marker = asyncio.create_task(mark_progress())
                operation = asyncio.create_task(
                    ext.run_external_ticket_operation(
                        adapter._run_gh, ["api", "rate_limit"]
                    )
                )
                self.assertEqual(await operation, "ok")
                await marker
                self.assertLess(progress_at - started_at, 0.08)

        asyncio.run(verify())

    def test_sync_external_helpers_remain_usable_without_event_loop(self):
        imported = ext.import_ticket(
            "manual:offline-1", provider="manual", title="Offline import"
        )
        self.assertEqual(imported.title, "Offline import")
        self.assertEqual(
            ext.open_ticket_url("github", "openai/example#42"),
            "https://github.com/openai/example/issues/42",
        )

import json
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

        def fake_run(cmd, check, capture_output, text):
            calls.append(cmd)
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

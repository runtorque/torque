import asyncio
import json
import unittest

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub

install_aiohttp_stub()

from torque.board_sync import BoardSyncProvider, get_provider, register_provider
from torque.board_sync.github import (
    GitHubBoardSyncProvider,
    append_closing_refs_to_body,
    compute_outbound_hash,
    parse_torque_sync_marker,
    render_issue_body,
    strip_torque_sync_footer,
    github_settings as extract_github_settings,
)
from torque.state import BoardTask, GroupSettings


class FakeBoardSyncProvider:
    name = "fake"

    async def preflight(self, group_settings):
        return {"ok": True, "phase": "preflight", "settings": group_settings}

    async def push_task(self, task, group_settings):
        return {"provider": self.name, "task_id": task.id, "settings": group_settings}

    async def pull_task(self, task, group_settings):
        return {"ok": True, "phase": "pull_preview", "task_id": task.id}

    async def apply_pull(self, task, group_settings, fields):
        return {"ok": True, "phase": "apply_pull", "fields": fields}

    async def list_external_items(self, group_settings):
        return [{"ok": True, "phase": "list_external_items"}]

    async def append_closing_refs(self, pr_body, linked_issues, group_settings=None):
        return pr_body + " fake"


class FakeGhRunner:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls = []

    async def __call__(self, args, cwd=None):
        self.calls.append((list(args), cwd))
        if not self.responses:
            raise AssertionError(f"Unexpected gh call: {args}")
        response = self.responses.pop(0)
        if callable(response):
            response = response(list(args), cwd)
        if isinstance(response, dict):
            out = dict(response)
        else:
            out = {"returncode": 0, "stdout": response, "stderr": ""}
        out.setdefault("returncode", 0)
        out.setdefault("stdout", "")
        out.setdefault("stderr", "")
        out.setdefault("cmd", ["gh", *args])
        out.setdefault("cwd", cwd or "")
        return out


def gh_ok(payload=""):
    if isinstance(payload, (dict, list)):
        payload = json.dumps(payload)
    return {"returncode": 0, "stdout": str(payload), "stderr": ""}


def gh_fail(message, code=1):
    return {"returncode": code, "stdout": "", "stderr": message}


def github_settings(**overrides):
    nested = {
        "github_repo": "owner/repo",
        "github_project_owner": "owner",
        "github_project_number": 5,
        "github_project_status_field": "Status",
        "github_lane_status_map": {"In Progress": "Doing"},
        "github_close_issues_via_pr": True,
        "github_create_missing_labels": False,
    }
    nested.update(overrides.pop("board_sync_github", {}))
    return GroupSettings(
        board_sync_provider="github",
        board_sync_enabled=overrides.pop("board_sync_enabled", True),
        board_sync_github=nested,
        **overrides,
    )


def issue_view(number=123, repo="owner/repo", title="Ship it", body="Body"):
    return {
        "id": "I_kw123",
        "number": number,
        "title": title,
        "body": body,
        "url": f"https://github.com/{repo}/issues/{number}",
        "labels": [{"name": "bug"}],
        "assignees": [],
        "state": "OPEN",
        "updatedAt": "2026-05-20T14:58:00Z",
        "repository": {"nameWithOwner": repo},
    }


def project_view():
    return {"id": "PVT_kw", "number": 5, "title": "Roadmap"}


def field_list():
    return {
        "fields": [
            {
                "id": "PVTSSF_kw",
                "name": "Status",
                "options": [
                    {"id": "opt-doing", "name": "Doing"},
                    {"id": "opt-done", "name": "Done"},
                ],
            }
        ]
    }


class BoardSyncProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_fake_adapter_satisfies_protocol_shape(self):
        adapter: BoardSyncProvider = FakeBoardSyncProvider()
        settings = GroupSettings()
        task = BoardTask(id="T:1", task="Task")

        self.assertEqual((await adapter.preflight(settings))["phase"], "preflight")
        self.assertEqual((await adapter.push_task(task, settings))["task_id"], "T:1")
        self.assertEqual((await adapter.pull_task(task, settings))["phase"], "pull_preview")
        self.assertEqual(
            (await adapter.apply_pull(task, settings, ["task"]))["fields"],
            ["task"],
        )
        self.assertEqual((await adapter.list_external_items(settings))[0]["ok"], True)
        self.assertTrue((await adapter.append_closing_refs("body", [])).endswith("fake"))

    async def test_factory_returns_github_and_structured_errors(self):
        self.assertEqual(get_provider("github").name, "github")
        disabled = get_provider("none")
        disabled_push = await disabled.push_task(BoardTask(id="T:1", task="T"), GroupSettings())
        self.assertEqual(disabled_push["sync_state"], "error")
        self.assertIn("push + manual reconcile", disabled_push["last_error"])

        unknown = get_provider("jira")
        result = await unknown.preflight(GroupSettings())
        self.assertFalse(result["ok"])
        self.assertEqual(result["phase"], "provider_lookup")

    async def test_register_provider_accepts_custom_provider(self):
        register_provider("fake", lambda: FakeBoardSyncProvider())
        self.assertEqual(get_provider("fake").name, "fake")


class GitHubPreflightTests(unittest.IsolatedAsyncioTestCase):
    async def test_preflight_reports_missing_gh(self):
        provider = GitHubBoardSyncProvider(FakeGhRunner([gh_fail("not found", 127)]))

        result = await provider.preflight(github_settings())

        self.assertFalse(result["ok"])
        self.assertEqual(result["phase"], "gh_version")
        self.assertIn("GitHub CLI", result["error"])

    async def test_preflight_reports_unauthenticated(self):
        provider = GitHubBoardSyncProvider(FakeGhRunner([
            gh_ok("gh version 2.0"),
            gh_fail("not logged in"),
        ]))

        result = await provider.preflight(github_settings())

        self.assertFalse(result["ok"])
        self.assertEqual(result["phase"], "auth")
        self.assertIn("authentication failed", result["error"])

    async def test_preflight_reports_missing_project_scope(self):
        provider = GitHubBoardSyncProvider(FakeGhRunner([
            gh_ok("gh version 2.0"),
            gh_ok({"note": "ignored"}) | {"stderr": "Token scopes: 'repo', 'read:org'"},
            gh_ok({"nameWithOwner": "owner/repo", "url": "https://github.com/owner/repo"}),
        ]))

        result = await provider.preflight(github_settings())

        self.assertFalse(result["ok"])
        self.assertEqual(result["phase"], "project_scope")
        self.assertIn("gh auth refresh -s project", result["error"])

    async def test_preflight_reports_missing_repo_when_not_configured(self):
        settings = github_settings(board_sync_github={
            "github_repo": "",
            "github_project_owner": "",
            "github_project_number": 0,
        })
        provider = GitHubBoardSyncProvider(FakeGhRunner([
            gh_ok("gh version 2.0"),
            gh_ok("Logged in"),
            gh_fail("no git remotes"),
        ]))

        result = await provider.preflight(settings)

        self.assertFalse(result["ok"])
        self.assertEqual(result["phase"], "repo")

    async def test_preflight_reports_missing_configured_repo(self):
        provider = GitHubBoardSyncProvider(FakeGhRunner([
            gh_ok("gh version 2.0"),
            gh_ok("Logged in"),
            gh_fail("Could not resolve to a Repository"),
        ]))

        result = await provider.preflight(github_settings())

        self.assertFalse(result["ok"])
        self.assertEqual(result["phase"], "repo")
        self.assertIn("Repository", result["error"])

    async def test_preflight_resolves_project_and_status_options(self):
        provider = GitHubBoardSyncProvider(FakeGhRunner([
            gh_ok("gh version 2.0"),
            gh_ok("Token scopes: repo, project"),
            gh_ok({"nameWithOwner": "owner/repo", "url": "https://github.com/owner/repo"}),
            gh_ok(project_view()),
            gh_ok(field_list()),
        ]))

        result = await provider.preflight(github_settings())

        self.assertTrue(result["ok"])
        self.assertEqual(result["repo"], "owner/repo")
        self.assertEqual(result["project_id"], "PVT_kw")
        self.assertEqual(result["status_options"]["Doing"], "opt-doing")


class GitHubBodyAndClosingRefTests(unittest.IsolatedAsyncioTestCase):
    async def test_body_marker_round_trip(self):
        task = BoardTask(
            id="TORQUE:505",
            task="Adapter",
            description="Implement provider",
            group="Torque",
        )

        body = render_issue_body(task)
        marker = parse_torque_sync_marker(body)

        self.assertEqual(marker["version"], 1)
        self.assertEqual(marker["task_id"], "TORQUE:505")
        self.assertEqual(marker["group"], "Torque")
        self.assertEqual(strip_torque_sync_footer(body), "Implement provider")

    async def test_closing_refs_same_repo_cross_repo_no_duplicate_and_disabled(self):
        issues = [
            {"issue_repo": "owner/repo", "issue_number": 1, "base_repo": "owner/repo"},
            {"issue_repo": "other/repo", "issue_number": 2, "base_repo": "owner/repo"},
            {"issue_repo": "owner/repo", "issue_number": 1, "base_repo": "owner/repo"},
        ]

        body = append_closing_refs_to_body("Existing body", issues)

        self.assertIn("- Closes #1", body)
        self.assertIn("- Closes other/repo#2", body)
        self.assertEqual(body.count("Closes #1"), 1)
        self.assertEqual(
            append_closing_refs_to_body("Already fixes #1", issues),
            "Already fixes #1\n\nLinked Torque issues:\n- Closes other/repo#2",
        )
        self.assertEqual(
            append_closing_refs_to_body("Body", issues, enabled=False),
            "Body",
        )

    async def test_provider_closing_refs_honors_disabled_setting(self):
        provider = GitHubBoardSyncProvider(FakeGhRunner([]))
        settings = github_settings(board_sync_github={
            "github_close_issues_via_pr": False,
        })

        body = await provider.append_closing_refs(
            "Body",
            [{"issue_repo": "owner/repo", "issue_number": 1, "base_repo": "owner/repo"}],
            settings,
        )

        self.assertEqual(body, "Body")


class GitHubPushTests(unittest.IsolatedAsyncioTestCase):
    async def test_push_creates_issue_adds_project_status_and_records_sync_metadata(self):
        task = BoardTask(
            id="TORQUE:505",
            task="Ship board sync",
            description="Build adapter",
            group="Torque",
            lane="In Progress",
            labels=["bug", "torque:blocked"],
        )
        runner = FakeGhRunner([
            gh_ok({"items": [{"name": "bug"}]}),
            gh_ok("https://github.com/owner/repo/issues/123\n"),
            gh_ok(issue_view(body=render_issue_body(task))),
            gh_ok(project_view()),
            gh_ok(field_list()),
            gh_ok({"id": "PVTI_kw"}),
            gh_ok(""),
        ])
        provider = GitHubBoardSyncProvider(runner)

        sync = await provider.push_task(task, github_settings())

        self.assertEqual(sync["sync_state"], "idle")
        self.assertEqual(sync["github"]["issue_repo"], "owner/repo")
        self.assertEqual(sync["github"]["issue_number"], 123)
        self.assertEqual(sync["github"]["project_item_id"], "PVTI_kw")
        self.assertEqual(sync["github"]["status_option_id"], "opt-doing")
        self.assertEqual(sync["last_seen_provider_updated_at"], "2026-05-20T14:58:00Z")
        self.assertTrue(sync["last_synced_hash"])
        commands = [call[0] for call in runner.calls]
        self.assertEqual(commands[0][:3], ["label", "list", "--repo"])
        self.assertEqual(commands[1][0:2], ["issue", "create"])
        self.assertIn("--label", commands[1])
        self.assertNotIn("torque:blocked", " ".join(commands[1]))
        self.assertEqual(commands[5][0:2], ["project", "item-add"])
        self.assertEqual(commands[6][0:2], ["project", "item-edit"])

    async def test_push_errors_when_user_label_missing_and_creation_disabled(self):
        task = BoardTask(id="T:1", task="Task", labels=["missing"])
        provider = GitHubBoardSyncProvider(FakeGhRunner([
            gh_ok({"items": [{"name": "bug"}]}),
        ]))

        sync = await provider.push_task(task, github_settings())

        self.assertEqual(sync["sync_state"], "error")
        self.assertEqual(sync["phase"], "labels")
        self.assertIn("missing", sync["last_error"])

    async def test_push_with_only_system_labels_skips_label_lookup(self):
        task = BoardTask(
            id="T:2",
            task="System only",
            description="No user labels",
            labels=["torque:blocked"],
            lane="To Do",
        )
        settings = github_settings(board_sync_github={
            "github_project_owner": "",
            "github_project_number": 0,
        })
        runner = FakeGhRunner([
            gh_ok("https://github.com/owner/repo/issues/124\n"),
            gh_ok(issue_view(number=124, title="System only", body=render_issue_body(task))),
        ])
        provider = GitHubBoardSyncProvider(runner)

        sync = await provider.push_task(task, settings)

        self.assertEqual(sync["sync_state"], "idle")
        self.assertEqual(sync["github"]["issue_number"], 124)
        self.assertEqual(runner.calls[0][0][0:2], ["issue", "create"])

    async def test_push_skips_when_outbound_hash_unchanged(self):
        task = BoardTask(
            id="T:3",
            task="No changes",
            description="Body",
            labels=[],
            board_sync={
                "version": 1,
                "provider": "github",
                "enabled": True,
                "github": {"issue_repo": "owner/repo", "issue_number": 5},
                "last_synced_hash": "",
                "sync_state": "idle",
            },
        )
        settings = github_settings(board_sync_github={
            "github_project_owner": "",
            "github_project_number": 0,
        })
        task.board_sync["last_synced_hash"] = compute_outbound_hash(
            task,
            extract_github_settings(settings),
        )
        runner = FakeGhRunner([])
        provider = GitHubBoardSyncProvider(runner)

        sync = await provider.push_task(task, settings)

        self.assertTrue(sync["skipped"])
        self.assertEqual(runner.calls, [])


if __name__ == "__main__":
    unittest.main()

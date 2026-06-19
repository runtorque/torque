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
    github_issue_close_rule,
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

    async def list_projects(self, owner=None):
        return [{"ok": True, "phase": "list_projects", "owner": owner or "@me"}]

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


def gh_issue_view_rejecting_repository(payload):
    def _response(args, _cwd=None):
        if args[:2] != ["issue", "view"]:
            raise AssertionError(f"Expected issue view call, got: {args}")
        fields = args[args.index("--json") + 1]
        if "repository" in fields.split(","):
            return gh_fail(
                'Unknown JSON field: "repository"\nAvailable fields: assignees, body, labels, number, title, url',
            )
        return gh_ok(payload)

    return _response


def github_settings(**overrides):
    nested = {
        "github_repo": "owner/repo",
        "github_project_owner": "owner",
        "github_project_number": 5,
        "github_project_status_field": "Status",
        "github_lane_status_map": {"In Progress": "Doing"},
        "github_close_issues_via_pr": True,
        "github_create_missing_labels": True,
    }
    nested.update(overrides.pop("board_sync_github", {}))
    return GroupSettings(
        board_sync_provider="github",
        board_sync_enabled=overrides.pop("board_sync_enabled", True),
        board_sync_github=nested,
        **overrides,
    )


def issue_view(
    number=123,
    repo="owner/repo",
    title="Ship it",
    body="Body",
    state="OPEN",
):
    return {
        "id": "I_kw123",
        "number": number,
        "title": title,
        "body": body,
        "url": f"https://github.com/{repo}/issues/{number}",
        "labels": [{"name": "bug"}],
        "assignees": [],
        "state": state,
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
                    {"id": "opt-review", "name": "Review"},
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
        self.assertEqual((await adapter.list_projects("owner"))[0]["owner"], "owner")
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

    async def test_github_settings_defaults_missing_label_creation_to_true_when_unset(self):
        settings = extract_github_settings(GroupSettings(
            board_sync_provider="github",
            board_sync_enabled=True,
            board_sync_github={},
        ))

        self.assertTrue(settings.create_missing_labels)

        explicit_false = extract_github_settings(GroupSettings(
            board_sync_provider="github",
            board_sync_enabled=True,
            board_sync_github={"github_create_missing_labels": False},
        ))
        self.assertFalse(explicit_false.create_missing_labels)

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
        self.assertEqual(result["status_options_list"][0]["name"], "Doing")

    async def test_list_projects_defaults_to_current_user_and_normalizes_metadata(self):
        provider = GitHubBoardSyncProvider(FakeGhRunner([
            gh_ok({
                "projects": [
                    {
                        "number": 7,
                        "title": "Roadmap",
                        "id": "PVT_7",
                        "url": "https://github.com/orgs/acme/projects/7",
                        "owner": {"login": "acme"},
                    },
                    {
                        "number": 8,
                        "name": "Personal",
                        "node_id": "PVT_8",
                        "ownerLogin": "octocat",
                    },
                ]
            }),
        ]))

        projects = await provider.list_projects(None)

        self.assertEqual(projects[0]["name"], "Roadmap")
        self.assertEqual(projects[0]["owner"], "acme")
        self.assertEqual(projects[0]["id"], "PVT_7")
        self.assertEqual(projects[1]["owner"], "octocat")
        self.assertEqual(
            provider.runner.calls[0][0],
            ["project", "list", "--owner", "@me", "--format", "json", "--limit", "100"],
        )

    async def test_list_projects_returns_structured_error_on_gh_failure(self):
        provider = GitHubBoardSyncProvider(FakeGhRunner([
            gh_fail("missing project scope"),
        ]))

        projects = await provider.list_projects("acme")

        self.assertFalse(projects[0]["ok"])
        self.assertEqual(projects[0]["phase"], "list_projects")
        self.assertIn("missing project scope", projects[0]["error"])


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

    async def test_push_issue_view_uses_valid_gh_json_fields(self):
        task = BoardTask(
            id="T:valid-fields",
            task="Valid gh fields",
            description="No repository JSON field",
        )
        settings = github_settings(board_sync_github={
            "github_project_owner": "",
            "github_project_number": 0,
        })
        runner = FakeGhRunner([
            gh_ok("https://github.com/owner/repo/issues/125\n"),
            gh_issue_view_rejecting_repository(
                issue_view(
                    number=125,
                    title="Valid gh fields",
                    body=render_issue_body(task),
                )
            ),
        ])
        provider = GitHubBoardSyncProvider(runner)

        sync = await provider.push_task(task, settings)

        self.assertEqual(sync["sync_state"], "idle")
        self.assertEqual(sync["github"]["issue_number"], 125)
        issue_view_cmd = runner.calls[1][0]
        issue_view_fields = issue_view_cmd[issue_view_cmd.index("--json") + 1]
        self.assertNotIn("repository", issue_view_fields.split(","))
        self.assertNotIn("Unknown JSON field", sync.get("last_error", ""))

    async def test_push_creates_missing_user_labels_by_default_before_sync(self):
        task = BoardTask(
            id="T:1",
            task="Task",
            description="Body",
            labels=[
                "Research",
                "MCP",
                "Browser",
                "Parked",
                "do not dispatch",
                "torque:blocked",
            ],
        )
        settings = github_settings(board_sync_github={
            "github_project_owner": "",
            "github_project_number": 0,
        })
        runner = FakeGhRunner([
            gh_ok({"items": [{"name": "parked"}]}),
            gh_ok(""),
            gh_ok(""),
            gh_ok(""),
            gh_ok(""),
            gh_ok("https://github.com/owner/repo/issues/123\n"),
            gh_ok(issue_view(number=123, title="Task", body=render_issue_body(task))),
        ])
        provider = GitHubBoardSyncProvider(runner)

        sync = await provider.push_task(task, settings)

        self.assertEqual(sync["sync_state"], "idle")
        self.assertEqual(sync["github"]["issue_number"], 123)
        commands = [call[0] for call in runner.calls]
        self.assertEqual(commands[0][:3], ["label", "list", "--repo"])
        self.assertEqual(
            [cmd[:3] for cmd in commands[1:5]],
            [
                ["label", "create", "Research"],
                ["label", "create", "MCP"],
                ["label", "create", "Browser"],
                ["label", "create", "do not dispatch"],
            ],
        )
        self.assertEqual(commands[5][0:2], ["issue", "create"])
        self.assertIn("--label", commands[5])
        self.assertIn("do not dispatch", ",".join(commands[5]))
        self.assertNotIn("torque:blocked", " ".join(commands[5]))

    async def test_push_errors_when_user_label_missing_and_creation_disabled(self):
        task = BoardTask(id="T:1", task="Task", labels=["missing"])
        provider = GitHubBoardSyncProvider(FakeGhRunner([
            gh_ok({"items": [{"name": "bug"}]}),
        ]))

        sync = await provider.push_task(
            task,
            github_settings(board_sync_github={"github_create_missing_labels": False}),
        )

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

    async def test_push_done_derived_task_closes_open_issue_even_when_hash_unchanged(self):
        task = BoardTask(
            id="T:done-derived",
            task="Merged workflow child",
            description="Body",
            lane="Done",
            parent_task_id="T:root",
            pipeline_depth=1,
            pipeline_root_id="T:root",
            board_sync={
                "version": 1,
                "provider": "github",
                "enabled": True,
                "github": {"issue_repo": "owner/repo", "issue_number": 321},
                "last_synced_hash": "",
                "sync_state": "idle",
            },
        )
        settings_obj = github_settings(board_sync_github={
            "github_project_owner": "",
            "github_project_number": 0,
        })
        task.board_sync["last_synced_hash"] = compute_outbound_hash(
            task,
            extract_github_settings(settings_obj),
        )
        runner = FakeGhRunner([
            gh_ok(""),
            gh_ok(issue_view(
                number=321,
                title="Merged workflow child",
                body=render_issue_body(task),
                state="OPEN",
            )),
            gh_ok(""),
            gh_ok(issue_view(
                number=321,
                title="Merged workflow child",
                body=render_issue_body(task),
                state="CLOSED",
            )),
        ])
        provider = GitHubBoardSyncProvider(runner)

        sync = await provider.push_task(task, settings_obj)

        self.assertEqual(sync["sync_state"], "idle")
        self.assertEqual(sync["github"]["issue_state"], "CLOSED")
        self.assertEqual(
            sync["github"]["issue_close_reconcile_reason"],
            "task_done",
        )
        self.assertEqual(sync["github"]["issue_close_reconcile_status"], "closed")
        commands = [call[0] for call in runner.calls]
        self.assertEqual(commands[0][:2], ["issue", "edit"])
        self.assertEqual(commands[2][:3], ["issue", "close", "321"])
        self.assertIn("--reason", commands[2])
        self.assertNotIn("skipped", sync)

    async def test_push_active_blocked_task_does_not_close_open_issue(self):
        task = BoardTask(
            id="T:active",
            task="Active task",
            description="Body",
            lane="In Progress",
            labels=["torque:blocked"],
            board_sync={
                "version": 1,
                "provider": "github",
                "enabled": True,
                "github": {"issue_repo": "owner/repo", "issue_number": 322},
                "sync_state": "idle",
            },
        )
        runner = FakeGhRunner([
            gh_ok(""),
            gh_ok(issue_view(
                number=322,
                title="Active task",
                body=render_issue_body(task),
                state="OPEN",
            )),
        ])
        provider = GitHubBoardSyncProvider(runner)

        sync = await provider.push_task(
            task,
            github_settings(board_sync_github={
                "github_project_owner": "",
                "github_project_number": 0,
            }),
        )

        self.assertEqual(sync["sync_state"], "idle")
        self.assertEqual(sync["github"]["issue_state"], "OPEN")
        commands = [call[0] for call in runner.calls]
        self.assertNotIn(["issue", "close"], [cmd[:2] for cmd in commands])
        self.assertNotIn("issue_close_reconciled_at", sync["github"])

    async def test_push_done_task_already_closed_records_stable_reconciliation(self):
        task = BoardTask(
            id="T:already-closed",
            task="Done task",
            description="Body",
            lane="Done",
            board_sync={
                "version": 1,
                "provider": "github",
                "enabled": True,
                "github": {"issue_repo": "owner/repo", "issue_number": 323},
                "sync_state": "idle",
            },
        )
        runner = FakeGhRunner([
            gh_ok(""),
            gh_ok(issue_view(
                number=323,
                title="Done task",
                body=render_issue_body(task),
                state="CLOSED",
            )),
        ])
        provider = GitHubBoardSyncProvider(runner)

        sync = await provider.push_task(
            task,
            github_settings(board_sync_github={
                "github_project_owner": "",
                "github_project_number": 0,
            }),
        )

        self.assertEqual(sync["sync_state"], "idle")
        self.assertEqual(sync["github"]["issue_state"], "CLOSED")
        self.assertEqual(
            sync["github"]["issue_close_reconcile_status"],
            "already_closed",
        )
        commands = [call[0] for call in runner.calls]
        self.assertNotIn(["issue", "close"], [cmd[:2] for cmd in commands])

    async def test_push_done_task_close_error_is_surfaced_safely(self):
        task = BoardTask(
            id="T:close-error",
            task="Close failure",
            description="Body",
            lane="Done",
            board_sync={
                "version": 1,
                "provider": "github",
                "enabled": True,
                "github": {"issue_repo": "owner/repo", "issue_number": 324},
                "sync_state": "idle",
            },
        )
        runner = FakeGhRunner([
            gh_ok(""),
            gh_ok(issue_view(
                number=324,
                title="Close failure",
                body=render_issue_body(task),
                state="OPEN",
            )),
            gh_fail("permission denied"),
        ])
        provider = GitHubBoardSyncProvider(runner)

        sync = await provider.push_task(
            task,
            github_settings(board_sync_github={
                "github_project_owner": "",
                "github_project_number": 0,
            }),
        )

        self.assertEqual(sync["sync_state"], "error")
        self.assertEqual(sync["phase"], "issue_close")
        self.assertIn("permission denied", sync["last_error"])
        commands = [call[0] for call in runner.calls]
        self.assertEqual(commands[-1][:3], ["issue", "close", "324"])

    async def test_issue_close_rule_sources(self):
        done = BoardTask(id="T:done", task="Done", lane="Done")
        archived = BoardTask(
            id="T:arch",
            task="Archived",
            lane="Archived",
            archived_from_lane="Done",
        )
        merged = BoardTask(
            id="T:merged",
            task="Merged",
            lane="In Progress",
            worktree_boundary={"status": "merged"},
        )
        active = BoardTask(
            id="T:active-rule",
            task="Active",
            lane="In Progress",
            labels=["merged", "blocked"],
        )

        self.assertEqual(github_issue_close_rule(done), (True, "task_done"))
        self.assertEqual(
            github_issue_close_rule(archived),
            (True, "archived_from_done"),
        )
        self.assertEqual(
            github_issue_close_rule(merged),
            (True, "worktree_merged"),
        )
        self.assertEqual(github_issue_close_rule(active), (False, ""))

    async def test_project_status_uses_explicit_task_status_map_then_lane_fallback(self):
        settings = github_settings(board_sync_github={
            "github_lane_status_map": {
                "In Progress": "Doing",
                "On Review": "Review",
            },
        })
        mapped_task = BoardTask(
            id="T:mapped-status",
            task="Mapped status",
            lane="In Progress",
            status="On Review",
            board_sync={
                "version": 1,
                "provider": "github",
                "enabled": True,
                "github": {
                    "issue_repo": "owner/repo",
                    "issue_number": 123,
                },
            },
        )
        runner = FakeGhRunner([
            gh_ok(""),
            gh_ok(issue_view(number=123, title="Mapped status", body=render_issue_body(mapped_task))),
            gh_ok(project_view()),
            gh_ok(field_list()),
            gh_ok({"id": "PVTI_kw"}),
            gh_ok(""),
        ])
        provider = GitHubBoardSyncProvider(runner)

        sync = await provider.push_task(mapped_task, settings)

        self.assertEqual(sync["github"]["status_option_id"], "opt-review")
        self.assertEqual(runner.calls[-1][0][-1], "opt-review")

        fallback_settings = github_settings(board_sync_github={
            "github_lane_status_map": {"In Progress": "Doing"},
        })
        fallback_task = BoardTask(
            id="T:unmapped-status",
            task="Unmapped status",
            lane="In Progress",
            status="On Review",
            board_sync={
                "version": 1,
                "provider": "github",
                "enabled": True,
                "github": {
                    "issue_repo": "owner/repo",
                    "issue_number": 124,
                },
            },
        )
        fallback_runner = FakeGhRunner([
            gh_ok(""),
            gh_ok(issue_view(number=124, title="Unmapped status", body=render_issue_body(fallback_task))),
            gh_ok(project_view()),
            gh_ok(field_list()),
            gh_ok({"id": "PVTI_kw2"}),
            gh_ok(""),
        ])
        fallback_provider = GitHubBoardSyncProvider(fallback_runner)

        fallback_sync = await fallback_provider.push_task(
            fallback_task,
            fallback_settings,
        )

        self.assertEqual(fallback_sync["github"]["status_option_id"], "opt-doing")
        self.assertEqual(fallback_runner.calls[-1][0][-1], "opt-doing")

    async def test_archived_missing_project_status_option_skips_without_touching_github_once(self):
        settings_obj = github_settings(board_sync_github={
            "github_lane_status_map": {
                "Done": "Done",
                "In Progress": "Doing",
            },
            "github_assignee_map": {
                "worker-1": "octocat",
            },
        })
        settings = extract_github_settings(settings_obj)

        def archived_task(task_id, number):
            task = BoardTask(
                id=task_id,
                task="Already synced",
                description="Body",
                lane="Archived",
                archived_from_lane="Done",
                agent_id="",
                board_sync={
                    "version": 1,
                    "provider": "github",
                    "enabled": True,
                    "github": {
                        "issue_repo": "owner/repo",
                        "issue_number": number,
                        "project_item_id": f"PVTI_{number}",
                        "status_option_id": "opt-done",
                        "issue_state": "CLOSED",
                        "issue_close_reconciled_at": "2026-05-20T00:00:00+00:00",
                    },
                    "sync_state": "idle",
                },
            )
            previously_synced = BoardTask(
                id=task_id,
                task=task.task,
                description=task.description,
                lane="Done",
                agent_id="worker-1",
            )
            task.board_sync["last_synced_hash"] = compute_outbound_hash(
                previously_synced,
                settings,
            )
            return task

        first = archived_task("T:archived-1", 123)
        second = archived_task("T:archived-2", 124)
        runner = FakeGhRunner([
            gh_ok(project_view()),
            gh_ok(field_list()),
        ])
        provider = GitHubBoardSyncProvider(runner)

        with self.assertLogs("torque", level="WARNING") as captured:
            first_sync = await provider.push_task(first, settings_obj)
            second_sync = await provider.push_task(second, settings_obj)

        self.assertEqual(first_sync["sync_state"], "idle")
        self.assertEqual(second_sync["sync_state"], "idle")
        self.assertEqual(first_sync["last_error"], "")
        self.assertEqual(second_sync["last_error"], "")
        self.assertEqual(first_sync["last_synced_hash"], compute_outbound_hash(first, settings))
        self.assertEqual(second_sync["last_synced_hash"], compute_outbound_hash(second, settings))
        commands = [call[0] for call in runner.calls]
        self.assertNotIn(["issue", "edit"], [cmd[:2] for cmd in commands])
        self.assertNotIn(["project", "item-edit"], [cmd[:2] for cmd in commands])
        warnings = [
            line for line in captured.output
            if "Skipping GitHub Project status update" in line
        ]
        self.assertEqual(len(warnings), 1)
        self.assertIn("Archived", warnings[0])

    async def test_missing_project_status_option_skips_status_update_without_error(self):
        task = BoardTask(
            id="T:unknown-lane",
            task="Unknown lane",
            description="Body",
            lane="Needs Triage",
            board_sync={
                "version": 1,
                "provider": "github",
                "enabled": True,
                "github": {
                    "issue_repo": "owner/repo",
                    "issue_number": 125,
                    "project_item_id": "PVTI_125",
                },
            },
        )
        runner = FakeGhRunner([
            gh_ok(""),
            gh_ok(issue_view(number=125, title="Unknown lane", body=render_issue_body(task))),
            gh_ok(project_view()),
            gh_ok(field_list()),
        ])
        provider = GitHubBoardSyncProvider(runner)

        with self.assertLogs("torque", level="WARNING") as captured:
            sync = await provider.push_task(task, github_settings())

        self.assertEqual(sync["sync_state"], "idle")
        self.assertEqual(sync["last_error"], "")
        commands = [call[0] for call in runner.calls]
        self.assertIn(["issue", "edit"], [cmd[:2] for cmd in commands])
        self.assertNotIn(["project", "item-edit"], [cmd[:2] for cmd in commands])
        self.assertIn("Needs Triage", "\n".join(captured.output))

    async def test_provider_reuses_project_and_label_caches_across_batch_instance(self):
        settings = github_settings(board_sync_github={
            "github_project_owner": "owner",
            "github_project_number": 5,
        })
        first = BoardTask(
            id="T:cache-1",
            task="Cache one",
            lane="In Progress",
            labels=["bug"],
            board_sync={
                "version": 1,
                "provider": "github",
                "enabled": True,
                "github": {
                    "issue_repo": "owner/repo",
                    "issue_number": 201,
                    "project_item_id": "PVTI_201",
                },
            },
        )
        second = BoardTask(
            id="T:cache-2",
            task="Cache two",
            lane="In Progress",
            labels=["bug"],
            board_sync={
                "version": 1,
                "provider": "github",
                "enabled": True,
                "github": {
                    "issue_repo": "owner/repo",
                    "issue_number": 202,
                    "project_item_id": "PVTI_202",
                },
            },
        )
        runner = FakeGhRunner([
            gh_ok({"items": [{"name": "bug"}]}),
            gh_ok(""),
            gh_ok(issue_view(number=201, title="Cache one", body=render_issue_body(first))),
            gh_ok(project_view()),
            gh_ok(field_list()),
            gh_ok(""),
            gh_ok(""),
            gh_ok(issue_view(number=202, title="Cache two", body=render_issue_body(second))),
            gh_ok(""),
        ])
        provider = GitHubBoardSyncProvider(runner)

        first_sync = await provider.push_task(first, settings)
        second_sync = await provider.push_task(second, settings)

        self.assertEqual(first_sync["sync_state"], "idle")
        self.assertEqual(second_sync["sync_state"], "idle")
        commands = [call[0] for call in runner.calls]
        self.assertEqual(
            sum(1 for cmd in commands if cmd[:2] == ["label", "list"]),
            1,
        )
        self.assertEqual(
            sum(1 for cmd in commands if cmd[:2] == ["project", "field-list"]),
            1,
        )


class GitHubPullTests(unittest.IsolatedAsyncioTestCase):
    async def test_pull_preview_returns_field_diff_without_mutating_task(self):
        task = BoardTask(
            id="TORQUE:510",
            task="Local title",
            description="Local body",
            group="Torque",
            labels=["local"],
            provider="github",
            external_id="owner/repo#123",
        )
        provider = GitHubBoardSyncProvider(FakeGhRunner([
            gh_ok(issue_view(
                title="Remote title",
                body=(
                    "Remote body\n\n---\nSynced from Torque task TORQUE:510.\n"
                    "<!-- torque-sync:v1 task_id=TORQUE:510 -->"
                ),
            ) | {"labels": [{"name": "bug"}, {"name": "remote"}]}),
        ]))

        preview = await provider.pull_task(task, github_settings())

        self.assertTrue(preview["ok"])
        self.assertEqual(preview["diff_count"], 3)
        self.assertEqual(preview["changes"]["task"]["remote"], "Remote title")
        self.assertEqual(preview["changes"]["description"]["remote"], "Remote body")
        self.assertEqual(preview["changes"]["labels"]["remote"], ["bug", "remote"])
        self.assertIn(
            {"field": "task", "local": "Local title", "remote": "Remote title"},
            preview["diff"],
        )
        self.assertEqual(task.task, "Local title")
        self.assertEqual(task.description, "Local body")
        self.assertEqual(task.labels, ["local"])

    async def test_pull_apply_returns_only_selected_remote_fields(self):
        task = BoardTask(
            id="TORQUE:511",
            task="Local title",
            description="Local body",
            group="Torque",
            labels=["local"],
            provider="github",
            external_id="owner/repo#123",
        )
        provider = GitHubBoardSyncProvider(FakeGhRunner([
            gh_ok(issue_view(
                title="Remote title",
                body="Remote body",
            ) | {"labels": [{"name": "remote"}]}),
        ]))

        applied = await provider.apply_pull(
            task,
            github_settings(),
            ["description"],
        )

        self.assertTrue(applied["ok"])
        self.assertEqual(applied["requested_fields"], ["description"])
        self.assertEqual(applied["applied_fields"], ["description"])
        self.assertEqual(applied["fields"], {"description": "Remote body"})

    async def test_pull_apply_missing_issue_returns_structured_error(self):
        task = BoardTask(
            id="TORQUE:512",
            task="Gone",
            provider="github",
            external_id="owner/repo#404",
        )
        provider = GitHubBoardSyncProvider(FakeGhRunner([
            gh_fail("GraphQL: Could not resolve to an Issue with the number of 404."),
        ]))

        result = await provider.apply_pull(task, github_settings(), ["task"])

        self.assertFalse(result["ok"])
        self.assertEqual(result["phase"], "pull_preview")
        self.assertEqual(result["provider_phase"], "issue_view")
        self.assertEqual(result["error_code"], "external_not_found")
        self.assertEqual(result["issue_number"], 404)

    async def test_project_item_list_includes_import_matching_metadata(self):
        body = (
            "Remote project body\n\n---\nSynced from Torque task TORQUE:513.\n"
            "<!-- torque-sync:v1 task_id=TORQUE:513 group=Torque -->"
        )
        provider = GitHubBoardSyncProvider(FakeGhRunner([
            gh_ok({
                "items": [{
                    "id": "PVTI_1",
                    "status": "Todo",
                    "content": {
                        "type": "Issue",
                        "number": 55,
                        "title": "Remote issue",
                        "body": body,
                        "url": "https://github.com/owner/repo/issues/55",
                        "repository": {"nameWithOwner": "owner/repo"},
                        "labels": [{"name": "bug"}],
                        "state": "OPEN",
                    },
                }]
            }),
        ]))

        items = await provider.list_external_items(github_settings())

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["external_id"], "owner/repo#55")
        self.assertEqual(items[0]["external_url"], "https://github.com/owner/repo/issues/55")
        self.assertEqual(items[0]["description"], "Remote project body")
        self.assertEqual(items[0]["labels"], ["bug"])
        self.assertEqual(items[0]["torque_marker"]["task_id"], "TORQUE:513")
        self.assertEqual(items[0]["matched_task_id"], "TORQUE:513")


if __name__ == "__main__":
    unittest.main()

import json
import unittest
from unittest.mock import patch

from torque.worktree import WorktreeManager


class FakeProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout.encode() if isinstance(stdout, str) else stdout
        self.stderr = stderr.encode() if isinstance(stderr, str) else stderr

    async def communicate(self):
        return self.stdout, self.stderr


class WorktreeGithubPrimitiveTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.mgr = WorktreeManager()

    def _fake_exec(self, expected):
        calls = []
        expected = list(expected)

        async def fake(*cmd, **kwargs):
            calls.append((list(cmd), dict(kwargs)))
            if not expected:
                raise AssertionError(f"Unexpected command: {cmd}")
            matcher, response = expected.pop(0)
            if callable(matcher):
                self.assertTrue(
                    matcher(list(cmd), kwargs),
                    f"Command did not match: {cmd}",
                )
            else:
                self.assertEqual(list(cmd), matcher)
            if isinstance(response, BaseException):
                raise response
            return response

        return fake, calls, expected

    async def test_github_preflight_missing_gh_reports_preflight_phase(self):
        fake, _calls, remaining = self._fake_exec([
            (["gh", "--version"], FileNotFoundError("gh")),
        ])

        with patch("torque.worktree.asyncio.create_subprocess_exec",
                   side_effect=fake):
            result = await self.mgr.github_preflight("/wt")

        self.assertFalse(result["ok"])
        self.assertEqual(result["phase"], "github_preflight")
        self.assertIn("GitHub CLI", result["error"])
        self.assertEqual(remaining, [])

    async def test_github_preflight_auth_and_repo_view_failures_are_structured(self):
        cases = [
            (
                "auth",
                [
                    (["gh", "--version"], FakeProcess()),
                    (["gh", "auth", "status"],
                     FakeProcess(returncode=1, stderr="not logged in")),
                ],
                "authentication",
            ),
            (
                "repo",
                [
                    (["gh", "--version"], FakeProcess()),
                    (["gh", "auth", "status"], FakeProcess()),
                    (["gh", "repo", "view", "--json", "nameWithOwner,url"],
                     FakeProcess(returncode=1, stderr="not a GitHub repo")),
                ],
                "GitHub repository",
            ),
        ]

        for _name, sequence, expected_error in cases:
            fake, calls, remaining = self._fake_exec(sequence)
            with patch("torque.worktree.asyncio.create_subprocess_exec",
                       side_effect=fake):
                result = await self.mgr.github_preflight("/wt")

            self.assertFalse(result["ok"])
            self.assertEqual(result["phase"], "github_preflight")
            self.assertIn(expected_error, result["error"])
            self.assertTrue(
                all(
                    call[1].get("cwd") in {None, "/wt"}
                    for call in calls
                )
            )
            self.assertEqual(remaining, [])

    async def test_github_remote_selection_prefers_origin_then_first_github_remote(self):
        origin_case = (
            "upstream\tgit@github.com:acme/upstream.git (fetch)\n"
            "origin\thttps://github.com/acme/repo.git (fetch)\n"
        )
        fake, _calls, remaining = self._fake_exec([
            (["git", "-C", "/wt", "remote", "-v"],
             FakeProcess(stdout=origin_case)),
        ])
        with patch("torque.worktree.asyncio.create_subprocess_exec",
                   side_effect=fake):
            result = await self.mgr.github_select_remote("/wt")

        self.assertTrue(result["ok"])
        self.assertEqual(result["remote"], "origin")
        self.assertEqual(remaining, [])

        non_origin_case = (
            "mirror\tssh://example.com/acme/repo.git (fetch)\n"
            "upstream\tgit@github.com:acme/upstream.git (fetch)\n"
        )
        fake, _calls, remaining = self._fake_exec([
            (["git", "-C", "/wt", "remote", "-v"],
             FakeProcess(stdout=non_origin_case)),
        ])
        with patch("torque.worktree.asyncio.create_subprocess_exec",
                   side_effect=fake):
            result = await self.mgr.github_select_remote("/wt")

        self.assertTrue(result["ok"])
        self.assertEqual(result["remote"], "upstream")
        self.assertEqual(remaining, [])

    async def test_push_branch_failure_is_structured(self):
        fake, _calls, remaining = self._fake_exec([
            (
                ["git", "-C", "/wt", "push", "-u", "origin", "feature"],
                FakeProcess(returncode=1, stderr="rejected"),
            ),
        ])

        with patch("torque.worktree.asyncio.create_subprocess_exec",
                   side_effect=fake):
            result = await self.mgr.github_push_branch(
                "/wt", "origin", "feature"
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["phase"], "push_branch")
        self.assertIn("rejected", result["error"])
        self.assertEqual(remaining, [])

    async def test_create_or_reuse_pr_reuses_existing_pr(self):
        view = {
            "url": "https://github.com/acme/repo/pull/7",
            "number": 7,
            "headRefOid": "abc123",
            "state": "OPEN",
        }
        fake, _calls, remaining = self._fake_exec([
            (
                self._gh_pr_view_matcher("feature"),
                FakeProcess(stdout=json.dumps(view)),
            ),
        ])

        with patch("torque.worktree.asyncio.create_subprocess_exec",
                   side_effect=fake):
            result = await self.mgr.github_create_or_reuse_pr(
                "/wt", "feature", "main", title="Feature", body="Body"
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["phase"], "pr_create")
        self.assertTrue(result["existing"])
        self.assertEqual(result["url"], view["url"])
        self.assertEqual(result["number"], 7)
        self.assertEqual(result["head_sha"], "abc123")
        self.assertEqual(remaining, [])

    async def test_create_or_reuse_pr_creates_new_pr(self):
        created = {
            "url": "https://github.com/acme/repo/pull/9",
            "number": 9,
            "body": "Body",
            "headRefOid": "def456",
            "state": "OPEN",
        }
        fake, _calls, remaining = self._fake_exec([
            (
                self._gh_pr_view_matcher("feature"),
                FakeProcess(returncode=1, stderr="no pull requests found"),
            ),
            (
                [
                    "gh", "pr", "create",
                    "--base", "main",
                    "--head", "feature",
                    "--title", "Feature",
                    "--body", "Body",
                ],
                FakeProcess(stdout="https://github.com/acme/repo/pull/9\n"),
            ),
            (
                self._gh_pr_view_matcher("feature"),
                FakeProcess(stdout=json.dumps(created)),
            ),
        ])

        with patch("torque.worktree.asyncio.create_subprocess_exec",
                   side_effect=fake):
            result = await self.mgr.github_create_or_reuse_pr(
                "/wt", "feature", "main", title="Feature", body="Body"
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["phase"], "pr_create")
        self.assertFalse(result["existing"])
        self.assertEqual(result["url"], created["url"])
        self.assertEqual(result["number"], 9)
        self.assertEqual(result["body"], "Body")
        self.assertEqual(result["head_sha"], "def456")
        self.assertEqual(remaining, [])

    async def test_github_pr_edit_body_updates_existing_pr_body(self):
        updated = {
            "url": "https://github.com/acme/repo/pull/7",
            "number": 7,
            "body": "Body\n\nLinked Torque issues:\n- Closes #12",
            "headRefOid": "abc123",
            "state": "OPEN",
        }
        body = updated["body"]
        fake, _calls, remaining = self._fake_exec([
            (
                [
                    "gh", "pr", "edit", "7",
                    "--body", body,
                ],
                FakeProcess(),
            ),
            (
                self._gh_pr_view_matcher("7"),
                FakeProcess(stdout=json.dumps(updated)),
            ),
        ])

        with patch("torque.worktree.asyncio.create_subprocess_exec",
                   side_effect=fake):
            result = await self.mgr.github_pr_edit_body("/wt", 7, body)

        self.assertTrue(result["ok"])
        self.assertEqual(result["phase"], "pr_edit_body")
        self.assertEqual(result["body"], body)
        self.assertEqual(result["number"], 7)
        self.assertEqual(remaining, [])

    async def test_github_pr_edit_body_failure_is_structured(self):
        fake, _calls, remaining = self._fake_exec([
            (
                [
                    "gh", "pr", "edit", "7",
                    "--body", "Body",
                ],
                FakeProcess(returncode=1, stderr="permission denied"),
            ),
        ])

        with patch("torque.worktree.asyncio.create_subprocess_exec",
                   side_effect=fake):
            result = await self.mgr.github_pr_edit_body("/wt", 7, "Body")

        self.assertFalse(result["ok"])
        self.assertEqual(result["phase"], "pr_edit_body")
        self.assertIn("permission denied", result["error"])
        self.assertEqual(remaining, [])

    async def test_request_squash_merge_reports_immediate_merge(self):
        merged = {
            "url": "https://github.com/acme/repo/pull/7",
            "number": 7,
            "headRefOid": "abc123",
            "state": "MERGED",
            "mergedAt": "2026-05-19T18:00:00Z",
            "mergeCommit": {"oid": "merge789"},
            "mergeStateStatus": "CLEAN",
        }
        fake, _calls, remaining = self._fake_exec([
            (
                [
                    "gh", "pr", "merge", "7",
                    "--squash",
                    "--match-head-commit", "abc123",
                    "--subject", "Subject",
                    "--body", "Body",
                ],
                FakeProcess(),
            ),
            (
                self._gh_pr_view_matcher("7"),
                FakeProcess(stdout=json.dumps(merged)),
            ),
        ])

        with patch("torque.worktree.asyncio.create_subprocess_exec",
                   side_effect=fake):
            result = await self.mgr.github_request_squash_merge(
                "/wt", 7, "abc123", subject="Subject", body="Body"
            )

        self.assertTrue(result["ok"])
        self.assertFalse(result["pending"])
        self.assertEqual(result["phase"], "pr_merge")
        self.assertEqual(result["merge_commit_sha"], "merge789")
        self.assertEqual(remaining, [])

    async def test_request_squash_merge_reports_pending_auto_merge(self):
        pending = {
            "url": "https://github.com/acme/repo/pull/7",
            "number": 7,
            "headRefOid": "abc123",
            "state": "OPEN",
            "mergedAt": None,
            "mergeStateStatus": "BLOCKED",
        }
        fake, _calls, remaining = self._fake_exec([
            (
                [
                    "gh", "pr", "merge", "7",
                    "--squash",
                    "--match-head-commit", "abc123",
                    "--subject", "Subject",
                    "--body", "",
                    "--auto",
                ],
                FakeProcess(stdout="Auto-merge enabled\n"),
            ),
            (
                self._gh_pr_view_matcher("7"),
                FakeProcess(stdout=json.dumps(pending)),
            ),
        ])

        with patch("torque.worktree.asyncio.create_subprocess_exec",
                   side_effect=fake):
            result = await self.mgr.github_request_squash_merge(
                "/wt", 7, "abc123", subject="Subject", auto=True
            )

        self.assertTrue(result["ok"])
        self.assertTrue(result["pending"])
        self.assertEqual(result["phase"], "pr_merge")
        self.assertEqual(result["merge_state"], "BLOCKED")
        self.assertEqual(remaining, [])

    async def test_request_squash_merge_failure_includes_pr_status(self):
        status = {
            "url": "https://github.com/acme/repo/pull/7",
            "number": 7,
            "headRefOid": "abc123",
            "state": "OPEN",
            "mergeStateStatus": "DIRTY",
        }
        fake, _calls, remaining = self._fake_exec([
            (
                [
                    "gh", "pr", "merge", "7",
                    "--squash",
                    "--match-head-commit", "abc123",
                    "--body", "",
                ],
                FakeProcess(returncode=1, stderr="merge blocked"),
            ),
            (
                self._gh_pr_view_matcher("7"),
                FakeProcess(stdout=json.dumps(status)),
            ),
        ])

        with patch("torque.worktree.asyncio.create_subprocess_exec",
                   side_effect=fake):
            result = await self.mgr.github_request_squash_merge(
                "/wt", 7, "abc123"
            )

        self.assertFalse(result["ok"])
        self.assertFalse(result["pending"])
        self.assertEqual(result["phase"], "pr_merge")
        self.assertEqual(result["merge_state"], "DIRTY")
        self.assertIn("merge blocked", result["error"])
        self.assertEqual(remaining, [])

    async def test_remote_base_sync_uses_merge_when_base_checked_out(self):
        fake, _calls, remaining = self._fake_exec([
            (
                [
                    "git", "-C", "/wt",
                    "fetch", "--prune", "origin",
                    "+refs/heads/main:refs/remotes/origin/main",
                ],
                FakeProcess(),
            ),
            (
                ["git", "-C", "/repo", "rev-parse", "main"],
                FakeProcess(stdout="base_sha\n"),
            ),
            (
                ["git", "-C", "/repo", "rev-parse",
                 "refs/remotes/origin/main"],
                FakeProcess(stdout="remote_sha\n"),
            ),
            (
                ["git", "-C", "/repo",
                 "merge-base", "--is-ancestor",
                 "main", "refs/remotes/origin/main"],
                FakeProcess(),
            ),
            (
                ["git", "-C", "/repo", "symbolic-ref", "--short", "HEAD"],
                FakeProcess(stdout="main\n"),
            ),
            (
                ["git", "-C", "/repo",
                 "merge", "--ff-only", "refs/remotes/origin/main"],
                FakeProcess(),
            ),
        ])

        with patch("torque.worktree.asyncio.create_subprocess_exec",
                   side_effect=fake):
            result = await self.mgr.github_sync_remote_base(
                "/wt", "/repo", "origin", "main"
            )

        self.assertTrue(result["ok"])
        self.assertTrue(result["synced"])
        self.assertEqual(result["phase"], "remote_base_sync")
        self.assertEqual(remaining, [])

    async def test_remote_base_sync_uses_update_ref_when_base_not_checked_out(self):
        fake, _calls, remaining = self._fake_exec([
            (
                [
                    "git", "-C", "/wt",
                    "fetch", "--prune", "origin",
                    "+refs/heads/main:refs/remotes/origin/main",
                ],
                FakeProcess(),
            ),
            (
                ["git", "-C", "/repo", "rev-parse", "main"],
                FakeProcess(stdout="base_sha\n"),
            ),
            (
                ["git", "-C", "/repo", "rev-parse",
                 "refs/remotes/origin/main"],
                FakeProcess(stdout="remote_sha\n"),
            ),
            (
                ["git", "-C", "/repo",
                 "merge-base", "--is-ancestor",
                 "main", "refs/remotes/origin/main"],
                FakeProcess(),
            ),
            (
                ["git", "-C", "/repo", "symbolic-ref", "--short", "HEAD"],
                FakeProcess(stdout="feature\n"),
            ),
            (
                ["git", "-C", "/repo",
                 "update-ref", "refs/heads/main", "remote_sha", "base_sha"],
                FakeProcess(),
            ),
        ])

        with patch("torque.worktree.asyncio.create_subprocess_exec",
                   side_effect=fake):
            result = await self.mgr.github_sync_remote_base(
                "/wt", "/repo", "origin", "main"
            )

        self.assertTrue(result["ok"])
        self.assertTrue(result["synced"])
        self.assertEqual(result["phase"], "remote_base_sync")
        self.assertEqual(remaining, [])

    def _gh_pr_view_matcher(self, selector):
        def matcher(cmd, kwargs):
            return (
                cmd[0:4] == ["gh", "pr", "view", str(selector)]
                and cmd[4] == "--json"
                and "url" in cmd[5]
                and "statusCheckRollup" in cmd[5]
                and kwargs.get("cwd") == "/wt"
            )

        return matcher


if __name__ == "__main__":
    unittest.main()

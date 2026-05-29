import asyncio
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from torque.worktree import (
    WorktreeManager,
    _github_host_from_url,
)


class GithubHostFromUrlTests(unittest.TestCase):
    def test_parses_common_remote_url_forms(self):
        cases = {
            "https://github.com/acme/repo.git": "github.com",
            "https://user@github.com/acme/repo.git": "github.com",
            "git@github.com:acme/repo.git": "github.com",
            "ssh://git@github.com:22/acme/repo.git": "github.com",
            "git@github.ol.epicgames.net:epic/torque.git":
                "github.ol.epicgames.net",
            "https://GitHub.com/Acme/Repo.git": "github.com",
            "": "",
            "not a url": "",
        }
        for url, expected in cases.items():
            self.assertEqual(_github_host_from_url(url), expected, url)


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

    def _git(self, *args, cwd=None, check=True):
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if check and proc.returncode != 0:
            self.fail(
                f"git {' '.join(args)} failed\n"
                f"stdout={proc.stdout}\nstderr={proc.stderr}"
            )
        return proc

    def _seed_squash_remote_case(self, tmpdir, *, squash_remote=True):
        root = Path(tmpdir)
        remote = root / "remote.git"
        repo = root / "repo"
        self._git("init", "--bare", str(remote))
        self._git("clone", str(remote), str(repo))
        self._git("config", "user.email", "tester@example.com", cwd=repo)
        self._git("config", "user.name", "Tester", cwd=repo)
        (repo / "base.txt").write_text("base\n")
        self._git("add", "base.txt", cwd=repo)
        self._git("commit", "-m", "base", cwd=repo)
        self._git("branch", "-M", "main", cwd=repo)
        self._git("push", "-u", "origin", "main", cwd=repo)

        self._git("switch", "-c", "feature", cwd=repo)
        (repo / "feature.txt").write_text("feature\n")
        self._git("add", "feature.txt", cwd=repo)
        self._git("commit", "-m", "feature work", cwd=repo)
        self._git("push", "-u", "origin", "feature", cwd=repo)
        remote_old = self._git(
            "rev-parse", "origin/feature", cwd=repo
        ).stdout.strip()

        self._git("switch", "main", cwd=repo)
        if squash_remote:
            self._git("merge", "--squash", "feature", cwd=repo)
            self._git("commit", "-m", "squash feature", cwd=repo)
            self._git("push", "origin", "main", cwd=repo)
        main_tip = self._git("rev-parse", "main", cwd=repo).stdout.strip()

        self._git("switch", "-C", "feature", "main", cwd=repo)
        (repo / "next.txt").write_text("next\n")
        self._git("add", "next.txt", cwd=repo)
        self._git("commit", "-m", "next work", cwd=repo)
        local_tip = self._git("rev-parse", "feature", cwd=repo).stdout.strip()
        return repo, remote_old, main_tip, local_tip

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
        origin_v = "origin\thttps://github.com/acme/repo.git (fetch)\n"
        cases = [
            (
                "auth",
                [
                    (["gh", "--version"], FakeProcess()),
                    (["git", "-C", "/wt", "remote", "-v"],
                     FakeProcess(stdout=origin_v)),
                    (["gh", "auth", "status", "--hostname", "github.com"],
                     FakeProcess(returncode=1, stderr="not logged in")),
                ],
                "authentication",
            ),
            (
                "repo",
                [
                    (["gh", "--version"], FakeProcess()),
                    (["git", "-C", "/wt", "remote", "-v"],
                     FakeProcess(stdout=origin_v)),
                    (["gh", "auth", "status", "--hostname", "github.com"],
                     FakeProcess()),
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

    async def test_github_preflight_scopes_auth_to_target_remote_host(self):
        """Auth check targets the origin remote's host, not all gh accounts.

        This is the regression scenario: ``gh`` has two accounts (github.com,
        authed; an enterprise host whose keyring login times out). The
        preflight must scope ``gh auth status`` to the target host
        (github.com) so the unrelated, unreachable account never blocks it.
        """
        origin_v = "origin\tgit@github.com:runtorque/torque.git (fetch)\n"
        fake, calls, remaining = self._fake_exec([
            (["gh", "--version"], FakeProcess()),
            (["git", "-C", "/wt", "remote", "-v"],
             FakeProcess(stdout=origin_v)),
            (["gh", "auth", "status", "--hostname", "github.com"],
             FakeProcess()),
            (["gh", "repo", "view", "--json", "nameWithOwner,url"],
             FakeProcess(stdout=json.dumps({
                 "nameWithOwner": "runtorque/torque",
                 "url": "https://github.com/runtorque/torque",
             }))),
        ])

        with patch("torque.worktree.asyncio.create_subprocess_exec",
                   side_effect=fake):
            result = await self.mgr.github_preflight("/wt")

        self.assertTrue(result["ok"])
        self.assertEqual(result["name_with_owner"], "runtorque/torque")
        # The auth check was scoped to the resolved target host; a bare
        # ``gh auth status`` (which would inspect all accounts) never ran.
        auth_calls = [
            c[0] for c in calls if c[0][:3] == ["gh", "auth", "status"]
        ]
        self.assertEqual(
            auth_calls,
            [["gh", "auth", "status", "--hostname", "github.com"]],
        )
        self.assertEqual(remaining, [])

    async def test_github_preflight_host_skips_unrelated_enterprise_origin(self):
        """When origin is a non-github.com host, the auth scope follows the
        github.com remote that the merge will actually push to."""
        remote_v = (
            "origin\tgit@github.ol.epicgames.net:epic/fork.git (fetch)\n"
            "upstream\thttps://github.com/runtorque/torque.git (fetch)\n"
        )
        fake, calls, remaining = self._fake_exec([
            (["gh", "--version"], FakeProcess()),
            (["git", "-C", "/wt", "remote", "-v"],
             FakeProcess(stdout=remote_v)),
            (["gh", "auth", "status", "--hostname", "github.com"],
             FakeProcess()),
            (["gh", "repo", "view", "--json", "nameWithOwner,url"],
             FakeProcess(stdout=json.dumps({
                 "nameWithOwner": "runtorque/torque",
                 "url": "https://github.com/runtorque/torque",
             }))),
        ])

        with patch("torque.worktree.asyncio.create_subprocess_exec",
                   side_effect=fake):
            result = await self.mgr.github_preflight("/wt")

        self.assertTrue(result["ok"])
        auth_calls = [
            c[0] for c in calls if c[0][:3] == ["gh", "auth", "status"]
        ]
        self.assertEqual(
            auth_calls,
            [["gh", "auth", "status", "--hostname", "github.com"]],
        )
        self.assertEqual(remaining, [])

    async def test_github_preflight_falls_back_to_unscoped_auth_without_remote(self):
        """With no GitHub remote, auth check stays host-agnostic."""
        fake, calls, remaining = self._fake_exec([
            (["gh", "--version"], FakeProcess()),
            (["git", "-C", "/wt", "remote", "-v"],
             FakeProcess(stdout="origin\tssh://example.com/x/y.git (fetch)\n")),
            (["gh", "auth", "status"], FakeProcess()),
            (["gh", "repo", "view", "--json", "nameWithOwner,url"],
             FakeProcess(stdout=json.dumps({
                 "nameWithOwner": "x/y",
                 "url": "https://example.com/x/y",
             }))),
        ])

        with patch("torque.worktree.asyncio.create_subprocess_exec",
                   side_effect=fake):
            result = await self.mgr.github_preflight("/wt")

        self.assertTrue(result["ok"])
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

    async def test_push_branch_auto_force_with_lease_after_squash_reset(self):
        with tempfile.TemporaryDirectory(prefix="torque-push-safe-") as tmp:
            repo, remote_old, main_tip, local_tip = await asyncio.to_thread(
                self._seed_squash_remote_case,
                tmp,
                squash_remote=True,
            )

            initial = await self.mgr.github_push_branch(
                str(repo), "origin", "feature"
            )
            self.assertFalse(initial["ok"])
            self.assertIn("non-fast-forward", initial["error"])
            # In the reused-branch case the old remote PR head is not an
            # ancestor of the reset local branch; it is safe because the
            # remote branch's tree is already incorporated into remote main.
            self.assertNotEqual(
                (
                    await asyncio.to_thread(
                        self._git,
                        "merge-base",
                        "--is-ancestor",
                        remote_old,
                        local_tip,
                        cwd=repo,
                        check=False,
                    )
                ).returncode,
                0,
            )
            self.assertEqual(
                (
                    await asyncio.to_thread(
                        self._git,
                        "merge-base",
                        "--is-ancestor",
                        main_tip,
                        local_tip,
                        cwd=repo,
                        check=False,
                    )
                ).returncode,
                0,
            )

            result = await self.mgr.github_force_push_branch_with_lease_if_safe(
                str(repo),
                "origin",
                "feature",
                base_branch="main",
                push_error=initial,
            )

            self.assertTrue(result["ok"], result)
            self.assertTrue(result["auto_force_push"])
            self.assertTrue(result["force_with_lease"])
            self.assertEqual(result["auto_force_reason"], "remote_merged_to_base")
            self.assertEqual(result["force_lease_sha"], remote_old)
            remote_after = (
                await asyncio.to_thread(
                    self._git,
                    "ls-remote",
                    "origin",
                    "refs/heads/feature",
                    cwd=repo,
                )
            ).stdout.split()[0]
            self.assertEqual(remote_after, local_tip)

    async def test_push_branch_auto_force_rejects_unmerged_remote_work(self):
        with tempfile.TemporaryDirectory(prefix="torque-push-unsafe-") as tmp:
            repo, remote_old, _main_tip, _local_tip = await asyncio.to_thread(
                self._seed_squash_remote_case,
                tmp,
                squash_remote=False,
            )

            initial = await self.mgr.github_push_branch(
                str(repo), "origin", "feature"
            )
            self.assertFalse(initial["ok"])
            self.assertIn("non-fast-forward", initial["error"])

            result = await self.mgr.github_force_push_branch_with_lease_if_safe(
                str(repo),
                "origin",
                "feature",
                base_branch="main",
                push_error=initial,
            )

            self.assertFalse(result["ok"])
            self.assertTrue(result["non_fast_forward"])
            self.assertFalse(result["auto_force_push"])
            self.assertFalse(result["safety_gate_passed"])
            self.assertIn("refusing auto force-push", result["error"])
            remote_after = (
                await asyncio.to_thread(
                    self._git,
                    "ls-remote",
                    "origin",
                    "refs/heads/feature",
                    cwd=repo,
                )
            ).stdout.split()[0]
            self.assertEqual(remote_after, remote_old)

    async def test_push_branch_auto_force_allows_remote_ancestor_of_local(self):
        fake, _calls, remaining = self._fake_exec([
            (
                ["git", "-C", "/wt", "status", "--porcelain"],
                FakeProcess(),
            ),
            (
                [
                    "git", "-C", "/wt",
                    "fetch", "--no-tags", "origin",
                    "+refs/heads/feature:refs/remotes/origin/feature",
                ],
                FakeProcess(),
            ),
            (
                [
                    "git", "-C", "/wt",
                    "fetch", "--no-tags", "origin",
                    "+refs/heads/main:refs/remotes/origin/main",
                ],
                FakeProcess(),
            ),
            (
                [
                    "git", "-C", "/wt",
                    "rev-parse", "--verify",
                    "refs/remotes/origin/feature^{commit}",
                ],
                FakeProcess(stdout="remote-sha\n"),
            ),
            (
                [
                    "git", "-C", "/wt",
                    "rev-parse", "--verify", "feature^{commit}",
                ],
                FakeProcess(stdout="local-sha\n"),
            ),
            (
                [
                    "git", "-C", "/wt",
                    "rev-parse", "--verify",
                    "refs/remotes/origin/main^{commit}",
                ],
                FakeProcess(stdout="base-sha\n"),
            ),
            (
                [
                    "git", "-C", "/wt",
                    "merge-base", "--is-ancestor",
                    "base-sha", "local-sha",
                ],
                FakeProcess(),
            ),
            (
                [
                    "git", "-C", "/wt",
                    "merge-base", "--is-ancestor",
                    "remote-sha", "local-sha",
                ],
                FakeProcess(),
            ),
            (
                [
                    "git", "-C", "/wt",
                    "push",
                    "--force-with-lease=refs/heads/feature:remote-sha",
                    "-u", "origin", "feature",
                ],
                FakeProcess(),
            ),
        ])
        initial = {
            "ok": False,
            "phase": "push_branch",
            "error": "rejected (non-fast-forward)",
        }

        with patch("torque.worktree.asyncio.create_subprocess_exec",
                   side_effect=fake):
            result = await self.mgr.github_force_push_branch_with_lease_if_safe(
                "/wt",
                "origin",
                "feature",
                base_branch="main",
                push_error=initial,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["auto_force_reason"], "remote_ancestor_of_local")
        self.assertTrue(result["auto_force_push"])
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

    async def test_create_or_reuse_pr_ignores_stale_merged_pr_for_branch(self):
        stale = {
            "url": "https://github.com/acme/repo/pull/8",
            "number": 8,
            "headRefOid": "oldsha",
            "state": "MERGED",
            "mergedAt": "2026-05-20T12:00:00Z",
            "mergeCommit": {"oid": "mergesha"},
        }
        created = {
            "url": "https://github.com/acme/repo/pull/10",
            "number": 10,
            "body": "Body",
            "headRefOid": "newsha",
            "state": "OPEN",
        }
        fake, _calls, remaining = self._fake_exec([
            (
                self._gh_pr_view_matcher("feature"),
                FakeProcess(stdout=json.dumps(stale)),
            ),
            (
                [
                    "gh", "pr", "create",
                    "--base", "main",
                    "--head", "feature",
                    "--title", "Feature",
                    "--body", "Body",
                ],
                FakeProcess(stdout="https://github.com/acme/repo/pull/10\n"),
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
        self.assertNotIn("already_merged", result)
        self.assertEqual(result["url"], created["url"])
        self.assertEqual(result["number"], 10)
        self.assertEqual(result["head_sha"], "newsha")
        self.assertEqual(remaining, [])

    async def test_create_or_reuse_pr_no_commits_between_is_already_merged(self):
        err = "GraphQL: No commits between main and feature (createPullRequest)"
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
                FakeProcess(returncode=1, stderr=err),
            ),
            (
                self._gh_pr_view_matcher("feature"),
                FakeProcess(returncode=1, stderr="no pull requests found"),
            ),
        ])

        with patch("torque.worktree.asyncio.create_subprocess_exec",
                   side_effect=fake):
            result = await self.mgr.github_create_or_reuse_pr(
                "/wt", "feature", "main", title="Feature", body="Body"
            )

        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(result["phase"], "pr_create")
        self.assertTrue(result["already_merged"])
        self.assertTrue(result["no_commits_between"])
        self.assertNotIn("error", result)
        self.assertIn("no commits", result["warning"].lower())
        self.assertEqual(result["url"], "")
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
                    "git", "-C", "/repo",
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
                    "git", "-C", "/repo",
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

    async def test_remote_branch_sha_uses_ls_remote_without_updating_refs(self):
        fake, _calls, remaining = self._fake_exec([
            (
                [
                    "git", "-C", "/repo",
                    "ls-remote", "origin", "refs/heads/main",
                ],
                FakeProcess(stdout="remote_sha\trefs/heads/main\n"),
            ),
        ])

        with patch("torque.worktree.asyncio.create_subprocess_exec",
                   side_effect=fake):
            result = await self.mgr.github_remote_branch_sha(
                "/repo", "origin", "main"
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["phase"], "remote_base_ground_truth")
        self.assertEqual(result["sha"], "remote_sha")
        self.assertEqual(result["remote_sha"], "remote_sha")
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

import importlib
import types
import unittest


class _FakeState:
    def __init__(self, *, agent_directory="", default_directory="",
                 weaver_provider="", weaver_boot_command=""):
        self._settings = types.SimpleNamespace(
            agent_directory=agent_directory,
            default_directory=default_directory,
            agent_provider="",
            agent_boot_command="",
            agent_tab_color="",
            tab_color="",
            agent_profile="",
            profile="Default",
            agent_shell="",
            shell="",
            env_vars={},
            agent_env_file="",
            env_file="",
            agent_session_resume=True,
            agent_idle_timeout=5,
            git_worktree=False,
            worktree_base_dir=".loom/worktrees",
            worktree_base_branch="",
            worktree_auto_checkpoint=False,
            checkpoint_on_progress=False,
            worktree_merge_squash=True,
            worktree_symlinks=[],
        )
        self._weaver_settings = types.SimpleNamespace(
            weaver_provider=weaver_provider,
            weaver_boot_command=weaver_boot_command,
        )

    def get_group_settings(self, group):
        return self._settings

    def get_weaver_settings(self, group):
        return self._weaver_settings

    def get_default_command(self):
        return "claude"


class _FakeBridge:
    def __init__(self, current_path="", fail=False):
        self.current_path = current_path
        self.fail = fail

    async def get_launch_context(self):
        if self.fail:
            raise RuntimeError("bridge unavailable")
        terminal_adapter = importlib.import_module("loom.terminal_adapter")
        return terminal_adapter.TerminalLaunchContext(
            current_path=self.current_path,
        )


class _FakeTemplateManager:
    def resolve_agent_config(self, explicit_template, gs, overrides, *,
                             base_dir=""):
        data = dict(overrides or {})
        if "provider" not in data:
            data["provider"] = ""
        if "command" not in data:
            data["command"] = ""
        return data


class AgentLaunchServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.server_agent_mod = importlib.import_module("loom.server_agent")
        self.server_agent_mod = importlib.reload(self.server_agent_mod)

    async def test_resolve_base_dir_prefers_group_directory(self):
        service = self.server_agent_mod.AgentLaunchService(
            state=_FakeState(agent_directory="~/repo"),
            connection=None,
            bridge=_FakeBridge(current_path="/tmp/ignored"),
            worktree_mgr=None,
            template_mgr=_FakeTemplateManager(),
        )

        resolved = await service.resolve_base_dir("backend")

        self.assertTrue(resolved.endswith("/repo"))

    async def test_resolve_base_dir_falls_back_to_bridge_launch_context(self):
        service = self.server_agent_mod.AgentLaunchService(
            state=_FakeState(),
            connection=None,
            bridge=_FakeBridge(current_path="/tmp/project"),
            worktree_mgr=None,
            template_mgr=_FakeTemplateManager(),
        )

        resolved = await service.resolve_base_dir("backend")

        self.assertEqual(resolved, "/tmp/project")

    async def test_resolve_base_dir_returns_empty_when_bridge_lookup_fails(self):
        service = self.server_agent_mod.AgentLaunchService(
            state=_FakeState(),
            connection=None,
            bridge=_FakeBridge(fail=True),
            worktree_mgr=None,
            template_mgr=_FakeTemplateManager(),
        )

        resolved = await service.resolve_base_dir("backend")

        self.assertEqual(resolved, "")

    def test_resolve_weaver_launch_config_prefers_weaver_specific_overrides(self):
        service = self.server_agent_mod.AgentLaunchService(
            state=_FakeState(
                weaver_provider="codex",
                weaver_boot_command="codex --model gpt-5.4",
            ),
            connection=None,
            bridge=_FakeBridge(),
            worktree_mgr=None,
            template_mgr=_FakeTemplateManager(),
        )

        resolved = service.resolve_weaver_launch_config("backend")

        self.assertEqual(resolved["provider"], "codex")
        self.assertEqual(resolved["command"], "codex --model gpt-5.4")

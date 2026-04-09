import importlib
import types
import unittest


class _FakeState:
    def __init__(self, *, agent_directory="", default_directory=""):
        self._settings = types.SimpleNamespace(
            agent_directory=agent_directory,
            default_directory=default_directory,
        )

    def get_group_settings(self, group):
        return self._settings


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
    pass


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

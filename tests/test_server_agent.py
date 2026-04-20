import importlib
import types
import unittest


class _FakeState:
    def __init__(self, *, agent_directory="", default_directory="",
                 weaver_provider="", weaver_boot_command="",
                 weaver_model="", weaver_reasoning_effort="",
                 weaver_directory="", weaver_profile="",
                 weaver_shell="", weaver_tab_color="",
                 agent_provider="", agent_boot_command="",
                 agent_model="", agent_reasoning_effort="",
                 default_command="claude", git_worktree=False):
        self._settings = types.SimpleNamespace(
            agent_directory=agent_directory,
            default_directory=default_directory,
            agent_provider=agent_provider,
            agent_boot_command=agent_boot_command,
            agent_model=agent_model,
            agent_reasoning_effort=agent_reasoning_effort,
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
            git_worktree=git_worktree,
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
            weaver_model=weaver_model,
            weaver_reasoning_effort=weaver_reasoning_effort,
            weaver_directory=weaver_directory,
            weaver_profile=weaver_profile,
            weaver_shell=weaver_shell,
            weaver_tab_color=weaver_tab_color,
        )
        self._default_command = default_command

    def get_group_settings(self, group):
        return self._settings

    def get_weaver_settings(self, group):
        return self._weaver_settings

    def get_default_command(self):
        return self._default_command


class _FakeBridge:
    def __init__(self, current_path="", fail=False):
        self.current_path = current_path
        self.fail = fail
        self.capabilities = types.SimpleNamespace(
            supports_embedded_terminal=False,
        )

    async def get_launch_context(self):
        if self.fail:
            raise RuntimeError("bridge unavailable")
        terminal_adapter = importlib.import_module("loom.terminal_adapter")
        return terminal_adapter.TerminalLaunchContext(
            current_path=self.current_path,
        )


class _CapturingBridge(_FakeBridge):
    def __init__(self, current_path="", fail=False):
        super().__init__(current_path=current_path, fail=fail)
        self.create_session_calls = []

    async def create_session(self, cell, **kwargs):
        self.create_session_calls.append({
            "cell": cell,
            "kwargs": kwargs,
        })


class _FakeTemplateManager:
    def resolve_agent_config(self, explicit_template, gs, overrides, *,
                             base_dir=""):
        data = {
            "provider": getattr(gs, "agent_provider", ""),
            "command": getattr(gs, "agent_boot_command", ""),
            "model": getattr(gs, "agent_model", ""),
            "reasoning_effort": getattr(gs, "agent_reasoning_effort", ""),
        }
        data.update(overrides or {})
        return data


class AgentLaunchServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.server_agent_mod = importlib.import_module("loom.server_agent")
        self.server_agent_mod = importlib.reload(self.server_agent_mod)
        self.state_mod = importlib.import_module("loom.state")
        self.state_mod = importlib.reload(self.state_mod)

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
                git_worktree=True,
            ),
            connection=None,
            bridge=_FakeBridge(),
            worktree_mgr=None,
            template_mgr=_FakeTemplateManager(),
        )

        resolved = service.resolve_weaver_launch_config("backend")

        self.assertEqual(resolved["provider"], "codex")
        self.assertEqual(resolved["command"], "codex --model gpt-5.4")
        self.assertFalse(resolved["worktree"])

    def test_resolve_weaver_launch_config_applies_model_and_reasoning_defaults(self):
        service = self.server_agent_mod.AgentLaunchService(
            state=_FakeState(
                weaver_provider="codex",
                weaver_model="gpt-5",
                weaver_reasoning_effort="high",
            ),
            connection=None,
            bridge=_FakeBridge(),
            worktree_mgr=None,
            template_mgr=_FakeTemplateManager(),
        )

        resolved = service.resolve_weaver_launch_config("backend")

        self.assertEqual(resolved["agent_type"], "codex")
        self.assertEqual(
            resolved["command"],
            "codex --model gpt-5 -c model_reasoning_effort=high",
        )
        self.assertFalse(resolved["worktree"])

    def test_resolve_weaver_launch_config_applies_terminal_overrides(self):
        service = self.server_agent_mod.AgentLaunchService(
            state=_FakeState(
                agent_directory="/repo",
                weaver_directory="/repo/.loom/weaver",
                weaver_profile="Ops",
                weaver_shell="fish",
                weaver_tab_color="none",
            ),
            connection=None,
            bridge=_FakeBridge(),
            worktree_mgr=None,
            template_mgr=_FakeTemplateManager(),
        )

        resolved = service.resolve_weaver_launch_config("backend")

        self.assertEqual(resolved["directory"], "/repo/.loom/weaver")
        self.assertEqual(resolved["profile"], "Ops")
        self.assertEqual(resolved["shell"], "fish")
        self.assertEqual(resolved["tab_color"], "")
        self.assertFalse(resolved["worktree"])

    def test_resolve_agent_launch_config_detects_default_provider_from_command(self):
        service = self.server_agent_mod.AgentLaunchService(
            state=_FakeState(
                agent_model="gpt-5",
                agent_reasoning_effort="high",
                default_command="codex",
            ),
            connection=None,
            bridge=_FakeBridge(),
            worktree_mgr=None,
            template_mgr=_FakeTemplateManager(),
        )

        resolved = service.resolve_agent_launch_config("backend")

        self.assertEqual(resolved["agent_type"], "codex")
        self.assertEqual(
            resolved["command"],
            "codex --model gpt-5 -c model_reasoning_effort=high",
        )

    def test_resolve_agent_launch_config_does_not_append_flags_to_raw_command_override(self):
        service = self.server_agent_mod.AgentLaunchService(
            state=_FakeState(
                agent_provider="codex",
                agent_model="gpt-5",
                agent_reasoning_effort="high",
            ),
            connection=None,
            bridge=_FakeBridge(),
            worktree_mgr=None,
            template_mgr=_FakeTemplateManager(),
        )

        resolved = service.resolve_agent_launch_config(
            "backend",
            overrides={
                "provider": "codex",
                "command": "codex --full-auto",
                "model": "gpt-5.1",
                "reasoning_effort": "low",
            },
        )

        self.assertEqual(resolved["agent_type"], "codex")
        self.assertEqual(resolved["command"], "codex --full-auto")

    def test_resolve_agent_launch_config_includes_runtime_worktree_name(self):
        service = self.server_agent_mod.AgentLaunchService(
            state=_FakeState(agent_directory="/repo"),
            connection=None,
            bridge=_FakeBridge(current_path="/tmp/ignored"),
            worktree_mgr=None,
            template_mgr=_FakeTemplateManager(),
        )

        resolved = service.resolve_agent_launch_config(
            "backend",
            overrides={
                "provider": "",
                "command": "codex",
                "directory": "/repo",
                "worktree": True,
                "worktree_base_dir": ".loom/worktrees",
                "worktree_base_branch": "main",
                "worktree_name": "Feature API / v2",
            },
        )

        self.assertEqual(resolved["worktree_name"], "Feature API / v2")

    async def test_create_agent_with_config_forwards_dispatch_focus_restore(self):
        state = self.state_mod.MatrixState()
        state.add_group("backend")
        bridge = _CapturingBridge(current_path="/tmp/project")
        service = self.server_agent_mod.AgentLaunchService(
            state=state,
            connection=None,
            bridge=bridge,
            worktree_mgr=None,
            template_mgr=_FakeTemplateManager(),
        )

        cell = await service.create_agent_with_config(
            "backend",
            "Worker",
            {
                "profile": "Default",
                "command": "codex",
                "directory": "/tmp/project",
                "tab_color": "",
                "env_vars": {"LOOM_ENV": "1"},
                "env_file": "/tmp/project/.env",
                "shell": "zsh",
                "system_prompt": "Stay focused",
            },
            restore_focus_to_prev_tab=True,
        )

        self.assertIsNotNone(cell)
        self.assertEqual(len(bridge.create_session_calls), 1)
        self.assertTrue(
            bridge.create_session_calls[0]["kwargs"][
                "restore_focus_to_prev_tab"
            ]
        )

    async def test_create_agent_with_config_stamps_created_by_weaver_id(self):
        state = self.state_mod.MatrixState()
        state.add_group("backend")
        bridge = _CapturingBridge(current_path="/tmp/project")
        service = self.server_agent_mod.AgentLaunchService(
            state=state,
            connection=None,
            bridge=bridge,
            worktree_mgr=None,
            template_mgr=_FakeTemplateManager(),
        )

        cell = await service.create_agent_with_config(
            "backend",
            "Worker",
            {
                "profile": "Default",
                "command": "codex",
                "directory": "/tmp/project",
                "tab_color": "",
                "env_vars": {},
                "env_file": "",
                "shell": "zsh",
                "system_prompt": "",
            },
            created_by_weaver_id="weaver-1",
        )

        self.assertIsNotNone(cell)
        self.assertEqual(cell.created_by_weaver_id, "weaver-1")

    def test_runtime_env_vars_for_engineer_adds_binding(self):
        cell = types.SimpleNamespace(
            id="eng-1",
            cell_type="agent",
            kind="engineer",
        )

        env = self.server_agent_mod.runtime_env_vars_for_cell(
            cell, {"BASE": "1"}
        )

        self.assertEqual(
            env,
            {"BASE": "1", "LOOM_ENGINEER_ID": "eng-1"},
        )

    def test_mcp_entrypoint_for_cell_uses_engineer_entrypoint(self):
        engineer = types.SimpleNamespace(cell_type="agent", kind="engineer")
        worker = types.SimpleNamespace(cell_type="agent", kind="worker")

        self.assertEqual(
            self.server_agent_mod.mcp_entrypoint_for_cell(engineer),
            self.server_agent_mod.ENGINEER_MCP_ENTRYPOINT,
        )
        self.assertEqual(
            self.server_agent_mod.mcp_entrypoint_for_cell(worker),
            self.server_agent_mod.DEFAULT_MCP_ENTRYPOINT,
        )

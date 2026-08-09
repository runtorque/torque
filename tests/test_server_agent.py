import importlib
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub


class _FakeState:
    def __init__(self, *, agent_directory="", default_directory="",
                 engineer_provider="", engineer_boot_command="",
                 engineer_model="", engineer_reasoning_effort="", engineer_fast_mode="inherit",
                 engineer_directory="", engineer_profile="",
                 engineer_shell="", engineer_tab_color="",
                 architect_provider="", architect_boot_command="",
                 architect_model="", architect_reasoning_effort="", architect_fast_mode="inherit",
                 architect_directory="", architect_profile="",
                 architect_shell="", architect_tab_color="",
                 worker_provider="", worker_boot_command="",
                 worker_model="", worker_reasoning_effort="", worker_fast_mode="inherit",
                 agent_provider="", agent_boot_command="",
                 agent_model="", agent_reasoning_effort="", agent_fast_mode="inherit",
                 agent_tab_color="", tab_color="",
                 default_agent_template="",
                 default_command="claude", git_worktree=False,
                 agent_settings=None):
        self._settings = types.SimpleNamespace(
            agent_directory=agent_directory,
            default_directory=default_directory,
            agent_provider=agent_provider,
            agent_boot_command=agent_boot_command,
            agent_model=agent_model,
            agent_reasoning_effort=agent_reasoning_effort,
            agent_fast_mode=agent_fast_mode,
            default_agent_template=default_agent_template,
            worker_provider=worker_provider,
            worker_boot_command=worker_boot_command,
            worker_model=worker_model,
            worker_reasoning_effort=worker_reasoning_effort,
            worker_fast_mode=worker_fast_mode,
            agent_tab_color=agent_tab_color,
            tab_color=tab_color,
            agent_terminal_profile="",
            profile="Default",
            agent_shell="",
            shell="",
            env_vars={},
            agent_env_file="",
            env_file="",
            agent_session_resume=True,
            agent_idle_timeout=5,
            git_worktree=git_worktree,
            worktree_base_dir=".torque/worktrees",
            worktree_base_branch="",
            worktree_auto_checkpoint=False,
            checkpoint_on_progress=False,
            worktree_merge_squash=True,
            worktree_symlinks=[],
            worktree_symlink_gitignored_paths=False,
        )
        self._engineer_settings = types.SimpleNamespace(
            engineer_provider=engineer_provider,
            engineer_boot_command=engineer_boot_command,
            engineer_model=engineer_model,
            engineer_reasoning_effort=engineer_reasoning_effort,
            engineer_fast_mode=engineer_fast_mode,
            engineer_directory=engineer_directory,
            engineer_profile=engineer_profile,
            engineer_shell=engineer_shell,
            engineer_tab_color=engineer_tab_color,
        )
        self._architect_settings = types.SimpleNamespace(
            architect_provider=architect_provider,
            architect_boot_command=architect_boot_command,
            architect_model=architect_model,
            architect_reasoning_effort=architect_reasoning_effort,
            architect_fast_mode=architect_fast_mode,
            architect_directory=architect_directory,
            architect_profile=architect_profile,
            architect_shell=architect_shell,
            architect_tab_color=architect_tab_color,
        )
        self._default_command = default_command
        self._agent_settings = agent_settings or types.SimpleNamespace(
            provider=None, boot_command=None, model=None,
            reasoning_effort=None, fast_mode=None,
        )

    def get_group_settings(self, group):
        return self._settings

    def get_engineer_settings(self, group):
        return self._engineer_settings

    def get_architect_settings(self, group):
        return self._architect_settings

    def get_agent_settings(self, agent_id):
        return self._agent_settings

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
        terminal_adapter = importlib.import_module("torque.terminal_adapter")
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


class _FailingCreateBridge(_FakeBridge):
    async def create_session(self, cell, **kwargs): raise RuntimeError("terminal session timeout")


class _EmptyWorktreeManager:
    async def get_repo_root(self, directory): return "/repo"

    async def create(self, *args, **kwargs): return ""


class _FakeTemplateManager:
    def resolve_agent_config(self, explicit_template, gs, overrides, *,
                             base_dir="", apply_default_template=True):
        data = {
            "provider": getattr(gs, "agent_provider", ""),
            "command": getattr(gs, "agent_boot_command", ""),
            "model": getattr(gs, "agent_model", ""),
            "reasoning_effort": getattr(gs, "agent_reasoning_effort", ""),
            "runner_backend": getattr(gs, "runner_backend", ""),
        }
        template = (
            explicit_template
            or (
                getattr(gs, "default_agent_template", "")
                if apply_default_template else ""
            )
        )
        if template:
            data["template"] = template
        data.update(overrides or {})
        return data


class AgentLaunchServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub()
        self.server_agent_mod = importlib.import_module("torque.server_agent")
        self.server_agent_mod = importlib.reload(self.server_agent_mod)
        self.state_mod = importlib.import_module("torque.state")
        self.state_mod = importlib.reload(self.state_mod)

    def _launch_cfg(self, **extra):
        cfg = {"profile": "Default", "command": "codex", "directory": "/repo", "tab_color": ""}
        cfg.update(extra)
        return cfg

    def _launch_service(self, state, bridge, worktree_mgr=None):
        return self.server_agent_mod.AgentLaunchService(
            state=state, connection=None, bridge=bridge, worktree_mgr=worktree_mgr,
            template_mgr=_FakeTemplateManager(),
        )

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

    def test_fast_mode_resolution_prefers_per_launch_then_kind_then_group(self):
        service = self._launch_service(
            _FakeState(agent_fast_mode="on", worker_fast_mode="off",
                       engineer_fast_mode="off", architect_fast_mode="on"),
            _FakeBridge(),
        )
        self.assertEqual(service.resolve_worker_launch_config("backend")["fast_mode"], "off")
        self.assertEqual(service.resolve_engineer_launch_config("backend")["fast_mode"], "off")
        self.assertEqual(service.resolve_architect_launch_config("backend")["fast_mode"], "on")
        self.assertEqual(
            service.resolve_worker_launch_config(
                "backend", overrides={"fast_mode": "on"}
            )["fast_mode"],
            "on",
        )

    def test_engineer_launch_precedence_includes_per_agent_and_per_launch(self):
        state = _FakeState(
            agent_provider="claude-code",
            engineer_provider="codex",
            agent_settings=types.SimpleNamespace(
                provider="gemini-cli", boot_command=None, model=None,
                reasoning_effort=None, fast_mode=None,
            ),
        )
        service = self._launch_service(state, _FakeBridge())

        per_agent = service.resolve_engineer_launch_config(
            "backend", agent_id="eng-1"
        )
        per_launch = service.resolve_engineer_launch_config(
            "backend", agent_id="eng-1", overrides={"provider": "codex"}
        )

        self.assertEqual(per_agent["provider"], "gemini-cli")
        self.assertEqual(per_launch["provider"], "codex")

    def test_engineer_launch_without_agent_id_preserves_group_resolution(self):
        state = _FakeState(
            agent_provider="claude-code", engineer_provider="codex",
            agent_settings=types.SimpleNamespace(
                provider="gemini-cli", boot_command=None, model=None,
                reasoning_effort=None, fast_mode=None,
            ),
        )
        service = self._launch_service(state, _FakeBridge())

        self.assertEqual(
            service.resolve_engineer_launch_config("backend")["provider"],
            "codex",
        )

    def test_blank_engineer_agent_values_inherit_immediate_kind_layer(self):
        state = _FakeState(
            agent_provider="claude-code", agent_model="generic-model",
            agent_reasoning_effort="low", agent_fast_mode="on",
            engineer_provider="codex", engineer_model="kind-model",
            engineer_reasoning_effort="high", engineer_fast_mode="off",
            agent_settings=types.SimpleNamespace(
                provider="", boot_command="", model="",
                reasoning_effort="", fast_mode="inherit",
            ),
        )
        service = self._launch_service(state, _FakeBridge())

        resolved = service.resolve_engineer_launch_config(
            "backend", agent_id="eng-1"
        )

        self.assertEqual(resolved["provider"], "codex")
        self.assertEqual(
            resolved["command"],
            "codex --model kind-model -c model_reasoning_effort=high",
        )
        self.assertEqual(resolved["fast_mode"], "off")

    def test_blank_engineer_boot_command_inherits_kind_command(self):
        state = _FakeState(
            agent_boot_command="claude", engineer_provider="codex",
            engineer_boot_command="codex --kind",
            agent_settings=types.SimpleNamespace(
                provider=None, boot_command=" ", model=None,
                reasoning_effort=None, fast_mode=None,
            ),
        )
        service = self._launch_service(state, _FakeBridge())

        resolved = service.resolve_engineer_launch_config(
            "backend", agent_id="eng-1"
        )

        self.assertEqual(resolved["command"], "codex --kind")

    def test_blank_architect_agent_values_inherit_immediate_kind_layer(self):
        state = _FakeState(
            agent_provider="claude-code", agent_fast_mode="on",
            architect_provider="codex", architect_fast_mode="off",
            agent_settings=types.SimpleNamespace(
                provider="", boot_command="", model="",
                reasoning_effort="", fast_mode="inherit",
            ),
        )
        service = self._launch_service(state, _FakeBridge())

        resolved = service.resolve_architect_launch_config(
            "backend", agent_id="arch-1"
        )

        self.assertEqual(resolved["provider"], "codex")
        self.assertEqual(resolved["fast_mode"], "off")

    def test_blank_per_launch_values_also_inherit_kind_layer(self):
        state = _FakeState(
            agent_provider="claude-code", engineer_provider="codex",
        )
        service = self._launch_service(state, _FakeBridge())

        resolved = service.resolve_engineer_launch_config(
            "backend", overrides={"provider": "", "fast_mode": "inherit"}
        )

        self.assertEqual(resolved["provider"], "codex")

    def test_resolve_engineer_launch_config_prefers_engineer_specific_overrides(self):
        service = self.server_agent_mod.AgentLaunchService(
            state=_FakeState(
                engineer_provider="codex",
                engineer_boot_command="codex --model gpt-5.4",
                git_worktree=True,
            ),
            connection=None,
            bridge=_FakeBridge(),
            worktree_mgr=None,
            template_mgr=_FakeTemplateManager(),
        )

        resolved = service.resolve_engineer_launch_config("backend")

        self.assertEqual(resolved["provider"], "codex")
        self.assertEqual(resolved["command"], "codex --model gpt-5.4")
        self.assertFalse(resolved["worktree"])

    def test_resolve_engineer_launch_config_applies_model_and_reasoning_defaults(self):
        service = self.server_agent_mod.AgentLaunchService(
            state=_FakeState(
                engineer_provider="codex",
                engineer_model="gpt-5",
                engineer_reasoning_effort="high",
            ),
            connection=None,
            bridge=_FakeBridge(),
            worktree_mgr=None,
            template_mgr=_FakeTemplateManager(),
        )

        resolved = service.resolve_engineer_launch_config("backend")

        self.assertEqual(resolved["agent_type"], "codex")
        self.assertEqual(
            resolved["command"],
            "codex --model gpt-5 -c model_reasoning_effort=high",
        )
        self.assertFalse(resolved["worktree"])

    def test_resolve_engineer_launch_config_inherits_agent_defaults_when_empty(self):
        service = self.server_agent_mod.AgentLaunchService(
            state=_FakeState(
                agent_provider="codex",
                agent_model="gpt-5",
                agent_reasoning_effort="high",
                git_worktree=True,
            ),
            connection=None,
            bridge=_FakeBridge(),
            worktree_mgr=None,
            template_mgr=_FakeTemplateManager(),
        )

        resolved = service.resolve_engineer_launch_config("backend")

        self.assertEqual(resolved["provider"], "codex")
        self.assertEqual(
            resolved["command"],
            "codex --model gpt-5 -c model_reasoning_effort=high",
        )
        self.assertFalse(resolved["worktree"])

    def test_resolve_engineer_launch_config_uses_system_default_when_all_empty(self):
        service = self.server_agent_mod.AgentLaunchService(
            state=_FakeState(default_command="codex", git_worktree=True),
            connection=None,
            bridge=_FakeBridge(),
            worktree_mgr=None,
            template_mgr=_FakeTemplateManager(),
        )

        resolved = service.resolve_engineer_launch_config("backend")

        self.assertEqual(resolved["provider"], "")
        self.assertEqual(resolved["agent_type"], "codex")
        self.assertEqual(resolved["command"], "codex")
        self.assertFalse(resolved["worktree"])

    def test_resolve_engineer_launch_config_applies_terminal_overrides(self):
        service = self.server_agent_mod.AgentLaunchService(
            state=_FakeState(
                agent_directory="/repo",
                engineer_directory="/repo/.torque/engineer",
                engineer_profile="Ops",
                engineer_shell="fish",
                engineer_tab_color="none",
            ),
            connection=None,
            bridge=_FakeBridge(),
            worktree_mgr=None,
            template_mgr=_FakeTemplateManager(),
        )

        resolved = service.resolve_engineer_launch_config("backend")

        self.assertEqual(resolved["directory"], "/repo/.torque/engineer")
        self.assertEqual(resolved["profile"], "Ops")
        self.assertEqual(resolved["shell"], "fish")
        self.assertEqual(resolved["tab_color"], "")
        self.assertFalse(resolved["worktree"])

    def test_resolve_engineer_launch_config_none_tab_color_suppresses_inherited_colors(self):
        service = self.server_agent_mod.AgentLaunchService(
            state=_FakeState(
                engineer_tab_color="none",
                agent_tab_color="#654321",
                tab_color="#123456",
            ),
            connection=None,
            bridge=_FakeBridge(),
            worktree_mgr=None,
            template_mgr=_FakeTemplateManager(),
        )

        resolved = service.resolve_engineer_launch_config("backend")

        self.assertEqual(resolved["tab_color"], "")

    def test_resolve_engineer_launch_config_ignores_default_worker_role(self):
        service = self.server_agent_mod.AgentLaunchService(
            state=_FakeState(default_agent_template="ui-worker"),
            connection=None,
            bridge=_FakeBridge(),
            worktree_mgr=None,
            template_mgr=_FakeTemplateManager(),
        )

        resolved = service.resolve_engineer_launch_config("backend")

        self.assertEqual(resolved["template"], "")

    def test_resolve_engineer_launch_config_keeps_explicit_role(self):
        service = self.server_agent_mod.AgentLaunchService(
            state=_FakeState(default_agent_template="ui-worker"),
            connection=None,
            bridge=_FakeBridge(),
            worktree_mgr=None,
            template_mgr=_FakeTemplateManager(),
        )

        resolved = service.resolve_engineer_launch_config(
            "backend",
            explicit_template="engineer-hire-role",
        )

        self.assertEqual(resolved["template"], "engineer-hire-role")

    def test_resolve_architect_launch_config_prefers_architect_specific_overrides(self):
        service = self.server_agent_mod.AgentLaunchService(
            state=_FakeState(
                architect_provider="codex",
                architect_boot_command="codex --architect",
                git_worktree=True,
            ),
            connection=None,
            bridge=_FakeBridge(),
            worktree_mgr=None,
            template_mgr=_FakeTemplateManager(),
        )

        resolved = service.resolve_architect_launch_config("backend")

        self.assertEqual(resolved["provider"], "codex")
        self.assertEqual(resolved["command"], "codex --architect")
        self.assertFalse(resolved["worktree"])

    def test_resolve_architect_launch_config_applies_model_and_reasoning_defaults(self):
        service = self.server_agent_mod.AgentLaunchService(
            state=_FakeState(
                architect_provider="codex",
                architect_model="gpt-5",
                architect_reasoning_effort="high",
            ),
            connection=None,
            bridge=_FakeBridge(),
            worktree_mgr=None,
            template_mgr=_FakeTemplateManager(),
        )

        resolved = service.resolve_architect_launch_config("backend")

        self.assertEqual(resolved["agent_type"], "codex")
        self.assertEqual(
            resolved["command"],
            "codex --model gpt-5 -c model_reasoning_effort=high",
        )
        self.assertFalse(resolved["worktree"])

    def test_resolve_architect_launch_config_inherits_agent_defaults_when_empty(self):
        service = self.server_agent_mod.AgentLaunchService(
            state=_FakeState(
                agent_provider="codex",
                agent_model="gpt-5",
                agent_reasoning_effort="high",
                git_worktree=True,
            ),
            connection=None,
            bridge=_FakeBridge(),
            worktree_mgr=None,
            template_mgr=_FakeTemplateManager(),
        )

        resolved = service.resolve_architect_launch_config("backend")

        self.assertEqual(resolved["provider"], "codex")
        self.assertEqual(
            resolved["command"],
            "codex --model gpt-5 -c model_reasoning_effort=high",
        )
        self.assertFalse(resolved["worktree"])

    def test_resolve_architect_launch_config_uses_system_default_when_all_empty(self):
        service = self.server_agent_mod.AgentLaunchService(
            state=_FakeState(default_command="codex", git_worktree=True),
            connection=None,
            bridge=_FakeBridge(),
            worktree_mgr=None,
            template_mgr=_FakeTemplateManager(),
        )

        resolved = service.resolve_architect_launch_config("backend")

        self.assertEqual(resolved["provider"], "")
        self.assertEqual(resolved["agent_type"], "codex")
        self.assertEqual(resolved["command"], "codex")
        self.assertFalse(resolved["worktree"])

    def test_resolve_architect_launch_config_applies_terminal_overrides(self):
        service = self.server_agent_mod.AgentLaunchService(
            state=_FakeState(
                agent_directory="/repo",
                architect_directory="/repo/.torque/architect",
                architect_profile="Ops",
                architect_shell="fish",
                architect_tab_color="none",
            ),
            connection=None,
            bridge=_FakeBridge(),
            worktree_mgr=None,
            template_mgr=_FakeTemplateManager(),
        )

        resolved = service.resolve_architect_launch_config("backend")

        self.assertEqual(resolved["directory"], "/repo/.torque/architect")
        self.assertEqual(resolved["profile"], "Ops")
        self.assertEqual(resolved["shell"], "fish")
        self.assertEqual(resolved["tab_color"], "")
        self.assertFalse(resolved["worktree"])

    def test_resolve_architect_launch_config_none_tab_color_suppresses_inherited_colors(self):
        service = self.server_agent_mod.AgentLaunchService(
            state=_FakeState(
                architect_tab_color="none",
                agent_tab_color="#654321",
                tab_color="#123456",
            ),
            connection=None,
            bridge=_FakeBridge(),
            worktree_mgr=None,
            template_mgr=_FakeTemplateManager(),
        )

        resolved = service.resolve_architect_launch_config("backend")

        self.assertEqual(resolved["tab_color"], "")

    def test_resolve_architect_launch_config_ignores_default_worker_role(self):
        service = self.server_agent_mod.AgentLaunchService(
            state=_FakeState(default_agent_template="ui-worker"),
            connection=None,
            bridge=_FakeBridge(),
            worktree_mgr=None,
            template_mgr=_FakeTemplateManager(),
        )

        resolved = service.resolve_architect_launch_config("backend")

        self.assertEqual(resolved["template"], "")

    def test_resolve_worker_launch_config_prefers_worker_specific_overrides(self):
        service = self.server_agent_mod.AgentLaunchService(
            state=_FakeState(
                agent_provider="claude-code",
                worker_provider="codex",
                worker_model="gpt-5.4",
                worker_reasoning_effort="high",
                git_worktree=True,
            ),
            connection=None,
            bridge=_FakeBridge(),
            worktree_mgr=None,
            template_mgr=_FakeTemplateManager(),
        )

        resolved = service.resolve_worker_launch_config("backend")

        self.assertEqual(resolved["provider"], "codex")
        self.assertEqual(resolved["agent_type"], "codex")
        self.assertEqual(
            resolved["command"],
            "codex --model gpt-5.4 -c model_reasoning_effort=high",
        )
        self.assertTrue(resolved["worktree"])

    def test_resolve_worker_launch_config_rejects_provider_slug_model(self):
        """Reject a provider/model category error before launch resolution."""
        service = self._launch_service(
            _FakeState(worker_provider="codex"),
            _FakeBridge(),
        )

        with self.assertRaisesRegex(
            ValueError,
            "Invalid model override `codex`: that is a provider name",
        ):
            service.resolve_worker_launch_config(
                "backend", overrides={"model": "codex"}
            )

    def test_resolve_worker_launch_config_allows_unknown_model_id(self):
        service = self._launch_service(
            _FakeState(worker_provider="codex"),
            _FakeBridge(),
        )

        resolved = service.resolve_worker_launch_config(
            "backend", overrides={"model": "future-account-model"}
        )

        self.assertEqual(
            resolved["command"], "codex --model future-account-model"
        )

    def test_resolve_worker_launch_config_explicit_command_ignores_slug_model(self):
        service = self._launch_service(
            _FakeState(worker_provider="codex"),
            _FakeBridge(),
        )

        resolved = service.resolve_worker_launch_config(
            "backend",
            overrides={
                "command": "codex --full-auto",
                "model": "codex",
            },
        )

        self.assertEqual(resolved["command"], "codex --full-auto")

    async def test_persisted_worker_model_launches_codex_worker_with_terra(self):
        """A persisted Worker default reaches the fresh Codex launch input."""
        from torque.db import TorqueDB
        from torque.adapters.codex import CodexAdapter

        with tempfile.TemporaryDirectory() as tmp:
            db = TorqueDB(Path(tmp) / "torque.db")
            db.init()
            self.addCleanup(db.close)
            state = self.state_mod.MatrixState(db=db)
            state.add_group("backend")
            state.update_group_settings(
                "backend",
                worker_provider="codex",
                worker_model="gpt-5.6-terra",
            )

            # Model resolution must consume the persisted field, not the
            # pre-update in-memory settings object.
            reloaded = self.state_mod.MatrixState(db=db)
            reloaded.load()
            bridge = _CapturingBridge(current_path=tmp)
            service = self.server_agent_mod.AgentLaunchService(
                state=reloaded,
                connection=None,
                bridge=bridge,
                worktree_mgr=None,
                template_mgr=_FakeTemplateManager(),
            )
            launch_cfg = service.resolve_worker_launch_config(
                "backend", base_dir=tmp)
            worker = await service.create_agent_with_config(
                "backend", "Terra Worker", launch_cfg, kind="worker")

        self.assertEqual(
            launch_cfg["command"], "codex --model gpt-5.6-terra",
        )
        self.assertEqual(worker.command, launch_cfg["command"])
        self.assertEqual(bridge.create_session_calls[0]["cell"].command,
                         "codex --model gpt-5.6-terra")
        telemetry = CodexAdapter().parse_event({
            "hook_event_name": "SessionStart",
            "model": "gpt-5.6-terra",
            "session_id": "terra-session",
        }, worker)
        self.assertEqual(telemetry.data["model"], "gpt-5.6-terra")

    async def test_persisted_claude_model_reaches_new_and_resumed_worker_commands(self):
        """Claude's built-in selector values use the existing model path."""
        from torque.db import TorqueDB
        from torque.adapters.claude_code import ClaudeCodeAdapter

        with tempfile.TemporaryDirectory() as tmp:
            db = TorqueDB(Path(tmp) / "torque.db")
            db.init()
            self.addCleanup(db.close)
            state = self.state_mod.MatrixState(db=db)
            state.add_group("backend")
            state.update_group_settings(
                "backend",
                worker_provider="claude-code",
                worker_model="claude-opus-5",
            )

            reloaded = self.state_mod.MatrixState(db=db)
            reloaded.load()
            bridge = _CapturingBridge(current_path=tmp)
            service = self.server_agent_mod.AgentLaunchService(
                state=reloaded,
                connection=None,
                bridge=bridge,
                worktree_mgr=None,
                template_mgr=_FakeTemplateManager(),
            )
            launch_cfg = service.resolve_worker_launch_config(
                "backend", base_dir=tmp)
            worker = await service.create_agent_with_config(
                "backend", "Claude Worker", launch_cfg, kind="worker")

        self.assertEqual(launch_cfg["command"], "claude --model claude-opus-5")
        self.assertEqual(worker.command, launch_cfg["command"])
        self.assertEqual(bridge.create_session_calls[0]["cell"].command,
                         "claude --model claude-opus-5")
        self.assertEqual(
            ClaudeCodeAdapter().get_resume_command(worker.command, "session-123"),
            "claude --model claude-opus-5 --resume session-123",
        )

    def test_resolve_worker_launch_config_explicit_provider_command_win(self):
        service = self.server_agent_mod.AgentLaunchService(
            state=_FakeState(
                agent_provider="gemini-cli",
                worker_provider="codex",
                worker_boot_command="codex --worker-default",
                worker_model="gpt-worker",
                worker_reasoning_effort="high",
            ),
            connection=None,
            bridge=_FakeBridge(),
            worktree_mgr=None,
            template_mgr=_FakeTemplateManager(),
        )

        resolved = service.resolve_worker_launch_config(
            "backend",
            overrides={
                "provider": "claude-code",
                "command": "claude --explicit",
            },
        )

        self.assertEqual(resolved["provider"], "claude-code")
        self.assertEqual(resolved["agent_type"], "claude-code")
        self.assertEqual(resolved["command"], "claude --explicit")

    def test_resolve_worker_launch_config_rejects_provider_command_mismatch(self):
        service = self.server_agent_mod.AgentLaunchService(
            state=_FakeState(
                agent_provider="codex",
            ),
            connection=None,
            bridge=_FakeBridge(),
            worktree_mgr=None,
            template_mgr=_FakeTemplateManager(),
        )

        with self.assertRaisesRegex(ValueError, "Provider/command mismatch"):
            service.resolve_worker_launch_config(
                "backend",
                overrides={
                    "command": "claude --dangerously-skip-permissions",
                },
            )

    def test_resolve_worker_launch_config_explicit_model_reasoning_win(self):
        service = self.server_agent_mod.AgentLaunchService(
            state=_FakeState(
                agent_provider="claude-code",
                worker_provider="codex",
                worker_model="gpt-worker",
                worker_reasoning_effort="high",
            ),
            connection=None,
            bridge=_FakeBridge(),
            worktree_mgr=None,
            template_mgr=_FakeTemplateManager(),
        )

        resolved = service.resolve_worker_launch_config(
            "backend",
            overrides={
                "model": "gpt-explicit",
                "reasoning_effort": "low",
            },
        )

        self.assertEqual(resolved["provider"], "codex")
        self.assertEqual(
            resolved["command"],
            "codex --model gpt-explicit -c model_reasoning_effort=low",
        )

    def test_resolve_worker_launch_config_empty_explicit_overrides_inherit_worker_defaults(self):
        service = self.server_agent_mod.AgentLaunchService(
            state=_FakeState(
                agent_provider="claude-code",
                worker_provider="codex",
                worker_model="gpt-worker",
                worker_reasoning_effort="high",
            ),
            connection=None,
            bridge=_FakeBridge(),
            worktree_mgr=None,
            template_mgr=_FakeTemplateManager(),
        )

        resolved = service.resolve_worker_launch_config(
            "backend",
            overrides={
                "provider": "",
                "command": "",
                "model": "",
                "reasoning_effort": "",
            },
        )

        self.assertEqual(resolved["provider"], "codex")
        self.assertEqual(
            resolved["command"],
            "codex --model gpt-worker -c model_reasoning_effort=high",
        )

    def test_resolve_worker_launch_config_inherits_agent_defaults_when_empty(self):
        service = self.server_agent_mod.AgentLaunchService(
            state=_FakeState(
                agent_provider="codex",
                agent_model="gpt-5",
                agent_reasoning_effort="medium",
            ),
            connection=None,
            bridge=_FakeBridge(),
            worktree_mgr=None,
            template_mgr=_FakeTemplateManager(),
        )

        resolved = service.resolve_worker_launch_config("backend")

        self.assertEqual(resolved["provider"], "codex")
        self.assertEqual(
            resolved["command"],
            "codex --model gpt-5 -c model_reasoning_effort=medium",
        )

    def test_resolve_worker_launch_config_uses_system_default_when_all_empty(self):
        service = self.server_agent_mod.AgentLaunchService(
            state=_FakeState(default_command="codex"),
            connection=None,
            bridge=_FakeBridge(),
            worktree_mgr=None,
            template_mgr=_FakeTemplateManager(),
        )

        resolved = service.resolve_worker_launch_config("backend")

        self.assertEqual(resolved["provider"], "")
        self.assertEqual(resolved["agent_type"], "codex")
        self.assertEqual(resolved["command"], "codex")

    def test_resolve_worker_launch_config_applies_default_worker_role(self):
        service = self.server_agent_mod.AgentLaunchService(
            state=_FakeState(default_agent_template="ui-worker"),
            connection=None,
            bridge=_FakeBridge(),
            worktree_mgr=None,
            template_mgr=_FakeTemplateManager(),
        )

        resolved = service.resolve_worker_launch_config("backend")

        self.assertEqual(resolved["template"], "ui-worker")

    def test_generic_launch_config_ignores_worker_specific_overrides(self):
        service = self.server_agent_mod.AgentLaunchService(
            state=_FakeState(
                worker_provider="codex",
                worker_model="gpt-5",
                worker_reasoning_effort="high",
                default_command="claude",
            ),
            connection=None,
            bridge=_FakeBridge(),
            worktree_mgr=None,
            template_mgr=_FakeTemplateManager(),
        )

        resolved = service.resolve_agent_launch_config("backend")

        self.assertEqual(resolved["provider"], "")
        self.assertEqual(resolved["command"], "claude")
        self.assertNotIn("gpt-5", resolved["command"])

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
                "worktree_base_dir": ".torque/worktrees",
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
                "env_vars": {"TORQUE_ENV": "1"},
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

    async def test_resolve_agent_launch_config_defaults_runner_backend_to_pty(self):
        service = self._launch_service(_FakeState(agent_provider="codex"), _FakeBridge())

        resolved = service.resolve_agent_launch_config("backend")

        self.assertEqual(resolved["agent_type"], "codex")
        self.assertEqual(resolved["runner_backend"], "pty")

    async def test_resolve_agent_launch_config_rejects_removed_sdk_backend(self):
        service = self._launch_service(_FakeState(agent_provider="codex"), _FakeBridge())

        with self.assertRaisesRegex(ValueError, "runner_backend must be one of: pty"):
            service.resolve_agent_launch_config(
                "backend",
                overrides={"runner_backend": "codex-sdk-readonly"},
            )

    async def test_create_agent_with_config_rejects_removed_sdk_backend_before_side_effects(self):
        state = self.state_mod.MatrixState()
        state.add_group("backend")
        bridge = _CapturingBridge(current_path="/tmp/project")
        worktree_mgr = mock.AsyncMock()
        service = self._launch_service(state, bridge, worktree_mgr=worktree_mgr)

        with self.assertRaisesRegex(ValueError, "runner_backend must be one of: pty"):
            await service.create_agent_with_config(
                "backend",
                "Removed SDK",
                {
                    "profile": "Default",
                    "command": "codex",
                    "directory": "/tmp/project",
                    "tab_color": "",
                    "agent_type": "codex",
                    "runner_backend": "codex-sdk-readonly",
                    "worktree": True,
                    "worktree_auto_checkpoint": True,
                    "checkpoint_on_progress": True,
                },
                persistent_prompt_text="SYSTEM",
            )

        self.assertEqual(state.agents, {})
        self.assertEqual(bridge.create_session_calls, [])
        worktree_mgr.get_repo_root.assert_not_called()
        worktree_mgr.create.assert_not_called()

    async def test_create_agent_with_config_appends_explicit_agent_class_prompt(self):
        state = self.state_mod.MatrixState()
        state.add_group("backend")
        bridge = _CapturingBridge(current_path="/tmp/project")
        service = self._launch_service(state, bridge)
        captured_prompts = []

        def capture_persistent_prompt(cell, launch_cfg, prompt_text):
            del cell, launch_cfg
            captured_prompts.append(prompt_text)

        service.apply_persistent_prompt = capture_persistent_prompt

        cell = await service.create_agent_with_config(
            "backend",
            "Product Manager",
            {
                "profile": "Default",
                "command": "codex",
                "directory": "/tmp/project",
                "tab_color": "",
                "env_vars": {},
                "env_file": "",
                "shell": "zsh",
                "system_prompt": "",
                "agent_type": "codex",
                "agent_class_id": "product-manager",
            },
            persistent_prompt_text="BASE PROMPT",
            kind="architect",
        )

        self.assertIsNotNone(cell)
        self.assertEqual(cell.effective_agent_class_id, "product-manager")
        self.assertEqual(len(captured_prompts), 1)
        self.assertIn("BASE PROMPT", captured_prompts[0])
        self.assertIn("## Agent Class", captured_prompts[0])
        self.assertIn("Effective Torque MCP authority", captured_prompts[0])

    async def test_create_agent_with_config_default_class_does_not_append_prompt(self):
        state = self.state_mod.MatrixState()
        state.add_group("backend")
        bridge = _CapturingBridge(current_path="/tmp/project")
        service = self._launch_service(state, bridge)
        captured_prompts = []
        service.apply_persistent_prompt = (
            lambda _cell, _launch_cfg, prompt_text:
            captured_prompts.append(prompt_text)
        )

        cell = await service.create_agent_with_config(
            "backend",
            "Architect",
            {
                "profile": "Default",
                "command": "codex",
                "directory": "/tmp/project",
                "tab_color": "",
                "env_vars": {},
                "env_file": "",
                "shell": "zsh",
                "system_prompt": "",
                "agent_type": "codex",
            },
            persistent_prompt_text="BASE PROMPT",
            kind="architect",
        )

        self.assertIsNotNone(cell)
        self.assertEqual(cell.effective_agent_class_id, "default-architect")
        self.assertEqual(captured_prompts, ["BASE PROMPT"])

    async def test_create_agent_with_config_stamps_created_by_engineer_id(self):
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
            created_by_engineer_id="engineer-1",
        )

        self.assertIsNotNone(cell)
        self.assertEqual(cell.created_by_engineer_id, "engineer-1")

    async def test_create_agent_with_config_launches_in_inherited_worktree(self):
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
        source = self.state_mod.AgentCell(
            id="impl-1",
            name="Implementer",
            group="backend",
            cell_type="agent",
            worktree_path="/repo/.torque/worktrees/impl",
            worktree_branch="torque/impl",
            worktree_repo_root="/repo",
            worktree_base_branch="main",
            worktree_changed_files=["README.md"],
        )

        cell = await service.create_agent_with_config(
            "backend",
            "Reviewer",
            {
                "profile": "Default",
                "command": "codex",
                "directory": "/repo",
                "tab_color": "",
                "env_vars": {},
                "env_file": "",
                "shell": "zsh",
                "system_prompt": "",
                "worktree": True,
            },
            inherited_worktree_from=source,
        )

        self.assertIsNotNone(cell)
        self.assertEqual(cell.worktree_path, source.worktree_path)
        self.assertEqual(cell.worktree_branch, source.worktree_branch)
        self.assertEqual(cell.directory, source.worktree_path)
        self.assertEqual(cell.worktree_changed_files, ["README.md"])
        self.assertEqual(
            bridge.create_session_calls[0]["cell"].directory,
            source.worktree_path,
        )

    async def test_create_agent_with_config_logs_add_agent_none(self):
        state = types.SimpleNamespace(add_agent=lambda **kw: None)
        service = self._launch_service(state, _CapturingBridge())

        with self.assertLogs("torque", level="WARNING") as logs:
            await service.create_agent_with_config(
                "missing-group", "Worker", self._launch_cfg(),
            )

        self.assertIn("add_agent returned None for group=missing-group name=Worker",
                      "\n".join(logs.output))

    async def test_create_agent_with_config_logs_empty_worktree_create(self):
        state = self.state_mod.MatrixState()
        state.add_group("backend")
        bridge = _CapturingBridge(current_path="/repo")
        service = self._launch_service(
            state, bridge, worktree_mgr=_EmptyWorktreeManager())

        with self.assertLogs("torque", level="WARNING") as logs:
            await service.create_agent_with_config(
                "backend", "Worker", self._launch_cfg(worktree=True),
            )

        self.assertIn("worktree create failed silently for cell=worker repo=/repo",
                      "\n".join(logs.output))

    async def test_create_agent_with_config_logs_and_rolls_back_session_failure(self):
        events_mod = importlib.import_module("torque.events")
        state = self.state_mod.MatrixState()
        state.add_group("backend")
        state.panel_log = events_mod.PanelEventLog(max_size=10)
        service = self._launch_service(state, _FailingCreateBridge())

        with self.assertLogs("torque", level="WARNING") as logs:
            with self.assertRaises(RuntimeError):
                await service.create_agent_with_config(
                    "backend", "Worker", self._launch_cfg(),
                )

        self.assertEqual(state.agents, {})
        self.assertEqual(state.panel_log.get_recent(1), [])
        self.assertIn("Agent create failed for cell=worker group=backend",
                      "\n".join(logs.output))

        cell = types.SimpleNamespace(slug="worker", name="Worker", id="worker-1")
        with self.assertLogs("torque", level="WARNING") as logs:
            prompts = self.server_agent_mod._new_agent_prompt_sequence(
                {"initial_prompt": ""},
                cell=cell,
                task_id="TORQUE:391",
            )

        self.assertEqual(prompts, [])
        self.assertIn("dispatch_task: empty prompt sequence for cell=worker "
                      "task=TORQUE:391 (startup=0, initial=0, final=0)",
                      "\n".join(logs.output))

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
            {"BASE": "1", "TORQUE_ENGINEER_ID": "eng-1"},
        )

    def test_runtime_env_vars_for_architect_adds_binding(self):
        cell = types.SimpleNamespace(
            id="arch-1",
            cell_type="agent",
            kind="architect",
        )

        env = self.server_agent_mod.runtime_env_vars_for_cell(
            cell, {"BASE": "1"}
        )

        self.assertEqual(
            env,
            {"BASE": "1", "TORQUE_ARCHITECT_ID": "arch-1"},
        )

    def test_mcp_env_vars_for_architect_exclude_shared_config_identity(self):
        cell = types.SimpleNamespace(
            id="arch-1",
            cell_type="agent",
            kind="architect",
        )

        with mock.patch.object(
            self.server_agent_mod.torque_config, "WS_PORT", 18934
        ), mock.patch.object(
            self.server_agent_mod.torque_config, "DATA_DIR", Path("/tmp/torque-data")
        ), mock.patch.dict(os.environ, {"TORQUE_PROFILE": "desktop"}, clear=False):
            env = self.server_agent_mod.mcp_env_vars_for_cell(cell)

        self.assertEqual(
            env,
            {
                "TORQUE_PORT": "18934",
                "TORQUE_DATA_DIR": "/tmp/torque-data",
                "TORQUE_PROFILE": "desktop",
            },
        )

    def test_mcp_env_vars_for_worker_include_default_http_bindings(self):
        cell = types.SimpleNamespace(
            id="worker-1",
            cell_type="agent",
            kind="worker",
        )

        with mock.patch.object(
            self.server_agent_mod.torque_config, "WS_PORT", 18935
        ), mock.patch.object(
            self.server_agent_mod.torque_config, "DATA_DIR", Path("/tmp/torque-worker")
        ), mock.patch.dict(os.environ, {"TORQUE_PROFILE": ""}, clear=False):
            env = self.server_agent_mod.mcp_env_vars_for_cell(cell)

        self.assertNotIn("TORQUE_CELL_ID", env)
        self.assertEqual(env["TORQUE_PORT"], "18935")
        self.assertEqual(env["TORQUE_DATA_DIR"], "/tmp/torque-worker")
        self.assertNotIn("TORQUE_ARCHITECT_ID", env)
        self.assertNotIn("TORQUE_ENGINEER_ID", env)

    def test_mcp_entrypoint_for_cell_uses_kind_specific_entrypoint(self):
        architect = types.SimpleNamespace(cell_type="agent", kind="architect")
        engineer = types.SimpleNamespace(cell_type="agent", kind="engineer")
        worker = types.SimpleNamespace(cell_type="agent", kind="worker")

        self.assertEqual(
            self.server_agent_mod.mcp_entrypoint_for_cell(architect),
            self.server_agent_mod.ARCHITECT_MCP_ENTRYPOINT,
        )
        self.assertEqual(
            self.server_agent_mod.mcp_entrypoint_for_cell(engineer),
            self.server_agent_mod.ENGINEER_MCP_ENTRYPOINT,
        )
        self.assertEqual(
            self.server_agent_mod.mcp_entrypoint_for_cell(worker),
            self.server_agent_mod.DEFAULT_MCP_ENTRYPOINT,
        )

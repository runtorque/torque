import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub

install_aiohttp_stub()

from torque.db import TorqueDB
from torque.state import AgentCell, EngineerSettings, GroupSettings, MatrixState
from torque.commands.settings import _handle_settings_mutation_command


class PerAgentSettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = TorqueDB(Path(self.tmp.name) / "torque.db")
        self.db.init()
        self.addCleanup(self.db.close)
        self.state = MatrixState(self.db)
        self.state.groups["g"] = ["eng-1"]
        self.state.group_settings["g"] = GroupSettings()
        self.state.engineer_settings["g"] = EngineerSettings(
            group="g", engineer_provider="codex", default_worker_concurrency=2,
            push_interval=60,
        )
        self.cell = AgentCell(
            id="eng-1", name="Engineer", group="g", kind="engineer",
            persistent=True,
        )
        self.state.agents[self.cell.id] = self.cell
        self.db.save_groups(self.state.groups)
        self.db.save_group_settings("g", self.state.group_settings["g"])
        self.db.save_agent(self.cell)
        self.db.save_engineer_settings("g", vars(self.state.engineer_settings["g"]))

    def test_unset_fields_follow_later_group_changes(self):
        self.state.update_agent_settings("eng-1", provider="gemini-cli")

        before = self.state.resolve_agent_settings("eng-1")
        self.state.update_engineer_settings("g", default_worker_concurrency=7)
        after = self.state.resolve_agent_settings("eng-1")

        self.assertEqual(before["provider"], {
            "value": "gemini-cli",
            "origin": "per-agent",
            "inherited": {"value": "codex", "origin": "group"},
        })
        self.assertEqual(after["default_worker_concurrency"], {"value": 7, "origin": "group"})

    def test_digest_row_tracks_overrides_per_field_not_per_row(self):
        self.state.update_agent_digest_settings("eng-1", push_interval=15)
        self.state.update_engineer_settings("g", max_interval=900)

        resolved = self.state.resolve_agent_settings("eng-1")

        self.assertEqual(resolved["push_interval"], {
            "value": 15,
            "origin": "per-agent",
            "inherited": {"value": 60, "origin": "group"},
        })
        self.assertEqual(resolved["max_interval"], {"value": 900, "origin": "group"})

    def test_nullable_overrides_survive_restart_without_ephemeral_activity(self):
        self.state.update_agent_settings(
            "eng-1", provider="gemini-cli", default_worker_concurrency=4,
            restrict_to_created_agents=True,
        )
        self.cell.activity = "thinking"

        restarted = MatrixState(self.db)
        restarted.load()

        stored = restarted.get_agent_settings("eng-1")
        self.assertEqual(stored.provider, "gemini-cli")
        self.assertEqual(stored.default_worker_concurrency, 4)
        self.assertTrue(stored.restrict_to_created_agents)
        self.assertEqual(restarted.agents["eng-1"].activity, "")

    def test_clear_override_restores_inheritance_and_delete_removes_row(self):
        self.state.update_agent_settings("eng-1", provider="gemini-cli")
        self.state.update_agent_settings("eng-1", provider=None)
        self.assertEqual(
            self.state.resolve_agent_settings("eng-1")["provider"],
            {"value": "codex", "origin": "group"},
        )

        self.state.delete_agent_settings("eng-1")
        self.assertIsNone(self.db.load_agent_settings("eng-1"))

    def test_blank_and_inherit_inputs_clear_engineer_overrides_by_field(self):
        self.state.engineer_settings["g"].engineer_boot_command = "codex"
        self.state.engineer_settings["g"].engineer_model = "kind-model"
        self.state.engineer_settings["g"].engineer_reasoning_effort = "high"
        self.state.engineer_settings["g"].engineer_fast_mode = "off"
        self.state.engineer_settings["g"].wave_size_preference = "large"
        self.state.update_agent_settings(
            "eng-1", provider="gemini-cli", boot_command="gemini",
            model="agent-model", reasoning_effort="low", fast_mode="on",
            wave_size_preference="small",
        )

        self.state.update_agent_settings(
            "eng-1", provider=" ", boot_command="", model="  ",
            reasoning_effort="", fast_mode="inherit",
            wave_size_preference="",
        )

        stored = self.state.get_agent_settings("eng-1")
        for name in ("provider", "boot_command", "model", "reasoning_effort",
                     "fast_mode", "wave_size_preference"):
            self.assertIsNone(getattr(stored, name), name)
        resolved = self.state.resolve_agent_settings("eng-1")
        self.assertEqual(resolved["provider"], {"value": "codex", "origin": "group"})
        self.assertEqual(resolved["boot_command"], {"value": "codex", "origin": "group"})
        self.assertEqual(resolved["model"], {"value": "kind-model", "origin": "group"})
        self.assertEqual(resolved["reasoning_effort"], {"value": "high", "origin": "group"})
        self.assertEqual(resolved["fast_mode"], {"value": "off", "origin": "group"})
        self.assertEqual(resolved["wave_size_preference"], {"value": "large", "origin": "group"})

    def test_blank_inputs_clear_architect_overrides_symmetrically(self):
        architect = AgentCell(
            id="arch-1", name="Architect", group="g", kind="architect",
            persistent=True,
        )
        self.state.agents[architect.id] = architect
        group = self.state.group_settings["g"]
        group.architect_provider = "codex"
        group.architect_fast_mode = "off"
        group.architect_autonomy_mode = "ask_always"
        self.state.update_agent_settings(
            architect.id, provider="gemini-cli", fast_mode="on",
            autonomy_mode="dispatch_freely",
        )

        self.state.update_agent_settings(
            architect.id, provider="", fast_mode="inherit", autonomy_mode="",
        )

        stored = self.state.get_agent_settings(architect.id)
        self.assertIsNone(stored.provider)
        self.assertIsNone(stored.fast_mode)
        self.assertIsNone(stored.autonomy_mode)
        resolved = self.state.resolve_agent_settings(architect.id)
        self.assertEqual(resolved["provider"], {"value": "codex", "origin": "group"})
        self.assertEqual(resolved["fast_mode"], {"value": "off", "origin": "group"})
        self.assertEqual(resolved["autonomy_mode"], {"value": "ask_always", "origin": "group"})

    def test_explicit_false_boolean_remains_an_active_override(self):
        self.state.engineer_settings["g"].restrict_to_created_agents = True

        self.state.update_agent_settings(
            "eng-1", restrict_to_created_agents=False,
        )

        self.assertEqual(
            self.state.resolve_agent_settings("eng-1")["restrict_to_created_agents"],
            {
                "value": False,
                "origin": "per-agent",
                "inherited": {"value": True, "origin": "group"},
            },
        )

    def test_agent_settings_keys_project_through_both_snapshot_sites(self):
        self.state.update_agent_settings("eng-1", provider="gemini-cli")
        self.state.update_agent_digest_settings("eng-1", push_interval=15)
        expected_settings = self.state.agent_settings_snapshot()
        self.assertEqual(
            set(expected_settings),
            {"agent_settings", "resolved_agent_settings"},
        )

        full = self.state.to_dict()
        compact = self.state.to_dict_compact()

        for snapshot in (full, compact):
            self.assertEqual(snapshot["agent_settings"], expected_settings["agent_settings"])
            self.assertEqual(
                snapshot["resolved_agent_settings"],
                expected_settings["resolved_agent_settings"],
            )
            self.assertEqual(
                snapshot["agent_digest_settings"]["eng-1"],
                asdict(self.state.agent_digest_settings["eng-1"]),
            )
            self.assertIn("engineer_settings", snapshot)
            self.assertIn("architect_settings", snapshot)

    def test_digest_settings_command_delegates_sparse_clear_and_returns_origins(self):
        response = _handle_settings_mutation_command({
            "cmd": "update_agent_digest_settings",
            "agent_id": "eng-1",
            "settings": {"push_interval": 15},
        }, self.state)
        self.assertEqual(response["type"], "agent_settings")
        self.assertEqual(
            response["resolved"]["push_interval"],
            {
                "value": 15,
                "origin": "per-agent",
                "inherited": {"value": 60, "origin": "group"},
            },
        )

        response = _handle_settings_mutation_command({
            "cmd": "update_agent_digest_settings",
            "agent_id": "eng-1",
            "settings": {"push_interval": None},
        }, self.state)
        self.assertEqual(
            response["resolved"]["push_interval"],
            {"value": 60, "origin": "group"},
        )


if __name__ == "__main__":
    unittest.main()

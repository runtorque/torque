import tempfile
import unittest
from pathlib import Path

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub

install_aiohttp_stub()

from torque.db import TorqueDB
from torque.state import AgentCell, EngineerSettings, GroupSettings, MatrixState


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

        self.assertEqual(before["provider"], {"value": "gemini-cli", "origin": "per-agent"})
        self.assertEqual(after["default_worker_concurrency"], {"value": 7, "origin": "group"})

    def test_digest_row_tracks_overrides_per_field_not_per_row(self):
        self.state.update_agent_digest_settings("eng-1", push_interval=15)
        self.state.update_engineer_settings("g", max_interval=900)

        resolved = self.state.resolve_agent_settings("eng-1")

        self.assertEqual(resolved["push_interval"], {"value": 15, "origin": "per-agent"})
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


if __name__ == "__main__":
    unittest.main()

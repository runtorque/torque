import importlib
import unittest

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub


class EngineerDispatchShapeHintTests(unittest.TestCase):
    def setUp(self):
        install_aiohttp_stub()
        self.state_mod = importlib.import_module("torque.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.hints_mod = importlib.import_module("torque.engineer_hints")
        self.hints_mod = importlib.reload(self.hints_mod)

    def _make_state(self):
        state = self.state_mod.MatrixState()
        state.board_lanes = ["Backlog", "To Do", "In Progress", "Done", "Archived"]
        group = "g"
        engineer = self.state_mod.AgentCell(
            id="engineer-1",
            name="Engineer",
            group=group,
            cell_type="agent",
            kind="engineer",
        )
        state.agents[engineer.id] = engineer
        state.groups[group] = [engineer.id]
        state.group_settings[group] = self.state_mod.GroupSettings(
            engineer_agent_id=engineer.id,
        )
        return state, group, engineer

    def _add_ready_tasks(self, state, group, engineer, count=2):
        tasks = []
        for idx in range(count):
            tasks.append(
                state.board_add_task(
                    f"Ready wave {idx}",
                    group,
                    lane="To Do",
                    assigned_engineer_id=engineer.id,
                )
            )
        return tasks

    def test_serial_heavy_dispatch_shape_distribution_yields_low_priority_hint(self):
        state, group, engineer = self._make_state()
        self._add_ready_tasks(state, group, engineer, count=2)
        for idx in range(18):
            state.record_engineer_dispatch_shape(
                engineer.id,
                group=group,
                source_tool="engineer_task_dispatch",
                shape="serial",
                task_ids=[f"serial-{idx}"],
                hintable=True,
            )
        for idx in range(2):
            state.record_engineer_dispatch_shape(
                engineer.id,
                group=group,
                source_tool="engineer_batch_dispatch",
                shape="batch",
                task_ids=[f"batch-{idx}-a", f"batch-{idx}-b"],
                task_count=2,
            )

        hints = self.hints_mod.compute_engineer_hints(
            state,
            group,
            engineer_id=engineer.id,
        )

        dispatch_hints = [
            hint for hint in hints
            if hint["kind"] == "dispatch_shape_distribution"
        ]
        self.assertEqual(len(dispatch_hints), 1)
        hint = dispatch_hints[0]
        self.assertEqual(hint["priority"], 25)
        self.assertEqual(
            hint["fingerprint"],
            "dispatch_shape:serial-heavy:engineer-1",
        )
        self.assertEqual(
            hint["message"],
            "Dispatch shape: last 20 direct dispatches were "
            "18 serial / 2 batch / 0 warm cluster. For the next "
            "independent wave, consider engineer_batch_dispatch.",
        )

    def test_dispatch_shape_hint_uses_hintable_serial_subset(self):
        state, group, engineer = self._make_state()
        self._add_ready_tasks(state, group, engineer, count=2)
        for idx in range(18):
            state.record_engineer_dispatch_shape(
                engineer.id,
                group=group,
                source_tool="engineer_task_dispatch",
                shape="serial",
                task_ids=[f"override-{idx}"],
                hintable=False,
                metadata={"has_launch_overrides": True},
            )
        for idx in range(2):
            state.record_engineer_dispatch_shape(
                engineer.id,
                group=group,
                source_tool="engineer_task_dispatch",
                shape="warm_cluster",
                task_ids=[f"warm-{idx}"],
                hintable=False,
                metadata={"existing_agent": True},
            )

        hints = self.hints_mod.compute_engineer_hints(
            state,
            group,
            engineer_id=engineer.id,
        )

        self.assertNotIn(
            "dispatch_shape_distribution",
            {hint["kind"] for hint in hints},
        )

    def test_dispatch_shape_hint_requires_two_ready_visible_tasks(self):
        state, group, engineer = self._make_state()
        self._add_ready_tasks(state, group, engineer, count=1)
        for idx in range(10):
            state.record_engineer_dispatch_shape(
                engineer.id,
                group=group,
                source_tool="engineer_task_dispatch",
                shape="serial",
                task_ids=[f"serial-{idx}"],
                hintable=True,
            )

        hints = self.hints_mod.compute_engineer_hints(
            state,
            group,
            engineer_id=engineer.id,
        )

        self.assertNotIn(
            "dispatch_shape_distribution",
            {hint["kind"] for hint in hints},
        )


if __name__ == "__main__":
    unittest.main()

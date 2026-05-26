import importlib
import types
import unittest

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub


class ServerDeployStateCommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub()
        self.state_mod = importlib.import_module("torque.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.server_mod = importlib.import_module("torque.server")
        self.server_mod = importlib.reload(self.server_mod)

    @staticmethod
    def _make_cell(value):
        return (lambda x: lambda: x)(value).__closure__[0]

    def _extract_handle_command(self, state):
        main_code = self.server_mod.main.__code__
        handle_code = next(
            const
            for const in main_code.co_consts
            if isinstance(const, type(main_code))
            and const.co_name == "handle_command"
        )
        closure_values = {name: None for name in handle_code.co_freevars}
        closure_values.update({
            "handle_command": None,
            "state": state,
        })
        closure = tuple(
            self._make_cell(closure_values[name])
            for name in handle_code.co_freevars
        )
        return types.FunctionType(
            handle_code,
            self.server_mod.__dict__,
            "handle_command",
            None,
            closure,
        )

    async def test_get_deploy_state_returns_status_bar_shape_and_reuses_architect_payload(self):
        state = self.state_mod.MatrixState()
        seen = []

        def fake_architect_deploy_state_payload(arg_state, group):
            seen.append((arg_state, group))
            return {
                "boot_timestamp": 100,
                "boot_head_commit": "bootsha",
                "current_head_commit": "headsha",
                "pending_deploy": {
                    "count": 2,
                    "torque_task_ids": ["TORQUE:1", "TORQUE:2"],
                },
                "daemon_uptime_seconds": 42,
            }

        original = self.server_mod.architect_deploy_state_payload
        self.server_mod.architect_deploy_state_payload = fake_architect_deploy_state_payload
        self.addCleanup(lambda: setattr(self.server_mod, "architect_deploy_state_payload", original))
        handle_command = self._extract_handle_command(state)

        result = await handle_command({"cmd": "get_deploy_state", "group": "Torque"})

        self.assertEqual(seen, [(state, "Torque")])
        self.assertEqual(result["type"], "deploy_state")
        self.assertEqual(result["group"], "Torque")
        self.assertEqual(result["pending_deploy"], {
            "count": 2,
            "torque_task_ids": ["TORQUE:1", "TORQUE:2"],
        })
        self.assertEqual(result["daemon_uptime_seconds"], 42)
        self.assertEqual(result["error"], "")

    async def test_get_deploy_state_normalizes_missing_pending_shape(self):
        state = self.state_mod.MatrixState()

        def fake_architect_deploy_state_payload(_state, _group):
            return {"daemon_uptime_seconds": 0}

        original = self.server_mod.architect_deploy_state_payload
        self.server_mod.architect_deploy_state_payload = fake_architect_deploy_state_payload
        self.addCleanup(lambda: setattr(self.server_mod, "architect_deploy_state_payload", original))
        handle_command = self._extract_handle_command(state)

        result = await handle_command({"cmd": "get_deploy_state"})

        self.assertEqual(result["type"], "deploy_state")
        self.assertEqual(result["group"], "")
        self.assertEqual(result["pending_deploy"], {"count": 0, "torque_task_ids": []})
        self.assertEqual(result["error"], "")


if __name__ == "__main__":
    unittest.main()

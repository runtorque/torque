import importlib
import types
import unittest

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub


class ServerExternalLinkTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_external_link_task_accepts_board_sync_payload_for_linked_issue(self):
        state = self.state_mod.MatrixState()
        state.add_group("g")
        state.board_add_task(
            "Sync-created link",
            "g",
            id="task-1",
            board_sync={
                "version": 1,
                "provider": "github",
                "enabled": True,
                "last_synced_hash": "old-hash",
                "github": {
                    "issue_repo": "acme/widgets",
                    "issue_number": 456,
                    "issue_url": "https://github.com/acme/widgets/issues/456",
                },
            },
        )
        handle_command = self._extract_handle_command(state)

        result = await handle_command({
            "cmd": "external_link_task",
            "id": "task-1",
            "ref": "https://github.com/acme/widgets/issues/789",
            "board_sync": {
                "version": 1,
                "provider": "github",
                "enabled": True,
            },
        })

        task = state.board_tasks["task-1"]
        self.assertEqual(result["type"], "external_linked")
        self.assertEqual(task.provider, "github")
        self.assertEqual(task.external_id, "acme/widgets#789")
        self.assertEqual(
            task.external_url,
            "https://github.com/acme/widgets/issues/789",
        )
        self.assertEqual(
            task.board_sync,
            {
                "version": 1,
                "provider": "github",
                "enabled": True,
            },
        )

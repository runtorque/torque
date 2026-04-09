import unittest

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub

install_aiohttp_stub()

from loom.state import MatrixState


class TerminalBackendStateTests(unittest.TestCase):
    def test_new_cells_inherit_group_default_terminal_backend(self):
        state = MatrixState()
        state.add_group("backend")
        state.update_group_settings("backend", default_terminal_backend="pty")

        agent = state.add_agent(name="Worker", group="backend")
        terminal = state.add_terminal(
            name="Shell",
            group="backend",
            parent_id=agent.id,
        )

        self.assertEqual(agent.terminal_backend, "pty")
        self.assertEqual(terminal.terminal_backend, "pty")

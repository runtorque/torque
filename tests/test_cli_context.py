import contextlib
import importlib.util
import io
import os
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


def _load_cli_module():
    path = Path(__file__).resolve().parents[1] / "bin" / "torque"
    loader = SourceFileLoader("torque_cli_context", str(path))
    spec = importlib.util.spec_from_loader("torque_cli_context", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class CliContextDetectionTests(unittest.TestCase):
    def setUp(self):
        self.cli = _load_cli_module()

    def _state(self):
        return {
            "agents": {
                "agent-1": {
                    "id": "agent-1",
                    "name": "Worker",
                    "slug": "worker",
                    "cell_type": "agent",
                    "group": "Build",
                    "parent_id": "",
                    "session_id": "agent-guid",
                },
                "term-1": {
                    "id": "term-1",
                    "name": "Shell",
                    "slug": "shell",
                    "cell_type": "terminal",
                    "group": "Build",
                    "parent_id": "agent-1",
                    "session_id": "term-guid",
                },
            }
        }

    def test_detect_context_uses_torque_cell_id_without_iterm_session(self):
        with mock.patch.dict(os.environ, {"TORQUE_CELL_ID": "term-1"}, clear=True):
            cell, parent, group = self.cli.detect_context(self._state())

        self.assertEqual(cell["id"], "term-1")
        self.assertEqual(parent["id"], "agent-1")
        self.assertEqual(group, "Build")

    def test_detect_context_keeps_iterm_session_fallback(self):
        with mock.patch.dict(
            os.environ,
            {"ITERM_SESSION_ID": "w0t0p0:term-guid"},
            clear=True,
        ):
            cell, parent, group = self.cli.detect_context(self._state())

        self.assertEqual(cell["id"], "term-1")
        self.assertEqual(parent["id"], "agent-1")
        self.assertEqual(group, "Build")

    def test_agent_add_autodetects_group_from_torque_cell_id(self):
        calls = []
        self.cli.get_state = lambda _port: self._state()

        def fake_api_call(cmd, port=0, **kwargs):
            calls.append((cmd, port, kwargs))
            return {
                "ok": True,
                "data": {
                    "agents": {
                        "new-agent": {
                            "id": "new-agent",
                            "name": "Helper",
                            "slug": "helper",
                            "group": "Build",
                        }
                    }
                },
            }

        self.cli.api_call = fake_api_call
        args = SimpleNamespace(
            port=18932,
            name="Helper",
            group="",
            command="",
            directory="",
            profile="",
            color="",
        )

        with mock.patch.dict(os.environ, {"TORQUE_CELL_ID": "agent-1"}, clear=True), \
             contextlib.redirect_stdout(io.StringIO()):
            self.cli.cmd_agent_add(args)

        self.assertEqual(calls[0][0], "add_agent")
        self.assertEqual(calls[0][1], 18932)
        self.assertEqual(calls[0][2]["group"], "Build")

    def test_terminal_add_autodetects_parent_from_torque_cell_id(self):
        calls = []
        self.cli.get_state = lambda _port: self._state()

        def fake_api_call(cmd, port=0, **kwargs):
            calls.append((cmd, port, kwargs))
            return {
                "ok": True,
                "data": {
                    "agents": {
                        "new-term": {
                            "id": "new-term",
                            "name": "Logs",
                            "slug": "logs",
                            "cell_type": "terminal",
                            "group": "Build",
                        }
                    }
                },
            }

        self.cli.api_call = fake_api_call
        args = SimpleNamespace(
            port=18932,
            name="Logs",
            parent="",
            group="",
            command="",
            directory="",
            profile="",
            color="",
        )

        with mock.patch.dict(os.environ, {"TORQUE_CELL_ID": "agent-1"}, clear=True), \
             contextlib.redirect_stdout(io.StringIO()):
            self.cli.cmd_terminal_add(args)

        self.assertEqual(calls[0][0], "add_terminal")
        self.assertEqual(calls[0][1], 18932)
        self.assertEqual(calls[0][2]["parent_id"], "agent-1")
        self.assertEqual(calls[0][2]["group"], "Build")


if __name__ == "__main__":
    unittest.main()

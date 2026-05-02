import contextlib
import importlib.util
import io
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


def _load_cli_module():
    path = Path(__file__).resolve().parents[1] / "bin" / "torque"
    loader = SourceFileLoader("torque_cli_digest", str(path))
    spec = importlib.util.spec_from_loader("torque_cli_digest", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class CliDigestTests(unittest.TestCase):
    def setUp(self):
        self.cli = _load_cli_module()

    def test_parser_accepts_digest_pause(self):
        parser = self.cli.build_parser()
        args = parser.parse_args(["digest", "pause", "panelsmith"])
        self.assertEqual(args.command, "digest")
        self.assertEqual(args.digest_cmd, "pause")
        self.assertEqual(args.identifier, "panelsmith")

    def test_parser_accepts_digest_resume(self):
        parser = self.cli.build_parser()
        args = parser.parse_args(["digest", "resume", "panelsmith"])
        self.assertEqual(args.command, "digest")
        self.assertEqual(args.digest_cmd, "resume")
        self.assertEqual(args.identifier, "panelsmith")

    def test_cmd_digest_pause_resolves_agent_and_calls_daemon(self):
        state = {
            "agents": {
                "eng-1": {
                    "id": "eng-1",
                    "name": "Panelsmith",
                    "slug": "panelsmith",
                    "group": "torque",
                    "kind": "engineer",
                    "cell_type": "agent",
                }
            }
        }
        args = SimpleNamespace(port=18932, identifier="panelsmith")

        with mock.patch.object(self.cli, "get_state", return_value=state), \
             mock.patch.object(
                 self.cli,
                 "api_call",
                 return_value={"ok": True, "data": {"agent_id": "eng-1", "paused": True}},
             ) as api_call:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.cli.cmd_digest_pause(args)

        api_call.assert_called_once_with(
            "digest_pause",
            port=18932,
            agent_id="eng-1",
        )
        self.assertIn('Digest paused for "Panelsmith"', out.getvalue())

    def test_cmd_digest_resume_resolves_agent_and_calls_daemon(self):
        state = {
            "agents": {
                "eng-1": {
                    "id": "eng-1",
                    "name": "Panelsmith",
                    "slug": "panelsmith",
                    "group": "torque",
                    "kind": "engineer",
                    "cell_type": "agent",
                }
            }
        }
        args = SimpleNamespace(port=18932, identifier="Panelsmith")

        with mock.patch.object(self.cli, "get_state", return_value=state), \
             mock.patch.object(
                 self.cli,
                 "api_call",
                 return_value={"ok": True, "data": {"agent_id": "eng-1", "paused": False}},
             ) as api_call:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.cli.cmd_digest_resume(args)

        api_call.assert_called_once_with(
            "digest_resume",
            port=18932,
            agent_id="eng-1",
        )
        self.assertIn('Digest resumed for "Panelsmith"', out.getvalue())

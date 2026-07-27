"""Coverage for persisted Codex Fast startup preference composition."""

import shlex
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from torque.adapters.codex import (
    _append_codex_config_cli_flags,
    _codex_resume_config_cli_command,
    _codex_opts_with_fast_mode,
)


class CodexFastStartupTests(unittest.TestCase):
    def test_explicit_fast_replaces_custom_service_tier(self):
        opts = _codex_opts_with_fast_mode(
            ["--model", "gpt-5", "-c", "service_tier=flex", "--config=foo=bar"],
            "on",
        )
        self.assertEqual(opts.count("service_tier=priority"), 1)
        self.assertNotIn("service_tier=flex", opts)
        self.assertIn("--model", opts)

    def test_inherit_keeps_custom_service_tier_untouched(self):
        opts = ["-c", "service_tier=flex", "--model", "gpt-5"]
        self.assertEqual(_codex_opts_with_fast_mode(opts, "inherit"), opts)

    def test_fresh_and_resume_commands_get_one_effective_tier(self):
        config = {"mcp_servers": {"torque": {"url": "http://localhost/mcp", "env_http_headers": {}}}, "hooks": {}}
        fresh = _append_codex_config_cli_flags(
            "codex -c service_tier=flex prompt", config, None, fast_mode="off"
        )
        resume = _codex_resume_config_cli_command(
            "codex -c service_tier=flex prompt", config, None, fast_mode="on"
        )
        self.assertIn("service_tier=default", shlex.split(fresh))
        self.assertNotIn("service_tier=flex", shlex.split(fresh))
        self.assertIn("service_tier=priority", shlex.split(resume))
        self.assertNotIn("service_tier=flex", shlex.split(resume))

    def test_off_is_the_default_service_tier_contract(self):
        opts = _codex_opts_with_fast_mode(["--model", "gpt-5"], "off")
        self.assertEqual(opts[-2:], ["-c", "service_tier=default"])

    def test_adapter_reads_resolved_preference_from_launch_cell(self):
        from torque.adapters.codex import CodexAdapter
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as data:
            cell = SimpleNamespace(id="fast-agent", fast_mode="on")
            with mock.patch.dict("os.environ", {"TORQUE_DATA_DIR": data}, clear=False):
                CodexAdapter().prepare_launch_command(cell, tmp, "codex -c service_tier=flex")
            script = Path(data, "codex", "agents", "fast-agent", "launch.sh").read_text()
            self.assertIn("service_tier=priority", script)
            self.assertNotIn("service_tier=flex", script)


if __name__ == "__main__":
    unittest.main()

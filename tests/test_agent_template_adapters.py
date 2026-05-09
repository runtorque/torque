import fcntl
import json
import multiprocessing
import os
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from torque.adapters import detect_by_command, get_default_command_for_provider, get_providers
from torque.adapters.claude_code import ClaudeCodeAdapter
from torque.adapters.codex import CodexAdapter
from torque.adapters.generic import GenericAdapter


def _claude_mcp_config_worker(args):
    working_dir, operation = args
    adapter = ClaudeCodeAdapter()
    if operation == "install":
        return adapter.install_mcp_config(working_dir)
    adapter.uninstall_mcp_config(working_dir)
    return True


class AgentTemplateAdapterTests(unittest.TestCase):
    def test_claude_inject_system_prompt_writes_instructions_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = ClaudeCodeAdapter()
            result = adapter.inject_system_prompt(tmp, "Follow the spec.")
            self.assertEqual(result, "")
            self.assertEqual(
                (Path(tmp) / ".claude" / "instructions.md").read_text(),
                "Follow the spec.\n",
            )
            self.assertEqual(adapter.resolve_model_flags("sonnet"), " --model sonnet")
            self.assertEqual(
                adapter.resolve_reasoning_effort_flags("high"),
                " --effort high",
            )

    def test_claude_persistent_prompts_use_named_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = ClaudeCodeAdapter()
            result = adapter.inject_persistent_prompt(
                tmp, "torque-system-prompt-agent-1.md", "First prompt."
            )
            self.assertTrue(
                result.startswith(" --append-system-prompt-file ")
            )
            self.assertTrue(
                result.endswith("/.torque/torque-system-prompt-agent-1.md")
            )
            adapter.inject_persistent_prompt(
                tmp, "torque-system-prompt-agent-2.md", "Second prompt."
            )
            torque_dir = Path(tmp) / ".torque"
            self.assertEqual(
                (torque_dir / "torque-system-prompt-agent-1.md").read_text(),
                "First prompt.\n",
            )
            self.assertEqual(
                (torque_dir / "torque-system-prompt-agent-2.md").read_text(),
                "Second prompt.\n",
            )
            adapter.uninstall_persistent_prompt(tmp, "torque-system-prompt-agent-1.md")
            self.assertFalse((torque_dir / "torque-system-prompt-agent-1.md").exists())
            self.assertTrue((torque_dir / "torque-system-prompt-agent-2.md").exists())

    def test_claude_persistent_prompt_uninstall_without_filename_removes_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = ClaudeCodeAdapter()
            adapter.inject_persistent_prompt(
                tmp, "torque-system-prompt-agent-1.md", "First prompt."
            )
            adapter.inject_persistent_prompt(
                tmp, "torque-system-prompt-agent-2.md", "Second prompt."
            )

            adapter.uninstall_persistent_prompt(tmp)

            torque_dir = Path(tmp) / ".torque"
            self.assertFalse((torque_dir / "torque-system-prompt-agent-1.md").exists())
            self.assertFalse((torque_dir / "torque-system-prompt-agent-2.md").exists())

    def test_claude_hook_install_and_cleanup_preserve_user_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = ClaudeCodeAdapter()
            settings_file = Path(tmp) / ".claude" / "settings.local.json"
            settings_file.parent.mkdir(parents=True, exist_ok=True)
            settings_file.write_text(
                json.dumps(
                    {
                        "theme": "dark",
                        "hooks": {
                            "Stop": [{
                                "hooks": [{"type": "http", "url": "https://user.example/hook"}]
                            }],
                            "Notification": [{
                                "hooks": [{"type": "http", "url": "http://localhost:18932/events"}]
                            }],
                        },
                    }
                )
            )

            with mock.patch.dict(os.environ, {"TORQUE_PORT": "18933"}, clear=False):
                self.assertTrue(adapter.install_hooks(tmp))

            installed = json.loads(settings_file.read_text())
            self.assertEqual(installed["theme"], "dark")
            self.assertEqual(len(installed["hooks"]["Stop"]), 2)
            self.assertEqual(
                installed["hooks"]["Stop"][0]["hooks"][0]["url"],
                "https://user.example/hook",
            )
            self.assertEqual(len(installed["hooks"]["SessionEnd"]), 1)
            self.assertEqual(len(installed["hooks"]["Notification"]), 1)
            session_start_hook = installed["hooks"]["SessionStart"][0]["hooks"][0]
            self.assertIn("http://localhost:18933/events", session_start_hook["command"])
            self.assertIn("--fail", session_start_hook["command"])
            self.assertIn("--retry 3", session_start_hook["command"])
            self.assertIn("event_id", session_start_hook["command"])
            self.assertNotIn("> /dev/null", session_start_hook["command"])
            self.assertNotIn("http://localhost:18932/events", session_start_hook["command"])

            adapter.uninstall_hooks(tmp)

            cleaned = json.loads(settings_file.read_text())
            self.assertEqual(cleaned, {
                "theme": "dark",
                "hooks": {
                    "Stop": [{
                        "hooks": [{"type": "http", "url": "https://user.example/hook"}]
                    }]
                },
            })

    def test_claude_parse_session_end_event(self):
        adapter = ClaudeCodeAdapter()

        event = adapter.parse_event(
            {"hook_event_name": "SessionEnd", "reason": "clear"},
            SimpleNamespace(id="agent-1"),
        )

        self.assertIsNotNone(event)
        self.assertEqual(event.event_type, "session_end")
        self.assertEqual(event.data["reason"], "clear")

    def test_claude_mcp_config_install_and_cleanup_preserve_other_servers(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = ClaudeCodeAdapter()
            mcp_file = Path(tmp) / ".mcp.json"
            mcp_file.write_text(
                json.dumps(
                    {
                        "mcpServers": {
                            "github": {"type": "http", "url": "https://example.test/mcp"}
                        }
                    }
                )
            )

            with mock.patch(
                "torque.adapters.claude_code.os.rename", wraps=os.rename
            ) as rename, mock.patch(
                "torque.adapters.claude_code.fcntl.flock", wraps=fcntl.flock
            ) as flock:
                self.assertTrue(adapter.install_mcp_config(tmp))
                self.assertEqual(Path(rename.call_args.args[1]), mcp_file)
                self.assertEqual(Path(rename.call_args.args[0]).parent, Path(tmp))
                self.assertIn(
                    fcntl.LOCK_EX, [call.args[1] for call in flock.call_args_list]
                )

            installed = json.loads(mcp_file.read_text())
            self.assertIn("github", installed["mcpServers"])
            self.assertEqual(installed["mcpServers"]["torque"]["type"], "http")
            self.assertEqual(
                installed["mcpServers"]["torque"]["headers"]["X-Torque-Cell-Id"],
                "${TORQUE_CELL_ID}",
            )

            with mock.patch(
                "torque.adapters.claude_code.os.rename", wraps=os.rename
            ) as rename:
                adapter.uninstall_mcp_config(tmp)
                self.assertEqual(Path(rename.call_args.args[1]), mcp_file)
                self.assertEqual(Path(rename.call_args.args[0]).parent, Path(tmp))

            cleaned = json.loads(mcp_file.read_text())
            self.assertEqual(
                cleaned,
                {
                    "mcpServers": {
                        "github": {"type": "http", "url": "https://example.test/mcp"}
                    }
                },
            )

    def test_claude_mcp_config_concurrent_operations_preserve_user_servers(self):
        with tempfile.TemporaryDirectory() as tmp:
            mcp_file = Path(tmp) / ".mcp.json"
            user_servers = {
                "github": {"type": "http", "url": "https://example.test/mcp"},
                "linear": {"type": "http", "url": "https://linear.example/mcp"},
            }
            mcp_file.write_text(json.dumps({"mcpServers": user_servers}))

            ctx = multiprocessing.get_context("fork")
            with ctx.Pool(5) as pool:
                results = pool.map(
                    _claude_mcp_config_worker, [(tmp, "install")] * 5
                )

            self.assertEqual(results, [True] * 5)
            installed = json.loads(mcp_file.read_text())
            servers = installed["mcpServers"]
            self.assertEqual(servers["github"], user_servers["github"])
            self.assertEqual(servers["linear"], user_servers["linear"])
            self.assertEqual(list(servers).count("torque"), 1)
            self.assertEqual(servers["torque"]["type"], "http")

            with ctx.Pool(10) as pool:
                results = pool.map(
                    _claude_mcp_config_worker,
                    [(tmp, operation) for operation in ["install", "uninstall"] * 5],
                )

            self.assertEqual(results, [True] * 10)
            final = json.loads(mcp_file.read_text())
            servers = final["mcpServers"]
            self.assertEqual(servers["github"], user_servers["github"])
            self.assertEqual(servers["linear"], user_servers["linear"])
            if "torque" in servers:
                self.assertIn(servers["torque"]["type"], {"http", "stdio"})

    def test_claude_engineer_mcp_config_uses_local_stdio_entrypoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = ClaudeCodeAdapter()

            self.assertTrue(
                adapter.install_mcp_config(
                    tmp, mcp_entrypoint="torque/mcp_engineer.py"
                )
            )

            installed = json.loads((Path(tmp) / ".mcp.json").read_text())
            torque_entry = installed["mcpServers"]["torque"]
            self.assertEqual(torque_entry["type"], "stdio")
            self.assertIn("command", torque_entry)
            self.assertIn("args", torque_entry)
            self.assertIn("torque.mcp_engineer", " ".join(torque_entry["args"]))
            self.assertNotIn("url", torque_entry)
            self.assertNotIn("headers", torque_entry)

    def test_codex_system_prompt_and_model_flags_use_cli_args(self):
        adapter = CodexAdapter()
        self.assertEqual(
            adapter.inject_system_prompt("/tmp", "Be precise."),
            " 'Be precise.'",
        )
        self.assertEqual(adapter.resolve_model_flags("gpt-5"), " --model gpt-5")
        self.assertEqual(
            adapter.resolve_reasoning_effort_flags("high"),
            " -c model_reasoning_effort=high",
        )

    def test_codex_persistent_prompts_use_cli_args(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = CodexAdapter()
            result = adapter.inject_persistent_prompt(
                tmp, "torque-system-prompt-agent-1.md", "First prompt."
            )
            self.assertEqual(result, "")
            self.assertEqual(
                (Path(tmp) / ".torque" / "torque-system-prompt-agent-1.md").read_text(),
                "First prompt.\n",
            )
            config = (Path(tmp) / ".codex" / "config.toml").read_text()
            self.assertIn("model_instructions_file =", config)
            self.assertIn("torque-system-prompt-agent-1.md", config)
            self.assertFalse(
                (Path(tmp) / ".codex" / "AGENTS.md").exists()
            )

    def test_codex_persistent_prompt_bootstraps_startup_prompt(self):
        adapter = CodexAdapter()

        self.assertEqual(
            adapter.startup_prompt_from_persistent_prompt("First prompt."),
            "First prompt.",
        )

    def test_codex_persistent_prompt_cleanup_removes_only_targeted_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = CodexAdapter()
            agents_file = Path(tmp) / ".codex" / "AGENTS.md"
            agents_file.parent.mkdir(parents=True, exist_ok=True)
            agents_file.write_text(
                "\n".join([
                    "<!-- Torque system prompt: torque-system-prompt-agent-1.md (managed by Torque, do not edit) -->",
                    "First prompt.",
                    "<!-- Torque system prompt: torque-system-prompt-agent-1.md (managed by Torque, do not edit) -->",
                    "",
                    "User notes stay here.",
                    "",
                    "<!-- Torque system prompt: torque-system-prompt-agent-2.md (managed by Torque, do not edit) -->",
                    "Second prompt.",
                    "<!-- Torque system prompt: torque-system-prompt-agent-2.md (managed by Torque, do not edit) -->",
                    "",
                ])
            )

            adapter.uninstall_persistent_prompt(tmp, "torque-system-prompt-agent-1.md")

            content = agents_file.read_text()
            self.assertNotIn("First prompt.", content)
            self.assertIn("User notes stay here.", content)
            self.assertIn("Second prompt.", content)

    def test_codex_persistent_prompt_cleanup_removes_managed_prompt_file_and_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = CodexAdapter()
            adapter.inject_persistent_prompt(
                tmp, "torque-system-prompt-agent-1.md", "First prompt."
            )

            adapter.uninstall_persistent_prompt(tmp, "torque-system-prompt-agent-1.md")

            self.assertFalse(
                (Path(tmp) / ".torque" / "torque-system-prompt-agent-1.md").exists()
            )
            self.assertFalse(
                (Path(tmp) / ".codex" / "config.toml").exists()
            )

    def test_codex_resume_command_preserves_flags(self):
        adapter = CodexAdapter()
        resumed = adapter.get_resume_command(
            "codex --model gpt-5 'Follow the spec.'",
            "session-123",
        )
        self.assertEqual(
            resumed,
            "codex resume --model gpt-5 session-123 'Follow the spec.'",
        )

    def test_codex_hook_install_and_cleanup_preserve_user_entries(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as codex_home:
            adapter = CodexAdapter()
            hooks_file = Path(tmp) / ".codex" / "hooks.json"
            hooks_file.parent.mkdir(parents=True, exist_ok=True)
            hooks_file.write_text(
                json.dumps(
                    {
                        "hooks": {
                            "Stop": [{
                                "hooks": [{"type": "command", "command": "echo user-stop"}]
                            }],
                            "PreToolUse": [{
                                "hooks": [{
                                    "type": "command",
                                    "command": "curl -s -X POST http://localhost:18932/events -d '{}' ",
                                }]
                            }],
                        }
                    }
                )
            )

            with mock.patch.dict(
                os.environ,
                {"TORQUE_PORT": "18933", "CODEX_HOME": codex_home},
                clear=False,
            ):
                self.assertTrue(adapter.install_hooks(tmp))

            installed = json.loads(hooks_file.read_text())
            self.assertEqual(len(installed["hooks"]["Stop"]), 2)
            self.assertEqual(
                installed["hooks"]["Stop"][0]["hooks"][0]["command"],
                "echo user-stop",
            )
            self.assertEqual(len(installed["hooks"]["PreToolUse"]), 1)
            session_start_hook = installed["hooks"]["SessionStart"][0]["hooks"][0]
            self.assertIn("http://localhost:18933/events", session_start_hook["command"])
            self.assertIn("--fail", session_start_hook["command"])
            self.assertIn("--retry 3", session_start_hook["command"])
            self.assertIn("event_id", session_start_hook["command"])
            self.assertNotIn("> /dev/null", session_start_hook["command"])
            self.assertNotIn("http://localhost:18932/events", session_start_hook["command"])

            with mock.patch.dict(os.environ, {"CODEX_HOME": codex_home}, clear=False):
                adapter.uninstall_hooks(tmp)

            cleaned = json.loads(hooks_file.read_text())
            self.assertEqual(
                cleaned,
                {
                    "hooks": {
                        "Stop": [{
                            "hooks": [{"type": "command", "command": "echo user-stop"}]
                        }]
                    }
                },
            )

    def test_codex_mcp_config_install_and_cleanup_restore_user_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = CodexAdapter()
            config_file = Path(tmp) / ".codex" / "config.toml"
            config_file.parent.mkdir(parents=True, exist_ok=True)
            config_file.write_text(
                "[profiles.default]\nmodel = \"gpt-5\"\n"
            )

            with mock.patch.dict(os.environ, {"TORQUE_PORT": "18933"}, clear=False):
                self.assertTrue(adapter.install_mcp_config(tmp))

            installed = config_file.read_text()
            self.assertIn("[profiles.default]", installed)
            self.assertIn("[mcp_servers.torque]", installed)
            self.assertIn("hooks = true", installed)
            self.assertNotIn("codex_hooks = true", installed)
            self.assertIn('url = "http://127.0.0.1:18933/mcp"', installed)

            adapter.uninstall_mcp_config(tmp)

            self.assertEqual(
                config_file.read_text(),
                "[profiles.default]\nmodel = \"gpt-5\"\n",
            )

    def test_codex_mcp_config_preserves_model_instructions_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = CodexAdapter()
            adapter.inject_persistent_prompt(
                tmp, "torque-system-prompt-agent-1.md", "First prompt."
            )

            self.assertTrue(adapter.install_mcp_config(tmp))

            installed = (Path(tmp) / ".codex" / "config.toml").read_text()
            self.assertIn("model_instructions_file =", installed)
            self.assertIn("torque-system-prompt-agent-1.md", installed)
            self.assertIn("[mcp_servers.torque]", installed)
            self.assertIn("hooks = true", installed)
            self.assertNotIn("codex_hooks = true", installed)

    def test_codex_mcp_config_cleanup_removes_new_and_legacy_managed_features(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = CodexAdapter()
            config_file = Path(tmp) / ".codex" / "config.toml"
            config_file.parent.mkdir(parents=True, exist_ok=True)
            config_file.write_text(
                "\n".join([
                    "[features]",
                    "web_search = true",
                    "# -- Torque Codex hooks feature (managed by Torque, do not edit) --",
                    "hooks = true",
                    "# -- Torque Codex hooks feature (managed by Torque, do not edit) --",
                    "codex_hooks = true",
                    "",
                    "# -- Torque MCP server (managed by Torque, do not edit) --",
                    "[mcp_servers.torque]",
                    'url = "http://127.0.0.1:18932/mcp"',
                    'env_http_headers = { "X-Torque-Cell-Id" = "TORQUE_CELL_ID" }',
                    "",
                    "[profiles.default]",
                    'model = "gpt-5"',
                    "",
                ])
            )

            adapter.uninstall_mcp_config(tmp)

            cleaned = config_file.read_text()
            self.assertIn("[features]\nweb_search = true", cleaned)
            self.assertIn("[profiles.default]", cleaned)
            self.assertNotIn("Torque Codex hooks feature", cleaned)
            self.assertNotIn("hooks = true", cleaned)
            self.assertNotIn("codex_hooks = true", cleaned)
            self.assertNotIn("[mcp_servers.torque]", cleaned)

    def test_codex_mcp_config_cleanup_removes_orphaned_legacy_feature_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = CodexAdapter()
            config_file = Path(tmp) / ".codex" / "config.toml"
            config_file.parent.mkdir(parents=True, exist_ok=True)
            config_file.write_text(
                "\n".join([
                    "# -- Torque Codex hooks feature (managed by Torque, do not edit) --",
                    "[features]",
                    "codex_hooks = true",
                    "",
                    "# -- Torque MCP server (managed by Torque, do not edit) --",
                    "[mcp_servers.torque]",
                    'url = "http://127.0.0.1:18932/mcp"',
                    'env_http_headers = { "X-Torque-Cell-Id" = "TORQUE_CELL_ID" }',
                    "",
                ])
            )

            adapter.uninstall_mcp_config(tmp)

            self.assertFalse(config_file.exists())

    def test_codex_hook_install_writes_trusted_state_and_removes_stale_hooks(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as codex_home:
            adapter = CodexAdapter()
            hooks_file = Path(tmp) / ".codex" / "hooks.json"
            hooks_file.parent.mkdir(parents=True, exist_ok=True)
            hooks_file.write_text(
                json.dumps(
                    {
                        "hooks": {
                            "Stop": [{
                                "hooks": [{"type": "command", "command": "echo user-stop"}]
                            }],
                            "PreToolUse": [{
                                "hooks": [{
                                    "type": "command",
                                    "command": "curl -s -X POST http://localhost:18932/events -d '{}' ",
                                }]
                            }],
                        }
                    }
                )
            )
            user_config = Path(codex_home) / "config.toml"
            user_config.write_text('model = "gpt-5"\n')

            with mock.patch.dict(
                os.environ,
                {"TORQUE_PORT": "18933", "CODEX_HOME": codex_home},
                clear=False,
            ):
                self.assertTrue(adapter.install_hooks(tmp))

            installed = json.loads(hooks_file.read_text())
            self.assertEqual(len(installed["hooks"]["Stop"]), 2)
            self.assertEqual(
                installed["hooks"]["Stop"][0]["hooks"][0]["command"],
                "echo user-stop",
            )
            self.assertEqual(len(installed["hooks"]["PreToolUse"]), 1)
            session_start_hook = installed["hooks"]["SessionStart"][0]["hooks"][0]
            self.assertIn("http://localhost:18933/events", session_start_hook["command"])
            self.assertNotIn("http://localhost:18932/events", hooks_file.read_text())

            source = os.path.abspath(str(hooks_file))
            trust_config = user_config.read_text()
            self.assertIn('model = "gpt-5"', trust_config)
            self.assertIn(
                "# -- Torque Codex hook trust: "
                f"{source} (managed by Torque, do not edit) --",
                trust_config,
            )
            self.assertIn(f'[hooks.state."{source}:session_start:0:0"]', trust_config)
            self.assertIn(f'[hooks.state."{source}:pre_tool_use:0:0"]', trust_config)
            self.assertIn(f'[hooks.state."{source}:stop:1:0"]', trust_config)
            self.assertEqual(
                len(re.findall(r'trusted_hash = "sha256:[0-9a-f]{64}"', trust_config)),
                3,
            )

            with mock.patch.dict(os.environ, {"CODEX_HOME": codex_home}, clear=False):
                adapter.uninstall_hooks(tmp)

            cleaned_hooks = json.loads(hooks_file.read_text())
            self.assertEqual(
                cleaned_hooks,
                {
                    "hooks": {
                        "Stop": [{
                            "hooks": [{"type": "command", "command": "echo user-stop"}]
                        }]
                    }
                },
            )
            self.assertEqual(user_config.read_text(), 'model = "gpt-5"\n')

    def test_codex_engineer_mcp_config_uses_local_stdio_entrypoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = CodexAdapter()

            self.assertTrue(
                adapter.install_mcp_config(
                    tmp, mcp_entrypoint="torque/mcp_engineer.py"
                )
            )

            installed = (Path(tmp) / ".codex" / "config.toml").read_text()
            self.assertIn("[mcp_servers.torque]", installed)
            self.assertIn("command = ", installed)
            self.assertIn("torque.mcp_engineer", installed)
            self.assertNotIn('url = "http://127.0.0.1', installed)
            self.assertNotIn('env_http_headers = { "X-Torque-Cell-Id"', installed)

    def test_generic_adapter_is_noop_for_template_specific_flags(self):
        adapter = GenericAdapter()
        self.assertEqual(adapter.inject_system_prompt("/tmp", "ignored"), "")
        self.assertEqual(adapter.resolve_model_flags("ignored"), "")
        self.assertEqual(adapter.resolve_reasoning_effort_flags("ignored"), "")

    def test_provider_registry_and_detection_cover_cross_provider_semantics(self):
        providers = get_providers()

        self.assertEqual(
            [p["name"] for p in providers],
            ["claude-code", "codex", "gemini-cli"],
        )
        self.assertEqual(
            providers[0]["reasoning_efforts"],
            ["low", "medium", "high", "xhigh", "max"],
        )
        self.assertEqual(
            providers[1]["reasoning_efforts"],
            ["low", "medium", "high", "xhigh"],
        )
        self.assertEqual(providers[2]["reasoning_efforts"], [])
        self.assertEqual(get_default_command_for_provider("codex"), "codex")
        self.assertEqual(get_default_command_for_provider("gemini-cli"), "gemini")
        self.assertEqual(get_default_command_for_provider("missing"), "")
        self.assertEqual(detect_by_command("claude --model sonnet").name, "claude-code")
        self.assertEqual(detect_by_command("claude-gateway").name, "claude-code")
        self.assertEqual(detect_by_command("codex --model gpt-5").name, "codex")
        self.assertEqual(detect_by_command("codex-gateway").name, "codex")
        self.assertEqual(detect_by_command("gemini --model pro").name, "gemini-cli")
        self.assertIsNone(detect_by_command("python app.py"))

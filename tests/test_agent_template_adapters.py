import fcntl
import http.server
import json
import multiprocessing
import os
import queue
import re
import shlex
import subprocess
import sys
import tempfile
import threading
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


def _start_statusline_event_server(received: queue.Queue, *, marker_file: Path | None = None, requests: int = 1):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            received.put({
                "path": self.path,
                "cell_id": self.headers.get("X-Torque-Cell-Id", ""),
                "body": json.loads(body.decode("utf-8")),
            })
            if marker_file is not None:
                marker_file.write_text("posted")
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, fmt, *args):
            pass

    server = http.server.HTTPServer(("localhost", 0), Handler)

    def serve():
        for _ in range(requests):
            server.handle_request()

    thread = threading.Thread(target=serve)
    thread.daemon = True
    thread.start()
    return server, thread


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
            self.assertIs(installed.get("autoMemoryEnabled"), False)
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
            self.assertFalse(
                (Path(tmp) / ".torque" / "claude-auto-memory-original.json").exists()
            )

    def test_claude_hook_cleanup_restores_user_auto_memory_setting(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = ClaudeCodeAdapter()
            settings_file = Path(tmp) / ".claude" / "settings.local.json"
            settings_file.parent.mkdir(parents=True, exist_ok=True)
            settings_file.write_text(json.dumps({
                "theme": "dark",
                "autoMemoryEnabled": True,
            }))

            with mock.patch.dict(os.environ, {"TORQUE_PORT": "1"}, clear=False):
                self.assertTrue(adapter.install_hooks(tmp))

            installed = json.loads(settings_file.read_text())
            self.assertIs(installed.get("autoMemoryEnabled"), False)

            adapter.uninstall_hooks(tmp)

            cleaned = json.loads(settings_file.read_text())
            self.assertEqual(cleaned.get("theme"), "dark")
            self.assertIs(cleaned.get("autoMemoryEnabled"), True)

    def test_claude_parse_session_end_event(self):
        adapter = ClaudeCodeAdapter()

        event = adapter.parse_event(
            {"hook_event_name": "SessionEnd", "reason": "clear"},
            SimpleNamespace(id="agent-1"),
        )

        self.assertIsNotNone(event)
        self.assertEqual(event.event_type, "session_end")
        self.assertEqual(event.data["reason"], "clear")

    def test_claude_parse_statusline_context_update_event(self):
        adapter = ClaudeCodeAdapter()

        event = adapter.parse_event(
            {
                "hook_event_name": "StatusLine",
                "session_id": "claude-session-1",
                "model": {"id": "claude-sonnet-4-6"},
                "context_window": {
                    "context_window_size": 200000,
                    "used_percentage": 12.5,
                    "total_tokens": 25000,
                    "input_tokens": 24000,
                    "output_tokens": 950,
                    "reasoning_output_tokens": 50,
                    "cache_read_input_tokens": 1000,
                    "cache_creation_input_tokens": 250,
                    "session_total_tokens": 123456,
                },
                "rate_limits": {
                    "five_hour": {
                        "used_percentage": 42.4,
                        "resets_at": "2026-05-26T05:00:00Z",
                    },
                    "seven_day": {
                        "available": False,
                    },
                },
            },
            SimpleNamespace(id="agent-1"),
        )

        self.assertIsNotNone(event)
        self.assertEqual(event.event_type, "context_update")
        context_window = event.data["context_window"]
        self.assertEqual(context_window["source"], "claude_statusline")
        self.assertEqual(context_window["model"], "claude-sonnet-4-6")
        self.assertEqual(context_window["session_id"], "claude-session-1")
        self.assertEqual(context_window["used_tokens"], 25000)
        self.assertEqual(context_window["limit_tokens"], 200000)
        self.assertEqual(context_window["used_pct"], 12.5)
        self.assertEqual(context_window["input_tokens"], 24000)
        self.assertEqual(context_window["output_tokens"], 950)
        self.assertEqual(context_window["cached_input_tokens"], 1250)
        self.assertEqual(context_window["reasoning_output_tokens"], 50)
        self.assertEqual(context_window["session_total_tokens"], 123456)
        self.assertEqual(
            event.data["provider_usage"],
            {
                "five_hour": {
                    "available": True,
                    "used_percentage": 42,
                    "resets_at": "2026-05-26T05:00:00Z",
                },
                "seven_day": {
                    "available": False,
                    "used_percentage": None,
                    "resets_at": None,
                },
            },
        )

        self.assertIsNone(
            adapter.parse_event(
                {
                    "hook_event_name": "StatusLine",
                    "context_window": {
                        "context_window_size": 200000,
                        "used_percentage": 0,
                    },
                },
                SimpleNamespace(id="agent-1"),
            )
        )

    def test_claude_parse_statusline_rate_limits_without_context(self):
        adapter = ClaudeCodeAdapter()

        event = adapter.parse_event(
            {
                "hook_event_name": "StatusLine",
                "rate_limits": {
                    "five_hour": {
                        "used_percentage": "63.8",
                        "resets_at": "2026-05-26T09:30:00Z",
                    },
                },
            },
            SimpleNamespace(id="agent-1"),
        )

        self.assertIsNotNone(event)
        self.assertEqual(event.event_type, "context_update")
        self.assertNotIn("context_window", event.data)
        self.assertEqual(
            event.data["provider_usage"],
            {
                "five_hour": {
                    "available": True,
                    "used_percentage": 64,
                    "resets_at": "2026-05-26T09:30:00Z",
                },
                "seven_day": {
                    "available": False,
                    "used_percentage": None,
                    "resets_at": None,
                },
            },
        )

    def test_claude_statusline_proxy_preserves_project_local_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = ClaudeCodeAdapter()
            settings_file = Path(tmp) / ".claude" / "settings.local.json"
            settings_file.parent.mkdir(parents=True, exist_ok=True)
            user_script = Path(tmp) / "user_statusline.py"
            user_script.write_text(
                "\n".join([
                    "#!/usr/bin/env python3",
                    "import json, sys",
                    "payload = json.load(sys.stdin)",
                    "sys.stdout.write('USER-LINE:' + payload.get('session_id', ''))",
                    "",
                ])
            )
            user_script.chmod(0o755)
            original_statusline = {
                "type": "command",
                "command": f"{shlex.quote(sys.executable)} {shlex.quote(str(user_script))}",
            }
            settings_file.write_text(
                json.dumps(
                    {
                        "theme": "dark",
                        "statusLine": original_statusline,
                    }
                )
            )

            with mock.patch.dict(os.environ, {"TORQUE_PORT": "1"}, clear=False):
                self.assertTrue(adapter.install_hooks(tmp))

            installed = json.loads(settings_file.read_text())
            proxy = installed["statusLine"]
            self.assertEqual(proxy["type"], "command")
            self.assertIn("claude-statusline-proxy.py", proxy["command"])
            self.assertTrue(
                (Path(tmp) / ".torque" / "claude-statusline-original.json").exists()
            )
            original_capture = json.loads(
                (Path(tmp) / ".torque" / "claude-statusline-original.json").read_text()
            )
            self.assertEqual(original_capture["source"], "project")
            self.assertEqual(original_capture["value"], original_statusline)

            payload = {
                "session_id": "claude-session-1",
                "context_window": {
                    "context_window_size": 200000,
                    "used_percentage": 12.5,
                    "total_tokens": 25000,
                },
            }
            result = subprocess.run(
                proxy["command"],
                shell=True,
                input=json.dumps(payload).encode("utf-8"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env={**os.environ, "TORQUE_CELL_ID": "agent-1", "TORQUE_PORT": "1"},
                timeout=5,
                check=False,
            )
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout.decode("utf-8"), "USER-LINE:claude-session-1")

            adapter.uninstall_hooks(tmp)

            cleaned = json.loads(settings_file.read_text())
            self.assertEqual(cleaned["statusLine"], original_statusline)
            self.assertFalse(
                (Path(tmp) / ".torque" / "claude-statusline-original.json").exists()
            )

    def test_claude_statusline_proxy_chains_effective_global_statusline_and_posts(self):
        received = queue.Queue()
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as home:
            marker_file = Path(tmp) / "post-before-chain"
            server, thread = _start_statusline_event_server(
                received,
                marker_file=marker_file,
            )
            self.addCleanup(server.server_close)
            self.addCleanup(thread.join, 5)

            adapter = ClaudeCodeAdapter()
            settings_file = Path(tmp) / ".claude" / "settings.local.json"
            settings_file.parent.mkdir(parents=True, exist_ok=True)
            settings_file.write_text(json.dumps({"theme": "dark"}))
            global_dir = Path(home) / ".claude"
            global_dir.mkdir(parents=True, exist_ok=True)
            base_script = Path(tmp) / "base_global_statusline.py"
            base_script.write_text(
                "#!/usr/bin/env python3\n"
                "import sys\n"
                "sys.stdout.write('BASE-GLOBAL')\n"
            )
            base_script.chmod(0o755)
            local_script = Path(tmp) / "local_global_statusline.py"
            local_script.write_text(
                "\n".join([
                    "#!/usr/bin/env python3",
                    "import json, os, sys",
                    "payload = json.load(sys.stdin)",
                    f"marker = {json.dumps(str(marker_file))}",
                    "sys.stdout.write("
                    "'GLOBAL-LINE:'"
                    " + payload.get('session_id', '')"
                    " + ':'"
                    " + str(os.path.exists(marker))"
                    ")",
                    "",
                ])
            )
            local_script.chmod(0o755)
            base_statusline = {
                "type": "command",
                "command": f"{shlex.quote(sys.executable)} {shlex.quote(str(base_script))}",
            }
            effective_statusline = {
                "type": "command",
                "command": f"{shlex.quote(sys.executable)} {shlex.quote(str(local_script))}",
            }
            (global_dir / "settings.json").write_text(
                json.dumps({"statusLine": base_statusline})
            )
            (global_dir / "settings.local.json").write_text(
                json.dumps({"statusLine": effective_statusline})
            )

            with mock.patch.dict(
                os.environ,
                {"HOME": home, "TORQUE_PORT": str(server.server_port)},
                clear=False,
            ):
                self.assertTrue(adapter.install_hooks(tmp))

            installed = json.loads(settings_file.read_text())
            proxy = installed["statusLine"]
            original_capture = json.loads(
                (Path(tmp) / ".torque" / "claude-statusline-original.json").read_text()
            )
            self.assertEqual(original_capture["source"], "global")
            self.assertEqual(original_capture["value"], effective_statusline)
            payload = {
                "session_id": "claude-session-1",
                "context_window": {
                    "context_window_size": 200000,
                    "used_percentage": 25,
                    "total_tokens": 50000,
                },
            }
            result = subprocess.run(
                proxy["command"],
                shell=True,
                input=json.dumps(payload).encode("utf-8"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env={**os.environ, "TORQUE_CELL_ID": "agent-1", "TORQUE_PORT": "1"},
                timeout=5,
                check=False,
            )
            self.assertEqual(result.returncode, 0)
            self.assertEqual(
                result.stdout.decode("utf-8"),
                "GLOBAL-LINE:claude-session-1:True",
            )
            posted = received.get(timeout=5)
            self.assertEqual(posted["path"], "/events")
            self.assertEqual(posted["cell_id"], "agent-1")
            self.assertEqual(posted["body"]["hook_event_name"], "StatusLine")
            self.assertEqual(posted["body"]["session_id"], "claude-session-1")

            adapter.uninstall_hooks(tmp)

            cleaned = json.loads(settings_file.read_text())
            self.assertNotIn("statusLine", cleaned)
            self.assertEqual(cleaned["theme"], "dark")
            self.assertFalse(
                (Path(tmp) / ".torque" / "claude-statusline-original.json").exists()
            )
            self.assertFalse(
                (Path(tmp) / ".torque" / "claude-statusline-proxy.py").exists()
            )

        server.server_close()
        thread.join(timeout=5)

    def test_claude_statusline_proxy_recaptures_legacy_missing_marker_on_reinstall(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as home:
            adapter = ClaudeCodeAdapter()
            root = Path(tmp)
            settings_file = root / ".claude" / "settings.local.json"
            settings_file.parent.mkdir(parents=True, exist_ok=True)
            settings_file.write_text(
                json.dumps(
                    {
                        "theme": "dark",
                        "statusLine": {
                            "type": "command",
                            "command": (
                                "python3 "
                                + shlex.quote(
                                    str(root / ".torque" / "claude-statusline-proxy.py")
                                )
                            ),
                        },
                    }
                )
            )
            original_file = root / ".torque" / "claude-statusline-original.json"
            original_file.parent.mkdir(parents=True, exist_ok=True)
            original_file.write_text(
                json.dumps({"present": False, "value": None}) + "\n"
            )

            global_dir = Path(home) / ".claude"
            global_dir.mkdir(parents=True, exist_ok=True)
            global_script = root / "global_statusline.py"
            global_script.write_text(
                "\n".join([
                    "#!/usr/bin/env python3",
                    "import json, sys",
                    "payload = json.load(sys.stdin)",
                    "sys.stdout.write('GLOBAL-LINE:' + payload.get('session_id', ''))",
                    "",
                ])
            )
            global_script.chmod(0o755)
            global_statusline = {
                "type": "command",
                "command": f"{shlex.quote(sys.executable)} {shlex.quote(str(global_script))}",
            }
            (global_dir / "settings.json").write_text(
                json.dumps({"statusLine": global_statusline})
            )

            with mock.patch.dict(
                os.environ,
                {"HOME": home, "TORQUE_PORT": "1"},
                clear=False,
            ):
                self.assertTrue(adapter.install_hooks(str(root)))

            installed = json.loads(settings_file.read_text())
            proxy = installed["statusLine"]
            recaptured = json.loads(original_file.read_text())
            self.assertEqual(recaptured["source"], "global")
            self.assertEqual(recaptured["value"], global_statusline)

            result = subprocess.run(
                proxy["command"],
                shell=True,
                input=json.dumps(
                    {
                        "session_id": "legacy-session",
                        "context_window": {"used_percentage": 9},
                    }
                ).encode("utf-8"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env={**os.environ, "TORQUE_CELL_ID": "agent-1", "TORQUE_PORT": "1"},
                timeout=5,
                check=False,
            )
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout.decode("utf-8"), "GLOBAL-LINE:legacy-session")

            adapter.uninstall_hooks(str(root))

            cleaned = json.loads(settings_file.read_text())
            self.assertEqual(cleaned, {"theme": "dark"})
            self.assertFalse(original_file.exists())

    def test_claude_statusline_proxy_preserves_legacy_project_marker_on_reinstall(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as home:
            adapter = ClaudeCodeAdapter()
            root = Path(tmp)
            settings_file = root / ".claude" / "settings.local.json"
            settings_file.parent.mkdir(parents=True, exist_ok=True)
            settings_file.write_text(
                json.dumps(
                    {
                        "theme": "dark",
                        "statusLine": {
                            "type": "command",
                            "command": (
                                "python3 "
                                + shlex.quote(
                                    str(root / ".torque" / "claude-statusline-proxy.py")
                                )
                            ),
                        },
                    }
                )
            )

            project_script = root / "project_statusline.py"
            project_script.write_text(
                "\n".join([
                    "#!/usr/bin/env python3",
                    "import json, sys",
                    "payload = json.load(sys.stdin)",
                    "sys.stdout.write('PROJECT-LINE:' + payload.get('session_id', ''))",
                    "",
                ])
            )
            project_script.chmod(0o755)
            project_statusline = {
                "type": "command",
                "command": f"{shlex.quote(sys.executable)} {shlex.quote(str(project_script))}",
            }
            original_file = root / ".torque" / "claude-statusline-original.json"
            original_file.parent.mkdir(parents=True, exist_ok=True)
            original_file.write_text(
                json.dumps({"present": True, "value": project_statusline}) + "\n"
            )

            global_dir = Path(home) / ".claude"
            global_dir.mkdir(parents=True, exist_ok=True)
            (global_dir / "settings.json").write_text(
                json.dumps(
                    {
                        "statusLine": {
                            "type": "command",
                            "command": "printf global-line",
                        }
                    }
                )
            )

            with mock.patch.dict(
                os.environ,
                {"HOME": home, "TORQUE_PORT": "1"},
                clear=False,
            ):
                self.assertTrue(adapter.install_hooks(str(root)))

            installed = json.loads(settings_file.read_text())
            proxy = installed["statusLine"]
            preserved = json.loads(original_file.read_text())
            self.assertNotIn("source", preserved)
            self.assertEqual(preserved["value"], project_statusline)

            result = subprocess.run(
                proxy["command"],
                shell=True,
                input=json.dumps({"session_id": "project-session"}).encode("utf-8"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env={**os.environ, "TORQUE_CELL_ID": "agent-1", "TORQUE_PORT": "1"},
                timeout=5,
                check=False,
            )
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout.decode("utf-8"), "PROJECT-LINE:project-session")

            adapter.uninstall_hooks(str(root))

            cleaned = json.loads(settings_file.read_text())
            self.assertEqual(cleaned["theme"], "dark")
            self.assertEqual(cleaned["statusLine"], project_statusline)

    def test_claude_statusline_proxy_takes_over_without_project_local_statusline(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as home:
            adapter = ClaudeCodeAdapter()
            settings_file = Path(tmp) / ".claude" / "settings.local.json"
            settings_file.parent.mkdir(parents=True, exist_ok=True)
            settings_file.write_text(json.dumps({"theme": "dark"}))

            with mock.patch.dict(
                os.environ,
                {"HOME": home, "TORQUE_PORT": "1"},
                clear=False,
            ):
                self.assertTrue(adapter.install_hooks(tmp))

            installed = json.loads(settings_file.read_text())
            proxy = installed["statusLine"]
            original_capture = json.loads(
                (Path(tmp) / ".torque" / "claude-statusline-original.json").read_text()
            )
            self.assertEqual(original_capture["source"], "none")
            self.assertFalse(original_capture["present"])
            payload = {
                "session_id": "claude-session-1",
                "model": {"display_name": "Claude Sonnet"},
                "workspace": {"current_dir": "/tmp/project"},
                "gitBranch": "feature/statusline",
                "context_window": {
                    "context_window_size": 200000,
                    "used_percentage": 25,
                    "total_tokens": 50000,
                },
            }
            result = subprocess.run(
                proxy["command"],
                shell=True,
                input=json.dumps(payload).encode("utf-8"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env={**os.environ, "TORQUE_CELL_ID": "agent-1", "TORQUE_PORT": "1"},
                timeout=5,
                check=False,
            )
            self.assertEqual(result.returncode, 0)
            self.assertEqual(
                result.stdout.decode("utf-8"),
                "Claude Sonnet · /tmp/project · feature/statusline · ctx 25%\n",
            )

            adapter.uninstall_hooks(tmp)

            cleaned = json.loads(settings_file.read_text())
            self.assertNotIn("statusLine", cleaned)
            self.assertEqual(cleaned["theme"], "dark")

    def test_claude_statusline_global_capture_uninstall_leaves_no_shadow_in_roots(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as home:
            global_dir = Path(home) / ".claude"
            global_dir.mkdir(parents=True, exist_ok=True)
            global_statusline = {
                "type": "command",
                "command": "printf global-statusline",
            }
            (global_dir / "settings.json").write_text(
                json.dumps({"statusLine": global_statusline})
            )
            adapter = ClaudeCodeAdapter()

            for root_name in ("repo-root", "worktree"):
                with self.subTest(root_name=root_name):
                    root = Path(tmp) / root_name
                    settings_file = root / ".claude" / "settings.local.json"
                    settings_file.parent.mkdir(parents=True, exist_ok=True)
                    settings_file.write_text(json.dumps({"theme": root_name}))

                    with mock.patch.dict(
                        os.environ,
                        {"HOME": home, "TORQUE_PORT": "1"},
                        clear=False,
                    ):
                        self.assertTrue(adapter.install_hooks(str(root)))

                    installed = json.loads(settings_file.read_text())
                    self.assertIn("claude-statusline-proxy.py", installed["statusLine"]["command"])
                    original_file = root / ".torque" / "claude-statusline-original.json"
                    original_capture = json.loads(original_file.read_text())
                    self.assertEqual(original_capture["source"], "global")
                    self.assertEqual(original_capture["value"], global_statusline)

                    adapter.uninstall_hooks(str(root))

                    cleaned = json.loads(settings_file.read_text())
                    self.assertEqual(cleaned, {"theme": root_name})
                    self.assertFalse(original_file.exists())
                    self.assertFalse(
                        (root / ".torque" / "claude-statusline-proxy.py").exists()
                    )

    def test_claude_statusline_proxy_bakes_install_time_port(self):
        received = queue.Queue()
        server, thread = _start_statusline_event_server(received)
        try:
            port = str(server.server_port)
            with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as home:
                adapter = ClaudeCodeAdapter()
                settings_file = Path(tmp) / ".claude" / "settings.local.json"
                settings_file.parent.mkdir(parents=True, exist_ok=True)
                settings_file.write_text(json.dumps({"theme": "dark"}))

                with mock.patch.dict(
                    os.environ,
                    {"HOME": home, "TORQUE_PORT": port},
                    clear=False,
                ):
                    self.assertTrue(adapter.install_hooks(tmp))

                installed = json.loads(settings_file.read_text())
                proxy = installed["statusLine"]
                proxy_source = (Path(tmp) / ".torque" / "claude-statusline-proxy.py").read_text()
                self.assertIn(f"http://localhost:{port}/events", proxy_source)
                self.assertNotIn('os.environ.get("TORQUE_PORT")', proxy_source)
                self.assertLess(
                    proxy_source.index("payload = _post_event(raw)"),
                    proxy_source.index("if not _forward_original(raw):"),
                )

                payload = {
                    "session_id": "claude-session-1",
                    "model": {"display_name": "Claude Sonnet"},
                    "workspace": {"current_dir": "/tmp/project"},
                    "gitBranch": "feature/statusline",
                    "context_window": {
                        "context_window_size": 200000,
                        "used_percentage": 25,
                        "total_tokens": 50000,
                    },
                }
                env = {k: v for k, v in os.environ.items() if k != "TORQUE_PORT"}
                env["TORQUE_CELL_ID"] = "agent-1"
                result = subprocess.run(
                    proxy["command"],
                    shell=True,
                    input=json.dumps(payload).encode("utf-8"),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=env,
                    timeout=5,
                    check=False,
                )

                self.assertEqual(result.returncode, 0)
                self.assertEqual(
                    result.stdout.decode("utf-8"),
                    "Claude Sonnet · /tmp/project · feature/statusline · ctx 25%\n",
                )
                posted = received.get(timeout=5)
                self.assertEqual(posted["path"], "/events")
                self.assertEqual(posted["cell_id"], "agent-1")
                self.assertEqual(posted["body"]["hook_event_name"], "StatusLine")
                self.assertEqual(posted["body"]["session_id"], "claude-session-1")
                self.assertIn("event_id", posted["body"])
        finally:
            server.server_close()
            thread.join(timeout=5)

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
            source_paths = {source, str(hooks_file.expanduser().resolve(strict=False))}
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
                3 * len(source_paths),
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

    def test_codex_parse_stop_attaches_context_window_from_transcript(self):
        with tempfile.TemporaryDirectory() as tmp:
            transcript = Path(tmp) / "rollout.jsonl"
            transcript.write_text(
                "\n".join([
                    "not-json",
                    json.dumps({
                        "type": "event_msg",
                        "payload": {
                            "type": "token_count",
                            "info": {
                                "total_token_usage": {"total_tokens": 1000},
                                "last_token_usage": {
                                    "input_tokens": 100,
                                    "cached_input_tokens": 20,
                                    "output_tokens": 10,
                                    "reasoning_output_tokens": 5,
                                    "total_tokens": 110,
                                },
                                "model_context_window": 1000,
                            },
                        },
                    }),
                    json.dumps({
                        "type": "event_msg",
                        "payload": {
                            "type": "token_count",
                            "info": {
                                "total_token_usage": {"total_tokens": 1799981},
                                "last_token_usage": {
                                    "input_tokens": 147293,
                                    "cached_input_tokens": 139648,
                                    "output_tokens": 439,
                                    "reasoning_output_tokens": 16,
                                    "total_tokens": 147732,
                                },
                                "model_context_window": 258400,
                            },
                            "rate_limits": {
                                "plan_type": "pro",
                                "limit_id": "codex",
                                "primary": {
                                    "used_percent": 42.4,
                                    "window_minutes": 300,
                                    "resets_at": 1779771600,
                                },
                                "secondary": {
                                    "used_percent": 12.2,
                                    "window_minutes": 10080,
                                    "resets_at": 1779787800,
                                },
                            },
                        },
                    }),
                ]) + "\n"
            )

            event = CodexAdapter().parse_event(
                {
                    "hook_event_name": "Stop",
                    "session_id": "codex-session-1",
                    "model": "gpt-5.4",
                    "transcript_path": str(transcript),
                    "last_assistant_message": "done",
                },
                SimpleNamespace(id="agent-1"),
            )

        self.assertIsNotNone(event)
        self.assertEqual(event.event_type, "session_end")
        context_window = event.data["context_window"]
        self.assertEqual(context_window["source"], "codex_transcript")
        self.assertEqual(context_window["model"], "gpt-5.4")
        self.assertEqual(context_window["session_id"], "codex-session-1")
        self.assertEqual(context_window["used_tokens"], 147732)
        self.assertEqual(context_window["limit_tokens"], 258400)
        self.assertEqual(context_window["input_tokens"], 147293)
        self.assertEqual(context_window["output_tokens"], 439)
        self.assertEqual(context_window["cached_input_tokens"], 139648)
        self.assertEqual(context_window["reasoning_output_tokens"], 16)
        self.assertEqual(context_window["session_total_tokens"], 1799981)
        self.assertAlmostEqual(context_window["used_pct"], 57.17, places=2)
        self.assertEqual(
            event.data["provider_usage"],
            {
                "five_hour": {
                    "available": True,
                    "used_percentage": 42,
                    "resets_at": "2026-05-26T05:00:00Z",
                },
                "seven_day": {
                    "available": True,
                    "used_percentage": 12,
                    "resets_at": "2026-05-26T09:30:00Z",
                },
            },
        )
        claude_event = ClaudeCodeAdapter().parse_event(
            {
                "hook_event_name": "StatusLine",
                "rate_limits": {
                    "five_hour": {
                        "used_percentage": 42.4,
                        "resets_at": "2026-05-26T05:00:00Z",
                    },
                    "seven_day": {
                        "used_percentage": 12.2,
                        "resets_at": "2026-05-26T09:30:00Z",
                    },
                },
            },
            SimpleNamespace(id="agent-1"),
        )
        self.assertIsNotNone(claude_event)
        self.assertEqual(
            event.data["provider_usage"]["five_hour"]["resets_at"],
            claude_event.data["provider_usage"]["five_hour"]["resets_at"],
        )
        self.assertEqual(
            event.data["provider_usage"]["seven_day"]["resets_at"],
            claude_event.data["provider_usage"]["seven_day"]["resets_at"],
        )

    def test_codex_token_count_without_rate_limits_leaves_provider_usage_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            transcript = Path(tmp) / "rollout.jsonl"
            transcript.write_text(
                json.dumps({
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {"total_tokens": 100},
                            "last_token_usage": {
                                "input_tokens": 40,
                                "cached_input_tokens": 10,
                                "output_tokens": 5,
                                "reasoning_output_tokens": 1,
                                "total_tokens": 45,
                            },
                            "model_context_window": 1000,
                        },
                    },
                }) + "\n"
            )

            event = CodexAdapter().parse_event(
                {
                    "hook_event_name": "Stop",
                    "session_id": "codex-session-1",
                    "model": "gpt-5.4",
                    "transcript_path": str(transcript),
                },
                SimpleNamespace(id="agent-1"),
            )

        self.assertIsNotNone(event)
        self.assertIn("context_window", event.data)
        self.assertNotIn("provider_usage", event.data)

    def test_codex_pre_tool_use_attaches_live_session_and_provider_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            transcript = Path(tmp) / "rollout.jsonl"
            transcript.write_text(
                json.dumps({
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {"total_tokens": 1000},
                            "last_token_usage": {
                                "input_tokens": 40,
                                "cached_input_tokens": 10,
                                "output_tokens": 5,
                                "reasoning_output_tokens": 1,
                                "total_tokens": 45,
                            },
                            "model_context_window": 1000,
                        },
                        "rate_limits": {
                            "primary": {
                                "used_percent": 63.8,
                                "window_minutes": 300,
                                "resets_at": 1779771600,
                            },
                            "secondary": {
                                "used_percent": 12.2,
                                "window_minutes": 10080,
                                "resets_at": 1779787800,
                            },
                        },
                    },
                }) + "\n"
            )

            event = CodexAdapter().parse_event(
                {
                    "hook_event_name": "PreToolUse",
                    "session_id": "codex-live-session",
                    "model": "gpt-5.4",
                    "transcript_path": str(transcript),
                    "tool_name": "Bash",
                    "tool_input": {"command": "echo hello"},
                },
                SimpleNamespace(id="agent-1"),
            )

        self.assertIsNotNone(event)
        self.assertEqual(event.event_type, "tool_start")
        self.assertEqual(event.data["session_id"], "codex-live-session")
        self.assertEqual(event.data["context_window"]["session_id"], "codex-live-session")
        self.assertEqual(
            event.data["provider_usage"]["five_hour"]["used_percentage"],
            64,
        )

    def test_codex_rate_limits_with_null_window_marks_that_window_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            transcript = Path(tmp) / "rollout.jsonl"
            transcript.write_text(
                json.dumps({
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {"total_tokens": 100},
                            "last_token_usage": {
                                "input_tokens": 40,
                                "cached_input_tokens": 10,
                                "output_tokens": 5,
                                "reasoning_output_tokens": 1,
                                "total_tokens": 45,
                            },
                            "model_context_window": 1000,
                        },
                        "rate_limits": {
                            "primary": {
                                "used_percent": 63.8,
                                "window_minutes": 300,
                                "resets_at": 1779771600,
                            },
                            "secondary": None,
                        },
                    },
                }) + "\n"
            )

            event = CodexAdapter().parse_event(
                {
                    "hook_event_name": "Stop",
                    "session_id": "codex-session-1",
                    "model": "gpt-5.4",
                    "transcript_path": str(transcript),
                },
                SimpleNamespace(id="agent-1"),
            )

        self.assertIsNotNone(event)
        self.assertEqual(
            event.data["provider_usage"],
            {
                "five_hour": {
                    "available": True,
                    "used_percentage": 64,
                    "resets_at": "2026-05-26T05:00:00Z",
                },
                "seven_day": {
                    "available": False,
                    "used_percentage": None,
                    "resets_at": None,
                },
            },
        )

    def test_codex_context_window_transcript_parse_is_defensive(self):
        event = CodexAdapter().parse_event(
            {
                "hook_event_name": "SessionStart",
                "session_id": "codex-session-1",
                "model": "gpt-5.4",
                "transcript_path": "/path/that/does/not/exist.jsonl",
            },
            SimpleNamespace(id="agent-1"),
        )

        self.assertIsNotNone(event)
        self.assertEqual(event.event_type, "session_start")
        self.assertNotIn("context_window", event.data)

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

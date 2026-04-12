import asyncio
import importlib
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub


class LocalPtyAdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub()
        self.state_mod = importlib.import_module("loom.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.pty_mod = importlib.import_module("loom.local_pty")
        self.pty_mod = importlib.reload(self.pty_mod)

    def test_capabilities_expose_embedded_terminal_without_toolbelt_registration(self):
        self.assertTrue(self.pty_mod.LocalPtyAdapter.capabilities.supports_embedded_terminal)
        self.assertTrue(self.pty_mod.LocalPtyAdapter.capabilities.supports_focus_tracking)
        self.assertFalse(self.pty_mod.LocalPtyAdapter.capabilities.supports_toolbelt_registration)

    async def test_create_session_emits_output_and_tracks_focus(self):
        state = self.state_mod.MatrixState()
        state.add_group("Loom")
        cell = state.add_terminal(
            name="Terminal 1",
            group="Loom",
            terminal_backend="pty",
            command="printf 'hello-loom\\n'",
        )
        adapter = self.pty_mod.LocalPtyAdapter(state)
        seen = asyncio.Future()

        async def on_output(cell_id, session_id, text):
            if (
                cell_id == cell.id
                and "hello-loom" in text
                and not seen.done()
            ):
                seen.set_result((cell_id, session_id, text))

        adapter.on_terminal_output = on_output
        await adapter.start()
        await adapter.create_session(cell)

        result = await asyncio.wait_for(seen, timeout=4)

        self.assertEqual(result[0], cell.id)
        self.assertEqual(state.active_session_id, cell.session_id)
        self.assertEqual(state.current_window_id, "standalone")
        self.assertIsNotNone(cell.session_id)

        await adapter.close_session(cell.session_id)

    async def test_write_input_accepts_raw_terminal_bytes(self):
        state = self.state_mod.MatrixState()
        state.add_group("Loom")
        with tempfile.TemporaryDirectory() as tmpdir:
            cell = state.add_terminal(
                name="Terminal 1",
                group="Loom",
                terminal_backend="pty",
                directory=tmpdir,
            )
            adapter = self.pty_mod.LocalPtyAdapter(state)
            seen = asyncio.Future()

            async def on_output(cell_id, session_id, text):
                if (
                    cell_id == cell.id
                    and "raw-loom" in text
                    and not seen.done()
                ):
                    seen.set_result((cell_id, session_id, text))

            adapter.on_terminal_output = on_output
            await adapter.start()
            await adapter.create_session(cell)
            self.assertIsNotNone(cell.session_id)

            await adapter.write_input(
                cell.session_id,
                "printf 'raw-loom\\n'\r",
            )
            result = await asyncio.wait_for(seen, timeout=4)

            self.assertEqual(result[0], cell.id)
            self.assertIn("raw-loom", result[2])

            await adapter.close_session(cell.session_id)

    async def test_shutdown_closes_live_sessions(self):
        state = self.state_mod.MatrixState()
        state.add_group("Loom")
        cell = state.add_terminal(
            name="Terminal 1",
            group="Loom",
            terminal_backend="pty",
            command="sleep 30",
        )
        adapter = self.pty_mod.LocalPtyAdapter(state)

        await adapter.start()
        await adapter.create_session(cell)

        self.assertIsNotNone(cell.session_id)
        self.assertEqual(cell.status, "running")

        await adapter.shutdown()

        self.assertIsNone(cell.session_id)
        self.assertEqual(cell.status, "stopped")
        self.assertEqual(adapter._sessions, {})

    async def test_send_text_waits_for_input_ready_signal_for_hook_based_agent(self):
        state = self.state_mod.MatrixState()
        state.add_group("Loom")
        cell = state.add_agent(
            name="Weaver",
            group="Loom",
            terminal_backend="pty",
            command="claude",
            directory="/tmp",
        )
        cell.agent_type = "claude-code"
        cell.session_id = "session-1"
        adapter = self.pty_mod.LocalPtyAdapter(state)
        adapter._sessions[cell.session_id] = SimpleNamespace(cell_id=cell.id)
        writes: list[tuple[str, str]] = []

        async def fake_write_input(session_id, data):
            writes.append((session_id, data))

        adapter.write_input = fake_write_input

        send_task = asyncio.create_task(
            adapter.send_text(cell.session_id, "Initial prompt")
        )
        await asyncio.sleep(0.05)
        self.assertEqual(writes, [])

        adapter.signal_input_ready(cell.id)
        await asyncio.wait_for(send_task, timeout=1)

        self.assertEqual(
            writes,
            [
                ("session-1", "Initial prompt"),
                ("session-1", "\r"),
            ],
        )

    async def test_send_text_claude_multiline_uses_newline_shortcut_then_submit(self):
        state = self.state_mod.MatrixState()
        state.add_group("Loom")
        cell = state.add_agent(
            name="Weaver",
            group="Loom",
            terminal_backend="pty",
            command="claude",
            directory="/tmp",
        )
        cell.agent_type = "claude-code"
        cell.session_id = "session-1"
        adapter = self.pty_mod.LocalPtyAdapter(state)
        adapter._sessions[cell.session_id] = SimpleNamespace(cell_id=cell.id)
        writes: list[tuple[str, str]] = []

        async def fake_write_input(session_id, data):
            writes.append((session_id, data))

        adapter.write_input = fake_write_input
        adapter._input_ready_sessions.add(cell.session_id)

        await adapter.send_text(cell.session_id, "line one\nline two")

        self.assertEqual(
            writes,
            [
                ("session-1", "line one"),
                ("session-1", "\n"),
                ("session-1", "line two"),
                ("session-1", "\r"),
            ],
        )

    async def test_send_text_single_line_waits_before_submit(self):
        state = self.state_mod.MatrixState()
        state.add_group("Loom")
        cell = state.add_agent(
            name="Weaver",
            group="Loom",
            terminal_backend="pty",
            command="codex",
            directory="/tmp",
        )
        cell.agent_type = "codex"
        cell.session_id = "session-1"
        adapter = self.pty_mod.LocalPtyAdapter(state)
        adapter._sessions[cell.session_id] = SimpleNamespace(cell_id=cell.id)
        adapter._input_ready_sessions.add(cell.session_id)
        writes: list[tuple[str, str]] = []

        async def fake_write_input(session_id, data):
            writes.append((session_id, data))

        delays = []

        async def fake_sleep(delay):
            delays.append(delay)

        adapter.write_input = fake_write_input
        orig_sleep = self.pty_mod.asyncio.sleep
        self.pty_mod.asyncio.sleep = fake_sleep
        try:
            await adapter.send_text(cell.session_id, "Initial prompt")
        finally:
            self.pty_mod.asyncio.sleep = orig_sleep

        self.assertEqual(
            writes,
            [
                ("session-1", "Initial prompt"),
                ("session-1", "\r"),
            ],
        )
        self.assertEqual(delays, [0.3])

    async def test_send_text_waits_for_codex_ready_screen_once_in_standalone(self):
        state = self.state_mod.MatrixState()
        state.add_group("Loom")
        cell = state.add_agent(
            name="Weaver",
            group="Loom",
            terminal_backend="pty",
            command="codex",
            directory="/tmp",
        )
        cell.agent_type = "codex"
        cell.session_id = "session-2"
        adapter = self.pty_mod.LocalPtyAdapter(state)
        adapter._sessions[cell.session_id] = SimpleNamespace(
            cell_id=cell.id,
            session_id=cell.session_id,
        )
        writes: list[tuple[str, str]] = []
        screen_reads = 0
        ready_screen = "\n".join([
            "OpenAI Codex",
            "model: gpt-5.4 high",
            "directory: ~/repo",
            "› Ready",
        ])
        screen_snapshots = [
            "Loading Codex...",
            ready_screen,
            ready_screen,
        ]

        async def fake_write_input(session_id, data):
            writes.append((session_id, data))

        async def fake_read_screen_text(session):
            nonlocal screen_reads
            idx = min(screen_reads, len(screen_snapshots) - 1)
            screen_reads += 1
            return screen_snapshots[idx]

        delays = []

        async def fake_sleep(delay):
            delays.append(delay)

        adapter.write_input = fake_write_input
        adapter._read_screen_text = fake_read_screen_text
        orig_sleep = self.pty_mod.asyncio.sleep
        self.pty_mod.asyncio.sleep = fake_sleep
        try:
            await adapter.send_text(cell.session_id, "line one\nline two")
            first_reads = screen_reads
            await adapter.send_text(cell.session_id, "follow up")
        finally:
            self.pty_mod.asyncio.sleep = orig_sleep

        self.assertEqual(
            writes,
            [
                ("session-2", "line one\nline two"),
                ("session-2", "\r"),
                ("session-2", "follow up"),
                ("session-2", "\r"),
            ],
        )
        self.assertEqual(first_reads, 3)
        self.assertEqual(screen_reads, first_reads)
        self.assertEqual(delays, [0.25, 0.25, 0.3, 0.3])

    async def test_send_text_claude_applies_post_ready_delay_after_hook_signal(self):
        state = self.state_mod.MatrixState()
        state.add_group("Loom")
        cell = state.add_agent(
            name="Weaver",
            group="Loom",
            terminal_backend="pty",
            command="claude",
            directory="/tmp",
        )
        cell.agent_type = "claude-code"
        cell.session_id = "session-claude"
        adapter = self.pty_mod.LocalPtyAdapter(state)
        adapter._sessions[cell.session_id] = SimpleNamespace(
            cell_id=cell.id,
            session_id=cell.session_id,
        )
        writes: list[tuple[str, str]] = []

        async def fake_write_input(session_id, data):
            writes.append((session_id, data))

        delays = []

        async def fake_sleep(delay):
            delays.append(delay)

        adapter.write_input = fake_write_input
        adapter.signal_input_ready(cell.id)
        orig_sleep = self.pty_mod.asyncio.sleep
        self.pty_mod.asyncio.sleep = fake_sleep
        try:
            await adapter.send_text(cell.session_id, "Initial prompt")
        finally:
            self.pty_mod.asyncio.sleep = orig_sleep

        self.assertEqual(
            writes,
            [
                ("session-claude", "Initial prompt"),
                ("session-claude", "\r"),
            ],
        )
        self.assertEqual(delays, [0.5, 0.3])

    async def test_create_session_installs_hooks_in_resolved_cwd_when_directory_blank(self):
        state = self.state_mod.MatrixState()
        state.add_group("Loom")
        with tempfile.TemporaryDirectory() as tmpdir:
            cell = state.add_agent(
                name="Weaver",
                group="Loom",
                terminal_backend="pty",
                command="",
                directory="",
            )
            cell.agent_type = "claude-code"
            adapter = self.pty_mod.LocalPtyAdapter(state)
            prev_cwd = os.getcwd()

            try:
                os.chdir(tmpdir)
                with mock.patch.dict(os.environ, {"LOOM_PORT": "18933"}, clear=False):
                    await adapter.start()
                    await adapter.create_session(cell)
                resolved_tmpdir = os.path.realpath(tmpdir)
                settings_file = Path(resolved_tmpdir) / ".claude" / "settings.local.json"
                self.assertEqual(os.path.realpath(cell.directory), resolved_tmpdir)
                self.assertTrue(settings_file.exists())
                self.assertIn("http://localhost:18933/events", settings_file.read_text())
            finally:
                os.chdir(prev_cwd)
                if cell.session_id:
                    await adapter.close_session(cell.session_id)

    def test_session_environment_scrubs_iterm_markers_and_sets_standalone_defaults(self):
        state = self.state_mod.MatrixState()
        adapter = self.pty_mod.LocalPtyAdapter(state)

        with mock.patch.dict(
            os.environ,
            {
                "ITERM_SESSION_ID": "w1t0p0:deadbeef",
                "ITERM_PROFILE": "Default",
                "LC_TERMINAL": "iTerm2",
                "LC_TERMINAL_VERSION": "3.6.7",
                "TERM_SESSION_ID": "session-123",
                "TERM_FEATURES": "24bitcc",
                "TERMINFO_DIRS": "/Applications/iTerm.app/Contents/Resources/terminfo",
                "TERM_PROGRAM": "iTerm.app",
                "TERM_PROGRAM_VERSION": "3.6.7",
                "TERM": "dumb",
                "STARSHIP_SESSION_KEY": "starship-session",
                "STARSHIP_SHELL": "zsh",
            },
            clear=False,
        ):
            env = adapter._session_environment(
                "cell-123",
                {"CUSTOM_PATH": "~/loom-test"},
            )

        self.assertEqual(env["LOOM_CELL_ID"], "cell-123")
        self.assertEqual(env["LOOM_STANDALONE_PTY"], "1")
        self.assertEqual(env["TERM"], "xterm-256color")
        self.assertEqual(env["COLORTERM"], "truecolor")
        self.assertEqual(env["CLAUDE_GATEWAY_NO_AUTO_UPDATE"], "true")
        self.assertEqual(env["DISABLE_AUTOUPDATER"], "1")
        self.assertEqual(env["CUSTOM_PATH"], os.path.expanduser("~/loom-test"))
        self.assertNotIn("ITERM_SESSION_ID", env)
        self.assertNotIn("ITERM_PROFILE", env)
        self.assertNotIn("LC_TERMINAL", env)
        self.assertNotIn("LC_TERMINAL_VERSION", env)
        self.assertNotIn("TERM_SESSION_ID", env)
        self.assertNotIn("TERM_FEATURES", env)
        self.assertNotIn("TERMINFO_DIRS", env)
        self.assertNotIn("TERM_PROGRAM", env)
        self.assertNotIn("TERM_PROGRAM_VERSION", env)
        self.assertNotIn("STARSHIP_SESSION_KEY", env)
        self.assertNotIn("STARSHIP_SHELL", env)

    def test_prepare_claude_config_overlay_cleans_upgrade_banner_without_mutating_real_config(self):
        state = self.state_mod.MatrixState()
        adapter = self.pty_mod.LocalPtyAdapter(state)

        with tempfile.TemporaryDirectory() as home_dir:
            home_path = Path(home_dir)
            config_path = home_path / ".claude"
            config_path.mkdir()
            settings_path = config_path / "settings.json"
            settings_path.write_text(
                json.dumps(
                    {
                        "model": "opus",
                        "companyAnnouncements": [
                            "⚠️  CLAUDE GATEWAY UPDATE AVAILABLE ⚠️\n   Current: v1.7.8 → Latest: v2.1.1\n   Run: npm update -g claude-gateway-helper",
                            "Keep shipping.",
                        ],
                    }
                ),
                encoding="utf-8",
            )
            history_path = config_path / "history.jsonl"
            history_path.write_text("{}", encoding="utf-8")
            home_level_config = home_path / ".claude.json"
            home_level_config.write_text("{\"theme\":\"dark\"}", encoding="utf-8")

            env = {"CLAUDE_CONFIG_DIR": str(config_path)}
            with mock.patch.object(Path, "home", return_value=home_path):
                overlay_dir = adapter._prepare_claude_config_overlay(env)
                self.addCleanup(shutil.rmtree, overlay_dir, ignore_errors=True)

                self.assertTrue(overlay_dir)
                self.assertEqual(env["CLAUDE_CONFIG_DIR"], overlay_dir)
                overlay_settings = json.loads(
                    (Path(overlay_dir) / "settings.json").read_text(encoding="utf-8")
                )
                self.assertEqual(overlay_settings["companyAnnouncements"], ["Keep shipping."])
                self.assertTrue((Path(overlay_dir) / "history.jsonl").is_symlink())
                self.assertTrue((Path(overlay_dir) / ".claude.json").is_symlink())
                self.assertEqual(
                    settings_path.read_text(encoding="utf-8"),
                    json.dumps(
                        {
                            "model": "opus",
                            "companyAnnouncements": [
                                "⚠️  CLAUDE GATEWAY UPDATE AVAILABLE ⚠️\n   Current: v1.7.8 → Latest: v2.1.1\n   Run: npm update -g claude-gateway-helper",
                                "Keep shipping.",
                            ],
                        }
                    ),
                )

    def test_prepare_zsh_bootstrap_wraps_original_profiles_and_installs_precmd_hook(self):
        state = self.state_mod.MatrixState()
        adapter = self.pty_mod.LocalPtyAdapter(state)

        with tempfile.TemporaryDirectory() as zdotdir:
            env = {"ZDOTDIR": zdotdir}
            bootstrap_dir = adapter._prepare_zsh_bootstrap(env)
            self.addCleanup(shutil.rmtree, bootstrap_dir, ignore_errors=True)

            self.assertEqual(env["ZDOTDIR"], bootstrap_dir)
            self.assertEqual(env["LOOM_ORIGINAL_ZDOTDIR"], zdotdir)

            zshrc = Path(bootstrap_dir) / ".zshrc"
            zshenv = Path(bootstrap_dir) / ".zshenv"
            self.assertTrue(zshrc.exists())
            self.assertTrue(zshenv.exists())
            zshrc_text = zshrc.read_text()
            self.assertIn('source "$ZDOTDIR/.zshrc"', zshrc_text)
            self.assertIn("add-zsh-hook precmd _loom_precmd", zshrc_text)
            self.assertIn("printf '\\033]7;file://%s%s\\007'", zshrc_text)

    def test_startup_commands_skip_typed_bootstrap_for_zsh_sessions(self):
        state = self.state_mod.MatrixState()
        state.add_group("Loom")
        cell = state.add_terminal(
            name="Terminal 1",
            group="Loom",
            terminal_backend="pty",
            directory="/tmp",
        )
        adapter = self.pty_mod.LocalPtyAdapter(state)

        zsh_commands = adapter._startup_commands(cell, shell_name="zsh", cwd="/tmp")
        bash_commands = adapter._startup_commands(cell, shell_name="bash", cwd="/tmp")

        self.assertEqual(zsh_commands, [])
        self.assertTrue(any("PROMPT_COMMAND=" in command for command in bash_commands))
        self.assertTrue(any("printf '\\033]7;file://" in command for command in bash_commands))

    async def test_read_screen_text_strips_ansi_sequences_for_readiness_checks(self):
        state = self.state_mod.MatrixState()
        adapter = self.pty_mod.LocalPtyAdapter(state)
        session = SimpleNamespace(
            buffer=(
                "\x1b[2m╭────────────────╮\x1b[0m\r\n"
                "\x1b[1mOpenAI Codex\x1b[0m\r\n"
                "\x1b[2mmodel:\x1b[0m gpt-5.4 high\r\n"
                "\x1b[2mdirectory:\x1b[0m ~/repo\r\n"
                "\x1b[1m›\x1b[0m Ready\r\n"
                "\x1b]0;title\x07"
            )
        )

        screen_text = await adapter._read_screen_text(session)

        self.assertIn("OpenAI Codex", screen_text)
        self.assertIn("model: gpt-5.4 high", screen_text)
        self.assertIn("directory: ~/repo", screen_text)
        self.assertIn("› Ready", screen_text)
        self.assertNotIn("\x1b", screen_text)


if __name__ == "__main__":
    unittest.main()

import importlib
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


class FakeProcess:
    def __init__(self):
        self.returncode = None
        self.terminated = False
        self.killed = False
        self.wait_timeouts = []

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def wait(self, timeout=None):
        self.wait_timeouts.append(timeout)
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9



def _install_test_stubs():
    if "iterm2" not in sys.modules:
        iterm2 = types.ModuleType("iterm2")
        iterm2.Connection = object
        sys.modules["iterm2"] = iterm2
    if "aiohttp" not in sys.modules:
        aiohttp = types.ModuleType("aiohttp")
        web = types.ModuleType("aiohttp.web")

        class ClientSession:
            async def close(self):
                return None

        class Application:
            def __init__(self):
                self.routes = []

            def add_routes(self, routes):
                self.routes.extend(routes)

        class AppRunner:
            def __init__(self, app):
                self.app = app

            async def setup(self):
                return None

            async def cleanup(self):
                return None

        class TCPSite:
            def __init__(self, runner, host, port):
                self.runner = runner
                self.host = host
                self.port = port

            async def start(self):
                return None

        def route(method, path, handler):
            return (method, path, handler)

        def json_response(payload):
            return payload

        aiohttp.ClientSession = ClientSession
        web.Application = Application
        web.AppRunner = AppRunner
        web.TCPSite = TCPSite
        web.Request = object
        web.Response = object
        web.get = lambda path, handler: route("GET", path, handler)
        web.post = lambda path, handler: route("POST", path, handler)
        web.json_response = json_response
        aiohttp.web = web
        sys.modules["aiohttp"] = aiohttp
        sys.modules["aiohttp.web"] = web


class RustBridgeRuntimeTests(unittest.TestCase):
    def setUp(self):
        _install_test_stubs()
        self.mod = importlib.import_module("loom.rust_bridge_runtime")
        self.mod = importlib.reload(self.mod)

    def test_resolve_settings_defaults_ignore_general_loom_runtime_env(self):
        with tempfile.TemporaryDirectory() as home_dir:
            settings = self.mod.resolve_rust_bridge_settings(
                env={
                    "HOME": home_dir,
                    "LOOM_PROFILE": "toolbelt-profile",
                    "LOOM_PORT": "18932",
                    "LOOM_DATA_DIR": "/tmp/toolbelt-runtime",
                },
                script_dir=Path("/repo"),
            )

        self.assertEqual(settings.launch_mode, self.mod.RUST_BRIDGE_MODE_SPAWN)
        self.assertEqual(settings.profile, self.mod.RUST_BRIDGE_DEFAULT_PROFILE)
        self.assertEqual(settings.rust_port, self.mod.RUST_BRIDGE_DEFAULT_PORT)
        self.assertEqual(
            settings.data_dir,
            Path(home_dir) / ".loom" / "profiles" / self.mod.RUST_BRIDGE_DEFAULT_PROFILE,
        )
        self.assertEqual(settings.rust_cwd, Path("/repo/rust"))

    def test_resolve_settings_prefers_rust_specific_env(self):
        settings = self.mod.resolve_rust_bridge_settings(
            env={
                "HOME": "/tmp/home",
                "LOOM_PROFILE": "toolbelt-profile",
                "LOOM_RUST_BRIDGE_MODE": "attach",
                "LOOM_RUST_PROFILE": "bridge-qa",
                "LOOM_RUST_PORT": "19044",
                "LOOM_RUST_BRIDGE_PORT": "19144",
                "LOOM_RUST_DATA_DIR": "/tmp/bridge-qa",
                "LOOM_RUST_CMD": "cargo run -p loom-app --features headless",
                "LOOM_BRIDGE_UI": "0",
            },
            script_dir=Path("/repo"),
        )

        self.assertEqual(settings.launch_mode, self.mod.RUST_BRIDGE_MODE_ATTACH)
        self.assertEqual(settings.profile, "bridge-qa")
        self.assertEqual(settings.rust_port, 19044)
        self.assertEqual(settings.bridge_port, 19144)
        self.assertEqual(settings.data_dir, Path("/tmp/bridge-qa"))
        self.assertFalse(settings.open_browser)

    def test_build_server_env_sets_bridge_contract(self):
        settings = self.mod.RustBridgeSettings(
            launch_mode=self.mod.RUST_BRIDGE_MODE_SPAWN,
            profile="bridge-dev",
            rust_port=19055,
            bridge_port=19155,
            data_dir=Path("/tmp/bridge-dev"),
            rust_command="cargo run -p loom-app --features headless",
            rust_cwd=Path("/repo/rust"),
            open_browser=True,
        )

        env = self.mod.build_rust_server_env(settings, base_env={"HOME": "/tmp/home"})

        self.assertEqual(env["LOOM_RUST_BRIDGE"], "1")
        self.assertEqual(env["LOOM_RUST_PROFILE"], "bridge-dev")
        self.assertEqual(env["LOOM_RUST_PORT"], "19055")
        self.assertEqual(env["LOOM_RUST_BRIDGE_PORT"], "19155")
        self.assertEqual(env["LOOM_TERMINAL_BACKEND"], "iterm2")
        self.assertEqual(env["LOOM_INSTALL_DIR"], "/tmp/bridge-dev")
        self.assertEqual(env["LOOM_TERMINAL_BRIDGE_URL"], "http://127.0.0.1:19155/")
        self.assertEqual(env["LOOM_PORT"], "19055")
        self.assertEqual(env["LOOM_DATA_DIR"], "/tmp/bridge-dev")

    def test_launcher_attach_reuses_matching_runtime(self):
        popen = mock.Mock()
        launcher = self.mod.RustBridgeLauncher(
            settings=self.mod.RustBridgeSettings(
                launch_mode=self.mod.RUST_BRIDGE_MODE_ATTACH,
                profile="rust-bridge",
                rust_port=18934,
                bridge_port=19034,
                data_dir=Path("/tmp/loom-rust"),
                rust_command="cargo run -p loom-app --features headless",
                rust_cwd=Path("/repo/rust"),
                open_browser=False,
            ),
            popen_factory=popen,
        )
        launcher.probe_runtime = lambda timeout=0.75: {
            "profile": "rust-bridge",
            "data_dir": "/tmp/loom-rust",
            "port": 18934,
        }

        runtime = launcher.ensure_rust_server()

        self.assertEqual(runtime["profile"], "rust-bridge")
        self.assertTrue(launcher._attached_to_existing)
        popen.assert_not_called()

    def test_launcher_attach_refuses_mismatched_runtime(self):
        launcher = self.mod.RustBridgeLauncher(
            settings=self.mod.RustBridgeSettings(
                launch_mode=self.mod.RUST_BRIDGE_MODE_ATTACH,
                profile="rust-bridge",
                rust_port=18934,
                bridge_port=19034,
                data_dir=Path("/tmp/loom-rust"),
                rust_command="cargo run -p loom-app --features headless",
                rust_cwd=Path("/repo/rust"),
                open_browser=False,
            ),
            popen_factory=mock.Mock(),
        )
        launcher.probe_runtime = lambda timeout=0.75: {
            "profile": "other",
            "data_dir": "/tmp/other",
        }

        with self.assertRaises(self.mod.RustBridgeError) as ctx:
            launcher.ensure_rust_server()

        self.assertIn("Refusing to attach", str(ctx.exception))

    def test_stop_rust_server_does_not_terminate_attached_runtime(self):
        process = FakeProcess()
        launcher = self.mod.RustBridgeLauncher(
            settings=self.mod.RustBridgeSettings(
                launch_mode=self.mod.RUST_BRIDGE_MODE_ATTACH,
                profile="rust-bridge",
                rust_port=18934,
                bridge_port=19034,
                data_dir=Path("/tmp/loom-rust"),
                rust_command="cargo run -p loom-app --features headless",
                rust_cwd=Path("/repo/rust"),
                open_browser=False,
            )
        )
        launcher._rust_process = process
        launcher._attached_to_existing = True

        launcher.stop_rust_server()

        self.assertFalse(process.terminated)
        self.assertFalse(process.killed)


class FakeCallbackClient:
    def __init__(self):
        self.focus_updates = []
        self.agent_sync_payloads = []

    async def post_focus_update(self, **payload):
        self.focus_updates.append(payload)

    async def post_agent_sync(self, payload):
        self.agent_sync_payloads.append(payload)


class RustBridgeCallbackStateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _install_test_stubs()
        self.mod = importlib.import_module("loom.rust_bridge_runtime")
        self.mod = importlib.reload(self.mod)

    async def test_broadcast_filters_agent_sync_to_terminal_owned_fields(self):
        client = FakeCallbackClient()
        state = self.mod.RustBridgeCallbackState(client)
        state._delta_ops = [
            {
                "op": "agent_upsert",
                "id": "agent-1",
                "session_id": "session-1",
                "window_id": "window-1",
                "status": "idle",
                "current_process": "codex",
                "current_path": "/tmp/repo",
                "current_branch": "main",
                "git_root": "/tmp/repo",
                "agent_type": "codex",
                "current_task_id": "task-1",
                "activity": "tool_call",
                "activity_detail": "Dispatching",
                "pending_weaver_message": True,
                "session_tokens_in": 42,
                "worktree_dirty": True,
            }
        ]

        await state.broadcast()

        self.assertEqual(
            client.agent_sync_payloads,
            [{
                "id": "agent-1",
                "session_id": "session-1",
                "window_id": "window-1",
                "status": "idle",
                "current_process": "codex",
                "current_path": "/tmp/repo",
                "current_branch": "main",
                "git_root": "/tmp/repo",
                "agent_type": "codex",
            }],
        )

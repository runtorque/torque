import importlib
import tempfile
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


class FakeWebview:
    def __init__(self):
        self.calls = []
        self.window = FakeWindow()

    def create_window(self, title, url, **kwargs):
        self.calls.append(("create_window", title, url, kwargs))
        return self.window

    def start(self, **kwargs):
        self.calls.append(("start", kwargs))


class FakeEvent:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self


class FakeWindowEvents:
    def __init__(self):
        self.closed = FakeEvent()
        self.closing = FakeEvent()


class FakeWindow:
    def __init__(self):
        self.events = FakeWindowEvents()


class DesktopLauncherTests(unittest.TestCase):
    def setUp(self):
        self.desktop_mod = importlib.import_module("loom.desktop")
        self.desktop_mod = importlib.reload(self.desktop_mod)

    def test_resolve_settings_defaults_to_desktop_profile_and_port(self):
        with tempfile.TemporaryDirectory() as home_dir:
            settings = self.desktop_mod.resolve_desktop_settings(
                env={"HOME": home_dir},
                script_dir=Path("/repo"),
            )

        self.assertEqual(settings.launch_mode, self.desktop_mod.DESKTOP_MODE_SPAWN)
        self.assertEqual(settings.profile, "desktop")
        self.assertEqual(settings.port, 18933)
        self.assertEqual(settings.url, "http://127.0.0.1:18933/")
        self.assertEqual(settings.script_path, Path("/repo/loom.py"))
        self.assertEqual(
            settings.data_dir,
            Path(home_dir) / ".loom" / "profiles" / "desktop",
        )

        env = self.desktop_mod.build_server_env(
            settings,
            base_env={"HOME": home_dir},
        )
        self.assertEqual(env["LOOM_DESKTOP_MODE"], "spawn")
        self.assertEqual(env["LOOM_DESKTOP_ATTACH"], "0")
        self.assertEqual(env["LOOM_STANDALONE"], "1")
        self.assertEqual(env["LOOM_PROFILE"], "desktop")
        self.assertEqual(env["LOOM_PORT"], "18933")
        self.assertEqual(env["LOOM_DATA_DIR"], str(settings.data_dir))
        self.assertEqual(env["LOOM_DESKTOP_SHELL"], "pywebview")

    def test_resolve_settings_honors_attach_mode(self):
        settings = self.desktop_mod.resolve_desktop_settings(
            env={
                "HOME": "/tmp/home",
                "LOOM_DESKTOP_MODE": "attach",
                "LOOM_DESKTOP_PROFILE": "desktop-dev",
            },
            script_dir=Path("/repo"),
        )

        self.assertEqual(settings.launch_mode, self.desktop_mod.DESKTOP_MODE_ATTACH)
        self.assertEqual(settings.profile, "desktop-dev")
        self.assertEqual(
            settings.data_dir,
            Path("/tmp/home/.loom/profiles/desktop-dev"),
        )

    def test_desktop_specific_env_overrides_general_loom_runtime_values(self):
        settings = self.desktop_mod.resolve_desktop_settings(
            env={
                "HOME": "/tmp/home",
                "LOOM_PROFILE": "toolbelt-profile",
                "LOOM_PORT": "18932",
                "LOOM_DATA_DIR": "/tmp/toolbelt-runtime",
                "LOOM_DESKTOP_PROFILE": "desktop-dev",
                "LOOM_DESKTOP_PORT": "19022",
                "LOOM_DESKTOP_DATA_DIR": "/tmp/desktop-runtime",
            },
            script_dir=Path("/repo"),
        )

        self.assertEqual(settings.profile, "desktop-dev")
        self.assertEqual(settings.port, 19022)
        self.assertEqual(settings.data_dir, Path("/tmp/desktop-runtime"))

    def test_run_spawns_server_opens_window_and_cleans_up_child(self):
        with tempfile.TemporaryDirectory() as home_dir:
            settings = self.desktop_mod.resolve_desktop_settings(
                env={"HOME": home_dir},
                script_dir=Path("/repo"),
            )

            fake_process = FakeProcess()
            popen_calls = []
            fake_webview = FakeWebview()
            probes = iter([None, {"standalone": True}])

            launcher = self.desktop_mod.DesktopLauncher(
                settings=settings,
                env={"HOME": home_dir},
                python_executable="/fake/python",
                popen_factory=lambda cmd, **kwargs: (
                    popen_calls.append((cmd, kwargs)) or fake_process
                ),
                sleep_fn=lambda _seconds: None,
            )
            launcher.probe_runtime = lambda timeout=0.75: next(probes, None)

            with mock.patch.object(self.desktop_mod, "_is_port_open", return_value=False):
                launcher.run(webview_module=fake_webview)

        self.assertEqual(
            popen_calls[0][0],
            ["/fake/python", "/repo/loom.py"],
        )
        self.assertEqual(
            popen_calls[0][1]["env"]["LOOM_DATA_DIR"],
            str(settings.data_dir),
        )
        self.assertEqual(
            fake_webview.calls[0][:3],
            ("create_window", "Loom", "http://127.0.0.1:18933/"),
        )
        self.assertEqual(len(fake_webview.window.events.closed.handlers), 1)
        self.assertEqual(len(fake_webview.window.events.closing.handlers), 1)
        self.assertEqual(fake_webview.calls[1], ("start", {"debug": False}))
        self.assertTrue(fake_process.terminated)
        self.assertEqual(fake_process.wait_timeouts, [5])

    def test_existing_toolbelt_instance_is_rejected(self):
        launcher = self.desktop_mod.DesktopLauncher(
            settings=self.desktop_mod.DesktopSettings(
                launch_mode="spawn",
                profile="desktop",
                port=18933,
                data_dir=Path("/tmp/loom-desktop"),
                script_path=Path("/repo/loom.py"),
            ),
            popen_factory=mock.Mock(),
        )
        launcher.probe_runtime = lambda timeout=0.75: {"standalone": False}

        with self.assertRaises(RuntimeError) as ctx:
            launcher.ensure_server()

        self.assertIn("iTerm2-hosted Loom instance", str(ctx.exception))
        launcher._popen_factory.assert_not_called()

    def test_existing_standalone_can_be_attached_when_opted_in(self):
        fake_webview = FakeWebview()
        launcher = self.desktop_mod.DesktopLauncher(
            settings=self.desktop_mod.DesktopSettings(
                launch_mode="attach",
                profile="desktop",
                port=18933,
                data_dir=Path("/tmp/loom-desktop"),
                script_path=Path("/repo/loom.py"),
            ),
            popen_factory=mock.Mock(),
        )
        launcher.probe_runtime = lambda timeout=0.75: {
            "standalone": True,
            "profile": "desktop",
            "data_dir": "/tmp/loom-desktop",
        }

        launcher.run(webview_module=fake_webview)

        self.assertEqual(
            fake_webview.calls[0][:3],
            ("create_window", "Loom", "http://127.0.0.1:18933/"),
        )
        launcher._popen_factory.assert_not_called()

    def test_attach_mode_requires_matching_runtime_target(self):
        launcher = self.desktop_mod.DesktopLauncher(
            settings=self.desktop_mod.DesktopSettings(
                launch_mode="attach",
                profile="desktop",
                port=18933,
                data_dir=Path("/tmp/loom-desktop"),
                script_path=Path("/repo/loom.py"),
            ),
            popen_factory=mock.Mock(),
        )
        launcher.probe_runtime = lambda timeout=0.75: {
            "standalone": True,
            "profile": "other",
            "data_dir": "/tmp/other",
        }

        with self.assertRaises(RuntimeError) as ctx:
            launcher.ensure_server()

        self.assertIn("Refusing to attach", str(ctx.exception))
        launcher._popen_factory.assert_not_called()

    def test_attach_mode_errors_when_no_server_is_running(self):
        launcher = self.desktop_mod.DesktopLauncher(
            settings=self.desktop_mod.DesktopSettings(
                launch_mode="attach",
                profile="desktop",
                port=18933,
                data_dir=Path("/tmp/loom-desktop"),
                script_path=Path("/repo/loom.py"),
            ),
            popen_factory=mock.Mock(),
        )
        launcher.probe_runtime = lambda timeout=0.75: None

        with self.assertRaises(RuntimeError) as ctx:
            launcher.ensure_server()

        self.assertIn("No standalone Loom server is listening", str(ctx.exception))
        launcher._popen_factory.assert_not_called()

    def test_spawn_mode_rejects_existing_matching_server_until_attach_requested(self):
        launcher = self.desktop_mod.DesktopLauncher(
            settings=self.desktop_mod.DesktopSettings(
                launch_mode="spawn",
                profile="desktop",
                port=18933,
                data_dir=Path("/tmp/loom-desktop"),
                script_path=Path("/repo/loom.py"),
            ),
            popen_factory=mock.Mock(),
        )
        launcher.probe_runtime = lambda timeout=0.75: {
            "standalone": True,
            "profile": "desktop",
            "data_dir": "/tmp/loom-desktop",
        }

        with self.assertRaises(RuntimeError) as ctx:
            launcher.ensure_server()

        self.assertIn("loom desktop --attach", str(ctx.exception))
        launcher._popen_factory.assert_not_called()

    def test_window_close_handler_stops_desktop_owned_child_server(self):
        fake_process = FakeProcess()
        launcher = self.desktop_mod.DesktopLauncher(
            settings=self.desktop_mod.DesktopSettings(
                launch_mode="spawn",
                profile="desktop",
                port=18933,
                data_dir=Path("/tmp/loom-desktop"),
                script_path=Path("/repo/loom.py"),
            ),
            popen_factory=mock.Mock(),
        )
        launcher._server_process = fake_process
        window = FakeWindow()

        class BindOnlyWebview:
            def create_window(self, *args, **kwargs):
                return window

        launcher.create_window(BindOnlyWebview())
        self.assertEqual(len(window.events.closed.handlers), 1)

        window.events.closed.handlers[0]()
        self.assertTrue(fake_process.terminated)

    def test_missing_pywebview_has_helpful_error(self):
        with self.assertRaises(RuntimeError) as ctx:
            self.desktop_mod.load_pywebview(
                import_module=lambda _name: (_ for _ in ()).throw(
                    ModuleNotFoundError("No module named 'webview'")
                )
            )

        self.assertIn("make desktop-deps", str(ctx.exception))
        self.assertIn("--python", str(ctx.exception))

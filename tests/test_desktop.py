import importlib
import tempfile
import unittest
import urllib.error
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
        self.desktop_mod = importlib.import_module("torque.desktop")
        self.desktop_mod = importlib.reload(self.desktop_mod)

    def test_patch_pywebview_cocoa_first_mouse_overrides_host_once(self):
        class FakeWebKitHost:
            pass

        class FakeBrowserView:
            WebKitHost = FakeWebKitHost

        class FakeCocoa:
            BrowserView = FakeBrowserView

        def fake_import(name):
            if name == "webview.platforms.cocoa":
                return FakeCocoa
            raise ModuleNotFoundError(name)

        patched = self.desktop_mod._patch_pywebview_cocoa_first_mouse(
            import_module=fake_import
        )
        self.assertTrue(patched)
        self.assertTrue(
            FakeWebKitHost.acceptsFirstMouse_(object(), None)
        )
        self.assertTrue(
            getattr(FakeWebKitHost, "_torque_accepts_first_mouse_patched")
        )

        patched_again = self.desktop_mod._patch_pywebview_cocoa_first_mouse(
            import_module=fake_import
        )
        self.assertTrue(patched_again)

    def test_patch_pywebview_cocoa_first_mouse_ignores_missing_backend(self):
        patched = self.desktop_mod._patch_pywebview_cocoa_first_mouse(
            import_module=lambda _name: (_ for _ in ()).throw(
                ModuleNotFoundError("webview.platforms.cocoa")
            )
        )
        self.assertFalse(patched)

    def test_resolve_settings_defaults_to_shared_default_profile_and_desktop_port(self):
        with tempfile.TemporaryDirectory() as home_dir:
            settings = self.desktop_mod.resolve_desktop_settings(
                env={"HOME": home_dir},
                script_dir=Path("/repo"),
            )

        self.assertEqual(settings.launch_mode, self.desktop_mod.DESKTOP_MODE_SPAWN)
        self.assertEqual(settings.profile, "default")
        self.assertEqual(settings.port, 18933)
        self.assertEqual(settings.url, "http://127.0.0.1:18933/")
        self.assertEqual(settings.script_path, Path("/repo/torque.py"))
        self.assertEqual(
            settings.data_dir,
            Path(home_dir) / ".torque" / "profiles" / "default",
        )

        env = self.desktop_mod.build_server_env(
            settings,
            base_env={"HOME": home_dir},
        )
        self.assertEqual(env["TORQUE_DESKTOP_MODE"], "spawn")
        self.assertEqual(env["TORQUE_DESKTOP_ATTACH"], "0")
        self.assertEqual(env["TORQUE_STANDALONE"], "1")
        self.assertEqual(env["TORQUE_PROFILE"], "default")
        self.assertEqual(env["TORQUE_PORT"], "18933")
        self.assertEqual(env["TORQUE_DATA_DIR"], str(settings.data_dir))

    def test_resolve_settings_honors_attach_mode(self):
        settings = self.desktop_mod.resolve_desktop_settings(
            env={
                "HOME": "/tmp/home",
                "TORQUE_DESKTOP_MODE": "attach",
                "TORQUE_DESKTOP_PROFILE": "desktop-dev",
            },
            script_dir=Path("/repo"),
        )

        self.assertEqual(settings.launch_mode, self.desktop_mod.DESKTOP_MODE_ATTACH)
        self.assertEqual(settings.profile, "desktop-dev")
        self.assertEqual(
            settings.data_dir,
            Path("/tmp/home/.torque/profiles/desktop-dev"),
        )

    def test_runtime_probe_uses_lightweight_endpoint(self):
        requests = []
        response_payload = (
            b'{"ok":true,"data":{"runtime":{"standalone":true,'
            b'"profile":"default","port":18933}}}'
        )

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return response_payload

        def fake_urlopen(request, timeout):
            requests.append((request, timeout))
            return FakeResponse()

        runtime = self.desktop_mod._probe_runtime_payload(
            18933,
            urlopen=fake_urlopen,
        )

        self.assertTrue(runtime["standalone"])
        self.assertEqual(runtime["profile"], "default")
        self.assertEqual(len(requests), 1)
        self.assertEqual(
            requests[0][0].full_url,
            "http://127.0.0.1:18933/api/runtime",
        )
        self.assertEqual(requests[0][0].get_method(), "GET")

    def test_runtime_probe_falls_back_for_older_daemon(self):
        requests = []
        response_payload = (
            b'{"ok":true,"data":{"runtime":{"standalone":true,'
            b'"profile":"default","port":18933}}}'
        )

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return response_payload

        def fake_urlopen(request, timeout):
            requests.append((request, timeout))
            if len(requests) == 1:
                raise urllib.error.HTTPError(
                    request.full_url,
                    404,
                    "Not Found",
                    {},
                    None,
                )
            return FakeResponse()

        runtime = self.desktop_mod._probe_runtime_payload(
            18933,
            urlopen=fake_urlopen,
        )

        self.assertTrue(runtime["standalone"])
        self.assertEqual(len(requests), 2)
        self.assertEqual(
            requests[1][0].full_url,
            "http://127.0.0.1:18933/api/cmd",
        )
        self.assertEqual(requests[1][0].get_method(), "POST")

    def test_desktop_specific_env_overrides_general_torque_runtime_values(self):
        settings = self.desktop_mod.resolve_desktop_settings(
            env={
                "HOME": "/tmp/home",
                "TORQUE_PROFILE": "toolbelt-profile",
                "TORQUE_PORT": "18932",
                "TORQUE_DATA_DIR": "/tmp/toolbelt-runtime",
                "TORQUE_DESKTOP_PROFILE": "desktop-dev",
                "TORQUE_DESKTOP_PORT": "19022",
                "TORQUE_DESKTOP_DATA_DIR": "/tmp/desktop-runtime",
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
                with mock.patch.object(
                    self.desktop_mod,
                    "_patch_pywebview_cocoa_first_mouse",
                ) as patch_first_mouse:
                    launcher.run(webview_module=fake_webview)
                    patch_first_mouse.assert_called_once_with()

        self.assertEqual(
            popen_calls[0][0],
            ["/fake/python", "/repo/torque.py"],
        )
        self.assertEqual(
            popen_calls[0][1]["env"]["TORQUE_DATA_DIR"],
            str(settings.data_dir),
        )
        self.assertEqual(
            fake_webview.calls[0][:3],
            ("create_window", "Torque", "http://127.0.0.1:18933/"),
        )
        self.assertEqual(len(fake_webview.window.events.closed.handlers), 1)
        self.assertEqual(len(fake_webview.window.events.closing.handlers), 1)
        self.assertEqual(fake_webview.calls[1], ("start", {"debug": False}))
        self.assertTrue(fake_process.terminated)
        self.assertEqual(fake_process.wait_timeouts, [5])

    def test_existing_non_standalone_instance_is_rejected(self):
        launcher = self.desktop_mod.DesktopLauncher(
            settings=self.desktop_mod.DesktopSettings(
                launch_mode="spawn",
                profile="desktop",
                port=18933,
                data_dir=Path("/tmp/torque-desktop"),
                script_path=Path("/repo/torque.py"),
            ),
            popen_factory=mock.Mock(),
        )
        launcher.probe_runtime = lambda timeout=0.75: {"standalone": False}

        with self.assertRaises(RuntimeError) as ctx:
            launcher.ensure_server()

        self.assertIn("unsupported Torque instance", str(ctx.exception))
        launcher._popen_factory.assert_not_called()

    def test_existing_standalone_can_be_attached_when_opted_in(self):
        fake_webview = FakeWebview()
        launcher = self.desktop_mod.DesktopLauncher(
            settings=self.desktop_mod.DesktopSettings(
                launch_mode="attach",
                profile="desktop",
                port=18933,
                data_dir=Path("/tmp/torque-desktop"),
                script_path=Path("/repo/torque.py"),
            ),
            popen_factory=mock.Mock(),
        )
        launcher.probe_runtime = lambda timeout=0.75: {
            "standalone": True,
            "profile": "desktop",
            "data_dir": "/tmp/torque-desktop",
        }

        launcher.run(webview_module=fake_webview)

        self.assertEqual(
            fake_webview.calls[0][:3],
            ("create_window", "Torque", "http://127.0.0.1:18933/"),
        )
        launcher._popen_factory.assert_not_called()

    def test_attach_mode_requires_matching_runtime_target(self):
        launcher = self.desktop_mod.DesktopLauncher(
            settings=self.desktop_mod.DesktopSettings(
                launch_mode="attach",
                profile="desktop",
                port=18933,
                data_dir=Path("/tmp/torque-desktop"),
                script_path=Path("/repo/torque.py"),
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
                data_dir=Path("/tmp/torque-desktop"),
                script_path=Path("/repo/torque.py"),
            ),
            popen_factory=mock.Mock(),
        )
        launcher.probe_runtime = lambda timeout=0.75: None

        with self.assertRaises(RuntimeError) as ctx:
            launcher.ensure_server()

        self.assertIn("No standalone Torque server is listening", str(ctx.exception))
        launcher._popen_factory.assert_not_called()

    def test_spawn_mode_rejects_existing_matching_server_until_attach_requested(self):
        launcher = self.desktop_mod.DesktopLauncher(
            settings=self.desktop_mod.DesktopSettings(
                launch_mode="spawn",
                profile="desktop",
                port=18933,
                data_dir=Path("/tmp/torque-desktop"),
                script_path=Path("/repo/torque.py"),
            ),
            popen_factory=mock.Mock(),
        )
        launcher.probe_runtime = lambda timeout=0.75: {
            "standalone": True,
            "profile": "desktop",
            "data_dir": "/tmp/torque-desktop",
        }

        with self.assertRaises(RuntimeError) as ctx:
            launcher.ensure_server()

        self.assertIn("torque desktop --attach", str(ctx.exception))
        launcher._popen_factory.assert_not_called()

    def test_window_close_handler_stops_desktop_owned_child_server(self):
        fake_process = FakeProcess()
        launcher = self.desktop_mod.DesktopLauncher(
            settings=self.desktop_mod.DesktopSettings(
                launch_mode="spawn",
                profile="desktop",
                port=18933,
                data_dir=Path("/tmp/torque-desktop"),
                script_path=Path("/repo/torque.py"),
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

        self.assertIn("make deps", str(ctx.exception))
        self.assertIn("make desktop-deps", str(ctx.exception))
        self.assertIn("--python", str(ctx.exception))

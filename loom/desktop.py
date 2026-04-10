"""Native desktop launcher for Loom using pywebview."""

from __future__ import annotations

import importlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .config import SCRIPT_DIR, log

DESKTOP_DEFAULT_PROFILE = "desktop"
DESKTOP_DEFAULT_PORT = 18933
WINDOW_TITLE = "Loom"
WINDOW_WIDTH = 1440
WINDOW_HEIGHT = 960
WINDOW_MIN_SIZE = (1024, 720)
STARTUP_TIMEOUT_SECS = 15.0
PROBE_TIMEOUT_SECS = 0.75
POLL_INTERVAL_SECS = 0.25


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def _slugify_profile(name: str) -> str:
    import re

    text = re.sub(r"[^A-Za-z0-9._-]+", "-", (name or "").strip().lower())
    text = text.strip(".-_")
    return text or "default"


@dataclass(frozen=True)
class DesktopSettings:
    profile: str
    port: int
    data_dir: Path
    script_path: Path

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"


def resolve_desktop_settings(*,
                             env: Mapping[str, str] | None = None,
                             script_dir: Path | None = None) -> DesktopSettings:
    env_map = dict(os.environ if env is None else env)
    profile = (env_map.get("LOOM_PROFILE", "") or "").strip() \
        or DESKTOP_DEFAULT_PROFILE

    port_text = (env_map.get("LOOM_PORT", "") or "").strip()
    port = int(port_text or DESKTOP_DEFAULT_PORT)

    data_dir_text = (env_map.get("LOOM_DATA_DIR", "") or "").strip()
    if data_dir_text:
        data_dir = Path(os.path.expanduser(data_dir_text))
    else:
        home_dir = Path(env_map.get("HOME") or str(Path.home()))
        data_dir = home_dir / ".loom" / "profiles" / _slugify_profile(profile)

    root = script_dir or SCRIPT_DIR
    return DesktopSettings(
        profile=profile,
        port=port,
        data_dir=data_dir,
        script_path=root / "loom.py",
    )


def build_server_env(settings: DesktopSettings,
                     *,
                     base_env: Mapping[str, str] | None = None) -> dict[str, str]:
    env_map = dict(os.environ if base_env is None else base_env)
    env_map["LOOM_STANDALONE"] = "1"
    env_map["LOOM_PROFILE"] = settings.profile
    env_map["LOOM_PORT"] = str(settings.port)
    env_map["LOOM_DATA_DIR"] = str(settings.data_dir)
    env_map.setdefault("LOOM_DESKTOP_SHELL", "pywebview")
    return env_map


def load_pywebview(import_module=importlib.import_module):
    try:
        return import_module("webview")
    except (ImportError, ModuleNotFoundError) as exc:
        raise RuntimeError(
            "pywebview is required for Loom desktop mode. "
            "Install it in the runtime you are using "
            "(for example: make desktop-deps)."
        ) from exc


def _probe_runtime_payload(port: int,
                           *,
                           timeout: float = PROBE_TIMEOUT_SECS,
                           urlopen=urllib.request.urlopen) -> dict | None:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/cmd",
        data=json.dumps({"cmd": "get_config"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
        payload = json.loads(raw)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None

    if not payload.get("ok"):
        return None
    data = payload.get("data") or {}
    runtime = data.get("runtime")
    return runtime if isinstance(runtime, dict) else None


def _is_port_open(port: int, *, host: str = "127.0.0.1",
                  timeout: float = 0.25) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


class DesktopLauncher:
    """Thin native shell around the existing standalone Loom server."""

    def __init__(self, *,
                 settings: DesktopSettings | None = None,
                 env: Mapping[str, str] | None = None,
                 script_dir: Path | None = None,
                 python_executable: str | None = None,
                 popen_factory=subprocess.Popen,
                 urlopen=urllib.request.urlopen,
                 sleep_fn=time.sleep,
                 monotonic=time.monotonic):
        self.settings = settings or resolve_desktop_settings(
            env=env,
            script_dir=script_dir,
        )
        self.env = build_server_env(self.settings, base_env=env)
        self.python_executable = python_executable or sys.executable
        self._popen_factory = popen_factory
        self._urlopen = urlopen
        self._sleep = sleep_fn
        self._monotonic = monotonic
        self._server_process = None
        self._attached_to_existing = False

    def probe_runtime(self, timeout: float = PROBE_TIMEOUT_SECS) -> dict | None:
        return _probe_runtime_payload(
            self.settings.port,
            timeout=timeout,
            urlopen=self._urlopen,
        )

    def ensure_server(self) -> dict:
        runtime = self.probe_runtime()
        if runtime:
            if not runtime.get("standalone"):
                raise RuntimeError(
                    f"Port {self.settings.port} is already serving an "
                    "iTerm2-hosted Loom instance. The desktop shell uses its "
                    "own standalone profile and port by default so it does not "
                    "attach to the live toolbelt daemon."
                )
            if _truthy(self.env.get("LOOM_DESKTOP_ATTACH")):
                self._attached_to_existing = True
                log.info(
                    "Attaching desktop shell to existing standalone Loom at %s",
                    self.settings.url,
                )
                return runtime
            raise RuntimeError(
                f"A standalone Loom server is already listening on "
                f"{self.settings.url}. Reuse it with LOOM_DESKTOP_ATTACH=1 or "
                "choose a different LOOM_PORT."
            )

        if _is_port_open(self.settings.port):
            raise RuntimeError(
                f"Port {self.settings.port} is already in use by another "
                "process. Set LOOM_PORT to a free port before launching the "
                "desktop shell."
            )

        self.settings.data_dir.mkdir(parents=True, exist_ok=True)
        log.info(
            "Launching standalone Loom server for desktop shell on %s "
            "(profile=%s, data_dir=%s)",
            self.settings.url,
            self.settings.profile,
            self.settings.data_dir,
        )
        self._server_process = self._popen_factory(
            [self.python_executable, str(self.settings.script_path)],
            cwd=str(self.settings.script_path.parent),
            env=self.env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        deadline = self._monotonic() + STARTUP_TIMEOUT_SECS
        while self._monotonic() < deadline:
            runtime = self.probe_runtime()
            if runtime:
                if not runtime.get("standalone"):
                    self.stop_server()
                    raise RuntimeError(
                        f"Port {self.settings.port} became reachable, but it "
                        "is not serving standalone Loom. Refusing to attach the "
                        "desktop shell to a non-standalone server."
                    )
                return runtime
            if self._server_process.poll() is not None:
                raise RuntimeError(
                    "The standalone Loom server exited before it was ready. "
                    f"Check {self.settings.data_dir / 'loom.log'} for details."
                )
            self._sleep(POLL_INTERVAL_SECS)

        self.stop_server()
        raise RuntimeError(
            "Timed out waiting for the standalone Loom server to start. "
            f"Check {self.settings.data_dir / 'loom.log'} for details."
        )

    def create_window(self, webview_module) -> None:
        log.info("Opening desktop window at %s", self.settings.url)
        webview_module.create_window(
            WINDOW_TITLE,
            self.settings.url,
            width=WINDOW_WIDTH,
            height=WINDOW_HEIGHT,
            min_size=WINDOW_MIN_SIZE,
        )

    def stop_server(self) -> None:
        proc = self._server_process
        self._server_process = None
        if not proc or self._attached_to_existing:
            return
        if proc.poll() is not None:
            return

        log.info("Stopping desktop-owned standalone Loom server")
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)

    def run(self, webview_module=None) -> None:
        webview = webview_module or load_pywebview()
        self.ensure_server()
        try:
            self.create_window(webview)
            webview.start(
                debug=_truthy(self.env.get("LOOM_DESKTOP_DEBUG")),
            )
        finally:
            self.stop_server()


def _install_signal_handlers():
    handled = []
    previous = {}

    def _raise_keyboard_interrupt(_signum, _frame):
        raise KeyboardInterrupt

    for sig_name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue
        handled.append(sig)
        previous[sig] = signal.getsignal(sig)
        signal.signal(sig, _raise_keyboard_interrupt)

    return handled, previous


def _restore_signal_handlers(handled, previous):
    for sig in handled:
        try:
            signal.signal(sig, previous[sig])
        except Exception:
            pass


def main() -> int:
    launcher = DesktopLauncher()
    handled, previous = _install_signal_handlers()
    try:
        launcher.run()
        return 0
    except KeyboardInterrupt:
        return 130
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        launcher.stop_server()
        _restore_signal_handlers(handled, previous)

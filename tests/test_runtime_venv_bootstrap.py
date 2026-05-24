import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]


def _load_bootstrap_module():
    path = ROOT / "scripts" / "bootstrap_runtime_venv.py"
    spec = importlib.util.spec_from_file_location("bootstrap_runtime_venv", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class RuntimeVenvBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.bootstrap = _load_bootstrap_module()

    def test_rejects_base_python_below_minimum_with_actionable_error(self):
        calls = []

        def fake_runner(cmd, **kwargs):
            calls.append(cmd)
            self.assertEqual(cmd[:2], ["/old/python3", "-c"])
            return SimpleNamespace(returncode=0, stdout="3.9.18\n", stderr="")

        with self.assertRaises(self.bootstrap.PythonTooOld) as ctx:
            self.bootstrap.ensure_runtime_venv(
                "/old/python3",
                Path("/tmp/unused-venv"),
                ROOT / "requirements" / "desktop.txt",
                runner=fake_runner,
            )

        self.assertIn("Python 3.10 or newer", str(ctx.exception))
        self.assertEqual(len(calls), 1)

    def test_creates_clean_venv_and_installs_desktop_requirements(self):
        commands = []
        with tempfile.TemporaryDirectory() as td:
            venv_dir = Path(td) / "venv"
            requirements = ROOT / "requirements" / "desktop.txt"

            def fake_runner(cmd, **kwargs):
                cmd = [str(part) for part in cmd]
                commands.append(cmd)
                if cmd[:2] == ["/base/python3", "-c"]:
                    return SimpleNamespace(returncode=0, stdout="3.11.8\n", stderr="")
                if cmd[:3] == ["/base/python3", "-m", "venv"]:
                    runtime_python = venv_dir / "bin" / "python"
                    runtime_python.parent.mkdir(parents=True, exist_ok=True)
                    runtime_python.write_text("#!/bin/sh\n", encoding="utf-8")
                    return SimpleNamespace(returncode=0, stdout="", stderr="")
                if cmd[:2] == [str(venv_dir / "bin" / "python"), "-c"]:
                    return SimpleNamespace(returncode=0, stdout="3.11.8\n", stderr="")
                if cmd[:4] == [str(venv_dir / "bin" / "python"), "-m", "ensurepip", "--upgrade"]:
                    return SimpleNamespace(returncode=0, stdout="", stderr="")
                if cmd[:4] == [str(venv_dir / "bin" / "python"), "-m", "pip", "install"]:
                    self.assertEqual(cmd[-2:], ["-r", str(requirements)])
                    return SimpleNamespace(returncode=0, stdout="", stderr="")
                self.fail(f"unexpected command: {cmd}")

            resolved = self.bootstrap.ensure_runtime_venv(
                "/base/python3",
                venv_dir,
                requirements,
                runner=fake_runner,
            )

        self.assertEqual(resolved, venv_dir / "bin" / "python")
        self.assertIn(["/base/python3", "-m", "venv", str(venv_dir)], commands)
        self.assertIn(
            [str(venv_dir / "bin" / "python"), "-m", "pip", "install", "-r", str(requirements)],
            commands,
        )

    def test_rebuilds_existing_invalid_runtime_venv(self):
        removed = []
        with tempfile.TemporaryDirectory() as td:
            venv_dir = Path(td) / "venv"
            runtime_python = venv_dir / "bin" / "python"
            runtime_python.parent.mkdir(parents=True)
            runtime_python.write_text("#!/bin/sh\n", encoding="utf-8")
            requirements = ROOT / "requirements" / "desktop.txt"

            def fake_runner(cmd, **kwargs):
                cmd = [str(part) for part in cmd]
                if cmd[:2] == ["/base/python3", "-c"]:
                    return SimpleNamespace(returncode=0, stdout="3.11.8\n", stderr="")
                if cmd[:2] == [str(runtime_python), "-c"] and not removed:
                    return SimpleNamespace(returncode=1, stdout="", stderr="broken")
                if cmd[:3] == ["/base/python3", "-m", "venv"]:
                    runtime_python.parent.mkdir(parents=True, exist_ok=True)
                    runtime_python.write_text("#!/bin/sh\n", encoding="utf-8")
                    return SimpleNamespace(returncode=0, stdout="", stderr="")
                if cmd[:2] == [str(runtime_python), "-c"]:
                    return SimpleNamespace(returncode=0, stdout="3.11.8\n", stderr="")
                if cmd[:4] == [str(runtime_python), "-m", "ensurepip", "--upgrade"]:
                    return SimpleNamespace(returncode=0, stdout="", stderr="")
                if cmd[:4] == [str(runtime_python), "-m", "pip", "install"]:
                    return SimpleNamespace(returncode=0, stdout="", stderr="")
                self.fail(f"unexpected command: {cmd}")

            def fake_remove(path):
                removed.append(Path(path))
                for child in sorted(Path(path).rglob("*"), reverse=True):
                    if child.is_file():
                        child.unlink()
                    else:
                        child.rmdir()
                Path(path).rmdir()

            self.bootstrap.ensure_runtime_venv(
                "/base/python3",
                venv_dir,
                requirements,
                runner=fake_runner,
                remove_tree=fake_remove,
            )

        self.assertEqual(removed, [venv_dir])


if __name__ == "__main__":
    unittest.main()

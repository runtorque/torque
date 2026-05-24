#!/usr/bin/env python3
"""Bootstrap Torque's owned Python runtime virtual environment."""

from __future__ import print_function

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

MIN_PYTHON = (3, 10)
REQUIRED_MODULES = (
    "aiohttp",
    "jinja2",
    "yaml",
    "orjson",
    "certifi",
    "cryptography",
    "webview",
)


class BootstrapError(RuntimeError):
    pass


class PythonTooOld(BootstrapError):
    pass


def _version_text(version):
    return ".".join(str(part) for part in version)


def _parse_version(text):
    parts = []
    for raw in (text or "").strip().split("."):
        try:
            parts.append(int(raw))
        except ValueError:
            break
    if len(parts) < 2:
        raise BootstrapError("Could not parse Python version output: %r" % text)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def _run_probe(cmd, runner=subprocess.run):
    try:
        return runner(
            [str(part) for part in cmd],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError as exc:
        raise BootstrapError(
            "Python executable not found: %s" % cmd[0]
        ) from exc
    except OSError as exc:
        raise BootstrapError(
            "Could not execute Python executable %s: %s" % (cmd[0], exc)
        ) from exc


def python_version(python, runner=subprocess.run):
    result = _run_probe(
        [python, "-c", "import sys; print('%d.%d.%d' % sys.version_info[:3])"],
        runner=runner,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise BootstrapError(
            "Could not inspect Python executable %s%s" % (
                python,
                (": " + detail) if detail else "",
            )
        )
    return _parse_version(result.stdout)


def ensure_min_python(python, label, runner=subprocess.run):
    version = python_version(python, runner=runner)
    if version < MIN_PYTHON:
        raise PythonTooOld(
            "%s requires Python %s or newer; found Python %s at %s." % (
                label,
                _version_text(MIN_PYTHON),
                _version_text(version),
                python,
            )
        )
    return version


def runtime_python_path(venv_dir):
    venv_dir = Path(venv_dir).expanduser()
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _run_command(cmd, runner=subprocess.run):
    try:
        result = runner(
            [str(part) for part in cmd],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError as exc:
        raise BootstrapError("Command not found: %s" % cmd[0]) from exc
    except OSError as exc:
        raise BootstrapError("Could not execute %s: %s" % (cmd[0], exc)) from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise BootstrapError(
            "Command failed (%s)%s" % (
                " ".join(str(part) for part in cmd),
                (":\n" + detail) if detail else "",
            )
        )
    return result


def create_venv(base_python, venv_dir, runner=subprocess.run):
    _run_command([base_python, "-m", "venv", str(venv_dir)], runner=runner)


def install_requirements(venv_python, requirements, runner=subprocess.run):
    _run_command([venv_python, "-m", "ensurepip", "--upgrade"], runner=runner)
    _run_command([venv_python, "-m", "pip", "install", "-r", str(requirements)], runner=runner)


def verify_required_modules(venv_python, modules=REQUIRED_MODULES, runner=subprocess.run):
    import_lines = ["import %s" % module for module in modules]
    code = "; ".join(import_lines)
    result = _run_probe([venv_python, "-c", code], runner=runner)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise BootstrapError(
            "Torque runtime at %s is missing required modules%s" % (
                venv_python,
                (": " + detail) if detail else ".",
            )
        )


def ensure_runtime_venv(base_python, venv_dir, requirements, runner=subprocess.run,
                        remove_tree=shutil.rmtree):
    venv_dir = Path(venv_dir).expanduser()
    requirements = Path(requirements).expanduser()
    if not requirements.is_file():
        raise BootstrapError("Requirements file not found: %s" % requirements)

    ensure_min_python(base_python, "Torque runtime bootstrap", runner=runner)
    venv_python = runtime_python_path(venv_dir)

    rebuild = False
    if venv_python.exists():
        try:
            ensure_min_python(venv_python, "Existing Torque runtime venv", runner=runner)
        except BootstrapError:
            rebuild = True
    else:
        rebuild = True

    if rebuild:
        if venv_dir.exists():
            remove_tree(str(venv_dir))
        venv_dir.parent.mkdir(parents=True, exist_ok=True)
        create_venv(base_python, venv_dir, runner=runner)
        if not venv_python.exists():
            raise BootstrapError(
                "python3 -m venv completed but did not create %s" % venv_python
            )
        ensure_min_python(venv_python, "New Torque runtime venv", runner=runner)

    install_requirements(venv_python, requirements, runner=runner)
    verify_required_modules(venv_python, runner=runner)
    return venv_python


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--venv",
        default=str(Path.home() / ".torque" / "runtime" / "venv"),
        help="Torque runtime venv path (default: ~/.torque/runtime/venv)",
    )
    parser.add_argument(
        "--requirements",
        default=str(Path(__file__).resolve().parents[1] / "requirements" / "desktop.txt"),
        help="Requirements file to install into the runtime venv",
    )
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="Base Python used to create the venv (default: current interpreter)",
    )
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        venv_python = ensure_runtime_venv(args.python, args.venv, args.requirements)
    except PythonTooOld as exc:
        print("Error: %s" % exc, file=sys.stderr)
        print(
            "Install Python %s+ and rerun `make deps`, or set "
            "TORQUE_BASE_PYTHON=/path/to/python3.10+." % _version_text(MIN_PYTHON),
            file=sys.stderr,
        )
        return 1
    except BootstrapError as exc:
        print("Error: %s" % exc, file=sys.stderr)
        print(
            "Install Python %s+ with the venv module available, then rerun "
            "`make deps`." % _version_text(MIN_PYTHON),
            file=sys.stderr,
        )
        return 1
    print("Torque runtime venv ready: %s" % venv_python)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

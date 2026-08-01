#!/usr/bin/env python3
"""Run a Makefile test command and publish an immutable result record.

This intentionally owns result publication rather than relying on the caller's
stdout capture: an external terminal may truncate or discard that stream.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import selectors
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime


MINIMUM_PYTHON = (3, 10)


def _git(*args: str) -> str | None:
    try:
        return subprocess.check_output(
            ["git", *args], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _interpreter_details(requested: str) -> dict[str, object]:
    executable = requested.split()[0] if requested.split() else requested
    resolved = shutil.which(executable) if not os.path.isabs(executable) else executable
    resolved = os.path.realpath(resolved) if resolved else None
    version = None
    version_tuple = None
    if resolved:
        try:
            version = subprocess.check_output(
                [resolved, "--version"], text=True, stderr=subprocess.STDOUT
            ).strip()
            match = re.search(r"(\d+)\.(\d+)", version)
            if match:
                version_tuple = (int(match.group(1)), int(match.group(2)))
        except (OSError, subprocess.CalledProcessError):
            pass
    minimum_satisfied = version_tuple >= MINIMUM_PYTHON if version_tuple else None
    return {
        "requested": requested,
        "path": resolved,
        "version": version,
        "minimum_version": ".".join(map(str, MINIMUM_PYTHON)),
        "minimum_satisfied": minimum_satisfied,
    }


def _read_totals(read_fd: int) -> dict[str, object]:
    """Read unittest's authoritative TestResult record, never stdout text."""
    os.set_blocking(read_fd, False)
    chunks = []
    while True:
        try:
            chunk = os.read(read_fd, 64 * 1024)
        except BlockingIOError:
            break
        if not chunk:
            break
        chunks.append(chunk)
    if not chunks:
        return {
            "source": "unavailable",
            "ran": None,
            "passed": None,
            "failed": None,
            "skipped": None,
            "errors": None,
        }
    try:
        return json.loads(b"".join(chunks).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {
            "source": "unavailable",
            "ran": None,
            "passed": None,
            "failed": None,
            "skipped": None,
            "errors": None,
        }


def _publish(path: Path, record: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=".test-result-", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(record, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        # Result records are append-only: no retention, cleanup, pointer, or
        # overwrite is permitted here. Growth policy is separate operator work.
        # link(2) is atomic no-replace publication; os.replace() would let a
        # later run overwrite evidence from an earlier run.
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True)
    parser.add_argument("--command-env", required=True)
    parser.add_argument("--interpreter", required=True)
    parser.add_argument("--result-dir", required=True)
    parser.add_argument("--output-truncated", choices=("true", "false", "unknown"), default="unknown")
    args = parser.parse_args()

    command = os.environ.get(args.command_env)
    if command is None:
        parser.error(f"environment variable {args.command_env} is not set")
    started = datetime.now(UTC)
    started_at = started.isoformat().replace("+00:00", "Z")
    sha = _git("rev-parse", "HEAD") or "unknown"
    dirty_status = _git("status", "--porcelain", "--untracked-files=normal")
    dirty: bool | None = None if dirty_status is None else bool(dirty_status)
    run_id = secrets.token_hex(8)
    stamp = started.strftime("%Y%m%dT%H%M%S.%fZ")
    footer = Path(args.result_dir) / f"{args.target}-{stamp}-{sha[:12]}-{run_id}.json"
    interpreter = _interpreter_details(args.interpreter)
    if interpreter["minimum_satisfied"] is False:
        print(
            f"warning: test interpreter {interpreter['path']} is below Python "
            f"{interpreter['minimum_version']}",
            file=sys.stderr,
        )

    started_monotonic = time.monotonic()
    read_fd, write_fd = os.pipe()
    suite_environment = os.environ.copy()
    suite_environment["TORQUE_TEST_RESULT_FD"] = str(write_fd)
    process = subprocess.Popen(
        command,
        shell=True,
        executable="/bin/sh",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=suite_environment,
        pass_fds=(write_fd,),
    )
    os.close(write_fd)
    assert process.stdout is not None and process.stderr is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, sys.stdout.buffer)
    selector.register(process.stderr, selectors.EVENT_READ, sys.stderr.buffer)
    while selector.get_map():
        for key, _ in selector.select():
            chunk = os.read(key.fileobj.fileno(), 64 * 1024)
            if not chunk:
                selector.unregister(key.fileobj)
                continue
            key.data.write(chunk)
            key.data.flush()
    selector.close()
    suite_rc = process.wait()
    totals = _read_totals(read_fd)
    os.close(read_fd)
    duration = time.monotonic() - started_monotonic
    record: dict[str, object] = {
        "schema_version": 1,
        "target": args.target,
        "command": command,
        "started_at": started_at,
        "sha": sha,
        "dirty": dirty,
        "exit_code": suite_rc,
        "totals": totals,
        "duration_seconds": duration,
        # An upstream terminal capture is outside this process; unknown is
        # safer than claiming that its stdout was not truncated.
        "output_truncated": args.output_truncated,
        "interpreter": interpreter,
        "run_id": run_id,
    }
    try:
        _publish(footer, record)
    except OSError as exc:
        print(f"error: could not publish test result footer: {exc}", file=sys.stderr)
        # A footer-write failure may fail green, but must never green red.
        return suite_rc if suite_rc != 0 else 1
    return suite_rc


if __name__ == "__main__":
    raise SystemExit(main())

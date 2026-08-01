#!/usr/bin/env python3
"""Prepare and finalize immutable Makefile test-result records.

The Makefile deliberately invokes the suite command itself.  This helper only
prepares invocation metadata and publishes the result after that command has
returned, preserving its process, terminal, and stream behaviour.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone


MINIMUM_PYTHON = (3, 10)


def _git(*args):
    try:
        return subprocess.check_output(
            ["git", *args], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _interpreter_details():
    # This helper is itself invoked through the exact TEST_PYTHON expansion
    # used by the recipe. sys.executable therefore handles quoted paths and
    # flags without attempting to re-parse shell syntax differently.
    requested = os.environ.get("TEST_PYTHON", "")
    resolved = os.path.realpath(sys.executable)
    version = sys.version.splitlines()[0]
    version_tuple = sys.version_info[:2]
    minimum_satisfied = version_tuple >= MINIMUM_PYTHON if version_tuple else None
    return {
        "requested": requested,
        "path": resolved,
        "version": version,
        "minimum_version": ".".join(map(str, MINIMUM_PYTHON)),
        "minimum_satisfied": minimum_satisfied,
    }


def _atomic_no_replace(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=".test-result-", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        # link(2) atomically refuses an existing name. Result records are
        # append-only: there is intentionally no pruning, pointer, rotation,
        # or overwrite here; retention is separate operator work.
        os.link(str(temporary), str(path))
    finally:
        temporary.unlink(missing_ok=True)


def prepare(args):
    command = os.environ.get(args.command_env)
    if command is None:
        raise ValueError("command environment variable is not set: %s" % args.command_env)
    started = datetime.now(timezone.utc)
    sha = _git("rev-parse", "HEAD") or "unknown"
    dirty_status = _git("status", "--porcelain", "--untracked-files=normal")
    run_id = secrets.token_hex(8)
    footer_name = "%s-%s-%s-%s.json" % (
        args.target,
        started.strftime("%Y%m%dT%H%M%S.%fZ"),
        sha[:12],
        run_id,
    )
    metadata = {
        "schema_version": 1,
        "target": args.target,
        "command": command,
        "started_at": started.isoformat().replace("+00:00", "Z"),
        "sha": sha,
        "dirty": None if dirty_status is None else bool(dirty_status),
        "interpreter": _interpreter_details(),
        "output_truncated": args.output_truncated,
        "run_id": run_id,
        "footer_path": str(Path(args.result_dir) / footer_name),
        "started_monotonic": time.monotonic(),
    }
    _atomic_no_replace(args.invocation, metadata)
    if metadata["interpreter"]["minimum_satisfied"] is False:
        print(
            "warning: test interpreter %s is below Python %s" % (
                metadata["interpreter"]["path"],
                metadata["interpreter"]["minimum_version"],
            ),
            file=sys.stderr,
        )


def finalize(args):
    try:
        metadata = json.loads(Path(args.invocation).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise ValueError("invocation metadata is missing or invalid")
    record = dict(metadata)
    record.pop("footer_path")
    started_monotonic = record.pop("started_monotonic")
    record["exit_code"] = args.exit_code
    record["duration_seconds"] = time.monotonic() - started_monotonic
    # The direct-shell design intentionally does not copy, pipe, or parse
    # stdout. There is therefore no reliable stream from which to derive
    # counts; retain an explicit null rather than inventing an outcome.
    record["totals"] = None
    record["totals_source"] = "stdout"
    record["totals_parse_status"] = "unavailable"
    record["totals_parse_reason"] = (
        "obtaining counts would require interposing via a wrapper, pipe, or "
        "redirection; those are forbidden because they change process, TTY, "
        "and stream semantics"
    )
    _atomic_no_replace(metadata["footer_path"], record)
    print("Test result footer: %s" % metadata["footer_path"])


def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="operation", required=True)
    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--target", required=True)
    prepare_parser.add_argument("--command-env", required=True)
    prepare_parser.add_argument("--result-dir", required=True)
    prepare_parser.add_argument("--invocation", required=True)
    prepare_parser.add_argument("--output-truncated", choices=("true", "false", "unknown"), default="unknown")
    finalize_parser = subparsers.add_parser("finalize")
    finalize_parser.add_argument("--invocation", required=True)
    finalize_parser.add_argument("--exit-code", required=True, type=int)
    args = parser.parse_args()
    try:
        if args.operation == "prepare":
            prepare(args)
        else:
            finalize(args)
    except (OSError, ValueError) as exc:
        print("error: test result footer: %s" % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

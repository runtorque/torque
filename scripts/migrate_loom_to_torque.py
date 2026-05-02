#!/usr/bin/env python3
"""Migrate ~/.loom data to ~/.torque after the Loom → Torque rename.

One-shot. Idempotent (won't overwrite ~/.torque if it exists).
Run once per machine before launching Torque for the first time.

What it does:
  1. Backup ~/.loom to ~/.loom.pre-torque.bak (full copy).
  2. Move ~/.loom to ~/.torque.
  3. Rename loom.db / loom.db-shm / loom.db-wal / loom.log / loom.db.pre-kinds.bak
     and the same files inside profiles/<name>/.
  4. Rewrite SQLite text content in torque.db (and per-profile DBs and
     event_ingest.db): replace 'loom' / 'Loom' / 'LOOM' with the corresponding
     case-matched 'torque' / 'Torque' / 'TORQUE' across every TEXT column.
  5. Clear stale agents.worktree_branch values starting with 'loom/' (the old
     git branches were deleted as part of the rename; agents will get fresh
     worktrees on next dispatch).
  6. Drop legacy iTerm2 global key bindings prefixed loom_ (Torque registers
     torque_* RPCs on startup; stale loom_* entries in iTerm2's global key
     bindings would point at non-existent RPCs).

To recover, restore from ~/.loom.pre-torque.bak.
"""

from __future__ import annotations

import json
import os
import plistlib
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

OLD = Path.home() / ".loom"
NEW = Path.home() / ".torque"
BACKUP = Path.home() / ".loom.pre-torque.bak"

ITERM2_PREFS = (
    Path.home()
    / "Library"
    / "Preferences"
    / "com.googlecode.iterm2.plist"
)


def _rename_loom_files(directory: Path) -> None:
    """Rename any loom.* file in directory to torque.*."""
    if not directory.is_dir():
        return
    for entry in list(directory.iterdir()):
        if entry.is_file() and entry.name.startswith("loom"):
            new_name = entry.name.replace("loom", "torque", 1)
            entry.rename(directory / new_name)


def _list_text_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    cols: list[str] = []
    for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall():
        col_name = row[1]
        col_type = (row[2] or "").upper()
        # SQLite is dynamically typed; treat empty/TEXT/VARCHAR/CLOB as text-rewriteable.
        if col_type == "" or "TEXT" in col_type or "CHAR" in col_type or "CLOB" in col_type:
            cols.append(col_name)
    return cols


def _rewrite_db(db_path: Path) -> None:
    if not db_path.exists():
        return
    print(f"  rewriting {db_path}")
    conn = sqlite3.connect(str(db_path))
    try:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        ]
        replacements = [
            ("LOOM", "TORQUE"),
            ("Loom", "Torque"),
            ("loom", "torque"),
        ]
        for table in tables:
            for col in _list_text_columns(conn, table):
                for old, new in replacements:
                    conn.execute(
                        f'UPDATE "{table}" SET "{col}" = REPLACE("{col}", ?, ?) '
                        f'WHERE "{col}" LIKE ?',
                        (old, new, f"%{old}%"),
                    )
        # Clear stale worktree_branch refs whose git branches were deleted.
        # Column may be NOT NULL in some schema versions, so use empty string.
        if "agents" in tables:
            conn.execute(
                "UPDATE agents SET worktree_branch = '' "
                "WHERE worktree_branch LIKE 'torque/%'"
            )
        conn.commit()
    finally:
        conn.close()


def _drop_legacy_iterm2_keybindings() -> None:
    """Remove any com.googlecode.iterm2 global key bindings whose action
    invokes a loom_* RPC. Torque registers torque_* RPCs on startup;
    leftover loom_* bindings would silently target a non-existent function."""
    if not ITERM2_PREFS.exists():
        return
    try:
        with ITERM2_PREFS.open("rb") as fh:
            prefs = plistlib.load(fh)
    except Exception as exc:
        print(f"  could not read iTerm2 prefs ({exc}); skipping keybinding cleanup")
        return

    bindings = prefs.get("GlobalKeyMap")
    if not isinstance(bindings, dict):
        return

    removed: list[str] = []
    for shortcut, value in list(bindings.items()):
        if not isinstance(value, dict):
            continue
        text = json.dumps(value)
        if "loom_" in text:
            del bindings[shortcut]
            removed.append(shortcut)

    if not removed:
        return

    print(f"  removing {len(removed)} legacy loom_* iTerm2 keybinding(s)")
    # Use defaults(1) so iTerm2's preferences daemon picks up the change.
    prefs_path = str(ITERM2_PREFS)
    for shortcut in removed:
        try:
            subprocess.run(
                [
                    "/usr/bin/defaults",
                    "delete",
                    "com.googlecode.iterm2",
                    "GlobalKeyMap",
                    shortcut,
                ],
                check=False,
                capture_output=True,
            )
        except Exception:
            pass


def main() -> int:
    sentinel = NEW / "MIGRATED_FROM_LOOM"
    resuming = NEW.exists() and BACKUP.exists() and not sentinel.exists()

    if not OLD.exists() and not resuming:
        print(f"No {OLD} found — nothing to migrate.")
        return 0
    if NEW.exists() and not resuming:
        print(
            f"ERROR: {NEW} already exists. Move or remove it before running this script.",
            file=sys.stderr,
        )
        return 1

    if resuming:
        print(
            f"Resuming partial migration: {NEW} and {BACKUP} both exist, sentinel missing."
        )
    else:
        print(f"Backing up {OLD} -> {BACKUP}")
        if BACKUP.exists():
            print(
                f"ERROR: {BACKUP} already exists. Remove it first.",
                file=sys.stderr,
            )
            return 1
        shutil.copytree(OLD, BACKUP, symlinks=True)

        print(f"Moving {OLD} -> {NEW}")
        shutil.move(str(OLD), str(NEW))

    print("Renaming loom.* files inside the new directory")
    _rename_loom_files(NEW)
    profiles = NEW / "profiles"
    if profiles.is_dir():
        for profile_dir in profiles.iterdir():
            if profile_dir.is_dir():
                _rename_loom_files(profile_dir)

    print("Rewriting SQLite content")
    _rewrite_db(NEW / "torque.db")
    _rewrite_db(NEW / "event_ingest.db")
    if profiles.is_dir():
        for profile_dir in profiles.iterdir():
            _rewrite_db(profile_dir / "torque.db")
            _rewrite_db(profile_dir / "event_ingest.db")

    print("Cleaning up legacy iTerm2 keybindings")
    _drop_legacy_iterm2_keybindings()

    sentinel.write_text(
        "Migrated from ~/.loom on first Torque launch. "
        "Backup at ~/.loom.pre-torque.bak — remove once verified.\n"
    )

    print()
    print("Migration complete.")
    print(f"  New data dir: {NEW}")
    print(f"  Backup:       {BACKUP}")
    print()
    print("Verify Torque starts cleanly, then `rm -rf ~/.loom.pre-torque.bak`.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

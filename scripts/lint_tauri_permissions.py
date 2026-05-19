#!/usr/bin/env python3
"""Verify every Tauri generate_handler command has a local permission."""
from __future__ import annotations

import re
import sys
from pathlib import Path

MACRO_RE = re.compile(r"tauri::generate_handler!\s*\[")
HANDLER_RE = re.compile(r"(?:(?:[A-Za-z_][A-Za-z0-9_]*|r#[A-Za-z_][A-Za-z0-9_]*)::)*([A-Za-z_][A-Za-z0-9_]*)\s*$")
SECTION_RE = re.compile(r"(?ms)^\s*\[\[permission\]\]\s*$")
IDENT_RE = re.compile(r"(?m)^\s*identifier\s*=\s*\"([^\"]+)\"")
ALLOW_RE = re.compile(r"(?ms)^\s*commands\.allow\s*=\s*\[(.*?)\]")
STRING_RE = re.compile(r'"([^"\\]*(?:\\.[^"\\]*)*)"')


def strip_comments(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        return "".join("\n" if ch == "\n" else " " for ch in match.group(0))

    return re.sub(r"//[^\n]*|/\*.*?\*/", replace, text, flags=re.S)


def matching_bracket(text: str, open_pos: int) -> int:
    depth = 1
    pos = open_pos + 1
    while pos < len(text):
        if text[pos] == "[":
            depth += 1
        elif text[pos] == "]":
            depth -= 1
            if depth == 0:
                return pos
        pos += 1
    raise ValueError("unterminated tauri::generate_handler![] macro")


def iter_generate_handlers(src_root: Path):
    for rust_file in sorted(src_root.rglob("*.rs")):
        raw = rust_file.read_text(encoding="utf-8")
        text = strip_comments(raw)
        for match in MACRO_RE.finditer(text):
            open_pos = match.end() - 1
            close_pos = matching_bracket(text, open_pos)
            body = text[open_pos + 1 : close_pos]
            body_line = text.count("\n", 0, open_pos) + 1
            start = 0
            depth = 0
            for pos, ch in enumerate(body):
                if ch in "([{":
                    depth += 1
                    continue
                if ch in ")]}":
                    depth = max(0, depth - 1)
                    continue
                if ch != "," or depth:
                    continue
                segment = body[start:pos]
                token = " ".join(segment.split())
                leading = len(segment) - len(segment.lstrip())
                line = body_line + body.count("\n", 0, start + leading)
                start = pos + 1
                if not token:
                    continue
                handler = HANDLER_RE.search(token)
                if handler:
                    yield handler.group(1), rust_file, line
            segment = body[start:]
            token = " ".join(segment.split())
            if token:
                leading = len(segment) - len(segment.lstrip())
                line = body_line + body.count("\n", 0, start + leading)
                handler = HANDLER_RE.search(token)
                if handler:
                    yield handler.group(1), rust_file, line


def iter_permission_entries(permissions_dir: Path):
    for toml_file in sorted(permissions_dir.rglob("*.toml")):
        text = toml_file.read_text(encoding="utf-8")
        sections = list(SECTION_RE.finditer(text))
        for index, match in enumerate(sections):
            section_end = sections[index + 1].start() if index + 1 < len(sections) else len(text)
            section = text[match.end() : section_end]
            identifier = IDENT_RE.search(section)
            allowed = ALLOW_RE.search(section)
            if not identifier or not allowed:
                continue
            for command in STRING_RE.findall(allowed.group(1)):
                yield identifier.group(1), command, toml_file


def expected_identifier(command: str) -> str:
    return f"allow-{command.replace('_', '-')}"


def main() -> int:
    repo = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]
    src_root = repo / "src-tauri" / "src"
    permissions_dir = repo / "src-tauri" / "permissions"

    handlers: dict[str, list[tuple[Path, int]]] = {}
    for command, path, line in iter_generate_handlers(src_root):
        handlers.setdefault(command, []).append((path, line))

    permissions = {(identifier, command) for identifier, command, _ in iter_permission_entries(permissions_dir)}
    missing = [command for command in sorted(handlers) if (expected_identifier(command), command) not in permissions]
    if not missing:
        print(f"Tauri permissions lint passed ({len(handlers)} command handlers checked).")
        return 0

    print("Missing Tauri command permissions:", file=sys.stderr)
    for command in missing:
        locations = ", ".join(
            f"{path.relative_to(repo)}:{line}" for path, line in handlers[command]
        )
        print(f"  - {command} ({locations})", file=sys.stderr)
        print(
            "    expected [[permission]] "
            f'identifier = "{expected_identifier(command)}" with commands.allow = ["{command}"] '
            "under src-tauri/permissions/*.toml",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

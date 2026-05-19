#!/usr/bin/env python3
"""Verify every Tauri generate_handler command has a matching local permission."""
import re
import sys
from pathlib import Path

MACRO_RE = re.compile(r"tauri::generate_handler!\s*\[")
HANDLER_RE = re.compile(
    r"(?:(?:[A-Za-z_][A-Za-z0-9_]*|r#[A-Za-z_][A-Za-z0-9_]*)::)*([A-Za-z_][A-Za-z0-9_]*)\s*$"
)
SECTION_RE = re.compile(r"(?ms)^\s*\[\[permission\]\]\s*$")
IDENT_RE = re.compile(r"(?m)^\s*identifier\s*=\s*\"([^\"]+)\"")
ALLOW_RE = re.compile(r"(?ms)^\s*commands\.allow\s*=\s*\[(.*?)\]")
STRING_RE = re.compile(r'"([^"\\]*(?:\\.[^"\\]*)*)"')


def strip_comments(text):
    def replace(match):
        return "".join("\n" if ch == "\n" else " " for ch in match.group(0))

    return re.sub(r"//[^\n]*|/\*.*?\*/", replace, text, flags=re.S)


def matching_bracket(text, open_pos):
    depth = 1
    for pos in range(open_pos + 1, len(text)):
        if text[pos] == "[":
            depth += 1
        elif text[pos] == "]":
            depth -= 1
            if depth == 0:
                return pos
    raise ValueError("unterminated tauri::generate_handler![] macro")


def top_level_segments(body):
    start = depth = 0
    for pos, ch in enumerate(body):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        elif ch == "," and depth == 0:
            yield start, pos
            start = pos + 1
    yield start, len(body)


def iter_generate_handlers(src_root):
    for rust_file in sorted(src_root.rglob("*.rs")):
        text = strip_comments(rust_file.read_text(encoding="utf-8"))
        for match in MACRO_RE.finditer(text):
            open_pos = match.end() - 1
            body = text[open_pos + 1 : matching_bracket(text, open_pos)]
            body_line = text.count("\n", 0, open_pos) + 1
            for start, end in top_level_segments(body):
                segment = body[start:end]
                token = " ".join(segment.split())
                if not token:
                    continue
                handler = HANDLER_RE.search(token)
                if handler:
                    leading = len(segment) - len(segment.lstrip())
                    line = body_line + body.count("\n", 0, start + leading)
                    yield handler.group(1), rust_file, line


def iter_permission_entries(permissions_dir):
    for toml_file in sorted(permissions_dir.rglob("*.toml")):
        text = toml_file.read_text(encoding="utf-8")
        sections = list(SECTION_RE.finditer(text))
        for index, match in enumerate(sections):
            end = sections[index + 1].start() if index + 1 < len(sections) else len(text)
            section = text[match.end() : end]
            identifier = IDENT_RE.search(section)
            allowed = ALLOW_RE.search(section)
            if identifier and allowed:
                for command in STRING_RE.findall(allowed.group(1)):
                    yield identifier.group(1), command


def expected_identifier(command):
    return f"allow-{command.replace('_', '-')}"


def main():
    repo = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]
    handlers = {}
    for command, path, line in iter_generate_handlers(repo / "src-tauri" / "src"):
        handlers.setdefault(command, []).append((path, line))

    permissions = set(iter_permission_entries(repo / "src-tauri" / "permissions"))
    missing = [cmd for cmd in sorted(handlers) if (expected_identifier(cmd), cmd) not in permissions]
    if not missing:
        print(f"Tauri permissions lint passed ({len(handlers)} command handlers checked).")
        return 0

    print("Missing Tauri command permissions:", file=sys.stderr)
    for command in missing:
        locations = ", ".join(f"{p.relative_to(repo)}:{line}" for p, line in handlers[command])
        print(f"  - {command} ({locations})", file=sys.stderr)
        print(
            f'    expected [[permission]] identifier = "{expected_identifier(command)}" '
            f'with commands.allow = ["{command}"] under src-tauri/permissions/*.toml',
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

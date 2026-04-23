"""LOOM:116 — static webview/CSS layout regression tests.

These are structural guardrails for the LOOM:116 top-bar-relocation change.
The standalone-mode header has to live inside #standalone-main-stack so it
only spans the agent panel column (and doesn't stretch above the terminal
workspace). If someone accidentally moves it back to body level, these
tests catch it before the visual regression hits production.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
WEBVIEW = ROOT / "webview.html"
STYLE = ROOT / "static" / "style.css"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class WebviewHeaderLocationTests(unittest.TestCase):
    """Header element must live inside the agent panel stack, not at body level."""

    def test_header_is_nested_inside_standalone_main_stack(self):
        html = _read(WEBVIEW)

        # Find the #standalone-main-stack opening tag and its closing </div>.
        stack_open = re.search(r'<div[^>]*id="standalone-main-stack"[^>]*>', html)
        self.assertIsNotNone(
            stack_open,
            "standalone-main-stack div not found in webview.html",
        )
        start = stack_open.end()
        depth = 1
        i = start
        while i < len(html) and depth:
            match = re.search(r"</?div\b", html[i:])
            if not match:
                break
            tag_start = i + match.start()
            if html[tag_start:tag_start + 2] == "</":
                depth -= 1
                i = tag_start + len("</div>")
            else:
                depth += 1
                i = tag_start + len("<div")
        stack_end = i
        stack_inner = html[start:stack_end]
        self.assertIn(
            "<header>",
            stack_inner,
            "<header> must live inside #standalone-main-stack so it only "
            "spans the agent panel column in standalone mode (LOOM:116).",
        )
        self.assertIn(
            'id="main"',
            stack_inner,
            "main element must still be inside #standalone-main-stack alongside the header.",
        )

    def test_header_is_not_a_body_level_sibling_of_workspace_shell(self):
        html = _read(WEBVIEW)
        # The old pre-LOOM:116 layout put <header> directly above
        # <div id="workspace-shell">. Lock against a revert.
        pattern = re.compile(
            r"</header>\s*(?:<!--[^>]*-->\s*)*<div[^>]*id=\"workspace-shell\"",
            re.DOTALL,
        )
        self.assertIsNone(
            pattern.search(html),
            "<header> must not be a body-level sibling directly above "
            "#workspace-shell. Move it inside #standalone-main-stack so the "
            "standalone-mode top bar doesn't stretch above the terminal pane.",
        )

    def test_header_controls_are_preserved_inside_the_new_location(self):
        html = _read(WEBVIEW)
        # Confirm the exact controls the user expects still exist on the
        # relocated header. Each is load-bearing: global settings, add group,
        # restart daemon. Connection status indicator + LOOM branding also.
        self.assertRegex(
            html,
            r'<header>[\s\S]*id="conn-dot"[\s\S]*</header>',
            "#conn-dot (connection status) missing from header",
        )
        self.assertRegex(
            html,
            r'<header>[\s\S]*<h1>LOOM</h1>[\s\S]*</header>',
            "LOOM branding missing from header",
        )
        self.assertRegex(
            html,
            r'<header>[\s\S]*openGlobalSettings\(\)[\s\S]*</header>',
            "Settings gear button missing from header",
        )
        self.assertRegex(
            html,
            r'<header>[\s\S]*openAddGroup\(\)[\s\S]*</header>',
            "+ Group button missing from header",
        )
        self.assertRegex(
            html,
            r'<header>[\s\S]*restartDaemon\(\)[\s\S]*</header>',
            "Restart daemon button missing from header",
        )


class StandaloneMainStackFlexDirectionTests(unittest.TestCase):
    """Both mode-variants of #standalone-main-stack must stack header above main."""

    def test_default_toolbelt_rule_is_flex_column(self):
        css = _read(STYLE)
        pattern = re.compile(
            r"#standalone-main-stack\s*\{([^}]*)\}",
            re.DOTALL,
        )
        match = pattern.search(css)
        self.assertIsNotNone(
            match,
            "Missing base #standalone-main-stack rule in style.css",
        )
        rule_body = match.group(1)
        self.assertIn(
            "flex-direction: column",
            rule_body,
            "#standalone-main-stack (toolbelt default) must use "
            "flex-direction: column so the header stacks above #main.",
        )

    def test_runtime_embedded_rule_is_flex_column(self):
        css = _read(STYLE)
        pattern = re.compile(
            r"body\.runtime-embedded\s+#standalone-main-stack\s*\{([^}]*)\}",
            re.DOTALL,
        )
        match = pattern.search(css)
        self.assertIsNotNone(
            match,
            "Missing body.runtime-embedded #standalone-main-stack rule in style.css",
        )
        rule_body = match.group(1)
        self.assertIn(
            "flex-direction: column",
            rule_body,
            "body.runtime-embedded #standalone-main-stack must use "
            "flex-direction: column so the header stacks above #main in "
            "standalone mode.",
        )

    def test_runtime_embedded_grid_leaves_main_stack_in_column_one(self):
        # Sanity: the outer grid still places #standalone-main-stack in
        # column 1 so the relocated header stays within the agent panel
        # bounds and doesn't spill into the terminal-workspace column.
        css = _read(STYLE)
        pattern = re.compile(
            r"body\.runtime-embedded\s+#standalone-main-stack\s*\{([^}]*)\}",
            re.DOTALL,
        )
        rule_body = pattern.search(css).group(1)
        self.assertIn(
            "grid-column: 1",
            rule_body,
            "standalone grid must still pin #standalone-main-stack to "
            "column 1; otherwise the relocated header won't stay within "
            "the agent panel.",
        )


if __name__ == "__main__":
    unittest.main()

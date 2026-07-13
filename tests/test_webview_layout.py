"""TORQUE:116 — static webview/CSS layout regression tests.

These are structural guardrails for the TORQUE:116 top-bar-relocation change.
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
STYLE_MODULES = [
    ROOT / "static" / "styles" / name
    for name in (
        "tokens-base.css",
        "workspace-grid.css",
        "modals.css",
        "workspace-shell.css",
        "board-panels.css",
        "agent-panel.css",
        "desktop-features.css",
        "feature-panels.css",
    )
]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _read_app_styles() -> str:
    """Return the production stylesheet cascade as one searchable source."""
    return "\n".join(_read(path) for path in STYLE_MODULES)


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
            "spans the agent panel column in standalone mode (TORQUE:116).",
        )
        self.assertIn(
            'id="main"',
            stack_inner,
            "main element must still be inside #standalone-main-stack alongside the header.",
        )

    def test_header_is_not_a_body_level_sibling_of_workspace_shell(self):
        html = _read(WEBVIEW)
        # The old pre-TORQUE:116 layout put <header> directly above
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
        # restart daemon. Connection status indicator + TORQUE branding also.
        self.assertRegex(
            html,
            r'<header>[\s\S]*id="conn-dot"[\s\S]*</header>',
            "#conn-dot (connection status) missing from header",
        )
        self.assertRegex(
            html,
            r'<header>[\s\S]*<h1[^>]*class="[^"]*\bapp-wordmark\b[^"]*"[^>]*>TORQUE</h1>[\s\S]*</header>',
            "TORQUE branding missing from header",
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
        css = _read_app_styles()
        pattern = re.compile(
            r"#standalone-main-stack\s*\{([^}]*)\}",
            re.DOTALL,
        )
        match = pattern.search(css)
        self.assertIsNotNone(
            match,
            "Missing base #standalone-main-stack rule in the app stylesheet cascade",
        )
        rule_body = match.group(1)
        self.assertIn(
            "flex-direction: column",
            rule_body,
            "#standalone-main-stack (toolbelt default) must use "
            "flex-direction: column so the header stacks above #main.",
        )

    def test_runtime_embedded_rule_is_flex_column(self):
        css = _read_app_styles()
        pattern = re.compile(
            r"body\.runtime-embedded\s+#standalone-main-stack\s*\{([^}]*)\}",
            re.DOTALL,
        )
        match = pattern.search(css)
        self.assertIsNotNone(
            match,
            "Missing body.runtime-embedded #standalone-main-stack rule in the app stylesheet cascade",
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
        css = _read_app_styles()
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


def _panelbar_html(html: str) -> str:
    start = html.index('<div id="panelbar">')
    end = html.index("<!-- Context menu", start)
    return html[start:end]


class WebviewStatusBarLayoutTests(unittest.TestCase):
    """Bottom panelbar keeps stable status ids and panel buttons."""

    PANEL_APPS = [
        "board",
        "chat",
        "actions",
        "templates",
        "history",
        "context",
        "engineer",
        "events",
        "supervisor",
        "health",
    ]

    def test_bottom_panelbar_status_clusters_and_daemon_anchor_are_preserved(self):
        html = _read(WEBVIEW)
        panelbar = _panelbar_html(html)

        self.assertNotIn('<div id="taskbar">', html)
        self.assertIn('id="statusbar-info"', panelbar)
        self.assertIn('id="statusbar-panel-buttons"', panelbar)
        self.assertLess(
            html.index('<div id="bottom-panel"'),
            html.index('<div id="panelbar">'),
            "panel launcher/status bar must stay below the bottom panel",
        )
        self.assertLess(
            panelbar.index('id="statusbar-panel-buttons"'),
            panelbar.index('class="taskbar-spacer"'),
            "panel buttons must stay on the left of the bottom bar",
        )
        self.assertLess(
            panelbar.index('class="taskbar-spacer"'),
            panelbar.index('id="statusbar-info"'),
            "statusbar-info must be right-aligned after the spacer",
        )
        self.assertRegex(
            panelbar,
            r'id="statusbar-info"[\s\S]*id="daemon-status-indicator"[\s\S]*id="taskbar-conn-dot"',
            "daemon wrapper and #taskbar-conn-dot anchor must stay inside statusbar-info",
        )
        self.assertRegex(
            panelbar,
            r'id="statusbar-claude-usage"[\s\S]*Claude —',
            "Claude usage chip must render as an unknown placeholder",
        )

    def test_panel_buttons_keep_ids_data_app_labels_and_launcher_handlers(self):
        panel_html = _panelbar_html(_read(WEBVIEW))

        for app in self.PANEL_APPS:
            self.assertRegex(
                panel_html,
                r'<button[^>]*class="[^"]*\btaskbar-app\b[^"]*"[^>]*data-app="'
                + re.escape(app)
                + r'"[^>]*onclick="panelNavOpenPanel\('
                + re.escape(repr(app))
                + r'\)"',
                f"{app} panel button must remain a .taskbar-app with its launcher handler",
            )

        self.assertIn('id="taskbar-restore-layout"', panel_html)
        self.assertGreater(
            panel_html.index('id="taskbar-restore-layout"'),
            panel_html.rindex('class="taskbar-app"'),
            "Restore layout button follows the panel app buttons in the bottom cluster",
        )

    def test_status_bar_script_loads_after_events_before_panel_manager(self):
        html = _read(WEBVIEW)
        self.assertLess(
            html.index('/static/js/events.js'),
            html.index('/static/js/status_bar.js'),
            "status_bar.js may use events helpers, so it must load after events.js",
        )
        self.assertLess(
            html.index('/static/js/status_bar.js'),
            html.index('/static/js/panel_manager.js'),
            "status_bar.js must be ready before panel_manager/main drive panel state",
        )


if __name__ == "__main__":
    unittest.main()

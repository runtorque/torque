import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TauriSecurityContractTests(unittest.TestCase):
    def test_react_global_accessibility_policy_is_durable(self):
        css = (ROOT / "ui" / "src" / "design" / "globals.css").read_text()

        self.assertIn(":focus-visible", css)
        self.assertIn("box-shadow: var(--focus-ring)", css)
        self.assertIn("@media (prefers-reduced-motion: reduce)", css)
        self.assertIn("transition-duration: 0.01ms !important", css)
        self.assertIn("animation-duration: 0.01ms !important", css)
        self.assertIn("scroll-behavior: auto !important", css)

    def test_production_csp_and_global_bridge_are_fail_closed(self):
        config = json.loads((ROOT / "src-tauri" / "tauri.conf.json").read_text())
        app = config["app"]
        csp = app["security"]["csp"]

        self.assertFalse(app["withGlobalTauri"])
        self.assertFalse(app["security"]["dangerousDisableAssetCspModification"])
        self.assertIn("default-src 'self' http://127.0.0.1:*", csp)
        self.assertIn("object-src 'none'", csp)
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertIn("ws://127.0.0.1:*", csp)
        self.assertNotIn("'unsafe-eval'", csp)

    def test_capabilities_are_restricted_to_torque_windows_and_commands(self):
        capability = json.loads(
            (ROOT / "src-tauri" / "capabilities" / "default.json").read_text()
        )
        permissions = set(capability["permissions"])

        self.assertEqual(["main", "panel-*"], capability["windows"])
        self.assertEqual(["http://127.0.0.1:*"], capability["remote"]["urls"])
        self.assertNotIn("shell:allow-open", permissions)
        self.assertNotIn("dialog:allow-confirm", permissions)
        self.assertIn("allow-open-external", permissions)
        self.assertIn("allow-confirm", permissions)

        cargo = (ROOT / "src-tauri" / "Cargo.toml").read_text()
        self.assertNotIn("tauri-plugin-shell", cargo)


if __name__ == "__main__":
    unittest.main()

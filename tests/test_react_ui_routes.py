import tempfile
import unittest
from pathlib import Path

from torque.react_ui_assets import (
    normalize_ui_default,
    react_ui_cache_headers,
    react_ui_request_path,
    resolve_react_ui_file,
)


class ReactUiRouteTests(unittest.TestCase):
    def test_resolves_index_and_hashed_assets_inside_dist(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            index = root / "index.html"
            asset = root / "assets" / "index-abc123.js"
            asset.parent.mkdir()
            index.write_text("<html></html>", encoding="utf-8")
            asset.write_text("export {};", encoding="utf-8")

            self.assertEqual(resolve_react_ui_file(root, ""), index.resolve())
            self.assertEqual(
                resolve_react_ui_file(root, "assets/index-abc123.js"),
                asset.resolve(),
            )
            self.assertEqual(react_ui_cache_headers(index), {"Cache-Control": "no-store"})
            self.assertEqual(
                react_ui_cache_headers(asset),
                {"Cache-Control": "public, max-age=31536000, immutable"},
            )

    def test_rejects_missing_and_traversal_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "dist"
            root.mkdir()
            outside = root.parent / "secret.txt"
            outside.write_text("secret", encoding="utf-8")

            self.assertIsNone(resolve_react_ui_file(root, "../secret.txt"))
            self.assertIsNone(resolve_react_ui_file(root, "missing.js"))

    def test_react_is_default_and_legacy_is_an_explicit_profile_rollback(self):
        self.assertEqual(normalize_ui_default(None), "react")
        self.assertEqual(normalize_ui_default(" REACT "), "react")
        self.assertEqual(normalize_ui_default("legacy"), "legacy")

        with self.assertRaisesRegex(ValueError, "Invalid TORQUE_UI_DEFAULT"):
            normalize_ui_default("classic")

    def test_root_asset_route_restores_the_consumed_assets_prefix(self):
        self.assertEqual(
            react_ui_request_path("/assets/index-abc123.js", "index-abc123.js"),
            "assets/index-abc123.js",
        )
        self.assertEqual(
            react_ui_request_path("/ui-next/assets/index-abc123.js", "assets/index-abc123.js"),
            "assets/index-abc123.js",
        )

    def test_server_registers_root_assets_and_keeps_both_explicit_clients(self):
        server_source = (Path(__file__).resolve().parents[1] / "torque" / "server.py").read_text(
            encoding="utf-8"
        )

        self.assertIn('app_server.router.add_get("/", handle_index)', server_source)
        self.assertIn('app_server.router.add_get("/assets/{path:.*}", handle_index)', server_source)
        self.assertIn('app_server.router.add_get("/legacy/", handle_legacy)', server_source)
        self.assertIn('app_server.router.add_get("/ui-next/{path:.*}", handle_react_ui)', server_source)


if __name__ == "__main__":
    unittest.main()

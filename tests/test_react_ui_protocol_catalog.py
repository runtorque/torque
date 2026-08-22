import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ReactUiProtocolCatalogTests(unittest.TestCase):
    def test_known_operations_cover_classic_client_and_literal_backend_emits(self):
        types_source = (ROOT / "ui/src/protocol/types.ts").read_text(encoding="utf-8")
        catalog_block = types_source.split(
            "export const KNOWN_DELTA_OPERATIONS = [", 1
        )[1].split("] as const;", 1)[0]
        known = set(re.findall(r"'([a-z][a-z0-9_]*)'", catalog_block))

        classic_apply = (ROOT / "static/js/ws/delta-apply.js").read_text(
            encoding="utf-8"
        )
        classic_registry = (ROOT / "static/js/ws/delta-registry.js").read_text(
            encoding="utf-8"
        )
        required = set(re.findall(r"case ['\"]([a-z][a-z0-9_]*)['\"]", classic_apply))
        required.update(
            re.findall(
                r"_registerDeltaOperations\(['\"]([a-z][a-z0-9_]*)['\"]",
                classic_registry,
            )
        )
        for source_path in (ROOT / "torque").rglob("*.py"):
            required.update(
                re.findall(
                    r"_emit\(\s*['\"]([a-z][a-z0-9_]*)['\"]",
                    source_path.read_text(encoding="utf-8"),
                )
            )

        self.assertEqual(required - known, set())


if __name__ == "__main__":
    unittest.main()

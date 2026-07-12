"""Runtime dependency constraint contract tests."""

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = ROOT / "requirements"


class RuntimeRequirementTests(unittest.TestCase):
    def test_runtime_requirement_sets_share_direct_constraints(self):
        for name in ("daemon.txt", "ai.txt"):
            text = (REQUIREMENTS / name).read_text(encoding="utf-8")
            self.assertIn("-c constraints.txt", text, name)

    def test_every_declared_direct_dependency_is_pinned(self):
        constraints = (
            REQUIREMENTS / "constraints.txt"
        ).read_text(encoding="utf-8")
        pinned = {
            line.split("==", 1)[0].strip().lower()
            for line in constraints.splitlines()
            if "==" in line and not line.lstrip().startswith("#")
        }
        declared = set()
        for name in ("daemon.txt", "desktop.txt", "ai.txt"):
            for raw in (REQUIREMENTS / name).read_text(
                encoding="utf-8"
            ).splitlines():
                line = raw.strip()
                if not line or line.startswith(("#", "-r", "-c")):
                    continue
                package = re.split(r"[<>=!~\[]", line, maxsplit=1)[0]
                declared.add(package.strip().lower())
        self.assertEqual(declared - pinned, set())


if __name__ == "__main__":
    unittest.main()

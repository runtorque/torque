import json
import subprocess
import sys
import unittest
from pathlib import Path

from torque import ai_deps


ROOT = Path(__file__).resolve().parents[1]


class AIDependencyProbeTests(unittest.TestCase):
    def test_probe_reports_missing_without_importing_dependencies(self):
        calls = []

        def fake_find_spec(module_name):
            calls.append(module_name)
            return None

        self.assertEqual(
            ai_deps.missing_ai_dependency_packages(find_spec=fake_find_spec),
            ["sentence-transformers", "sqlite-vec"],
        )
        self.assertEqual(
            ai_deps.embeddings_dependency_status(find_spec=fake_find_spec),
            "missing",
        )
        self.assertEqual(
            calls,
            [
                "sentence_transformers",
                "sqlite_vec",
                "sentence_transformers",
                "sqlite_vec",
            ],
        )

    def test_probe_reports_available_when_all_specs_exist(self):
        def fake_find_spec(_module_name):
            return object()

        self.assertEqual(
            ai_deps.missing_ai_dependency_packages(find_spec=fake_find_spec),
            [],
        )
        self.assertEqual(
            ai_deps.embeddings_dependency_status(find_spec=fake_find_spec),
            "available",
        )

    def test_import_guard_does_not_load_heavy_optional_modules(self):
        code = """
import json
import sys

names = ["sentence_transformers", "torch", "sqlite_vec"]
for name in names:
    sys.modules.pop(name, None)

import torque
from torque import ai_deps

ai_deps.embeddings_dependency_status()
print(json.dumps({name: name in sys.modules for name in names}, sort_keys=True))
"""
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        )

        self.assertEqual(
            json.loads(proc.stdout),
            {
                "sentence_transformers": False,
                "sqlite_vec": False,
                "torch": False,
            },
        )


if __name__ == "__main__":
    unittest.main()

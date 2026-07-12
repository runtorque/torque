import subprocess
import sys
import unittest
from pathlib import Path


class DocumentationContractTests(unittest.TestCase):
    def test_documentation_contracts(self):
        root = Path(__file__).resolve().parents[1]
        proc = subprocess.run(
            [sys.executable, "scripts/check_docs_contract.py"],
            cwd=root,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            self.fail(
                "documentation contract check failed\n"
                f"stdout:\n{proc.stdout}\n"
                f"stderr:\n{proc.stderr}"
            )

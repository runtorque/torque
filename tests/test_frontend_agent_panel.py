import subprocess
import unittest
from pathlib import Path


class FrontendAgentPanelTests(unittest.TestCase):
    def test_frontend_agent_panel(self):
        root = Path(__file__).resolve().parents[1]
        script = Path(__file__).with_name('frontend_agent_panel.test.js')
        proc = subprocess.run(
            ['node', '--test', str(script)],
            cwd=root,
            capture_output=True,
            text=True,
        )

        if proc.returncode != 0:
            self.fail(
                'frontend agent panel tests failed\n'
                f'stdout:\n{proc.stdout}\n'
                f'stderr:\n{proc.stderr}'
            )

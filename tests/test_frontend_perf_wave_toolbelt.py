import subprocess
import unittest
from pathlib import Path


class FrontendPerfWaveToolbeltTests(unittest.TestCase):
    def test_frontend_perf_wave_toolbelt(self):
        root = Path(__file__).resolve().parents[1]
        script = Path(__file__).with_name('frontend_perf_wave_toolbelt.test.js')
        proc = subprocess.run(
            ['node', '--test', str(script)],
            cwd=root,
            capture_output=True,
            text=True,
        )

        if proc.returncode != 0:
            self.fail(
                'frontend perf-wave toolbelt tests failed\n'
                f'stdout:\n{proc.stdout}\n'
                f'stderr:\n{proc.stderr}'
            )

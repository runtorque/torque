"""Glob-run every frontend_*.test.js that lacks a hand-written .py wrapper.

`make test` (`python3 -m unittest discover -s tests`) only runs frontend Node
suites that have a `test_frontend_*.py` wrapper shelling out to `node --test`.
Historically ~9 `tests/frontend_*.test.js` files had no wrapper, so they never
gated `make test` (they ran only under a manual `node --test`). This runner
closes that hole: it globs all `tests/frontend_*.test.js`, subtracts the files
already referenced by other `test_*.py` wrappers (so nothing runs twice), and
executes each remaining file via `node --test`, failing on any non-zero exit.

Auto-includes any future `frontend_*.test.js` until/unless someone adds a
dedicated wrapper for it (at which point the dedup below drops it here).
"""

import re
import shutil
import subprocess
import unittest
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent
_ROOT = _TESTS_DIR.parent
_SELF = Path(__file__).name
_JS_NAME_RE = re.compile(r"frontend_\w*\.test\.js")


def _all_frontend_js():
    return sorted(p.name for p in _TESTS_DIR.glob("frontend_*.test.js"))


def _wrapped_frontend_js():
    """Filenames referenced by any other test_*.py wrapper (run-elsewhere)."""
    wrapped = set()
    for py in _TESTS_DIR.glob("test_*.py"):
        if py.name == _SELF:
            continue
        try:
            text = py.read_text(encoding="utf-8")
        except OSError:
            continue
        wrapped.update(_JS_NAME_RE.findall(text))
    return wrapped


def _unwrapped_frontend_js():
    wrapped = _wrapped_frontend_js()
    return [name for name in _all_frontend_js() if name not in wrapped]


class FrontendGlobNodeSuite(unittest.TestCase):
    """Run every otherwise-unwrapped frontend Node suite via `node --test`."""

    def test_unwrapped_frontend_node_suites(self):
        if shutil.which("node") is None:
            self.skipTest("node not available; skipping frontend Node suites")

        unwrapped = _unwrapped_frontend_js()
        self.assertTrue(
            unwrapped or _wrapped_frontend_js(),
            "no frontend_*.test.js files discovered under tests/",
        )

        failures = []
        for name in unwrapped:
            with self.subTest(suite=name):
                proc = subprocess.run(
                    ["node", "--test", str(_TESTS_DIR / name)],
                    cwd=_ROOT,
                    capture_output=True,
                    text=True,
                )
                if proc.returncode != 0:
                    failures.append(name)
                    self.fail(
                        f"frontend Node suite failed: {name}\n"
                        f"stdout:\n{proc.stdout}\n"
                        f"stderr:\n{proc.stderr}"
                    )


if __name__ == "__main__":
    unittest.main()

"""Emit unittest TestResult counts for the Makefile test-footer runner.

This module is inert unless the runner supplies TORQUE_TEST_RESULT_FD.  It
reports the in-memory TestResult rather than attempting to recover facts from
human-oriented unittest output.
"""

import json
import os
import unittest


_fd_text = os.environ.get("TORQUE_TEST_RESULT_FD")
if _fd_text:
    _result_fd = int(_fd_text)
    _original_run = unittest.TextTestRunner.run

    def _run_with_result_footer(self, test):
        result = _original_run(self, test)
        failed = len(result.failures)
        errors = len(result.errors)
        skipped = len(result.skipped)
        expected_failures = len(result.expectedFailures)
        unexpected_successes = len(result.unexpectedSuccesses)
        ran = result.testsRun
        payload = {
            "source": "unittest.TestResult",
            "ran": ran,
            "passed": ran - failed - errors - skipped - expected_failures - unexpected_successes,
            "failed": failed,
            "skipped": skipped,
            "errors": errors,
            "expected_failures": expected_failures,
            "unexpected_successes": unexpected_successes,
        }
        os.write(_result_fd, json.dumps(payload, sort_keys=True).encode("utf-8"))
        return result

    unittest.TextTestRunner.run = _run_with_result_footer

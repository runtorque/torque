from types import SimpleNamespace
import unittest

from torque.adapters.codex import CodexAdapter
from torque.session_end_backstop import CodexIdleSessionEndDetector


class CodexIdleSessionEndDetectorTests(unittest.TestCase):
    def setUp(self):
        self.adapter = CodexAdapter()
        self.detector = CodexIdleSessionEndDetector()
        self.ready_screen = "\n".join([
            "OpenAI Codex",
            "model: gpt-5.4 high",
            "directory: ~/repo",
            "› Ready",
        ])
        self.busy_screen = "\n".join([
            "OpenAI Codex",
            "Working on your request...",
            "Running tests",
        ])
        self.stable_polls = self.adapter.get_input_ready_policy().stable_polls
        self.cell = SimpleNamespace(
            id="cell-codex",
            cell_type="agent",
            agent_type="codex",
            session_id="session-codex",
            status="running",
        )

    def _ready(self, screen: str) -> bool:
        return self.adapter.is_input_ready_screen(screen)

    def test_fires_once_after_busy_to_stable_idle_transition(self):
        # The old pre-submit composer must not count until the running turn has
        # shown a non-ready/busy screen.
        self.assertFalse(self.detector.observe(
            self.cell,
            ready=self._ready(self.ready_screen),
            stable_polls=self.stable_polls,
        ))

        self.assertFalse(self.detector.observe(
            self.cell,
            ready=self._ready(self.busy_screen),
            stable_polls=self.stable_polls,
        ))
        self.assertFalse(self.detector.observe(
            self.cell,
            ready=self._ready(self.ready_screen),
            stable_polls=self.stable_polls,
        ))
        self.assertTrue(self.detector.observe(
            self.cell,
            ready=self._ready(self.ready_screen),
            stable_polls=self.stable_polls,
        ))

        # Remaining idle at the composer must not re-fire.
        for _ in range(3):
            self.assertFalse(self.detector.observe(
                self.cell,
                ready=self._ready(self.ready_screen),
                stable_polls=self.stable_polls,
            ))

    def test_does_not_fire_when_idle_at_rest_or_non_codex(self):
        self.cell.status = "idle"
        self.assertFalse(self.detector.observe(
            self.cell,
            ready=self._ready(self.ready_screen),
            stable_polls=self.stable_polls,
        ))

        self.cell.status = "running"
        self.cell.agent_type = "claude-code"
        self.assertFalse(self.detector.observe(
            self.cell,
            ready=True,
            stable_polls=1,
        ))

    def test_reset_starts_a_new_turn_without_counting_old_ready_screen(self):
        self.assertFalse(self.detector.observe(
            self.cell,
            ready=self._ready(self.busy_screen),
            stable_polls=self.stable_polls,
        ))
        self.assertFalse(self.detector.observe(
            self.cell,
            ready=self._ready(self.ready_screen),
            stable_polls=self.stable_polls,
        ))
        self.assertTrue(self.detector.observe(
            self.cell,
            ready=self._ready(self.ready_screen),
            stable_polls=self.stable_polls,
        ))

        self.detector.reset(self.cell.id, self.cell.session_id)
        self.assertFalse(self.detector.observe(
            self.cell,
            ready=self._ready(self.ready_screen),
            stable_polls=self.stable_polls,
        ))
        self.assertFalse(self.detector.observe(
            self.cell,
            ready=self._ready(self.busy_screen),
            stable_polls=self.stable_polls,
        ))
        self.assertFalse(self.detector.observe(
            self.cell,
            ready=self._ready(self.ready_screen),
            stable_polls=self.stable_polls,
        ))
        self.assertTrue(self.detector.observe(
            self.cell,
            ready=self._ready(self.ready_screen),
            stable_polls=self.stable_polls,
        ))

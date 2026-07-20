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
        # Bannerless screens: the startup banner ("OpenAI Codex" / "model:" /
        # "directory:") has scrolled out of the viewport after real work, so
        # turn completion must be recognized from the composer alone.
        self.bannerless_busy_screen = "\n".join([
            "  Editing src/app.py",
            "",
            "  • Working  (12s • Esc to interrupt)",
            "╭──────────────────────────────────────────────╮",
            "│ ›                                            │",
            "╰──────────────────────────────────────────────╯",
        ])
        self.bannerless_idle_screen = "\n".join([
            "  Ran tests: 42 passed, 0 failed",
            "",
            "╭──────────────────────────────────────────────╮",
            "│ › Ask Codex to do something                  │",
            "╰──────────────────────────────────────────────╯",
            "  ⏎ send   ⌃J newline",
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

    def _idle(self, screen: str) -> bool:
        return self.adapter.is_idle_composer_screen(screen)

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

    def test_idle_composer_recognized_after_banner_scrolls_off(self):
        # Regression: once real work pushes the startup banner out of the
        # viewport, is_input_ready_screen no longer matches the idle composer,
        # which left Codex agents stuck on "running". The banner-independent
        # idle-composer check must still recognize completion.
        self.assertFalse(self._ready(self.bannerless_idle_screen))
        self.assertTrue(self._idle(self.bannerless_idle_screen))
        # A busy composer keeps the prompt marker but shows the interrupt hint.
        self.assertFalse(self._idle(self.bannerless_busy_screen))

        # busy -> stable idle transition, evaluated through the real predicate.
        self.assertFalse(self.detector.observe(
            self.cell,
            ready=self._idle(self.bannerless_busy_screen),
            stable_polls=self.stable_polls,
            screen_text=self.bannerless_busy_screen,
        ))
        self.assertFalse(self.detector.observe(
            self.cell,
            ready=self._idle(self.bannerless_idle_screen),
            stable_polls=self.stable_polls,
            screen_text=self.bannerless_idle_screen,
        ))
        self.assertTrue(self.detector.observe(
            self.cell,
            ready=self._idle(self.bannerless_idle_screen),
            stable_polls=self.stable_polls,
            screen_text=self.bannerless_idle_screen,
        ))

    def test_screen_change_marks_busy_when_interrupt_hint_is_missed(self):
        # A fast/output-quiet turn whose interrupt hint was never caught in a
        # poll must still complete: visible screen movement during the running
        # turn is treated as evidence of work.
        first = "\n".join([
            "╭──────────────────────────────────────────────╮",
            "│ › Ask Codex to do something                  │",
            "╰──────────────────────────────────────────────╯",
        ])
        streamed = "\n".join([
            "  Done — updated the config.",
            "╭──────────────────────────────────────────────╮",
            "│ › Ask Codex to do something                  │",
            "╰──────────────────────────────────────────────╯",
        ])
        # First poll looks idle but is the pre-work composer: no completion.
        self.assertFalse(self.detector.observe(
            self.cell, ready=self._idle(first),
            stable_polls=self.stable_polls, screen_text=first,
        ))
        # Output appeared (screen changed) though the hint was never observed.
        self.assertFalse(self.detector.observe(
            self.cell, ready=self._idle(streamed),
            stable_polls=self.stable_polls, screen_text=streamed,
        ))
        # Screen is now stable and idle -> completion fires.
        self.assertTrue(self.detector.observe(
            self.cell, ready=self._idle(streamed),
            stable_polls=self.stable_polls, screen_text=streamed,
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

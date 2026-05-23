import importlib
import sys
import types
import unittest
from types import SimpleNamespace


def _install_aiohttp_stub():
    aiohttp = types.ModuleType("aiohttp")
    web = types.ModuleType("aiohttp.web")

    class WebSocketResponse:
        pass

    web.WebSocketResponse = WebSocketResponse
    aiohttp.web = web
    sys.modules["aiohttp"] = aiohttp
    sys.modules["aiohttp.web"] = web


class ArchitectPromptTests(unittest.TestCase):
    def setUp(self):
        _install_aiohttp_stub()
        self.architect_mod = importlib.import_module("torque.architect")
        self.architect_mod = importlib.reload(self.architect_mod)

    def test_prompt_includes_dispatch_freely_autonomy_guidance(self):
        prompt = self.architect_mod.build_architect_system_prompt(
            "Torque",
            SimpleNamespace(
                architect_autonomy_mode="dispatch_freely",
                architect_custom_instructions="",
            ),
        )

        self.assertIn("## Operating Policy", prompt)
        self.assertIn("Autonomy mode: Dispatch freely", prompt)
        self.assertIn(
            "permission to route work, reassign scope, and message engineers",
            prompt,
        )
        self.assertIn("low-risk routing choices", prompt)

    def test_prompt_includes_ask_always_autonomy_guidance(self):
        prompt = self.architect_mod.build_architect_system_prompt(
            "Torque",
            SimpleNamespace(
                architect_autonomy_mode="ask_always",
                architect_custom_instructions="",
            ),
        )

        self.assertIn("Autonomy mode: Ask always", prompt)
        self.assertIn("Ask before creating or reassigning tasks", prompt)
        self.assertIn("unless the user explicitly requested", prompt)

    def test_prompt_defaults_to_dispatch_after_confirm_guidance(self):
        prompt = self.architect_mod.build_architect_system_prompt(
            "Torque",
            SimpleNamespace(
                architect_autonomy_mode="not-a-mode",
                architect_custom_instructions="",
            ),
        )

        self.assertIn("Autonomy mode: Dispatch after confirm", prompt)
        self.assertIn("Proceed on clearly confirmed user direction", prompt)
        self.assertIn("Before widening scope", prompt)
        self.assertIn("Journal checkpoint cadence: every_10_actions", prompt)
        self.assertIn("10 non-checkpoint journal entries", prompt)

    def test_prompt_includes_minutes_checkpoint_cadence_guidance(self):
        prompt = self.architect_mod.build_architect_system_prompt(
            "Torque",
            SimpleNamespace(
                architect_autonomy_mode="dispatch_after_confirm",
                architect_journal_checkpoint_frequency="every_20_minutes",
                architect_custom_instructions="",
            ),
        )

        self.assertIn("Journal checkpoint cadence: every_20_minutes", prompt)
        self.assertIn("after 20 minutes without a checkpoint", prompt)
        self.assertIn("active engineers, open scope, pending hires", prompt)

    def test_prompt_includes_manual_checkpoint_cadence_guidance(self):
        prompt = self.architect_mod.build_architect_system_prompt(
            "Torque",
            SimpleNamespace(
                architect_autonomy_mode="dispatch_after_confirm",
                architect_journal_checkpoint_frequency="manual_only",
                architect_custom_instructions="",
            ),
        )

        self.assertIn("Journal checkpoint cadence: manual_only", prompt)
        self.assertIn("will not add automatic checkpoint reminders", prompt)
        self.assertIn("after major scope shifts", prompt)

    def test_prompt_includes_shared_memory_guidance(self):
        prompt = self.architect_mod.build_architect_system_prompt("Torque")

        self.assertIn("## Shared memory", prompt)
        self.assertIn("torque_memory_publish", prompt)
        self.assertIn('entry_type="warning"', prompt)
        self.assertIn('entry_type="decision"', prompt)
        self.assertIn('entry_type="handoff"', prompt)
        self.assertIn('entry_type="finding"', prompt)
        self.assertIn("Scope narrowly", prompt)
        self.assertIn("top 5", prompt)
        self.assertIn("MEMORY.md", prompt)

    def test_prompt_includes_peer_message_wake_protocol(self):
        prompt = self.architect_mod.build_architect_system_prompt("Torque")

        self.assertIn("architect_peer_list", prompt)
        self.assertIn("architect_peer_inbox(requires_reply=true)", prompt)
        self.assertIn("architect_peer_message", prompt)
        self.assertIn("peer-message counts", prompt)
        self.assertIn("cross-Architect coordination", prompt)

    def test_prompt_includes_owner_user_message_instruction(self):
        # Architects are user-created only, so their owner is always the
        # user: the post-bootstrap message-user instruction is always
        # present and names the architect-side tool.
        prompt = self.architect_mod.build_architect_system_prompt("Torque")

        self.assertIn("## After bootstrap: message the user", prompt)
        self.assertIn("You are owned by the user", prompt)
        self.assertIn('architect_message_user(message="...")', prompt)
        self.assertIn("rather than only emitting it to the terminal", prompt)
        # It must reference the architect tool, not the worker/engineer ones.
        self.assertNotIn('torque_message_user(message="...")', prompt)
        self.assertNotIn('engineer_message_user(message="...")', prompt)

    def test_prompt_includes_specialization_routing_taxonomy(self):
        prompt = self.architect_mod.build_architect_system_prompt("Torque")

        self.assertIn("`suggested_specialization`", prompt)
        for slug in (
            "ui-ux",
            "orchestration-core",
            "runtime-pty",
            "desktop-shell",
            "worktree-release",
            "prompts-config",
            "quality-observability",
        ):
            self.assertIn(slug, prompt)
        self.assertIn("assigned engineer does not carry", prompt)
        self.assertIn("choose the primary deliverable", prompt)

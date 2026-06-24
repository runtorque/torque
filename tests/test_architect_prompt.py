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
        self.agent_classes_mod = importlib.import_module("torque.agent_classes")
        self.agent_classes_mod = importlib.reload(self.agent_classes_mod)

    def _class_prompt_context(self, class_id: str):
        definition = self.agent_classes_mod.agent_class_definition_by_id(class_id)
        self.assertIsNotNone(definition)
        preview = self.agent_classes_mod.enriched_agent_class_preview(definition)
        return preview, preview["agent_profile"]

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

    def test_prompt_includes_boot_summary_first_with_raw_fallback(self):
        prompt = self.architect_mod.build_architect_system_prompt("Torque")

        self.assertIn("architect_boot_summary", prompt)
        self.assertIn("architect_attention_digest", prompt)
        self.assertIn("what needs my\n   attention now?", prompt)
        self.assertIn("read the cached boot-recovery summary", prompt)
        self.assertIn("fall back to the raw tools", prompt)
        self.assertIn("architect_journal_read", prompt)
        self.assertIn("architect_decision_list", prompt)
        self.assertIn("never\n   wait for summary generation", prompt)

    def test_prompt_includes_completion_audit_before_goal_completion(self):
        prompt = self.architect_mod.build_architect_system_prompt("Torque")

        self.assertIn("architect_completion_audit", prompt)
        self.assertIn("Before marking a decision/task wave or product goal complete", prompt)
        self.assertIn("complete_with_caveats", prompt)

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

    def test_prompt_includes_detailed_task_spec_contract(self):
        prompt = self.architect_mod.build_architect_system_prompt("Torque")

        self.assertIn("**Detailed task-spec contract**", prompt)
        self.assertIn("problem/context and why it matters", prompt)
        self.assertIn("user-facing goal and product", prompt)
        self.assertIn("relevant decisions, prior tasks", prompt)
        self.assertIn("explicit non-goals", prompt)
        self.assertIn("acceptance criteria", prompt)
        self.assertIn("verification or\n   test expectations", prompt)
        self.assertIn("required handoff evidence before Done/merge", prompt)
        self.assertIn("when\n   to ask or escalate", prompt)

    def test_full_architect_class_prompt_retains_management_guidance(self):
        class_snapshot, profile_snapshot = self._class_prompt_context(
            "default-architect"
        )

        prompt = self.architect_mod.build_architect_system_prompt(
            "Torque",
            agent_class_snapshot=class_snapshot,
            agent_profile_snapshot=profile_snapshot,
        )

        self.assertIn("Your role is to own product-level scope", prompt)
        self.assertIn("## Available tools", prompt)
        self.assertIn("architect_engineer_hire", prompt)
        self.assertIn("architect_task_create", prompt)
        self.assertIn("architect_engineer_message", prompt)
        self.assertIn("engineer_merge", prompt)
        self.assertIn("Hiring discipline", prompt)
        self.assertIn("Routing over instructing", prompt)

    def test_product_manager_prompt_uses_pm_safe_authority_not_full_architect_guidance(self):
        class_snapshot, profile_snapshot = self._class_prompt_context(
            "product-manager"
        )

        prompt = self.architect_mod.build_architect_system_prompt(
            "Torque",
            SimpleNamespace(
                architect_autonomy_mode="dispatch_after_confirm",
                architect_custom_instructions="",
            ),
            agent_class_snapshot=class_snapshot,
            agent_profile_snapshot=profile_snapshot,
        )

        self.assertIn("Product Manager", prompt)
        self.assertIn("proposal-only product authority", prompt)
        self.assertIn("architect_product_task_propose", prompt)
        self.assertIn("architect_product_decision_create", prompt)
        self.assertIn("architect_product_peer_*", prompt)
        self.assertIn("Autonomy mode: Dispatch after confirm (authority-bounded)", prompt)
        self.assertIn("Unavailable powers in this session", prompt)
        self.assertIn("accepted-decision authority", prompt)
        self.assertNotIn("## Shared memory", prompt)
        self.assertNotIn("architect_engineer_hire", prompt)
        self.assertNotIn("architect_pending_hire_status", prompt)
        self.assertNotIn("architect_engineer_message", prompt)
        self.assertNotIn("architect_task_create", prompt)
        self.assertNotIn("architect_task_reassign", prompt)
        self.assertNotIn("architect_task_move", prompt)
        self.assertNotIn("architect_deploy_state", prompt)
        self.assertNotIn("engineer_merge", prompt)
        self.assertNotIn("Hiring discipline", prompt)
        self.assertNotIn("Routing over instructing", prompt)
        self.assertNotIn(
            "permission to route work, reassign scope, and message engineers",
            prompt,
        )

    def test_creative_prompt_emphasizes_thinking_and_proposals_without_management_guidance(self):
        class_snapshot, profile_snapshot = self._class_prompt_context(
            "creative-architect"
        )

        prompt = self.architect_mod.build_architect_system_prompt(
            "Torque",
            SimpleNamespace(
                architect_autonomy_mode="ask_always",
                architect_journal_checkpoint_frequency="manual_only",
                architect_custom_instructions="",
            ),
            agent_class_snapshot=class_snapshot,
            agent_profile_snapshot=profile_snapshot,
        )

        self.assertIn("Creative Architect", prompt)
        self.assertIn("ideation and product-discovery partner", prompt)
        self.assertIn("Diverge first", prompt)
        self.assertIn("Scratchpad/Mind Map", prompt)
        self.assertIn("architect_thinking_scratchpad_*", prompt)
        self.assertIn("architect_thinking_mind_map_*", prompt)
        self.assertIn("architect_product_idea_brief_*", prompt)
        self.assertIn("Idea Brief", prompt)
        self.assertIn("architect_product_task_propose", prompt)
        self.assertIn("architect_product_decision_create", prompt)
        self.assertIn("architect_product_peer_*", prompt)
        self.assertIn("Autonomy mode: Ask always (authority-bounded)", prompt)
        self.assertIn("checkpoint with the visible private journal wrapper", prompt)
        self.assertNotIn("architect_engineer_hire", prompt)
        self.assertNotIn("architect_pending_hire_status", prompt)
        self.assertNotIn("architect_engineer_message", prompt)
        self.assertNotIn("architect_task_create", prompt)
        self.assertNotIn("architect_task_reassign", prompt)
        self.assertNotIn("architect_task_move", prompt)
        self.assertNotIn("architect_deploy_state", prompt)
        self.assertNotIn("engineer_merge", prompt)
        self.assertNotIn("Hiring discipline", prompt)
        self.assertNotIn("Routing over instructing", prompt)
        self.assertNotIn("active engineers, open scope, pending hires", prompt)

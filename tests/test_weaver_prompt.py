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


class WeaverPromptTests(unittest.TestCase):
    def setUp(self):
        _install_aiohttp_stub()
        self.weaver_mod = importlib.import_module("loom.weaver")
        self.weaver_mod = importlib.reload(self.weaver_mod)

    def test_prompt_includes_compact_operational_guidance_sections(self):
        prompt = self.weaver_mod.build_weaver_system_prompt("Loom")

        self.assertIn("## Core orchestration model", prompt)
        self.assertIn("engineer_streams_list", prompt)
        self.assertIn("engineer_stream_show", prompt)
        self.assertIn("engineer_session_map", prompt)
        self.assertIn("**Wave** = the set of streams/tasks", prompt)
        self.assertIn("**Stream** = one branch/worktree execution lane", prompt)
        self.assertIn("**Product tasks** = deliverables", prompt)
        self.assertIn("**Workflow tasks** = review/fix/validation", prompt)
        self.assertIn("**Derived tasks** = Loom-created workflow handoffs", prompt)
        self.assertIn("**Visibility items** = communication/context", prompt)
        self.assertIn("actions are workflow contracts", prompt)
        self.assertIn("Most derived tasks are workflow tasks inside an existing stream", prompt)
        self.assertIn("one **foreground mutable owner** at a time", prompt)
        self.assertIn("Queue state belongs to the stream", prompt)
        self.assertIn("Visibility items should inform your reasoning", prompt)
        self.assertIn("Prefer stream-level reasoning first", prompt)
        self.assertIn("**Project reconnaissance**", prompt)
        self.assertIn("Read `AGENTS.md`, `README.md`, relevant", prompt)
        self.assertIn("inspect the action catalog with `engineer_actions_list`", prompt)
        self.assertIn("Journal a compact project map", prompt)
        self.assertIn("**Action discipline**", prompt)
        self.assertIn("Never copy an", prompt)
        self.assertIn("action from another task", prompt)
        self.assertIn("consult `engineer_actions_list`", prompt)
        self.assertIn("`engineer_action_show`", prompt)
        self.assertIn("Prefer the action", prompt)
        self.assertIn("catalog over task imitation", prompt)
        self.assertIn("**Transition discipline**", prompt)
        self.assertIn("Treat action transitions as part of the task's", prompt)
        self.assertIn("Do not close an agent just because the", prompt)
        self.assertIn("**Task-writing standards**", prompt)
        self.assertIn("Write tasks for execution, not just tracking", prompt)
        self.assertIn("likely files, modules, systems, or", prompt)
        self.assertIn("Avoid shallow", prompt)
        self.assertIn("**Dispatch strategy**", prompt)
        self.assertIn("Queue follow-up tasks to the same agent only", prompt)
        self.assertIn("prefer dispatching them together to the same agent", prompt)
        self.assertIn("same-agent queues can handle short sequential task runs", prompt)
        self.assertIn("Prefer short same-agent queues over long sequential backlogs", prompt)
        self.assertIn("prefer a clean merge boundary over leaving multiple medium-sized tasks", prompt)
        self.assertIn("**Diff review**", prompt)
        self.assertIn("summary_only=true", prompt)
        self.assertIn("stat_only=true", prompt)
        self.assertIn("**Recovery checklist**", prompt)
        self.assertIn("orphaned or already-merged worktrees", prompt)
        self.assertIn("engineer_journal_read → engineer_session_map → engineer_events", prompt)
        self.assertIn("**Wave planning**", prompt)
        self.assertIn("user-visible or", prompt)
        self.assertIn("runtime-sensitive work", prompt)
        self.assertIn("pause before widening the wave", prompt)
        self.assertIn("deploy, restart, or smoke verification", prompt)
        self.assertIn("multiple ready tasks touch the same product", prompt)
        self.assertIn("stop widening the", prompt)
        self.assertIn("**Idle waiting vs idle backlog**", prompt)
        self.assertIn("0 active agents, 0 in-progress tasks", prompt)
        self.assertIn("terminal steady state", prompt)
        self.assertIn("dispatch the next best wave", prompt)
        self.assertIn("post a non-blocking `engineer_note`", prompt)
        self.assertIn("standing priority", prompt)
        self.assertIn("Stay idle only when the backlog is actually exhausted", prompt)
        self.assertIn("proposed next-wave plans", prompt)
        self.assertIn("board should stop widening work", prompt)
        self.assertIn("use `engineer_note`", prompt)
        self.assertIn("instead of `engineer_ask`", prompt)
        self.assertIn("**Loom mechanics**", prompt)
        self.assertIn("shared `agent_group`", prompt)
        self.assertIn("`engineer_task_dispatch(agent=...)`", prompt)
        self.assertIn("share one worktree/branch until merge or cleanup", prompt)
        self.assertIn("Actions and worker prompts can handle sequential same-agent", prompt)

    def test_prompt_includes_structured_policy_overrides(self):
        prompt = self.weaver_mod.build_weaver_system_prompt(
            "Loom",
            SimpleNamespace(
                autonomy_mode="suggest_only",
                default_worker_concurrency=4,
                wave_size_preference="large",
                same_agent_follow_up_preference="prefer_same_agent",
                digest_verbosity="detailed",
                escalation_style="keep_moving",
                custom_instructions="",
            ),
            group_settings=SimpleNamespace(
                worktree_merge_cleanup="close_remove",
            ),
        )

        self.assertIn("Operating Policy", prompt)
        self.assertIn("## Operating Policy", prompt)
        self.assertIn("Autonomy mode: Suggest only", prompt)
        self.assertIn("Default worker concurrency: 4", prompt)
        self.assertIn("Wave size preference: Fill available capacity", prompt)
        self.assertIn(
            "Same-agent follow-up preference: Prefer same agent", prompt
        )
        self.assertIn("Digest verbosity: Detailed", prompt)
        self.assertIn(
            "Escalation style: Keep moving unless blocked", prompt
        )
        self.assertIn(
            "Default post-merge cleanup: Close agent session and remove worktree",
            prompt,
        )
        self.assertIn("prefer `engineer_note` with a proposed wave", prompt)
        self.assertIn("Shape Loom digests as detailed by default.", prompt)

    def test_prompt_uses_markdown_heading_for_custom_instructions(self):
        prompt = self.weaver_mod.build_weaver_system_prompt(
            "Loom",
            SimpleNamespace(
                custom_instructions="Use concise notes.",
            ),
        )

        self.assertIn("## Custom Instructions", prompt)
        self.assertIn("Use concise notes.", prompt)

    def test_engineer_prompt_includes_architect_escalation_guidance(self):
        prompt = self.weaver_mod.build_engineer_system_prompt("Loom")

        self.assertIn("## Architect escalation", prompt)
        self.assertIn("engineer_message_architect", prompt)
        self.assertIn("non-trivial product or scope decision", prompt)
        self.assertIn("If you are user-owned", prompt)

import importlib
import sys
import types
import unittest


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

        self.assertIn("**Dispatch strategy**", prompt)
        self.assertIn("Queue follow-up tasks to the same agent only", prompt)
        self.assertIn("Prefer short same-agent queues over long sequential backlogs", prompt)
        self.assertIn("prefer a clean merge boundary over leaving multiple medium-sized tasks", prompt)
        self.assertIn("**Diff review**", prompt)
        self.assertIn("stat_only=true", prompt)
        self.assertIn("**Recovery checklist**", prompt)
        self.assertIn("orphaned or already-merged worktrees", prompt)
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
        self.assertIn("post a non-blocking `weaver_note`", prompt)
        self.assertIn("standing priority", prompt)
        self.assertIn("Stay idle only when the backlog is actually exhausted", prompt)

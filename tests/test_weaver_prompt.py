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
        self.assertIn("queue small follow-up tasks to", prompt)
        self.assertIn("the same agent when files and decisions overlap", prompt)
        self.assertIn("**Diff review**", prompt)
        self.assertIn("stat_only=true", prompt)
        self.assertIn("**Recovery checklist**", prompt)
        self.assertIn("orphaned or already-merged worktrees", prompt)
        self.assertIn("**Wave planning**", prompt)
        self.assertIn("rotate in queued", prompt)
        self.assertIn("tasks as agents finish", prompt)

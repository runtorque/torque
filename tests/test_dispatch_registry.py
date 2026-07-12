"""Declarative command/tool handler registry tests."""

import unittest

from torque.dispatch_registry import AsyncHandlerRegistry


class AsyncHandlerRegistryTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_and_async_prefix_routes_dispatch(self):
        registry = AsyncHandlerRegistry()
        registry.register_many(
            {"area_list", "area_show"},
            lambda payload: {"type": payload["cmd"]},
            label="areas",
        )

        async def thinking_handler(name, value):
            return f"{name}:{value}"

        registry.register_prefix(
            "thinking_", thinking_handler, label="thinking"
        )

        exact = await registry.dispatch("area_show", {"cmd": "area_show"})
        prefix = await registry.dispatch(
            "thinking_list", "thinking_list", "ok"
        )
        missing = await registry.dispatch("unknown")

        self.assertTrue(exact.handled)
        self.assertEqual(exact.value, {"type": "area_show"})
        self.assertTrue(prefix.handled)
        self.assertEqual(prefix.value, "thinking_list:ok")
        self.assertFalse(missing.handled)
        self.assertIsNone(missing.value)

    async def test_longest_prefix_wins(self):
        registry = AsyncHandlerRegistry()
        registry.register_prefix("task_", lambda: "task")
        registry.register_prefix("task_proposal_", lambda: "proposal")

        result = await registry.dispatch("task_proposal_list")

        self.assertEqual(result.value, "proposal")

    def test_duplicate_and_invalid_routes_are_rejected(self):
        registry = AsyncHandlerRegistry()
        registry.register_many({"area_list"}, lambda: None)
        with self.assertRaisesRegex(ValueError, "already registered"):
            registry.register_many({"area_list"}, lambda: None)
        with self.assertRaisesRegex(ValueError, "non-empty"):
            registry.register_many({""}, lambda: None)
        with self.assertRaisesRegex(ValueError, "non-empty"):
            registry.register_prefix("", lambda: None)


if __name__ == "__main__":
    unittest.main()

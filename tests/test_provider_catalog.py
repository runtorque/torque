import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest import mock

import torque.adapters as adapters
from torque.provider_catalog import discover_codex_models
from torque.provider_catalog import _resolve_codex_executable


class CodexProviderCatalogTests(unittest.TestCase):
    def test_claude_code_metadata_uses_the_static_builtin_catalog(self):
        providers = adapters.get_providers()
        claude = next(
            provider for provider in providers
            if provider["name"] == "claude-code"
        )

        self.assertEqual(claude["model_catalog_source"], "built-in")
        self.assertEqual(
            [model["id"] for model in claude["models"]],
            [
                "claude-haiku-4-5",
                "claude-sonnet-4-6",
                "claude-sonnet-5",
                "claude-opus-4-8",
                "claude-opus-5",
                "claude-fable-5",
            ],
        )

    def test_executable_resolution_uses_common_install_when_path_is_stripped(self):
        with mock.patch(
            "torque.provider_catalog.shutil.which",
            return_value=None,
        ), mock.patch(
            "torque.provider_catalog.Path.is_file",
            autospec=True,
            side_effect=lambda path: str(path) == "/opt/homebrew/bin/codex",
        ), mock.patch(
            "torque.provider_catalog.os.access",
            return_value=True,
        ):
            self.assertEqual(
                _resolve_codex_executable(),
                "/opt/homebrew/bin/codex",
            )

    def test_app_server_catalog_normalizes_visible_models_and_efforts(self):
        payload = {
            "data": [
                {
                    "id": "gpt-hidden",
                    "displayName": "Hidden",
                    "hidden": True,
                },
                {
                    "id": "gpt-5.6-terra",
                    "displayName": "GPT-5.6-Terra",
                    "description": "Balanced coding model.",
                    "isDefault": False,
                    "defaultReasoningEffort": "medium",
                    "supportedReasoningEfforts": [
                        {
                            "reasoningEffort": "medium",
                            "description": "Balanced",
                        },
                        {
                            "reasoningEffort": "xhigh",
                            "description": "Extra high",
                        },
                    ],
                },
                {
                    "id": "gpt-5.6-sol",
                    "displayName": "GPT-5.6-Sol",
                    "isDefault": True,
                    "defaultReasoningEffort": "low",
                    "supportedReasoningEfforts": [
                        {"reasoningEffort": "low"},
                        {"reasoningEffort": "high"},
                    ],
                },
            ],
        }

        with mock.patch(
            "torque.provider_catalog._codex_app_server_model_list",
            return_value=payload,
        ):
            models = discover_codex_models(executable="/usr/local/bin/codex")

        self.assertEqual(
            [model["id"] for model in models],
            ["gpt-5.6-sol", "gpt-5.6-terra"],
        )
        self.assertTrue(models[0]["is_default"])
        self.assertEqual(models[1]["default_reasoning_effort"], "medium")
        self.assertEqual(
            models[1]["reasoning_efforts"],
            [
                {"value": "medium", "description": "Balanced"},
                {"value": "xhigh", "description": "Extra high"},
            ],
        )

    def test_debug_catalog_is_used_when_app_server_is_unavailable(self):
        debug_payload = {
            "models": [
                {
                    "slug": "gpt-5.6-sol",
                    "display_name": "GPT-5.6-Sol",
                    "visibility": "list",
                    "priority": 1,
                    "default_reasoning_level": "low",
                    "supported_reasoning_levels": [
                        {"effort": "low", "description": "Fast"},
                        {"effort": "high", "description": "Deep"},
                    ],
                },
                {
                    "slug": "gpt-hidden",
                    "visibility": "hide",
                    "priority": 2,
                },
            ],
        }
        completed = SimpleNamespace(
            returncode=0,
            stdout=json.dumps(debug_payload),
        )

        with mock.patch(
            "torque.provider_catalog._codex_app_server_model_list",
            side_effect=RuntimeError("unsupported"),
        ), mock.patch(
            "torque.provider_catalog.subprocess.run",
            return_value=completed,
        ) as run:
            models = discover_codex_models(executable="/usr/local/bin/codex")

        run.assert_called_once()
        self.assertEqual([model["id"] for model in models], ["gpt-5.6-sol"])
        self.assertEqual(
            [item["value"] for item in models[0]["reasoning_efforts"]],
            ["low", "high"],
        )

    def test_async_provider_metadata_is_cached_and_attached_to_codex(self):
        old_models = dict(adapters._provider_models_cache)
        old_refreshed = adapters._provider_catalog_refreshed_at
        old_task = adapters._provider_catalog_refresh_task
        discovered = [{
            "id": "gpt-5.6-sol",
            "display_name": "GPT-5.6-Sol",
            "description": "",
            "is_default": True,
            "default_reasoning_effort": "low",
            "reasoning_efforts": [{"value": "low", "description": ""}],
        }]
        try:
            adapters._provider_models_cache.clear()
            adapters._provider_catalog_refreshed_at = 0.0
            adapters._provider_catalog_refresh_task = None
            with mock.patch(
                "torque.adapters.discover_codex_models",
                return_value=discovered,
            ) as discover:
                first = asyncio.run(adapters.get_providers_async())
                second = asyncio.run(adapters.get_providers_async())

            discover.assert_called_once_with()
            codex = next(provider for provider in first if provider["name"] == "codex")
            self.assertEqual(codex["models"], discovered)
            self.assertEqual(codex["model_catalog_source"], "detected")
            self.assertEqual(first, second)
        finally:
            adapters._provider_models_cache.clear()
            adapters._provider_models_cache.update(old_models)
            adapters._provider_catalog_refreshed_at = old_refreshed
            adapters._provider_catalog_refresh_task = old_task

    def test_empty_discovery_result_retries_after_short_failure_ttl(self):
        old_models = dict(adapters._provider_models_cache)
        old_refreshed = adapters._provider_catalog_refreshed_at
        old_task = adapters._provider_catalog_refresh_task
        try:
            adapters._provider_models_cache.clear()
            adapters._provider_catalog_refreshed_at = 0.0
            adapters._provider_catalog_refresh_task = None
            with mock.patch(
                "torque.adapters.discover_codex_models",
                side_effect=[[], [{
                    "id": "gpt-retry",
                    "display_name": "GPT Retry",
                    "description": "",
                    "is_default": True,
                    "default_reasoning_effort": "medium",
                    "reasoning_efforts": [],
                }]],
            ) as discover:
                first = asyncio.run(adapters.get_providers_async())
                adapters._provider_catalog_refreshed_at -= 2
                second = asyncio.run(adapters.get_providers_async())

            self.assertNotIn(
                "models",
                next(provider for provider in first if provider["name"] == "codex"),
            )
            self.assertEqual(discover.call_count, 2)
            self.assertEqual(
                next(provider for provider in second if provider["name"] == "codex")[
                    "models"
                ][0]["id"],
                "gpt-retry",
            )
        finally:
            adapters._provider_models_cache.clear()
            adapters._provider_models_cache.update(old_models)
            adapters._provider_catalog_refreshed_at = old_refreshed
            adapters._provider_catalog_refresh_task = old_task


if __name__ == "__main__":
    unittest.main()

import asyncio
import importlib
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import yaml

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub


class ServerModuleExtractionTests(unittest.TestCase):
    def setUp(self):
        install_aiohttp_stub()
        self.server_actions = importlib.import_module('torque.server_actions')
        self.server_actions = importlib.reload(self.server_actions)
        self.server_dispatch = importlib.import_module('torque.server_dispatch')
        self.server_dispatch = importlib.reload(self.server_dispatch)
        self.server_worktrees = importlib.import_module('torque.server_worktrees')
        self.server_worktrees = importlib.reload(self.server_worktrees)
        self.state_mod = importlib.import_module('torque.state')
        self.state_mod = importlib.reload(self.state_mod)
        self.server_mod = importlib.import_module('torque.server')
        self.server_mod = importlib.reload(self.server_mod)

    def test_parse_unified_diff_summarizes_added_file(self):
        diff_text = (
            'diff --git a/new.txt b/new.txt\n'
            'new file mode 100644\n'
            'index 0000000..1111111\n'
            '--- /dev/null\n'
            '+++ b/new.txt\n'
            '@@ -0,0 +1,2 @@\n'
            '+hello\n'
            '+world\n'
        )

        files = self.server_worktrees._parse_unified_diff(diff_text)

        self.assertEqual(len(files), 1)
        self.assertEqual(files[0]['path'], 'new.txt')
        self.assertEqual(files[0]['status'], 'added')
        self.assertEqual(files[0]['insertions'], 2)
        self.assertEqual(files[0]['deletions'], 0)

    def _task_with_github_sync(self, state, task_id, *,
                               repo="acme/repo", number=166):
        task = state.board_add_task(
            f"Task {task_id}",
            "g",
            lane="Backlog",
            id=task_id,
            board_sync={
                "provider": "github",
                "github": {
                    "issue_repo": repo,
                    "issue_number": number,
                    "issue_url": f"https://github.com/{repo}/issues/{number}",
                },
            },
        )
        self.assertIsNotNone(task)
        # Auto-synced tasks populate board_sync.github.* even when top-level
        # provider/external fields are blank.
        task.provider = ""
        task.external_id = ""
        task.external_url = ""
        return task

    def _rewrite_state(self):
        state = self.state_mod.MatrixState()
        state.add_group("g")
        return state

    def test_normalize_engineer_specialization_selection_validates_and_dedupes(self):
        normalize = self.server_mod._normalize_engineer_specialization_selection

        self.assertEqual(
            normalize(
                ["ui-ux", "runtime-pty", "ui-ux", "", "prompts-config"],
                valid_names={"ui-ux", "runtime-pty", "prompts-config"},
            ),
            ["ui-ux", "runtime-pty", "prompts-config"],
        )
        with self.assertRaises(ValueError):
            normalize(["ui-ux", "security-focus"],
                      valid_names={"ui-ux"})
        with self.assertRaisesRegex(ValueError, "Valid specializations: ui-ux"):
            normalize(["security-focus"], valid_names=["ui-ux"])
        with self.assertRaises(ValueError):
            normalize("ui-ux", valid_names={"ui-ux"})

    def test_command_log_redactor_masks_nested_secret_fields(self):
        raw_values = [
            "sk-ant-raw-secret",
            "Bearer raw-token",
            "nested-password",
            "private-key-material",
            "top-token",
        ]
        redacted = self.server_mod._redact_command_log_payload({
            "cmd": "update_ai_settings",
            "settings": {"enabled": True},
            "secrets": {
                "anthropic": {"api_key": raw_values[0]},
                "nested": [
                    {"Authorization": raw_values[1]},
                    {"password": raw_values[2]},
                ],
            },
            "private_key": raw_values[3],
            "token": raw_values[4],
            "safe": {"value": "visible"},
        })

        dumped = json.dumps(redacted, sort_keys=True)
        for raw in raw_values:
            self.assertNotIn(raw, dumped)
        self.assertNotIn("cmd", redacted)
        self.assertEqual(redacted["safe"]["value"], "visible")
        self.assertEqual(redacted["secrets"], "[REDACTED]")
        self.assertEqual(redacted["private_key"], "[REDACTED]")
        self.assertEqual(redacted["token"], "[REDACTED]")

        nested = self.server_mod._redact_command_log_payload({
            "cmd": "other",
            "payload": {
                "items": [
                    {"api_key": raw_values[0]},
                    {"Authorization": raw_values[1]},
                    {"password": raw_values[2]},
                ]
            },
        })
        self.assertEqual(
            nested["payload"]["items"][0]["api_key"],
            "[REDACTED]",
        )
        self.assertEqual(
            nested["payload"]["items"][1]["Authorization"],
            "[REDACTED]",
        )
        self.assertEqual(
            nested["payload"]["items"][2]["password"],
            "[REDACTED]",
        )

    def test_get_ai_settings_returns_redacted_contract_shape(self):
        from torque.db import TorqueDB

        with tempfile.TemporaryDirectory() as tmp:
            db = TorqueDB(Path(tmp) / "torque.db")
            db.init()
            db.save_ai_provider_secret("anthropic", "sk-ant-secret-1234")
            state = self.state_mod.MatrixState(db=db)
            state.update_global_settings(
                ai_enabled=True,
                ai_generation_provider="anthropic",
                ai_anthropic_model="claude-test",
                ai_index_corpus={"tasks": False},
            )

            with mock.patch.object(
                self.server_mod.ai_deps,
                "embeddings_dependency_status",
                return_value="available",
            ):
                response = self.server_mod._build_ai_settings_response(state, db)
            db.close()

        self.assertEqual(response["type"], "ai_settings")
        self.assertEqual(response["schema_version"], 1)
        settings = response["settings"]
        self.assertEqual(
            set(settings),
            {
                "enabled",
                "generation",
                "embeddings",
                "index",
                "boot_summary",
                "metering",
            },
        )
        self.assertTrue(settings["enabled"])
        generation = settings["generation"]
        self.assertEqual(generation["provider"], "anthropic")
        self.assertEqual(
            generation["providers"],
            ["anthropic", "openai_compatible"],
        )
        self.assertEqual(generation["anthropic"]["model"], "claude-test")
        self.assertEqual(
            generation["anthropic"]["key"],
            {"configured": True, "last4": "1234",
             "updated_at": generation["anthropic"]["key"]["updated_at"]},
        )
        self.assertGreater(generation["anthropic"]["key"]["updated_at"], 0)
        self.assertEqual(
            set(generation["openai_compatible"]),
            {"base_url", "model", "key"},
        )
        self.assertEqual(
            generation["openai_compatible"]["key"],
            {"configured": False, "last4": "", "updated_at": 0},
        )
        self.assertEqual(settings["embeddings"]["model_id"], "BAAI/bge-m3")
        self.assertEqual(settings["embeddings"]["desired_model_id"], "BAAI/bge-m3")
        self.assertEqual(settings["embeddings"]["dependency"]["status"], "available")
        self.assertEqual(settings["index"]["status"], "not_built")
        self.assertFalse(settings["index"]["corpus"]["tasks"])
        self.assertEqual(settings["index"]["counts"]["chunks"], 0)
        self.assertEqual(settings["boot_summary"]["status"], "empty")
        self.assertEqual(settings["boot_summary"]["min_interval_seconds"], 600)
        self.assertEqual(settings["boot_summary"]["max_refreshes_per_hour"], 20)
        self.assertEqual(settings["metering"]["calls_24h"], 0)
        self.assertNotIn(
            "sk-ant-secret-1234",
            json.dumps(response, sort_keys=True),
        )

    def test_update_ai_settings_stores_secret_without_echo_or_snapshot_leak(self):
        from torque.db import TorqueDB

        raw_key = "sk-ant-super-secret-4242"
        with tempfile.TemporaryDirectory() as tmp:
            db = TorqueDB(Path(tmp) / "torque.db")
            db.init()
            state = self.state_mod.MatrixState(db=db)

            response = self.server_mod._apply_ai_settings_update_command(
                state,
                db,
                {
                    "cmd": "update_ai_settings",
                    "settings": {
                        "enabled": True,
                        "generation": {
                            "provider": "anthropic",
                            "anthropic": {"model": "claude-test"},
                            "openai_compatible": {
                                "base_url": "http://localhost:11434/v1",
                                "model": "local-model",
                            },
                        },
                        "embeddings": {"model_id": "custom/model"},
                        "index": {"corpus": {"tasks": False}},
                        "boot_summary": {
                            "enabled": False,
                            "min_interval_seconds": 45,
                            "max_refreshes_per_hour": 7,
                        },
                    },
                    "secrets": {
                        "anthropic": {"api_key": raw_key},
                        "openai_compatible": {"api_key": ""},
                    },
                },
            )

            self.assertEqual(db.read_ai_provider_secret("anthropic"), raw_key)
            self.assertEqual(db.read_ai_provider_secret("openai_compatible"), "")
            serialized = json.dumps(response, sort_keys=True)
            deltas = json.dumps(state._delta_ops, sort_keys=True)
            state_snapshot = json.dumps(state.to_dict(), sort_keys=True)
            compact_snapshot = json.dumps(state.to_dict_compact(), sort_keys=True)
            offline_snapshot = json.dumps(db.load_all(), sort_keys=True)
            db.close()

        for surface in (
            serialized,
            deltas,
            state_snapshot,
            compact_snapshot,
            offline_snapshot,
        ):
            self.assertNotIn(raw_key, surface)
            self.assertNotIn("api_key", surface)
        self.assertEqual(
            response["settings"]["generation"]["anthropic"]["key"]["last4"],
            "4242",
        )
        self.assertTrue(
            response["settings"]["generation"]["anthropic"]["key"]["configured"]
        )
        ai_deltas = [
            op for op in state._delta_ops
            if op.get("op") == "ai_settings_update"
        ]
        self.assertEqual(len(ai_deltas), 1)
        self.assertEqual(
            ai_deltas[0]["settings"]["generation"]["anthropic"]["key"]["last4"],
            "4242",
        )
        self.assertEqual(state.global_settings.ai_embedding_model, "custom/model")
        self.assertFalse(state.global_settings.ai_index_corpus["tasks"])
        self.assertFalse(state.global_settings.ai_boot_summary_enabled)
        self.assertEqual(
            state.global_settings.ai_boot_summary_min_interval_seconds,
            45,
        )
        self.assertEqual(
            state.global_settings.ai_boot_summary_max_refreshes_per_hour,
            7,
        )

    def test_update_ai_settings_clear_secret_returns_unconfigured_metadata(self):
        from torque.db import TorqueDB

        raw_key = "sk-ant-clear-me-3131"
        with tempfile.TemporaryDirectory() as tmp:
            db = TorqueDB(Path(tmp) / "torque.db")
            db.init()
            db.save_ai_provider_secret("anthropic", raw_key)
            state = self.state_mod.MatrixState(db=db)

            response = self.server_mod._apply_ai_settings_update_command(
                state,
                db,
                {
                    "cmd": "update_ai_settings",
                    "clear_secrets": ["anthropic"],
                    "secrets": {"anthropic": {"api_key": ""}},
                },
            )

            self.assertEqual(db.read_ai_provider_secret("anthropic"), "")
            serialized = json.dumps(response, sort_keys=True)
            deltas = json.dumps(state._delta_ops, sort_keys=True)
            db.close()

        self.assertNotIn(raw_key, serialized)
        self.assertNotIn(raw_key, deltas)
        self.assertEqual(
            response["settings"]["generation"]["anthropic"]["key"],
            {"configured": False, "last4": "", "updated_at": 0},
        )

    def test_pr_task_ref_rewrite_uses_same_repo_short_ref(self):
        state = self._rewrite_state()
        self._task_with_github_sync(
            state,
            "TORQUE:680",
            repo="acme/repo",
            number=166,
        )

        text, diagnostics = (
            self.server_worktrees._rewrite_pr_torque_task_refs(
                "Implements TORQUE:680.",
                state=state,
                base_repo="acme/repo",
            )
        )

        self.assertEqual(text, "Implements #166.")
        self.assertEqual(diagnostics["replaced"][0]["ref"], "#166")

    def test_pr_task_ref_rewrite_uses_cross_repo_ref(self):
        state = self._rewrite_state()
        self._task_with_github_sync(
            state,
            "TORQUE:680",
            repo="runtorque/torque",
            number=166,
        )

        text, _diagnostics = (
            self.server_worktrees._rewrite_pr_torque_task_refs(
                "Implements TORQUE:680.",
                state=state,
                base_repo="acme/repo",
            )
        )

        self.assertEqual(text, "Implements runtorque/torque#166.")

    def test_pr_closing_issue_collection_uses_nested_board_sync_for_derived_tasks(self):
        state = self._rewrite_state()
        root = self._task_with_github_sync(
            state,
            "TORQUE:680",
            repo="acme/repo",
            number=166,
        )
        derived = self._task_with_github_sync(
            state,
            "TORQUE:680:1",
            repo="acme/repo",
            number=167,
        )
        derived.parent_task_id = root.id
        derived.pipeline_depth = 1
        derived.pipeline_root_id = root.id

        issues = self.server_worktrees._collect_linked_github_issues(
            [derived, root],
            base_repo="acme/repo",
        )

        self.assertEqual(
            [(issue["task_id"], issue["issue_number"]) for issue in issues],
            [("TORQUE:680:1", 167), ("TORQUE:680", 166)],
        )
        self.assertEqual(issues[0]["base_repo"], "acme/repo")

    def test_pr_task_ref_rewrite_parses_url_only_sync_mapping(self):
        state = self._rewrite_state()
        task = state.board_add_task(
            "Task with URL-only GitHub sync",
            "g",
            lane="Backlog",
            id="TORQUE:680",
            board_sync={
                "provider": "github",
                "github": {
                    "issue_url": "https://github.com/acme/repo/issues/166",
                },
            },
        )
        task.provider = ""
        task.external_id = ""
        task.external_url = ""

        text, _diagnostics = (
            self.server_worktrees._rewrite_pr_torque_task_refs(
                "Implements TORQUE:680.",
                state=state,
                base_repo="",
            )
        )

        self.assertEqual(text, "Implements acme/repo#166.")

    def test_pr_task_ref_formatter_falls_back_to_issue_url(self):
        ref = self.server_worktrees._github_issue_ref_for_pr_text({
            "issue_url": "https://github.com/acme/repo/issues/166",
        }, base_repo="acme/repo")

        self.assertEqual(ref, "https://github.com/acme/repo/issues/166")

    def test_pr_task_ref_rewrite_leaves_unknown_and_unmapped_refs(self):
        state = self._rewrite_state()
        state.board_add_task(
            "Unmapped task",
            "g",
            lane="Backlog",
            id="TORQUE:680",
        )

        text, diagnostics = (
            self.server_worktrees._rewrite_pr_torque_task_refs(
                "Refs TORQUE:680 and TORQUE:999.",
                state=state,
                base_repo="acme/repo",
            )
        )

        self.assertEqual(text, "Refs TORQUE:680 and TORQUE:999.")
        self.assertEqual(
            sorted({item["task_id"] for item in diagnostics["unresolved"]}),
            ["TORQUE:680", "TORQUE:999"],
        )

    def test_pr_task_ref_rewrite_handles_multiple_refs_and_derived_ids(self):
        state = self._rewrite_state()
        self._task_with_github_sync(
            state,
            "TORQUE:680",
            repo="acme/repo",
            number=166,
        )
        self._task_with_github_sync(
            state,
            "TORQUE:680:1",
            repo="acme/repo",
            number=167,
        )

        text, diagnostics = (
            self.server_worktrees._rewrite_pr_torque_task_refs(
                "Refs TORQUE:680, TORQUE:680:1, and TORQUE:680 again.",
                state=state,
                base_repo="acme/repo",
            )
        )

        self.assertEqual(text, "Refs #166, #167, and #166 again.")
        self.assertEqual(
            [item["ref"] for item in diagnostics["replaced"]],
            ["#166", "#167", "#166"],
        )

    def test_pr_task_ref_rewrite_skips_fenced_and_inline_code(self):
        state = self._rewrite_state()
        self._task_with_github_sync(
            state,
            "TORQUE:680",
            repo="acme/repo",
            number=166,
        )
        body = (
            "Outside TORQUE:680.\n"
            "`inline TORQUE:680`\n"
            "```sh\n"
            "echo TORQUE:680\n"
            "```\n"
            "Outside again TORQUE:680."
        )

        text, diagnostics = (
            self.server_worktrees._rewrite_pr_torque_task_refs(
                body,
                state=state,
                base_repo="acme/repo",
            )
        )

        self.assertEqual(
            text,
            (
                "Outside #166.\n"
                "`inline TORQUE:680`\n"
                "```sh\n"
                "echo TORQUE:680\n"
                "```\n"
                "Outside again #166."
            ),
        )
        self.assertEqual(diagnostics["skipped_inline_refs"], 1)
        self.assertEqual(diagnostics["skipped_fenced_refs"], 1)

    def test_pr_task_ref_rewrite_preserves_close_keywords_without_injecting(self):
        state = self._rewrite_state()
        self._task_with_github_sync(
            state,
            "TORQUE:680",
            repo="acme/repo",
            number=166,
        )

        plain, _plain_diagnostics = (
            self.server_worktrees._rewrite_pr_torque_task_refs(
                "Plain TORQUE:680.",
                state=state,
                base_repo="acme/repo",
            )
        )
        closing, _closing_diagnostics = (
            self.server_worktrees._rewrite_pr_torque_task_refs(
                "Closes TORQUE:680.",
                state=state,
                base_repo="acme/repo",
            )
        )

        self.assertEqual(plain, "Plain #166.")
        self.assertNotIn("closes", plain.lower())
        self.assertEqual(closing, "Closes #166.")

    def test_pr_task_ref_rewrite_is_idempotent(self):
        state = self._rewrite_state()
        self._task_with_github_sync(
            state,
            "TORQUE:680",
            repo="acme/repo",
            number=166,
        )

        first, _first_diagnostics = (
            self.server_worktrees._rewrite_pr_torque_task_refs(
                "Refs TORQUE:680.",
                state=state,
                base_repo="acme/repo",
            )
        )
        second, second_diagnostics = (
            self.server_worktrees._rewrite_pr_torque_task_refs(
                first,
                state=state,
                base_repo="acme/repo",
            )
        )

        self.assertEqual(second, "Refs #166.")
        self.assertEqual(second_diagnostics["replaced"], [])

    def test_relay_agent_roster_is_group_scoped_and_excludes_tombstones(self):
        state = self.state_mod.MatrixState()
        state.add_group('g1')
        state.add_group('g2')
        state.active_group = 'g1'

        architect = state.add_agent(name='Architect', group='g1')
        architect.kind = 'architect'
        engineer = state.add_agent(name='Engineer', group='g1')
        engineer.kind = 'engineer'
        unknown = state.add_agent(name='Unknown', group='g1')
        unknown.kind = ''
        terminal = state.add_terminal(
            name='Terminal', group='g1', parent_id=engineer.id)
        terminal.kind = 'terminal'
        tombstoned = state.add_agent(name='Deleted Worker', group='g1')
        tombstoned.kind = 'worker'
        tombstoned.deleted_at = 123.0
        out_of_group = state.add_agent(name='Other Engineer', group='g2')
        out_of_group.kind = 'engineer'

        roster = self.server_mod._relay_agent_roster(state)

        self.assertEqual(roster, [
            {'id': architect.id, 'name': 'Architect', 'kind': 'architect'},
            {'id': engineer.id, 'name': 'Engineer', 'kind': 'engineer'},
            {'id': unknown.id, 'name': 'Unknown', 'kind': ''},
            {'id': terminal.id, 'name': 'Terminal', 'kind': 'terminal'},
        ])

    def test_relay_agent_state_snapshot_is_group_scoped_and_ephemeral_only(self):
        state = self.state_mod.MatrixState()
        state.add_group('g1')
        state.add_group('g2')
        state.active_group = 'g1'

        worker = state.add_agent(name='Worker', group='g1')
        worker.kind = 'worker'
        worker.agent_type = 'codex'
        worker.status = 'running'
        worker.activity_detail = 'torque_context'
        worker.needs_attention = True
        worker.context_window = {
            'used_pct': 42.2,
            'used_tokens': 12345,
            'model': 'claude',
        }
        worker.provider_usage = {
            'five_hour': {
                'available': True,
                'used_percentage': 12,
                'resets_at': '2026-05-26T05:00:00Z',
            },
        }
        tombstoned = state.add_agent(name='Deleted Worker', group='g1')
        tombstoned.kind = 'worker'
        tombstoned.deleted_at = 123.0
        other = state.add_agent(name='Other Worker', group='g2')
        other.kind = 'worker'

        snapshot = self.server_mod._relay_agent_state_snapshot(state)

        self.assertEqual(len(snapshot), 1)
        self.assertEqual(snapshot[0]['id'], worker.id)
        self.assertEqual(snapshot[0]['name'], 'Worker')
        self.assertEqual(snapshot[0]['kind'], 'worker')
        self.assertEqual(snapshot[0]['agent_type'], 'codex')
        self.assertEqual(snapshot[0]['status'], 'running')
        self.assertEqual(snapshot[0]['activity_detail'], 'torque_context')
        self.assertTrue(snapshot[0]['needs_attention'])
        self.assertEqual(snapshot[0]['context_window']['used_pct'], 42.2)
        self.assertEqual(
            snapshot[0]['provider_usage']['five_hour']['used_percentage'],
            12,
        )

    def test_action_to_yaml_keeps_prompt_as_block_scalar(self):
        yaml_text = self.server_actions._action_to_yaml('review/code', {
            'description': 'Review code',
            'prompt': 'Line 1\nLine 2',
            'labels': ['review'],
        })

        self.assertIn('name: review/code', yaml_text)
        self.assertIn('prompt: |', yaml_text)
        self.assertIn('  Line 1', yaml_text)
        self.assertIn('labels:', yaml_text)

    def test_action_to_yaml_round_trips_auto_close_on_done(self):
        yaml_text = self.server_actions._action_to_yaml('feature/review', {
            'description': 'Review code',
            'auto_close_on_done': True,
            'prompt': '{{ TASK }}\n',
        })

        self.assertIn('auto_close_on_done: true', yaml_text)

    def test_action_to_yaml_round_trips_disable_role_preamble(self):
        yaml_text = self.server_actions._action_to_yaml('diagnostic/run', {
            'description': 'Diagnostic action',
            'disable_role_preamble': True,
            'prompt': '{{ TASK }}\n',
        })

        self.assertIn('disable_role_preamble: true', yaml_text)

    def test_action_to_yaml_round_trips_review_required_above_loc(self):
        yaml_text = self.server_actions._action_to_yaml('feature/implement', {
            'description': 'Implement feature',
            'review_required_above_loc': 100,
            'prompt': '{{ TASK }}\n',
        })

        self.assertIn('review_required_above_loc: 100', yaml_text)

    def test_action_to_yaml_round_trips_implementation_depth(self):
        yaml_text = self.server_actions._action_to_yaml('oneshot/feature', {
            'description': 'Implement feature',
            'implementation_depth': True,
            'prompt': '{{ TASK }}\n',
        })

        self.assertIn('implementation_depth: true', yaml_text)

    def test_action_to_yaml_round_trips_transition_loc_gate(self):
        yaml_text = self.server_actions._action_to_yaml('feature/implement', {
            'description': 'Implement feature',
            'implementation_depth': True,
            'transitions': [{
                'action': 'feature/review',
                'when': 'ready for review',
                'status': 'On Review',
                'loc_gate': {
                    'ship_direct_max': '25',
                    'review_default_above': '75',
                    'self_review_bypass_allowed': False,
                },
            }],
            'prompt': '{{ TASK }}\n',
        })
        act = yaml.safe_load(yaml_text)

        self.assertEqual(
            act['transitions'][0]['loc_gate'],
            {
                'ship_direct_max': 25,
                'review_default_above': 75,
                'self_review_bypass_allowed': False,
            },
        )

    def test_action_parser_preserves_transition_loc_gate(self):
        actions_mod = importlib.import_module('torque.actions')
        actions_mod = importlib.reload(actions_mod)
        act = actions_mod.parse_yaml("""
name: feature/implement
implementation_depth: true
transitions:
  - action: feature/review
    when: ready for review
    loc_gate:
      ship_direct_max: 25
      review_default_above: 75
      self_review_bypass_allowed: false
prompt: |
  {{ TASK }}
""")

        self.assertEqual(
            act['transitions'][0]['loc_gate']['review_default_above'],
            75,
        )

    def test_action_to_yaml_preserves_implementation_depth_false_with_threshold(self):
        yaml_text = self.server_actions._action_to_yaml('feature/research', {
            'description': 'Research',
            'implementation_depth': False,
            'review_required_above_loc': 100,
            'prompt': '{{ TASK }}\n',
        })
        act = yaml.safe_load(yaml_text)

        self.assertIn('implementation_depth: false', yaml_text)
        self.assertIn('review_required_above_loc: 100', yaml_text)
        self.assertIsNone(
            self.server_mod._review_gate_threshold_from_action(act))

    def test_dispatch_queue_helper_respects_self_dispatch(self):
        active = self.state_mod.BoardTask(
            id='task-1',
            task='Parent task',
            group='g',
            lane='In Progress',
        )

        self.assertTrue(self.server_dispatch._should_queue_existing_agent_dispatch(
            active,
            target_task_id='task-2',
            self_dispatch=False,
        ))
        self.assertFalse(self.server_dispatch._should_queue_existing_agent_dispatch(
            active,
            target_task_id='task-2',
            self_dispatch=True,
        ))

    def test_merge_auto_done_helper_skips_ambiguous_open_linked_tasks(self):
        state = self.state_mod.MatrixState()
        state.add_group('g')
        worker = self.state_mod.AgentCell(
            id='worker-1',
            name='Worker',
            group='g',
            cell_type='agent',
        )
        state.agents[worker.id] = worker
        state.groups['g'].append(worker.id)
        first = state.board_add_task(
            'First task',
            'g',
            lane='In Progress',
            id='task-1',
            agent_id=worker.id,
        )
        second = state.board_add_task(
            'Second task',
            'g',
            lane='In Progress',
            id='task-2',
            agent_id=worker.id,
        )

        decision = self.server_mod._maybe_auto_move_merged_task_to_done(
            state,
            worker,
            enabled=True,
            cleanup_requested=True,
        )

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertFalse(decision['moved'])
        self.assertIn('2 open linked tasks', decision['reason'])
        self.assertEqual(state.board_tasks['task-1'].lane, 'In Progress')
        self.assertEqual(state.board_tasks['task-2'].lane, 'In Progress')

    def test_compact_snapshot_opt_in_from_query_or_payload(self):
        request = types.SimpleNamespace(query={'compact': '1'})
        self.assertTrue(self.server_mod._request_wants_compact_snapshot(request))
        request = types.SimpleNamespace(query={'protocol_version': 'compact-v1'})
        self.assertTrue(self.server_mod._request_wants_compact_snapshot(request))
        request = types.SimpleNamespace(query={'compact': '0'})
        self.assertFalse(self.server_mod._request_wants_compact_snapshot(request))
        self.assertTrue(self.server_mod._payload_wants_compact_snapshot({
            'protocol_version': 'compact-v1',
        }))
        self.assertTrue(self.server_mod._payload_wants_compact_snapshot({
            'compact': True,
        }))

    def test_api_cmd_lifecycle_guard_rejects_worker_context_unless_forced(self):
        for cmd in ("restart", "stop", "deploy"):
            with self.subTest(cmd=cmd):
                with self.assertLogs(self.server_mod.log, level="WARNING"):
                    result = self.server_mod._api_worker_context_guard(
                        {"cmd": cmd}, {"TORQUE_CELL_ID": "worker-1"}, "127.0.0.1")

                self.assertEqual(result["status"], 403)
                self.assertIn("force=true", result["message"])
                self.assertIsNone(self.server_mod._api_worker_context_guard(
                    {"cmd": cmd, "force": True},
                    {"TORQUE_CELL_ID": "worker-1"},
                    "127.0.0.1"))
        result = self.server_mod._api_worker_context_guard(
            {"cmd": "restart", "torque_cell_id": "worker-2"},
            {},
            "127.0.0.1",
        )
        self.assertEqual(result["status"], 403)
        self.assertIn("worker-2", result["message"])

    def test_daemon_stop_state_rejects_fresh_api_requests_after_stop(self):
        stop_state = self.server_mod._DaemonStopState()

        self.assertFalse(stop_state.should_reject_api_request("get_config"))
        self.assertTrue(stop_state.request())
        self.assertTrue(stop_state.should_reject_api_request("get_config"))
        self.assertTrue(stop_state.should_reject_api_request("refresh"))
        self.assertFalse(stop_state.should_reject_api_request("stop"))
        self.assertFalse(stop_state.request())

        result = self.server_mod._daemon_stop_result()
        self.assertTrue(self.server_mod._is_daemon_stop_result(result))
        self.assertFalse(self.server_mod._is_daemon_stop_result({"type": "ok"}))
        self.assertEqual(
            self.server_mod._daemon_stop_rejection_payload()["type"],
            self.server_mod._DAEMON_STOP_RESULT_TYPE,
        )

    def test_daemon_stop_command_schedules_even_when_cleanup_fails(self):
        stop_state = self.server_mod._DaemonStopState()
        scheduled = []

        class BadState:
            agents = {"agent-1": types.SimpleNamespace(id="agent-1")}

            def _db_save_agent(self, _cell):
                raise RuntimeError("db boom")

        def schedule_stop():
            scheduled.append("scheduled")

        with self.assertLogs(self.server_mod.log, level="ERROR") as logs:
            result = asyncio.run(self.server_mod._handle_daemon_stop_command(
                daemon_stop_state=stop_state,
                schedule_daemon_stop=schedule_stop,
                state=BadState(),
            ))

        self.assertTrue(self.server_mod._is_daemon_stop_result(result))
        self.assertEqual(scheduled, ["scheduled"])
        self.assertTrue(stop_state.should_reject_api_request("get_config"))
        self.assertIn("Failed to persist agent", "\n".join(logs.output))

        scheduled.clear()
        repeat = asyncio.run(self.server_mod._handle_daemon_stop_command(
            daemon_stop_state=stop_state,
            schedule_daemon_stop=schedule_stop,
            state=BadState(),
        ))
        self.assertEqual(scheduled, ["scheduled"])
        self.assertIn("already requested", repeat["message"])

    def test_stop_command_handler_is_present_but_deploy_v1_is_documented_absent(self):
        source = Path(self.server_mod.__file__).read_text()

        self.assertIn('elif cmd == "stop":', source)
        self.assertIn("v1 does not implement a", source)
        self.assertIn("_API_DAEMON_LIFECYCLE_COMMANDS", source)

    def test_shutdown_daemon_runtime_drains_in_existing_order(self):
        events = []

        class FakeWs:
            def __init__(self, name):
                self.name = name

            async def close(self):
                events.append(f"{self.name}.close")

        class AsyncMethod:
            def __init__(self, name):
                self.name = name

            async def __call__(self):
                events.append(self.name)

        terminal_clients = {"cell-1": {FakeWs("terminal_ws")}}
        ui_ws_clients = {FakeWs("ui_ws")}
        panel_log = types.SimpleNamespace(aclose=AsyncMethod("panel_log.aclose"))
        drainer = types.SimpleNamespace(stop=AsyncMethod("event_drainer.stop"))
        ingest_client = types.SimpleNamespace(aclose=AsyncMethod("event_client.aclose"))
        bridge = types.SimpleNamespace(shutdown=AsyncMethod("bridge.shutdown"))
        runner = types.SimpleNamespace(cleanup=AsyncMethod("runner.cleanup"))
        state = types.SimpleNamespace(
            flush_db_writes=AsyncMethod("state.flush_db_writes"))

        class FakeDb:
            close_async_writes = AsyncMethod("db.close_async_writes")

            def close(self):
                events.append("db.close")

        asyncio.run(self.server_mod._shutdown_daemon_runtime(
            terminal_clients=terminal_clients,
            ui_ws_clients=ui_ws_clients,
            panel_log=panel_log,
            event_ingest_drainer=drainer,
            event_ingest_client=ingest_client,
            bridge=bridge,
            runner=runner,
            state=state,
            db=FakeDb(),
        ))

        self.assertEqual(events, [
            "terminal_ws.close",
            "ui_ws.close",
            "panel_log.aclose",
            "event_drainer.stop",
            "event_client.aclose",
            "bridge.shutdown",
            "runner.cleanup",
            "state.flush_db_writes",
            "db.close_async_writes",
            "db.close",
        ])
        self.assertEqual(terminal_clients, {})
        self.assertEqual(ui_ws_clients, set())

    def test_shutdown_daemon_runtime_marks_active_agents_stopped_before_ws_close(self):
        events = []

        class FakeWs:
            def __init__(self, name):
                self.name = name

            async def close(self):
                events.append(f"{self.name}.close")

        class AsyncMethod:
            def __init__(self, name):
                self.name = name

            async def __call__(self):
                events.append(self.name)

        state = self.state_mod.MatrixState()
        state.add_group("g")
        running = state.add_agent(
            name="Running Worker",
            group="g",
            terminal_backend="pty",
            command="codex",
        )
        idle = state.add_agent(
            name="Idle Worker",
            group="g",
            terminal_backend="pty",
            command="codex",
        )
        stopped = state.add_agent(
            name="Stopped Worker",
            group="g",
            terminal_backend="pty",
            command="codex",
        )
        running.status = "running"
        running.session_id = "sid-running"
        running.current_process = "codex"
        running.activity = "thinking"
        idle.status = "idle"
        idle.session_id = "sid-idle"
        idle.current_process = "codex"
        stopped.status = "stopped"
        stopped.session_id = None
        state.active_session_id = "sid-running"

        saved = []
        state._emit_agent = lambda cell: events.append(
            f"emit:{cell.id}:{cell.status}:{cell.session_id}")
        state._db_save_agent = lambda cell: (
            saved.append((cell.id, cell.status, cell.session_id)),
            events.append(f"save:{cell.id}:{cell.status}:{cell.session_id}"),
        )
        state._emit = lambda op, **payload: events.append(
            f"emit_op:{op}:{payload.get('active_session_id')}")

        async def broadcast():
            events.append("state.broadcast")

        state.broadcast = broadcast

        terminal_clients = {"cell-1": {FakeWs("terminal_ws")}}
        ui_ws_clients = {FakeWs("ui_ws")}
        panel_log = types.SimpleNamespace(aclose=AsyncMethod("panel_log.aclose"))
        drainer = types.SimpleNamespace(stop=AsyncMethod("event_drainer.stop"))
        ingest_client = types.SimpleNamespace(aclose=AsyncMethod("event_client.aclose"))
        bridge = types.SimpleNamespace(shutdown=AsyncMethod("bridge.shutdown"))
        runner = types.SimpleNamespace(cleanup=AsyncMethod("runner.cleanup"))

        class FakeDb:
            close_async_writes = AsyncMethod("db.close_async_writes")

            def close(self):
                events.append("db.close")

        state.flush_db_writes = AsyncMethod("state.flush_db_writes")

        asyncio.run(self.server_mod._shutdown_daemon_runtime(
            terminal_clients=terminal_clients,
            ui_ws_clients=ui_ws_clients,
            panel_log=panel_log,
            event_ingest_drainer=drainer,
            event_ingest_client=ingest_client,
            bridge=bridge,
            runner=runner,
            state=state,
            db=FakeDb(),
        ))

        self.assertEqual(running.status, "stopped")
        self.assertIsNone(running.session_id)
        self.assertEqual(running.current_process, "")
        self.assertEqual(running.activity, "")
        self.assertEqual(idle.status, "stopped")
        self.assertIsNone(idle.session_id)
        self.assertEqual(stopped.status, "stopped")
        self.assertEqual(
            saved,
            [
                (running.id, "stopped", None),
                (idle.id, "stopped", None),
            ],
        )
        self.assertIsNone(state.active_session_id)
        self.assertLess(
            events.index(f"save:{running.id}:stopped:None"),
            events.index("terminal_ws.close"),
        )
        self.assertLess(
            events.index("state.broadcast"),
            events.index("terminal_ws.close"),
        )
        self.assertLess(
            events.index("terminal_ws.close"),
            events.index("bridge.shutdown"),
        )

    def test_task_detail_command_returns_full_task_shape(self):
        state = self.state_mod.MatrixState()
        state.groups['g'] = []
        state.board_tasks['task-1'] = self.state_mod.BoardTask(
            id='task-1',
            task='Inspect detail',
            group='g',
            lane='To Do',
            description='full detail',
            messages=[{'message': 'progress'}],
        )
        state.task_id_aliases['legacy-1'] = 'task-1'

        result = self.server_mod._handle_task_detail_command(
            {'id': 'legacy-1'},
            state,
        )

        self.assertEqual(result['type'], 'task_detail')
        self.assertEqual(result['id'], 'task-1')
        self.assertEqual(result['task']['description'], 'full detail')
        self.assertEqual(result['task']['messages'], [{'message': 'progress'}])

    def test_deferred_snapshot_commands_return_maps(self):
        state = self.state_mod.MatrixState()

        class FakeDB:
            def load_all_decisions(self, *, include_archived=False):
                self.include_archived = include_archived
                return [
                    {'id': 'decision-1', 'archived': False},
                    {'id': 'decision-2', 'archived': True},
                ]

            def load_pending_hires(self, *, status_filter='', architect_id=''):
                self.status_filter = status_filter
                self.architect_id = architect_id
                return [
                    {'id': 'hire-1', 'status': status_filter or 'pending'},
                ]

        fake_db = FakeDB()
        state.db = fake_db

        decisions = self.server_mod._handle_decisions_snapshot_command(
            {'include_archived': True},
            state,
        )
        hires = self.server_mod._handle_pending_hires_snapshot_command(
            {'status_filter': 'pending', 'architect_id': 'arch-1'},
            state,
        )

        self.assertEqual(decisions['type'], 'decisions_snapshot')
        self.assertEqual(set(decisions['decisions']), {'decision-1', 'decision-2'})
        self.assertTrue(fake_db.include_archived)
        self.assertEqual(hires['type'], 'pending_hires_snapshot')
        self.assertEqual(set(hires['pending_hires']), {'hire-1'})
        self.assertEqual(fake_db.status_filter, 'pending')
        self.assertEqual(fake_db.architect_id, 'arch-1')

    def test_archived_tasks_command_returns_full_archived_details(self):
        state = self.state_mod.MatrixState()
        state.groups['g'] = []
        state.groups['other'] = []
        state.board_tasks['live'] = self.state_mod.BoardTask(
            id='live',
            task='Live task',
            group='g',
            lane='To Do',
        )
        state.board_tasks['archived'] = self.state_mod.BoardTask(
            id='archived',
            task='Archived task',
            group='g',
            lane=self.state_mod.ARCHIVED_LANE,
            archived_at='2026-04-22T00:00:00+00:00',
            description='archived detail',
            messages=[{'message': 'archived progress'}],
        )
        state.board_tasks['archived-other'] = self.state_mod.BoardTask(
            id='archived-other',
            task='Other archived task',
            group='other',
            lane=self.state_mod.ARCHIVED_LANE,
            archived_at='2026-04-22T00:00:00+00:00',
        )

        result = self.server_mod._handle_archived_tasks_command(
            {'group': 'g'},
            state,
        )

        self.assertEqual(result['type'], 'archived_tasks')
        self.assertEqual(set(result['board_tasks']), {'archived'})
        self.assertEqual(
            result['board_tasks']['archived']['messages'],
            [{'message': 'archived progress'}],
        )

    def test_engineer_journal_snapshot_command_returns_author_payloads(self):
        state = self.state_mod.MatrixState()
        state.groups['g'] = []
        state.agents['eng-a'] = self.state_mod.AgentCell(
            id='eng-a',
            name='Engineer A',
            group='g',
            kind='engineer',
        )
        state.agents['eng-b'] = self.state_mod.AgentCell(
            id='eng-b',
            name='Engineer B',
            group='g',
            kind='engineer',
        )
        state.engineer_worklog['g'] = [
            {'id': 2, 'entry': 'new'},
            {'id': 1, 'entry': 'old'},
        ]
        state.journal_read = lambda group, limit=20, **kwargs: [
            {
                'id': 1,
                'group': group,
                'entry': f"author={kwargs.get('author_cell_id')} limit={limit}",
                'author_cell_id': kwargs.get('author_cell_id'),
            },
        ]

        async def run():
            return await self.server_mod._handle_engineer_journal_snapshot_command(
                {
                    'group': 'g',
                    'limit': 7,
                    'worklog_limit': 1,
                    'include_streams': False,
                },
                state,
            )

        result = asyncio.run(run())

        self.assertEqual(result['type'], 'engineer_journal_snapshot')
        self.assertEqual(result['group'], 'g')
        self.assertEqual(
            result['engineer_journal'],
            {
                'eng-a': [{
                    'id': 1,
                    'group': 'g',
                    'entry': 'author=eng-a limit=7',
                    'author_cell_id': 'eng-a',
                }],
                'eng-b': [{
                    'id': 1,
                    'group': 'g',
                    'entry': 'author=eng-b limit=7',
                    'author_cell_id': 'eng-b',
                }],
            },
        )
        self.assertEqual(
            result['engineer_worklog'],
            {'g': [{'id': 2, 'entry': 'new'}]},
        )
        self.assertEqual(
            result['engineer_streams']['g'],
            {'count': 0, 'by_state': {}, 'items': [], 'truncated': False},
        )

    def test_derive_helper_preserves_parent_assigned_engineer(self):
        parent = self.state_mod.BoardTask(
            id='task-parent',
            task='Parent task',
            group='g',
            lane='In Progress',
            assigned_engineer_id='eng-123',
        )
        child = self.state_mod.BoardTask(
            id='task-child',
            task='Child task',
            group='g',
            lane='Backlog',
        )

        assigned_engineer_id = (
            self.server_mod._inherit_assigned_engineer_for_derived_task(
                parent,
                child,
            )
        )

        self.assertEqual(assigned_engineer_id, 'eng-123')
        self.assertEqual(child.assigned_engineer_id, 'eng-123')

    def test_board_archive_command_ignores_include_descendants_and_archives_one_task(self):
        state = self.state_mod.MatrixState()
        state.add_group('g')
        parent = state.board_add_task('Done parent', 'g', lane='Done', id='task-1')
        child = state.board_add_task(
            'Done child',
            'g',
            lane='Done',
            id='task-2',
            parent_task_id='task-1',
        )

        result = self.server_mod._handle_board_archive_command(
            state,
            {'id': parent.id, 'include_descendants': True},
        )

        self.assertIsNone(result)
        self.assertEqual(state.board_tasks[parent.id].lane, 'Archived')
        self.assertEqual(state.board_tasks[child.id].lane, 'Done')

    def test_board_archive_tasks_command_archives_multiple_in_one_batch(self):
        state = self.state_mod.MatrixState()
        state.add_group('g')
        first = state.board_add_task('Done one', 'g', lane='Done', id='task-1')
        second = state.board_add_task('Done two', 'g', lane='Done', id='task-2')

        result = self.server_mod._handle_board_archive_tasks_command(
            state,
            {'ids': [first.id, second.id]},
        )

        self.assertEqual(result['type'], 'toast')
        self.assertEqual(result['level'], 'success')
        self.assertEqual(result['message'], 'Archived 2 completed tasks')
        self.assertEqual(state.board_tasks[first.id].lane, 'Archived')
        self.assertEqual(state.board_tasks[second.id].lane, 'Archived')

    def test_board_unarchive_command_ignores_include_descendants_and_restores_one_task(self):
        state = self.state_mod.MatrixState()
        state.add_group('g')
        parent = state.board_add_task('Done parent', 'g', lane='Done', id='task-1')
        child = state.board_add_task(
            'Done child',
            'g',
            lane='Done',
            id='task-2',
            parent_task_id='task-1',
        )
        state.board_archive_task(parent.id)
        state.board_archive_task(child.id)

        result = self.server_mod._handle_board_unarchive_command(
            state,
            {'id': parent.id, 'lane': 'Done', 'include_descendants': True},
        )

        self.assertIsNone(result)
        self.assertEqual(state.board_tasks[parent.id].lane, 'Done')
        self.assertEqual(state.board_tasks[child.id].lane, 'Archived')

    def test_engineer_flush_now_command_reports_success(self):
        calls = []

        class FakeBuffer:
            def request_manual_flush(self, group):
                calls.append(group)
                return True, ""

        result = self.server_mod._handle_engineer_flush_now_command(
            FakeBuffer(),
            {'group': 'g'},
        )

        self.assertEqual(calls, ['g'])
        self.assertEqual(result, {'type': 'ok'})

    def test_engineer_flush_now_command_accepts_agent_id(self):
        calls = []

        class FakeBuffer:
            def request_manual_flush(self, recipient_or_group):
                calls.append(recipient_or_group)
                return True, ""

        result = self.server_mod._handle_engineer_flush_now_command(
            FakeBuffer(),
            {'agent_id': 'eng-1'},
        )

        self.assertEqual(calls, ['eng-1'])
        self.assertEqual(result, {'type': 'ok'})

    def test_digest_pause_command_updates_agent_settings_and_pauses_buffer(self):
        state = self.state_mod.MatrixState()
        state.agents['eng-1'] = self.state_mod.AgentCell(
            id='eng-1',
            name='Panelsmith',
            slug='panelsmith',
            group='g',
            kind='engineer',
            cell_type='agent',
        )
        calls = []

        class FakeBuffer:
            def on_delivery_paused(self, agent_id):
                calls.append(('paused', agent_id))

            def on_delivery_resumed(self, agent_id):
                calls.append(('resumed', agent_id))

        result = self.server_mod._handle_digest_pause_resume_command(
            state,
            FakeBuffer(),
            {'agent_id': 'panelsmith'},
            paused=True,
        )

        self.assertEqual(
            result,
            {'type': 'ok', 'agent_id': 'eng-1', 'paused': True},
        )
        self.assertTrue(state.get_agent_digest_settings('eng-1').paused)
        self.assertEqual(calls, [('paused', 'eng-1')])

    def test_digest_resume_command_updates_agent_settings_and_resumes_buffer(self):
        state = self.state_mod.MatrixState()
        state.agents['eng-1'] = self.state_mod.AgentCell(
            id='eng-1',
            name='Panelsmith',
            slug='panelsmith',
            group='g',
            kind='engineer',
            cell_type='agent',
        )
        state.update_agent_digest_settings('eng-1', paused=True)
        calls = []

        class FakeBuffer:
            def on_delivery_paused(self, agent_id):
                calls.append(('paused', agent_id))

            def on_delivery_resumed(self, agent_id):
                calls.append(('resumed', agent_id))

        result = self.server_mod._handle_digest_pause_resume_command(
            state,
            FakeBuffer(),
            {'agent_id': 'eng-1'},
            paused=False,
        )

        self.assertEqual(
            result,
            {'type': 'ok', 'agent_id': 'eng-1', 'paused': False},
        )
        self.assertFalse(state.get_agent_digest_settings('eng-1').paused)
        self.assertEqual(calls, [('resumed', 'eng-1')])

    def test_engineer_flush_now_command_surfaces_pause_error(self):
        class FakeBuffer:
            def request_manual_flush(self, group):
                self.group = group
                return False, "Delivery is paused"

        buffer = FakeBuffer()
        result = self.server_mod._handle_engineer_flush_now_command(
            buffer,
            {'group': 'g'},
        )

        self.assertEqual(buffer.group, 'g')
        self.assertEqual(
            result,
            {'type': 'error', 'message': 'Delivery is paused'},
        )

    def test_doctor_command_returns_stage1_report(self):
        from torque.db import TorqueDB

        with tempfile.TemporaryDirectory() as tmp:
            db = TorqueDB(Path(tmp) / 'torque.db')
            db.init()
            self.addCleanup(db.close)
            db.save_agent(
                self.state_mod.AgentCell(
                    id='engineer-1',
                    name='Engineer',
                    group='torque',
                    slug='engineer',
                    cell_type='agent',
                    kind='engineer',
                    persistent=True,
                )
            )

            result = asyncio.run(self.server_mod._handle_doctor_command(db))

        self.assertEqual(result['schema_version'], 3)
        self.assertEqual(result['result'], 'pass')
        self.assertIn('migration', result)
        self.assertIn('runtime_locations', result)
        self.assertIn('checks', result)
        self.assertEqual(result['runtime_locations']['runtime_python'], sys.executable)
        self.assertEqual(result['agents']['engineer'], 1)

    def test_emit_task_artifact_uploaded_event_uses_digest_friendly_summary(self):
        task = self.state_mod.BoardTask(
            id='task-1',
            task='Review uploaded evidence',
            group='g',
            lane='In Progress',
        )
        actor = self.state_mod.AgentCell(
            id='agent-1',
            name='Worker',
            group='g',
            cell_type='agent',
        )
        calls = []

        def fake_panel_event(kind, cell_id, agent_name, group, message, task_id=""):
            calls.append({
                'kind': kind,
                'cell_id': cell_id,
                'agent_name': agent_name,
                'group': group,
                'message': message,
                'task_id': task_id,
            })

        self.server_mod._emit_task_artifact_uploaded_event(
            fake_panel_event,
            task,
            actor,
            {
                'type': 'test_report',
                'title': 'pytest.log',
                'filename': 'pytest.log',
                'url': '/attachments/task-1/pytest.log',
                'summary': '15 B | uploaded report',
                'content': 'E assert 1 == 2\n',
            },
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['kind'], 'task_artifact_uploaded')
        self.assertEqual(calls[0]['cell_id'], 'agent-1')
        self.assertEqual(calls[0]['task_id'], 'task-1')
        self.assertIn('/attachments/task-1/pytest.log', calls[0]['message'])
        self.assertIn('E assert 1 == 2', calls[0]['message'])

    def test_workflow_breach_command_emits_assigned_engineer_panel_event(self):
        state = self.state_mod.MatrixState()
        state.groups['g'] = []
        engineer = self.state_mod.AgentCell(
            id='eng-1',
            name='Engineer',
            group='g',
            cell_type='agent',
            kind='engineer',
        )
        worker = self.state_mod.AgentCell(
            id='worker-1',
            name='Worker',
            group='g',
            cell_type='agent',
            kind='worker',
            owner_engineer_id=engineer.id,
            worktree_branch='torque/worker-1',
        )
        state.agents[engineer.id] = engineer
        state.agents[worker.id] = worker
        state.groups['g'] = [engineer.id, worker.id]
        task = state.board_add_task(
            'Fix residue',
            'g',
            id='task-1',
            agent_id=worker.id,
            assigned_engineer_id=engineer.id,
        )
        events = []

        def panel_event(kind, cell_id, agent_name, group, message, task_id=''):
            events.append({
                'kind': kind,
                'cell_id': cell_id,
                'agent_name': agent_name,
                'group': group,
                'message': message,
                'task_id': task_id,
            })

        result = self.server_mod._handle_workflow_breach_command(
            {
                'subkind': 'manual',
                'task_id': task.id,
                'context': 'Operator caught reviewer residue',
            },
            state,
            panel_event,
        )

        self.assertEqual(result['type'], 'workflow_breach')
        self.assertEqual(result['event']['worker_id'], worker.id)
        self.assertEqual(events[0]['kind'], 'workflow_breach')
        self.assertEqual(events[0]['cell_id'], engineer.id)
        self.assertIn('manual', events[0]['message'])
        self.assertIn('Operator caught reviewer residue', events[0]['message'])
        self.assertIn('branch=torque/worker-1', events[0]['message'])

    def test_stale_base_workflow_breach_targets_worker_owner(self):
        state = self.state_mod.MatrixState()
        state.groups['g'] = []
        engineer = self.state_mod.AgentCell(
            id='eng-1',
            name='Engineer',
            group='g',
            cell_type='agent',
            kind='engineer',
        )
        worker = self.state_mod.AgentCell(
            id='worker-1',
            name='Worker',
            group='g',
            cell_type='agent',
            kind='worker',
            owner_engineer_id=engineer.id,
            worktree_branch='torque/worker-1',
        )
        state.agents[engineer.id] = engineer
        state.agents[worker.id] = worker
        task = state.board_add_task(
            'Merge branch',
            'g',
            id='task-1',
            lane='In Progress',
            agent_id=worker.id,
        )
        worker.current_task_id = task.id
        events = []

        def panel_event(kind, cell_id, agent_name, group, message, task_id=''):
            events.append((kind, cell_id, agent_name, group, message, task_id))

        event = self.server_mod._emit_stale_base_catch_workflow_breach(
            state,
            panel_event,
            worker,
            {
                'stale': True,
                'warning': '⚠ STALE BASE: branch forked from old main',
            },
        )

        self.assertEqual(event['subkind'], 'stale_base_catch')
        self.assertEqual(event['task_id'], task.id)
        self.assertEqual(events[0][0], 'workflow_breach')
        self.assertEqual(events[0][1], engineer.id)
        self.assertIn('stale_base_catch', events[0][4])
        self.assertIn('source=auto', events[0][4])

    def test_stale_base_override_workflow_breach_targets_worker_owner(self):
        state = self.state_mod.MatrixState()
        state.groups['g'] = []
        engineer = self.state_mod.AgentCell(
            id='eng-1',
            name='Engineer',
            group='g',
            cell_type='agent',
            kind='engineer',
        )
        worker = self.state_mod.AgentCell(
            id='worker-1',
            name='Worker',
            group='g',
            cell_type='agent',
            kind='worker',
            owner_engineer_id=engineer.id,
            worktree_branch='torque/worker-1',
        )
        state.agents[engineer.id] = engineer
        state.agents[worker.id] = worker
        task = state.board_add_task(
            'Merge branch',
            'g',
            id='task-1',
            lane='In Progress',
            agent_id=worker.id,
        )
        worker.current_task_id = task.id
        events = []

        def panel_event(kind, cell_id, agent_name, group, message, task_id=''):
            events.append((kind, cell_id, agent_name, group, message, task_id))

        event = self.server_mod._emit_stale_base_override_workflow_breach(
            state,
            panel_event,
            worker,
            {
                'stale': True,
                'warning': '⚠ STALE BASE: branch forked from old main',
            },
        )

        self.assertEqual(event['subkind'], 'stale_base_override')
        self.assertEqual(event['task_id'], task.id)
        self.assertEqual(events[0][0], 'workflow_breach')
        self.assertEqual(events[0][1], engineer.id)
        self.assertIn('stale_base_override', events[0][4])
        self.assertIn('source=operator', events[0][4])
        self.assertIn('force=true', event['context'])

    def test_role_commands_and_template_compat_dispatch_through_helper(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / 'repo' / 'subdir'
            project.mkdir(parents=True)
            (root / 'repo' / '.torque' / 'agents').mkdir(parents=True)
            home = root / 'home'
            (home / '.torque').mkdir(parents=True)
            (home / '.torque' / 'agents').mkdir(parents=True)
            prev_home = os.environ.get('HOME')
            os.environ['HOME'] = str(home)
            self.addCleanup(
                lambda: os.environ.__setitem__('HOME', prev_home)
                if prev_home is not None
                else os.environ.pop('HOME', None)
            )

            roles_mod = importlib.import_module('torque.roles')
            roles_mod = importlib.reload(roles_mod)
            role_mgr = roles_mod.RoleManager()

            async def resolve_base_dir(_group):
                return str(project)

            save_role = asyncio.run(
                self.server_mod._handle_role_template_command(
                    {
                        'cmd': 'save_role',
                        'group': 'g',
                        'name': 'demo',
                        'scope': 'user',
                        'data': {
                            'name': 'demo',
                            'preamble': 'Be careful.',
                            'priorities': ['ship small', 'test first'],
                        },
                    },
                    role_mgr,
                    resolve_base_dir,
                )
            )

            self.assertEqual(save_role['type'], 'roles')
            demo = next(item for item in save_role['roles']
                        if item['name'] == 'demo')
            self.assertEqual(demo['preamble'], 'Be careful.')
            self.assertEqual(demo['priorities'], ['ship small', 'test first'])
            saved_role = yaml.safe_load(
                (home / '.torque' / 'roles' / 'demo.yaml').read_text(
                    encoding='utf-8'
                )
            )
            self.assertEqual(saved_role['preamble'], 'Be careful.\n')
            self.assertEqual(
                saved_role['priorities'],
                ['ship small', 'test first'],
            )
            self.assertEqual(
                role_mgr.load_role('demo', base_dir=str(project)),
                {
                    'name': 'demo',
                    'preamble': 'Be careful.',
                    'priorities': ['ship small', 'test first'],
                },
            )

            list_roles = asyncio.run(
                self.server_mod._handle_role_template_command(
                    {'cmd': 'list_roles', 'group': 'g'},
                    role_mgr,
                    resolve_base_dir,
                )
            )
            self.assertEqual(list_roles['type'], 'roles')
            self.assertEqual(
                [item['name'] for item in list_roles['roles']],
                ['demo'],
            )

            save_template = asyncio.run(
                self.server_mod._handle_role_template_command(
                    {
                        'cmd': 'save_template',
                        'group': 'g',
                        'name': 'compat',
                        'scope': 'project',
                        'template': {
                            'name': 'compat',
                            'description': 'Compat save',
                        },
                    },
                    role_mgr,
                    resolve_base_dir,
                )
            )

            self.assertEqual(save_template['type'], 'templates')
            self.assertTrue((project / '.torque' / 'roles' / 'compat.yaml').is_file())
            self.assertFalse((root / 'repo' / '.torque' / 'agents' / 'compat.yaml').exists())

            legacy_user_template = home / '.torque' / 'agents' / 'legacy.yaml'
            legacy_user_template.write_text('name: legacy\ndescription: Legacy\n')
            delete_role_legacy = asyncio.run(
                self.server_mod._handle_role_template_command(
                    {
                        'cmd': 'delete_role',
                        'group': 'g',
                        'name': 'legacy',
                        'scope': 'user',
                    },
                    role_mgr,
                    resolve_base_dir,
                )
            )

            self.assertEqual(delete_role_legacy, {
                'type': 'error',
                'message': 'Role "legacy" not found',
            })
            self.assertTrue(legacy_user_template.exists())

            legacy_user_template.write_text('name: legacy\ndescription: Legacy\n')
            delete_template = asyncio.run(
                self.server_mod._handle_role_template_command(
                    {
                        'cmd': 'delete_template',
                        'group': 'g',
                        'name': 'legacy',
                        'scope': 'user',
                    },
                    role_mgr,
                    resolve_base_dir,
                )
            )

            self.assertEqual(delete_template, {
                'type': 'error',
                'message': 'Template "legacy" not found',
            })
            self.assertTrue(legacy_user_template.exists())

            legacy_rename_path = home / '.torque' / 'agents' / 'rename-me.yaml'
            legacy_rename_path.write_text('name: rename-me\ndescription: Legacy\n')
            rename_template = asyncio.run(
                self.server_mod._handle_role_template_command(
                    {
                        'cmd': 'save_template',
                        'group': 'g',
                        'name': 'renamed',
                        'old_name': 'rename-me',
                        'scope': 'user',
                        'template': {
                            'name': 'renamed',
                            'description': 'Renamed compat save',
                        },
                    },
                    role_mgr,
                    resolve_base_dir,
                )
            )

            self.assertEqual(rename_template['type'], 'templates')
            self.assertTrue(legacy_rename_path.exists())
            self.assertTrue((home / '.torque' / 'roles' / 'renamed.yaml').is_file())

            delete_role = asyncio.run(
                self.server_mod._handle_role_template_command(
                    {
                        'cmd': 'delete_role',
                        'group': 'g',
                        'name': 'demo',
                        'scope': 'user',
                    },
                    role_mgr,
                    resolve_base_dir,
                )
            )

            self.assertEqual(delete_role['type'], 'roles')
            self.assertEqual(delete_role['deleted'], 'demo')
            self.assertFalse((home / '.torque' / 'roles' / 'demo.yaml').exists())


class ServerPromptQueueTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub()
        self.state_mod = importlib.import_module('torque.state')
        self.state_mod = importlib.reload(self.state_mod)
        self.server_mod = importlib.import_module('torque.server')
        self.server_mod = importlib.reload(self.server_mod)

    async def test_dismiss_engineer_note_archives_it_to_panel_events(self):
        state = self.state_mod.MatrixState()
        state.add_group('g')
        engineer = self.state_mod.AgentCell(
            id='engineer-1',
            name='Engineer',
            group='g',
            cell_type='agent',
            kind='engineer',
        )
        state.agents[engineer.id] = engineer
        state.groups['g'].append(engineer.id)
        state.group_settings['g'] = self.state_mod.GroupSettings(
            engineer_agent_id=engineer.id,
        )
        state.update_engineer_settings(
            'g',
            pending_note='FYI: release branch is ready',
            pending_note_kind='note',
            pending_note_set_at=123.0,
            pending_note_actor_id=engineer.id,
        )
        state._delta_ops.clear()
        events = []

        def panel_event(kind, cell_id, agent_name, group, message, task_id=''):
            events.append((kind, cell_id, agent_name, group, message, task_id))

        result = await self.server_mod._handle_engineer_dismiss_note_command(
            {'group': 'g'},
            state,
            panel_event,
        )

        self.assertEqual(result, {'type': 'ok'})
        journal_ops = [
            op for op in state._delta_ops
            if op.get('op') == 'journal_append'
        ]
        self.assertEqual(len(journal_ops), 1)
        self.assertEqual(journal_ops[0]['type'], 'note_dismissed')
        self.assertEqual(journal_ops[0]['entry'], 'FYI: release branch is ready')
        self.assertEqual(journal_ops[0]['author_cell_id'], 'engineer-1')
        self.assertEqual(journal_ops[0]['timestamp'], 123.0)
        self.assertEqual(
            events,
            [(
                'engineer_note_dismissed',
                'engineer-1',
                'Engineer',
                'g',
                'FYI: release branch is ready',
                '',
            )],
        )
        ws = state.get_engineer_settings('g')
        self.assertEqual(ws.pending_note, '')
        self.assertEqual(ws.pending_note_kind, '')
        self.assertEqual(ws.pending_note_set_at, 0.0)
        self.assertEqual(ws.pending_note_actor_id, '')

        result = await self.server_mod._handle_engineer_dismiss_note_command(
            {'group': 'g'},
            state,
            panel_event,
        )
        self.assertEqual(result, {'type': 'ok'})
        journal_ops = [
            op for op in state._delta_ops
            if op.get('op') == 'journal_append'
        ]
        self.assertEqual(len(journal_ops), 1)

    async def test_queue_cell_prompt_send_does_not_wait_for_slow_background_delivery(self):
        cell = self.state_mod.AgentCell(
            id='agent-1',
            name='Worker',
            group='g',
            cell_type='agent',
            session_id='session-1',
        )
        queued_tasks = []
        release = asyncio.Event()

        async def fake_send_prompt(cell, prompt, **kwargs):
            self.assertEqual(cell.session_id, 'session-1')
            self.assertEqual(prompt, 'hello')
            self.assertTrue(kwargs.get('background'))

            async def _deliver():
                await release.wait()

            task = asyncio.create_task(_deliver())
            queued_tasks.append(task)
            return task

        queued = await self.server_mod._queue_cell_prompt_send(
            cell,
            'hello',
            fake_send_prompt,
        )

        self.assertTrue(queued)
        self.assertEqual(len(queued_tasks), 1)
        self.assertFalse(queued_tasks[0].done())

        state = self.state_mod.MatrixState()
        state.update_engineer_settings('g', paused=True)
        self.assertTrue(state.get_engineer_settings('g').paused)

        release.set()
        await queued_tasks[0]

    async def test_deliver_engineer_reply_waits_for_prompt_before_resuming_delivery(self):
        from torque.db import TorqueDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = TorqueDB(Path(tmp.name) / 'torque.db')
        db.init()
        self.addCleanup(db.close)
        state = self.state_mod.MatrixState(db=db)
        state.add_group('g')
        state.update_engineer_settings(
            'g',
            pending_question='Need approval',
            pending_question_set_at=456.0,
            pending_question_actor_id='engineer-1',
            paused=True,
        )
        engineer = self.state_mod.AgentCell(
            id='engineer-1',
            name='Engineer',
            group='g',
            cell_type='agent',
            kind='engineer',
            hired_by_architect_id='arch-1',
            session_id='session-1',
        )
        architect = self.state_mod.AgentCell(
            id='arch-1',
            name='Architect',
            group='g',
            cell_type='agent',
            kind='architect',
        )
        state.agents[architect.id] = architect
        state.agents[engineer.id] = engineer
        state.groups['g'] = [architect.id, engineer.id]
        state._delta_ops.clear()
        sequence = []
        started = asyncio.Event()
        release = asyncio.Event()

        async def fake_send_prompt(cell, prompt, **kwargs):
            self.assertIs(cell, engineer)
            self.assertIn('## Human Reply', prompt)
            self.assertTrue(kwargs.get('background'))
            self.assertTrue(kwargs.get('prime_input_ready'))

            async def _deliver():
                sequence.append('reply-start')
                started.set()
                await release.wait()
                sequence.append('reply-finish')

            return asyncio.create_task(_deliver())

        class FakeBuffer:
            def on_delivery_resumed(self, group):
                sequence.append(f'resume:{group}')

        task = asyncio.create_task(
            self.server_mod._deliver_engineer_reply_and_resume(
                state,
                engineer,
                group='g',
                answer='Ship it',
                send_prompt=fake_send_prompt,
                engineer_buffer=FakeBuffer(),
            )
        )

        await started.wait()
        self.assertEqual(sequence, ['reply-start'])
        ws = state.get_engineer_settings('g')
        self.assertTrue(ws.paused)
        self.assertEqual(ws.pending_question, 'Need approval')

        release.set()
        result = await task

        self.assertEqual(result, {'type': 'ok'})
        self.assertEqual(sequence, ['reply-start', 'reply-finish', 'resume:g'])
        ws = state.get_engineer_settings('g')
        self.assertFalse(ws.paused)
        self.assertEqual(ws.pending_question, '')
        journal_ops = [
            op for op in state._delta_ops
            if op.get('op') == 'journal_append'
        ]
        qa_ops = [op for op in journal_ops if op.get('type') == 'qa']
        self.assertEqual(len(qa_ops), 1)
        self.assertEqual(qa_ops[0]['author_cell_id'], 'engineer-1')
        self.assertIn('Question:\nNeed approval', qa_ops[0]['entry'])
        self.assertIn('Answer:\nShip it', qa_ops[0]['entry'])
        direct_rows = db.load_direct_messages_for_agent(engineer.id)
        direct_by_type = {row['message_type']: row for row in direct_rows}
        self.assertEqual(set(direct_by_type), {'ask', 'ask_reply'})
        self.assertEqual(direct_by_type['ask']['sender_id'], engineer.id)
        self.assertEqual(direct_by_type['ask']['recipient_id'], architect.id)
        self.assertEqual(direct_by_type['ask']['recipient_kind'], 'architect')
        self.assertTrue(direct_by_type['ask']['blocking'])
        self.assertEqual(direct_by_type['ask_reply']['sender_id'], architect.id)
        self.assertEqual(direct_by_type['ask_reply']['recipient_id'], engineer.id)
        self.assertFalse(direct_by_type['ask_reply']['blocking'])
        self.assertEqual(
            direct_by_type['ask_reply']['reply_to_id'],
            direct_by_type['ask']['id'],
        )

    async def test_deliver_engineer_reply_buffers_when_session_is_absent(self):
        from torque.db import TorqueDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = TorqueDB(Path(tmp.name) / 'torque.db')
        db.init()
        self.addCleanup(db.close)
        state = self.state_mod.MatrixState(db=db)
        state.add_group('g')
        engineer = self.state_mod.AgentCell(
            id='engineer-1',
            name='Engineer',
            group='g',
            cell_type='agent',
            kind='engineer',
            session_id='',
        )
        state.agents[engineer.id] = engineer
        state.groups['g'] = [engineer.id]
        state.update_engineer_settings(
            'g',
            pending_question='Need approval',
            pending_question_set_at=456.0,
            pending_question_actor_id=engineer.id,
            paused=True,
        )

        async def fake_send_prompt(*_args, **_kwargs):
            self.fail('offline engineer reply should buffer, not inject')

        class FakeBuffer:
            def __init__(self):
                self.resumed = []

            def on_delivery_resumed(self, group):
                self.resumed.append(group)

        buffer = FakeBuffer()
        result = await self.server_mod._deliver_engineer_reply_and_resume(
            state,
            engineer,
            group='g',
            answer='Ship it',
            send_prompt=fake_send_prompt,
            engineer_buffer=buffer,
        )

        self.assertEqual(result, {'type': 'ok'})
        self.assertEqual(buffer.resumed, ['g'])
        ws = state.get_engineer_settings('g')
        self.assertEqual(ws.pending_question, '')
        direct_rows = db.load_direct_messages_for_agent(engineer.id)
        direct_by_type = {row['message_type']: row for row in direct_rows}
        self.assertEqual(set(direct_by_type), {'ask', 'ask_reply'})
        self.assertEqual(
            direct_by_type['ask_reply']['delivery_state'],
            'buffered',
        )
        self.assertEqual(
            direct_by_type['ask_reply']['delivery_reason'],
            'no_session',
        )

        sent = []

        async def replay_send_prompt(cell, prompt, **kwargs):
            sent.append((cell.id, prompt, kwargs))

            async def _delivered():
                return None

            return asyncio.create_task(_delivered())

        engineer.session_id = 'session-1'
        replayed = await self.server_mod._replay_buffered_cross_kind_messages(
            state,
            bridge=None,
            target=engineer,
            send_prompt=replay_send_prompt,
        )

        self.assertEqual(replayed, 1)
        self.assertEqual(sent[0][0], engineer.id)
        self.assertIn('## Human Reply', sent[0][1])
        self.assertIn('Need approval', sent[0][1])
        self.assertIn('Ship it', sent[0][1])
        replayed_row = db.load_direct_message(
            direct_by_type['ask_reply']['id']
        )
        self.assertEqual(replayed_row['delivery_state'], 'delivered')

    def test_pending_question_reply_target_prefers_actor_owner(self):
        state = self.state_mod.MatrixState()
        state.add_group('g')
        group_engineer = self.state_mod.AgentCell(
            id='engineer-1',
            name='Group Engineer',
            group='g',
            cell_type='agent',
            session_id='session-engineer',
        )
        owner = self.state_mod.AgentCell(
            id='eng-1',
            name='Asking Engineer',
            group='g',
            cell_type='agent',
            session_id='session-engineer',
        )
        state.agents[group_engineer.id] = group_engineer
        state.agents[owner.id] = owner
        state.group_settings['g'] = self.state_mod.GroupSettings(
            engineer_agent_id=group_engineer.id,
        )
        state.update_engineer_settings(
            'g',
            pending_question='Need approval',
            pending_question_actor_id=owner.id,
            paused=True,
        )

        target, label = self.server_mod._pending_question_reply_target(state, 'g')

        self.assertIs(target, owner)
        self.assertEqual(label, 'Engineer')

        state.update_engineer_settings(
            'g',
            pending_question='Legacy question',
            pending_question_actor_id='',
            paused=True,
        )
        target, label = self.server_mod._pending_question_reply_target(state, 'g')
        self.assertIs(target, group_engineer)
        self.assertEqual(label, 'Engineer')


class ServerWebSocketResyncTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub()
        self.state_mod = importlib.import_module('torque.state')
        self.state_mod = importlib.reload(self.state_mod)
        self.server_mod = importlib.import_module('torque.server')
        self.server_mod = importlib.reload(self.server_mod)

    async def test_register_ready_ui_ws_client_retries_until_inflight_updates_are_reflected(self):
        state = self.state_mod.MatrixState()
        release_snapshot = asyncio.Event()
        snapshot_started = asyncio.Event()

        class FakeWS:
            closed = False

            def __init__(self):
                self.messages = []
                self.state_payloads = []

            async def send_str(self, payload):
                data = json.loads(payload)
                if data.get('type') == 'state':
                    self.state_payloads.append(data)
                    if len(self.state_payloads) == 1:
                        snapshot_started.set()
                        await release_snapshot.wait()
                self.messages.append((data.get('type'), data.get('seq')))

        ws = FakeWS()
        register = asyncio.create_task(
            self.server_mod._register_ready_ui_ws_client(
                state,
                ws,
                lambda: {
                    'type': 'state',
                    'seq': state._seq,
                    'groups': dict(state.groups),
                    'agents': {},
                },
            )
        )

        await snapshot_started.wait()
        self.assertNotIn(ws, state._ws_clients)

        state.add_group('new-group')
        await state.broadcast()
        # Drain the deferred engineer-stream recompute so the resync
        # snapshot sees a settled seq instead of racing the follow-up
        # broadcast spawned by the group mutation.
        if state._engineer_recompute_task is not None:
            await state._engineer_recompute_task

        release_snapshot.set()
        ok = await register

        self.assertTrue(ok)
        self.assertIn(ws, state._ws_clients)
        self.assertEqual(ws.messages, [('state', 0), ('state', state._seq)])
        self.assertEqual(ws.state_payloads[-1]['groups'], {'new-group': []})

    async def test_register_ready_ui_ws_client_resync_replays_current_state_after_blocked_send(self):
        state = self.state_mod.MatrixState()
        release_snapshot = asyncio.Event()
        snapshot_started = asyncio.Event()

        class ReadyWS:
            closed = False

            def __init__(self):
                self.messages = []

            async def send_str(self, payload):
                data = json.loads(payload)
                self.messages.append((data.get('type'), data.get('seq')))

        class ResyncWS:
            closed = False

            def __init__(self):
                self.messages = []
                self.state_payloads = []

            async def send_str(self, payload):
                data = json.loads(payload)
                if data.get('type') == 'state':
                    self.state_payloads.append(data)
                    if len(self.state_payloads) == 1:
                        snapshot_started.set()
                        await release_snapshot.wait()
                self.messages.append((data.get('type'), data.get('seq')))

        ready_ws = ReadyWS()
        resync_ws = ResyncWS()
        state._ws_clients.add(ready_ws)
        state._ws_clients.add(resync_ws)

        register = asyncio.create_task(
            self.server_mod._register_ready_ui_ws_client(
                state,
                resync_ws,
                lambda: {
                    'type': 'state',
                    'seq': state._seq,
                    'groups': dict(state.groups),
                    'agents': {},
                },
            )
        )

        await snapshot_started.wait()
        self.assertIn(ready_ws, state._ws_clients)
        self.assertNotIn(resync_ws, state._ws_clients)

        state.add_group('resynced-group')
        await state.broadcast()
        # Drain the deferred engineer-stream recompute so seq numbers are
        # settled before the resync retry observes them. Without this
        # the ready_ws client also receives the follow-up engineer_streams
        # delta, which isn't what this test is exercising.
        if state._engineer_recompute_task is not None:
            await state._engineer_recompute_task
        settled_seq = state._seq
        settled_ready_msgs = list(ready_ws.messages)

        release_snapshot.set()
        ok = await register

        self.assertTrue(ok)
        self.assertIn(resync_ws, state._ws_clients)
        self.assertEqual(ready_ws.messages, settled_ready_msgs)
        self.assertEqual(
            resync_ws.messages,
            [('state', 0), ('state', settled_seq)],
        )
        self.assertEqual(
            resync_ws.state_payloads[-1]['groups'],
            {'resynced-group': []},
        )

    async def test_register_ready_ui_ws_client_skips_closing_socket(self):
        state = self.state_mod.MatrixState()

        class ClosingWS:
            closed = False

            async def send_str(self, _payload):
                raise ConnectionResetError('Cannot write to closing transport')

        ws = ClosingWS()
        ok = await self.server_mod._register_ready_ui_ws_client(
            state,
            ws,
            lambda: {'type': 'state', 'seq': 0},
        )

        self.assertFalse(ok)
        self.assertNotIn(ws, state._ws_clients)


class ServerMergeCleanupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub()
        self.state_mod = importlib.import_module('torque.state')
        self.state_mod = importlib.reload(self.state_mod)
        self.server_mod = importlib.import_module('torque.server')
        self.server_mod = importlib.reload(self.server_mod)

    def test_launch_resolver_for_cell_routes_workers_but_not_terminals(self):
        def generic():
            return "generic"

        def worker():
            return "worker"

        worker_cell = self.state_mod.AgentCell(
            id='worker-1',
            name='Worker',
            group='g',
            cell_type='agent',
            kind='worker',
        )
        terminal_cell = self.state_mod.AgentCell(
            id='term-1',
            name='Shell',
            group='g',
            cell_type='terminal',
            kind='worker',
        )

        self.assertIs(
            self.server_mod._launch_resolver_for_cell(
                worker_cell,
                resolve_agent_launch_config=generic,
                resolve_worker_launch_config=worker,
            ),
            worker,
        )
        self.assertIs(
            self.server_mod._launch_resolver_for_cell(
                terminal_cell,
                resolve_agent_launch_config=generic,
                resolve_worker_launch_config=worker,
            ),
            generic,
        )

    async def test_relaunch_after_worktree_removal_resets_live_agent_session(self):
        cell = self.state_mod.AgentCell(
            id='agent-1',
            name='Worker',
            group='g',
            cell_type='agent',
            session_id='session-old',
            agent_session_id='resume-token',
            status='running',
            directory='/repo',
            template='default',
            created_by_engineer_id='engineer-1',
        )
        emitted = []
        saved = []
        launch_calls = []
        prompt_calls = []
        closed = []
        created = []

        async def fake_resolve_base_dir(_group):
            return '/fallback'

        def fake_resolve_agent_launch_config(group, *, base_dir,
                                             explicit_template, overrides):
            launch_calls.append({
                'group': group,
                'base_dir': base_dir,
                'explicit_template': explicit_template,
                'overrides': overrides,
            })
            return {
                'env_vars': {'TORQUE_ENV': '1'},
                'env_file': '/tmp/torque.env',
                'shell': 'zsh',
                'system_prompt': 'system prompt',
            }

        def fake_apply_persistent_prompt(cell, launch_cfg, prompt):
            prompt_calls.append({
                'directory': cell.directory,
                'prompt': prompt,
                'env_file': launch_cfg.get('env_file'),
            })

        def fake_build_cell_persistent_prompt(cell, launch_cfg):
            return f"{launch_cfg.get('system_prompt')} @ {cell.directory}"

        class FakeBridge:
            async def close_session(self, session_id):
                closed.append(session_id)

            async def create_session(self, cell, **kwargs):
                cell.session_id = 'session-new'
                created.append({
                    'directory': cell.directory,
                    'kwargs': kwargs,
                })

        fake_state = types.SimpleNamespace(
            _emit_agent=lambda cell: emitted.append(
                (cell.status, cell.directory, cell.session_id)
            ),
            _db_save_agent=lambda cell: saved.append(
                (cell.status, cell.directory, cell.session_id)
            ),
        )

        await self.server_mod._relaunch_agent_after_worktree_removal(
            cell,
            bridge=FakeBridge(),
            state=fake_state,
            resolve_base_dir=fake_resolve_base_dir,
            resolve_agent_launch_config=fake_resolve_agent_launch_config,
            apply_persistent_prompt=fake_apply_persistent_prompt,
            build_cell_persistent_prompt=fake_build_cell_persistent_prompt,
        )

        self.assertEqual(closed, ['session-old'])
        self.assertEqual(cell.created_by_engineer_id, 'engineer-1')
        self.assertEqual(cell.session_id, 'session-new')
        self.assertEqual(cell.agent_session_id, '')
        self.assertEqual(cell.status, 'stopped')
        self.assertEqual(
            launch_calls,
            [{
                'group': 'g',
                'base_dir': '/repo',
                'explicit_template': 'default',
                'overrides': {},
            }],
        )
        self.assertEqual(
            prompt_calls,
            [{
                'directory': '/repo',
                'prompt': 'system prompt @ /repo',
                'env_file': '/tmp/torque.env',
            }],
        )
        self.assertEqual(
            created,
            [{
                'directory': '/repo',
                'kwargs': {
                    'env_vars': {'TORQUE_ENV': '1'},
                    'env_file': '/tmp/torque.env',
                    'shell': 'zsh',
                    'system_prompt': 'system prompt',
                    'mcp_entrypoint': 'torque/mcp.py',
                },
            }],
        )
        self.assertEqual(
            emitted,
            [('stopped', '/repo', None)],
        )
        self.assertEqual(
            saved,
            [('stopped', '/repo', None)],
        )


class ServerEngineerMessageFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub()
        self.state_mod = importlib.import_module('torque.state')
        self.state_mod = importlib.reload(self.state_mod)
        self.server_mod = importlib.import_module('torque.server')
        self.server_mod = importlib.reload(self.server_mod)
        self.server_agent_mod = importlib.import_module('torque.server_agent')
        self.server_agent_mod = importlib.reload(self.server_agent_mod)
        from torque.db import TorqueDB

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = TorqueDB(Path(self.tmp.name) / 'torque.db')
        self.db.init()
        self.addCleanup(self.db.close)

    def _make_state(self):
        state = self.state_mod.MatrixState(db=self.db)
        state.groups['g'] = []
        return state

    async def test_architect_ui_peer_message_uses_sender_as_caller_and_architect_id_as_recipient(self):
        state = self._make_state()
        sender = self.state_mod.AgentCell(
            id='arch-sender',
            name='Sender Architect',
            group='g',
            cell_type='agent',
            kind='architect',
        )
        recipient = self.state_mod.AgentCell(
            id='arch-recipient',
            name='Recipient Architect',
            group='g',
            cell_type='agent',
            kind='architect',
            session_id='session-recipient',
        )
        state.agents[sender.id] = sender
        state.agents[recipient.id] = recipient
        state.groups['g'] = [sender.id, recipient.id]
        injected = []

        async def handle_command(payload):
            injected.append(dict(payload))
            return {'type': 'ok', 'delivered': True}

        result = await self.server_mod._dispatch_architect_ui_tool(
            'architect_peer_message',
            {
                'sender_architect_id': sender.id,
                'architect_id': recipient.id,
                'message': 'Please verify the peer routing.',
                'ack_required': True,
            },
            state,
            handle_command=handle_command,
        )

        self.assertEqual(result['type'], 'ok')
        self.assertEqual(result['recipient_architect_id'], recipient.id)
        persisted = self.db.load_agent_peer_message(result['message_id'])
        self.assertEqual(persisted['sender_id'], sender.id)
        self.assertEqual(persisted['recipient_id'], recipient.id)
        self.assertEqual(persisted['sender_kind'], 'architect')
        self.assertEqual(persisted['recipient_kind'], 'architect')
        self.assertEqual(injected[0]['cmd'], 'inject_mcp_message')
        self.assertEqual(injected[0]['agent_id'], recipient.id)
        self.assertEqual(injected[0]['sender_name'], sender.name)
        self.assertTrue(injected[0]['ack_required'])

    def _make_agent_launch_service(self, state, bridge):
        class FakeTemplateManager:
            pass

        return self.server_agent_mod.AgentLaunchService(
            state=state,
            connection=None,
            bridge=bridge,
            worktree_mgr=None,
            template_mgr=FakeTemplateManager(),
        )

    def _add_engineer_and_worker(self, state, *, current_task_id=''):
        engineer = self.state_mod.AgentCell(
            id='engineer-1',
            name='Engineer',
            group='g',
            cell_type='agent',
        )
        worker = self.state_mod.AgentCell(
            id='agent-1',
            name='Worker',
            group='g',
            cell_type='agent',
            session_id='session-1',
            status='running',
            current_task_id=current_task_id,
        )
        state.agents[engineer.id] = engineer
        state.agents[worker.id] = worker
        state.groups['g'] = [engineer.id, worker.id]
        state.group_settings['g'] = self.state_mod.GroupSettings(
            engineer_agent_id=engineer.id
        )
        state.history_record_agent(worker)
        return engineer, worker

    async def test_send_engineer_message_creates_derived_follow_up_task_and_history(self):
        state = self._make_state()
        parent = state.board_add_task(
            'Implement feature',
            'g',
            lane='In Progress',
            id='task-parent',
            agent_id='agent-1',
        )
        _engineer, worker = self._add_engineer_and_worker(
            state,
            current_task_id=parent.id,
        )

        primed = []
        sent = []
        events = []

        class FakeBridge:
            def prime_input_ready(self, session_id):
                primed.append(session_id)

            async def send_text(self, session_id, text):
                sent.append((session_id, text))

        def panel_event(kind, cell_id, agent_name, group, message, task_id=''):
            events.append({
                'kind': kind,
                'cell_id': cell_id,
                'agent_name': agent_name,
                'group': group,
                'message': message,
                'task_id': task_id,
            })

        result = await self.server_mod._send_engineer_message_to_agent(
            state,
            FakeBridge(),
            worker,
            'Please rebase the branch and report back',
            panel_event,
        )

        self.assertEqual(result['type'], 'ok')
        follow_up = state.board_tasks[result['task_id']]
        self.assertEqual(follow_up.parent_task_id, parent.id)
        self.assertEqual(follow_up.pipeline_root_id, parent.id)
        self.assertEqual(follow_up.pipeline_depth, 1)
        self.assertEqual(follow_up.reply_agent_id, worker.id)
        self.assertEqual(follow_up.status, 'Awaiting Reply')
        self.assertIn('torque:derived', follow_up.labels)
        self.assertIn('torque:engineer-message', follow_up.labels)
        self.assertEqual(follow_up.messages[-1]['action'], 'engineer_message')
        self.assertTrue(worker.pending_engineer_message)
        self.assertEqual(primed, ['session-1'])
        self.assertEqual(sent[0][0], 'session-1')
        self.assertIn(f'Task: {follow_up.id}', sent[0][1])
        self.assertIn(
            f'torque_reply(task=\"{follow_up.id}\", message=\"your response\")',
            sent[0][1],
        )
        self.assertEqual(events, [{
            'kind': 'engineer_message',
            'cell_id': worker.id,
            'agent_name': worker.name,
            'group': worker.group,
            'message': 'Please rebase the branch and report back',
            'task_id': follow_up.id,
        }])
        self.assertEqual(
            self.db.load_agent_tasks(worker.id)[0]['task_id'],
            follow_up.id,
        )
        self.assertEqual(
            state.engineer_worklog[worker.group][0]['task_id'],
            follow_up.id,
        )
        self.assertEqual(
            self.db.load_agent_messages_by_task(follow_up.id)[0]['action'],
            'engineer_message',
        )

    async def test_send_engineer_message_without_active_task_creates_root_follow_up(self):
        state = self._make_state()
        _engineer, worker = self._add_engineer_and_worker(state)

        class FakeBridge:
            def prime_input_ready(self, _session_id):
                pass

            async def send_text(self, _session_id, _text):
                return None

        result = await self.server_mod._send_engineer_message_to_agent(
            state,
            FakeBridge(),
            worker,
            'Need a quick status update',
            lambda *args, **kwargs: None,
        )

        follow_up = state.board_tasks[result['task_id']]
        self.assertEqual(follow_up.parent_task_id, '')
        self.assertEqual(follow_up.pipeline_root_id, '')
        self.assertEqual(follow_up.pipeline_depth, 0)
        self.assertEqual(follow_up.group, 'g')
        self.assertEqual(follow_up.labels, ['torque:engineer-message'])

    async def test_send_engineer_message_failure_restores_progress_clocks(self):
        state = self._make_state()
        _engineer, worker = self._add_engineer_and_worker(state)
        worker.status = 'running'
        worker.last_progress_at = 123.0
        worker.last_heartbeat_at = 124.0
        worker.last_activity_at = 124.0
        worker.last_event_at = 124.0

        class FailingBridge:
            def prime_input_ready(self, _session_id):
                pass

            async def send_text(self, _session_id, _text):
                raise RuntimeError('terminal unavailable')

        result = await self.server_mod._send_engineer_message_to_agent(
            state,
            FailingBridge(),
            worker,
            'Need a quick status update',
            lambda *args, **kwargs: None,
        )

        self.assertEqual(result['type'], 'error')
        self.assertEqual(worker.status, 'running')
        self.assertEqual(worker.last_progress_at, 123.0)
        self.assertEqual(worker.last_heartbeat_at, 124.0)
        self.assertEqual(worker.last_activity_at, 124.0)
        self.assertEqual(worker.last_event_at, 124.0)
        self.assertEqual(state.board_tasks, {})

    async def test_send_engineer_message_reply_not_required_appends_inline_thread(self):
        state = self._make_state()
        parent = state.board_add_task(
            'Implement feature',
            'g',
            lane='In Progress',
            id='task-parent',
            agent_id='agent-1',
        )
        engineer, worker = self._add_engineer_and_worker(
            state,
            current_task_id=parent.id,
        )

        sent = []
        events = []

        class FakeBridge:
            def prime_input_ready(self, _session_id):
                pass

            async def send_text(self, session_id, text):
                sent.append((session_id, text))

        def panel_event(*args, **kwargs):
            events.append((args, kwargs))

        result = await self.server_mod._send_engineer_message_to_agent(
            state,
            FakeBridge(),
            worker,
            'FYI: switch to the smaller repro before continuing',
            panel_event,
            reply_required=False,
        )

        self.assertEqual(result['type'], 'ok')
        self.assertFalse(result['reply_required'])
        self.assertEqual(result['task_id'], '')
        self.assertEqual(result['thread_task_id'], parent.id)
        self.assertEqual(
            [t.id for t in state.board_tasks.values()],
            [parent.id],
        )
        self.assertEqual(len(state.board_tasks[parent.id].messages_thread), 1)
        entry = state.board_tasks[parent.id].messages_thread[0]
        self.assertEqual(entry['sender_agent_id'], engineer.id)
        self.assertEqual(entry['recipient_agent_id'], worker.id)
        self.assertEqual(
            entry['content'],
            'FYI: switch to the smaller repro before continuing',
        )
        self.assertFalse(entry['reply_required'])
        self.assertFalse(worker.pending_engineer_message)
        self.assertEqual(events, [])
        self.assertNotIn('torque_reply', sent[0][1])
        self.assertEqual(
            self.db.load_agent_messages_by_task(parent.id)[0]['action'],
            'engineer_message',
        )

    async def test_send_engineer_message_reply_not_required_requires_active_task(self):
        state = self._make_state()
        _engineer, worker = self._add_engineer_and_worker(state)

        class FakeBridge:
            async def send_text(self, _session_id, _text):
                self.fail('message should not send without inline parent')

        result = await self.server_mod._send_engineer_message_to_agent(
            state,
            FakeBridge(),
            worker,
            'FYI only',
            lambda *args, **kwargs: None,
            reply_required=False,
        )

        self.assertEqual(result['type'], 'error')
        self.assertIn('active parent task', result['message'])
        self.assertEqual(state.board_tasks, {})

    async def test_reply_not_required_inline_thread_accumulates_chronologically(self):
        state = self._make_state()
        parent = state.board_add_task(
            'Implement feature',
            'g',
            lane='In Progress',
            id='task-parent',
            agent_id='agent-1',
        )
        _engineer, worker = self._add_engineer_and_worker(
            state,
            current_task_id=parent.id,
        )

        class FakeBridge:
            async def send_text(self, _session_id, _text):
                return None

        with mock.patch.object(
            self.server_mod.time,
            'time',
            side_effect=[9.0, 10.0, 10.5, 19.0, 20.0, 20.5],
        ):
            await self.server_mod._send_engineer_message_to_agent(
                state,
                FakeBridge(),
                worker,
                'First inline update',
                lambda *args, **kwargs: None,
                reply_required=False,
            )
            await self.server_mod._send_engineer_message_to_agent(
                state,
                FakeBridge(),
                worker,
                'Second inline update',
                lambda *args, **kwargs: None,
                reply_required=False,
            )

        thread = state.board_tasks[parent.id].messages_thread
        self.assertEqual(
            [entry['content'] for entry in thread],
            ['First inline update', 'Second inline update'],
        )
        self.assertEqual([entry['timestamp'] for entry in thread], [10.0, 20.0])
        self.assertTrue(
            all(
                set(entry) == {
                    'timestamp',
                    'sender_agent_id',
                    'recipient_agent_id',
                    'content',
                    'reply_required',
                }
                for entry in thread
            )
        )

    async def test_send_user_message_routes_directly_through_terminal_adapter(self):
        state = self._make_state()
        worker = self.state_mod.AgentCell(
            id='agent-1',
            name='Worker',
            group='g',
            cell_type='agent',
            session_id='session-1',
            status='idle',
        )
        state.agents[worker.id] = worker
        state.groups['g'] = [worker.id]
        sent = []

        class FakeBridge:
            async def send_text(self, session_id, text):
                sent.append((session_id, text))

        delivered = await self.server_mod._handle_send_user_message_command(
            {
                'cmd': 'send_user_message',
                'cell_id': worker.id,
                'text': 'line one\nline two',
            },
            state,
            FakeBridge(),
        )

        self.assertTrue(delivered)
        self.assertEqual(sent, [('session-1', 'line one\nline two')])
        self.assertEqual(worker.status, 'running')
        self.assertEqual(
            [
                entry['message']
                for entry in state.agent_message_history_read(worker.id)
            ],
            ['line one\nline two'],
        )
        self.assertIn(
            'agent_message_history_append',
            [op.get('op') for op in state._delta_ops],
        )

        ignored = await self.server_mod._handle_send_user_message_command(
            {
                'cmd': 'send_user_message',
                'cell_id': worker.id,
                'text': '   ',
            },
            state,
            FakeBridge(),
        )

        self.assertFalse(ignored)
        self.assertEqual(sent, [('session-1', 'line one\nline two')])
        self.assertEqual(len(state.agent_message_history_read(worker.id)), 1)

    async def test_send_user_message_failure_restores_progress_clocks(self):
        state = self._make_state()
        worker = self.state_mod.AgentCell(
            id='agent-1',
            name='Worker',
            group='g',
            cell_type='agent',
            session_id='session-1',
            status='idle',
            last_progress_at=123.0,
            last_heartbeat_at=124.0,
        )
        state.agents[worker.id] = worker
        state.groups['g'] = [worker.id]

        class FailingBridge:
            async def send_text(self, _session_id, _text):
                raise RuntimeError('terminal unavailable')

        with self.assertRaises(RuntimeError):
            await self.server_mod._handle_send_user_message_command(
                {
                    'cmd': 'send_user_message',
                    'cell_id': worker.id,
                    'text': 'line one',
                },
                state,
                FailingBridge(),
            )

        self.assertEqual(worker.status, 'idle')
        self.assertEqual(worker.last_progress_at, 123.0)
        self.assertEqual(worker.last_heartbeat_at, 124.0)
        self.assertEqual(worker.last_activity_at, 124.0)
        self.assertEqual(worker.last_event_at, 124.0)
        self.assertEqual(state.agent_message_history_read(worker.id), [])

    async def test_send_text_command_rolls_back_when_background_send_fails(self):
        state = self._make_state()
        worker = self.state_mod.AgentCell(
            id='agent-1',
            name='Worker',
            group='g',
            cell_type='agent',
            session_id='session-1',
            status='idle',
            last_progress_at=123.0,
            last_heartbeat_at=124.0,
        )
        state.agents[worker.id] = worker
        state.groups['g'] = [worker.id]
        send_started = asyncio.Event()
        release_delivery = asyncio.Event()

        class FailingBridge:
            async def send_text(self, _session_id, _text, **_kwargs):
                send_started.set()
                await release_delivery.wait()
                raise RuntimeError('terminal unavailable')

        service = self._make_agent_launch_service(state, FailingBridge())
        queued = await self.server_mod._handle_send_text_command(
            {
                'cmd': 'send_text',
                'id': worker.id,
                'text': 'start this',
            },
            state,
            service.send_agent_prompt,
        )

        self.assertTrue(queued)
        self.assertEqual(worker.status, 'running')
        self.assertEqual(len(service._background_prompt_tasks), 1)
        task = next(iter(service._background_prompt_tasks))

        await asyncio.wait_for(send_started.wait(), timeout=1.0)
        release_delivery.set()
        result = await asyncio.gather(task, return_exceptions=True)

        self.assertIsInstance(result[0], RuntimeError)
        self.assertEqual(worker.status, 'idle')
        self.assertEqual(worker.last_progress_at, 123.0)
        self.assertEqual(worker.last_heartbeat_at, 124.0)
        self.assertEqual(worker.last_activity_at, 124.0)
        self.assertEqual(worker.last_event_at, 124.0)

    async def test_user_agent_message_persists_and_queues_wrapped_prompt(self):
        state = self._make_state()
        worker = self.state_mod.AgentCell(
            id='agent-1',
            name='Worker',
            group='g',
            cell_type='agent',
            kind='worker',
            session_id='session-1',
            status='idle',
        )
        state.agents[worker.id] = worker
        state.groups['g'] = [worker.id]
        sent = []
        direct_notifications = []

        class FakeNotifier:
            def on_direct_user_message(self, row):
                direct_notifications.append(dict(row))

        state.notification_manager = FakeNotifier()

        async def fake_send_prompt(cell, prompt, **kwargs):
            sent.append((cell.id, prompt, kwargs))

            async def _delivered():
                return None

            return asyncio.create_task(_delivered())

        result = await self.server_mod._handle_user_agent_message_command(
            {
                'cmd': 'user_agent_message',
                'agent_id': worker.id,
                'message': 'Can you summarize your current plan?',
                'idempotency_key': 'browser-submit-1',
            },
            state,
            fake_send_prompt,
        )

        self.assertEqual(result['type'], 'ok')
        self.assertTrue(result['delivered'])
        self.assertFalse(result['buffered'])
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][0], worker.id)
        prompt = sent[0][1]
        self.assertIn('## Message from the User', prompt)
        self.assertNotIn('Message ID:', prompt)
        self.assertNotIn('Thread ID:', prompt)
        self.assertNotIn('Sent:', prompt)
        self.assertIn('Can you summarize your current plan?', prompt)
        self.assertIn(
            (
                'mcp__torque__torque_message_user('
                f'message="...", reply_to_id="{result["message_id"]}")'
            ),
            prompt,
        )
        self.assertIn(
            'Do not rely on free-text terminal output',
            prompt,
        )
        self.assertTrue(sent[0][2].get('background'))
        self.assertTrue(sent[0][2].get('prime_input_ready'))
        self.assertTrue(sent[0][2].get('settled_submit'))

        persisted = self.db.load_direct_message(result['message_id'])
        self.assertEqual(persisted['sender_kind'], 'user')
        self.assertEqual(persisted['recipient_id'], worker.id)
        self.assertEqual(persisted['recipient_kind'], 'worker')
        self.assertEqual(persisted['delivery_state'], 'delivered')
        self.assertEqual(persisted['delivery_reason'], '')
        self.assertEqual(
            state.direct_messages_by_agent[worker.id][0]['delivery_state'],
            'delivered',
        )
        self.assertEqual(direct_notifications, [])

    async def test_user_agent_message_preserves_multiline_storage_and_prompt(self):
        state = self._make_state()
        worker = self.state_mod.AgentCell(
            id='agent-1',
            name='Worker',
            group='g',
            cell_type='agent',
            kind='worker',
            session_id='session-1',
            status='idle',
        )
        state.agents[worker.id] = worker
        state.groups['g'] = [worker.id]
        sent = []

        async def fake_send_prompt(cell, prompt, **kwargs):
            sent.append((cell.id, prompt, kwargs))

            async def _delivered():
                return None

            return asyncio.create_task(_delivered())

        message = 'First line of the DM\nSecond line stays separate'
        result = await self.server_mod._handle_user_agent_message_command(
            {
                'cmd': 'user_agent_message',
                'agent_id': worker.id,
                'message': message,
                'idempotency_key': 'browser-submit-multiline',
            },
            state,
            fake_send_prompt,
        )

        self.assertEqual(result['type'], 'ok')
        self.assertEqual(len(sent), 1)
        prompt = sent[0][1]
        self.assertIn('First line of the DM\nSecond line stays separate', prompt)
        self.assertIn(
            'First line of the DM\nSecond line stays separate\n\nReply to this user-facing conversation',
            prompt,
        )
        self.assertNotIn('First line of the DMSecond line stays separate', prompt)

        persisted = self.db.load_direct_message(result['message_id'])
        self.assertEqual(persisted['message'], message)
        self.assertEqual(state.direct_messages_by_agent[worker.id][0]['message'], message)
        self.assertIn('\n', persisted['message'])

    async def test_user_agent_compact_passthrough_and_other_slash_is_normal_message(self):
        state = self._make_state()
        worker = self.state_mod.AgentCell(
            id='agent-1',
            name='Worker',
            group='g',
            cell_type='agent',
            kind='worker',
            session_id='session-1',
            status='idle',
        )
        state.agents[worker.id] = worker
        state.groups['g'] = [worker.id]
        sent = []

        async def fake_send_prompt(cell, prompt, **kwargs):
            sent.append((cell.id, prompt, kwargs))

            async def _delivered():
                return None

            return asyncio.create_task(_delivered())

        compact = await self.server_mod._handle_user_agent_message_command(
            {
                'cmd': 'user_agent_message',
                'agent_id': worker.id,
                'message': '/compact',
                'idempotency_key': 'compact-submit',
            },
            state,
            fake_send_prompt,
        )
        other = await self.server_mod._handle_user_agent_message_command(
            {
                'cmd': 'user_agent_message',
                'agent_id': worker.id,
                'message': '/compact now please',
                'idempotency_key': 'other-slash-submit',
            },
            state,
            fake_send_prompt,
        )
        unsupported = await self.server_mod._handle_user_agent_message_command(
            {
                'cmd': 'user_agent_message',
                'agent_id': worker.id,
                'message': '/loopy every 1m should stay natural language',
                'idempotency_key': 'unsupported-slash-submit',
            },
            state,
            fake_send_prompt,
        )

        self.assertEqual(compact['type'], 'ok')
        self.assertEqual(other['type'], 'ok')
        self.assertEqual(unsupported['type'], 'ok')
        self.assertEqual(len(sent), 3)
        self.assertEqual(sent[0][1], '/compact')
        self.assertIn('## Message from the User', sent[1][1])
        self.assertIn('/compact now please', sent[1][1])
        self.assertIn('/loopy every 1m should stay natural language', sent[2][1])
        compact_row = self.db.load_direct_message(compact['message_id'])
        other_row = self.db.load_direct_message(other['message_id'])
        unsupported_row = self.db.load_direct_message(unsupported['message_id'])
        self.assertEqual(compact_row['message_type'], 'slash_command')
        self.assertEqual(compact_row['context_snapshot']['slash_command'], 'compact')
        self.assertEqual(other_row['message_type'], 'message')
        self.assertEqual(unsupported_row['message_type'], 'message')

    async def test_user_agent_loop_create_fire_cancel_and_invalid_no_spam(self):
        state = self._make_state()
        worker = self.state_mod.AgentCell(
            id='agent-1',
            name='Worker',
            group='g',
            cell_type='agent',
            kind='worker',
            session_id='session-1',
            status='idle',
        )
        state.agents[worker.id] = worker
        state.groups['g'] = [worker.id]
        sent = []

        async def fake_send_prompt(cell, prompt, **kwargs):
            sent.append((cell.id, prompt, kwargs))

            async def _delivered():
                return None

            return asyncio.create_task(_delivered())

        invalid = await self.server_mod._handle_user_agent_message_command(
            {
                'cmd': 'user_agent_message',
                'agent_id': worker.id,
                'message': '/loop every 1s too fast',
                'idempotency_key': 'loop-invalid',
            },
            state,
            fake_send_prompt,
        )
        self.assertEqual(invalid['type'], 'error')
        self.assertIn('at least 1m', invalid['message'])
        self.assertEqual(state.agent_message_loops, {})
        self.assertEqual(sent, [])
        self.assertEqual(self.db.load_direct_messages_for_agent(worker.id), [])

        created = await self.server_mod._handle_user_agent_message_command(
            {
                'cmd': 'user_agent_message',
                'agent_id': worker.id,
                'message': '/loop every 1m please report progress',
                'idempotency_key': 'loop-create',
            },
            state,
            fake_send_prompt,
        )
        self.assertEqual(created['type'], 'agent_message_loop')
        loop = created['loop']
        self.assertEqual(loop['agent_id'], worker.id)
        self.assertEqual(loop['interval_seconds'], 60)
        self.assertEqual(loop['message'], 'please report progress')
        self.assertEqual(loop['status'], 'active')
        self.assertEqual(len(sent), 0)
        audit_rows = self.db.load_direct_messages_for_agent(worker.id)
        self.assertEqual(len(audit_rows), 1)
        self.assertEqual(audit_rows[0]['message_type'], 'system')
        self.assertIn('User started /loop every 1m', audit_rows[0]['message'])

        server_dispatch = importlib.import_module('torque.server_dispatch')
        server_dispatch = importlib.reload(server_dispatch)

        async def handle_command(payload):
            return await self.server_mod._handle_user_agent_message_command(
                payload,
                state,
                fake_send_prompt,
            )

        state.agent_message_loop_update(loop['id'], next_run_at=10.0)
        fired = await server_dispatch._fire_due_agent_message_loops(
            state,
            handle_command,
            panel_event=None,
            now_ts=10.0,
        )
        self.assertEqual(len(fired), 1)
        self.assertEqual(len(sent), 1)
        self.assertIn('This message was sent by a user-scheduled /loop.', sent[0][1])
        self.assertIn('please report progress', sent[0][1])
        self.assertIn('torque_stop_user_message_loop', sent[0][1])
        updated_loop = state.agent_message_loops[loop['id']]
        self.assertEqual(updated_loop.run_count, 1)
        self.assertGreater(updated_loop.next_run_at, 10.0)
        self.assertEqual(updated_loop.deferred_at, 0)
        self.assertEqual(updated_loop.deferred_reason, '')
        loop_row = self.db.load_direct_message(fired[0]['message_id'])
        self.assertEqual(loop_row['message_type'], 'loop')
        self.assertEqual(loop_row['context_snapshot']['loop_id'], loop['id'])

        cancelled = await self.server_mod._handle_user_agent_message_command(
            {
                'cmd': 'user_agent_message',
                'agent_id': worker.id,
                'message': '/loop cancel',
                'idempotency_key': 'loop-cancel',
            },
            state,
            fake_send_prompt,
        )
        self.assertEqual(cancelled['type'], 'agent_message_loop')
        self.assertEqual(cancelled['loop']['status'], 'cancelled')
        self.assertIsNone(state.active_agent_message_loop_for_agent(worker.id))
        after_cancel_rows = self.db.load_direct_messages_for_agent(worker.id)
        self.assertTrue(
            any('User cancelled /loop' in row['message']
                for row in after_cancel_rows)
        )

        worker.session_id = ''
        stopped_when_buffered = await self.server_mod._handle_user_agent_message_command(
            {
                'cmd': 'user_agent_message',
                'agent_id': worker.id,
                'message': '/loop every 1m do not spam offline',
                'idempotency_key': 'loop-offline-create',
            },
            state,
            fake_send_prompt,
        )
        offline_loop_id = stopped_when_buffered['loop']['id']
        state.agent_message_loop_update(offline_loop_id, next_run_at=20.0)
        offline_fired = await server_dispatch._fire_due_agent_message_loops(
            state,
            handle_command,
            panel_event=None,
            now_ts=20.0,
        )
        self.assertEqual(offline_fired, [])
        self.assertEqual(
            state.agent_message_loops[offline_loop_id].status,
            'stopped',
        )
        self.assertIsNone(state.active_agent_message_loop_for_agent(worker.id))
        offline_rows = self.db.load_direct_messages_for_agent(worker.id)
        self.assertTrue(
            any('System stopped /loop because delivery was not live' in row['message']
                for row in offline_rows)
        )

    async def test_due_agent_message_loop_defers_busy_target_then_fires_once_when_idle(self):
        state = self._make_state()
        worker = self.state_mod.AgentCell(
            id='agent-1',
            name='Worker',
            group='g',
            cell_type='agent',
            kind='worker',
            session_id='session-1',
            status='running',
            activity='tool_call',
            activity_detail='sleep 120',
        )
        state.agents[worker.id] = worker
        state.groups['g'] = [worker.id]
        sent = []

        async def fake_send_prompt(cell, prompt, **kwargs):
            sent.append((cell.id, prompt, kwargs))

            async def _delivered():
                return None

            return asyncio.create_task(_delivered())

        async def handle_command(payload):
            return await self.server_mod._handle_user_agent_message_command(
                payload,
                state,
                fake_send_prompt,
            )

        loop = state.agent_message_loop_add(
            agent_id=worker.id,
            group_name='g',
            interval_seconds=60,
            message='please report after sleep',
            created_by='user',
            now=0.0,
        )
        state.agent_message_loop_update(loop.id, next_run_at=10.0)
        state._delta_ops.clear()
        broadcasts = []

        async def capture_broadcast():
            broadcasts.append([dict(op) for op in state._delta_ops])
            state._delta_ops = []

        state.broadcast = capture_broadcast
        server_dispatch = importlib.import_module('torque.server_dispatch')
        server_dispatch = importlib.reload(server_dispatch)

        busy_first = await server_dispatch._fire_due_agent_message_loops(
            state,
            handle_command,
            panel_event=None,
            now_ts=10.0,
        )
        busy_second = await server_dispatch._fire_due_agent_message_loops(
            state,
            handle_command,
            panel_event=None,
            now_ts=40.0,
        )

        self.assertEqual(busy_first, [])
        self.assertEqual(busy_second, [])
        self.assertEqual(sent, [])
        deferred = state.agent_message_loops[loop.id]
        self.assertEqual(deferred.status, 'active')
        self.assertEqual(deferred.run_count, 0)
        self.assertEqual(deferred.next_run_at, 10.0)
        self.assertEqual(deferred.deferred_at, 10.0)
        self.assertEqual(deferred.deferred_reason, 'agent_busy')
        self.assertEqual(len(broadcasts), 1)
        loop_ops = [
            op for op in broadcasts[0]
            if op.get('op') == 'agent_message_loop_upsert'
        ]
        self.assertEqual(len(loop_ops), 1)
        self.assertEqual(loop_ops[0]['loop']['deferred_reason'], 'agent_busy')

        worker.activity = ''
        worker.activity_detail = ''
        fired = await server_dispatch._fire_due_agent_message_loops(
            state,
            handle_command,
            panel_event=None,
            now_ts=45.0,
        )
        duplicate = await server_dispatch._fire_due_agent_message_loops(
            state,
            handle_command,
            panel_event=None,
            now_ts=46.0,
        )

        self.assertEqual(len(fired), 1)
        self.assertEqual(duplicate, [])
        self.assertEqual(len(sent), 1)
        self.assertIn('please report after sleep', sent[0][1])
        updated = state.agent_message_loops[loop.id]
        self.assertEqual(updated.run_count, 1)
        self.assertEqual(updated.last_run_at, 45.0)
        self.assertEqual(updated.next_run_at, 105.0)
        self.assertEqual(updated.deferred_at, 0)
        self.assertEqual(updated.deferred_reason, '')

    async def test_due_agent_message_loop_cancel_while_deferred_prevents_later_delivery(self):
        state = self._make_state()
        worker = self.state_mod.AgentCell(
            id='agent-1',
            name='Worker',
            group='g',
            cell_type='agent',
            kind='worker',
            session_id='session-1',
            status='running',
            activity='thinking',
        )
        state.agents[worker.id] = worker
        state.groups['g'] = [worker.id]
        sent = []

        async def fake_send_prompt(cell, prompt, **kwargs):
            sent.append((cell.id, prompt, kwargs))

            async def _delivered():
                return None

            return asyncio.create_task(_delivered())

        async def handle_command(payload):
            return await self.server_mod._handle_user_agent_message_command(
                payload,
                state,
                fake_send_prompt,
            )

        loop = state.agent_message_loop_add(
            agent_id=worker.id,
            group_name='g',
            interval_seconds=60,
            message='should not deliver after cancel',
            created_by='user',
            now=0.0,
        )
        state.agent_message_loop_update(loop.id, next_run_at=10.0)
        server_dispatch = importlib.import_module('torque.server_dispatch')
        server_dispatch = importlib.reload(server_dispatch)

        deferred = await server_dispatch._fire_due_agent_message_loops(
            state,
            handle_command,
            panel_event=None,
            now_ts=10.0,
        )
        self.assertEqual(deferred, [])
        self.assertEqual(state.agent_message_loops[loop.id].deferred_reason,
                         'agent_busy')

        cancelled = await self.server_mod._handle_user_agent_message_command(
            {
                'cmd': 'user_agent_message',
                'agent_id': worker.id,
                'message': '/loop cancel',
                'idempotency_key': 'loop-cancel-while-deferred',
            },
            state,
            fake_send_prompt,
        )
        self.assertEqual(cancelled['type'], 'agent_message_loop')
        self.assertEqual(cancelled['loop']['status'], 'cancelled')
        self.assertEqual(cancelled['loop']['next_run_at'], 0)
        self.assertEqual(cancelled['loop']['deferred_at'], 0)
        self.assertEqual(cancelled['loop']['deferred_reason'], '')

        worker.activity = ''
        later = await server_dispatch._fire_due_agent_message_loops(
            state,
            handle_command,
            panel_event=None,
            now_ts=70.0,
        )
        self.assertEqual(later, [])
        self.assertEqual(sent, [])
        self.assertIsNone(state.active_agent_message_loop_for_agent(worker.id))

    async def test_user_agent_message_busy_target_still_delivers_normal_dm(self):
        state = self._make_state()
        worker = self.state_mod.AgentCell(
            id='agent-1',
            name='Worker',
            group='g',
            cell_type='agent',
            kind='worker',
            session_id='session-1',
            status='running',
            activity='tool_call',
        )
        state.agents[worker.id] = worker
        state.groups['g'] = [worker.id]
        sent = []

        async def fake_send_prompt(cell, prompt, **kwargs):
            sent.append((cell.id, prompt, kwargs))

            async def _delivered():
                return None

            return asyncio.create_task(_delivered())

        result = await self.server_mod._handle_user_agent_message_command(
            {
                'cmd': 'user_agent_message',
                'agent_id': worker.id,
                'message': 'Normal direct messages are unchanged.',
                'idempotency_key': 'normal-dm-busy-target',
            },
            state,
            fake_send_prompt,
        )

        self.assertEqual(result['type'], 'ok')
        self.assertEqual(result['delivery_state'], 'delivered')
        self.assertEqual(len(sent), 1)
        self.assertIn('Normal direct messages are unchanged.', sent[0][1])

    async def test_due_agent_message_loop_fire_broadcasts_fresh_loop_delta_once(self):
        state = self._make_state()
        worker = self.state_mod.AgentCell(
            id='agent-1',
            name='Worker',
            group='g',
            cell_type='agent',
            kind='worker',
            session_id='session-1',
            status='idle',
        )
        state.agents[worker.id] = worker
        state.groups['g'] = [worker.id]
        sent = []

        async def fake_send_prompt(cell, prompt, **kwargs):
            sent.append((cell.id, prompt, kwargs))

            async def _delivered():
                return None

            return asyncio.create_task(_delivered())

        async def handle_command(payload):
            return await self.server_mod._handle_user_agent_message_command(
                payload,
                state,
                fake_send_prompt,
            )

        loop = state.agent_message_loop_add(
            agent_id=worker.id,
            group_name='g',
            interval_seconds=60,
            message='please report progress',
            created_by='user',
            now=0.0,
        )
        state.agent_message_loop_update(loop.id, next_run_at=10.0)
        state._delta_ops.clear()
        broadcasts = []

        async def capture_broadcast():
            broadcasts.append([dict(op) for op in state._delta_ops])
            state._delta_ops = []

        state.broadcast = capture_broadcast
        server_dispatch = importlib.import_module('torque.server_dispatch')
        server_dispatch = importlib.reload(server_dispatch)

        fired = await server_dispatch._fire_due_agent_message_loops(
            state,
            handle_command,
            panel_event=None,
            now_ts=10.0,
        )

        self.assertEqual(len(fired), 1)
        self.assertEqual(len(sent), 1)
        self.assertEqual(len(broadcasts), 1)
        loop_ops = [
            op for op in broadcasts[0]
            if op.get('op') == 'agent_message_loop_upsert'
        ]
        self.assertEqual(len(loop_ops), 1)
        loop_delta = loop_ops[0]['loop']
        self.assertEqual(loop_delta['id'], loop.id)
        self.assertEqual(loop_delta['status'], 'active')
        self.assertEqual(loop_delta['run_count'], 1)
        self.assertEqual(loop_delta['last_run_at'], 10.0)
        self.assertEqual(loop_delta['next_run_at'], 70.0)
        self.assertEqual(loop_delta['last_message_id'], fired[0]['message_id'])
        self.assertEqual(state._delta_ops, [])

    async def test_due_agent_message_loop_offline_stop_broadcasts_once_for_cycle(self):
        state = self._make_state()
        workers = []
        for idx in (1, 2):
            worker = self.state_mod.AgentCell(
                id=f'agent-{idx}',
                name=f'Worker {idx}',
                group='g',
                cell_type='agent',
                kind='worker',
                session_id='',
                status='idle',
            )
            state.agents[worker.id] = worker
            workers.append(worker)
        state.groups['g'] = [worker.id for worker in workers]

        async def fake_send_prompt(*_args, **_kwargs):
            raise AssertionError('offline /loop delivery must not prompt-send')

        async def handle_command(payload):
            return await self.server_mod._handle_user_agent_message_command(
                payload,
                state,
                fake_send_prompt,
            )

        loops = []
        for worker in workers:
            loop = state.agent_message_loop_add(
                agent_id=worker.id,
                group_name='g',
                interval_seconds=60,
                message=f'check offline {worker.id}',
                created_by='user',
                now=0.0,
            )
            state.agent_message_loop_update(loop.id, next_run_at=20.0)
            loops.append(loop)
        state._delta_ops.clear()
        broadcasts = []

        async def capture_broadcast():
            broadcasts.append([dict(op) for op in state._delta_ops])
            state._delta_ops = []

        state.broadcast = capture_broadcast
        server_dispatch = importlib.import_module('torque.server_dispatch')
        server_dispatch = importlib.reload(server_dispatch)

        fired = await server_dispatch._fire_due_agent_message_loops(
            state,
            handle_command,
            panel_event=None,
            now_ts=20.0,
        )

        self.assertEqual(fired, [])
        self.assertEqual(len(broadcasts), 1)
        loop_ops = [
            op for op in broadcasts[0]
            if op.get('op') == 'agent_message_loop_upsert'
        ]
        self.assertEqual(len(loop_ops), 2)
        self.assertEqual(
            {op['loop']['id'] for op in loop_ops},
            {loop.id for loop in loops},
        )
        for op in loop_ops:
            self.assertEqual(op['loop']['status'], 'stopped')
            self.assertEqual(op['loop']['stopped_by'], 'system')
            self.assertEqual(op['loop']['stop_reason'], 'no_session')
            self.assertEqual(op['loop']['next_run_at'], 0)
        self.assertEqual(state._delta_ops, [])
        for loop in loops:
            self.assertEqual(state.agent_message_loops[loop.id].status, 'stopped')
        for worker in workers:
            offline_rows = self.db.load_direct_messages_for_agent(worker.id)
            self.assertTrue(
                any('System stopped /loop because delivery was not live' in row['message']
                    for row in offline_rows)
            )

    async def test_user_agent_message_marks_agent_running_before_delivery_finishes(self):
        state = self._make_state()
        worker = self.state_mod.AgentCell(
            id='agent-1',
            name='Worker',
            group='g',
            cell_type='agent',
            kind='worker',
            session_id='session-1',
            status='idle',
        )
        state.agents[worker.id] = worker
        state.groups['g'] = [worker.id]
        send_entered = asyncio.Event()
        release_delivery = asyncio.Event()

        class BlockingBridge:
            async def send_text(self, _session_id, _text, **_kwargs):
                send_entered.set()
                await release_delivery.wait()

        service = self._make_agent_launch_service(state, BlockingBridge())
        command_task = asyncio.create_task(
            self.server_mod._handle_user_agent_message_command(
                {
                    'cmd': 'user_agent_message',
                    'agent_id': worker.id,
                    'message': 'Start this now.',
                    'idempotency_key': 'optimistic-submit',
                },
                state,
                service.send_agent_prompt,
            )
        )

        await asyncio.wait_for(send_entered.wait(), timeout=1.0)
        self.assertFalse(command_task.done())
        self.assertEqual(worker.status, 'running')
        self.assertGreater(worker.last_progress_at, 0)
        self.assertGreaterEqual(state._seq, 1)

        release_delivery.set()
        result = await command_task
        self.assertEqual(result['delivery_state'], 'delivered')
        self.assertEqual(worker.status, 'running')

    async def test_user_agent_message_reply_hint_cadence_and_replay(self):
        state = self._make_state()
        state.group_settings['g'] = self.state_mod.GroupSettings(
            guidance_hint_cadence=4,
        )
        worker = self.state_mod.AgentCell(
            id='agent-1',
            name='Worker',
            group='g',
            cell_type='agent',
            kind='worker',
            session_id='session-1',
            status='idle',
        )
        state.agents[worker.id] = worker
        state.groups['g'] = [worker.id]
        sent = []

        async def fake_send_prompt(cell, prompt, **kwargs):
            sent.append((cell.id, prompt, kwargs))

            async def _delivered():
                return None

            return asyncio.create_task(_delivered())

        results = []
        for idx in range(1, 4):
            results.append(
                await self.server_mod._handle_user_agent_message_command(
                    {
                        'cmd': 'user_agent_message',
                        'agent_id': worker.id,
                        'message': f'Message {idx}',
                        'idempotency_key': f'cadence-live-{idx}',
                    },
                    state,
                    fake_send_prompt,
                )
            )

        self.assertEqual([r['delivery_state'] for r in results], [
            'delivered',
            'delivered',
            'delivered',
        ])
        self.assertEqual(len(sent), 3)

        worker.session_id = ''
        buffered = await self.server_mod._handle_user_agent_message_command(
            {
                'cmd': 'user_agent_message',
                'agent_id': worker.id,
                'message': 'Message 4 via replay',
                'idempotency_key': 'cadence-buffered-4',
            },
            state,
            fake_send_prompt,
        )
        self.assertEqual(buffered['delivery_state'], 'buffered')
        self.assertEqual(buffered['delivery_reason'], 'no_session')
        self.assertEqual(len(sent), 3)

        worker.session_id = 'session-1'
        replayed = await self.server_mod._replay_buffered_cross_kind_messages(
            state,
            bridge=None,
            target=worker,
            send_prompt=fake_send_prompt,
        )
        self.assertEqual(replayed, 1)

        result5 = await self.server_mod._handle_user_agent_message_command(
            {
                'cmd': 'user_agent_message',
                'agent_id': worker.id,
                'message': 'Message 5',
                'idempotency_key': 'cadence-live-5',
            },
            state,
            fake_send_prompt,
        )
        self.assertEqual(result5['delivery_state'], 'delivered')
        self.assertEqual(len(sent), 5)

        hint = 'Do not rely on free-text terminal output'
        self.assertEqual(
            [hint in prompt for _agent_id, prompt, _kwargs in sent],
            [True, False, False, True, False],
        )
        expected_reply_to_ids = [
            results[0]['message_id'],
            results[1]['message_id'],
            results[2]['message_id'],
            buffered['message_id'],
            result5['message_id'],
        ]
        for (_agent_id, prompt, _kwargs), reply_to_id in zip(
                sent,
                expected_reply_to_ids):
            reply_snippet = (
                'mcp__torque__torque_message_user('
                f'message="...", reply_to_id="{reply_to_id}")'
            )
            self.assertIn(reply_snippet, prompt)
            self.assertNotIn('Message ID:', prompt)
            self.assertNotIn('Thread ID:', prompt)
            self.assertNotIn('Sent:', prompt)

    async def test_user_agent_message_reply_hint_cadence_zero_is_legacy(self):
        state = self._make_state()
        state.group_settings['g'] = self.state_mod.GroupSettings(
            guidance_hint_cadence=0,
        )
        worker = self.state_mod.AgentCell(
            id='agent-1',
            name='Worker',
            group='g',
            cell_type='agent',
            kind='worker',
            session_id='session-1',
            status='idle',
        )
        state.agents[worker.id] = worker
        state.groups['g'] = [worker.id]
        sent = []

        async def fake_send_prompt(cell, prompt, **kwargs):
            sent.append(prompt)

            async def _delivered():
                return None

            return asyncio.create_task(_delivered())

        for idx in range(1, 4):
            await self.server_mod._handle_user_agent_message_command(
                {
                    'cmd': 'user_agent_message',
                    'agent_id': worker.id,
                    'message': f'Legacy cadence {idx}',
                    'idempotency_key': f'cadence-zero-{idx}',
                },
                state,
                fake_send_prompt,
            )

        self.assertEqual(len(sent), 3)
        self.assertTrue(
            all('Do not rely on free-text terminal output' in p for p in sent)
        )
        self.assertTrue(
            all('mcp__torque__torque_message_user(' in p for p in sent)
        )

    async def test_user_agent_message_buffers_down_agent_and_replays_on_wake(self):
        state = self._make_state()
        architect = self.state_mod.AgentCell(
            id='arch-1',
            name='Architect',
            group='g',
            cell_type='agent',
            kind='architect',
            session_id='',
        )
        state.agents[architect.id] = architect
        state.groups['g'] = [architect.id]
        sent = []

        async def fake_send_prompt(cell, prompt, **kwargs):
            sent.append((cell.id, prompt, kwargs))

            async def _delivered():
                return None

            return asyncio.create_task(_delivered())

        result = await self.server_mod._handle_user_agent_message_command(
            {
                'cmd': 'user_agent_message',
                'agent_id': architect.id,
                'message': 'I left context while you were offline.',
                'idempotency_key': 'offline-submit',
            },
            state,
            fake_send_prompt,
        )

        self.assertEqual(result['delivery_state'], 'buffered')
        self.assertEqual(result['delivery_reason'], 'no_session')
        self.assertEqual(sent, [])
        persisted = self.db.load_direct_message(result['message_id'])
        self.assertEqual(persisted['delivery_state'], 'buffered')
        self.assertEqual(persisted['delivery_reason'], 'no_session')

        architect.session_id = 'session-arch'

        class NoRawBridge:
            def prime_input_ready(self, _session_id):
                raise AssertionError('direct replay must not prime bridge raw')

            async def send_text(self, _session_id, _text):
                raise AssertionError('direct replay must not use bridge.send_text')

        replayed = await self.server_mod._replay_buffered_cross_kind_messages(
            state,
            NoRawBridge(),
            architect,
            send_prompt=fake_send_prompt,
        )

        self.assertEqual(replayed, 1)
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][0], architect.id)
        prompt = sent[0][1]
        self.assertNotIn('Message ID:', prompt)
        self.assertNotIn('Thread ID:', prompt)
        self.assertNotIn('Sent:', prompt)
        self.assertIn(
            (
                'mcp__torque__architect_message_user('
                f'message="...", reply_to_id="{result["message_id"]}")'
            ),
            prompt,
        )
        replayed_row = self.db.load_direct_message(result['message_id'])
        self.assertEqual(replayed_row['delivery_state'], 'delivered')
        self.assertEqual(replayed_row['delivery_reason'], '')

    async def test_user_agent_message_buffers_dismissed_agent_until_wake(self):
        state = self._make_state()
        engineer = self.state_mod.AgentCell(
            id='engineer-1',
            name='Engineer',
            group='g',
            cell_type='agent',
            kind='engineer',
            session_id='session-eng',
            dismissed_at=123,
        )
        state.agents[engineer.id] = engineer
        state.groups['g'] = [engineer.id]
        sent = []

        async def fake_send_prompt(cell, prompt, **kwargs):
            sent.append((cell.id, prompt, kwargs))

            async def _delivered():
                return None

            return asyncio.create_task(_delivered())

        result = await self.server_mod._handle_user_agent_message_command(
            {
                'cmd': 'user_agent_message',
                'agent_id': engineer.id,
                'message': 'Context for when you return.',
                'idempotency_key': 'dismissed-submit',
            },
            state,
            fake_send_prompt,
        )

        self.assertEqual(result['delivery_state'], 'buffered')
        self.assertEqual(result['delivery_reason'], 'agent_dismissed')
        self.assertEqual(sent, [])

        engineer.dismissed_at = 0
        replayed = await self.server_mod._replay_buffered_cross_kind_messages(
            state,
            bridge=None,
            target=engineer,
            send_prompt=fake_send_prompt,
        )

        self.assertEqual(replayed, 1)
        self.assertEqual(sent[0][0], engineer.id)
        replayed_row = self.db.load_direct_message(result['message_id'])
        self.assertEqual(replayed_row['delivery_state'], 'delivered')
        self.assertEqual(replayed_row['delivery_reason'], '')

    async def test_user_agent_message_send_failure_stays_buffered_with_reason(self):
        state = self._make_state()
        engineer = self.state_mod.AgentCell(
            id='engineer-1',
            name='Engineer',
            group='g',
            cell_type='agent',
            kind='engineer',
            session_id='session-eng',
            status='idle',
        )
        state.agents[engineer.id] = engineer
        state.groups['g'] = [engineer.id]

        class FailingBridge:
            async def send_text(self, _session_id, _text, **_kwargs):
                raise RuntimeError('terminal unavailable')

        service = self._make_agent_launch_service(state, FailingBridge())
        result = await self.server_mod._handle_user_agent_message_command(
            {
                'cmd': 'user_agent_message',
                'agent_id': engineer.id,
                'message': 'This should retry later.',
                'idempotency_key': 'failure-submit',
            },
            state,
            service.send_agent_prompt,
        )

        self.assertEqual(result['delivery_state'], 'buffered')
        self.assertEqual(result['delivery_reason'], 'terminal unavailable')
        persisted = self.db.load_direct_message(result['message_id'])
        self.assertEqual(persisted['delivery_state'], 'buffered')
        self.assertEqual(persisted['delivery_reason'], 'terminal unavailable')
        self.assertEqual(
            state.direct_messages_by_agent[engineer.id][0]['delivery_reason'],
            'terminal unavailable',
        )
        self.assertEqual(engineer.status, 'idle')

    async def test_user_agent_message_aliases_prompt_unavailable_and_idempotency_conflict(self):
        state = self._make_state()
        worker = self.state_mod.AgentCell(
            id='agent-1',
            name='Worker',
            group='g',
            cell_type='agent',
            kind='worker',
            session_id='session-1',
        )
        state.agents[worker.id] = worker
        state.groups['g'] = [worker.id]

        result = await self.server_mod._handle_user_agent_message_command(
            {
                'cmd': 'user_agent_message',
                'cell_id': worker.id,
                'text': 'Alias payload should persist.',
                'thread_id': 'custom-thread',
                'reply_to_id': 'agent-reply-1',
                'idempotency_key': 'alias-submit',
            },
            state,
            send_prompt=None,
        )
        conflict = await self.server_mod._handle_user_agent_message_command(
            {
                'cmd': 'user_agent_message',
                'target_agent_id': worker.id,
                'message': 'Different payload must be rejected.',
                'idempotency_key': 'alias-submit',
            },
            state,
            send_prompt=None,
        )

        self.assertEqual(result['type'], 'ok')
        self.assertEqual(result['delivery_state'], 'buffered')
        self.assertEqual(result['delivery_reason'], 'send_prompt_unavailable')
        self.assertEqual(result['reply_to_id'], 'agent-reply-1')
        row = self.db.load_direct_message(result['message_id'])
        self.assertEqual(row['message'], 'Alias payload should persist.')
        self.assertEqual(row['thread_id'], result['thread_id'])
        self.assertEqual(row['reply_to_id'], 'agent-reply-1')
        self.assertEqual(row['delivery_state'], 'buffered')
        self.assertEqual(row['delivery_reason'], 'send_prompt_unavailable')
        self.assertEqual(conflict['type'], 'error')
        self.assertIn('idempotency key was reused', conflict['message'])
        count = self.db._conn.execute(
            "SELECT COUNT(*) FROM agent_peer_messages WHERE sender_kind='user' "
            "AND recipient_id=?",
            (worker.id,),
        ).fetchone()[0]
        self.assertEqual(count, 1)

    async def test_user_agent_message_idempotency_dedupes_browser_retry(self):
        state = self._make_state()
        worker = self.state_mod.AgentCell(
            id='agent-1',
            name='Worker',
            group='g',
            cell_type='agent',
            kind='worker',
            session_id='session-1',
        )
        state.agents[worker.id] = worker
        state.groups['g'] = [worker.id]
        sent = []

        async def fake_send_prompt(cell, prompt, **kwargs):
            sent.append((cell.id, prompt, kwargs))

            async def _delivered():
                return None

            return asyncio.create_task(_delivered())

        payload = {
            'cmd': 'user_agent_message',
            'agent_id': worker.id,
            'message': 'Please acknowledge this once.',
            'reply_to_id': 'agent-msg-1',
            'idempotency_key': 'same-browser-submit',
        }
        first = await self.server_mod._handle_user_agent_message_command(
            dict(payload),
            state,
            fake_send_prompt,
        )
        second = await self.server_mod._handle_user_agent_message_command(
            dict(payload),
            state,
            fake_send_prompt,
        )

        self.assertEqual(first['message_id'], second['message_id'])
        self.assertFalse(first['deduped'])
        self.assertTrue(second['deduped'])
        self.assertEqual(len(sent), 1)
        count = self.db._conn.execute(
            "SELECT COUNT(*) FROM agent_peer_messages WHERE sender_kind='user' "
            "AND recipient_id=?",
            (worker.id,),
        ).fetchone()[0]
        self.assertEqual(count, 1)

    async def test_replay_buffered_architect_peer_messages_reads_db_after_cache_loss(self):
        state = self._make_state()
        sender = self.state_mod.AgentCell(
            id='arch-a',
            name='Architect A',
            group='g',
            cell_type='agent',
            kind='architect',
        )
        recipient = self.state_mod.AgentCell(
            id='arch-b',
            name='Architect B',
            group='g',
            cell_type='agent',
            kind='architect',
            session_id='session-b',
        )
        state.agents[sender.id] = sender
        state.agents[recipient.id] = recipient
        state.groups['g'] = [sender.id, recipient.id]
        state.save_peer_message({
            'id': 'msg-peer-buffered',
            'thread_id': 'msg-peer-buffered',
            'group_name': 'g',
            'sender_id': sender.id,
            'sender_kind': 'architect',
            'recipient_id': recipient.id,
            'recipient_kind': 'architect',
            'message': 'Please ack this after restart.',
            'created_at': 123.0,
            'ack_required': True,
            'delivery_state': 'buffered',
            'delivery_reason': 'recipient_dismissed',
        })
        sender.mcp_messages = []
        recipient.mcp_messages = []

        primed = []
        sent = []

        class FakeBridge:
            def prime_input_ready(self, session_id):
                primed.append(session_id)

            async def send_text(self, session_id, text):
                sent.append((session_id, text))

        replayed = await self.server_mod._replay_buffered_cross_kind_messages(
            state,
            FakeBridge(),
            recipient,
        )

        self.assertEqual(replayed, 1)
        self.assertEqual(primed, ['session-b'])
        self.assertEqual(sent[0][0], 'session-b')
        self.assertIn('## Message from Architect A (architect)', sent[0][1])
        self.assertIn('Ack required. Reply with:', sent[0][1])
        self.assertIn(
            'mcp__torque__architect_reply(message_id="msg-peer-buffered"',
            sent[0][1],
        )
        persisted = self.db.load_agent_peer_message('msg-peer-buffered')
        self.assertEqual(persisted['delivery_state'], 'delivered')
        self.assertEqual(persisted['delivery_reason'], '')
        self.assertTrue(recipient.mcp_messages[0]['delivered'])
        self.assertFalse(recipient.mcp_messages[0]['buffered'])
        self.assertTrue(sender.mcp_messages[0]['delivered'])

    async def test_replay_buffered_architect_peer_message_failure_stays_buffered(self):
        state = self._make_state()
        sender = self.state_mod.AgentCell(
            id='arch-a',
            name='Architect A',
            group='g',
            cell_type='agent',
            kind='architect',
        )
        recipient = self.state_mod.AgentCell(
            id='arch-b',
            name='Architect B',
            group='g',
            cell_type='agent',
            kind='architect',
            session_id='session-b',
        )
        state.agents[sender.id] = sender
        state.agents[recipient.id] = recipient
        state.groups['g'] = [sender.id, recipient.id]
        state.save_peer_message({
            'id': 'msg-peer-fail',
            'thread_id': 'msg-peer-fail',
            'group_name': 'g',
            'sender_id': sender.id,
            'sender_kind': 'architect',
            'recipient_id': recipient.id,
            'recipient_kind': 'architect',
            'message': 'Retry me later.',
            'created_at': 124.0,
            'delivery_state': 'buffered',
            'delivery_reason': 'no_session',
        })
        sender.mcp_messages = []
        recipient.mcp_messages = []

        class FailingBridge:
            def prime_input_ready(self, _session_id):
                pass

            async def send_text(self, _session_id, _text):
                raise RuntimeError('terminal unavailable')

        replayed = await self.server_mod._replay_buffered_cross_kind_messages(
            state,
            FailingBridge(),
            recipient,
        )

        self.assertEqual(replayed, 0)
        persisted = self.db.load_agent_peer_message('msg-peer-fail')
        self.assertEqual(persisted['delivery_state'], 'buffered')
        self.assertEqual(persisted['delivery_reason'], 'replay_failed')
        self.assertFalse(recipient.mcp_messages[0]['delivered'])
        self.assertTrue(recipient.mcp_messages[0]['buffered'])
        self.assertEqual(
            recipient.mcp_messages[0]['delivery_reason'],
            'replay_failed',
        )

    async def test_session_start_hook_replays_buffered_architect_peer_message(self):
        state = self._make_state()
        sender = self.state_mod.AgentCell(
            id='arch-a', name='Architect A', group='g',
            cell_type='agent', kind='architect')
        recipient = self.state_mod.AgentCell(
            id='arch-b', name='Architect B', group='g',
            cell_type='agent', kind='architect', session_id='session-b')
        state.agents[sender.id] = sender
        state.agents[recipient.id] = recipient
        state.groups['g'] = [sender.id, recipient.id]
        state.save_peer_message({
            'id': 'msg-wake-peer',
            'thread_id': 'msg-wake-peer',
            'group_name': 'g',
            'sender_id': sender.id,
            'sender_kind': 'architect',
            'recipient_id': recipient.id,
            'recipient_kind': 'architect',
            'message': 'Wake replay regression.',
            'created_at': 125.0,
            'delivery_state': 'buffered',
            'delivery_reason': 'no_session',
        })
        broadcasts = []

        async def fake_broadcast():
            broadcasts.append(True)

        state.broadcast = fake_broadcast
        scheduled = []

        def schedule(coro):
            task = asyncio.create_task(coro)
            scheduled.append(task)
            return task

        class FakeBridge:
            def __init__(self):
                self.ready = []
                self.primed = []
                self.sent = []

            def signal_input_ready(self, cell_id):
                self.ready.append(cell_id)

            def prime_input_ready(self, session_id):
                self.primed.append(session_id)

            async def send_text(self, session_id, text):
                self.sent.append((session_id, text))

        bridge = FakeBridge()
        handler = self.server_mod._make_agent_session_start_handler(
            state, bridge, lambda: None, schedule_task=schedule)

        handler(recipient)
        self.assertEqual(bridge.ready, [recipient.id])
        self.assertEqual(len(scheduled), 1)
        await scheduled[0]

        self.assertEqual(bridge.primed, ['session-b'])
        self.assertEqual(bridge.sent[0][0], 'session-b')
        self.assertIn('Wake replay regression.', bridge.sent[0][1])
        self.assertEqual(broadcasts, [True])
        persisted = self.db.load_agent_peer_message('msg-wake-peer')
        self.assertEqual(persisted['delivery_state'], 'delivered')
        self.assertEqual(persisted['delivery_reason'], '')

    async def test_session_start_hook_skips_dismissed_architect_replay(self):
        state = self._make_state()
        sender = self.state_mod.AgentCell(
            id='arch-a', name='Architect A', group='g',
            cell_type='agent', kind='architect')
        recipient = self.state_mod.AgentCell(
            id='arch-b', name='Architect B', group='g',
            cell_type='agent', kind='architect', session_id='session-b',
            dismissed_at=123)
        state.agents[sender.id] = sender
        state.agents[recipient.id] = recipient
        state.groups['g'] = [sender.id, recipient.id]
        state.save_peer_message({
            'id': 'msg-dismissed-wake-peer',
            'thread_id': 'msg-dismissed-wake-peer',
            'group_name': 'g',
            'sender_id': sender.id,
            'sender_kind': 'architect',
            'recipient_id': recipient.id,
            'recipient_kind': 'architect',
            'message': 'Do not replay while dismissed.',
            'created_at': 126.0,
            'delivery_state': 'buffered',
            'delivery_reason': 'recipient_dismissed',
        })
        scheduled = []

        class FakeBridge:
            def __init__(self):
                self.ready = []

            def signal_input_ready(self, cell_id):
                self.ready.append(cell_id)

        bridge = FakeBridge()
        handler = self.server_mod._make_agent_session_start_handler(
            state, bridge, lambda: None, schedule_task=scheduled.append)

        handler(recipient)

        self.assertEqual(bridge.ready, [recipient.id])
        self.assertEqual(scheduled, [])
        persisted = self.db.load_agent_peer_message('msg-dismissed-wake-peer')
        self.assertEqual(persisted['delivery_state'], 'buffered')
        self.assertEqual(persisted['delivery_reason'], 'recipient_dismissed')

    def test_handle_engineer_reply_completes_follow_up_only_and_preserves_parent_state(self):
        state = self._make_state()
        parent = state.board_add_task(
            'Implement feature',
            'g',
            lane='In Progress',
            id='task-parent',
            agent_id='agent-1',
            status='Reviewing',
        )
        _engineer, worker = self._add_engineer_and_worker(
            state,
            current_task_id=parent.id,
        )
        follow_up = state.board_add_task(
            'Engineer: Need rebase status',
            'g',
            lane='Backlog',
            id='task-reply',
            parent_task_id=parent.id,
            pipeline_root_id=parent.id,
            pipeline_depth=1,
            reply_agent_id=worker.id,
            labels=['torque:derived', 'torque:engineer-message'],
            status='Awaiting Reply',
        )
        state.history_record_dispatch(worker, follow_up)
        worker.pending_engineer_message = True
        events = []

        def panel_event(kind, cell_id, agent_name, group, message, task_id=''):
            events.append((kind, cell_id, agent_name, group, message, task_id))

        result = self.server_mod._handle_engineer_reply(
            state,
            worker,
            message='Rebased successfully',
            task_id=follow_up.id,
            panel_event=panel_event,
        )

        self.assertEqual(result, {'type': 'ok', 'task_id': follow_up.id})
        self.assertEqual(state.board_tasks[follow_up.id].lane, 'Done')
        self.assertEqual(state.board_tasks[follow_up.id].status, '')
        self.assertEqual(state.board_tasks[parent.id].lane, 'In Progress')
        self.assertEqual(state.board_tasks[parent.id].status, 'Reviewing')
        self.assertFalse(worker.pending_engineer_message)
        self.assertEqual(
            state.board_tasks[follow_up.id].messages[-1]['action'],
            'reply',
        )
        self.assertEqual(self.db.load_agent_messages_by_task(parent.id), [])
        reply_messages = self.db.load_agent_messages_by_task(follow_up.id)
        self.assertEqual(reply_messages[0]['action'], 'reply')
        task_rows = self.db.load_agent_tasks(worker.id)
        self.assertEqual(task_rows[0]['task_id'], follow_up.id)
        self.assertEqual(task_rows[0]['outcome'], 'answered')
        self.assertIsNotNone(task_rows[0]['completed_at'])
        self.assertEqual(
            events,
            [('agent_reply', worker.id, worker.name, worker.group,
              'Rebased successfully', follow_up.id)],
        )

    def test_handle_engineer_reply_requires_explicit_task_when_multiple_are_pending(self):
        state = self._make_state()
        _engineer, worker = self._add_engineer_and_worker(state)
        first = state.board_add_task(
            'Engineer: First question',
            'g',
            lane='Backlog',
            id='task-first',
            reply_agent_id=worker.id,
            labels=['torque:engineer-message'],
            status='Awaiting Reply',
        )
        second = state.board_add_task(
            'Engineer: Second question',
            'g',
            lane='Backlog',
            id='task-second',
            reply_agent_id=worker.id,
            labels=['torque:engineer-message'],
            status='Awaiting Reply',
        )
        worker.pending_engineer_message = True

        ambiguous = self.server_mod._handle_engineer_reply(
            state,
            worker,
            message='Need task id',
        )
        answered = self.server_mod._handle_engineer_reply(
            state,
            worker,
            message='Answer for the first thread',
            task_id=first.id,
        )

        self.assertEqual(ambiguous['type'], 'error')
        self.assertIn('Multiple pending engineer messages', ambiguous['message'])
        self.assertEqual(answered, {'type': 'ok', 'task_id': first.id})
        self.assertEqual(state.board_tasks[first.id].lane, 'Done')
        self.assertEqual(state.board_tasks[second.id].lane, 'Backlog')
        self.assertTrue(worker.pending_engineer_message)

    async def test_resolve_human_ask_buffers_answer_for_offline_reply_agent(self):
        state = self._make_state()
        worker = self.state_mod.AgentCell(
            id='agent-1',
            name='Worker',
            group='g',
            cell_type='agent',
            kind='worker',
            session_id='',
        )
        state.agents[worker.id] = worker
        state.groups['g'] = [worker.id]
        parent = state.board_add_task(
            'Implement feature',
            'g',
            lane='In Progress',
            id='task-parent',
            agent_id=worker.id,
            status='Awaiting Input',
        )
        ask = state.board_add_task(
            'Can I use the fallback API?',
            'g',
            lane='Backlog',
            id='ask-1',
            labels=['torque:human', 'torque:derived'],
            parent_task_id=parent.id,
            reply_agent_id=worker.id,
        )
        self.assertIsNotNone(ask)

        async def fake_send_prompt(*_args, **_kwargs):
            self.fail('offline ask reply should buffer, not inject')

        events = []
        result = await self.server_mod._resolve_human_ask_task(
            state,
            ask,
            'Use the minimal fallback.',
            fake_send_prompt,
            panel_event=lambda *args, **kwargs: events.append((args, kwargs)),
        )

        self.assertEqual(result['type'], 'ok')
        self.assertEqual(result['delivery_state'], 'buffered')
        self.assertEqual(result['delivery_reason'], 'no_session')
        self.assertEqual(state.board_tasks[ask.id].lane, 'Done')
        self.assertEqual(state.board_tasks[parent.id].status, '')
        self.assertEqual(events[0][0][0], 'ask_resolved')
        direct_rows = self.db.load_direct_messages_for_agent(worker.id)
        direct_by_type = {row['message_type']: row for row in direct_rows}
        self.assertEqual(set(direct_by_type), {'ask', 'ask_reply'})
        self.assertEqual(
            direct_by_type['ask_reply']['delivery_state'],
            'buffered',
        )
        self.assertEqual(
            direct_by_type['ask_reply']['source_task_id'],
            ask.id,
        )

        sent = []

        async def replay_send_prompt(cell, prompt, **kwargs):
            sent.append((cell.id, prompt, kwargs))

            async def _delivered():
                return None

            return asyncio.create_task(_delivered())

        worker.session_id = 'session-1'
        replayed = await self.server_mod._replay_buffered_cross_kind_messages(
            state,
            bridge=None,
            target=worker,
            send_prompt=replay_send_prompt,
        )

        self.assertEqual(replayed, 1)
        self.assertIn('Can I use the fallback API?', sent[0][1])
        self.assertIn('Use the minimal fallback.', sent[0][1])

    async def test_resolve_architect_ask_delivers_user_reply_to_architect_inbox(self):
        state = self._make_state()
        architect = self.state_mod.AgentCell(
            id='arch-1',
            name='Architect',
            group='g',
            cell_type='agent',
            kind='architect',
            session_id='session-arch',
            status='idle',
        )
        state.agents[architect.id] = architect
        state.groups['g'].append(architect.id)
        ask = state.board_add_task(
            'Should we defer reporting?',
            'g',
            lane='Backlog',
            id='ask-1',
            description='Option A: defer. Option B: delay launch.',
            labels=['torque:human', 'architect-ask'],
            status='Awaiting Input',
            reply_agent_id=architect.id,
            created_by_architect_id=architect.id,
        )
        sent = []
        primed = []
        events = []

        class FakeBridge:
            def prime_input_ready(self, session_id):
                primed.append(session_id)

            async def send_text(self, session_id, text):
                sent.append((session_id, text))

        def panel_event(kind, cell_id, agent_name, group, message, task_id=''):
            events.append((kind, cell_id, agent_name, group, message, task_id))

        result = await self.server_mod._resolve_architect_ask_task(
            state,
            FakeBridge(),
            ask,
            'Defer reporting and launch the smaller scope.',
            panel_event=panel_event,
        )

        self.assertEqual(result['type'], 'ok')
        self.assertEqual(result['task_id'], ask.id)
        self.assertEqual(result['architect_id'], architect.id)
        self.assertEqual(state.board_tasks[ask.id].lane, 'Done')
        self.assertEqual(state.board_tasks[ask.id].status, '')
        self.assertEqual(
            state.board_tasks[ask.id].messages[-1]['action'],
            'architect_ask_reply',
        )
        self.assertEqual(primed, ['session-arch'])
        self.assertEqual(sent[0][0], 'session-arch')
        self.assertIn('User reply to architect ask', sent[0][1])
        self.assertIn('Should we defer reporting?', sent[0][1])
        self.assertIn('Defer reporting', sent[0][1])
        inbox = architect.mcp_messages[0]
        self.assertEqual(inbox['action'], 'architect_ask_reply')
        self.assertEqual(inbox['direction'], 'received')
        self.assertEqual(inbox['peer_kind'], 'human')
        self.assertEqual(inbox['peer_name'], 'User')
        self.assertEqual(inbox['task_id'], ask.id)
        self.assertTrue(inbox['delivered'])
        self.assertFalse(inbox['buffered'])
        self.assertIn('Defer reporting', inbox['message'])
        self.assertEqual(
            events,
            [('ask_resolved', architect.id, architect.name, architect.group,
              'Resolved: Should we defer reporting?', ask.id)],
        )
        direct_rows = self.db.load_direct_messages_for_agent(architect.id)
        direct_by_type = {
            row['message_type']: row
            for row in direct_rows
            if row['source_task_id'] == ask.id
        }
        self.assertEqual(set(direct_by_type), {'ask', 'ask_reply'})
        self.assertEqual(direct_by_type['ask']['sender_id'], architect.id)
        self.assertEqual(direct_by_type['ask']['recipient_id'], 'user')
        self.assertTrue(direct_by_type['ask']['blocking'])
        self.assertEqual(direct_by_type['ask_reply']['sender_id'], 'user')
        self.assertEqual(direct_by_type['ask_reply']['recipient_id'], architect.id)
        self.assertFalse(direct_by_type['ask_reply']['blocking'])
        self.assertEqual(
            direct_by_type['ask_reply']['reply_to_id'],
            direct_by_type['ask']['id'],
        )
        self.assertEqual(
            [entry['id'] for entry in state.direct_messages_by_agent[architect.id]],
            [direct_by_type['ask']['id'], direct_by_type['ask_reply']['id']],
        )


class ServerWorktreeMergeDiffTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub()
        self.state_mod = importlib.import_module('torque.state')
        self.state_mod = importlib.reload(self.state_mod)
        self.server_mod = importlib.import_module('torque.server')
        self.server_mod = importlib.reload(self.server_mod)
        self.server_artifacts = importlib.import_module('torque.server_artifacts')
        self.server_artifacts = importlib.reload(self.server_artifacts)
        self.server_worktrees = importlib.import_module('torque.server_worktrees')
        self.server_worktrees = importlib.reload(self.server_worktrees)
        self.worktree_mod = importlib.import_module('torque.worktree')
        self.worktree_mod = importlib.reload(self.worktree_mod)

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addAsyncCleanup(self._cleanup_tmp)
        self.repo_root = Path(self.tmp.name)
        self.worktree_mgr = self.worktree_mod.WorktreeManager()
        await self._git('init', '-b', 'main')
        await self._git('config', 'user.name', 'Torque Tests')
        await self._git('config', 'user.email', 'torque-tests@example.com')
        (self.repo_root / 'README.md').write_text('base\n')
        await self._git('add', 'README.md')
        await self._git('commit', '-m', 'Initial commit')

    async def _cleanup_tmp(self):
        self.tmp.cleanup()

    async def _git(self, *args, cwd=None):
        proc = await asyncio.create_subprocess_exec(
            'git',
            '-C',
            str(cwd or self.repo_root),
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            self.fail(
                f"git {' '.join(args)} failed: {stderr.decode().strip()}"
            )
        return stdout.decode().strip()

    async def _make_worktree_cell(self):
        cell = self.state_mod.AgentCell(
            id='agent-1',
            name='Worker',
            group='g',
            cell_type='agent',
            command='codex',
            directory=str(self.repo_root),
        )
        wt_path = await self.worktree_mgr.create(
            cell,
            str(self.repo_root),
            base_branch='main',
        )
        self.assertTrue(wt_path)
        cell.directory = wt_path
        return cell

    async def test_merge_diff_snapshot_captures_patch_before_regular_merge(self):
        cell = await self._make_worktree_cell()
        readme = Path(cell.worktree_path) / 'README.md'
        readme.write_text('branch change\n')
        sha = await self.worktree_mgr.checkpoint(
            cell,
            message='Update README',
        )
        self.assertTrue(sha)

        before = await self.server_worktrees._worktree_merge_diff_snapshot(
            cell,
            self.worktree_mgr,
        )
        self.assertIn('+branch change', before['patch_text'])
        self.assertEqual(before['stats']['files'], 1)

        merged = await self.worktree_mgr.server_merge(
            cell,
            'Merge README update',
            squash=False,
        )
        self.assertTrue(merged['ok'], merged.get('error'))

        after = await self.server_worktrees._worktree_merge_diff_snapshot(
            cell,
            self.worktree_mgr,
        )
        self.assertEqual(after['patch_text'], '')
        self.assertEqual(after['stats']['files'], 0)

    def test_merge_diff_preserve_flag_uses_group_default_until_overridden(self):
        state = self.state_mod.MatrixState()
        state.add_group('g')
        state.update_group_settings(
            'g',
            worktree_merge_preserve_diff=True,
        )
        cell = self.state_mod.AgentCell(
            id='agent-1',
            name='Worker',
            group='g',
            cell_type='agent',
        )

        self.assertTrue(
            self.server_mod._worktree_merge_preserve_diff_enabled(
                state,
                cell,
                {},
            )
        )
        self.assertFalse(
            self.server_mod._worktree_merge_preserve_diff_enabled(
                state,
                cell,
                {'preserve_merge_diff': False},
            )
        )

    async def test_preserved_merge_diff_artifact_survives_worktree_removal(self):
        cell = await self._make_worktree_cell()
        readme = Path(cell.worktree_path) / 'README.md'
        readme.write_text('branch change\n')
        sha = await self.worktree_mgr.checkpoint(
            cell,
            message='Update README',
        )
        self.assertTrue(sha)

        snapshot = await self.server_worktrees._worktree_merge_diff_snapshot(
            cell,
            self.worktree_mgr,
        )
        merged = await self.worktree_mgr.server_merge(
            cell,
            'Merge README update',
            squash=False,
        )
        self.assertTrue(merged['ok'], merged.get('error'))

        with tempfile.TemporaryDirectory() as attachments_dir:
            original_dir = self.server_artifacts.ATTACHMENTS_DIR
            self.server_artifacts.ATTACHMENTS_DIR = Path(attachments_dir)
            try:
                artifact = self.server_artifacts.store_preserved_merge_diff(
                    task_id='task-1',
                    patch_text=snapshot['patch_text'],
                    worktree_branch=cell.worktree_branch,
                    base_branch=cell.worktree_base_branch,
                    merge_commit_sha=merged['sha'],
                    boundary_task_id='task-1',
                    diff_stats=snapshot.get('stats'),
                    diff_files=snapshot.get('files'),
                    agent_id=cell.id,
                    agent_name=cell.name,
                )
                artifact_path = Path(artifact['path'])
                wt_path = cell.worktree_path
                removed = await self.worktree_mgr.remove(cell)
                self.assertTrue(artifact_path.is_file())
            finally:
                self.server_artifacts.ATTACHMENTS_DIR = original_dir

        self.assertTrue(removed)
        self.assertFalse(Path(wt_path).exists())

    async def test_merge_success_stays_warning_only_when_diff_persistence_fails(self):
        cell = await self._make_worktree_cell()
        readme = Path(cell.worktree_path) / 'README.md'
        readme.write_text('branch change\n')
        sha = await self.worktree_mgr.checkpoint(
            cell,
            message='Update README',
        )
        self.assertTrue(sha)

        snapshot = await self.server_worktrees._worktree_merge_diff_snapshot(
            cell,
            self.worktree_mgr,
        )
        merged = await self.worktree_mgr.server_merge(
            cell,
            'Merge README update',
            squash=False,
        )
        self.assertTrue(merged['ok'], merged.get('error'))

        state = self.state_mod.MatrixState()
        state.add_group('g')
        boundary_task = self.state_mod.BoardTask(
            id='task-1',
            task='Boundary task',
            group='g',
            lane='Done',
            updated_at='2026-04-10T00:00:00+00:00',
            worktree_boundary={
                'recorded_at': '2026-04-10T00:00:00+00:00',
            },
        )
        state.board_tasks[boundary_task.id] = boundary_task

        emitted = []

        def record_emit(event, **payload):
            emitted.append((event, payload))

        state._emit = record_emit

        def fail_board_update_task(tid, **fields):
            self.assertEqual(tid, boundary_task.id)
            boundary_task.artifacts = self.server_mod.normalize_artifacts(
                fields.get('artifacts', [])
            )
            boundary_task.updated_at = 'broken'
            record_emit('task_upsert', **self.server_mod.asdict(boundary_task))
            raise RuntimeError('simulated save failure')

        state.board_update_task = fail_board_update_task

        with tempfile.TemporaryDirectory() as attachments_dir:
            original_dir = self.server_artifacts.ATTACHMENTS_DIR
            self.server_artifacts.ATTACHMENTS_DIR = Path(attachments_dir)
            try:
                warning = self.server_mod._persist_preserved_merge_diff_warning_only(
                    state,
                    cell,
                    boundary_task,
                    snapshot,
                    merge_commit_sha=merged['sha'],
                )
            finally:
                self.server_artifacts.ATTACHMENTS_DIR = original_dir

        self.assertEqual(
            warning,
            'Merge succeeded, but Torque could not save the preserved diff artifact.',
        )
        self.assertEqual(boundary_task.artifacts, [])
        self.assertEqual(boundary_task.updated_at, '2026-04-10T00:00:00+00:00')
        self.assertEqual(
            [event for event, _payload in emitted],
            ['task_upsert', 'task_upsert'],
        )
        self.assertEqual(
            emitted[0][1]['artifacts'][0]['type'],
            'diff',
        )
        self.assertEqual(emitted[1][1]['artifacts'], [])
        self.assertEqual(
            list(Path(attachments_dir).rglob('*.patch')),
            [],
        )


class ResolvePendingEngineerSpecializationsTests(unittest.TestCase):
    """Cover the apply-at-creation logic for engineer specializations."""

    def setUp(self):
        install_aiohttp_stub()
        self.state_mod = importlib.reload(
            importlib.import_module('torque.state'))
        self.server_mod = importlib.reload(
            importlib.import_module('torque.server'))

    def _make_state(self, group: str = "torque",
                    default_specs=None) -> object:
        state = self.state_mod.MatrixState()
        state.add_group(group)
        if default_specs is not None:
            state.update_group_settings(
                group,
                default_engineer_specializations=list(default_specs),
            )
        return state

    def test_returns_empty_for_non_engineer(self):
        state = self._make_state(default_specs=["ui-frontend"])
        result = self.server_mod._resolve_pending_engineer_specializations(
            {}, state, "torque", is_engineer=False)
        self.assertEqual(result, [])

    def test_falls_back_to_group_default_when_specs_absent(self):
        state = self._make_state(default_specs=["ui-frontend", "react"])
        result = self.server_mod._resolve_pending_engineer_specializations(
            {}, state, "torque", is_engineer=True)
        self.assertEqual(result, ["ui-frontend", "react"])

    def test_explicit_pick_overrides_group_default(self):
        # Explicit user choice replaces the group default verbatim — no merge.
        state = self._make_state(default_specs=["ui-frontend", "react"])
        result = self.server_mod._resolve_pending_engineer_specializations(
            {"specializations": ["rust-systems"]},
            state, "torque", is_engineer=True)
        self.assertEqual(result, ["rust-systems"])

    def test_explicit_empty_list_is_intentional_clear(self):
        # An explicit empty list means "no specs" — must not be repopulated
        # from the group default.
        state = self._make_state(default_specs=["ui-frontend"])
        result = self.server_mod._resolve_pending_engineer_specializations(
            {"specializations": []},
            state, "torque", is_engineer=True)
        self.assertEqual(result, [])

    def test_no_default_and_no_explicit_returns_empty(self):
        state = self._make_state(default_specs=None)
        result = self.server_mod._resolve_pending_engineer_specializations(
            {}, state, "torque", is_engineer=True)
        self.assertEqual(result, [])

    def test_strips_whitespace_and_drops_blanks(self):
        state = self._make_state(default_specs=None)
        result = self.server_mod._resolve_pending_engineer_specializations(
            {"specializations": ["  rust  ", "", "  ", "react"]},
            state, "torque", is_engineer=True)
        self.assertEqual(result, ["rust", "react"])

    def test_default_with_blanks_is_cleaned(self):
        state = self._make_state(default_specs=["", " ui-frontend ", ""])
        result = self.server_mod._resolve_pending_engineer_specializations(
            {}, state, "torque", is_engineer=True)
        self.assertEqual(result, ["ui-frontend"])


class TorqueAiMcpReportToolNamesTests(unittest.TestCase):
    """TORQUE:238 cutover: workers report exclusively via
    `mcp__torque__torque_*` MCP tools (no Bash CLI rewrite bridge). The
    `_TORQUE_AI_MCP_REPORT_TOOL_NAMES` set drives the `/events` capture
    clause's broadcast-suppression for those specific tool names so
    the ai_report `_append_mcp` synthesis isn't double-emitted.
    """

    def setUp(self):
        install_aiohttp_stub()
        self.server_mod = importlib.import_module('torque.server')
        self.server_mod = importlib.reload(self.server_mod)

    def test_report_tool_names_match_actions(self):
        actions = self.server_mod._TORQUE_AI_MCP_REPORT_ACTIONS
        tool_names = self.server_mod._TORQUE_AI_MCP_REPORT_TOOL_NAMES
        # Every whitelisted action must have a corresponding fully-
        # qualified MCP tool name in the suppression set.
        self.assertEqual(
            tool_names,
            frozenset("mcp__torque__torque_" + a for a in actions),
        )

    def test_covers_all_nine_worker_reporting_actions(self):
        # The cutover whitelist must cover every action the MCP server
        # in `torque/mcp.py` routes to `cmd=ai_report`. If a new worker
        # action is added there, it MUST also be added here or the
        # `/events` PostToolUse for that tool will fire a duplicate
        # `mcp_call_append` broadcast (re-introducing the TORQUE:236
        # firehose pattern).
        expected = {
            "progress", "done", "blocked", "error",
            "ask", "derive", "ready", "verify", "name",
        }
        self.assertEqual(
            set(self.server_mod._TORQUE_AI_MCP_REPORT_ACTIONS),
            expected,
        )

    def test_engineer_architect_tool_names_NOT_in_suppression_set(self):
        # Engineer/architect MCP tools (e.g. `mcp__torque__engineer_*`,
        # `mcp__torque__architect_*`) MUST NOT appear in the suppression
        # set — they don't go through `_append_mcp`, so suppressing
        # the `/events` capture clause for them would lose the live
        # delta entirely.
        suppressed = self.server_mod._TORQUE_AI_MCP_REPORT_TOOL_NAMES
        for tool in (
            "mcp__torque__engineer_task_create",
            "mcp__torque__engineer_task_dispatch",
            "mcp__torque__architect_message_engineer",
            "mcp__torque__architect_journal",
            "mcp__torque__torque_reply",  # `reply` is not a worker reporting action
        ):
            self.assertNotIn(
                tool, suppressed,
                f"{tool} must not be in the report-tool-name suppression set",
            )

    def test_bridge_function_removed(self):
        # The `_maybe_torque_ai_mcp_tool_name` bridge from `:236` v1-v3
        # is gone — workers no longer use the Bash CLI, so the Bash
        # PostToolUse rewriting path is no longer needed. If this
        # symbol comes back, it likely means a regression that
        # re-introduces the dual-broadcast firehose.
        self.assertFalse(
            hasattr(self.server_mod, "_maybe_torque_ai_mcp_tool_name"),
            "_maybe_torque_ai_mcp_tool_name should be removed in TORQUE:238",
        )


class DirectMcpCallObservationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub()
        self.state_mod = importlib.import_module('torque.state')
        self.state_mod = importlib.reload(self.state_mod)
        self.server_mod = importlib.import_module('torque.server')
        self.server_mod = importlib.reload(self.server_mod)

    async def test_direct_mcp_observation_persists_and_live_emits_panel_row(self):
        state = self.state_mod.MatrixState()
        state.agents["worker-1"] = self.state_mod.AgentCell(
            id="worker-1",
            name="Worker",
            group="alpha",
            cell_type="agent",
            kind="worker",
        )

        class FakeIngestClient:
            def __init__(self):
                self.append_calls = []

            async def append(self, envelope, *, idempotency_key=""):
                self.append_calls.append((envelope, idempotency_key))
                return {"type": "ok", "cursor": 42, "duplicate": False}

        class FakeWS:
            def __init__(self):
                self.messages = []

            async def send_str(self, message):
                self.messages.append(json.loads(message))

        client = FakeIngestClient()
        ws = FakeWS()
        state._ws_clients.add(ws)

        await self.server_mod._record_mcp_call_observation(
            state,
            client,
            {
                "cell_id": "worker-1",
                "tool_name": "mcp__torque__torque_context",
                "hook_event_name": "PostToolUse",
                "session_id": "sess-1",
                "request_id": "ctx-1",
                "arguments": {"include": "summary"},
                "result": {"content": [{"type": "text", "text": "{}"}]},
                "is_error": False,
                "duration_ms": 7,
            },
        )

        self.assertEqual(len(client.append_calls), 1)
        envelope, event_id = client.append_calls[0]
        self.assertTrue(event_id.startswith(
            "mcp-direct:worker-1:mcp__torque__torque_context:"
        ))
        self.assertEqual(envelope["headers"]["X-Torque-Cell-Id"], "worker-1")
        self.assertEqual(
            envelope["raw"]["tool_name"],
            "mcp__torque__torque_context",
        )
        self.assertEqual(envelope["raw"]["tool_input"], {"include": "summary"})

        self.assertEqual(len(ws.messages), 1)
        ops = ws.messages[0]["ops"]
        self.assertEqual([op["op"] for op in ops], ["mcp_call_append"])
        call = ops[0]["call"]
        self.assertEqual(call["cursor"], 42)
        self.assertEqual(call["cell_id"], "worker-1")
        self.assertEqual(call["tool_name"], "mcp__torque__torque_context")
        self.assertEqual(call["hook_event_name"], "PostToolUse")
        self.assertEqual(call["agent_name"], "Worker")
        self.assertEqual(call["group"], "alpha")
        self.assertTrue(call["args_redacted"])
        self.assertEqual(call["args"]["arg_keys"], ["include"])

    async def test_report_tool_observation_persists_without_duplicate_live_delta(self):
        state = self.state_mod.MatrixState()
        state.agents["worker-1"] = self.state_mod.AgentCell(
            id="worker-1",
            name="Worker",
            group="alpha",
            cell_type="agent",
            kind="worker",
        )

        class FakeIngestClient:
            async def append(self, envelope, *, idempotency_key=""):
                return {"type": "ok", "cursor": 1, "duplicate": False}

        class FakeWS:
            def __init__(self):
                self.messages = []

            async def send_str(self, message):
                self.messages.append(json.loads(message))

        ws = FakeWS()
        state._ws_clients.add(ws)

        await self.server_mod._record_mcp_call_observation(
            state,
            FakeIngestClient(),
            {
                "cell_id": "worker-1",
                "tool_name": "mcp__torque__torque_progress",
                "arguments": {"message": "working"},
                "result": {"content": [{"type": "text", "text": "{}"}]},
                "is_error": False,
            },
        )

        self.assertEqual(ws.messages, [])

    async def test_claude_code_direct_observation_skips_existing_hook_path(self):
        state = self.state_mod.MatrixState()
        state.agents["worker-1"] = self.state_mod.AgentCell(
            id="worker-1",
            name="Worker",
            group="alpha",
            cell_type="agent",
            kind="worker",
            agent_type="claude-code",
        )

        class FakeIngestClient:
            def __init__(self):
                self.append_calls = []

            async def append(self, envelope, *, idempotency_key=""):
                self.append_calls.append((envelope, idempotency_key))
                return {"type": "ok", "cursor": 1, "duplicate": False}

        client = FakeIngestClient()
        await self.server_mod._record_mcp_call_observation(
            state,
            client,
            {
                "cell_id": "worker-1",
                "tool_name": "mcp__torque__torque_context",
                "arguments": {},
                "result": {"content": [{"type": "text", "text": "{}"}]},
                "is_error": False,
            },
        )

        self.assertEqual(client.append_calls, [])


if __name__ == '__main__':
    unittest.main()

import asyncio
import importlib
import json
import os
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
        self.server_actions = importlib.import_module('loom.server_actions')
        self.server_actions = importlib.reload(self.server_actions)
        self.server_dispatch = importlib.import_module('loom.server_dispatch')
        self.server_dispatch = importlib.reload(self.server_dispatch)
        self.server_worktrees = importlib.import_module('loom.server_worktrees')
        self.server_worktrees = importlib.reload(self.server_worktrees)
        self.state_mod = importlib.import_module('loom.state')
        self.state_mod = importlib.reload(self.state_mod)
        self.server_mod = importlib.import_module('loom.server')
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
        actions_mod = importlib.import_module('loom.actions')
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

    def test_standalone_mode_skips_keybinding_installation(self):
        old = self.server_mod.STANDALONE
        try:
            self.server_mod.STANDALONE = False
            self.assertTrue(self.server_mod._should_install_keybindings())
            self.server_mod.STANDALONE = True
            self.assertFalse(self.server_mod._should_install_keybindings())
        finally:
            self.server_mod.STANDALONE = old

    def test_get_keybinding_defaults_in_standalone_mode_returns_empty_dict(self):
        # Standalone mode leaves the keybindings module unimported;
        # the helper must not call get_default_bindings() on None.
        self.assertEqual(self.server_mod._get_keybinding_defaults(None), {})

    def test_get_keybinding_defaults_delegates_to_module_when_present(self):
        sentinel = {'add_agent': {'modifiers': ['cmd'], 'character': 'a'}}
        fake_module = types.SimpleNamespace(
            get_default_bindings=lambda: sentinel,
        )
        self.assertEqual(
            self.server_mod._get_keybinding_defaults(fake_module),
            sentinel,
        )

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
                        {"cmd": cmd}, {"LOOM_CELL_ID": "worker-1"}, "127.0.0.1")

                self.assertEqual(result["status"], 403)
                self.assertIn("force=true", result["message"])
                self.assertIsNone(self.server_mod._api_worker_context_guard(
                    {"cmd": cmd, "force": True},
                    {"LOOM_CELL_ID": "worker-1"},
                    "127.0.0.1"))
        result = self.server_mod._api_worker_context_guard(
            {"cmd": "restart", "loom_cell_id": "worker-2"},
            {},
            "127.0.0.1",
        )
        self.assertEqual(result["status"], 403)
        self.assertIn("worker-2", result["message"])

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

    def test_engineer_journal_snapshot_command_returns_group_payloads(self):
        state = self.state_mod.MatrixState()
        state.groups['g'] = []
        state.engineer_worklog['g'] = [
            {'id': 2, 'entry': 'new'},
            {'id': 1, 'entry': 'old'},
        ]
        state.journal_read = lambda group, limit=20, **_kwargs: [
            {'id': 1, 'group': group, 'entry': f'limit={limit}'},
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
            {'g': [{'id': 1, 'group': 'g', 'entry': 'limit=7'}]},
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
        from loom.db import LoomDB

        with tempfile.TemporaryDirectory() as tmp:
            db = LoomDB(Path(tmp) / 'loom.db')
            db.init()
            self.addCleanup(db.close)
            db.save_agent(
                self.state_mod.AgentCell(
                    id='engineer-1',
                    name='Engineer',
                    group='loom',
                    slug='engineer',
                    cell_type='agent',
                    kind='engineer',
                    persistent=True,
                )
            )

            result = self.server_mod._handle_doctor_command(db)

        self.assertEqual(result['schema_version'], 2)
        self.assertEqual(result['result'], 'pass')
        self.assertIn('migration', result)
        self.assertIn('checks', result)
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
            worktree_branch='loom/worker-1',
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
        self.assertIn('branch=loom/worker-1', events[0]['message'])

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
            worktree_branch='loom/worker-1',
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

    def test_role_commands_and_template_compat_dispatch_through_helper(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / 'repo' / 'subdir'
            project.mkdir(parents=True)
            (root / 'repo' / '.loom' / 'agents').mkdir(parents=True)
            home = root / 'home'
            (home / '.loom').mkdir(parents=True)
            (home / '.loom' / 'agents').mkdir(parents=True)
            prev_home = os.environ.get('HOME')
            os.environ['HOME'] = str(home)
            self.addCleanup(
                lambda: os.environ.__setitem__('HOME', prev_home)
                if prev_home is not None
                else os.environ.pop('HOME', None)
            )

            roles_mod = importlib.import_module('loom.roles')
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
                (home / '.loom' / 'roles' / 'demo.yaml').read_text(
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
            self.assertTrue((project / '.loom' / 'roles' / 'compat.yaml').is_file())
            self.assertFalse((root / 'repo' / '.loom' / 'agents' / 'compat.yaml').exists())

            legacy_user_template = home / '.loom' / 'agents' / 'legacy.yaml'
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

            legacy_rename_path = home / '.loom' / 'agents' / 'rename-me.yaml'
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
            self.assertTrue((home / '.loom' / 'roles' / 'renamed.yaml').is_file())

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
            self.assertFalse((home / '.loom' / 'roles' / 'demo.yaml').exists())


class ServerPromptQueueTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub()
        self.state_mod = importlib.import_module('loom.state')
        self.state_mod = importlib.reload(self.state_mod)
        self.server_mod = importlib.import_module('loom.server')
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
        )
        events = []

        def panel_event(kind, cell_id, agent_name, group, message, task_id=''):
            events.append((kind, cell_id, agent_name, group, message, task_id))

        result = await self.server_mod._handle_engineer_dismiss_note_command(
            {'group': 'g'},
            state,
            panel_event,
        )

        self.assertEqual(result, {'type': 'ok'})
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
        state = self.state_mod.MatrixState()
        state.add_group('g')
        state.update_engineer_settings(
            'g',
            pending_question='Need approval',
            paused=True,
        )
        engineer = self.state_mod.AgentCell(
            id='engineer-1',
            name='Engineer',
            group='g',
            cell_type='agent',
            session_id='session-1',
        )
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
        self.state_mod = importlib.import_module('loom.state')
        self.state_mod = importlib.reload(self.state_mod)
        self.server_mod = importlib.import_module('loom.server')
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
        self.state_mod = importlib.import_module('loom.state')
        self.state_mod = importlib.reload(self.state_mod)
        self.server_mod = importlib.import_module('loom.server')
        self.server_mod = importlib.reload(self.server_mod)

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
                'env_vars': {'LOOM_ENV': '1'},
                'env_file': '/tmp/loom.env',
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
                'env_file': '/tmp/loom.env',
            }],
        )
        self.assertEqual(
            created,
            [{
                'directory': '/repo',
                'kwargs': {
                    'env_vars': {'LOOM_ENV': '1'},
                    'env_file': '/tmp/loom.env',
                    'shell': 'zsh',
                    'system_prompt': 'system prompt',
                    'mcp_entrypoint': 'loom/mcp.py',
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
        self.state_mod = importlib.import_module('loom.state')
        self.state_mod = importlib.reload(self.state_mod)
        self.server_mod = importlib.import_module('loom.server')
        self.server_mod = importlib.reload(self.server_mod)
        from loom.db import LoomDB

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = LoomDB(Path(self.tmp.name) / 'loom.db')
        self.db.init()
        self.addCleanup(self.db.close)

    def _make_state(self):
        state = self.state_mod.MatrixState(db=self.db)
        state.groups['g'] = []
        return state

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
        self.assertIn('loom:derived', follow_up.labels)
        self.assertIn('loom:engineer-message', follow_up.labels)
        self.assertEqual(follow_up.messages[-1]['action'], 'engineer_message')
        self.assertTrue(worker.pending_engineer_message)
        self.assertEqual(primed, ['session-1'])
        self.assertEqual(sent[0][0], 'session-1')
        self.assertIn(f'Task: {follow_up.id}', sent[0][1])
        self.assertIn(
            f'loom_reply(task=\"{follow_up.id}\", message=\"your response\")',
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
        self.assertEqual(follow_up.labels, ['loom:engineer-message'])

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
        self.assertNotIn('loom_reply', sent[0][1])
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
            side_effect=[10.0, 10.5, 20.0, 20.5],
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
            labels=['loom:derived', 'loom:engineer-message'],
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
            labels=['loom:engineer-message'],
            status='Awaiting Reply',
        )
        second = state.board_add_task(
            'Engineer: Second question',
            'g',
            lane='Backlog',
            id='task-second',
            reply_agent_id=worker.id,
            labels=['loom:engineer-message'],
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
            labels=['loom:human', 'architect-ask'],
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


class ServerWorktreeMergeDiffTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub()
        self.state_mod = importlib.import_module('loom.state')
        self.state_mod = importlib.reload(self.state_mod)
        self.server_mod = importlib.import_module('loom.server')
        self.server_mod = importlib.reload(self.server_mod)
        self.server_artifacts = importlib.import_module('loom.server_artifacts')
        self.server_artifacts = importlib.reload(self.server_artifacts)
        self.server_worktrees = importlib.import_module('loom.server_worktrees')
        self.server_worktrees = importlib.reload(self.server_worktrees)
        self.worktree_mod = importlib.import_module('loom.worktree')
        self.worktree_mod = importlib.reload(self.worktree_mod)

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addAsyncCleanup(self._cleanup_tmp)
        self.repo_root = Path(self.tmp.name)
        self.worktree_mgr = self.worktree_mod.WorktreeManager()
        await self._git('init', '-b', 'main')
        await self._git('config', 'user.name', 'Loom Tests')
        await self._git('config', 'user.email', 'loom-tests@example.com')
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
            'Merge succeeded, but Loom could not save the preserved diff artifact.',
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
            importlib.import_module('loom.state'))
        self.server_mod = importlib.reload(
            importlib.import_module('loom.server'))

    def _make_state(self, group: str = "loom",
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
            {}, state, "loom", is_engineer=False)
        self.assertEqual(result, [])

    def test_falls_back_to_group_default_when_specs_absent(self):
        state = self._make_state(default_specs=["ui-frontend", "react"])
        result = self.server_mod._resolve_pending_engineer_specializations(
            {}, state, "loom", is_engineer=True)
        self.assertEqual(result, ["ui-frontend", "react"])

    def test_explicit_pick_overrides_group_default(self):
        # Explicit user choice replaces the group default verbatim — no merge.
        state = self._make_state(default_specs=["ui-frontend", "react"])
        result = self.server_mod._resolve_pending_engineer_specializations(
            {"specializations": ["rust-systems"]},
            state, "loom", is_engineer=True)
        self.assertEqual(result, ["rust-systems"])

    def test_explicit_empty_list_is_intentional_clear(self):
        # An explicit empty list means "no specs" — must not be repopulated
        # from the group default.
        state = self._make_state(default_specs=["ui-frontend"])
        result = self.server_mod._resolve_pending_engineer_specializations(
            {"specializations": []},
            state, "loom", is_engineer=True)
        self.assertEqual(result, [])

    def test_no_default_and_no_explicit_returns_empty(self):
        state = self._make_state(default_specs=None)
        result = self.server_mod._resolve_pending_engineer_specializations(
            {}, state, "loom", is_engineer=True)
        self.assertEqual(result, [])

    def test_strips_whitespace_and_drops_blanks(self):
        state = self._make_state(default_specs=None)
        result = self.server_mod._resolve_pending_engineer_specializations(
            {"specializations": ["  rust  ", "", "  ", "react"]},
            state, "loom", is_engineer=True)
        self.assertEqual(result, ["rust", "react"])

    def test_default_with_blanks_is_cleaned(self):
        state = self._make_state(default_specs=["", " ui-frontend ", ""])
        result = self.server_mod._resolve_pending_engineer_specializations(
            {}, state, "loom", is_engineer=True)
        self.assertEqual(result, ["ui-frontend"])


class LoomAiMcpReportToolNamesTests(unittest.TestCase):
    """LOOM:238 cutover: workers report exclusively via
    `mcp__loom__loom_*` MCP tools (no Bash CLI rewrite bridge). The
    `_LOOM_AI_MCP_REPORT_TOOL_NAMES` set drives the `/events` capture
    clause's broadcast-suppression for those specific tool names so
    the ai_report `_append_mcp` synthesis isn't double-emitted.
    """

    def setUp(self):
        install_aiohttp_stub()
        self.server_mod = importlib.import_module('loom.server')
        self.server_mod = importlib.reload(self.server_mod)

    def test_report_tool_names_match_actions(self):
        actions = self.server_mod._LOOM_AI_MCP_REPORT_ACTIONS
        tool_names = self.server_mod._LOOM_AI_MCP_REPORT_TOOL_NAMES
        # Every whitelisted action must have a corresponding fully-
        # qualified MCP tool name in the suppression set.
        self.assertEqual(
            tool_names,
            frozenset("mcp__loom__loom_" + a for a in actions),
        )

    def test_covers_all_nine_worker_reporting_actions(self):
        # The cutover whitelist must cover every action the MCP server
        # in `loom/mcp.py` routes to `cmd=ai_report`. If a new worker
        # action is added there, it MUST also be added here or the
        # `/events` PostToolUse for that tool will fire a duplicate
        # `mcp_call_append` broadcast (re-introducing the LOOM:236
        # firehose pattern).
        expected = {
            "progress", "done", "blocked", "error",
            "ask", "derive", "ready", "verify", "name",
        }
        self.assertEqual(
            set(self.server_mod._LOOM_AI_MCP_REPORT_ACTIONS),
            expected,
        )

    def test_engineer_architect_tool_names_NOT_in_suppression_set(self):
        # Engineer/architect MCP tools (e.g. `mcp__loom__engineer_*`,
        # `mcp__loom__architect_*`) MUST NOT appear in the suppression
        # set — they don't go through `_append_mcp`, so suppressing
        # the `/events` capture clause for them would lose the live
        # delta entirely.
        suppressed = self.server_mod._LOOM_AI_MCP_REPORT_TOOL_NAMES
        for tool in (
            "mcp__loom__engineer_task_create",
            "mcp__loom__engineer_task_dispatch",
            "mcp__loom__architect_message_engineer",
            "mcp__loom__architect_journal",
            "mcp__loom__loom_reply",  # `reply` is not a worker reporting action
        ):
            self.assertNotIn(
                tool, suppressed,
                f"{tool} must not be in the report-tool-name suppression set",
            )

    def test_bridge_function_removed(self):
        # The `_maybe_loom_ai_mcp_tool_name` bridge from `:236` v1-v3
        # is gone — workers no longer use the Bash CLI, so the Bash
        # PostToolUse rewriting path is no longer needed. If this
        # symbol comes back, it likely means a regression that
        # re-introduces the dual-broadcast firehose.
        self.assertFalse(
            hasattr(self.server_mod, "_maybe_loom_ai_mcp_tool_name"),
            "_maybe_loom_ai_mcp_tool_name should be removed in LOOM:238",
        )


if __name__ == '__main__':
    unittest.main()

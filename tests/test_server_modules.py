import importlib
import asyncio
import tempfile
import types
import unittest
from pathlib import Path

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

    def test_standalone_mode_skips_keybinding_installation(self):
        self.assertFalse(self.server_mod.STANDALONE)
        self.assertTrue(self.server_mod._should_install_keybindings())

        old = self.server_mod.STANDALONE
        try:
            self.server_mod.STANDALONE = True
            self.assertFalse(self.server_mod._should_install_keybindings())
        finally:
            self.server_mod.STANDALONE = old

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

    def test_weaver_flush_now_command_reports_success(self):
        calls = []

        class FakeBuffer:
            def request_manual_flush(self, group):
                calls.append(group)
                return True, ""

        result = self.server_mod._handle_weaver_flush_now_command(
            FakeBuffer(),
            {'group': 'g'},
        )

        self.assertEqual(calls, ['g'])
        self.assertEqual(result, {'type': 'ok'})

    def test_weaver_flush_now_command_surfaces_pause_error(self):
        class FakeBuffer:
            def request_manual_flush(self, group):
                self.group = group
                return False, "Delivery is paused"

        buffer = FakeBuffer()
        result = self.server_mod._handle_weaver_flush_now_command(
            buffer,
            {'group': 'g'},
        )

        self.assertEqual(buffer.group, 'g')
        self.assertEqual(
            result,
            {'type': 'error', 'message': 'Delivery is paused'},
        )


class ServerPromptQueueTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub()
        self.state_mod = importlib.import_module('loom.state')
        self.state_mod = importlib.reload(self.state_mod)
        self.server_mod = importlib.import_module('loom.server')
        self.server_mod = importlib.reload(self.server_mod)

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
        state.update_weaver_settings('g', paused=True)
        self.assertTrue(state.get_weaver_settings('g').paused)

        release.set()
        await queued_tasks[0]

    async def test_deliver_weaver_reply_waits_for_prompt_before_resuming_delivery(self):
        state = self.state_mod.MatrixState()
        state.add_group('g')
        state.update_weaver_settings(
            'g',
            pending_question='Need approval',
            paused=True,
        )
        weaver = self.state_mod.AgentCell(
            id='weaver-1',
            name='Weaver',
            group='g',
            cell_type='agent',
            session_id='session-1',
        )
        sequence = []
        started = asyncio.Event()
        release = asyncio.Event()

        async def fake_send_prompt(cell, prompt, **kwargs):
            self.assertIs(cell, weaver)
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
            self.server_mod._deliver_weaver_reply_and_resume(
                state,
                weaver,
                group='g',
                answer='Ship it',
                send_prompt=fake_send_prompt,
                weaver_buffer=FakeBuffer(),
            )
        )

        await started.wait()
        self.assertEqual(sequence, ['reply-start'])
        ws = state.get_weaver_settings('g')
        self.assertTrue(ws.paused)
        self.assertEqual(ws.pending_question, 'Need approval')

        release.set()
        result = await task

        self.assertEqual(result, {'type': 'ok'})
        self.assertEqual(sequence, ['reply-start', 'reply-finish', 'resume:g'])
        ws = state.get_weaver_settings('g')
        self.assertFalse(ws.paused)
        self.assertEqual(ws.pending_question, '')


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
            created_by_weaver_id='weaver-1',
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
        self.assertEqual(cell.created_by_weaver_id, 'weaver-1')
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


class ServerWeaverMessageFlowTests(unittest.IsolatedAsyncioTestCase):
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

    def _add_weaver_and_worker(self, state, *, current_task_id=''):
        weaver = self.state_mod.AgentCell(
            id='weaver-1',
            name='Weaver',
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
        state.agents[weaver.id] = weaver
        state.agents[worker.id] = worker
        state.groups['g'] = [weaver.id, worker.id]
        state.group_settings['g'] = self.state_mod.GroupSettings(
            weaver_agent_id=weaver.id
        )
        state.history_record_agent(worker)
        return weaver, worker

    async def test_send_weaver_message_creates_derived_follow_up_task_and_history(self):
        state = self._make_state()
        parent = state.board_add_task(
            'Implement feature',
            'g',
            lane='In Progress',
            id='task-parent',
            agent_id='agent-1',
        )
        _weaver, worker = self._add_weaver_and_worker(
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

        result = await self.server_mod._send_weaver_message_to_agent(
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
        self.assertIn('loom:weaver-message', follow_up.labels)
        self.assertEqual(follow_up.messages[-1]['action'], 'weaver_message')
        self.assertTrue(worker.pending_weaver_message)
        self.assertEqual(primed, ['session-1'])
        self.assertEqual(sent[0][0], 'session-1')
        self.assertIn(f'Task: {follow_up.id}', sent[0][1])
        self.assertIn(
            f'loom_reply(task=\"{follow_up.id}\", message=\"your response\")',
            sent[0][1],
        )
        self.assertEqual(events, [{
            'kind': 'weaver_message',
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
            state.weaver_worklog[worker.group][0]['task_id'],
            follow_up.id,
        )
        self.assertEqual(
            self.db.load_agent_messages_by_task(follow_up.id)[0]['action'],
            'weaver_message',
        )

    async def test_send_weaver_message_without_active_task_creates_root_follow_up(self):
        state = self._make_state()
        _weaver, worker = self._add_weaver_and_worker(state)

        class FakeBridge:
            def prime_input_ready(self, _session_id):
                pass

            async def send_text(self, _session_id, _text):
                return None

        result = await self.server_mod._send_weaver_message_to_agent(
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
        self.assertEqual(follow_up.labels, ['loom:weaver-message'])

    def test_handle_weaver_reply_completes_follow_up_only_and_preserves_parent_state(self):
        state = self._make_state()
        parent = state.board_add_task(
            'Implement feature',
            'g',
            lane='In Progress',
            id='task-parent',
            agent_id='agent-1',
            status='Reviewing',
        )
        _weaver, worker = self._add_weaver_and_worker(
            state,
            current_task_id=parent.id,
        )
        follow_up = state.board_add_task(
            'Weaver: Need rebase status',
            'g',
            lane='Backlog',
            id='task-reply',
            parent_task_id=parent.id,
            pipeline_root_id=parent.id,
            pipeline_depth=1,
            reply_agent_id=worker.id,
            labels=['loom:derived', 'loom:weaver-message'],
            status='Awaiting Reply',
        )
        state.history_record_dispatch(worker, follow_up)
        worker.pending_weaver_message = True
        events = []

        def panel_event(kind, cell_id, agent_name, group, message, task_id=''):
            events.append((kind, cell_id, agent_name, group, message, task_id))

        result = self.server_mod._handle_weaver_reply(
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
        self.assertFalse(worker.pending_weaver_message)
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

    def test_handle_weaver_reply_requires_explicit_task_when_multiple_are_pending(self):
        state = self._make_state()
        _weaver, worker = self._add_weaver_and_worker(state)
        first = state.board_add_task(
            'Weaver: First question',
            'g',
            lane='Backlog',
            id='task-first',
            reply_agent_id=worker.id,
            labels=['loom:weaver-message'],
            status='Awaiting Reply',
        )
        second = state.board_add_task(
            'Weaver: Second question',
            'g',
            lane='Backlog',
            id='task-second',
            reply_agent_id=worker.id,
            labels=['loom:weaver-message'],
            status='Awaiting Reply',
        )
        worker.pending_weaver_message = True

        ambiguous = self.server_mod._handle_weaver_reply(
            state,
            worker,
            message='Need task id',
        )
        answered = self.server_mod._handle_weaver_reply(
            state,
            worker,
            message='Answer for the first thread',
            task_id=first.id,
        )

        self.assertEqual(ambiguous['type'], 'error')
        self.assertIn('Multiple pending weaver messages', ambiguous['message'])
        self.assertEqual(answered, {'type': 'ok', 'task_id': first.id})
        self.assertEqual(state.board_tasks[first.id].lane, 'Done')
        self.assertEqual(state.board_tasks[second.id].lane, 'Backlog')
        self.assertTrue(worker.pending_weaver_message)


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


if __name__ == '__main__':
    unittest.main()

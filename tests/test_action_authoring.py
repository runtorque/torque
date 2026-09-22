"""Real scoped files exercise the action editor command contract."""
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from torque.actions import ActionManager, parse_yaml
from torque.commands.catalog import handle_catalog_command
from torque.server_actions import _action_to_yaml


class ActionAuthoringTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        self.home = self.root / 'home'
        self.home.mkdir()
        env = patch.dict(os.environ, {'HOME': str(self.home)})
        env.start()
        self.addCleanup(env.stop)
        self.manager = ActionManager()

        async def resolve(_group):
            return str(self.project)

        self.runtime = SimpleNamespace(state=None, db=None, template_mgr=None,
            specialization_mgr=None, action_mgr=self.manager,
            resolve_base_dir=resolve, handle_set_engineer_specializations_command=None,
            action_to_yaml=_action_to_yaml)

    def seed(self, scope, name='build', **values):
        directory = self.home if scope == 'user' else self.project
        path = directory / '.torque' / 'actions' / (name + '.yaml')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_action_to_yaml(name, {'prompt': '{{ TASK }}', **values}))
        return path

    async def command(self, cmd, **data):
        return await handle_catalog_command({'cmd': cmd, 'group': 'g', 'name': 'build', **data}, self.runtime)

    async def test_explicit_scopes_never_fall_back_and_legacy_uses_precedence(self):
        user = self.seed('user', description='user')
        project = self.seed('project', description='project')
        for scope, path in [('user', user), ('project', project), ('', project)]:
            frame = await self.command('get_action', scope=scope)
            self.assertEqual(frame['path'], str(path))
            self.assertEqual(frame['group'], 'g')
        user.unlink()
        self.assertEqual((await self.command('get_action', scope='user'))['type'], 'error')
        project.unlink()
        self.seed('user')
        self.assertEqual((await self.command('get_action', scope='project'))['type'], 'error')

    async def test_project_save_does_not_use_global_directory(self):
        user = self.seed('user', description='keep global')
        frame = await self.command('save_action', scope='project', action={'prompt': '{{ TASK }}', 'description': 'project'})
        self.assertEqual(frame['saved'], 'build')
        self.assertEqual(parse_yaml(user.read_text())['description'], 'keep global')
        self.assertEqual((await self.command('get_action', scope='project'))['action']['description'], 'project')

    async def test_scoped_rename_and_scope_move_preserve_other_definition(self):
        project = self.seed('project', description='keep project')
        user = self.seed('user')
        frame = await self.command('save_action', name='renamed', scope='user', old_name='build', old_scope='user', action={'prompt': '{{ TASK }}'})
        self.assertEqual(frame['saved'], 'renamed')
        self.assertTrue(project.exists())
        self.assertFalse(user.exists())
        frame = await self.command('save_action', name='renamed', scope='project', old_name='renamed', old_scope='user', action={'prompt': '{{ TASK }}'})
        self.assertEqual(frame['scope'], 'project')
        self.assertEqual((await self.command('get_action', name='renamed', scope='user'))['type'], 'error')

    async def test_legacy_rename_removes_only_highest_priority_original(self):
        project = self.seed('project')
        user = self.seed('user')
        frame = await self.command('save_action', name='renamed', old_name='build', action={'prompt': '{{ TASK }}'})
        self.assertEqual(frame['saved'], 'renamed')
        self.assertFalse(project.exists())
        self.assertTrue(user.exists())

    async def test_invalid_definitions_and_paths_leave_originals_intact(self):
        path = self.seed('project')
        original = path.read_text()
        for values in ({'name': '../outside'}, {'old_name': '../outside'}, {'scope': 'invalid'}, {'old_scope': 'invalid'}, {'action': []}, {'action': {'prompt': 3}}, {'action': {'prompt': 'No variable'}}, {'action': {'prompt': '{{ TASK }}', 'agent': []}}, {'action': {'prompt': '{{ TASK }}', 'terminals': [1]}}):
            frame = await self.command('save_action', **{'name': 'renamed', 'old_name': 'build', 'action': {'prompt': '{{ TASK }}'}, **values})
            self.assertEqual(frame['type'], 'error', values)
            self.assertEqual(path.read_text(), original)
        self.assertFalse((path.parent / 'renamed.yaml').exists())

    async def test_failed_replacement_keeps_source_and_existing_destination(self):
        original = self.seed('project')
        destination = self.seed('project', 'renamed', description='existing destination')
        before = destination.read_text()
        with patch('torque.commands.action_authoring.os.replace', side_effect=OSError('refused replacement')):
            frame = await self.command('save_action', name='renamed', old_name='build', old_scope='project', action={'prompt': '{{ TASK }}'})
        self.assertEqual(frame['type'], 'error')
        self.assertTrue(original.exists())
        self.assertEqual(destination.read_text(), before)
        self.assertFalse(list(destination.parent.glob('.action-*.tmp')))

    async def test_same_name_save_retains_yml_path_and_normalizes_empty_scope(self):
        path = self.seed('project')
        yml = path.with_suffix('.yml')
        path.rename(yml)
        for scope in ('', None):
            frame = await self.command('save_action', scope=scope, old_name='build', old_scope='project', action={'prompt': '{{ TASK }}', 'description': 'edited'})
            self.assertEqual(frame['saved'], 'build')
            self.assertTrue(yml.exists())
            self.assertFalse(path.exists())

    async def test_delete_only_selected_scope(self):
        project = self.seed('project')
        user = self.seed('user')
        frame = await self.command('delete_action', scope='user')
        self.assertEqual(frame['deleted'], 'build')
        self.assertEqual(frame['scope'], 'user')
        self.assertTrue(project.exists())
        self.assertFalse(user.exists())
        self.assertEqual((await self.command('delete_action', scope='user'))['type'], 'error')
        self.assertTrue(project.exists())

    async def test_draft_preview_and_variables_never_write_and_ignore_saved_definition(self):
        path = self.seed('project', prompt='Saved {{ TASK }}')
        original = path.read_text()
        draft = {'prompt': 'Draft {{ TASK }} {{ SCOPE }} {{ torque.agent.kind }}', 'group': '{{ TASK }}', 'labels': ['{{ TASK }}']}
        frame = await self.command('render_action', action=draft, vars={'TASK': 'sample', 'SCOPE': 'focused'})
        self.assertIn('Draft sample focused', frame['prompt'])
        self.assertEqual(frame['workspace_group'], 'g')
        self.assertEqual(frame['group'], '{{ TASK }}')
        self.assertEqual(frame['labels'], ['{{ TASK }}'])
        self.assertEqual(path.read_text(), original)
        frame = await self.command('render_action', name='', action=draft, variables_only=True)
        self.assertEqual(frame['type'], 'action_variables')
        self.assertEqual([row['name'] for row in frame['vars']], ['TASK', 'SCOPE'])
        self.assertEqual((await self.command('render_action', action=draft, vars={'torque': {}}))['type'], 'error')

    async def test_persisted_preview_obeys_scope(self):
        self.seed('project', prompt='Project {{ TASK }}')
        self.seed('user', prompt='User {{ TASK }}')
        frame = await self.command('render_action', scope='user', vars={'TASK': 'sample'})
        self.assertEqual(frame['prompt'].strip(), 'User sample')

    async def test_full_definition_round_trips_supported_unexposed_fields(self):
        values = {'prompt': '{{ torque.task.title }}', 'worktree': False, 'max_depth': 7,
                  'deliverable': {'required': True, 'kind': 'report'}, 'custom_metadata': {'owner': 'local'},
                  'agent': {'name_prefix': 'review', 'env_vars': {'TOKENLESS': '1'}, 'shell': '/bin/zsh'},
                  'implementation_depth': False, 'review_required_above_loc': 0,
                  'transitions': [{'action': 'review', 'loc_gate': {'ship_direct_max': 0, 'review_default_above': 1, 'self_review_bypass_allowed': False}}],
                  'terminals': [{'name': 'logs', 'command': 'echo logs'}]}
        frame = await self.command('save_action', action=values)
        self.assertEqual(frame['saved'], 'build')
        loaded = (await self.command('get_action', scope='project'))['action']
        for key, value in values.items():
            self.assertEqual(loaded[key], value, key)

    async def test_new_project_directory_never_falls_back_to_cwd(self):
        original = self.seed('project', description='cwd sentinel')
        missing = self.root / 'not-created' / 'project'

        async def resolve(_group):
            return str(missing)

        self.runtime.resolve_base_dir = resolve
        with patch('os.getcwd', return_value=str(self.project)):
            frame = await self.command('save_action', scope='project', action={'prompt': '{{ TASK }}', 'description': 'new project'})
        self.assertEqual(frame['saved'], 'build')
        self.assertEqual(parse_yaml(original.read_text())['description'], 'cwd sentinel')
        self.assertTrue((missing / '.torque' / 'actions' / 'build.yaml').exists())


    async def test_empty_authoring_collections_reload_and_preview(self):
        self.seed('project', transitions=[{'action': 'review'}],
                  terminals=[{'name': 'watch', 'command': 'echo watch'}])
        values = {'prompt': '{{ TASK }}', 'agent': {}, 'transitions': [],
                  'terminals': [], 'labels': [], 'custom_metadata': {}}
        saved = await self.command('save_action', scope='project', action=values)
        self.assertEqual(saved['saved'], 'build')
        loaded = await self.command('get_action', scope='project')
        self.assertEqual(loaded['type'], 'action_detail')
        for key, value in values.items():
            self.assertEqual(loaded['action'][key], value, key)
        preview = await self.command('render_action', scope='project', vars={'TASK': 'sample'})
        self.assertEqual(preview['prompt'].strip(), 'sample')
        self.assertEqual(self.manager.load_action_raw('build', str(self.project))['transitions'], [])


    async def test_multiline_prompt_with_unicode_and_line_end_spaces_reloads(self):
        prompt = ('{{ TASK }} — Olá 👋 ' + 'long text ' * 20 + '\n') * 3
        saved = await self.command('save_action', scope='project', action={'prompt': prompt, 'transitions': [], 'terminals': []})
        self.assertEqual(saved['saved'], 'build')
        loaded = await self.command('get_action', scope='project')
        self.assertEqual(loaded['action']['prompt'], prompt)
        preview = await self.command('render_action', scope='project', vars={'TASK': 'sample'})
        self.assertIn('sample — Olá 👋', preview['prompt'])

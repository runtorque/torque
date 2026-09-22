"""Creation acknowledgement and resolved-role UI contracts."""
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

try:
    import aiohttp  # noqa: F401
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub
    install_aiohttp_stub()

from torque.commands.agent_operations import AgentOperationRuntime, handle_agent_operation_command
from torque.commands.catalog import CatalogCommandRuntime, handle_catalog_command
from torque.commands.agent_classes import _handle_agent_class_command
from torque.roles import RoleManager
from torque.state import GroupSettings


class CreationParityTests(unittest.IsolatedAsyncioTestCase):
    async def test_terminal_acknowledgement_follows_session_creation(self):
        cell = SimpleNamespace(id='terminal-id', name='Shell', group='g', parent_id='')
        state = SimpleNamespace(agents={}, get_group_settings=lambda group: GroupSettings(), add_terminal=Mock(return_value=cell))
        bridge = SimpleNamespace(create_session=AsyncMock())
        runtime = AgentOperationRuntime(**{field.name: {'state': state, 'bridge': bridge}.get(field.name) for field in fields(AgentOperationRuntime)})
        frame = await handle_agent_operation_command({'cmd': 'add_terminal', 'name': 'Shell', 'group': 'g', 'command': '/bin/cat'}, runtime)
        bridge.create_session.assert_awaited_once()
        self.assertEqual(frame, {'type': 'terminal_created', 'id': 'terminal-id', 'name': 'Shell', 'kind': 'terminal', 'group': 'g', 'parent_id': ''})
        state.add_terminal.return_value = None
        bridge.create_session.reset_mock()
        frame = await handle_agent_operation_command({'cmd': 'add_terminal', 'name': 'Shell', 'group': 'g'}, runtime)
        self.assertEqual(frame['type'], 'error')
        bridge.create_session.assert_not_awaited()

    async def test_terminal_session_failure_cannot_return_a_success_acknowledgement(self):
        cell = SimpleNamespace(id='terminal-id', name='Shell', group='g', parent_id='')
        state = SimpleNamespace(agents={}, get_group_settings=lambda group: GroupSettings(), add_terminal=Mock(return_value=cell))
        bridge = SimpleNamespace(create_session=AsyncMock(side_effect=RuntimeError('session failed')))
        runtime = AgentOperationRuntime(**{field.name: {'state': state, 'bridge': bridge}.get(field.name) for field in fields(AgentOperationRuntime)})
        with self.assertRaisesRegex(RuntimeError, 'session failed'):
            await handle_agent_operation_command({'cmd': 'add_terminal', 'name': 'Shell', 'group': 'g'}, runtime)

    async def test_rendered_roles_are_group_correlated_and_missing_names_do_not_fall_back(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            roles = root / '.torque' / 'roles'
            roles.mkdir(parents=True)
            (roles / 'build.yaml').write_text('provider: generic\ncommand: /bin/cat\nmodel: role-model\nenv_vars:\n  MODE: qa\nworktree: false\n')
            manager = RoleManager()
            with patch.object(manager, '_source_dirs', return_value=[(str(roles), False)]):
                runtime = CatalogCommandRuntime(state=SimpleNamespace(get_group_settings=lambda group: GroupSettings(agent_model='group-model')), db=None, action_mgr=None, template_mgr=manager, specialization_mgr=None, resolve_base_dir=AsyncMock(return_value=str(root)), handle_set_engineer_specializations_command=None, action_to_yaml=None)
                frame = await handle_catalog_command({'cmd': 'render_template', 'group': 'g', 'name': 'build'}, runtime)
                self.assertEqual((frame['type'], frame['name'], frame['group']), ('template_rendered', 'build', 'g'))
                self.assertEqual(frame['config']['model'], 'role-model')
                self.assertEqual(frame['config']['env_vars'], {'MODE': 'qa'})
                self.assertFalse(frame['config']['worktree'])
                empty = await handle_catalog_command({'cmd': 'render_template', 'group': 'g', 'name': ''}, runtime)
                self.assertEqual(empty['config']['model'], 'group-model')
                missing = await handle_catalog_command({'cmd': 'render_template', 'group': 'g', 'name': 'missing'}, runtime)
                self.assertEqual(missing['type'], 'error')

    async def test_class_discovery_resolves_group_and_keeps_explicit_path_compatibility(self):
        import json
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            projects = {name: root / name for name in ('alpha', 'beta')}
            for name, project in projects.items():
                catalog = project / '.torque' / 'agent_classes'
                catalog.mkdir(parents=True)
                (catalog / 'local-worker.yaml').write_text(json.dumps({
                    'agent_class_schema_version': 5, 'id': 'local-worker',
                    'version': '1', 'base_kind': 'worker',
                    'display_name': name + ' worker',
                    'acl': {'mode': 'deny', 'rules': []},
                }))
            resolve = AsyncMock(side_effect=lambda group: str(projects[group]))
            for name in projects:
                frame = await _handle_agent_class_command({'cmd': 'agent_class_list', 'group': name}, None, None, resolve)
                self.assertEqual(frame['group'], name)
                self.assertEqual(frame['base_dir'], str(projects[name]))
                local = next(item for item in frame['classes'] if item['id'] == 'local-worker')
                self.assertEqual(local['display_name'], name + ' worker')
                self.assertTrue(local['launchable'])
            self.assertEqual(resolve.await_count, 2)
            explicit = await _handle_agent_class_command({'cmd': 'agent_class_list', 'group': 'alpha', 'base_dir': str(projects['beta'])}, None, None, resolve)
            self.assertEqual(explicit['base_dir'], str(projects['beta']))
            self.assertEqual(resolve.await_count, 2)
            with patch('torque.commands.agent_classes.os.getcwd', return_value=str(projects['alpha'])):
                legacy = await _handle_agent_class_command({'cmd': 'agent_class_list'}, None, None, resolve)
            self.assertEqual(legacy['group'], '')
            self.assertEqual(legacy['base_dir'], str(projects['alpha']))
            self.assertEqual(resolve.await_count, 2)

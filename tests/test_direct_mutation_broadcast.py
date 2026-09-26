"""Direct state mutations notify subscribers without incidental runtime traffic."""
import importlib
import json
import sqlite3
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub


class DirectMutationBroadcastTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub()
        self.direct = importlib.import_module('torque.commands.direct')
        self.state_mod = importlib.import_module('torque.state')
        db_mod = importlib.import_module('torque.db')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = db_mod.TorqueDB(Path(self.tmp.name) / 'torque.db')
        self.db.init()
        self.addCleanup(self.db.close)
        self.state = self.state_mod.MatrixState(db=self.db)
        self.runtime = self.direct.DirectCommandRuntime(**{
            field.name: None for field in fields(self.direct.DirectCommandRuntime)
        })
        self.runtime.state = self.state
        self.runtime.db = self.db
        self.runtime.DATA_DIR = self.tmp.name
        self.runtime.resolve_base_dir = AsyncMock(return_value=self.tmp.name)
        self.runtime.relay_settings_fingerprint = lambda: self.state.global_settings.relay_credential_id
        self.runtime.restart_cloud_connector = AsyncMock()
        self.frames = []
        frames = self.frames

        class Subscriber:
            async def send_str(self, message):
                frames.append(json.loads(message))

        self.state._ws_clients.add(Subscriber())

    def agent(self, kind):
        cell = self.state_mod.AgentCell(id=kind, name=kind, group='', cell_type='agent', kind=kind,
                                        directory=self.tmp.name)
        self.state.agents[cell.id] = cell
        self.state._db_save_agent(cell)
        return cell

    async def command(self, cmd, **data):
        result = await self.direct.handle_direct_command({'cmd': cmd, **data}, self.runtime)
        self.assertNotEqual(result.get('type'), 'error', result)
        return result

    def emitted(self, name):
        return [op for frame in self.frames for op in frame['ops'] if op['op'] == name]

    async def test_assignment_and_clear_publish_desired_class_without_changing_effective_authority(self):
        cell = self.agent('worker')
        effective = cell.effective_agent_class_id
        for cmd, class_id in (('agent_class_assign', 'default-worker'), ('agent_class_clear', '')):
            with self.subTest(cmd=cmd):
                self.frames.clear()
                result = await self.command(cmd, agent_id=cell.id, class_id=class_id, base_dir=self.tmp.name)
                self.assertEqual(result['type'], 'agent_class_assignment')
                ops = self.emitted('agent_upsert')
                self.assertEqual(len(ops), 1, self.frames)
                self.assertEqual(ops[0]['agent_class_id'], class_id)
                self.assertEqual(cell.effective_agent_class_id, effective)
                self.assertEqual(self.state._delta_ops, [])

    async def test_engineer_specializations_publish_the_ordered_selection(self):
        catalog = importlib.import_module('torque.commands.catalog')
        operations = importlib.import_module('torque.server_agent_operations')
        cell = self.agent('engineer')
        values = {field.name: None for field in fields(catalog.CatalogCommandRuntime)}
        values.update(state=self.state, db=self.db, resolve_base_dir=self.runtime.resolve_base_dir,
                      specialization_mgr=SimpleNamespace(canonical_project_names=lambda **_: ['api', 'ui']),
                      handle_set_engineer_specializations_command=operations._handle_set_engineer_specializations_command)
        self.runtime.catalog_command_runtime = catalog.CatalogCommandRuntime(**values)
        result = await self.command('set_engineer_specializations', engineer_id=cell.id, specializations=['ui', 'api'])
        self.assertEqual(result['specializations'], ['ui', 'api'])
        ops = self.emitted('agent_upsert')
        self.assertEqual(len(ops), 1, self.frames)
        self.assertEqual(ops[0]['engineer_specializations'], ['ui', 'api'])
        self.assertEqual(self.state._delta_ops, [])

    async def credential(self):
        with patch.object(self.direct.cloud_hooks, 'generate_daemon_credential', AsyncMock(return_value={
            'ok': True, 'credential_id': 'local-fixture', 'private_key_path': '/fixture/key',
        })), patch.object(self.direct.cloud_hooks, 'resolve_relay_config', return_value={}):
            return await self.command('generate_daemon_credential', pairing_token='fixture')

    async def test_persisted_credentials_publish_before_direct_acknowledgement(self):
        result = await self.credential()
        self.assertTrue(result['ok'])
        ops = self.emitted('global_settings_update')
        self.assertEqual(len(ops), 1, self.frames)
        self.assertEqual(ops[0]['relay_credential_id'], 'local-fixture')
        saved = self.db._conn.execute("SELECT value FROM global_settings WHERE key='relay_credential_id'").fetchone()
        self.assertEqual(json.loads(saved[0]), 'local-fixture')
        self.runtime.restart_cloud_connector.assert_awaited_once()
        self.assertEqual(self.state._delta_ops, [])

    async def test_failed_credential_storage_does_not_publish_or_apply_optimistic_settings(self):
        before = self.state.global_settings.relay_credential_id
        with patch.object(self.db, 'save_global_settings_durable', AsyncMock(side_effect=sqlite3.OperationalError('disk refused'))):
            result = await self.credential()
        self.assertFalse(result['ok'])
        self.assertEqual(self.state.global_settings.relay_credential_id, before)
        self.assertEqual(self.emitted('global_settings_update'), [])
        self.runtime.restart_cloud_connector.assert_not_awaited()

    async def test_class_status_read_does_not_flush_unrelated_queued_changes(self):
        cell = self.agent('worker')
        self.state._emit('runtime', marker='pending')
        await self.command('agent_class_status', agent_id=cell.id, base_dir=self.tmp.name)
        self.assertEqual(self.frames, [])
        self.assertEqual(len(self.state._delta_ops), 1)

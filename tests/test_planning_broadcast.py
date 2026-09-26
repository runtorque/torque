"""Planning writes must reach subscribers without a periodic runtime flush."""

import importlib
import inspect
import json
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


class PlanningBroadcastTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub()
        direct = importlib.import_module('torque.commands.direct')
        state_mod = importlib.import_module('torque.state')
        db_mod = importlib.import_module('torque.db')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = db_mod.TorqueDB(Path(self.tmp.name) / 'torque.db')
        self.db.init()
        self.addCleanup(self.db.close)
        self.state = state_mod.MatrixState(db=self.db)
        self.state.groups['Planning'] = []
        self.state._db_save_groups()
        self.runtime = direct.DirectCommandRuntime(**{
            field.name: None for field in fields(direct.DirectCommandRuntime)
        })
        self.runtime.state = self.state
        self.runtime.db = self.db
        self.dispatch = direct.handle_direct_command
        self.frames = []
        frames = self.frames

        class Subscriber:
            async def send_str(self, message):
                frames.append(json.loads(message))

        self.state._ws_clients.add(Subscriber())

    async def command(self, cmd, **data):
        return await self.dispatch({'cmd': cmd, 'group': 'Planning', **data}, self.runtime)

    async def write(self, cmd, op, **data):
        self.frames.clear()
        result = await self.command(cmd, **data)
        self.assertNotEqual(result['type'], 'error', result)
        # No timer, explicit broadcast, or unrelated command can flush this write.
        self.assertEqual(len(self.frames), 1, (cmd, self.frames))
        self.assertEqual(self.frames[0]['type'], 'delta')
        self.assertTrue(any(item['op'] == op for item in self.frames[0]['ops']), self.frames)
        self.assertEqual(self.state._delta_ops, [])
        return result

    async def test_initiative_create_update_archive_reach_subscriber(self):
        created = await self.write('initiative_create', 'initiative_upsert', title='Intent')
        ident = created['initiative']['id']
        await self.write('initiative_update', 'initiative_upsert', id=ident, why='External change')
        self.assertEqual(self.frames[0]['ops'][0]['why'], 'External change')
        self.assertEqual(self.db.load_initiative(ident)['why'], 'External change')
        await self.write('initiative_archive', 'initiative_upsert', id=ident)

    async def test_area_and_note_mutations_reach_subscriber(self):
        created = await self.write('area_create', 'area_upsert', title='Area')
        ident = created['area']['id']
        await self.write('area_update', 'area_upsert', id=ident, summary='Updated')
        note = await self.write('area_note_create', 'area_note_upsert', id=ident, title='Note', body='Context', note_type='caveat')
        await self.write('area_note_update', 'area_note_upsert', id=ident, note_id=note['note']['id'], body='Changed')
        await self.write('area_note_archive', 'area_note_upsert', id=ident, note_id=note['note']['id'])
        await self.write('area_archive', 'area_upsert', id=ident)

    async def test_scratchpad_mutations_reach_subscriber(self):
        op = 'thinking_scratchpad_note_upsert'
        created = await self.write('scratchpad_note_create', op, title='Note', body='Draft')
        ident = created['note']['id']
        await self.write('scratchpad_note_update', op, id=ident, body='Changed')
        await self.write('scratchpad_note_archive', op, id=ident)
        await self.write('scratchpad_note_delete', op, id=ident)

    async def test_brief_mutations_reach_subscriber(self):
        created = await self.write('idea_brief_create', 'idea_brief_upsert', title='Brief', problem_opportunity='Need a durable plan')
        ident = created['idea_brief']['id']
        for action in ('update', 'refine', 'park', 'propose', 'promote', 'archive'):
            with self.subTest(action=action):
                await self.write(f'idea_brief_{action}', 'idea_brief_upsert', id=ident, summary='Changed')

    async def test_links_reach_subscriber(self):
        initiative = await self.command('initiative_create', title='Intent')
        area = await self.command('area_create', title='Area')
        ident = initiative['initiative']['id']
        area_id = area['area']['id']
        for action in ('link', 'unlink'):
            await self.write(f'area_{action}_initiative', 'area_link_upsert' if action == 'link' else 'area_link_remove', id=area_id, initiative_id=ident)

    async def test_reads_do_not_flush_unrelated_pending_mutations(self):
        for prefix, key in (('initiative', 'initiative'), ('area', 'area'),
                            ('scratchpad_note', 'note'), ('idea_brief', 'idea_brief')):
            created = await self.command(f'{prefix}_create', title=prefix, problem_opportunity='Need a durable plan')
            self.state._delta_ops.clear()
            self.frames.clear()
            self.state._emit('runtime', marker='pending unrelated change')
            for action in ('list', 'show'):
                result = await self.command(f'{prefix}_{action}', id=created[key]['id'])
                self.assertNotEqual(result['type'], 'error', result)
            self.assertEqual(self.frames, [])
            self.assertEqual(len(self.state._delta_ops), 1)
            self.state._delta_ops.clear()

    async def test_http_update_broadcasts_before_acknowledging_without_background_activity(self):
        routes_mod = importlib.import_module('torque.server_routes')
        created = await self.command('initiative_create', title='External edit')
        ident = created['initiative']['id']
        self.state._delta_ops.clear()
        self.frames.clear()

        async def handle(data):
            return await self.dispatch(data, self.runtime)

        values = {name: None for name in inspect.signature(routes_mod.build_http_routes).parameters}
        values.update(handle_command=handle, db=self.db, state=self.state,
                      daemon_stop_state=SimpleNamespace(should_reject_api_request=lambda _: False),
                      api_worker_context_guard=lambda *_: None)
        routes = routes_mod.build_http_routes(**values)
        request = SimpleNamespace(json=AsyncMock(return_value={
            'cmd': 'initiative_update', 'id': ident, 'why': 'External HTTP change',
        }), headers={}, remote='127.0.0.1')

        def acknowledge(payload):
            # Assert at response construction, not after some later broadcast.
            self.assertEqual(len(self.frames), 1)
            self.assertEqual(self.frames[0]['ops'][0]['id'], ident)
            self.assertEqual(self.frames[0]['ops'][0]['why'], 'External HTTP change')
            return payload

        with patch.object(routes_mod, 'web', SimpleNamespace(json_response=acknowledge)):
            response = await routes.handle_api_cmd(request)
        self.assertTrue(response['ok'])
        self.assertEqual(response['data']['initiative']['why'], 'External HTTP change')
        self.assertEqual(self.db.load_initiative(ident)['why'], 'External HTTP change')

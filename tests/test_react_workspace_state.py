import asyncio
import sqlite3
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub
install_aiohttp_stub()

from torque.commands.ui_state import _handle_react_workspace_state_command, _UI_STATE_COMMAND_REGISTRY
from torque.db import TorqueDB
from torque.state import MatrixState
from torque.ui_preferences import normalize_react_workspace_state

PREFERENCE = {"version": 1, "activePanel": "control", "controlTab": "context"}


class ReactWorkspaceStateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "torque.db"
        self.db = TorqueDB(self.path)
        self.db.init()
        self.addCleanup(self.db.close)
        self.addAsyncCleanup(self.db.close_async_writes)

    async def test_command_acknowledges_persisted_preference_and_preserves_classic_keys(self):
        state = MatrixState(db=self.db)
        state.panel_active = "chat"
        state.standalone_panel_layout = {"last_active": "help"}
        state._emit = Mock()
        frame = await _handle_react_workspace_state_command({"cmd": "ui_set_react_workspace_state", "state": {**PREFERENCE, "untrusted": "discard"}}, state)
        self.assertEqual(frame, {"type": "react_workspace_state", "state": PREFERENCE})
        state._emit.assert_called_once_with("ui_update", key="react_workspace_state", value=PREFERENCE)
        self.assertEqual(state.panel_active, "chat")
        self.assertEqual(state.standalone_panel_layout, {"last_active": "help"})
        offline = TorqueDB(self.path)
        self.addCleanup(offline.close)
        offline.init()
        self.assertEqual(offline.load_all()["react_workspace_state"], PREFERENCE)
        restored = MatrixState(db=offline)
        restored.load()
        self.assertEqual(restored.to_dict()["react_workspace_state"], PREFERENCE)
        self.assertEqual(restored.to_dict_compact()["react_workspace_state"], PREFERENCE)

    async def test_invalid_command_cannot_replace_existing_preference(self):
        state = MatrixState(db=self.db)
        state.react_workspace_state = PREFERENCE.copy()
        state._emit = Mock()
        state._db_save_ui = Mock()
        invalid = [None, [], {}, {**PREFERENCE, "version": True}, {**PREFERENCE, "version": 2}, {**PREFERENCE, "activePanel": "legacy"}, {**PREFERENCE, "controlTab": []}, {**PREFERENCE, "controlTab": "unknown"}]
        for raw in invalid:
            with self.subTest(raw=raw):
                self.assertEqual(normalize_react_workspace_state(raw), {})
                self.assertEqual((await _handle_react_workspace_state_command({"cmd": "ui_set_react_workspace_state", "state": raw}, state))["type"], "error")
                self.assertEqual(state.react_workspace_state, PREFERENCE)
        state._emit.assert_not_called()
        state._db_save_ui.assert_not_called()

    def test_full_snapshot_round_trip_and_profile_isolation(self):
        state = MatrixState()
        state.react_workspace_state = PREFERENCE.copy()
        state.panel_active = "actions"
        self.db.save_all(state.to_dict())
        self.assertEqual(self.db.load_all()["react_workspace_state"], PREFERENCE)
        self.assertEqual(self.db.load_all()["panel_active"], "actions")
        other = TorqueDB(self.path.with_name("other-profile.db"))
        other.init()
        self.addCleanup(other.close)
        self.assertEqual(other.load_all()["react_workspace_state"], {})

    def test_missing_and_malformed_storage_are_safe_to_load(self):
        self.assertEqual(self.db.load_all()["react_workspace_state"], {})
        for raw in ["broken json", "[]", json.dumps({**PREFERENCE, "controlTab": {}}), json.dumps({**PREFERENCE, "version": 9})]:
            with self.subTest(raw=raw):
                self.db.save_ui_state("react_workspace_state", raw)
                state = MatrixState(db=self.db)
                state.load()
                self.assertEqual(state.react_workspace_state, {})
                self.assertEqual(self.db.load_all()["react_workspace_state"], {})

    async def test_delayed_or_failed_write_never_publishes_optimistic_state(self):
        state = MatrixState(db=self.db)
        state._emit = Mock()
        started, release = asyncio.Event(), asyncio.Event()
        async def delayed(*_args):
            started.set()
            await release.wait()
            raise sqlite3.OperationalError("fixture disk failure")
        self.db.save_ui_state_durable = AsyncMock(side_effect=delayed)
        task = asyncio.create_task(_UI_STATE_COMMAND_REGISTRY.dispatch(
            "ui_set_react_workspace_state",
            {"state": PREFERENCE}, state,
        ))
        await started.wait()
        self.assertFalse(task.done())
        self.assertEqual(state.react_workspace_state, {})
        state._emit.assert_not_called()
        with self.assertLogs("torque.commands.ui_state", level="ERROR"):
            release.set()
            result = await task
        self.assertEqual(result.value["type"], "error")
        self.assertEqual(state.react_workspace_state, {})
        state._emit.assert_not_called()


class OrderedReactWorkspaceStateTests(unittest.IsolatedAsyncioTestCase):
    setUp = ReactWorkspaceStateTests.setUp

    def request(self, revision, panel='agents', writer='11111111-1111-4111-8111-111111111111'):
        return {'cmd': 'ui_set_react_workspace_state', 'state': {**PREFERENCE, 'activePanel': panel}, 'writer_id': writer, 'revision': revision}

    async def test_delayed_older_request_cannot_overwrite_latest_even_after_restart(self):
        state = MatrixState(db=self.db)
        await _handle_react_workspace_state_command(self.request(2, 'planning'), state)
        await self.db.close_async_writes()
        restarted = TorqueDB(self.path)
        restarted.init()
        self.addCleanup(restarted.close)
        self.addAsyncCleanup(restarted.close_async_writes)
        restored = MatrixState(db=restarted)
        restored.load()
        restored._emit = Mock()
        result = await _handle_react_workspace_state_command(self.request(1), restored)
        self.assertEqual(result['type'], 'error')
        self.assertEqual(restored.react_workspace_state['activePanel'], 'planning')
        self.assertEqual(self.db.load_all()['react_workspace_state']['activePanel'], 'planning')
        restored._emit.assert_not_called()

    async def test_exact_retry_is_acknowledged_without_overwriting_another_window(self):
        state = MatrixState(db=self.db)
        first = self.request(1)
        await _handle_react_workspace_state_command(first, state)
        await _handle_react_workspace_state_command(self.request(1, 'planning', '22222222-2222-4222-8222-222222222222'), state)
        state._emit = Mock()
        result = await _handle_react_workspace_state_command(first, state)
        self.assertEqual(result['state'], first['state'])
        self.assertEqual(state.react_workspace_state['activePanel'], 'planning')
        self.assertEqual(self.db.load_all()['react_workspace_state']['activePanel'], 'planning')
        state._emit.assert_not_called()
        changed = await _handle_react_workspace_state_command(self.request(1, 'control'), state)
        self.assertEqual(changed['type'], 'error')

    async def test_invalid_writer_or_revision_never_writes(self):
        state = MatrixState(db=self.db)
        state._emit = Mock()
        for change in [{'writer_id': ''}, {'writer_id': 'invalid'}, {'revision': True}, {'revision': 0}, {'revision': -1}, {'revision': 1.5}, {'revision': 9007199254740992}, {'writer_id': None}]:
            with self.subTest(change=change):
                result = await _handle_react_workspace_state_command({**self.request(1), **change}, state)
                self.assertEqual(result['type'], 'error')
        self.assertEqual(self.db.load_all()['react_workspace_state'], {})
        state._emit.assert_not_called()

    async def test_cancelled_caller_still_commits_and_publishes_before_next_save(self):
        state = MatrixState(db=self.db)
        frames = []
        class Subscriber:
            async def send_str(self, message):
                frames.append(json.loads(message))
        state._ws_clients.add(Subscriber())
        started, release = asyncio.Event(), asyncio.Event()
        original = self.db.save_ordered_react_workspace_state_durable
        async def delayed(*args):
            if args[1] == 1:
                started.set()
                await release.wait()
            return await original(*args)
        self.db.save_ordered_react_workspace_state_durable = delayed
        first = asyncio.create_task(_handle_react_workspace_state_command(self.request(1), state))
        await started.wait()
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        second = asyncio.create_task(_handle_react_workspace_state_command(self.request(2, 'planning'), state))
        await asyncio.sleep(0)
        self.assertFalse(second.done())
        release.set()
        await second
        self.assertEqual(state.react_workspace_state['activePanel'], 'planning')
        self.assertEqual(self.db.load_all()['react_workspace_state']['activePanel'], 'planning')
        values = [op['value']['activePanel'] for frame in frames for op in frame.get('ops', []) if op.get('key') == 'react_workspace_state']
        self.assertEqual(values, ['agents', 'planning'])
        self.assertEqual(state._delta_ops, [])

    async def test_failed_transaction_leaves_no_receipt_and_allows_exact_retry(self):
        state = MatrixState(db=self.db)
        self.db._conn.execute("CREATE TRIGGER refuse_workspace BEFORE INSERT ON ui_state WHEN NEW.key = 'react_workspace_state' BEGIN SELECT RAISE(ABORT, 'fixture write failure'); END")
        self.db._conn.commit()
        with self.assertLogs('torque.commands.ui_state', level='ERROR'):
            result = await _handle_react_workspace_state_command(self.request(1), state)
        self.assertEqual(result['type'], 'error')
        self.assertEqual(self.db._conn.execute('SELECT COUNT(*) FROM react_workspace_writers').fetchone()[0], 0)
        self.assertEqual(state.react_workspace_state, {})
        self.db._conn.execute('DROP TRIGGER refuse_workspace')
        self.db._conn.commit()
        result = await _handle_react_workspace_state_command(self.request(1), state)
        self.assertEqual(result['state']['activePanel'], 'agents')
        self.assertEqual(self.db._conn.execute('SELECT COUNT(*) FROM react_workspace_writers').fetchone()[0], 1)

    async def test_snapshot_preserves_receipts_and_newer_revision_replaces_one_row(self):
        state = MatrixState(db=self.db)
        await _handle_react_workspace_state_command(self.request(1), state)
        await _handle_react_workspace_state_command(self.request(2, 'planning'), state)
        await self.db.flush_async_writes()
        self.db.save_all(state.to_dict())
        result = await _handle_react_workspace_state_command(self.request(1), state)
        self.assertEqual(result['type'], 'error')
        self.assertEqual(self.db._conn.execute('SELECT revision FROM react_workspace_writers').fetchall(), [(2,)])
        self.assertEqual(self.db.load_all()['react_workspace_state']['activePanel'], 'planning')

    async def test_upgrade_from_previous_schema_keeps_preference_and_creates_ordering_table(self):
        self.db.save_ui_state('react_workspace_state', json.dumps(PREFERENCE))
        self.db._conn.execute('DROP TABLE react_workspace_writers')
        self.db._conn.execute('DELETE FROM schema_migrations WHERE version=31')
        self.db._conn.execute("UPDATE meta SET value='30' WHERE key='schema_version'")
        self.db._conn.commit()
        self.db.init()
        self.assertEqual(self.db.load_all()['react_workspace_state'], PREFERENCE)
        self.assertEqual(self.db._conn.execute('SELECT COUNT(*) FROM react_workspace_writers').fetchone()[0], 0)
        state = MatrixState(db=self.db)
        await _handle_react_workspace_state_command(self.request(1), state)
        self.db.init()
        result = await _handle_react_workspace_state_command(self.request(1, 'planning'), state)
        self.assertEqual(result['type'], 'error')
        self.assertEqual(self.db.load_all()['react_workspace_state']['activePanel'], 'agents')

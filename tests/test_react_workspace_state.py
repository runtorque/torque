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

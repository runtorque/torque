"""Defaults exposed to React must survive the real settings write/load paths."""
import asyncio
import json
import re
import tempfile
import sqlite3
import unittest
from dataclasses import asdict, fields
from pathlib import Path
from unittest.mock import AsyncMock, Mock

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub
install_aiohttp_stub()

from torque.db import TorqueDB
from torque.state import ArchitectSettings, EngineerSettings, GlobalSettings, GroupSettings, MatrixState
from torque.commands.settings import _handle_settings_read_command
from torque.commands.engineer_operations import EngineerOperationRuntime, handle_engineer_operation_command

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'ui/src/features/control/settingsContract.fixture.json'


def defaults():
    return {name: asdict(cls()) for name, cls in [('global', GlobalSettings), ('group', GroupSettings), ('engineer', EngineerSettings), ('architect', ArchitectSettings)]}


class ReactSettingsContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = TorqueDB(Path(self.tmp.name) / 'torque.db')
        self.db.init()
        self.addCleanup(self.db.close)
        self.state = MatrixState(self.db)
        self.state.add_group('qa')

    def test_frontend_contract_fixture_matches_daemon_defaults(self):
        self.assertEqual(json.loads(FIXTURE.read_text()), defaults())

    def test_read_commands_supply_independent_defaults_without_secrets(self):
        kwargs = dict(bridge=Mock(list_profiles=AsyncMock(return_value=[])), resolve_base_dir=AsyncMock(return_value=None), template_mgr=Mock(resolve_agent_config=Mock(return_value={}), list_templates=Mock(return_value=[])), action_mgr=Mock(list_actions=Mock(return_value=[])), providers=lambda: [], runtime_payload=lambda **_: {}, resolve_relay_config=lambda _: {}, build_ai_settings_response=lambda *_: {}, db=self.db)
        self.state.update_global_settings(xterm_scrollback=9000)
        global_frame = asyncio.run(_handle_settings_read_command({'cmd': 'get_global_settings'}, self.state, **kwargs))
        group_frame = asyncio.run(_handle_settings_read_command({'cmd': 'get_group_settings', 'group': 'qa'}, self.state, **kwargs))
        self.assertEqual(global_frame['defaults'], defaults()['global'])
        self.assertEqual(global_frame['settings']['xterm_scrollback'], 9000)
        for key, scope in [('defaults', 'group'), ('engineer_defaults', 'engineer'), ('architect_defaults', 'architect')]:
            self.assertEqual(group_frame[key], defaults()[scope])
        self.assertNotIn('ai_provider_secrets', json.dumps(global_frame))

    def test_every_editable_default_survives_write_and_restart(self):
        # Contract test for all reset values, including maps/lists/enums. Live
        # side effects and arbitrary non-default values are separate acceptance.
        for scope, values in defaults().items():
            for key, value in values.items():
                if key in {'group', 'engineer_agent_id', 'engineer_hint_snoozes'} or key.startswith('pending_'):
                    continue
                with self.subTest(scope=scope, field=key):
                    if scope == 'global':
                        self.state.update_global_settings(**{key: value})
                    else:
                        getattr(self.state, f'update_{scope}_settings')('qa', **{key: value})
                    restarted = MatrixState(self.db)
                    restarted.load()
                    stored = restarted.global_settings if scope == 'global' else getattr(restarted, f'get_{scope}_settings')('qa')
                    self.assertEqual(getattr(stored, key), value)

    def test_digest_event_choices_match_classic_and_the_daemon_mandatory_floor(self):
        from torque.state import ENGINEER_MANDATORY_EVENTS, ARCHITECT_MANDATORY_EVENTS
        catalog = json.loads((ROOT / 'ui/src/features/control/digestEventCatalogs.json').read_text())
        self.assertEqual(set(catalog['engineer']['mandatory']), set(ENGINEER_MANDATORY_EVENTS))
        self.assertEqual(set(catalog['architect']['mandatory']), set(ARCHITECT_MANDATORY_EVENTS))
        classic = (ROOT / 'static/js/modals/group-settings.js').read_text()
        engineer = re.search(r'function _getEngineerEnabledEvents\(\) \{(.*?)\n\}', classic, re.S).group(1)
        self.assertEqual(catalog['engineer']['optional'], re.findall(r"events\.push\('([^']+)'\)", engineer))
        architect = re.search(r'const _ARCHITECT_DIGEST_EVENT_CATALOG = \[(.*?)\];', classic, re.S).group(1)
        optional = [event for event in re.findall(r"'([^']+)'", architect) if event not in ARCHITECT_MANDATORY_EVENTS]
        self.assertEqual(catalog['architect']['optional'], optional)

    def test_journal_checkpoint_choices_match_classic_and_are_backend_valid(self):
        from torque.state import normalize_architect_journal_checkpoint_frequency
        choices = json.loads((ROOT / 'ui/src/features/control/journalCheckpointChoices.json').read_text())
        classic = (ROOT / 'static/js/modals/group-settings.js').read_text()
        catalog = re.search(r'const _ARCHITECT_JOURNAL_CHECKPOINT_OPTIONS = \[(.*?)\];', classic, re.S).group(1)
        self.assertEqual(choices, re.findall(r"'([^']+)'", catalog))
        for value in choices + ['every_1_minutes', 'every_35_actions']:
            self.assertEqual(normalize_architect_journal_checkpoint_frequency(value, strict=True), value)

    def test_editable_numeric_boundaries_survive_write_and_restart(self):
        for scope, key, minimum, maximum in [
            ('group', 'guidance_hint_cadence', 0, 100),
            ('group', 'context_default_ttl_days', 1, 60),
            ('global', 'perceived_empty_probe_threshold', 2, 25),
            ('global', 'perceived_empty_window_seconds', 10, 3600),
        ]:
            for value in (minimum, maximum):
                with self.subTest(scope=scope, field=key, value=value):
                    if scope == 'global':
                        self.state.update_global_settings(**{key: value})
                    else:
                        self.state.update_group_settings('qa', **{key: value})
                    restarted = MatrixState(self.db)
                    restarted.load()
                    stored = restarted.global_settings if scope == 'global' else restarted.get_group_settings('qa')
                    self.assertEqual(getattr(stored, key), value)

    def test_engineer_visibility_setting_is_routed_and_persisted(self):
        runtime = EngineerOperationRuntime(**{field.name: None for field in fields(EngineerOperationRuntime)})
        runtime.state = self.state
        asyncio.run(handle_engineer_operation_command({'cmd': 'engineer_update_settings', 'group': 'qa', 'restrict_to_created_agents': True}, runtime))
        restarted = MatrixState(self.db)
        restarted.load()
        self.assertTrue(restarted.get_engineer_settings('qa').restrict_to_created_agents)

    def test_explicit_300_second_heartbeat_survives_single_bulk_and_restart_reads(self):
        self.state.update_engineer_settings('qa', max_interval=600, heartbeat_interval=300)
        self.db.save_agent_digest_settings('agent-qa', {'max_interval': 900, 'heartbeat_interval': 300})
        self.assertEqual(self.db.load_engineer_settings('qa')['heartbeat_interval'], 300)
        self.assertEqual(self.db.load_all_engineer_settings()['qa']['heartbeat_interval'], 300)
        self.assertEqual(self.db.load_agent_digest_settings('agent-qa')['heartbeat_interval'], 300)
        self.assertEqual(self.db.load_all_agent_digest_settings()['agent-qa']['heartbeat_interval'], 300)
        restarted = MatrixState(self.db)
        restarted.load()
        self.assertEqual(restarted.get_engineer_settings('qa').heartbeat_interval, 300)

    def test_pre_heartbeat_schema_migration_backfills_once_then_respects_edits(self):
        from torque.db_schema import _migration_0017_engineer_settings_contract
        conn = sqlite3.connect(':memory:')
        self.addCleanup(conn.close)
        conn.execute("CREATE TABLE engineer_settings (group_name TEXT PRIMARY KEY, max_interval INTEGER, enabled_events TEXT)")
        conn.execute("INSERT INTO engineer_settings VALUES ('qa', 240, '[]')")
        _migration_0017_engineer_settings_contract(conn, None)
        self.assertEqual(conn.execute('SELECT heartbeat_interval FROM engineer_settings').fetchone()[0], 240)
        conn.execute('UPDATE engineer_settings SET heartbeat_interval=300')
        _migration_0017_engineer_settings_contract(conn, None)
        self.assertEqual(conn.execute('SELECT heartbeat_interval FROM engineer_settings').fetchone()[0], 300)

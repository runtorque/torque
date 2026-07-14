"""Boundary tests for domain persistence modules."""

import unittest

from torque import db as db_module
from torque.db import TorqueDB
from torque.persistence.ai import AIPersistenceMixin
from torque.persistence import common
from torque.persistence.areas import AREA_COLUMNS, AreaPersistenceMixin
from torque.persistence.agent_history import AgentHistoryPersistenceMixin
from torque.persistence.architect_governance import (
    ArchitectGovernancePersistenceMixin,
)
from torque.persistence.behavior_overlays import BehaviorOverlayPersistenceMixin
from torque.persistence.digests import DigestPersistenceMixin
from torque.persistence.initiatives import (
    INITIATIVE_COLUMNS,
    InitiativePersistenceMixin,
)
from torque.persistence.migrations import MigrationPersistenceMixin
from torque.persistence.idea_briefs import (
    IDEA_BRIEF_COLUMNS,
    IdeaBriefPersistenceMixin,
)
from torque.persistence.playbooks import PlaybookPersistenceMixin
from torque.persistence.reliability import ReliabilityPersistenceMixin
from torque.persistence.snapshots import SnapshotPersistenceMixin
from torque.persistence.thinking import (
    THINKING_SCRATCHPAD_NOTE_COLUMNS,
    ThinkingPersistenceMixin,
)
from torque.persistence.task_ids import TaskIdPersistenceMixin
from torque.persistence.telemetry import TelemetryPersistenceMixin


class PersistenceModuleBoundaryTests(unittest.TestCase):
    def test_torque_db_composes_ai_persistence(self):
        self.assertTrue(issubclass(TorqueDB, AIPersistenceMixin))
        self.assertIs(
            TorqueDB.ai_upsert_summary,
            AIPersistenceMixin.ai_upsert_summary,
        )

    def test_torque_db_composes_task_id_persistence(self):
        self.assertTrue(issubclass(TorqueDB, TaskIdPersistenceMixin))
        self.assertIs(
            TorqueDB.migrate_task_ids_if_needed,
            TaskIdPersistenceMixin.migrate_task_ids_if_needed,
        )

    def test_torque_db_composes_telemetry_persistence(self):
        self.assertTrue(issubclass(TorqueDB, TelemetryPersistenceMixin))
        self.assertIs(
            TorqueDB.save_panel_events_batch,
            TelemetryPersistenceMixin.save_panel_events_batch,
        )

    def test_torque_db_composes_digest_persistence(self):
        self.assertTrue(issubclass(TorqueDB, DigestPersistenceMixin))
        self.assertIs(
            TorqueDB.complete_digest_delivery,
            DigestPersistenceMixin.complete_digest_delivery,
        )

    def test_torque_db_composes_snapshot_persistence(self):
        self.assertTrue(issubclass(TorqueDB, SnapshotPersistenceMixin))
        self.assertIs(TorqueDB.load_all, SnapshotPersistenceMixin.load_all)

    def test_torque_db_composes_migration_persistence(self):
        self.assertTrue(issubclass(TorqueDB, MigrationPersistenceMixin))
        self.assertIs(
            TorqueDB._backfill_kinds_if_needed,
            MigrationPersistenceMixin._backfill_kinds_if_needed,
        )

    def test_torque_db_composes_initiative_persistence(self):
        self.assertTrue(issubclass(TorqueDB, InitiativePersistenceMixin))
        self.assertIs(
            TorqueDB.create_initiative,
            InitiativePersistenceMixin.create_initiative,
        )

    def test_torque_db_composes_area_persistence(self):
        self.assertTrue(issubclass(TorqueDB, AreaPersistenceMixin))
        self.assertIs(TorqueDB.create_area, AreaPersistenceMixin.create_area)

    def test_torque_db_composes_thinking_persistence(self):
        self.assertTrue(issubclass(TorqueDB, ThinkingPersistenceMixin))
        self.assertIs(
            TorqueDB.create_scratchpad_note,
            ThinkingPersistenceMixin.create_scratchpad_note,
        )

    def test_torque_db_composes_idea_brief_persistence(self):
        self.assertTrue(issubclass(TorqueDB, IdeaBriefPersistenceMixin))
        self.assertIs(
            TorqueDB.create_idea_brief,
            IdeaBriefPersistenceMixin.create_idea_brief,
        )

    def test_torque_db_composes_architect_governance_persistence(self):
        self.assertTrue(
            issubclass(TorqueDB, ArchitectGovernancePersistenceMixin)
        )
        self.assertIs(
            TorqueDB.save_decision,
            ArchitectGovernancePersistenceMixin.save_decision,
        )

    def test_torque_db_composes_behavior_overlay_persistence(self):
        self.assertTrue(
            issubclass(TorqueDB, BehaviorOverlayPersistenceMixin)
        )
        self.assertIs(
            TorqueDB.save_behavior_overlay_version,
            BehaviorOverlayPersistenceMixin.save_behavior_overlay_version,
        )

    def test_torque_db_composes_agent_history_persistence(self):
        self.assertTrue(issubclass(TorqueDB, AgentHistoryPersistenceMixin))
        self.assertIs(
            TorqueDB.save_agent_peer_message,
            AgentHistoryPersistenceMixin.save_agent_peer_message,
        )

    def test_torque_db_composes_playbook_persistence(self):
        self.assertTrue(issubclass(TorqueDB, PlaybookPersistenceMixin))
        self.assertIs(
            TorqueDB.save_playbook,
            PlaybookPersistenceMixin.save_playbook,
        )

    def test_torque_db_composes_reliability_persistence(self):
        self.assertTrue(issubclass(TorqueDB, ReliabilityPersistenceMixin))
        self.assertIs(
            TorqueDB.save_mcp_idempotency,
            ReliabilityPersistenceMixin.save_mcp_idempotency,
        )

    def test_db_reexports_moved_symbols_for_compatibility(self):
        self.assertIs(db_module.INITIATIVE_COLUMNS, INITIATIVE_COLUMNS)
        self.assertIs(db_module.AREA_COLUMNS, AREA_COLUMNS)
        self.assertIs(
            db_module.THINKING_SCRATCHPAD_NOTE_COLUMNS,
            THINKING_SCRATCHPAD_NOTE_COLUMNS,
        )
        self.assertIs(db_module.IDEA_BRIEF_COLUMNS, IDEA_BRIEF_COLUMNS)
        self.assertIs(db_module._slugify, common.slugify)
        self.assertIs(db_module._unique_value, common.unique_value)
        self.assertIs(db_module._normalize_actor_kind, common.normalize_actor_kind)
        self.assertIs(db_module._snapshot_db_payload, common.snapshot_db_payload)


if __name__ == "__main__":
    unittest.main()

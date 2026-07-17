import tempfile
import unittest
from pathlib import Path

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub

install_aiohttp_stub()

from torque.db import TorqueDB
from torque.state import MatrixState


class OperatorNoticeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = TorqueDB(Path(self.tmp.name) / "torque.db")
        self.db.init()
        self.addCleanup(self.db.close)
        self.state = MatrixState(db=self.db)

    def test_alert_is_persisted_deduplicated_and_reopened(self):
        first = self.state.publish_operator_notice(
            notice_type="alert",
            severity="error",
            category="board_sync",
            title="GitHub sync failed",
            message="Remote rejected the update",
            task_id="TASK-1",
            action_kind="retry_board_sync",
            action_payload={"task_id": "TASK-1"},
            dedupe_key="sync:TASK-1",
            broadcast=False,
        )
        self.assertIsNotNone(first)
        self.assertEqual(first["occurrence_count"], 1)
        self.assertEqual(self.state.operator_notice_summary()["open_alerts"], 1)

        resolved = self.state.update_operator_notice(
            first["id"],
            "resolve",
            broadcast=False,
        )
        self.assertGreater(resolved["resolved_at"], 0)
        self.assertGreater(resolved["read_at"], 0)
        self.assertEqual(self.state.operator_notice_summary()["open_alerts"], 0)

        repeated = self.state.publish_operator_notice(
            notice_type="alert",
            severity="error",
            category="board_sync",
            title="GitHub sync failed",
            message="Remote rejected the update",
            task_id="TASK-1",
            action_kind="retry_board_sync",
            action_payload={"task_id": "TASK-1"},
            dedupe_key="sync:TASK-1",
            broadcast=False,
        )
        self.assertEqual(repeated["id"], first["id"])
        self.assertEqual(repeated["occurrence_count"], 2)
        self.assertEqual(repeated["resolved_at"], 0)
        self.assertEqual(repeated["read_at"], 0)
        self.assertEqual(self.state.operator_notice_summary()["open_alerts"], 1)

    def test_notification_read_archive_and_restore_lifecycle(self):
        notice = self.state.publish_operator_notice(
            notice_type="notification",
            severity="success",
            category="agent_lifecycle",
            title="Worker finished",
            message="Alpha completed TASK-2",
            agent_id="agent-1",
            task_id="TASK-2",
            action_kind="open_agent",
            broadcast=False,
        )
        summary = self.state.operator_notice_summary()
        self.assertEqual(summary["unread_notifications"], 1)
        self.assertEqual(summary["active_total"], 1)

        read = self.state.update_operator_notice(
            notice["id"],
            "read",
            broadcast=False,
        )
        self.assertGreater(read["read_at"], 0)
        self.assertEqual(
            self.state.operator_notice_summary()["unread_notifications"],
            0,
        )

        archived = self.state.update_operator_notice(
            notice["id"],
            "archive",
            broadcast=False,
        )
        self.assertGreater(archived["archived_at"], 0)
        self.assertEqual(self.state.operator_notice_summary()["active_total"], 0)

        restored = self.state.update_operator_notice(
            notice["id"],
            "restore",
            broadcast=False,
        )
        self.assertEqual(restored["archived_at"], 0)
        self.assertEqual(self.state.operator_notice_summary()["active_total"], 1)

    def test_mark_all_read_can_target_notice_type(self):
        alert = self.state.publish_operator_notice(
            notice_type="alert",
            title="Alert",
            message="Needs action",
            dedupe_key="alert-1",
            broadcast=False,
        )
        notification = self.state.publish_operator_notice(
            notice_type="notification",
            title="Update",
            message="For awareness",
            dedupe_key="notification-1",
            broadcast=False,
        )

        count = self.state.mark_all_operator_notices_read(
            notice_type="notification",
            broadcast=False,
        )
        self.assertEqual(count, 1)
        self.assertEqual(
            self.db.load_operator_notice(alert["id"])["read_at"],
            0,
        )
        self.assertGreater(
            self.db.load_operator_notice(notification["id"])["read_at"],
            0,
        )

    def test_snapshot_contains_typed_action_metadata(self):
        notice = self.state.publish_operator_notice(
            notice_type="notification",
            title="Open task",
            message="Review TASK-3",
            task_id="TASK-3",
            action_kind="open_task",
            action_payload={"task_id": "TASK-3"},
            broadcast=False,
        )
        snapshot = self.state.operator_notices_snapshot()
        self.assertEqual(
            snapshot[notice["id"]]["action_payload"],
            {"task_id": "TASK-3"},
        )
        self.assertEqual(
            snapshot[notice["id"]]["action_kind"],
            "open_task",
        )

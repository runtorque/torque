"""Durable BoardTask authored-content hash regression coverage."""

from dataclasses import fields, replace
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from torque.db import TorqueDB
from torque.db_board import (
    _BOARD_TASK_COLUMNS,
    _serialize_board_task,
    decode_board_task_row,
)
from torque.db_schema import _migration_0029_board_task_content_hash
from torque.server_artifacts import serialize_task_for_mcp
from torque.state import BoardTask, MatrixState
from torque.task_content import (
    TASK_CONTENT_HASH_EXCLUDED_FIELDS,
    TASK_CONTENT_HASH_INCLUDED_FIELDS,
    compute_task_content_hash,
)


def authored_task(**changes):
    values = {
        "id": "TORQUE:1",
        "task": "Implement durable identity",
        "description": "Pin the authored execution contract.",
        "group": "Torque",
        "action_name": "feature/implement",
        "action_vars": {"mode": "strict"},
        "agent_template": "careful",
        "suggested_action": "feature/review",
        "required_review_gates": [{
            "id": "review",
            "role": "review",
            "review_task_id": "TORQUE:1:1",
        }],
        "depends_on": ["TORQUE:3", "TORQUE:2"],
        "deliverable_required": True,
        "deliverable_type": "code",
        "deliverable_format": "git",
        "deliverable_artifact_title": "Implementation",
        "requires_review": True,
        "finalization_mode": "review_only",
        "instructions": "Preserve legacy instructions.",
        "context": "Preserve legacy context.",
        "criteria": "Preserve legacy criteria.",
    }
    values.update(changes)
    return BoardTask(**values)


class TaskContentHashManifestTests(unittest.TestCase):
    def test_body_edit_changes_hash(self):
        task = authored_task()
        changed = replace(task, description=task.description + " Updated.")
        self.assertNotEqual(
            compute_task_content_hash(task),
            compute_task_content_hash(changed),
        )

    def test_agent_template_change_changes_hash(self):
        careful = authored_task(agent_template="careful")
        fast = replace(careful, agent_template="fast")
        self.assertNotEqual(
            compute_task_content_hash(careful),
            compute_task_content_hash(fast),
        )

    def test_lane_transition_leaves_hash_unchanged(self):
        task = authored_task(lane="Backlog")
        changed = replace(task, lane="In Progress", lane_entered_at="later")
        self.assertEqual(
            compute_task_content_hash(task),
            compute_task_content_hash(changed),
        )

    def test_dispatch_state_change_leaves_hash_unchanged(self):
        task = authored_task(dispatch_state="queued")
        changed = replace(task, dispatch_state="live")
        self.assertEqual(
            compute_task_content_hash(task),
            compute_task_content_hash(changed),
        )

    def test_health_projection_update_leaves_hash_unchanged(self):
        task = authored_task(health_state="healthy", health_details={})
        changed = replace(
            task,
            health_state="blocked",
            health_since="later",
            health_details={"reason": "dependency"},
        )
        self.assertEqual(
            compute_task_content_hash(task),
            compute_task_content_hash(changed),
        )

    def test_completion_review_evidence_write_leaves_hash_unchanged(self):
        task = authored_task(completion_evidence={}, finalization_audit=[])
        changed = replace(
            task,
            completion_evidence={"tests": "passed"},
            finalization_audit=[{"outcome": "ship"}],
            finalization_status={"state": "complete"},
        )
        self.assertEqual(
            compute_task_content_hash(task),
            compute_task_content_hash(changed),
        )

    def test_label_write_leaves_hash_unchanged(self):
        task = authored_task(labels=["bug"])
        changed = replace(task, labels=["bug", "torque:runtime"])
        self.assertEqual(
            compute_task_content_hash(task),
            compute_task_content_hash(changed),
        )

    def test_manifest_classifies_every_board_task_field(self):
        actual = {item.name for item in fields(BoardTask)}
        included = set(TASK_CONTENT_HASH_INCLUDED_FIELDS)
        excluded = set(TASK_CONTENT_HASH_EXCLUDED_FIELDS)
        self.assertFalse(included & excluded)
        self.assertEqual(actual, included | excluded)

    def test_depends_on_is_an_order_insensitive_set(self):
        task = authored_task(depends_on=["TORQUE:2", "TORQUE:3", "TORQUE:2"])
        reordered = replace(task, depends_on=["TORQUE:3", "TORQUE:2"])
        changed = replace(task, depends_on=["TORQUE:2", "TORQUE:4"])
        self.assertEqual(
            compute_task_content_hash(task),
            compute_task_content_hash(reordered),
        )
        self.assertNotEqual(
            compute_task_content_hash(task),
            compute_task_content_hash(changed),
        )

    def test_identical_bodies_ignore_all_operational_state(self):
        first = authored_task(id="TORQUE:1", lane="Backlog", updated_at="one")
        second = authored_task(
            id="TORQUE:99",
            lane="Done",
            updated_at="two",
            agent_id="worker",
            messages=[{"action": "complete"}],
        )
        self.assertEqual(
            compute_task_content_hash(first),
            compute_task_content_hash(second),
        )

    def test_hash_has_no_ai_dependency(self):
        value = compute_task_content_hash(authored_task())
        self.assertRegex(value, r"^task-content-v1:sha256:[0-9a-f]{64}$")


class TaskContentHashBoundaryTests(unittest.TestCase):
    def test_serialization_round_trip_and_mcp_exposure_use_stored_hash(self):
        task = authored_task()
        task.task_content_hash = compute_task_content_hash(task)
        values = _serialize_board_task(task)
        decoded = decode_board_task_row(values, _BOARD_TASK_COLUMNS)
        restored = BoardTask(**{
            key: value for key, value in decoded.items()
            if key in BoardTask.__dataclass_fields__
        })

        self.assertEqual(restored.task_content_hash, task.task_content_hash)
        self.assertEqual(
            serialize_task_for_mcp(restored)["task_content_hash"],
            task.task_content_hash,
        )
        self.assertEqual(
            restored.task_content_hash,
            compute_task_content_hash(restored),
        )

    def test_unversioned_record_reads_without_error(self):
        task = authored_task(task_content_hash=None)
        values = _serialize_board_task(task)
        decoded = decode_board_task_row(values, _BOARD_TASK_COLUMNS)
        self.assertIsNone(decoded["task_content_hash"])
        self.assertIsNone(serialize_task_for_mcp(task)["task_content_hash"])

    def test_board_add_policy_projection_emits_only_current_hashes(self):
        state = MatrixState()
        state.groups["Torque"] = []
        task = state.board_add_task(
            "Original",
            "Torque",
            id="TORQUE:1",
            description="Before",
            finalization_mode="review_only",
        )
        upserts = [
            op for op in state._delta_ops
            if op.get("op") == "task_upsert" and op.get("id") == task.id
        ]
        self.assertGreaterEqual(len(upserts), 2)
        expected = compute_task_content_hash(task)
        self.assertTrue(all(
            upsert.get("task_content_hash") == expected
            for upsert in upserts
        ))

    def test_board_update_policy_projection_emits_only_current_hashes(self):
        state = MatrixState()
        state.groups["Torque"] = []
        task = state.board_add_task(
            "Original",
            "Torque",
            id="TORQUE:1",
            description="Before",
        )
        old_hash = task.task_content_hash
        start = len(state._delta_ops)
        state.board_update_task(
            task.id,
            description="After",
            finalization_mode="review_only",
        )
        upserts = [
            op for op in state._delta_ops[start:]
            if op.get("op") == "task_upsert" and op.get("id") == task.id
        ]
        self.assertGreaterEqual(len(upserts), 2)
        self.assertNotEqual(old_hash, task.task_content_hash)
        expected = compute_task_content_hash(task)
        self.assertTrue(all(
            upsert.get("task_content_hash") == expected
            for upsert in upserts
        ))

    def test_defensive_persist_refreshes_direct_mutation(self):
        state = MatrixState()
        task = authored_task(task_content_hash="stale")
        state._db_save_task(task)
        self.assertEqual(task.task_content_hash, compute_task_content_hash(task))

    def test_synthetic_v29_migration_eagerly_backfills_existing_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "synthetic.db"
            conn = sqlite3.connect(path)
            self.addCleanup(conn.close)
            conn.execute("""
                CREATE TABLE board_tasks (
                    id TEXT PRIMARY KEY, task TEXT, description TEXT,
                    action_name TEXT, action_vars TEXT, agent_template TEXT,
                    suggested_action TEXT, required_review_gates TEXT,
                    depends_on TEXT, deliverable_required INTEGER,
                    deliverable_type TEXT, deliverable_format TEXT,
                    deliverable_artifact_title TEXT, requires_review INTEGER,
                    finalization_mode TEXT, instructions TEXT,
                    context TEXT, criteria TEXT
                )
            """)
            conn.execute(
                "INSERT INTO board_tasks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "TORQUE:1", "Title", "Body", "feature/implement",
                    json.dumps({"mode": "strict"}), "careful", "feature/review",
                    json.dumps(["review"]), json.dumps(["TORQUE:3", "TORQUE:2"]),
                    1, "code", "git", "Artifact", 1, "review_only",
                    "instructions", "context", "criteria",
                ),
            )
            _migration_0029_board_task_content_hash(conn, None)
            stored = conn.execute(
                "SELECT task_content_hash FROM board_tasks WHERE id='TORQUE:1'"
            ).fetchone()[0]
        self.assertRegex(stored, r"^task-content-v1:sha256:[0-9a-f]{64}$")
        expected = compute_task_content_hash({
            "task": "Title",
            "description": "Body",
            "action_name": "feature/implement",
            "action_vars": {"mode": "strict"},
            "agent_template": "careful",
            "suggested_action": "feature/review",
            "required_review_gates": ["review"],
            "depends_on": ["TORQUE:3", "TORQUE:2"],
            "deliverable_required": True,
            "deliverable_type": "code",
            "deliverable_format": "git",
            "deliverable_artifact_title": "Artifact",
            "requires_review": True,
            "finalization_mode": "review_only",
            "instructions": "instructions",
            "context": "context",
            "criteria": "criteria",
        })
        self.assertEqual(stored, expected)

    def test_temporary_database_persists_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = TorqueDB(Path(tmp) / "torque.db")
            self.addCleanup(db.close)
            db.init()
            task = authored_task()
            task.task_content_hash = compute_task_content_hash(task)
            db.save_board_task(task)
            stored = db._conn.execute(
                "SELECT task_content_hash FROM board_tasks WHERE id=?",
                (task.id,),
            ).fetchone()[0]
        self.assertEqual(stored, task.task_content_hash)


if __name__ == "__main__":
    unittest.main()

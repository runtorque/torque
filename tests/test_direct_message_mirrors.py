import tempfile
import unittest
from pathlib import Path

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub

from torque.db import TorqueDB, canonical_user_agent_thread_id
from torque.direct_message_mirrors import (
    resolve_ask_owner_recipient,
    save_direct_ask_mirror,
    save_direct_ask_reply_mirror,
)

install_aiohttp_stub()
from torque.state import AgentCell, MatrixState


class DirectMessageMirrorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = TorqueDB(Path(self.tmp.name) / "torque.db")
        self.db.init()
        self.addCleanup(self.db.close)
        self.state = MatrixState(db=self.db)
        self.state.add_group("g")

    def _add_agent(self, agent_id: str, kind: str, name: str, **kwargs):
        cell = AgentCell(
            id=agent_id,
            name=name,
            slug=agent_id,
            group="g",
            cell_type="agent",
            kind=kind,
            **kwargs,
        )
        self.state.agents[cell.id] = cell
        self.state.groups.setdefault("g", []).append(cell.id)
        return cell

    def test_resolve_ask_owner_recipient_is_owner_aware_by_agent_kind(self):
        architect = self._add_agent("arch-1", "architect", "Architect")
        engineer = self._add_agent(
            "eng-1",
            "engineer",
            "Engineer",
            hired_by_architect_id=architect.id,
        )
        worker = self._add_agent(
            "worker-1",
            "worker",
            "Worker",
            owner_engineer_id=engineer.id,
        )
        user_owned_engineer = self._add_agent(
            "eng-user",
            "engineer",
            "User Owned Engineer",
        )
        user_owned_worker = self._add_agent(
            "worker-user",
            "worker",
            "User Owned Worker",
        )

        worker_recipient = resolve_ask_owner_recipient(self.state, worker)
        self.assertEqual(worker_recipient.id, engineer.id)
        self.assertEqual(worker_recipient.kind, "engineer")
        self.assertEqual(worker_recipient.name, engineer.name)

        engineer_recipient = resolve_ask_owner_recipient(self.state, engineer)
        self.assertEqual(engineer_recipient.id, architect.id)
        self.assertEqual(engineer_recipient.kind, "architect")
        self.assertEqual(engineer_recipient.name, architect.name)

        architect_recipient = resolve_ask_owner_recipient(self.state, architect)
        self.assertEqual(architect_recipient.id, "user")
        self.assertEqual(architect_recipient.kind, "user")

        fallthrough_engineer = resolve_ask_owner_recipient(
            self.state,
            user_owned_engineer,
        )
        self.assertEqual(fallthrough_engineer.id, "user")
        self.assertEqual(fallthrough_engineer.kind, "user")

        fallthrough_worker = resolve_ask_owner_recipient(
            self.state,
            user_owned_worker,
        )
        self.assertEqual(fallthrough_worker.id, "user")
        self.assertEqual(fallthrough_worker.kind, "user")

    def test_worker_ask_mirror_seeds_direct_cache_not_peer_cache(self):
        engineer = self._add_agent("eng-1", "engineer", "Engineer")
        worker = self._add_agent(
            "worker-1",
            "worker",
            "Worker",
            owner_engineer_id=engineer.id,
        )

        ask_row = save_direct_ask_mirror(
            self.state,
            worker,
            "Can I use the fallback API?",
            source_task_id="ask-task-1",
            created_at=10.0,
        )
        duplicate_ask = save_direct_ask_mirror(
            self.state,
            worker,
            "Can I use the fallback API?",
            source_task_id="ask-task-1",
            created_at=11.0,
        )

        self.assertEqual(duplicate_ask["id"], ask_row["id"])
        self.assertEqual(ask_row["message_type"], "ask")
        self.assertTrue(ask_row["blocking"])
        self.assertEqual(ask_row["source_task_id"], "ask-task-1")
        self.assertEqual(ask_row["sender_id"], worker.id)
        self.assertEqual(ask_row["sender_kind"], "worker")
        self.assertEqual(ask_row["recipient_id"], engineer.id)
        self.assertEqual(ask_row["recipient_kind"], "engineer")
        self.assertEqual(
            ask_row["thread_id"],
            canonical_user_agent_thread_id(worker.id),
        )
        self.assertEqual(ask_row["delivery_state"], "delivered")
        self.assertEqual(ask_row["read_at"], 0)

        self.assertEqual(worker.mcp_messages, [])
        self.assertEqual(engineer.mcp_messages, [])
        self.assertEqual(
            [row["id"] for row in self.db.load_agent_peer_messages_for_agent(worker.id)],
            [],
        )
        self.assertEqual(
            [row["id"] for row in self.db.load_agent_peer_messages_for_agent(engineer.id)],
            [],
        )
        self.assertEqual(
            [row["id"] for row in self.db.load_direct_messages_for_agent(worker.id)],
            [ask_row["id"]],
        )
        self.assertEqual(
            [row["id"] for row in self.db.load_direct_messages_for_agent(engineer.id)],
            [ask_row["id"]],
        )

        worker_cached = self.state.direct_messages_by_agent[worker.id][0]
        engineer_cached = self.state.direct_messages_by_agent[engineer.id][0]
        self.assertEqual(worker_cached["id"], ask_row["id"])
        self.assertEqual(worker_cached["direction"], "sent")
        self.assertEqual(worker_cached["peer_kind"], "engineer")
        self.assertEqual(engineer_cached["id"], ask_row["id"])
        self.assertEqual(engineer_cached["direction"], "received")
        self.assertEqual(engineer_cached["peer_kind"], "worker")

    def test_ask_reply_mirror_links_to_display_ask_row(self):
        engineer = self._add_agent("eng-1", "engineer", "Engineer")
        worker = self._add_agent(
            "worker-1",
            "worker",
            "Worker",
            owner_engineer_id=engineer.id,
        )

        ask_row = save_direct_ask_mirror(
            self.state,
            worker,
            "Should I proceed?",
            source_task_id="ask-task-2",
            created_at=20.0,
        )
        reply_row = save_direct_ask_reply_mirror(
            self.state,
            worker,
            "Proceed with the minimal version.",
            question="Should I proceed?",
            source_task_id="ask-task-2",
            created_at=30.0,
        )
        duplicate_reply = save_direct_ask_reply_mirror(
            self.state,
            worker,
            "Proceed with the minimal version.",
            question="Should I proceed?",
            source_task_id="ask-task-2",
            created_at=31.0,
        )

        self.assertEqual(duplicate_reply["id"], reply_row["id"])
        self.assertEqual(reply_row["message_type"], "ask_reply")
        self.assertFalse(reply_row["blocking"])
        self.assertEqual(reply_row["reply_to_id"], ask_row["id"])
        self.assertEqual(reply_row["source_task_id"], "ask-task-2")
        self.assertEqual(reply_row["sender_id"], engineer.id)
        self.assertEqual(reply_row["sender_kind"], "engineer")
        self.assertEqual(reply_row["recipient_id"], worker.id)
        self.assertEqual(reply_row["recipient_kind"], "worker")
        self.assertEqual(reply_row["thread_id"], ask_row["thread_id"])

        self.assertEqual(
            [entry["id"] for entry in self.state.direct_messages_by_agent[worker.id]],
            [ask_row["id"], reply_row["id"]],
        )
        self.assertEqual(
            [entry["id"] for entry in self.state.direct_messages_by_agent[engineer.id]],
            [ask_row["id"], reply_row["id"]],
        )
        self.assertEqual(worker.mcp_messages, [])
        self.assertEqual(engineer.mcp_messages, [])


if __name__ == "__main__":
    unittest.main()

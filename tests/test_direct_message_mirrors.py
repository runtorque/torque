import tempfile
import unittest
from pathlib import Path

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub

from torque.db import TorqueDB, canonical_user_agent_thread_id
from torque.direct_message_mirrors import (
    NON_USER_ASK_LABEL,
    ask_owner_recipient_is_user,
    ask_recipient_is_user,
    ask_task_labels_for_owner_recipient,
    participant_is_user,
    resolve_ask_owner_recipient,
    resolve_ask_recipient,
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

    def test_ask_user_attention_labels_follow_owner_aware_recipient(self):
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
        user_owned_worker = self._add_agent(
            "worker-user",
            "worker",
            "User Owned Worker",
        )

        self.assertTrue(ask_owner_recipient_is_user(self.state, architect))
        self.assertTrue(
            ask_owner_recipient_is_user(self.state, user_owned_worker)
        )
        self.assertFalse(ask_owner_recipient_is_user(self.state, worker))

        base = ["torque:human", "torque:derived"]
        self.assertEqual(
            ask_task_labels_for_owner_recipient(self.state, architect, base),
            base,
        )
        self.assertEqual(
            ask_task_labels_for_owner_recipient(self.state, worker, base),
            ["torque:human", "torque:derived", NON_USER_ASK_LABEL],
        )

    def test_owner_routed_ask_mirror_is_excluded_from_user_dm_cache_and_loader(self):
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
        self.assertEqual(self.db.load_direct_messages_for_agent(worker.id), [])
        self.assertEqual(self.db.load_direct_messages_for_agent(engineer.id), [])
        self.assertEqual(self.state.direct_messages_by_agent.get(worker.id, []), [])
        self.assertEqual(self.state.direct_messages_by_agent.get(engineer.id, []), [])
        self.assertEqual(self.db.load_recent_user_direct_messages(limit=10), [])

    def test_canonical_resolver_name_and_alias_are_the_same(self):
        # The canonical resolver and its back-compat alias must be one object,
        # so no surface can drift onto a parallel copy.
        self.assertIs(resolve_ask_recipient, resolve_ask_owner_recipient)
        self.assertIs(ask_recipient_is_user, ask_owner_recipient_is_user)

    def test_routing_table_full_matrix(self):
        architect = self._add_agent("arch-1", "architect", "Architect")
        hired_engineer = self._add_agent(
            "eng-1",
            "engineer",
            "Hired Engineer",
            hired_by_architect_id=architect.id,
        )
        user_engineer = self._add_agent("eng-user", "engineer", "User Engineer")
        eng_worker = self._add_agent(
            "worker-eng",
            "worker",
            "Engineer Worker",
            owner_engineer_id=hired_engineer.id,
        )
        user_worker = self._add_agent("worker-user", "worker", "User Worker")

        cases = [
            # (agent, expected recipient kind, expected recipient id, is_user)
            (eng_worker, "engineer", hired_engineer.id, False),
            (user_worker, "user", "user", True),
            (hired_engineer, "architect", architect.id, False),
            (user_engineer, "user", "user", True),
            (architect, "user", "user", True),
        ]
        for agent, kind, ident, is_user in cases:
            recipient = resolve_ask_recipient(self.state, agent)
            self.assertEqual(recipient.kind, kind, agent.name)
            self.assertEqual(recipient.id, ident, agent.name)
            self.assertEqual(
                ask_recipient_is_user(self.state, agent), is_user, agent.name
            )
            self.assertEqual(
                participant_is_user(recipient.kind, recipient.id),
                is_user,
                agent.name,
            )

    def test_owner_absent_falls_through_to_user(self):
        # owner_engineer_id pointing at a missing/deleted owner -> user.
        orphan_worker = self._add_agent(
            "worker-orphan",
            "worker",
            "Orphan Worker",
            owner_engineer_id="eng-missing",
        )
        # hired_by_architect_id pointing at a missing architect -> user.
        orphan_engineer = self._add_agent(
            "eng-orphan",
            "engineer",
            "Orphan Engineer",
            hired_by_architect_id="arch-missing",
        )
        # owner id resolving to a wrong-kind cell -> user.
        terminal = self._add_agent("term-1", "terminal", "Terminal")
        miswired_worker = self._add_agent(
            "worker-miswired",
            "worker",
            "Miswired Worker",
            owner_engineer_id=terminal.id,
        )

        for agent in (orphan_worker, orphan_engineer, miswired_worker):
            recipient = resolve_ask_recipient(self.state, agent)
            self.assertEqual(recipient.kind, "user", agent.name)
            self.assertEqual(recipient.id, "user", agent.name)
            self.assertTrue(ask_recipient_is_user(self.state, agent), agent.name)

    def test_none_agent_resolves_to_user(self):
        recipient = resolve_ask_recipient(self.state, None)
        self.assertEqual(recipient.kind, "user")
        self.assertEqual(recipient.id, "user")
        self.assertTrue(ask_recipient_is_user(self.state, None))

    def test_snapshot_and_deltas_project_only_user_destined_raises(self):
        architect = self._add_agent("arch-1", "architect", "Architect")
        hired_engineer = self._add_agent(
            "eng-hired", "engineer", "Hired Engineer",
            hired_by_architect_id=architect.id,
        )
        owned_worker = self._add_agent(
            "worker-owned", "worker", "Owned Worker",
            owner_engineer_id=hired_engineer.id,
        )
        orphan_worker = self._add_agent("worker-orphan", "worker", "Orphan")
        orphan_engineer = self._add_agent("eng-orphan", "engineer", "Orphan Engineer")

        emitted = []
        original_emit = self.state._emit
        self.state._emit = lambda operation, **payload: emitted.append((operation, payload))
        try:
            rows = {
                cell.id: save_direct_ask_mirror(
                    self.state, cell, f"Question from {cell.id}?",
                    source_task_id=f"raise-{cell.id}",
                )
                for cell in (
                    owned_worker, hired_engineer, architect,
                    orphan_worker, orphan_engineer,
                )
            }
        finally:
            self.state._emit = original_emit

        snapshot = self.state.direct_messages_snapshot()
        visible_ids = {
            entry["id"]
            for entries in snapshot.values()
            for entry in entries
        }
        self.assertEqual(
            visible_ids,
            {rows[architect.id]["id"], rows[orphan_worker.id]["id"], rows[orphan_engineer.id]["id"]},
        )
        self.assertNotIn(owned_worker.id, snapshot)
        self.assertNotIn(hired_engineer.id, snapshot)
        self.assertEqual(
            [operation for operation, _payload in emitted],
            ["direct_message_upsert", "direct_message_upsert", "direct_message_upsert"],
        )
        self.assertEqual(
            {payload["id"] for _operation, payload in emitted}, visible_ids,
        )
        self.assertEqual(
            {row["id"] for row in self.db.load_recent_user_direct_messages(limit=10)},
            visible_ids,
        )

    def test_mirror_recipient_matches_canonical_resolver(self):
        # The recipient stamped onto the persisted DM row must be IDENTICAL to
        # the canonical resolver output -- this is what the connector egress
        # and P6 consume, so the two must never disagree.
        engineer = self._add_agent("eng-1", "engineer", "Engineer")
        worker = self._add_agent(
            "worker-1",
            "worker",
            "Worker",
            owner_engineer_id=engineer.id,
        )
        user_worker = self._add_agent("worker-user", "worker", "User Worker")

        for agent in (worker, user_worker):
            resolved = resolve_ask_recipient(self.state, agent)
            row = save_direct_ask_mirror(
                self.state,
                agent,
                "Question?",
                source_task_id=f"ask-{agent.id}",
            )
            self.assertEqual(row["recipient_kind"], resolved.kind, agent.name)
            self.assertEqual(row["recipient_id"], resolved.id, agent.name)
            self.assertEqual(
                participant_is_user(row["recipient_kind"], row["recipient_id"]),
                ask_recipient_is_user(self.state, agent),
                agent.name,
            )

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

        self.assertEqual(self.state.direct_messages_by_agent.get(worker.id, []), [])
        self.assertEqual(self.state.direct_messages_by_agent.get(engineer.id, []), [])
        self.assertEqual(self.db.load_direct_messages_for_agent(worker.id), [])
        # The reply remains a durable non-user transport row for reconnect
        # replay, despite being absent from the user conversation projection.
        buffered = self.state.update_direct_message_delivery(
            reply_row["id"], "buffered", emit=False)
        self.assertEqual(buffered["delivery_state"], "buffered")
        self.assertEqual(
            [row["id"] for row in self.db.load_buffered_direct_messages(worker.id)],
            [reply_row["id"]],
        )
        self.assertEqual(worker.mcp_messages, [])
        self.assertEqual(engineer.mcp_messages, [])

    def test_load_recent_user_direct_messages_is_bounded_newest_first(self):
        engineer = self._add_agent("eng-1", "engineer", "Engineer")
        owned_worker = self._add_agent(
            "worker-owned",
            "worker",
            "Owned Worker",
            owner_engineer_id=engineer.id,
        )
        user_worker = self._add_agent("worker-user", "worker", "User Worker")

        # Only the resolver-stamped user-destined ask belongs in this loader;
        # the owner-routed row stays durable but is not user-DM projection.
        user_ask = save_direct_ask_mirror(
            self.state, user_worker, "user ask",
            source_task_id="t-user", created_at=10.0,
        )
        owner_ask = save_direct_ask_mirror(
            self.state, owned_worker, "owner ask",
            source_task_id="t-owner", created_at=20.0,
        )

        recent = self.db.load_recent_user_direct_messages(limit=10)
        self.assertEqual([row["id"] for row in recent], [user_ask["id"]])
        self.assertNotIn(owner_ask["id"], [row["id"] for row in recent])

        bounded = self.db.load_recent_user_direct_messages(limit=1)
        self.assertEqual([row["id"] for row in bounded], [user_ask["id"]])


if __name__ == "__main__":
    unittest.main()

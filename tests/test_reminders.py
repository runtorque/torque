import asyncio
import tempfile
import time
import unittest
from pathlib import Path

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub
install_aiohttp_stub()

from torque.commands.user_dm import parse_user_dm_command, user_dm_command_catalog
from torque.db import TorqueDB
from torque.server import _handle_user_agent_message_command
from torque.state import AgentCell, MatrixState


class ReminderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.db = TorqueDB(Path(self.tmp.name) / "torque.db"); self.db.init(); self.addCleanup(self.db.close)
        self.state = MatrixState(self.db); self.state.groups["g"] = []
        self.agent = AgentCell(id="agent", name="Agent", group="g", cell_type="agent", kind="worker", session_id="session")
        self.state.agents[self.agent.id] = self.agent; self.state.groups["g"] = [self.agent.id]

    async def command(self, text, key="key"):
        sent = []
        async def prompt(*args, **kwargs): sent.append((args, kwargs))
        result = await _handle_user_agent_message_command(
            {"agent_id": self.agent.id, "message": text, "idempotency_key": key}, self.state, prompt)
        self.assertEqual(sent, [])
        return result

    async def test_create_list_due_and_cancel_are_local_and_durable(self):
        result = await self.command(" /remind in 1m line one\nline two ", "create")
        self.assertEqual(result["type"], "ok")
        reminder = self.db.list_reminders(status="pending")[0]
        self.assertEqual(reminder["message"], "line one\nline two")
        listed = await self.command("/reminders", "list")
        self.assertIn(reminder["id"], self.db.load_direct_message(listed["message_id"])["message"])
        cancelled = await self.command("/remind cancel " + reminder["id"], "cancel")
        self.assertIn("Cancelled reminder", self.db.load_direct_message(cancelled["message_id"])["message"])
        self.assertEqual(self.db.load_reminder(reminder["id"])["status"], "cancelled")

    async def test_due_delivery_is_server_owned_once_and_retry_safe(self):
        await self.command("/remind in 1m check this", "create")
        reminder = self.db.list_reminders(status="pending")[0]
        self.db._conn.execute("UPDATE reminders SET due_at=0 WHERE id=?", (reminder["id"],)); self.db._conn.commit()
        self.state.reconcile_reminders(now=time.time())
        row = self.db.load_direct_message(reminder["id"] + ":due")
        self.assertEqual(self.db.load_reminder(reminder["id"])["status"], "delivered")
        self.assertEqual(row["message_type"], "reminder")
        self.assertEqual(row["sender_id"], "torque-server")
        self.state.reconcile_reminders(now=time.time())
        self.assertEqual(len([item for item in self.db.load_direct_messages_for_thread(row["thread_id"]) if item["id"] == row["id"]]), 1)

    async def test_deleted_target_falls_back_without_scoped_metadata(self):
        await self.command("/remind in 1m hidden", "create")
        reminder = self.db.list_reminders(status="pending")[0]
        self.state.agents.pop(self.agent.id)
        self.db._conn.execute("UPDATE reminders SET due_at=0 WHERE id=?", (reminder["id"],)); self.db._conn.commit()
        self.state.reconcile_reminders(now=time.time())
        notice = self.db.load_operator_notice_for_dedupe(reminder["dedupe_key"])
        self.assertEqual(self.db.load_reminder(reminder["id"])["status"], "delivered")
        self.assertEqual(notice["category"], "reminder")
        self.assertEqual(notice["group_name"], "")
        self.assertEqual(notice["agent_id"], "")

    async def test_idempotency_and_closed_parser(self):
        first = await self.command("/remind in 1m once", "retry")
        second = await self.command("/remind in 1m once", "retry")
        self.assertTrue(second["deduped"])
        self.assertEqual(len(self.db.list_reminders(status="pending")), 1)
        for index, text in enumerate(("/remind in 59s no", "/remind in 31d no", "/remind in 1M no", "/remind in 1m  no", "/remind cancel", "/REMIND in 1m no")):
            response = await self.command(text, "bad" + str(index))
            self.assertEqual(response["type"], "ok")
        self.assertIsNone(parse_user_dm_command("/remindful prose"))
        self.assertEqual(parse_user_dm_command(" /remind in 1m hi ").id, "remind")
        self.assertIn("remind", [item["id"] for item in user_dm_command_catalog()])

    async def test_active_limit_and_keyset_reconcile(self):
        now = time.time()
        for index in range(1001):
            self.db.save_reminder({
                "id": f"rem-{index:012x}", "requester_id": "other", "requester_agent_id": "x",
                "thread_id": "user-agent:user:x", "target_agent_id": "missing", "group_name": "",
                "message": "x", "created_at": now, "due_at": 0, "terminal_at": 0, "status": "pending",
                "cancelled_at": 0, "delivered_at": 0, "request_idempotency_key": "", "dedupe_key": f"bulk:{index}",
                "outbox_state": "pending", "attempt_count": 0, "last_attempt_at": 0, "last_error": "", "updated_at": now,
            })
        self.state.reconcile_reminders(now=now)
        self.assertEqual(self.db.load_reminder("rem-0000000003e8")["status"], "delivered")

    async def test_active_limit_is_exactly_one_hundred(self):
        for index in range(100):
            result = await self.command(f"/remind in 1m item {index}", "limit" + str(index))
            self.assertEqual(result["type"], "ok")
        overflow = await self.command("/remind in 1m overflow", "limit-overflow")
        self.assertIn("At most 100", self.db.load_direct_message(overflow["message_id"])["message"])
        self.assertEqual(len(self.db.list_reminders(requester_id="user", status="pending", limit=101)), 100)

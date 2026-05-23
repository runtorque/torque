import asyncio
import tempfile
import time
import unittest
import logging
from pathlib import Path
import json
import sqlite3
from unittest import mock

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub

from torque.db import TorqueDB, canonical_user_agent_thread_id

install_aiohttp_stub()
from torque.state import (
    AgentCell,
    BoardTask,
    GlobalSettings,
    GroupSettings,
    MatrixState,
    Schedule,
)


class TorqueDBTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = TorqueDB(Path(self.tmp.name) / "torque.db")
        self.db.init()
        self.addCleanup(self.db.close)

    def test_global_settings_round_trips_xterm_scrollback(self):
        self.db.save_global_settings(GlobalSettings(xterm_scrollback=4321))

        snapshot = self.db.load_all()

        self.assertEqual(snapshot["global_settings"]["xterm_scrollback"], 4321)
        self.assertEqual(
            self.db._conn.execute(
                "SELECT xterm_scrollback FROM global_settings "
                "WHERE key='xterm_scrollback'"
            ).fetchone()[0],
            4321,
        )

    def test_global_settings_round_trips_relay_fields(self):
        self.db.save_global_settings(GlobalSettings(
            relay_enabled=True,
            relay_url="wss://relay.example/ws",
            relay_daemon_id="daemon-9",
            relay_credential_id="cred-9",
            relay_private_key_path="/keys/relay.pem",
        ))

        gs = self.db.load_all()["global_settings"]

        self.assertIs(gs["relay_enabled"], True)
        self.assertEqual(gs["relay_url"], "wss://relay.example/ws")
        self.assertEqual(gs["relay_daemon_id"], "daemon-9")
        self.assertEqual(gs["relay_credential_id"], "cred-9")
        self.assertEqual(gs["relay_private_key_path"], "/keys/relay.pem")

    def test_global_settings_legacy_db_without_relay_keys_loads_defaults(self):
        # Upgrade path: a global_settings table populated before relay fields
        # existed must reconstruct to safe defaults (relay off, empty strings)
        # — the key/value blob storage needs no schema migration for new fields.
        legacy_path = Path(self.tmp.name) / "legacy-relay.db"
        conn = sqlite3.connect(str(legacy_path))
        conn.execute(
            "CREATE TABLE global_settings ("
            "key TEXT PRIMARY KEY, value TEXT NOT NULL, "
            "xterm_scrollback INTEGER NOT NULL DEFAULT 2000)"
        )
        for key, value in (("default_command", '"claude"'),
                           ("xterm_scrollback", "2500")):
            conn.execute(
                "INSERT INTO global_settings (key, value, xterm_scrollback) "
                "VALUES (?,?,?)",
                (key, value, 2500),
            )
        conn.commit()
        conn.close()

        migrated = TorqueDB(legacy_path)
        self.addCleanup(migrated.close)
        migrated.init()
        gs_raw = migrated.load_all()["global_settings"]
        # Legacy keys preserved; relay keys simply absent from the blob.
        self.assertNotIn("relay_url", gs_raw)
        # Reconstructing the dataclass fills relay defaults (mirrors state.load).
        gls_fields = set(GlobalSettings.__dataclass_fields__)
        gs = GlobalSettings(**{
            k: v for k, v in gs_raw.items() if k in gls_fields})
        self.assertFalse(gs.relay_enabled)
        self.assertEqual(gs.relay_url, "")
        self.assertEqual(gs.relay_private_key_path, "")
        self.assertEqual(gs.default_command, "claude")

    def test_global_settings_schema_migrates_xterm_scrollback_column(self):
        legacy_path = Path(self.tmp.name) / "legacy-global-settings.db"
        conn = sqlite3.connect(str(legacy_path))
        conn.execute(
            "CREATE TABLE global_settings ("
            "key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        conn.commit()
        conn.close()

        migrated = TorqueDB(legacy_path)
        self.addCleanup(migrated.close)
        migrated.init()

        columns = {
            row[1]
            for row in migrated._conn.execute("PRAGMA table_info(global_settings)")
        }
        self.assertIn("xterm_scrollback", columns)

    def test_board_task_messages_thread_round_trips(self):
        task = BoardTask(
            id="task-1",
            task="Inline thread parent",
            group="g",
            messages_thread=[{
                "timestamp": 123.0,
                "sender_agent_id": "engineer-1",
                "recipient_agent_id": "worker-1",
                "content": "No reply needed; continue with context.",
                "reply_required": False,
            }],
        )

        self.db.save_board_task(task)
        snapshot = self.db.load_all()

        self.assertEqual(
            snapshot["board_tasks"]["task-1"]["messages_thread"],
            task.messages_thread,
        )
        row = self.db._conn.execute(
            "SELECT messages_thread FROM board_tasks WHERE id='task-1'"
        ).fetchone()
        self.assertEqual(json.loads(row[0]), task.messages_thread)

    def test_save_board_tasks_rolls_back_entire_batch_on_failure(self):
        first = BoardTask(id="task-1", task="First", group="g", lane="Done")
        second = BoardTask(id="task-2", task="Second", group="g", lane="Done")
        original_insert = self.db._insert_board_task_row

        def fail_on_second(executor, task):
            if task.id == "task-2":
                raise RuntimeError("synthetic insert failure")
            return original_insert(executor, task)

        with mock.patch.object(
            self.db,
            "_insert_board_task_row",
            side_effect=fail_on_second,
        ):
            with self.assertRaisesRegex(RuntimeError, "synthetic insert failure"):
                self.db.save_board_tasks([first, second])

        row_count = self.db._conn.execute(
            "SELECT COUNT(*) FROM board_tasks"
        ).fetchone()[0]
        self.assertEqual(row_count, 0)

    def test_board_task_messages_thread_schema_migration_is_idempotent(self):
        legacy_path = Path(self.tmp.name) / "legacy-messages-thread.db"
        seeded = TorqueDB(legacy_path)
        seeded.init()
        seeded.save_board_task(BoardTask(id="task-1", task="Legacy task", group="g"))
        seeded.close()

        conn = sqlite3.connect(str(legacy_path))
        columns = [
            row[1]
            for row in conn.execute("PRAGMA table_info(board_tasks)")
            if row[1] != "messages_thread"
        ]
        quoted_columns = ", ".join(f'"{col}"' for col in columns)
        conn.execute(
            f"CREATE TABLE board_tasks_without_messages_thread "
            f"AS SELECT {quoted_columns} FROM board_tasks"
        )
        conn.execute("DROP TABLE board_tasks")
        conn.execute(
            "ALTER TABLE board_tasks_without_messages_thread "
            "RENAME TO board_tasks"
        )
        conn.commit()
        conn.close()

        migrated = TorqueDB(legacy_path)
        migrated.init()
        migrated.close()
        migrated_again = TorqueDB(legacy_path)
        self.addCleanup(migrated_again.close)
        migrated_again.init()

        columns = [
            row[1]
            for row in migrated_again._conn.execute(
                "PRAGMA table_info(board_tasks)"
            )
        ]
        self.assertEqual(columns.count("messages_thread"), 1)
        row = migrated_again._conn.execute(
            "SELECT messages_thread FROM board_tasks LIMIT 1"
        ).fetchone()
        self.assertEqual(json.loads(row[0]), [])

    def test_agent_message_history_round_trips_newest_first_and_cascades(self):
        agent = AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            cell_type="agent",
        )
        self.db.save_agent(agent)

        first = self.db.save_agent_message_history({
            "agent_id": agent.id,
            "message": "first",
            "sent_at": 1.0,
        })
        second = self.db.save_agent_message_history({
            "agent_id": agent.id,
            "message": "second",
            "sent_at": 2.0,
        })

        self.assertGreater(second["id"], first["id"])
        self.assertEqual(
            [
                entry["message"]
                for entry in self.db.load_agent_message_history(agent.id)
            ],
            ["second", "first"],
        )
        self.assertEqual(
            [
                entry["message"]
                for entry in self.db.load_agent_message_history(
                    agent.id,
                    limit=1,
                )
            ],
            ["second"],
        )

        self.db.delete_agent(agent.id)

        self.assertEqual(self.db.load_agent_message_history(agent.id), [])

    def test_agent_peer_messages_schema_has_table_and_indexes(self):
        columns = [
            row[1]
            for row in self.db._conn.execute(
                "PRAGMA table_info(agent_peer_messages)"
            ).fetchall()
        ]
        self.assertEqual(columns, [
            "id",
            "thread_id",
            "reply_to_id",
            "group_name",
            "sender_id",
            "sender_kind",
            "sender_name",
            "recipient_id",
            "recipient_kind",
            "recipient_name",
            "message",
            "message_type",
            "created_at",
            "ack_required",
            "blocking",
            "source_task_id",
            "context_task_ids",
            "context_engineer_ids",
            "context_decision_ids",
            "context_summary",
            "context_snapshot",
            "delivery_state",
            "delivery_reason",
            "delivered_at",
            "read_at",
            "archived_at",
            # Additive (TORQUE:602): the base table is created by
            # initialize_database(); _ensure_agent_peer_messages_schema()'s
            # column-add loop appends idempotency_key last on both fresh + upgrade.
            "idempotency_key",
        ])
        indexes = {
            row[1]
            for row in self.db._conn.execute(
                "PRAGMA index_list(agent_peer_messages)"
            ).fetchall()
        }
        self.assertTrue({
            "idx_agent_peer_messages_recipient_recent",
            "idx_agent_peer_messages_sender_recent",
            "idx_agent_peer_messages_thread",
            "idx_agent_peer_messages_group_recent",
        }.issubset(indexes))

    def test_agent_peer_messages_migration_is_idempotent(self):
        legacy_path = Path(self.tmp.name) / "legacy-peer-messages.db"
        seeded = TorqueDB(legacy_path)
        seeded.init()
        seeded.close()

        conn = sqlite3.connect(str(legacy_path))
        conn.execute("DROP TABLE agent_peer_messages")
        conn.commit()
        conn.close()

        migrated = TorqueDB(legacy_path)
        migrated.init()
        migrated.close()
        migrated_again = TorqueDB(legacy_path)
        self.addCleanup(migrated_again.close)
        migrated_again.init()

        tables = {
            row[0]
            for row in migrated_again._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        self.assertIn("agent_peer_messages", tables)
        indexes = [
            row[1]
            for row in migrated_again._conn.execute(
                "PRAGMA index_list(agent_peer_messages)"
            ).fetchall()
            if str(row[1]).startswith("idx_agent_peer_messages_")
        ]
        self.assertEqual(len(indexes), len(set(indexes)))

    def test_agent_peer_messages_migrates_direct_message_columns_idempotently(self):
        legacy_path = Path(self.tmp.name) / "legacy-direct-message-cols.db"
        conn = sqlite3.connect(str(legacy_path))
        conn.execute(
            """
            CREATE TABLE agent_peer_messages (
                id                   TEXT PRIMARY KEY,
                thread_id            TEXT NOT NULL,
                reply_to_id          TEXT NOT NULL DEFAULT '',
                group_name           TEXT NOT NULL DEFAULT '',
                sender_id            TEXT NOT NULL,
                sender_kind          TEXT NOT NULL,
                recipient_id         TEXT NOT NULL,
                recipient_kind       TEXT NOT NULL,
                message              TEXT NOT NULL,
                created_at           REAL NOT NULL,
                ack_required         INTEGER NOT NULL DEFAULT 0,
                context_task_ids     TEXT NOT NULL DEFAULT '[]',
                context_engineer_ids TEXT NOT NULL DEFAULT '[]',
                context_decision_ids TEXT NOT NULL DEFAULT '[]',
                context_summary      TEXT NOT NULL DEFAULT '',
                context_snapshot     TEXT NOT NULL DEFAULT '{}',
                delivery_state       TEXT NOT NULL DEFAULT 'buffered',
                delivery_reason      TEXT NOT NULL DEFAULT '',
                delivered_at         REAL NOT NULL DEFAULT 0,
                archived_at          REAL NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            "INSERT INTO agent_peer_messages "
            "(id, thread_id, sender_id, sender_kind, recipient_id, "
            "recipient_kind, message, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                "legacy-msg",
                "legacy-thread",
                "arch-a",
                "architect",
                "arch-b",
                "architect",
                "legacy body",
                1.0,
            ),
        )
        conn.commit()
        conn.close()

        migrated = TorqueDB(legacy_path)
        migrated.init()
        migrated.close()
        migrated_again = TorqueDB(legacy_path)
        self.addCleanup(migrated_again.close)
        migrated_again.init()

        columns = {
            row[1]
            for row in migrated_again._conn.execute(
                "PRAGMA table_info(agent_peer_messages)"
            ).fetchall()
        }
        self.assertTrue({
            "message_type",
            "blocking",
            "source_task_id",
            "sender_name",
            "recipient_name",
            "read_at",
            "idempotency_key",
        }.issubset(columns))
        loaded = migrated_again.load_agent_peer_message("legacy-msg")
        self.assertEqual(loaded["message_type"], "message")
        self.assertFalse(loaded["blocking"])
        self.assertEqual(loaded["source_task_id"], "")
        self.assertEqual(loaded["sender_name"], "")
        self.assertEqual(loaded["recipient_name"], "")
        self.assertEqual(loaded["read_at"], 0)
        # Pre-existing rows get the additive idempotency_key column as '' (TORQUE:602).
        self.assertEqual(loaded["idempotency_key"], "")
        # A new user→agent direct message persists + round-trips the idempotency_key.
        migrated_again.save_direct_message({
            "id": "msg-idem",
            "thread_id": "user-agent:user:arch-b",
            "sender_id": "user",
            "sender_kind": "user",
            "recipient_id": "arch-b",
            "recipient_kind": "architect",
            "message": "hi",
            "idempotency_key": "client-key-xyz",
        })
        self.assertEqual(
            migrated_again.load_direct_message("msg-idem")["idempotency_key"],
            "client-key-xyz",
        )

    def test_agent_peer_messages_round_trip_query_and_delivery_helpers(self):
        base = {
            "group_name": "g",
            "sender_kind": "architect",
            "recipient_kind": "architect",
            "context_task_ids": ["TORQUE:1", "TORQUE:1:2"],
            "context_engineer_ids": ["eng-1"],
            "context_decision_ids": ["decision-1"],
            "context_summary": "Context summary",
            "context_snapshot": {
                "tasks": [{"id": "TORQUE:1", "title": "Plan"}],
            },
        }
        self.db.save_agent_peer_message({
            **base,
            "id": "msg-1",
            "thread_id": "thread-1",
            "sender_id": "arch-a",
            "recipient_id": "arch-b",
            "message": "first",
            "created_at": 1.0,
            "ack_required": True,
        })
        self.db.save_agent_peer_message({
            **base,
            "id": "msg-2",
            "thread_id": "thread-1",
            "reply_to_id": "msg-1",
            "sender_id": "arch-b",
            "recipient_id": "arch-a",
            "message": "second",
            "created_at": 2.0,
        })
        self.db.save_agent_peer_message({
            **base,
            "id": "msg-3",
            "thread_id": "thread-3",
            "sender_id": "arch-a",
            "recipient_id": "arch-c",
            "message": "third",
            "created_at": 3.0,
        })

        loaded = self.db.load_agent_peer_message("msg-1")
        self.assertTrue(loaded["ack_required"])
        self.assertEqual(loaded["context_task_ids"], ["TORQUE:1", "TORQUE:1:2"])
        self.assertEqual(loaded["context_engineer_ids"], ["eng-1"])
        self.assertEqual(loaded["context_decision_ids"], ["decision-1"])
        self.assertEqual(loaded["context_summary"], "Context summary")
        self.assertEqual(
            loaded["context_snapshot"]["tasks"][0]["title"],
            "Plan",
        )

        self.assertEqual(
            [
                row["id"]
                for row in self.db.load_agent_peer_messages_for_agent("arch-a")
            ],
            ["msg-3", "msg-2", "msg-1"],
        )
        self.assertEqual(
            [
                row["id"]
                for row in self.db.load_peer_messages_for_architect(
                    "arch-a",
                    peer_id="arch-b",
                )
            ],
            ["msg-2", "msg-1"],
        )
        self.assertEqual(
            [
                row["id"]
                for row in self.db.load_agent_peer_messages_for_thread(
                    "thread-1",
                    agent_id="arch-a",
                )
            ],
            ["msg-1", "msg-2"],
        )

        delivered = self.db.mark_peer_message_delivered(
            "msg-2",
            delivered_at=10.0,
        )
        self.assertEqual(delivered["delivery_state"], "delivered")
        self.assertEqual(delivered["delivered_at"], 10.0)
        self.assertEqual(
            [
                row["id"]
                for row in self.db.load_buffered_agent_peer_messages("arch-a")
            ],
            [],
        )

    def test_direct_messages_user_sender_round_trip_and_read_state(self):
        saved = self.db.save_direct_message({
            "id": "direct-1",
            "group_name": "g",
            "sender_id": "user",
            "sender_kind": "user",
            "sender_name": "User",
            "recipient_id": "worker-1",
            "recipient_kind": "worker",
            "recipient_name": "Worker One",
            "message": "Please check status.",
            "message_type": "message",
            "thread_id": "caller-supplied-thread-is-normalized",
            "created_at": 10.0,
            "blocking": False,
            "delivery_state": "buffered",
        })

        self.assertEqual(saved["id"], "direct-1")
        self.assertEqual(
            saved["thread_id"],
            canonical_user_agent_thread_id("worker-1"),
        )
        self.assertEqual(saved["sender_kind"], "user")
        self.assertEqual(saved["recipient_kind"], "worker")
        self.assertEqual(saved["sender_name"], "User")
        self.assertEqual(saved["recipient_name"], "Worker One")
        self.assertEqual(saved["message_type"], "message")
        self.assertFalse(saved["blocking"])
        self.assertEqual(saved["source_task_id"], "")
        self.assertEqual(saved["read_at"], 0)

        self.assertEqual(
            [
                row["id"]
                for row in self.db.load_direct_messages_for_agent("worker-1")
            ],
            ["direct-1"],
        )
        self.assertEqual(
            [
                row["id"]
                for row in self.db.load_buffered_direct_messages("worker-1")
            ],
            ["direct-1"],
        )

        read = self.db.mark_direct_message_read(
            "direct-1",
            read_at=20.0,
            reader_id="worker-1",
        )
        self.assertEqual(read["read_at"], 20.0)
        self.assertEqual(read["delivery_state"], "buffered")
        self.assertEqual(read["delivered_at"], 0)

        delivered = self.db.update_direct_message_delivery(
            "direct-1",
            "delivered",
            delivered_at=30.0,
        )
        self.assertEqual(delivered["read_at"], 20.0)
        self.assertEqual(delivered["delivery_state"], "delivered")
        self.assertEqual(delivered["delivered_at"], 30.0)

    def test_direct_and_peer_buffered_helpers_do_not_cross_return_rows(self):
        self.db.save_agent_peer_message({
            "id": "peer-buffered",
            "thread_id": "peer-thread",
            "group_name": "g",
            "sender_id": "arch-b",
            "sender_kind": "architect",
            "recipient_id": "arch-a",
            "recipient_kind": "architect",
            "message": "peer buffered",
            "created_at": 10.0,
            "delivery_state": "buffered",
        })
        self.db.save_direct_message({
            "id": "direct-buffered",
            "thread_id": "ignored-user-thread",
            "group_name": "g",
            "sender_id": "user",
            "sender_kind": "user",
            "recipient_id": "arch-a",
            "recipient_kind": "architect",
            "message": "direct buffered",
            "created_at": 11.0,
            "delivery_state": "buffered",
        })
        self.db.save_direct_message({
            "id": "direct-ask-display",
            "thread_id": canonical_user_agent_thread_id("worker-1"),
            "group_name": "g",
            "sender_id": "worker-1",
            "sender_kind": "worker",
            "recipient_id": "arch-a",
            "recipient_kind": "architect",
            "message": "display-only ask mirror",
            "message_type": "ask",
            "created_at": 12.0,
            "blocking": True,
            "source_task_id": "ask-task-1",
            "delivery_state": "delivered",
        })

        self.assertEqual(
            [
                row["id"]
                for row in self.db.load_buffered_agent_peer_messages("arch-a")
            ],
            ["peer-buffered"],
        )
        self.assertEqual(
            [
                row["id"]
                for row in self.db.load_buffered_direct_messages("arch-a")
            ],
            ["direct-buffered"],
        )
        self.assertEqual(
            [
                row["id"]
                for row in self.db.load_agent_peer_messages_for_agent("arch-a")
            ],
            ["peer-buffered"],
        )
        self.assertEqual(
            [
                row["id"]
                for row in self.db.load_direct_messages_for_agent("arch-a")
            ],
            ["direct-ask-display", "direct-buffered"],
        )
        self.assertEqual(
            [
                row["id"]
                for row in self.db.load_recent_agent_peer_messages_for_group("g")
            ],
            ["peer-buffered"],
        )
        self.assertEqual(
            [
                row["id"]
                for row in self.db.load_agent_peer_messages_for_thread(
                    "peer-thread",
                    agent_id="arch-a",
                )
            ],
            ["peer-buffered"],
        )
        self.assertEqual(
            [
                row["id"]
                for row in self.db.load_direct_messages_for_thread(
                    canonical_user_agent_thread_id("arch-a"),
                    agent_id="arch-a",
                )
            ],
            ["direct-buffered"],
        )

    def test_agent_peer_chat_loaders_apply_v1_topology_filter(self):
        def save(message_id, *, sender_kind, recipient_kind,
                 thread_id="thread-chat", created_at=1.0, **extra):
            self.db.save_agent_peer_message({
                "id": message_id,
                "thread_id": thread_id,
                "group_name": "g",
                "sender_id": f"{sender_kind}-{message_id}",
                "sender_kind": sender_kind,
                "sender_name": sender_kind.title(),
                "recipient_id": f"{recipient_kind}-{message_id}",
                "recipient_kind": recipient_kind,
                "recipient_name": recipient_kind.title(),
                "message": message_id,
                "created_at": created_at,
                **extra,
            })

        save(
            "chat-arch-eng",
            sender_kind="architect",
            recipient_kind="engineer",
            created_at=10.0,
            ack_required=True,
        )
        save(
            "chat-eng-arch",
            sender_kind="engineer",
            recipient_kind="architect",
            created_at=11.0,
        )
        save(
            "chat-arch-arch",
            sender_kind="architect",
            recipient_kind="architect",
            thread_id="thread-architects",
            created_at=12.0,
        )
        save(
            "excluded-arch-worker",
            sender_kind="architect",
            recipient_kind="worker",
            created_at=13.0,
        )
        save(
            "excluded-worker-arch",
            sender_kind="worker",
            recipient_kind="architect",
            created_at=14.0,
        )
        save(
            "excluded-eng-eng",
            sender_kind="engineer",
            recipient_kind="engineer",
            created_at=15.0,
        )
        save(
            "excluded-ask",
            sender_kind="architect",
            recipient_kind="engineer",
            created_at=16.0,
            message_type="ask",
        )
        save(
            "excluded-blocking",
            sender_kind="engineer",
            recipient_kind="architect",
            created_at=17.0,
            blocking=True,
        )
        self.db.save_direct_message({
            "id": "excluded-user-direct",
            "group_name": "g",
            "sender_id": "user",
            "sender_kind": "user",
            "recipient_id": "arch-1",
            "recipient_kind": "architect",
            "message": "user note",
            "created_at": 18.0,
        })

        recent = self.db.load_recent_agent_peer_chat_messages()

        self.assertEqual(
            [row["id"] for row in recent],
            ["chat-arch-arch", "chat-eng-arch", "chat-arch-eng"],
        )
        self.assertTrue(recent[-1]["ack_required"])
        self.assertEqual(
            [
                row["id"]
                for row in self.db.load_agent_peer_chat_messages_for_thread(
                    "thread-chat"
                )
            ],
            ["chat-arch-eng", "chat-eng-arch"],
        )
        self.db.save_agent_peer_message({
            "id": "chat-pair-reply",
            "thread_id": "thread-chat-reply",
            "group_name": "g",
            "sender_id": "engineer-chat-arch-eng",
            "sender_kind": "engineer",
            "sender_name": "Engineer",
            "recipient_id": "architect-chat-arch-eng",
            "recipient_kind": "architect",
            "recipient_name": "Architect",
            "message": "same pair, different raw thread",
            "created_at": 19.0,
        })
        self.assertEqual(
            [
                row["id"]
                for row in self.db.load_agent_peer_chat_messages_for_pair(
                    "engineer-chat-arch-eng",
                    "architect-chat-arch-eng",
                )
            ],
            ["chat-arch-eng", "chat-pair-reply"],
        )
        self.assertEqual(
            [
                row["id"]
                for row in self.db.load_recent_agent_peer_chat_messages(
                    group_name="missing"
                )
            ],
            [],
        )

    def test_agent_peer_messages_deterministic_ids_are_idempotent(self):
        row = {
            "id": "msg-deterministic",
            "thread_id": "msg-deterministic",
            "group_name": "g",
            "sender_id": "arch-a",
            "recipient_id": "arch-b",
            "message": "first write wins",
            "created_at": 1.0,
        }
        first = self.db.save_peer_message(row)
        second = self.db.save_peer_message({
            **row,
            "message": "retry payload should not overwrite",
            "created_at": 2.0,
        })

        self.assertEqual(first["message"], "first write wins")
        self.assertEqual(second["message"], "first write wins")
        self.assertEqual(
            self.db._conn.execute(
                "SELECT COUNT(*) FROM agent_peer_messages WHERE id=?",
                ("msg-deterministic",),
            ).fetchone()[0],
            1,
        )

    def _seed_stage1a_db(
        self,
        filename: str,
        *,
        agents=None,
        tasks=None,
        group_settings=None,
    ):
        path = Path(self.tmp.name) / filename
        seeded = TorqueDB(path)
        seeded.init()
        if agents:
            for agent in agents:
                seeded.save_agent(agent)
        if tasks:
            for task in tasks:
                seeded.save_board_task(task)
        if group_settings:
            for group_name, settings in group_settings.items():
                seeded.save_group_settings(group_name, settings)
        seeded.close()

        conn = sqlite3.connect(str(path))
        conn.execute(
            "ALTER TABLE agents ADD COLUMN template TEXT NOT NULL DEFAULT ''"
        )
        conn.execute(
            "ALTER TABLE agents ADD COLUMN created_by_engineer_id TEXT NOT NULL DEFAULT ''"
        )
        conn.execute(
            "ALTER TABLE board_tasks ADD COLUMN engineer_owner_id TEXT NOT NULL DEFAULT ''"
        )
        for agent in agents or []:
            item = dict(agent) if isinstance(agent, dict) else agent.__dict__
            conn.execute(
                "UPDATE agents SET template=?, created_by_engineer_id=? WHERE id=?",
                (
                    str(item.get("template", "") or item.get("role", "") or ""),
                    str(
                        item.get("created_by_engineer_id", "")
                        or item.get("owner_engineer_id", "")
                        or ""
                    ),
                    str(item.get("id", "") or ""),
                ),
            )
        for task in tasks or []:
            item = dict(task) if isinstance(task, dict) else task.__dict__
            conn.execute(
                "UPDATE board_tasks SET engineer_owner_id=? WHERE id=?",
                (
                    str(
                        item.get("engineer_owner_id", "")
                        or item.get("assigned_engineer_id", "")
                        or ""
                    ),
                    str(item.get("id", "") or ""),
                ),
            )
        conn.execute(
            "UPDATE meta SET value='1' WHERE key='schema_kinds_migration_version'"
        )
        conn.execute(
            "UPDATE meta SET value='2026-04-19T00:00:00+00:00' "
            "WHERE key='schema_kinds_migration_migrated_at'"
        )
        conn.execute(
            "UPDATE agents SET kind='', role='', owner_engineer_id='', "
            "hired_by_architect_id='', persistent=0"
        )
        conn.execute(
            "UPDATE board_tasks SET assigned_engineer_id='', "
            "created_by_architect_id='', suggested_action=''"
        )
        conn.commit()
        conn.close()
        return path

    def _add_legacy_board_task_owner_column(self, conn=None):
        close_conn = False
        if conn is None:
            conn = sqlite3.connect(str(self.db.db_path))
            close_conn = True
        conn.execute(
            "ALTER TABLE board_tasks ADD COLUMN engineer_owner_id TEXT NOT NULL DEFAULT ''"
        )
        conn.commit()
        if close_conn:
            conn.close()

    def test_engineer_specializations_round_trip(self):
        cell = AgentCell(
            id="engineer-1",
            name="Engineer",
            group="g",
            slug="engineer",
            cell_type="agent",
            kind="engineer",
            engineer_specializations=["ui-ux", "security-focus"],
        )
        self.db.save_agent(cell)
        self.db.save_groups({"g": [cell.id]}, {"g": "g"})
        self.db.save_group_members("g", [cell.id])

        loaded = self.db.load_all()
        self.assertEqual(
            loaded["agents"]["engineer-1"]["engineer_specializations"],
            ["ui-ux", "security-focus"],
        )

    def test_engineer_specializations_migration_is_idempotent(self):
        # Column already exists after init(); calling ensure again is a no-op.
        self.db._ensure_agent_engineer_specializations_column()
        columns = {
            row[1]
            for row in self.db._conn.execute("PRAGMA table_info(agents)")
        }
        self.assertIn("engineer_specializations", columns)

    def test_agent_tombstone_fields_round_trip_and_migration_is_idempotent(self):
        cell = AgentCell(
            id="engineer-1",
            name="Engineer",
            group="g",
            slug="engineer",
            cell_type="agent",
            kind="engineer",
            deleted_at=123.5,
            permanent_delete_after=456.5,
        )
        self.db.save_agent(cell)

        loaded = self.db.load_all()

        self.assertEqual(loaded["agents"]["engineer-1"]["deleted_at"], 123.5)
        self.assertEqual(
            loaded["agents"]["engineer-1"]["permanent_delete_after"],
            456.5,
        )
        self.db._ensure_agent_tombstone_columns()
        columns = {
            row[1]
            for row in self.db._conn.execute("PRAGMA table_info(agents)")
        }
        self.assertIn("deleted_at", columns)
        self.assertIn("permanent_delete_after", columns)

    def test_default_engineer_specializations_round_trip(self):
        self.db.save_groups({"g": []}, {"g": "g"})
        self.db.save_group_settings(
            "g",
            GroupSettings(
                default_engineer_specializations=["ui-frontend", "react"],
            ),
        )

        loaded = self.db.load_all()
        self.assertEqual(
            loaded["group_settings"]["g"]["default_engineer_specializations"],
            ["ui-frontend", "react"],
        )

    def test_default_engineer_specializations_defaults_to_empty(self):
        self.db.save_groups({"g": []}, {"g": "g"})
        self.db.save_group_settings("g", GroupSettings())

        loaded = self.db.load_all()
        self.assertEqual(
            loaded["group_settings"]["g"]["default_engineer_specializations"],
            [],
        )

    def test_engineer_merge_mode_round_trips_and_defaults_to_pr(self):
        self.db.save_groups({"g": [], "h": []}, {"g": "g", "h": "h"})
        self.db.save_group_settings(
            "g",
            GroupSettings(engineer_merge_mode="engineer-choice"),
        )
        self.db.save_group_settings("h", GroupSettings())

        loaded = self.db.load_all()
        self.assertEqual(
            loaded["group_settings"]["g"]["engineer_merge_mode"],
            "engineer-choice",
        )
        self.assertEqual(
            loaded["group_settings"]["h"]["engineer_merge_mode"],
            "pr",
        )

    def test_guidance_hint_cadence_round_trips_and_defaults_to_four(self):
        self.db.save_groups({"g": [], "h": []}, {"g": "g", "h": "h"})
        self.db.save_group_settings(
            "g",
            GroupSettings(guidance_hint_cadence=0),
        )
        self.db.save_group_settings("h", GroupSettings())

        loaded = self.db.load_all()
        self.assertEqual(
            loaded["group_settings"]["g"]["guidance_hint_cadence"],
            0,
        )
        self.assertEqual(
            loaded["group_settings"]["h"]["guidance_hint_cadence"],
            4,
        )

    def test_default_engineer_specializations_migration_adds_column(self):
        # Simulate an older DB by dropping the column then re-running init.
        legacy_path = Path(self.tmp.name) / "legacy-default-specs.db"
        legacy = TorqueDB(legacy_path)
        legacy.init()
        legacy._conn.execute(
            "CREATE TABLE legacy_gs AS SELECT * FROM group_settings"
        )
        # Drop and recreate without the column to mimic pre-migration state.
        legacy._conn.execute("DROP TABLE group_settings")
        legacy._conn.execute(
            "CREATE TABLE group_settings ("
            "group_name TEXT PRIMARY KEY, "
            "engineer_agent_id TEXT NOT NULL DEFAULT '')"
        )
        legacy._conn.commit()
        legacy.close()

        # Re-open and run init() again — migration should add the column.
        migrated = TorqueDB(legacy_path)
        self.addCleanup(migrated.close)
        migrated.init()
        columns = {
            row[1]
            for row in migrated._conn.execute(
                "PRAGMA table_info(group_settings)"
            )
        }
        self.assertIn("default_engineer_specializations", columns)

    def test_worktree_symlink_gitignored_paths_migration_adds_column(self):
        legacy_path = Path(self.tmp.name) / "legacy-gitignored-symlinks.db"
        legacy = TorqueDB(legacy_path)
        legacy.init()
        legacy._conn.execute("DROP TABLE group_settings")
        legacy._conn.execute(
            "CREATE TABLE group_settings ("
            "group_name TEXT PRIMARY KEY, "
            "engineer_agent_id TEXT NOT NULL DEFAULT '')"
        )
        legacy._conn.execute(
            "INSERT INTO group_settings (group_name, engineer_agent_id) "
            "VALUES ('g', 'eng-1')"
        )
        legacy._conn.commit()
        legacy.close()

        migrated = TorqueDB(legacy_path)
        self.addCleanup(migrated.close)
        migrated.init()
        columns = {
            row[1]
            for row in migrated._conn.execute(
                "PRAGMA table_info(group_settings)"
            )
        }
        self.assertIn("worktree_symlink_gitignored_paths", columns)
        row = migrated._conn.execute(
            "SELECT worktree_symlink_gitignored_paths FROM group_settings "
            "WHERE group_name='g'"
        ).fetchone()
        self.assertEqual(row[0], 0)

    def test_engineer_merge_mode_migration_adds_column(self):
        legacy_path = Path(self.tmp.name) / "legacy-engineer-merge-mode.db"
        legacy = TorqueDB(legacy_path)
        legacy.init()
        legacy._conn.execute("DROP TABLE group_settings")
        legacy._conn.execute(
            "CREATE TABLE group_settings ("
            "group_name TEXT PRIMARY KEY, "
            "engineer_agent_id TEXT NOT NULL DEFAULT '')"
        )
        legacy._conn.commit()
        legacy.close()

        migrated = TorqueDB(legacy_path)
        self.addCleanup(migrated.close)
        migrated.init()
        columns = {
            row[1]
            for row in migrated._conn.execute(
                "PRAGMA table_info(group_settings)"
            )
        }
        self.assertIn("engineer_merge_mode", columns)

    def test_guidance_hint_cadence_migration_adds_column_and_default(self):
        legacy_path = Path(self.tmp.name) / "legacy-guidance-cadence.db"
        legacy = TorqueDB(legacy_path)
        legacy.init()
        legacy._conn.execute("DROP TABLE group_settings")
        legacy._conn.execute(
            "CREATE TABLE group_settings ("
            "group_name TEXT PRIMARY KEY, "
            "engineer_agent_id TEXT NOT NULL DEFAULT '')"
        )
        legacy._conn.execute(
            "INSERT INTO group_settings (group_name, engineer_agent_id) "
            "VALUES ('g', 'eng-1')"
        )
        legacy._conn.commit()
        legacy.close()

        migrated = TorqueDB(legacy_path)
        self.addCleanup(migrated.close)
        migrated.init()
        columns = {
            row[1]
            for row in migrated._conn.execute(
                "PRAGMA table_info(group_settings)"
            )
        }
        self.assertIn("guidance_hint_cadence", columns)
        row = migrated._conn.execute(
            "SELECT guidance_hint_cadence FROM group_settings "
            "WHERE group_name='g'"
        ).fetchone()
        self.assertEqual(row[0], 4)

    def test_board_sync_schema_migration_adds_columns(self):
        legacy_path = Path(self.tmp.name) / "legacy-board-sync.db"
        legacy = TorqueDB(legacy_path)
        legacy.init()
        legacy.save_board_task(
            BoardTask(id="task-1", task="Legacy sync task", group="g")
        )
        legacy._conn.execute("DROP TABLE group_settings")
        legacy._conn.execute(
            "CREATE TABLE group_settings ("
            "group_name TEXT PRIMARY KEY, "
            "engineer_agent_id TEXT NOT NULL DEFAULT '')"
        )
        task_columns = [
            row[1]
            for row in legacy._conn.execute("PRAGMA table_info(board_tasks)")
            if row[1] != "board_sync"
        ]
        quoted_columns = ", ".join(f'"{col}"' for col in task_columns)
        legacy._conn.execute(
            f"CREATE TABLE board_tasks_without_board_sync "
            f"AS SELECT {quoted_columns} FROM board_tasks"
        )
        legacy._conn.execute("DROP TABLE board_tasks")
        legacy._conn.execute(
            "ALTER TABLE board_tasks_without_board_sync RENAME TO board_tasks"
        )
        legacy._conn.commit()
        legacy.close()

        migrated = TorqueDB(legacy_path)
        self.addCleanup(migrated.close)
        migrated.init()

        task_columns = {
            row[1]
            for row in migrated._conn.execute("PRAGMA table_info(board_tasks)")
        }
        settings_columns = {
            row[1]
            for row in migrated._conn.execute("PRAGMA table_info(group_settings)")
        }
        self.assertIn("board_sync", task_columns)
        self.assertTrue({
            "board_sync_provider",
            "board_sync_enabled",
            "board_sync_github",
        }.issubset(settings_columns))
        row = migrated._conn.execute(
            "SELECT board_sync FROM board_tasks LIMIT 1"
        ).fetchone()
        self.assertEqual(json.loads(row[0]), {})

    def test_load_all_roundtrips_json_and_boolean_fields(self):
        cell = AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            slug="worker",
            cell_type="agent",
            terminal_backend="pty",
            session_id="session-1",
            command="codex",
            directory="/repo",
            worktree_path="/repo/.torque/worktrees/agent-1",
            worktree_branch="torque/worker",
            worktree_repo_root="/repo",
            worktree_base_branch="main",
            worktree_auto_checkpoint=True,
            checkpoint_on_progress=True,
            worktree_merge_squash=False,
            agent_type="codex",
            agent_session_id="agent-session",
            last_progress_at=111.0,
            last_heartbeat_at=222.0,
            session_resume=False,
            idle_timeout=9,
            tasks_dispatched=3,
            created_by_engineer_id="engineer-1",
            kind="worker",
            role="researcher",
            owner_engineer_id="engineer-1",
            hired_by_architect_id="architect-1",
            persistent=True,
            queue_empty_emitted=False,
        )
        self.db.save_agent(cell)
        self.db.save_groups({"g": [cell.id]}, {"g": "g"})
        self.db.save_group_members("g", [cell.id])
        self.db.save_group_settings(
            "g",
            GroupSettings(
                default_terminal_backend="pty",
                notifications=True,
                agent_model="gpt-5",
                agent_reasoning_effort="high",
                board_default_labels=["ready"],
                worktree_symlinks=["shared/config.yml"],
                worktree_symlink_gitignored_paths=True,
                agent_session_resume=False,
                worktree_merge_squash=False,
                worktree_merge_cleanup="close_remove",
                worktree_merge_preserve_diff=True,
                engineer_merge_mode="direct",
                board_sync_provider="github",
                board_sync_enabled=True,
                board_sync_github={
                    "github_repo": "owner/repo",
                    "github_project_owner": "owner",
                    "github_project_number": 5,
                    "github_project_status_field": "Status",
                    "github_lane_status_map": {"In Progress": "Doing"},
                    "github_sync_default": "linked",
                    "github_close_issues_via_pr": True,
                    "github_create_missing_labels": False,
                    "github_assignee_map": {"engineer-1": "octocat"},
                },
            ),
        )
        self.db.save_board_task(
            BoardTask(
                id="task-1",
                task="Ship feature",
                description="Exercise persistence",
                slug="ship-feature",
                group="g",
                action_name="feature/implement",
                action_vars={"TEST_COMMAND": "python3 -m unittest"},
                agent_template="researcher",
                lane="In Progress",
                position=2,
                agent_id=cell.id,
                assigned_engineer_id="engineer-1",
                created_by_architect_id="architect-1",
                suggested_action="feature/review",
                reply_agent_id="agent-2",
                labels=["torque:blocked", "keep"],
                created_at="2026-04-06T00:00:00+00:00",
                updated_at="2026-04-06T01:00:00+00:00",
                lane_entered_at="2026-04-06T00:30:00+00:00",
                parent_task_id="parent-1",
                pipeline_depth=2,
                pipeline_root_id="root-1",
                provider="github",
                external_id="owner/repo#123",
                external_url="https://github.com/owner/repo/issues/123",
                board_sync={
                    "version": 1,
                    "provider": "github",
                    "enabled": True,
                    "github": {
                        "issue_repo": "owner/repo",
                        "issue_number": 123,
                        "issue_node_id": "I_kw",
                        "issue_url": "https://github.com/owner/repo/issues/123",
                        "project_owner": "owner",
                        "project_number": 5,
                        "project_id": "PVT_kw",
                        "project_item_id": "PVTI_kw",
                        "status_field_id": "PVTSSF_kw",
                        "status_option_id": "option-id",
                    },
                    "last_push_at": "2026-05-20T15:00:00+00:00",
                    "last_pull_at": "",
                    "last_seen_provider_updated_at": "2026-05-20T14:58:00Z",
                    "last_synced_hash": "sha256",
                    "sync_state": "idle",
                    "last_error": "",
                },
                status="Reviewing",
                scheduled_at="2026-04-07T10:00:00+00:00",
                messages=[{"action": "progress", "message": "Working"}],
                depends_on=["dep-1"],
                attachments=[{"path": "/tmp/mock.png", "filename": "mock.png"}],
                health_state="idle-risk",
                health_since="2026-04-06T01:05:00+00:00",
                health_details={"reasons": ["progress_silence_warning"]},
                verification_mode="deploy",
                verification_state="pending",
                verification_notes="Need manual smoke after deploy",
                verification_updated_at="2026-04-06T01:10:00+00:00",
                verification_updated_by="worker",
                verification_summary={
                    "tests_run": "python3 -m unittest",
                    "manual_smoke_done": False,
                    "deploy_needed": True,
                    "human_validation_pending": "Confirm dashboard loads",
                },
                worktree_boundary={
                    "version": "1",
                    "branch": "torque/worker",
                    "repo_root": "/repo",
                    "base_branch": "main",
                    "commit_sha": "abc123",
                    "kind": "checkpoint",
                    "status": "open",
                },
                resume_after_boundary_task_id="task-0",
                artifacts=[{
                    "id": "artifact-1",
                    "type": "test_report",
                    "title": "pytest",
                    "path": "/tmp/pytest.txt",
                    "summary": "2 failed, 18 passed",
                    "prompt": {"mode": "summary"},
                    "provenance": {"source": "agent", "agent_id": "agent-1"},
                    "storage": {"kind": "path", "path": "/tmp/pytest.txt"},
                    "lifecycle": {"owner": "task", "cleanup": "delete_with_task"},
                }],
            )
        )
        self.db.save_schedule(
            Schedule(
                id="sched-1",
                name="Morning sync",
                slug="morning-sync",
                task_template="Standup {date}",
                description="Recurring schedule",
                group="g",
                action_name="feature/review",
                action_vars={"CHECK": "docs"},
                labels=["ops"],
                cron_expr="0 8 * * 1-5",
                timezone="America/New_York",
                enabled=False,
                next_run_at="2026-04-07T12:00:00+00:00",
            )
        )
        self.db.save_auto_dispatch_queue("g", [
            {
                "task_id": "task-1",
                "agent_group": "release",
                "max_concurrent": 2,
                "target_agent_id": "agent-1",
                "engineer_owner_id": "engineer-1",
                "provider": "codex",
                "enqueued_at": "2026-04-07T09:00:00+00:00",
            }
        ])
        self.db.save_board_lanes(["Backlog", "To Do", "In Progress", "Done"])

        loaded = self.db.load_all()

        self.assertEqual(loaded["groups"], {"g": ["agent-1"]})
        self.assertEqual(loaded["group_slugs"], {"g": "g"})
        self.assertEqual(loaded["agents"]["agent-1"]["terminal_backend"], "pty")
        self.assertFalse(loaded["agents"]["agent-1"]["session_resume"])
        self.assertTrue(loaded["agents"]["agent-1"]["worktree_auto_checkpoint"])
        self.assertEqual(
            loaded["agents"]["agent-1"]["created_by_engineer_id"],
            "engineer-1",
        )
        self.assertEqual(loaded["agents"]["agent-1"]["last_progress_at"], 111.0)
        self.assertEqual(loaded["agents"]["agent-1"]["last_heartbeat_at"], 222.0)
        self.assertEqual(loaded["agents"]["agent-1"]["last_activity_at"], 222.0)
        self.assertEqual(loaded["agents"]["agent-1"]["kind"], "worker")
        self.assertEqual(loaded["agents"]["agent-1"]["role"], "researcher")
        self.assertEqual(
            loaded["agents"]["agent-1"]["owner_engineer_id"],
            "engineer-1",
        )
        self.assertEqual(
            loaded["agents"]["agent-1"]["hired_by_architect_id"],
            "architect-1",
        )
        self.assertTrue(loaded["agents"]["agent-1"]["persistent"])
        self.assertFalse(loaded["agents"]["agent-1"]["queue_empty_emitted"])
        self.assertEqual(
            loaded["group_settings"]["g"]["default_terminal_backend"],
            "pty",
        )
        self.assertEqual(
            loaded["group_settings"]["g"]["board_default_labels"],
            ["ready"],
        )
        self.assertEqual(
            loaded["group_settings"]["g"]["worktree_symlinks"],
            ["shared/config.yml"],
        )
        self.assertTrue(
            loaded["group_settings"]["g"]["worktree_symlink_gitignored_paths"],
        )
        self.assertFalse(loaded["group_settings"]["g"]["worktree_merge_squash"])
        self.assertEqual(
            loaded["group_settings"]["g"]["agent_model"],
            "gpt-5",
        )
        self.assertEqual(
            loaded["group_settings"]["g"]["agent_reasoning_effort"],
            "high",
        )
        self.assertEqual(
            loaded["group_settings"]["g"]["worktree_merge_cleanup"],
            "close_remove",
        )
        self.assertTrue(
            loaded["group_settings"]["g"]["worktree_merge_preserve_diff"],
        )
        self.assertEqual(
            loaded["group_settings"]["g"]["engineer_merge_mode"],
            "direct",
        )
        self.assertEqual(
            loaded["group_settings"]["g"]["board_sync_provider"],
            "github",
        )
        self.assertTrue(loaded["group_settings"]["g"]["board_sync_enabled"])
        self.assertEqual(
            loaded["group_settings"]["g"]["board_sync_github"]["github_repo"],
            "owner/repo",
        )
        self.assertEqual(
            loaded["group_settings"]["g"]["board_sync_github"][
                "github_lane_status_map"
            ],
            {"In Progress": "Doing"},
        )
        self.assertEqual(
            loaded["board_tasks"]["task-1"]["action_vars"],
            {"TEST_COMMAND": "python3 -m unittest"},
        )
        self.assertEqual(
            loaded["board_tasks"]["task-1"]["depends_on"],
            ["dep-1"],
        )
        self.assertEqual(
            loaded["board_tasks"]["task-1"]["reply_agent_id"],
            "agent-2",
        )
        self.assertEqual(
            loaded["board_tasks"]["task-1"]["assigned_engineer_id"],
            "engineer-1",
        )
        self.assertEqual(
            loaded["board_tasks"]["task-1"]["created_by_architect_id"],
            "architect-1",
        )
        self.assertEqual(
            loaded["board_tasks"]["task-1"]["suggested_action"],
            "feature/review",
        )
        self.assertEqual(
            loaded["board_tasks"]["task-1"]["board_sync"]["provider"],
            "github",
        )
        self.assertEqual(
            loaded["board_tasks"]["task-1"]["board_sync"]["github"][
                "issue_number"
            ],
            123,
        )
        self.assertEqual(
            loaded["board_tasks"]["task-1"]["board_sync"]["sync_state"],
            "idle",
        )
        self.assertEqual(
            loaded["board_tasks"]["task-1"]["lane_entered_at"],
            "2026-04-06T00:30:00+00:00",
        )
        self.assertEqual(
            loaded["board_tasks"]["task-1"]["attachments"][0]["filename"],
            "mock.png",
        )
        self.assertEqual(
            loaded["board_tasks"]["task-1"]["health_state"],
            "idle-risk",
        )
        self.assertEqual(
            loaded["board_tasks"]["task-1"]["health_details"]["reasons"],
            ["progress_silence_warning"],
        )
        self.assertEqual(
            loaded["board_tasks"]["task-1"]["artifacts"][0]["type"],
            "test_report",
        )
        self.assertEqual(
            loaded["board_tasks"]["task-1"]["verification_mode"],
            "deploy",
        )
        self.assertEqual(
            loaded["board_tasks"]["task-1"]["verification_state"],
            "pending",
        )
        self.assertEqual(
            loaded["board_tasks"]["task-1"]["verification_summary"]["tests_run"],
            "python3 -m unittest",
        )
        self.assertEqual(
            loaded["board_tasks"]["task-1"]["worktree_boundary"]["status"],
            "open",
        )
        self.assertEqual(
            loaded["board_tasks"]["task-1"]["resume_after_boundary_task_id"],
            "task-0",
        )
        self.assertFalse(loaded["schedules"]["sched-1"]["enabled"])
        self.assertEqual(loaded["schedules"]["sched-1"]["labels"], ["ops"])
        self.assertEqual(
            loaded["auto_dispatch_queues"]["g"][0]["task_id"],
            "task-1",
        )
        self.assertEqual(
            loaded["auto_dispatch_queues"]["g"][0]["agent_group"],
            "release",
        )
        self.assertEqual(
            loaded["auto_dispatch_queues"]["g"][0]["target_agent_id"],
            "agent-1",
        )
        self.assertEqual(
            loaded["auto_dispatch_queues"]["g"][0]["engineer_owner_id"],
            "engineer-1",
        )
        self.assertEqual(
            loaded["auto_dispatch_queues"]["g"][0]["provider"],
            "codex",
        )

    def test_save_all_ignores_unknown_group_settings_fields(self):
        self.db.save_all(
            {
                "groups": {"g": []},
                "group_slugs": {"g": "g"},
                "group_settings": {
                    "g": {
                        "notifications": True,
                        "retired_toggle": True,
                    },
                },
            }
        )

        loaded = self.db.load_all()

        self.assertTrue(loaded["group_settings"]["g"]["notifications"])
        self.assertNotIn("retired_toggle", loaded["group_settings"]["g"])

    def test_agent_activity_timestamp_migration_is_idempotent(self):
        cell = AgentCell(
            id="agent-activity",
            name="Worker",
            group="g",
            slug="worker",
        )
        self.db.save_agent(cell)
        self.db._conn.execute(
            "UPDATE agents SET last_activity_at=123, "
            "last_progress_at=0, last_heartbeat_at=0 WHERE id=?",
            (cell.id,),
        )
        self.db._conn.execute(
            "DELETE FROM meta WHERE key=?",
            ("schema_agent_activity_timestamps_version",),
        )
        self.db._conn.commit()

        self.db._migrate_agent_activity_timestamps_if_needed()
        first = self.db._conn.execute(
            "SELECT last_progress_at, last_heartbeat_at, last_activity_at "
            "FROM agents WHERE id=?",
            (cell.id,),
        ).fetchone()
        first_meta = self.db._conn.execute(
            "SELECT value FROM meta WHERE key=?",
            ("schema_agent_activity_timestamps_version",),
        ).fetchone()

        self.db._migrate_agent_activity_timestamps_if_needed()
        second = self.db._conn.execute(
            "SELECT last_progress_at, last_heartbeat_at, last_activity_at "
            "FROM agents WHERE id=?",
            (cell.id,),
        ).fetchone()
        second_meta = self.db._conn.execute(
            "SELECT value FROM meta WHERE key=?",
            ("schema_agent_activity_timestamps_version",),
        ).fetchone()

        self.assertEqual(first, (123.0, 123.0, 123.0))
        self.assertEqual(second, first)
        self.assertEqual(first_meta, ("1",))
        self.assertEqual(second_meta, first_meta)

    def test_save_agent_and_board_task_update_preserve_kinds_fields(self):
        cell = AgentCell(
            id="agent-kinds",
            name="Kinds Worker",
            group="g",
            slug="kinds-worker",
            kind="worker",
            role="researcher",
            owner_engineer_id="engineer-1",
            hired_by_architect_id="architect-1",
            persistent=False,
        )
        task = BoardTask(
            id="task-kinds",
            task="Kinds task",
            group="g",
            slug="kinds-task",
            agent_id=cell.id,
            assigned_engineer_id="engineer-1",
            created_by_architect_id="architect-1",
            suggested_action="feature/review",
        )

        self.db.save_agent(cell)
        self.db.save_board_task(task)

        cell.kind = "architect"
        cell.role = "lead"
        cell.owner_engineer_id = "engineer-2"
        cell.hired_by_architect_id = "architect-2"
        cell.persistent = True
        task.assigned_engineer_id = "engineer-2"
        task.created_by_architect_id = "architect-2"
        task.suggested_action = "feature/fix-review"

        self.db.save_agent(cell)
        self.db.save_board_task(task)

        row = self.db._conn.execute(
            "SELECT kind, role, owner_engineer_id, hired_by_architect_id, "
            "persistent FROM agents WHERE id=?",
            (cell.id,),
        ).fetchone()
        self.assertEqual(
            row,
            ("architect", "lead", "engineer-2", "architect-2", 1),
        )

        task_row = self.db._conn.execute(
            "SELECT assigned_engineer_id, created_by_architect_id, "
            "suggested_action FROM board_tasks WHERE id=?",
            (task.id,),
        ).fetchone()
        self.assertEqual(
            task_row,
            ("engineer-2", "architect-2", "feature/fix-review"),
        )

        loaded = self.db.load_all()
        self.assertEqual(loaded["agents"][cell.id]["kind"], "architect")
        self.assertEqual(loaded["agents"][cell.id]["role"], "lead")
        self.assertEqual(
            loaded["agents"][cell.id]["owner_engineer_id"],
            "engineer-2",
        )
        self.assertEqual(
            loaded["agents"][cell.id]["hired_by_architect_id"],
            "architect-2",
        )
        self.assertTrue(loaded["agents"][cell.id]["persistent"])
        self.assertEqual(
            loaded["board_tasks"][task.id]["assigned_engineer_id"],
            "engineer-2",
        )
        self.assertEqual(
            loaded["board_tasks"][task.id]["created_by_architect_id"],
            "architect-2",
        )
        self.assertEqual(
            loaded["board_tasks"][task.id]["suggested_action"],
            "feature/fix-review",
        )

    def test_save_agent_maps_legacy_inputs_into_kinds_columns(self):
        self.db.save_agent(
            AgentCell(
                id="agent-dual-write",
                name="Dual Writer",
                group="g",
                template="researcher",
                created_by_engineer_id="engineer-1",
            )
        )
        self.assertEqual(
            self.db._conn.execute(
                "SELECT role, owner_engineer_id "
                "FROM agents WHERE id='agent-dual-write'"
            ).fetchone(),
            ("researcher", "engineer-1"),
        )

        self.db.save_agent(
            AgentCell(
                id="agent-dual-write",
                name="Dual Writer",
                group="g",
                role="planner",
                owner_engineer_id="engineer-2",
            )
        )
        self.assertEqual(
            self.db._conn.execute(
                "SELECT role, owner_engineer_id "
                "FROM agents WHERE id='agent-dual-write'"
            ).fetchone(),
            ("planner", "engineer-2"),
        )

    def test_save_agent_prefers_kinds_columns_over_legacy_inputs(self):
        self.db.save_agent(
            AgentCell(
                id="agent-dual-conflict",
                name="Conflict",
                group="g",
                template="researcher",
                role="planner",
                created_by_engineer_id="engineer-1",
                owner_engineer_id="engineer-2",
            )
        )
        self.assertEqual(
            self.db._conn.execute(
                "SELECT role, owner_engineer_id "
                "FROM agents WHERE id='agent-dual-conflict'"
            ).fetchone(),
            ("planner", "engineer-2"),
        )

    def test_save_board_task_maps_legacy_owner_into_new_column(self):
        self.db.save_board_task(
            {
                "id": "task-dual-assigned",
                "task": "Assigned-first",
                "group": "g",
                "assigned_engineer_id": "engineer-1",
            }
        )
        self.assertEqual(
            self.db._conn.execute(
                "SELECT assigned_engineer_id "
                "FROM board_tasks WHERE id='task-dual-assigned'"
            ).fetchone(),
            ("engineer-1",),
        )

        self.db.save_board_task(
            {
                "id": "task-dual-legacy",
                "task": "Legacy-first",
                "group": "g",
                "engineer_owner_id": "engineer-2",
            }
        )
        self.assertEqual(
            self.db._conn.execute(
                "SELECT assigned_engineer_id "
                "FROM board_tasks WHERE id='task-dual-legacy'"
            ).fetchone(),
            ("engineer-2",),
        )

    def test_save_board_task_prefers_assigned_engineer_over_legacy_owner(self):
        self.db.save_board_task(
            {
                "id": "task-dual-conflict",
                "task": "Conflict",
                "group": "g",
                "assigned_engineer_id": "engineer-new",
                "engineer_owner_id": "engineer-legacy",
            }
        )
        self.assertEqual(
            self.db._conn.execute(
                "SELECT assigned_engineer_id "
                "FROM board_tasks WHERE id='task-dual-conflict'"
            ).fetchone(),
            ("engineer-new",),
        )

    def test_save_board_task_uses_legacy_owner_when_new_column_is_empty(self):
        self.db.save_board_task(
            {
                "id": "task-no-legacy-owner",
                "task": "No legacy owner column",
                "group": "g",
                "engineer_owner_id": "engineer-legacy",
            }
        )

        self.assertEqual(
            self.db._conn.execute(
                "SELECT assigned_engineer_id FROM board_tasks "
                "WHERE id='task-no-legacy-owner'"
            ).fetchone(),
            ("engineer-legacy",),
        )

    def test_load_all_restores_board_filters_by_group(self):
        filters = {
            "alpha": {
                "search_query": "deploy",
                "filter_labels": ["bug"],
                "filter_actions": ["triage"],
                "filter_agents": ["agent-1"],
                "pre_filter_lane": "Backlog",
            }
        }
        self.db.save_ui_state("board_filters_by_group", json.dumps(filters))

        loaded = self.db.load_all()

        self.assertEqual(loaded["board_filters_by_group"], filters)

    def test_load_all_restores_board_lane_ui_state_by_group(self):
        selected = {"alpha": "In Progress", "beta": "Done"}
        hidden = {"alpha": {"To Do": True}, "beta": {}}
        self.db.save_ui_state(
            "board_selected_lanes_by_group",
            json.dumps(selected),
        )
        self.db.save_ui_state(
            "board_hidden_wide_lanes_by_group",
            json.dumps(hidden),
        )

        loaded = self.db.load_all()

        self.assertEqual(loaded["board_selected_lanes_by_group"], selected)
        self.assertEqual(loaded["board_hidden_wide_lanes_by_group"], hidden)

    def test_save_all_preserves_kinds_fields(self):
        self.db.save_all(
            {
                "agents": {
                    "agent-1": {
                        "id": "agent-1",
                        "name": "Architect",
                        "group": "g",
                        "slug": "architect",
                        "kind": "architect",
                        "role": "lead",
                        "owner_engineer_id": "engineer-1",
                        "hired_by_architect_id": "architect-root",
                        "persistent": 1,
                    }
                },
                "groups": {"g": ["agent-1"]},
                "group_slugs": {"g": "g"},
                "board_tasks": {
                    "task-1": {
                        "id": "task-1",
                        "task": "Plan next wave",
                        "group": "g",
                        "slug": "plan-next-wave",
                        "assigned_engineer_id": "engineer-1",
                        "created_by_architect_id": "architect-root",
                        "suggested_action": "feature/review",
                    }
                },
            }
        )

        loaded = self.db.load_all()

        self.assertEqual(loaded["agents"]["agent-1"]["kind"], "architect")
        self.assertEqual(loaded["agents"]["agent-1"]["role"], "lead")
        self.assertEqual(
            loaded["agents"]["agent-1"]["owner_engineer_id"],
            "engineer-1",
        )
        self.assertEqual(
            loaded["agents"]["agent-1"]["hired_by_architect_id"],
            "architect-root",
        )
        self.assertTrue(loaded["agents"]["agent-1"]["persistent"])
        self.assertEqual(
            loaded["board_tasks"]["task-1"]["assigned_engineer_id"],
            "engineer-1",
        )
        self.assertEqual(
            loaded["board_tasks"]["task-1"]["created_by_architect_id"],
            "architect-root",
        )
        self.assertEqual(
            loaded["board_tasks"]["task-1"]["suggested_action"],
            "feature/review",
        )

    def test_save_all_coalesces_legacy_and_kinds_fields(self):
        self.db.save_all(
            {
                "agents": {
                    "agent-1": {
                        "id": "agent-1",
                        "name": "Worker",
                        "group": "g",
                        "role": "researcher",
                        "owner_engineer_id": "engineer-1",
                    },
                    "agent-2": {
                        "id": "agent-2",
                        "name": "Legacy Worker",
                        "group": "g",
                        "template": "planner",
                        "created_by_engineer_id": "engineer-2",
                    },
                },
                "groups": {"g": ["agent-1", "agent-2"]},
                "group_slugs": {"g": "g"},
                "board_tasks": {
                    "task-1": {
                        "id": "task-1",
                        "task": "Assigned-first",
                        "group": "g",
                        "assigned_engineer_id": "engineer-1",
                    },
                    "task-2": {
                        "id": "task-2",
                        "task": "Legacy-first",
                        "group": "g",
                        "engineer_owner_id": "engineer-2",
                    },
                },
            }
        )

        agent_rows = self.db._conn.execute(
            "SELECT id, role, owner_engineer_id "
            "FROM agents ORDER BY id"
        ).fetchall()
        self.assertEqual(
            agent_rows,
            [
                ("agent-1", "researcher", "engineer-1"),
                ("agent-2", "planner", "engineer-2"),
            ],
        )
        task_rows = self.db._conn.execute(
            "SELECT id, assigned_engineer_id "
            "FROM board_tasks ORDER BY id"
        ).fetchall()
        self.assertEqual(
            task_rows,
            [
                ("task-1", "engineer-1"),
                ("task-2", "engineer-2"),
            ],
        )

    def test_init_migrates_legacy_task_ids_and_rewrites_references(self):
        legacy_db = Path(self.tmp.name) / "legacy.db"
        conn = sqlite3.connect(legacy_db)
        conn.executescript(
            """
            CREATE TABLE groups (name TEXT PRIMARY KEY, slug TEXT NOT NULL DEFAULT '', position INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE group_members (group_name TEXT NOT NULL, agent_id TEXT NOT NULL, position INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (group_name, agent_id));
            CREATE TABLE board_tasks (
                id TEXT PRIMARY KEY,
                task TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                slug TEXT NOT NULL DEFAULT '',
                group_name TEXT NOT NULL DEFAULT '',
                action_name TEXT NOT NULL DEFAULT '',
                action_vars TEXT NOT NULL DEFAULT '{}',
                agent_template TEXT NOT NULL DEFAULT '',
                instructions TEXT NOT NULL DEFAULT '',
                context TEXT NOT NULL DEFAULT '',
                criteria TEXT NOT NULL DEFAULT '',
                lane TEXT NOT NULL DEFAULT 'Backlog',
                position INTEGER NOT NULL DEFAULT 0,
                agent_id TEXT NOT NULL DEFAULT '',
                labels TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT '',
                lane_entered_at TEXT NOT NULL DEFAULT '',
                provider TEXT NOT NULL DEFAULT '',
                external_id TEXT NOT NULL DEFAULT '',
                external_url TEXT NOT NULL DEFAULT '',
                parent_task_id TEXT NOT NULL DEFAULT '',
                pipeline_depth INTEGER NOT NULL DEFAULT 0,
                pipeline_root_id TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT '',
                scheduled_at TEXT NOT NULL DEFAULT '',
                messages TEXT NOT NULL DEFAULT '[]',
                depends_on TEXT NOT NULL DEFAULT '[]',
                attachments TEXT NOT NULL DEFAULT '[]',
                health_state TEXT NOT NULL DEFAULT 'healthy',
                health_since TEXT NOT NULL DEFAULT '',
                health_details TEXT NOT NULL DEFAULT '{}',
                artifacts TEXT NOT NULL DEFAULT '[]',
                verification_mode TEXT NOT NULL DEFAULT '',
                verification_state TEXT NOT NULL DEFAULT '',
                verification_notes TEXT NOT NULL DEFAULT '',
                verification_updated_at TEXT NOT NULL DEFAULT '',
                verification_updated_by TEXT NOT NULL DEFAULT '',
                verification_summary TEXT NOT NULL DEFAULT '{}',
                worktree_boundary TEXT NOT NULL DEFAULT '{}',
                resume_after_boundary_task_id TEXT NOT NULL DEFAULT '',
                archived_at TEXT NOT NULL DEFAULT '',
                archived_from_lane TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE auto_dispatch_queue (
                group_name TEXT NOT NULL,
                position INTEGER NOT NULL DEFAULT 0,
                task_id TEXT NOT NULL,
                agent_group TEXT NOT NULL DEFAULT '',
                max_concurrent INTEGER NOT NULL DEFAULT 1,
                target_agent_id TEXT NOT NULL DEFAULT '',
                enqueued_at TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (group_name, position)
            );
            CREATE TABLE panel_events (
                id INTEGER PRIMARY KEY,
                timestamp REAL NOT NULL,
                kind TEXT NOT NULL,
                cell_id TEXT NOT NULL DEFAULT '',
                agent_name TEXT NOT NULL DEFAULT '',
                group_name TEXT NOT NULL DEFAULT '',
                message TEXT NOT NULL DEFAULT '',
                task_id TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE schedules (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                slug TEXT NOT NULL DEFAULT '',
                task_template TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                group_name TEXT NOT NULL DEFAULT '',
                action_name TEXT NOT NULL DEFAULT '',
                action_vars TEXT NOT NULL DEFAULT '{}',
                agent_template TEXT NOT NULL DEFAULT '',
                labels TEXT NOT NULL DEFAULT '[]',
                cron_expr TEXT NOT NULL DEFAULT '',
                scheduled_at TEXT NOT NULL DEFAULT '',
                timezone TEXT NOT NULL DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1,
                last_run_at TEXT NOT NULL DEFAULT '',
                next_run_at TEXT NOT NULL DEFAULT '',
                run_count INTEGER NOT NULL DEFAULT 0,
                last_task_id TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE memory_entries (
                id TEXT PRIMARY KEY,
                project_key TEXT NOT NULL DEFAULT '',
                group_name TEXT NOT NULL DEFAULT '',
                scope_kind TEXT NOT NULL,
                scope_ref TEXT NOT NULL DEFAULT '',
                entry_type TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL DEFAULT '',
                pinned INTEGER NOT NULL DEFAULT 0,
                task_id TEXT NOT NULL DEFAULT '',
                source_kind TEXT NOT NULL DEFAULT '',
                source_id TEXT NOT NULL DEFAULT '',
                source_name TEXT NOT NULL DEFAULT '',
                retention_kind TEXT NOT NULL DEFAULT 'durable',
                expires_at REAL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE memory_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_id TEXT NOT NULL,
                target_kind TEXT NOT NULL,
                target_ref TEXT NOT NULL,
                created_at REAL NOT NULL,
                UNIQUE(entry_id, target_kind, target_ref)
            );
            CREATE TABLE agent_history (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                slug TEXT DEFAULT '',
                "group" TEXT DEFAULT '',
                agent_type TEXT DEFAULT '',
                template TEXT DEFAULT '',
                created_at REAL NOT NULL,
                removed_at REAL,
                worktree_branch TEXT DEFAULT '',
                total_tokens_in INTEGER DEFAULT 0,
                total_tokens_out INTEGER DEFAULT 0,
                total_tasks INTEGER DEFAULT 0,
                status TEXT DEFAULT 'active'
            );
            CREATE TABLE agent_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                task_title TEXT NOT NULL,
                started_at REAL NOT NULL,
                completed_at REAL,
                outcome TEXT DEFAULT ''
            );
            CREATE TABLE agent_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id TEXT NOT NULL,
                task_id TEXT DEFAULT '',
                timestamp REAL NOT NULL,
                action TEXT NOT NULL,
                message TEXT DEFAULT ''
            );
            """
        )
        conn.execute("INSERT INTO groups (name, slug, position) VALUES ('Torque', 'torque', 0)")
        conn.execute(
            "INSERT INTO board_tasks (id, task, group_name, created_at, updated_at, lane_entered_at) "
            "VALUES ('task-root', 'Root', 'Torque', '2026-04-08T10:00:00+00:00', '2026-04-08T10:00:00+00:00', '2026-04-08T10:00:00+00:00')"
        )
        conn.execute(
            "INSERT INTO board_tasks (id, task, group_name, parent_task_id, pipeline_depth, pipeline_root_id, depends_on, created_at, updated_at, lane_entered_at) "
            "VALUES ('task-child', 'Child', 'Torque', 'task-root', 1, 'task-root', '[\"task-root\"]', '2026-04-08T10:10:00+00:00', '2026-04-08T10:10:00+00:00', '2026-04-08T10:10:00+00:00')"
        )
        conn.execute(
            "INSERT INTO auto_dispatch_queue (group_name, position, task_id) VALUES ('Torque', 0, 'task-child')"
        )
        conn.execute(
            "INSERT INTO panel_events (id, timestamp, kind, task_id) VALUES (1, 1.0, 'task_dispatched', 'task-child')"
        )
        conn.execute(
            "INSERT INTO schedules (id, name, group_name, last_task_id, created_at, updated_at) VALUES ('sched', 'Nightly', 'Torque', 'task-root', '', '')"
        )
        conn.execute(
            "INSERT INTO memory_entries (id, scope_kind, scope_ref, entry_type, content, task_id, created_at, updated_at) "
            "VALUES ('mem-1', 'task', 'task-child', 'note', 'hello', 'task-child', 1, 1)"
        )
        conn.execute(
            "INSERT INTO memory_links (entry_id, target_kind, target_ref, created_at) VALUES ('mem-1', 'task', 'task-root', 1)"
        )
        conn.execute(
            "INSERT INTO agent_history (id, name, created_at) VALUES ('agent-1', 'Worker', 1)"
        )
        conn.execute(
            "INSERT INTO agent_tasks (agent_id, task_id, task_title, started_at) VALUES ('agent-1', 'task-root', 'Root', 1)"
        )
        conn.execute(
            "INSERT INTO agent_messages (agent_id, task_id, timestamp, action, message) VALUES ('agent-1', 'task-child', 1, 'progress', 'working')"
        )
        conn.commit()
        conn.close()

        migrated_db = TorqueDB(legacy_db)
        migrated_db.init()
        self.addCleanup(migrated_db.close)
        loaded = migrated_db.load_all()

        self.assertIn("TORQUE:1", loaded["board_tasks"])
        self.assertIn("TORQUE:1:1", loaded["board_tasks"])
        self.assertEqual(loaded["board_tasks"]["TORQUE:1:1"]["parent_task_id"], "TORQUE:1")
        self.assertEqual(loaded["board_tasks"]["TORQUE:1:1"]["pipeline_root_id"], "TORQUE:1")
        self.assertEqual(loaded["board_tasks"]["TORQUE:1:1"]["depends_on"], ["TORQUE:1"])
        self.assertEqual(loaded["board_tasks"]["TORQUE:1"]["reply_agent_id"], "")
        self.assertEqual(loaded["auto_dispatch_queues"]["Torque"][0]["task_id"], "TORQUE:1:1")
        self.assertEqual(
            loaded["auto_dispatch_queues"]["Torque"][0]["engineer_owner_id"],
            "",
        )
        self.assertEqual(loaded["schedules"]["sched"]["last_task_id"], "TORQUE:1")
        self.assertEqual(loaded["task_id_aliases"]["task-root"], "TORQUE:1")
        self.assertEqual(loaded["task_id_aliases"]["task-child"], "TORQUE:1:1")

    def test_load_all_restores_board_saved_views_by_group(self):
        views = {
            "alpha": [
                {
                    "name": "Review Queue",
                    "search_query": "review",
                    "filter_labels": ["torque:blocked"],
                    "filter_actions": ["feature/review"],
                    "filter_agents": [],
                }
            ]
        }
        self.db.save_ui_state("board_saved_views_by_group", json.dumps(views))

        loaded = self.db.load_all()

        self.assertEqual(loaded["board_saved_views_by_group"], views)

    def test_load_all_restores_board_lane_sorts_by_group(self):
        sorts = {
            "alpha": {
                "Backlog": "due",
                "Done": "oldest",
            }
        }
        self.db.save_ui_state("board_lane_sorts_by_group", json.dumps(sorts))

        loaded = self.db.load_all()

        self.assertEqual(loaded["board_lane_sorts_by_group"], sorts)

    def test_load_all_restores_board_card_density_by_group(self):
        density = {
            "alpha": "compact",
            "beta": "detailed",
        }
        self.db.save_ui_state("board_card_density_by_group",
                              json.dumps(density))

        loaded = self.db.load_all()

        self.assertEqual(loaded["board_card_density_by_group"], density)

    def test_load_all_restores_selected_principal_id(self):
        self.db.save_ui_state("selected_principal_id", "architect-7")

        loaded = self.db.load_all()

        self.assertEqual(loaded["selected_principal_id"], "architect-7")

    def test_load_all_restores_active_group(self):
        self.db.save_ui_state("active_group", "beta")

        loaded = self.db.load_all()

        self.assertEqual(loaded["active_group"], "beta")

    def test_load_all_restores_selected_agent_id(self):
        self.db.save_ui_state("selected_agent_id", "agent-7")

        loaded = self.db.load_all()

        self.assertEqual(loaded["selected_agent_id"], "agent-7")

    def test_load_all_defaults_selected_principal_id_to_empty(self):
        loaded = self.db.load_all()

        self.assertEqual(loaded["selected_principal_id"], "")
        self.assertEqual(loaded["selected_agent_id"], "")

    def test_load_all_restores_events_dismissed_attention(self):
        dismissed = {
            "ask-1": 123.0,
            "agent-1": 456.0,
        }
        self.db.save_ui_state(
            "events_dismissed_attention",
            json.dumps(dismissed),
        )

        loaded = self.db.load_all()

        self.assertEqual(loaded["events_dismissed_attention"], dismissed)

    def test_load_all_restores_standalone_panel_layout(self):
        layout = {
            "version": 1,
            "bottom": {
                "open": True,
                "size": 280,
                "tabs": ["board", "engineer"],
                "active": "board",
            },
            "right": {
                "open": True,
                "size": 320,
                "tabs": ["context", "events"],
                "active": "context",
            },
            "floats": {
                "actions": {
                    "x": 40,
                    "y": 50,
                    "width": 420,
                    "height": 320,
                    "z": 3,
                },
            },
            "float_frames": {
                "actions": {
                    "x": 40,
                    "y": 50,
                    "width": 420,
                    "height": 320,
                    "z": 3,
                },
                "templates": {
                    "x": 80,
                    "y": 90,
                    "width": 460,
                    "height": 340,
                    "z": 4,
                },
            },
        }
        self.db.save_ui_state(
            "standalone_panel_layout",
            json.dumps(layout),
        )

        loaded = self.db.load_all()

        self.assertEqual(loaded["standalone_panel_layout"], layout)

    def test_load_all_restores_native_window_and_split_state(self):
        bounds = {
            "main": {
                "x": 20,
                "y": 40,
                "width": 1280,
                "height": 820,
                "display_id": "main-display",
            }
        }
        self.db.save_ui_state("window_bounds", json.dumps(bounds))
        self.db.save_ui_state("workspace_sidebar_width", "732")
        self.db.save_ui_state("terminal_direct_messages_height", "224")
        self.db.save_ui_state("context_panel_split_ratio", "0.44")

        loaded = self.db.load_all()

        self.assertEqual(loaded["window_bounds"], bounds)
        self.assertEqual(loaded["workspace_sidebar_width"], 732)
        self.assertEqual(loaded["terminal_direct_messages_height"], 224)
        self.assertEqual(loaded["context_panel_split_ratio"], 0.44)

    def test_panel_event_paging_and_trim_keep_recent_events(self):
        for i in range(1, 6):
            self.db.save_panel_event(
                {
                    "id": i,
                    "timestamp": float(i),
                    "kind": "task_completed",
                    "cell_id": f"agent-{i}",
                    "agent_name": f"Agent {i}",
                    "group": "g",
                    "message": f"done {i}",
                    "task_id": f"task-{i}",
                }
            )

        self.assertEqual(self.db.get_panel_event_max_id(), 5)
        self.assertEqual(
            [evt["id"] for evt in self.db.load_panel_events(limit=3)],
            [3, 4, 5],
        )
        self.assertEqual(
            [evt["id"] for evt in self.db.load_panel_events(limit=2, before_id=4)],
            [2, 3],
        )
        self.assertEqual(
            [evt["id"] for evt in self.db.load_panel_events(limit=10,
                                                            cell_id="agent-3")],
            [3],
        )

        self.db.trim_panel_events(2)

        self.assertEqual(
            [evt["id"] for evt in self.db.load_panel_events(limit=10)],
            [4, 5],
        )

    def test_engineer_settings_and_journal_roundtrip(self):
        self.db.save_engineer_settings(
            "g",
            {
                "group": "g",
                "push_interval": 30,
                "max_interval": 120,
                "heartbeat_interval": 180,
                "default_worker_concurrency": 4,
                "autonomy_mode": "aggressive_auto_continue",
                "wave_size_preference": "large",
                "same_agent_follow_up_preference": "prefer_same_agent",
                "digest_verbosity": "detailed",
                "escalation_style": "keep_moving",
                "paused": True,
                "custom_instructions": "Focus on regressions.",
                "restrict_to_created_agents": True,
                "engineer_can_override_worker_provider": False,
                "pending_question": "Need approval",
                "pending_question_set_at": 123.5,
                "pending_question_actor_id": "eng-1",
                "pending_note": "FYI: release notes are ready",
                "pending_note_kind": "note",
                "pending_note_set_at": 456.5,
                "pending_note_actor_id": "eng-2",
                "enabled_events": ["task_completed"],
                "engineer_provider": "codex",
                "engineer_boot_command": "codex --model gpt-5",
                "engineer_model": "gpt-5.1",
                "engineer_reasoning_effort": "high",
                "engineer_directory": "/repo/.torque/engineer",
                "engineer_profile": "Ops",
                "engineer_shell": "fish",
                "engineer_tab_color": "none",
            },
        )
        first = self.db.save_journal_entry("g", 10.0, "decision", "Ship it")
        second = self.db.save_journal_entry("g", 20.0, "plan", "Add tests")

        loaded = self.db.load_engineer_settings("g")
        self.assertEqual(loaded["pending_question"], "Need approval")
        self.assertEqual(loaded["pending_question_set_at"], 123.5)
        self.assertEqual(loaded["pending_question_actor_id"], "eng-1")
        self.assertTrue(loaded["restrict_to_created_agents"])
        self.assertFalse(loaded["engineer_can_override_worker_provider"])
        self.assertEqual(loaded["pending_note"], "FYI: release notes are ready")
        self.assertEqual(loaded["pending_note_kind"], "note")
        self.assertEqual(loaded["pending_note_set_at"], 456.5)
        self.assertEqual(loaded["pending_note_actor_id"], "eng-2")
        self.assertEqual(loaded["enabled_events"], ["task_completed"])
        self.assertEqual(loaded["heartbeat_interval"], 180)
        self.assertEqual(loaded["default_worker_concurrency"], 4)
        self.assertEqual(loaded["autonomy_mode"], "aggressive_auto_continue")
        self.assertEqual(loaded["wave_size_preference"], "large")
        self.assertEqual(
            loaded["same_agent_follow_up_preference"], "prefer_same_agent"
        )
        self.assertEqual(loaded["digest_verbosity"], "detailed")
        self.assertEqual(loaded["escalation_style"], "keep_moving")
        self.assertEqual(loaded["engineer_boot_command"], "codex --model gpt-5")
        self.assertEqual(loaded["engineer_model"], "gpt-5.1")
        self.assertEqual(loaded["engineer_reasoning_effort"], "high")
        self.assertEqual(loaded["engineer_directory"], "/repo/.torque/engineer")
        self.assertEqual(loaded["engineer_profile"], "Ops")
        self.assertEqual(loaded["engineer_shell"], "fish")
        self.assertEqual(loaded["engineer_tab_color"], "none")

        entries = self.db.load_journal_entries("g", limit=10)

        self.assertEqual([entry["id"] for entry in entries], [second, first])
        self.assertEqual(entries[0]["type"], "plan")
        self.assertEqual(
            self.db.load_journal_entries("g", limit=10, entry_type="decision"),
            [entries[1]],
        )

    def test_journal_source_key_makes_system_entries_idempotent(self):
        first = self.db.save_journal_entry(
            "g",
            10.0,
            "qa",
            "Question:\nApprove?\n\nAnswer:\nYes",
            author_cell_id="eng-1",
            source_key="qa:g:eng-1:1",
        )
        second = self.db.save_journal_entry(
            "g",
            20.0,
            "qa",
            "Question:\nApprove?\n\nAnswer:\nYes again",
            author_cell_id="eng-1",
            source_key="qa:g:eng-1:1",
        )

        entries = self.db.load_journal_entries(
            "g", limit=10, author_cell_id="eng-1")
        self.assertEqual(second, first)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["timestamp"], 10.0)
        self.assertEqual(entries[0]["entry"], "Question:\nApprove?\n\nAnswer:\nYes")

    def test_init_migrates_legacy_engineer_journal_author_column_before_index(self):
        legacy_path = Path(self.tmp.name) / "legacy-journal.db"
        conn = sqlite3.connect(str(legacy_path))
        conn.execute("""
            CREATE TABLE engineer_journal (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                group_name  TEXT NOT NULL,
                timestamp   REAL NOT NULL,
                entry_type  TEXT NOT NULL,
                entry       TEXT NOT NULL
            )
        """)
        conn.execute(
            "INSERT INTO engineer_journal "
            "(group_name, timestamp, entry_type, entry) "
            "VALUES ('g', 10.0, 'plan', 'Legacy entry')"
        )
        conn.commit()
        conn.close()

        migrated = TorqueDB(legacy_path)
        self.addCleanup(migrated.close)

        migrated.init()

        cols = {
            row[1]
            for row in migrated._conn.execute(
                "PRAGMA table_info(engineer_journal)"
            ).fetchall()
        }
        self.assertIn("author_cell_id", cols)
        self.assertIn("source_key", cols)
        self.assertIsNotNone(
            migrated._conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='index' "
                "AND name='idx_engineer_journal_group_author'"
            ).fetchone()
        )
        entries = migrated.load_journal_entries("g", limit=10)
        self.assertEqual(entries[0]["entry"], "Legacy entry")
        self.assertEqual(entries[0]["author_cell_id"], "")

    def test_init_renames_legacy_engineer_schema_and_payload_names(self):
        legacy = "wea" + "ver"
        legacy_path = Path(self.tmp.name) / "legacy-engineer-names.db"
        conn = sqlite3.connect(str(legacy_path))
        conn.execute(
            "CREATE TABLE group_settings ("
            f"group_name TEXT PRIMARY KEY, {legacy}_agent_id TEXT NOT NULL DEFAULT '')"
        )
        conn.execute(
            f"INSERT INTO group_settings (group_name, {legacy}_agent_id) "
            "VALUES ('g', 'eng-1')"
        )
        conn.execute(
            f"CREATE TABLE {legacy}_settings ("
            "group_name TEXT PRIMARY KEY, "
            "push_interval INTEGER NOT NULL DEFAULT 60, "
            "max_interval INTEGER NOT NULL DEFAULT 300, "
            "heartbeat_interval INTEGER NOT NULL DEFAULT 300, "
            "default_worker_concurrency INTEGER NOT NULL DEFAULT 2, "
            "autonomy_mode TEXT NOT NULL DEFAULT 'dispatch_when_clear', "
            "wave_size_preference TEXT NOT NULL DEFAULT 'small', "
            "same_agent_follow_up_preference TEXT NOT NULL DEFAULT 'balanced', "
            "digest_verbosity TEXT NOT NULL DEFAULT 'balanced', "
            "escalation_style TEXT NOT NULL DEFAULT 'note_then_ask', "
            "paused INTEGER NOT NULL DEFAULT 0, "
            "custom_instructions TEXT NOT NULL DEFAULT '', "
            "restrict_to_created_agents INTEGER NOT NULL DEFAULT 0, "
            "pending_question TEXT NOT NULL DEFAULT '', "
            "pending_note TEXT NOT NULL DEFAULT '', "
            "pending_note_kind TEXT NOT NULL DEFAULT '', "
            "enabled_events TEXT NOT NULL DEFAULT '[]', "
            f"{legacy}_provider TEXT NOT NULL DEFAULT '', "
            f"{legacy}_boot_command TEXT NOT NULL DEFAULT '', "
            f"{legacy}_model TEXT NOT NULL DEFAULT '', "
            f"{legacy}_reasoning_effort TEXT NOT NULL DEFAULT '', "
            f"{legacy}_directory TEXT NOT NULL DEFAULT '', "
            f"{legacy}_profile TEXT NOT NULL DEFAULT '', "
            f"{legacy}_shell TEXT NOT NULL DEFAULT '', "
            f"{legacy}_tab_color TEXT NOT NULL DEFAULT '', "
            "pending_question_set_at REAL NOT NULL DEFAULT 0, "
            "pending_question_actor_id TEXT NOT NULL DEFAULT '')"
        )
        conn.execute(
            f"INSERT INTO {legacy}_settings "
            f"(group_name, push_interval, max_interval, enabled_events, "
            f"{legacy}_provider, {legacy}_boot_command) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("g", 45, 90, json.dumps(["task_completed"]), "codex", "codex --fast"),
        )
        conn.execute(
            f"CREATE TABLE {legacy}_task_log ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "group_name TEXT NOT NULL, task_id TEXT NOT NULL, "
            "task_title TEXT NOT NULL, agent_id TEXT NOT NULL, "
            "agent_name TEXT NOT NULL DEFAULT '', "
            "agent_slug TEXT NOT NULL DEFAULT '', "
            "agent_owned INTEGER NOT NULL DEFAULT 0, "
            "started_at REAL NOT NULL)"
        )
        conn.execute(
            f"INSERT INTO {legacy}_task_log "
            "(group_name, task_id, task_title, agent_id, started_at) "
            "VALUES ('g', 'T-1', 'Do work', 'agent-1', 10.0)"
        )
        conn.execute(
            f"CREATE INDEX idx_{legacy}_task_log_group "
            f"ON {legacy}_task_log(group_name, started_at DESC, id DESC)"
        )
        conn.execute(
            f"CREATE TABLE {legacy}_journal ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "group_name TEXT NOT NULL, timestamp REAL NOT NULL, "
            "author_cell_id TEXT NOT NULL DEFAULT '', "
            "entry_type TEXT NOT NULL, entry TEXT NOT NULL)"
        )
        conn.execute(
            f"INSERT INTO {legacy}_journal "
            "(group_name, timestamp, entry_type, entry) "
            "VALUES ('g', 20.0, 'plan', 'Continue')"
        )
        conn.execute(
            f"CREATE INDEX idx_{legacy}_journal_group "
            f"ON {legacy}_journal(group_name, id DESC)"
        )
        conn.execute(
            f"CREATE INDEX idx_{legacy}_journal_group_author "
            f"ON {legacy}_journal(group_name, author_cell_id, id DESC)"
        )
        conn.execute(
            "CREATE TABLE auto_dispatch_queue ("
            "group_name TEXT NOT NULL, position INTEGER NOT NULL DEFAULT 0, "
            "task_id TEXT NOT NULL, agent_group TEXT NOT NULL DEFAULT '', "
            "max_concurrent INTEGER NOT NULL DEFAULT 1, "
            "target_agent_id TEXT NOT NULL DEFAULT '', "
            f"{legacy}_owner_id TEXT NOT NULL DEFAULT '', "
            "enqueued_at TEXT NOT NULL DEFAULT '', "
            "PRIMARY KEY (group_name, position))"
        )
        conn.execute(
            "INSERT INTO auto_dispatch_queue "
            f"(group_name, position, task_id, {legacy}_owner_id) "
            "VALUES ('g', 0, 'T-1', 'eng-1')"
        )
        conn.commit()
        conn.close()

        migrated = TorqueDB(legacy_path)
        self.addCleanup(migrated.close)
        migrated.init()

        tables = {
            row[0]
            for row in migrated._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        self.assertIn("engineer_settings", tables)
        self.assertIn("engineer_task_log", tables)
        self.assertIn("engineer_journal", tables)
        self.assertNotIn(f"{legacy}_settings", tables)
        self.assertNotIn(f"{legacy}_task_log", tables)
        self.assertNotIn(f"{legacy}_journal", tables)

        settings_columns = {
            row[1]
            for row in migrated._conn.execute(
                "PRAGMA table_info(engineer_settings)"
            ).fetchall()
        }
        self.assertIn("engineer_provider", settings_columns)
        self.assertIn("engineer_boot_command", settings_columns)
        self.assertNotIn(f"{legacy}_provider", settings_columns)
        loaded = migrated.load_engineer_settings("g")
        self.assertEqual(loaded["engineer_provider"], "codex")
        self.assertEqual(loaded["engineer_boot_command"], "codex --fast")

        group_row = migrated._conn.execute(
            "SELECT engineer_agent_id FROM group_settings WHERE group_name='g'"
        ).fetchone()
        self.assertEqual(group_row[0], "eng-1")
        queue_row = migrated._conn.execute(
            "SELECT engineer_owner_id FROM auto_dispatch_queue "
            "WHERE group_name='g'"
        ).fetchone()
        self.assertEqual(queue_row[0], "eng-1")
        task_log = migrated.load_engineer_task_log("g", limit=10)
        self.assertEqual(task_log[0]["task_id"], "T-1")
        journal = migrated.load_journal_entries("g", limit=10)
        self.assertEqual(journal[0]["entry"], "Continue")
        schema_text = "\n".join(
            str(part or "")
            for row in migrated._conn.execute(
                "SELECT name, tbl_name, sql FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%'"
            ).fetchall()
            for part in row
        )
        self.assertNotIn(legacy, schema_text)

    def test_init_renames_legacy_engineer_payload_names(self):
        legacy = "wea" + "ver"
        legacy_path = Path(self.tmp.name) / "legacy-engineer-payloads.db"
        seeded = TorqueDB(legacy_path)
        seeded.init()
        seeded.save_board_task(
            BoardTask(
                id="T-1",
                task="Follow up",
                group="g",
                labels=[f"torque:{legacy}-message"],
                messages=[{"action": f"{legacy}_message"}],
            )
        )
        seeded.save_ui_state(
            "standalone_panel_layout",
            json.dumps(
                {
                    "version": 1,
                    "bottom": {
                        "tabs": [legacy],
                        "active": legacy,
                    },
                    "floats": {
                        legacy: {
                            "x": 40,
                            "y": 50,
                            "width": 420,
                            "height": 320,
                        },
                    },
                }
            ),
        )
        seeded.save_ui_state("panel_active", legacy)
        seeded.close()

        migrated = TorqueDB(legacy_path)
        self.addCleanup(migrated.close)
        migrated.init()

        task = migrated._conn.execute(
            "SELECT labels, messages FROM board_tasks LIMIT 1"
        ).fetchone()
        self.assertEqual(json.loads(task[0]), ["torque:engineer-message"])
        self.assertEqual(json.loads(task[1])[0]["action"], "engineer_message")
        layout_row = migrated._conn.execute(
            "SELECT value FROM ui_state WHERE key='standalone_panel_layout'"
        ).fetchone()
        layout = json.loads(layout_row[0])
        self.assertIn("engineer", layout["floats"])
        self.assertNotIn(legacy, layout["floats"])
        self.assertEqual(layout["bottom"]["tabs"], ["engineer"])
        self.assertEqual(layout["bottom"]["active"], "engineer")
        panel_row = migrated._conn.execute(
            "SELECT value FROM ui_state WHERE key='panel_active'"
        ).fetchone()
        self.assertEqual(panel_row[0], "engineer")

    def test_engineer_settings_load_backfills_heartbeat_from_legacy_rows(self):
        self.db._conn.execute(
            """
            INSERT INTO engineer_settings
                (group_name, push_interval, max_interval, paused,
                 custom_instructions, pending_question, enabled_events,
                 engineer_provider, engineer_boot_command)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy",
                60,
                240,
                0,
                "",
                "",
                '["task_completed"]',
                "",
                "",
            ),
        )
        self.db._conn.commit()

        loaded = self.db.load_engineer_settings("legacy")

        self.assertEqual(loaded["max_interval"], 240)
        self.assertEqual(loaded["heartbeat_interval"], 240)
        self.assertEqual(loaded["default_worker_concurrency"], 2)
        self.assertFalse(loaded["restrict_to_created_agents"])
        self.assertEqual(loaded["autonomy_mode"], "dispatch_when_clear")
        self.assertEqual(loaded["wave_size_preference"], "small")
        self.assertEqual(
            loaded["same_agent_follow_up_preference"], "balanced"
        )
        self.assertEqual(loaded["digest_verbosity"], "balanced")
        self.assertEqual(loaded["escalation_style"], "note_then_ask")
        self.assertEqual(loaded["engineer_directory"], "")
        self.assertEqual(loaded["engineer_profile"], "")
        self.assertEqual(loaded["engineer_shell"], "")
        self.assertEqual(loaded["engineer_tab_color"], "")
        self.assertTrue(loaded["engineer_can_override_worker_provider"])

    def test_engineer_settings_migration_defaults_worker_provider_override(self):
        legacy_path = Path(self.tmp.name) / "legacy-engineer-provider-toggle.db"
        conn = sqlite3.connect(str(legacy_path))
        conn.execute(
            """
            CREATE TABLE engineer_settings (
                group_name TEXT PRIMARY KEY,
                push_interval INTEGER NOT NULL DEFAULT 60,
                max_interval INTEGER NOT NULL DEFAULT 300,
                paused INTEGER NOT NULL DEFAULT 0,
                custom_instructions TEXT NOT NULL DEFAULT '',
                enabled_events TEXT NOT NULL DEFAULT '[]'
            )
            """
        )
        conn.execute(
            "INSERT INTO engineer_settings (group_name) VALUES ('legacy')"
        )
        conn.commit()
        conn.close()

        migrated = TorqueDB(legacy_path)
        self.addCleanup(migrated.close)
        migrated.init()

        columns = {
            row[1]
            for row in migrated._conn.execute(
                "PRAGMA table_info(engineer_settings)"
            )
        }
        self.assertIn("engineer_can_override_worker_provider", columns)
        row = migrated._conn.execute(
            "SELECT engineer_can_override_worker_provider "
            "FROM engineer_settings WHERE group_name='legacy'"
        ).fetchone()
        self.assertEqual(row[0], 1)
        self.assertTrue(
            migrated.load_engineer_settings("legacy")[
                "engineer_can_override_worker_provider"
            ]
        )

    def test_agent_digest_settings_roundtrip(self):
        self.db.save_agent_digest_settings(
            "eng-1",
            {
                "agent_id": "eng-1",
                "paused": True,
                "push_interval": 45,
                "max_interval": 180,
                "heartbeat_interval": 240,
                "digest_verbosity": "detailed",
                "enabled_events": ["task_completed", "task_derived"],
                "architect_digest": True,
                "wake_on_digest": True,
            },
        )

        loaded = self.db.load_agent_digest_settings("eng-1")

        self.assertIsNotNone(loaded)
        self.assertTrue(loaded["paused"])
        self.assertEqual(loaded["push_interval"], 45)
        self.assertEqual(loaded["max_interval"], 180)
        self.assertEqual(loaded["heartbeat_interval"], 240)
        self.assertEqual(loaded["digest_verbosity"], "detailed")
        self.assertEqual(
            loaded["enabled_events"], ["task_completed", "task_derived"]
        )
        self.assertTrue(loaded["architect_digest"])
        self.assertTrue(loaded["wake_on_digest"])

    def test_digest_queue_and_sent_events_roundtrip(self):
        first = self.db.save_digest_queued_event(
            "eng-1",
            {
                "id": 1,
                "kind": "task_completed",
                "message": "queued first",
                "_digest_queue_id": 999,
            },
            10.0,
        )
        second = self.db.save_digest_queued_event(
            "eng-1",
            {
                "id": 2,
                "kind": "agent_reply",
                "message": "queued second",
            },
            11.0,
        )

        queued = self.db.load_digest_queued_events()

        self.assertEqual(
            [evt["message"] for evt in queued["eng-1"]],
            ["queued first", "queued second"],
        )
        self.assertEqual(queued["eng-1"][0]["_digest_queue_id"], first)
        self.assertEqual(queued["eng-1"][1]["_digest_queue_id"], second)
        self.assertEqual(queued["eng-1"][0]["_digest_enqueued_at"], 10.0)
        self.assertNotEqual(queued["eng-1"][0]["_digest_queue_id"], 999)

        self.db.complete_digest_delivery(
            "eng-1",
            [
                (queued["eng-1"][0], 10.0, 20.0),
                (queued["eng-1"][1], 11.0, 20.0),
            ],
            [first, second],
        )

        self.assertEqual(self.db.load_digest_queued_events(), {})
        sent = self.db.load_digest_sent_events()["eng-1"]
        self.assertEqual(
            [evt["message"] for evt in sent],
            ["queued first", "queued second"],
        )
        self.assertEqual([evt["delivered_at"] for evt in sent], [20.0, 20.0])


    def test_sqlite_lock_retry_rolls_back_before_health_commit(self):
        attempts = {"count": 0}
        event = {"kind": "task_completed", "message": "no duplicate"}

        def operation():
            attempts["count"] += 1
            self.db._conn.execute(
                "INSERT INTO digest_queued_events "
                "(recipient_id, event_json, enqueued_at) VALUES (?,?,?)",
                ("eng-1", json.dumps(event, separators=(",", ":")), 10.0),
            )
            if attempts["count"] == 1:
                raise sqlite3.OperationalError("database is locked")
            self.db._conn.commit()

        self.db._run_sqlite_write_with_lock_retry(operation, surface="digest")

        queued = self.db.load_digest_queued_events()["eng-1"]
        self.assertEqual([evt["message"] for evt in queued], ["no duplicate"])
        health = self.db.load_mcp_health_summary(since=0)
        self.assertEqual(health["surfaces"]["digest"]["events"].get("retry"), 1)

    def test_digest_delivery_lock_retry_rolls_back_partial_sent_rows(self):
        attempts = {"count": 0}
        event_json = json.dumps(
            {"kind": "task_completed", "message": "deliver once"},
            separators=(",", ":"),
        )

        def operation():
            attempts["count"] += 1
            self.db._conn.execute("BEGIN")
            self.db._conn.execute(
                "INSERT INTO digest_sent_events "
                "(recipient_id, event_json, enqueued_at, delivered_at) "
                "VALUES (?,?,?,?)",
                ("eng-1", event_json, 10.0, 20.0),
            )
            if attempts["count"] == 1:
                raise sqlite3.OperationalError("database is locked")
            self.db._conn.commit()

        self.db._run_sqlite_write_with_lock_retry(operation, surface="digest")

        sent = self.db.load_digest_sent_events()["eng-1"]
        self.assertEqual([evt["message"] for evt in sent], ["deliver once"])
        health = self.db.load_mcp_health_summary(since=0)
        self.assertEqual(health["surfaces"]["digest"]["events"].get("retry"), 1)

    def test_digest_sent_events_are_capped_per_recipient(self):
        self.db.complete_digest_delivery(
            "eng-1",
            [
                (
                    {"id": i, "kind": "task_completed", "message": f"sent {i}"},
                    float(i),
                    1000.0 + i,
                )
                for i in range(205)
            ],
            [],
            sent_cap=200,
        )

        sent = self.db.load_digest_sent_events()["eng-1"]

        self.assertEqual(len(sent), 200)
        self.assertEqual(sent[0]["message"], "sent 5")
        self.assertEqual(sent[-1]["message"], "sent 204")
        row = self.db._conn.execute(
            "SELECT COUNT(*) FROM digest_sent_events WHERE recipient_id=?",
            ("eng-1",),
        ).fetchone()
        self.assertEqual(row[0], 200)

    def test_init_migrates_legacy_engineer_settings_to_agent_digest_settings(self):
        self.db.save_group("g", 0)
        self.db.save_group_settings(
            "g",
            GroupSettings(engineer_agent_id="eng-1"),
        )
        self.db.save_agent(
            AgentCell(
                id="eng-1",
                name="Engineer One",
                group="g",
                kind="engineer",
                persistent=True,
            )
        )
        self.db.save_engineer_settings(
            "g",
            {
                "group": "g",
                "paused": True,
                "push_interval": 30,
                "max_interval": 120,
                "heartbeat_interval": 240,
                "digest_verbosity": "compact",
                "enabled_events": ["task_completed"],
            },
        )
        self.db.close()

        reopened = TorqueDB(self.db.db_path)
        reopened.init()
        self.addCleanup(reopened.close)

        loaded = reopened.load_agent_digest_settings("eng-1")

        self.assertIsNotNone(loaded)
        self.assertTrue(loaded["paused"])
        self.assertEqual(loaded["push_interval"], 30)
        self.assertEqual(loaded["max_interval"], 120)
        self.assertEqual(loaded["heartbeat_interval"], 240)
        self.assertEqual(loaded["digest_verbosity"], "compact")
        self.assertEqual(loaded["enabled_events"], ["task_completed"])

    def test_engineer_task_log_roundtrip_and_group_rename(self):
        first_id = self.db.save_engineer_task_log_entry(
            {
                "group": "g",
                "task_id": "TORQUE:1",
                "task_title": "Implement Worklog",
                "agent_id": "agent-1",
                "agent_name": "Worker One",
                "agent_slug": "worker-one",
                "agent_owned": True,
                "started_at": 10.0,
            }
        )
        second_id = self.db.save_engineer_task_log_entry(
            {
                "group": "g",
                "task_id": "TORQUE:2",
                "task_title": "Review Worklog",
                "agent_id": "agent-2",
                "agent_name": "Worker Two",
                "agent_slug": "worker-two",
                "agent_owned": False,
                "started_at": 20.0,
            }
        )

        loaded = self.db.load_engineer_task_log("g", limit=10)

        self.assertEqual([entry["id"] for entry in loaded], [second_id, first_id])
        self.assertEqual(loaded[0]["task_id"], "TORQUE:2")
        self.assertFalse(loaded[0]["agent_owned"])
        self.assertEqual(loaded[1]["agent_slug"], "worker-one")

        self.db.rename_engineer_task_log_group("g", "renamed")
        renamed = self.db.load_engineer_task_log("renamed", limit=10)

        self.assertEqual([entry["task_id"] for entry in renamed], ["TORQUE:2", "TORQUE:1"])
        self.assertEqual(self.db.load_engineer_task_log("g", limit=10), [])

        self.db.trim_engineer_task_log("renamed", limit=1)
        self.assertEqual(
            [entry["task_id"] for entry in self.db.load_engineer_task_log("renamed", limit=10)],
            ["TORQUE:2"],
        )

    def test_playbook_candidates_roundtrip(self):
        candidate = {
            "id": "cand-1",
            "group": "g",
            "family_key": "g|feature/implement|feature|billing-dashboard",
            "status": "draft",
            "created_at": 10.0,
            "updated_at": 12.0,
            "name": "feature-implement-billing-dashboard",
            "root_action": "feature/implement",
            "labels": ["feature"],
            "normalized_task_family": "billing-dashboard",
            "entry_action": "feature/implement",
            "agent_template": "default",
            "workflow": [
                {"kind": "action", "action": "feature/implement"},
                {"kind": "action", "action": "feature/review"},
            ],
            "workflow_shape": [
                {"kind": "action", "action": "feature/implement"},
                {"kind": "action", "action": "feature/review"},
            ],
            "dispatch_sequence": [
                {
                    "action": "feature/implement",
                    "agent_template": "default",
                    "agent_type": "codex",
                    "uses_worktree": True,
                },
                {
                    "action": "feature/review",
                    "agent_template": "default",
                    "agent_type": "codex",
                    "uses_worktree": True,
                },
            ],
            "action_combination": ["feature/implement", "feature/review"],
            "constraints": {"worktree": True, "review_required": False},
            "evidence": {"quality_score": 0.91, "successful_runs": 3},
            "supporting_runs": [{"root_task_id": "root-1"}],
            "counterexamples": [{"root_task_id": "root-4", "reason": "blocked"}],
        }

        self.db.replace_playbook_candidates([candidate], group_name="g")

        loaded = self.db.load_playbook_candidates(group_name="g", limit=10)

        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["name"], candidate["name"])
        self.assertEqual(loaded[0]["workflow"][1]["action"], "feature/review")
        self.assertTrue(loaded[0]["constraints"]["worktree"])
        self.assertEqual(loaded[0]["supporting_runs"][0]["root_task_id"], "root-1")

    def test_playbooks_roundtrip(self):
        playbook = {
            "id": "playbook-cand-1",
            "group": "g",
            "source_candidate_id": "cand-1",
            "status": "draft",
            "generated": True,
            "review_required": True,
            "created_at": 20.0,
            "updated_at": 21.0,
            "published_at": None,
            "discarded_at": None,
            "name": "feature-implement-billing-dashboard",
            "description": "Generated from Torque history. Review before publication.",
            "match": {
                "root_action": "feature/implement",
                "labels": ["feature"],
                "normalized_task_family": "billing-dashboard",
            },
            "entry_action": "feature/implement",
            "agent_template": "default",
            "workflow": [
                {"kind": "action", "action": "feature/implement"},
                {"kind": "action", "action": "feature/review"},
            ],
            "constraints": {"worktree": True},
            "evidence": {"quality_score": 0.91},
            "publication_preview": {
                "generated": True,
                "review_state": "pending",
                "ready_to_publish": True,
            },
        }

        self.db.save_playbook(playbook)

        loaded = self.db.load_playbooks(group_name="g", status_filter="draft", limit=10)

        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["source_candidate_id"], "cand-1")
        self.assertTrue(loaded[0]["generated"])
        self.assertEqual(loaded[0]["match"]["normalized_task_family"], "billing-dashboard")
        self.assertTrue(loaded[0]["publication_preview"]["ready_to_publish"])

    def test_memory_entries_roundtrip_and_filters(self):
        self.db.save_memory_entry(
            {
                "id": "mem-1",
                "project_key": "/repo",
                "group_name": "g",
                "scope_kind": "task",
                "scope_ref": "task-1",
                "entry_type": "decision",
                "title": "Adopt SQLite memory",
                "content": "Use SQLite as the durable source of truth.",
                "pinned": True,
                "task_id": "task-1",
                "source_kind": "agent",
                "source_id": "agent-1",
                "source_name": "Worker",
                "retention_kind": "durable",
                "expires_at": None,
                "created_at": 10.0,
                "updated_at": 10.0,
            }
        )
        self.db.save_memory_entry(
            {
                "id": "mem-2",
                "project_key": "/repo",
                "group_name": "g",
                "scope_kind": "group",
                "scope_ref": "g",
                "entry_type": "finding",
                "title": "Keep snapshots small",
                "content": "Do not mirror memory into full UI snapshots.",
                "pinned": False,
                "task_id": "",
                "source_kind": "agent",
                "source_id": "agent-2",
                "source_name": "Reviewer",
                "retention_kind": "transient",
                "expires_at": 4_102_444_800.0,
                "created_at": 20.0,
                "updated_at": 20.0,
            }
        )
        self.db.save_memory_link(
            {
                "entry_id": "mem-1",
                "target_kind": "task",
                "target_ref": "task-1",
                "created_at": 11.0,
            }
        )
        self.db.save_memory_link(
            {
                "entry_id": "mem-1",
                "target_kind": "agent",
                "target_ref": "agent-1",
                "created_at": 12.0,
            }
        )
        self.db.save_memory_link(
            {
                "entry_id": "mem-2",
                "target_kind": "pipeline",
                "target_ref": "task-1",
                "created_at": 21.0,
            }
        )

        all_entries = self.db.load_memory_entries(group_name="g", limit=10)
        self.assertEqual([entry["id"] for entry in all_entries], ["mem-1", "mem-2"])
        self.assertEqual(
            [link["target_kind"] for link in all_entries[0]["links"]],
            ["task", "agent"],
        )

        pinned = self.db.load_memory_entries(
            group_name="g", pinned_only=True, limit=10
        )
        self.assertEqual([entry["id"] for entry in pinned], ["mem-1"])

        scoped = self.db.load_memory_entries(
            scope_kind="task", scope_ref="task-1", limit=10
        )
        self.assertEqual([entry["id"] for entry in scoped], ["mem-1"])

        searched = self.db.load_memory_entries(search="snapshots", limit=10)
        self.assertEqual([entry["id"] for entry in searched], ["mem-2"])

        linked = self.db.load_memory_entries(
            linked_target_kind="agent",
            linked_target_ref="agent-1",
            limit=10,
        )
        self.assertEqual([entry["id"] for entry in linked], ["mem-1"])

        self.db.set_memory_entry_pinned("mem-2", True, 30.0)
        entry = self.db.load_memory_entry("mem-2")
        self.assertTrue(entry["pinned"])
        self.assertEqual(entry["retention_kind"], "durable")
        self.assertIsNone(entry["expires_at"])
        self.assertEqual(entry["updated_at"], 30.0)
        self.assertEqual(
            entry["links"],
            [
                {
                    "entry_id": "mem-2",
                    "target_kind": "pipeline",
                    "target_ref": "task-1",
                    "created_at": 21.0,
                }
            ],
        )

    def test_memory_entries_purge_expired_transient_rows_on_read(self):
        self.db.save_memory_entry(
            {
                "id": "mem-active",
                "project_key": "/repo",
                "group_name": "g",
                "scope_kind": "group",
                "scope_ref": "g",
                "entry_type": "decision",
                "title": "Keep it",
                "content": "Durable decisions survive retention cleanup.",
                "pinned": False,
                "task_id": "",
                "source_kind": "agent",
                "source_id": "agent-1",
                "source_name": "Worker",
                "retention_kind": "durable",
                "expires_at": None,
                "created_at": 10.0,
                "updated_at": 10.0,
            }
        )
        self.db.save_memory_entry(
            {
                "id": "mem-expired",
                "project_key": "/repo",
                "group_name": "g",
                "scope_kind": "group",
                "scope_ref": "g",
                "entry_type": "note",
                "title": "Stale scratch note",
                "content": "Safe to remove.",
                "pinned": False,
                "task_id": "",
                "source_kind": "agent",
                "source_id": "agent-2",
                "source_name": "Worker 2",
                "retention_kind": "transient",
                "expires_at": 15.0,
                "created_at": 11.0,
                "updated_at": 11.0,
            }
        )

        entries = self.db.load_memory_entries(group_name="g", limit=10, now=20.0)

        self.assertEqual([entry["id"] for entry in entries], ["mem-active"])
        self.assertIsNone(self.db.load_memory_entry("mem-expired", now=20.0))

    def test_init_upgrades_legacy_memory_entries_before_retention_indexes(self):
        legacy_path = Path(self.tmp.name) / "legacy-memory.db"
        conn = sqlite3.connect(str(legacy_path))
        conn.executescript("""
            CREATE TABLE memory_entries (
                id TEXT PRIMARY KEY,
                project_key TEXT NOT NULL DEFAULT '',
                group_name TEXT NOT NULL DEFAULT '',
                scope_kind TEXT NOT NULL,
                scope_ref TEXT NOT NULL DEFAULT '',
                entry_type TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL DEFAULT '',
                pinned INTEGER NOT NULL DEFAULT 0,
                task_id TEXT NOT NULL DEFAULT '',
                source_kind TEXT NOT NULL DEFAULT '',
                source_id TEXT NOT NULL DEFAULT '',
                source_name TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            INSERT INTO memory_entries (
                id, project_key, group_name, scope_kind, scope_ref,
                entry_type, title, content, pinned, task_id,
                source_kind, source_id, source_name, created_at, updated_at
            ) VALUES (
                'mem-1', '/repo', 'g', 'group', 'g',
                'note', 'Legacy note', 'Migrated safely.', 0, '',
                'agent', 'agent-1', 'Worker', 1.0, 1.0
            );
        """)
        conn.commit()
        conn.close()

        upgraded = TorqueDB(legacy_path)
        self.addCleanup(upgraded.close)

        upgraded.init()

        entry = upgraded.load_memory_entry("mem-1")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["retention_kind"], "durable")
        cols = upgraded._conn.execute(
            "PRAGMA table_info(memory_entries)"
        ).fetchall()
        self.assertIn("retention_kind", [row[1] for row in cols])

    def test_init_applies_kinds_schema_migration_once_with_backup(self):
        legacy_path = Path(self.tmp.name) / "legacy-kinds.db"
        conn = sqlite3.connect(str(legacy_path))
        conn.execute("CREATE TABLE keep (id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO keep (id) VALUES ('row-1')")
        conn.commit()
        conn.close()

        migrated = TorqueDB(legacy_path)
        self.addCleanup(migrated.close)

        with self.assertLogs("torque", level="INFO") as cm:
            migrated.init()

        backup_path = legacy_path.with_name("torque.db.pre-kinds.bak")
        joined_logs = "\n".join(cm.output)
        self.assertIn(
            f"migration: created pre-kinds backup at {backup_path}",
            joined_logs,
        )
        self.assertIn(
            f"migration: kinds schema applied (version=1, backup={backup_path})",
            joined_logs,
        )
        self.assertIn(
            "migration: no Engineer found, skipping engineer backfill",
            joined_logs,
        )
        self.assertIn(
            "migration: kinds backfill applied (engineer=None, workers=0, tasks=0)",
            joined_logs,
        )
        self.assertTrue(backup_path.exists())
        self.assertGreater(backup_path.stat().st_size, 0)

        backup_conn = sqlite3.connect(str(backup_path))
        self.addCleanup(backup_conn.close)
        self.assertEqual(
            backup_conn.execute("SELECT id FROM keep").fetchone()[0],
            "row-1",
        )

        agent_cols = {
            row[1]: row
            for row in migrated._conn.execute("PRAGMA table_info(agents)").fetchall()
        }
        for col, default in {
            "kind": "''",
            "role": "''",
            "owner_engineer_id": "''",
            "hired_by_architect_id": "''",
            "persistent": "0",
        }.items():
            self.assertIn(col, agent_cols)
            self.assertEqual(str(agent_cols[col][4]), default)

        task_cols = {
            row[1]: row
            for row in migrated._conn.execute(
                "PRAGMA table_info(board_tasks)"
            ).fetchall()
        }
        for col in (
            "assigned_engineer_id",
            "created_by_architect_id",
            "suggested_action",
        ):
            self.assertIn(col, task_cols)
            self.assertEqual(str(task_cols[col][4]), "''")

        decisions_sql = migrated._conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='decisions'"
        ).fetchone()[0]
        self.assertIn("CREATE TABLE decisions", decisions_sql)
        self.assertIn("architect_id TEXT NOT NULL DEFAULT ''", decisions_sql)
        self.assertIn("linked_engineer_ids TEXT NOT NULL DEFAULT '[]'", decisions_sql)
        self.assertIsNotNone(
            migrated._conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='index' AND name='idx_decisions_architect'"
            ).fetchone()
        )

        version = migrated._conn.execute(
            "SELECT value FROM meta WHERE key='schema_kinds_migration_version'"
        ).fetchone()[0]
        migrated_at = migrated._conn.execute(
            "SELECT value FROM meta WHERE key='schema_kinds_migration_migrated_at'"
        ).fetchone()[0]
        self.assertEqual(version, "4")
        self.assertTrue(migrated_at)

        migrated.save_agent(
            AgentCell(
                id="agent-kinds",
                name="Kinds Worker",
                group="g",
                slug="kinds-worker",
            )
        )
        migrated.save_board_task(
            BoardTask(
                id="task-kinds",
                task="Kinds Task",
                slug="kinds-task",
                group="g",
            )
        )
        loaded = migrated.load_all()
        self.assertEqual(loaded["agents"]["agent-kinds"]["kind"], "")
        self.assertEqual(loaded["agents"]["agent-kinds"]["role"], "")
        self.assertEqual(
            loaded["agents"]["agent-kinds"]["owner_engineer_id"],
            "",
        )
        self.assertFalse(loaded["agents"]["agent-kinds"]["persistent"])
        self.assertEqual(
            loaded["board_tasks"]["task-kinds"]["assigned_engineer_id"],
            "",
        )
        self.assertEqual(
            loaded["board_tasks"]["task-kinds"]["created_by_architect_id"],
            "",
        )
        self.assertEqual(
            loaded["board_tasks"]["task-kinds"]["suggested_action"],
            "",
        )

        backup_mtime_ns = backup_path.stat().st_mtime_ns
        migrated.close()

        rerun = TorqueDB(legacy_path)
        self.addCleanup(rerun.close)
        logger = logging.getLogger("torque")
        handler = logging.Handler()
        captured_messages = []
        handler.emit = lambda record: captured_messages.append(record.getMessage())
        old_level = logger.level
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        try:
            rerun.init()
        finally:
            logger.removeHandler(handler)
            logger.setLevel(old_level)

        self.assertEqual(backup_path.stat().st_mtime_ns, backup_mtime_ns)
        self.assertFalse(
            any("pre-kinds backup" in msg for msg in captured_messages),
            captured_messages,
        )
        self.assertFalse(
            any("kinds schema applied" in msg for msg in captured_messages),
            captured_messages,
        )
        self.assertFalse(
            any("kinds backfill applied" in msg for msg in captured_messages),
            captured_messages,
        )

    def test_init_backfills_empty_worker_kind_rows_once(self):
        path = Path(self.tmp.name) / "worker-kind-backfill.db"
        seeded = TorqueDB(path)
        seeded.init()
        seeded.save_agent(
            AgentCell(
                id="engineer-1",
                name="Courier",
                group="torque",
                slug="courier",
                kind="engineer",
                persistent=True,
            )
        )
        seeded.save_agent(
            AgentCell(
                id="owned-worker",
                name="Owned Worker",
                group="torque",
                slug="owned-worker",
                kind="",
                owner_engineer_id="engineer-1",
                persistent=False,
            )
        )
        seeded.save_agent(
            AgentCell(
                id="user-worker",
                name="User Worker",
                group="torque",
                slug="user-worker",
                kind="",
                persistent=False,
            )
        )
        seeded.save_agent(
            AgentCell(
                id="persistent-empty",
                name="Persistent Empty",
                group="torque",
                slug="persistent-empty",
                kind="",
                persistent=True,
            )
        )
        seeded.close()

        conn = sqlite3.connect(str(path))
        conn.execute(
            "UPDATE meta SET value='3' "
            "WHERE key='schema_kinds_migration_version'"
        )
        conn.commit()
        conn.close()

        migrated = TorqueDB(path)
        self.addCleanup(migrated.close)
        with self.assertLogs("torque", level="INFO") as cm:
            migrated.init()

        self.assertIn(
            "migration: empty worker kind backfill applied (updated=2)",
            "\n".join(cm.output),
        )
        rows = migrated._conn.execute(
            "SELECT id, kind FROM agents ORDER BY id"
        ).fetchall()
        self.assertEqual(
            rows,
            [
                ("engineer-1", "engineer"),
                ("owned-worker", "worker"),
                ("persistent-empty", ""),
                ("user-worker", "worker"),
            ],
        )
        self.assertEqual(
            migrated._conn.execute(
                "SELECT value FROM meta WHERE key='schema_kinds_migration_version'"
            ).fetchone()[0],
            "4",
        )

        migrated.close()
        rerun = TorqueDB(path)
        self.addCleanup(rerun.close)
        rerun.init()
        self.assertEqual(
            rerun._conn.execute(
                "SELECT id, kind FROM agents ORDER BY id"
            ).fetchall(),
            rows,
        )
        self.assertEqual(
            rerun._conn.execute(
                "SELECT value FROM meta WHERE key='schema_kinds_migration_version'"
            ).fetchone()[0],
            "4",
        )

    def test_init_backfills_kinds_with_root_engineer_candidate(self):
        path = self._seed_stage1a_db(
            "kinds-backfill-root.db",
            agents=[
                AgentCell(
                    id="root-1",
                    name="Coordinator",
                    group="torque",
                    slug="coordinator",
                ),
                AgentCell(
                    id="worker-1",
                    name="Worker One",
                    group="torque",
                    slug="worker-one",
                    template="researcher",
                    created_by_engineer_id="root-1",
                ),
                AgentCell(
                    id="other-engineer",
                    name="Engineer",
                    group="alpha",
                    slug="engineer",
                ),
                AgentCell(
                    id="term-1",
                    name="Logs",
                    group="torque",
                    slug="torque:logs",
                    cell_type="terminal",
                ),
            ],
            tasks=[
                BoardTask(id="TORQUE:1", task="Owned task", group="torque"),
                BoardTask(id="ALPHA:1", task="Unowned task", group="alpha"),
            ],
        )

        migrated = TorqueDB(path)
        self.addCleanup(migrated.close)

        with self.assertLogs("torque", level="INFO") as cm:
            migrated.init()

        joined_logs = "\n".join(cm.output)
        self.assertIn(
            "migration: kinds backfill applied (engineer=root-1, workers=2, tasks=2)",
            joined_logs,
        )

        engineer = migrated._conn.execute(
            "SELECT name, slug, kind, role, owner_engineer_id, persistent "
            "FROM agents WHERE id='root-1'"
        ).fetchone()
        self.assertEqual(engineer, ("Engineer-2", "engineer-2", "engineer", "", "", 1))

        worker = migrated._conn.execute(
            "SELECT kind, role, owner_engineer_id, hired_by_architect_id, persistent "
            "FROM agents WHERE id='worker-1'"
        ).fetchone()
        self.assertEqual(worker, ("worker", "researcher", "root-1", "", 0))

        terminal = migrated._conn.execute(
            "SELECT kind, role, owner_engineer_id, persistent "
            "FROM agents WHERE id='term-1'"
        ).fetchone()
        self.assertEqual(terminal, ("terminal", "", "", 0))

        task_rows = migrated._conn.execute(
            "SELECT id, assigned_engineer_id, created_by_architect_id, suggested_action "
            "FROM board_tasks ORDER BY id"
        ).fetchall()
        self.assertEqual(
            task_rows,
            [
                ("ALPHA:1", "", "", ""),
                ("TORQUE:1", "", "", ""),
            ],
        )
        version = migrated._conn.execute(
            "SELECT value FROM meta WHERE key='schema_kinds_migration_version'"
        ).fetchone()[0]
        self.assertEqual(version, "4")

        migrated.close()

        rerun = TorqueDB(path)
        self.addCleanup(rerun.close)
        logger = logging.getLogger("torque")
        handler = logging.Handler()
        captured = []
        handler.emit = lambda record: captured.append(record.getMessage())
        old_level = logger.level
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        try:
            rerun.init()
        finally:
            logger.removeHandler(handler)
            logger.setLevel(old_level)
        self.assertFalse(
            any("kinds backfill applied" in msg for msg in captured),
            captured,
        )

    def test_init_backfills_configured_torque_engineer_without_heuristic_match(self):
        path = self._seed_stage1a_db(
            "kinds-backfill-configured-engineer.db",
            agents=[
                AgentCell(
                    id="root-1",
                    name="Coordinator",
                    group="torque",
                    slug="coordinator",
                ),
                AgentCell(
                    id="agent-1",
                    name="Planner",
                    group="alpha",
                    slug="planner",
                    template="planner",
                ),
            ],
            tasks=[
                BoardTask(id="TORQUE:1", task="Torque task", group="torque"),
                BoardTask(id="ALPHA:1", task="Alpha task", group="alpha"),
            ],
            group_settings={
                "torque": GroupSettings(engineer_agent_id="root-1"),
            },
        )

        migrated = TorqueDB(path)
        self.addCleanup(migrated.close)

        with self.assertLogs("torque", level="INFO") as cm:
            migrated.init()

        joined_logs = "\n".join(cm.output)
        self.assertIn(
            "migration: kinds backfill applied (engineer=root-1, workers=1, tasks=2)",
            joined_logs,
        )
        self.assertNotIn("no Engineer found", joined_logs)
        self.assertEqual(
            migrated._conn.execute(
                "SELECT name, slug, kind, persistent FROM agents WHERE id='root-1'"
            ).fetchone(),
            ("Engineer", "engineer", "engineer", 1),
        )
        self.assertEqual(
            migrated._conn.execute(
                "SELECT kind, role, owner_engineer_id, persistent "
                "FROM agents WHERE id='agent-1'"
            ).fetchone(),
            ("worker", "planner", "", 0),
        )
        self.assertEqual(
            migrated._conn.execute(
                "SELECT id, assigned_engineer_id FROM board_tasks ORDER BY id"
            ).fetchall(),
            [
                ("ALPHA:1", ""),
                ("TORQUE:1", "root-1"),
            ],
        )
        self.assertEqual(
            migrated._conn.execute(
                "SELECT value FROM meta WHERE key='schema_kinds_migration_version'"
            ).fetchone()[0],
            "4",
        )

    def test_init_backfills_existing_agents_without_promoting_when_no_engineer_found(self):
        path = self._seed_stage1a_db(
            "kinds-backfill-no-engineer.db",
            agents=[
                AgentCell(
                    id="agent-1",
                    name="Planner",
                    group="alpha",
                    slug="planner",
                    template="planner",
                ),
                AgentCell(
                    id="agent-2",
                    name="Coordinator",
                    group="torque",
                    slug="coordinator",
                    template="default",
                ),
            ],
            tasks=[BoardTask(id="ALPHA:1", task="Task", group="alpha")],
        )

        migrated = TorqueDB(path)
        self.addCleanup(migrated.close)

        with self.assertLogs("torque", level="INFO") as cm:
            migrated.init()

        joined_logs = "\n".join(cm.output)
        self.assertIn(
            "migration: no Engineer found, skipping engineer backfill",
            joined_logs,
        )
        self.assertIn(
            "migration: kinds backfill applied (engineer=None, workers=2, tasks=1)",
            joined_logs,
        )

        rows = migrated._conn.execute(
            "SELECT id, kind, role, owner_engineer_id, persistent "
            "FROM agents ORDER BY id"
        ).fetchall()
        self.assertEqual(
            rows,
            [
                ("agent-1", "worker", "planner", "", 0),
                ("agent-2", "worker", "default", "", 0),
            ],
        )
        self.assertEqual(
            migrated._conn.execute(
                "SELECT assigned_engineer_id FROM board_tasks WHERE id='task-1'"
                .replace("task-1", "ALPHA:1")
            ).fetchone()[0],
            "",
        )
        self.assertEqual(
            migrated._conn.execute(
                "SELECT value FROM meta WHERE key='schema_kinds_migration_version'"
            ).fetchone()[0],
            "4",
        )

    def test_init_skips_ambiguous_engineer_backfill_without_override(self):
        path = self._seed_stage1a_db(
            "kinds-backfill-ambiguous.db",
            agents=[
                AgentCell(
                    id="engineer-a",
                    name="Engineer Alpha",
                    group="torque",
                    slug="engineer-alpha",
                ),
                AgentCell(
                    id="engineer-b",
                    name="Bob",
                    group="torque",
                    slug="bob",
                    template="engineer",
                ),
                AgentCell(
                    id="worker-1",
                    name="Worker",
                    group="torque",
                    slug="worker",
                    created_by_engineer_id="engineer-a",
                ),
            ],
            tasks=[BoardTask(id="TORQUE:1", task="Task", group="torque")],
        )

        migrated = TorqueDB(path)
        self.addCleanup(migrated.close)

        with self.assertLogs("torque", level="ERROR") as cm:
            migrated.init()

        joined_logs = "\n".join(cm.output)
        self.assertIn("multiple Engineer candidates found", joined_logs)
        self.assertIn("engineer-a", joined_logs)
        self.assertIn("engineer-b", joined_logs)

        self.assertEqual(
            migrated._conn.execute(
                "SELECT value FROM meta WHERE key='schema_kinds_migration_version'"
            ).fetchone()[0],
            "1",
        )
        rows = migrated._conn.execute(
            "SELECT id, kind, role, owner_engineer_id, persistent "
            "FROM agents ORDER BY id"
        ).fetchall()
        self.assertEqual(
            rows,
            [
                ("engineer-a", "", "", "", 0),
                ("engineer-b", "", "", "", 0),
                ("worker-1", "", "", "", 0),
            ],
        )
        self.assertEqual(
            migrated._conn.execute(
                "SELECT assigned_engineer_id FROM board_tasks WHERE id='task-1'"
                .replace("task-1", "TORQUE:1")
            ).fetchone()[0],
            "",
        )

    def test_init_uses_configured_torque_engineer_to_disambiguate_candidates(self):
        path = self._seed_stage1a_db(
            "kinds-backfill-configured-disambiguation.db",
            agents=[
                AgentCell(
                    id="engineer-a",
                    name="Engineer Alpha",
                    group="torque",
                    slug="engineer-alpha",
                ),
                AgentCell(
                    id="engineer-b",
                    name="Bob",
                    group="torque",
                    slug="bob",
                    template="engineer",
                ),
                AgentCell(
                    id="worker-1",
                    name="Worker",
                    group="torque",
                    slug="worker",
                    template="researcher",
                    created_by_engineer_id="engineer-b",
                ),
            ],
            tasks=[BoardTask(id="TORQUE:1", task="Task", group="torque")],
            group_settings={
                "torque": GroupSettings(engineer_agent_id="engineer-b"),
            },
        )

        migrated = TorqueDB(path)
        self.addCleanup(migrated.close)

        with self.assertLogs("torque", level="INFO") as cm:
            migrated.init()

        joined_logs = "\n".join(cm.output)
        self.assertIn(
            "migration: kinds backfill applied (engineer=engineer-b, workers=2, tasks=1)",
            joined_logs,
        )
        self.assertNotIn("multiple Engineer candidates found", joined_logs)
        self.assertEqual(
            migrated._conn.execute(
                "SELECT name, slug, kind, persistent FROM agents WHERE id='engineer-b'"
            ).fetchone(),
            ("Engineer", "engineer", "engineer", 1),
        )
        self.assertEqual(
            migrated._conn.execute(
                "SELECT kind, role, owner_engineer_id, persistent "
                "FROM agents WHERE id='engineer-a'"
            ).fetchone(),
            ("worker", "", "", 0),
        )
        self.assertEqual(
            migrated._conn.execute(
                "SELECT assigned_engineer_id FROM board_tasks WHERE id='TORQUE:1'"
            ).fetchone()[0],
            "engineer-b",
        )
        self.assertEqual(
            migrated._conn.execute(
                "SELECT value FROM meta WHERE key='schema_kinds_migration_version'"
            ).fetchone()[0],
            "4",
        )

    def test_init_uses_env_override_for_ambiguous_engineer_backfill(self):
        path = self._seed_stage1a_db(
            "kinds-backfill-override.db",
            agents=[
                AgentCell(
                    id="engineer-a",
                    name="Engineer Alpha",
                    group="torque",
                    slug="engineer-alpha",
                ),
                AgentCell(
                    id="engineer-b",
                    name="Bob",
                    group="torque",
                    slug="bob",
                    template="engineer",
                ),
                AgentCell(
                    id="worker-1",
                    name="Worker",
                    group="torque",
                    slug="worker",
                    template="researcher",
                    created_by_engineer_id="engineer-b",
                ),
            ],
            tasks=[BoardTask(id="TORQUE:1", task="Task", group="torque")],
        )

        migrated = TorqueDB(path)
        self.addCleanup(migrated.close)

        with mock.patch.dict("os.environ", {"TORQUE_MIGRATE_ENGINEER_ID": "engineer-b"}):
            with self.assertLogs("torque", level="INFO") as cm:
                migrated.init()

        joined_logs = "\n".join(cm.output)
        self.assertIn(
            "migration: kinds backfill applied (engineer=engineer-b, workers=2, tasks=1)",
            joined_logs,
        )

        engineer = migrated._conn.execute(
            "SELECT name, slug, kind, persistent FROM agents WHERE id='engineer-b'"
        ).fetchone()
        self.assertEqual(engineer, ("Engineer", "engineer", "engineer", 1))
        self.assertEqual(
            migrated._conn.execute(
                "SELECT kind, role, owner_engineer_id, persistent "
                "FROM agents WHERE id='worker-1'"
            ).fetchone(),
            ("worker", "researcher", "engineer-b", 0),
        )
        self.assertEqual(
            migrated._conn.execute(
                "SELECT assigned_engineer_id FROM board_tasks WHERE id='task-1'"
                .replace("task-1", "TORQUE:1")
            ).fetchone()[0],
            "",
        )
        self.assertEqual(
            migrated._conn.execute(
                "SELECT value FROM meta WHERE key='schema_kinds_migration_version'"
            ).fetchone()[0],
            "4",
        )

    def test_init_backfills_task_assignments_from_group_settings_engineer(self):
        path = self._seed_stage1a_db(
            "kinds-backfill-task-assignment.db",
            agents=[
                AgentCell(
                    id="root-1",
                    name="Coordinator",
                    group="torque",
                    slug="coordinator",
                ),
                AgentCell(
                    id="worker-1",
                    name="Worker",
                    group="alpha",
                    slug="worker",
                    template="researcher",
                ),
            ],
            tasks=[
                BoardTask(id="TORQUE:1", task="Torque task", group="torque"),
                BoardTask(id="TORQUE:2", task="Second torque task", group="torque"),
                BoardTask(id="ALPHA:1", task="Alpha task", group="alpha"),
            ],
            group_settings={
                "torque": GroupSettings(engineer_agent_id="root-1"),
            },
        )

        migrated = TorqueDB(path)
        self.addCleanup(migrated.close)
        migrated.init()

        self.assertEqual(
            migrated._conn.execute(
                "SELECT id, assigned_engineer_id FROM board_tasks ORDER BY id"
            ).fetchall(),
            [
                ("ALPHA:1", ""),
                ("TORQUE:1", "root-1"),
                ("TORQUE:2", "root-1"),
            ],
        )
        self.assertEqual(
            migrated._conn.execute(
                "SELECT value FROM meta WHERE key=?",
                ("schema_kinds_task_assignment_fixup_applied",),
            ).fetchone()[0],
            "1",
        )

    def test_init_ignores_stale_group_settings_engineer_without_engineer(self):
        path = self._seed_stage1a_db(
            "kinds-backfill-stale-group-engineer.db",
            agents=[
                AgentCell(
                    id="worker-1",
                    name="Worker",
                    group="alpha",
                    slug="worker",
                ),
            ],
            tasks=[BoardTask(id="TORQUE:1", task="Torque task", group="torque")],
            group_settings={
                "torque": GroupSettings(engineer_agent_id="stale-id"),
            },
        )

        migrated = TorqueDB(path)
        self.addCleanup(migrated.close)

        with self.assertLogs("torque", level="INFO") as cm:
            migrated.init()

        self.assertIn(
            "migration: ignoring stale engineer_agent_id='stale-id' for group='torque'",
            "\n".join(cm.output),
        )
        self.assertEqual(
            migrated._conn.execute(
                "SELECT assigned_engineer_id FROM board_tasks WHERE id='TORQUE:1'"
            ).fetchone()[0],
            "",
        )
        self.assertEqual(
            migrated._conn.execute(
                "SELECT value FROM meta WHERE key='schema_kinds_migration_version'"
            ).fetchone()[0],
            "4",
        )

    def test_init_ignores_stale_group_settings_engineer_with_heuristic_engineer(self):
        path = self._seed_stage1a_db(
            "kinds-backfill-stale-group-engineer-heuristic.db",
            agents=[
                AgentCell(
                    id="root-1",
                    name="Engineer",
                    group="torque",
                    slug="engineer",
                ),
                AgentCell(
                    id="worker-1",
                    name="Worker",
                    group="torque",
                    slug="worker",
                    created_by_engineer_id="root-1",
                ),
            ],
            tasks=[BoardTask(id="TORQUE:1", task="Torque task", group="torque")],
            group_settings={
                "torque": GroupSettings(engineer_agent_id="stale-id"),
            },
        )

        migrated = TorqueDB(path)
        self.addCleanup(migrated.close)

        with self.assertLogs("torque", level="WARNING") as cm:
            migrated.init()

        self.assertIn(
            "migration: ignoring stale engineer_agent_id='stale-id' for group='torque'",
            "\n".join(cm.output),
        )
        self.assertEqual(
            migrated._conn.execute(
                "SELECT kind, persistent FROM agents WHERE id='root-1'"
            ).fetchone(),
            ("engineer", 1),
        )
        self.assertEqual(
            migrated._conn.execute(
                "SELECT assigned_engineer_id FROM board_tasks WHERE id='TORQUE:1'"
            ).fetchone()[0],
            "",
        )
        self.assertEqual(
            migrated._conn.execute(
                "SELECT value FROM meta WHERE key='schema_kinds_migration_version'"
            ).fetchone()[0],
            "4",
        )

    def test_init_fixes_version2_unassigned_tasks_from_group_settings(self):
        path = self._seed_stage1a_db(
            "kinds-backfill-fixup.db",
            agents=[
                AgentCell(
                    id="root-1",
                    name="Engineer",
                    group="torque",
                    slug="engineer",
                    kind="engineer",
                    persistent=True,
                ),
                AgentCell(
                    id="worker-1",
                    name="Worker",
                    group="alpha",
                    slug="worker",
                    template="researcher",
                    kind="worker",
                    role="researcher",
                ),
            ],
            tasks=[
                BoardTask(id="TORQUE:1", task="Needs fixup", group="torque"),
                BoardTask(
                    id="ALPHA:1",
                    task="Already assigned",
                    group="alpha",
                    assigned_engineer_id="explicit-owner",
                ),
            ],
            group_settings={
                "torque": GroupSettings(engineer_agent_id="root-1"),
            },
        )
        conn = sqlite3.connect(str(path))
        conn.execute(
            "UPDATE meta SET value='2' WHERE key='schema_kinds_migration_version'"
        )
        conn.execute(
            "DELETE FROM meta WHERE key='schema_kinds_task_assignment_fixup_applied'"
        )
        conn.execute(
            "UPDATE agents SET kind='engineer', persistent=1 WHERE id='root-1'"
        )
        conn.execute(
            "UPDATE agents SET kind='worker', role='researcher' WHERE id='worker-1'"
        )
        conn.execute(
            "UPDATE board_tasks SET assigned_engineer_id='explicit-owner' "
            "WHERE id='ALPHA:1'"
        )
        conn.execute(
            "UPDATE board_tasks SET assigned_engineer_id='' WHERE id='TORQUE:1'"
        )
        conn.commit()
        conn.close()

        migrated = TorqueDB(path)
        self.addCleanup(migrated.close)

        with self.assertLogs("torque", level="INFO") as cm:
            migrated.init()

        self.assertIn(
            "migration: kinds task assignment fixup applied (updated=1)",
            "\n".join(cm.output),
        )
        self.assertEqual(
            migrated._conn.execute(
                "SELECT id, assigned_engineer_id FROM board_tasks ORDER BY id"
            ).fetchall(),
            [
                ("ALPHA:1", "explicit-owner"),
                ("TORQUE:1", "root-1"),
            ],
        )
        self.assertEqual(
            migrated._conn.execute(
                "SELECT value FROM meta WHERE key=?",
                ("schema_kinds_task_assignment_fixup_applied",),
            ).fetchone()[0],
            "1",
        )

    def test_init_fixup_ignores_stale_group_settings_engineer_for_unassigned_tasks(self):
        path = self._seed_stage1a_db(
            "kinds-backfill-fixup-stale-group-engineer.db",
            agents=[
                AgentCell(
                    id="root-1",
                    name="Engineer",
                    group="torque",
                    slug="engineer",
                    kind="engineer",
                    persistent=True,
                ),
            ],
            tasks=[BoardTask(id="TORQUE:1", task="Needs fixup", group="torque")],
            group_settings={
                "torque": GroupSettings(engineer_agent_id="stale-id"),
            },
        )
        conn = sqlite3.connect(str(path))
        conn.execute(
            "UPDATE meta SET value='2' WHERE key='schema_kinds_migration_version'"
        )
        conn.execute(
            "DELETE FROM meta WHERE key='schema_kinds_task_assignment_fixup_applied'"
        )
        conn.execute(
            "UPDATE agents SET kind='engineer', persistent=1 WHERE id='root-1'"
        )
        conn.execute(
            "UPDATE board_tasks SET assigned_engineer_id='' WHERE id='TORQUE:1'"
        )
        conn.commit()
        conn.close()

        migrated = TorqueDB(path)
        self.addCleanup(migrated.close)

        with self.assertLogs("torque", level="INFO") as cm:
            migrated.init()

        joined_logs = "\n".join(cm.output)
        self.assertIn(
            "migration: ignoring stale engineer_agent_id='stale-id' for group='torque'",
            joined_logs,
        )
        self.assertIn(
            "migration: kinds task assignment fixup applied (updated=0)",
            joined_logs,
        )
        self.assertEqual(
            migrated._conn.execute(
                "SELECT assigned_engineer_id FROM board_tasks WHERE id='TORQUE:1'"
            ).fetchone()[0],
            "",
        )
        self.assertEqual(
            migrated._conn.execute(
                "SELECT value FROM meta WHERE key=?",
                ("schema_kinds_task_assignment_fixup_applied",),
            ).fetchone()[0],
            "1",
        )

class TorqueDBAsyncWriteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = TorqueDB(Path(self.tmp.name) / "torque.db")
        self.db.init()
        self.db.enable_async_writes(True)

    async def asyncTearDown(self):
        await self.db.close_async_writes()
        self.db.close()
        self.tmp.cleanup()

    async def test_deferred_agent_saves_preserve_fifo_final_state(self):
        cell = AgentCell(id="agent-1", name="Agent", group="g")

        for index in range(100):
            cell.status = f"state-{index}"
            cell.last_progress_at = float(index)
            self.db.save_agent_deferred(cell)

        await asyncio.wait_for(self.db.flush_async_writes(), timeout=5.0)

        loaded = self.db.load_all()["agents"]["agent-1"]
        self.assertEqual(loaded["status"], "state-99")
        self.assertEqual(loaded["last_progress_at"], 99.0)

    async def test_deferred_agent_save_returns_before_sync_write_runs(self):
        cell = AgentCell(id="agent-1", name="Agent", group="g")
        original_save_agent = TorqueDB.save_agent

        def slow_save_agent(db_self, saved_cell):
            time.sleep(0.15)
            return original_save_agent(db_self, saved_cell)

        with mock.patch.object(TorqueDB, "save_agent", slow_save_agent):
            started = time.monotonic()
            self.db.save_agent_deferred(cell)
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, 0.05)

            await asyncio.wait_for(self.db.flush_async_writes(), timeout=5.0)

        self.assertIn("agent-1", self.db.load_all()["agents"])

    async def test_delete_board_task_is_ordered_after_deferred_save(self):
        task = BoardTask(id="TORQUE:race", task="Race", group="g")
        original_save_board_task = TorqueDB.save_board_task

        def slow_save_board_task(db_self, saved_task):
            time.sleep(0.15)
            return original_save_board_task(db_self, saved_task)

        with mock.patch.object(TorqueDB, "save_board_task", slow_save_board_task):
            self.db.save_board_task_deferred(task)
            self.db.delete_board_task(task.id)
            await asyncio.wait_for(self.db.flush_async_writes(), timeout=5.0)

        self.assertFalse(self.db.board_task_exists(task.id))

    async def test_delete_agent_is_ordered_after_deferred_save(self):
        cell = AgentCell(id="agent-race", name="Agent", group="g")
        original_save_agent = TorqueDB.save_agent

        def slow_save_agent(db_self, saved_cell):
            time.sleep(0.15)
            return original_save_agent(db_self, saved_cell)

        with mock.patch.object(TorqueDB, "save_agent", slow_save_agent):
            self.db.save_agent_deferred(cell)
            self.db.delete_agent(cell.id)
            await asyncio.wait_for(self.db.flush_async_writes(), timeout=5.0)

        self.assertNotIn(cell.id, self.db.load_all()["agents"])

    async def test_delete_group_clears_queued_group_settings_save(self):
        self.db.save_group("stale", 0)
        original_save_group_settings = TorqueDB.save_group_settings

        def slow_save_group_settings(db_self, group_name, settings):
            time.sleep(0.15)
            return original_save_group_settings(db_self, group_name, settings)

        with mock.patch.object(
            TorqueDB,
            "save_group_settings",
            slow_save_group_settings,
        ):
            self.db.defer_write(
                "group_settings",
                "save_group_settings",
                "stale",
                GroupSettings(notifications=True),
            )
            self.db.delete_group("stale")
            await asyncio.wait_for(self.db.flush_async_writes(), timeout=5.0)

        loaded = self.db.load_all()
        self.assertNotIn("stale", loaded["groups"])
        self.assertNotIn("stale", loaded["group_settings"])

    async def test_memory_db_deferred_agent_save_falls_back_to_sync(self):
        db = TorqueDB(Path(":memory:"))
        db.init()
        db.enable_async_writes(True)
        try:
            cell = AgentCell(id="agent-memory", name="Agent", group="g")
            db.save_agent_deferred(cell)
            await asyncio.wait_for(db.flush_async_writes(), timeout=5.0)

            self.assertIn(cell.id, db.load_all()["agents"])
        finally:
            await db.close_async_writes()
            db.close()

    async def test_async_wrappers_return_saved_rows_after_queue_drain(self):
        task = BoardTask(id="TORQUE:1", task="Ship it", group="g")
        await self.db.save_board_task_async(task)
        task.lane = "Done"
        await self.db.save_board_task_async(task)
        self.assertEqual(
            self.db.load_all()["board_tasks"]["TORQUE:1"]["lane"],
            "Done",
        )

        decision = await self.db.save_decision_async({
            "id": "decision-1",
            "architect_id": "architect-1",
            "title": "Choose path",
            "rationale": "Fastest safe option",
            "status": "proposed",
        })
        self.assertEqual(decision["id"], "decision-1")
        updated = await self.db.save_decision_async({
            "id": "decision-1",
            "status": "accepted",
        })
        self.assertEqual(updated["status"], "accepted")

        pending_hire = await self.db.save_pending_hire_async({
            "id": "hire-1",
            "architect_id": "architect-1",
            "requested_name": "Engineer",
            "status": "pending",
        })
        self.assertEqual(pending_hire["status"], "pending")
        resolved = await self.db.save_pending_hire_async({
            "id": "hire-1",
            "status": "approved",
            "created_engineer_id": "engineer-1",
        })
        self.assertEqual(resolved["status"], "approved")
        self.assertEqual(resolved["created_engineer_id"], "engineer-1")

        await self.db.save_engineer_settings_async(
            "g",
            {
                "group": "g",
                "pending_question": "Need approval",
                "pending_question_actor_id": "eng-1",
                "pending_note": "FYI",
                "pending_note_kind": "note",
                "pending_note_set_at": 222.0,
                "pending_note_actor_id": "eng-1",
                "paused": True,
            },
        )
        engineer_settings = self.db.load_all_engineer_settings()["g"]
        self.assertEqual(engineer_settings["pending_question"], "Need approval")
        self.assertEqual(engineer_settings["pending_question_actor_id"], "eng-1")
        self.assertEqual(engineer_settings["pending_note"], "FYI")
        self.assertEqual(engineer_settings["pending_note_kind"], "note")
        self.assertEqual(engineer_settings["pending_note_set_at"], 222.0)
        self.assertEqual(engineer_settings["pending_note_actor_id"], "eng-1")
        self.assertTrue(engineer_settings["paused"])

        self.db.defer_write(
            "group_settings",
            "save_group_settings",
            "g",
            GroupSettings(notifications=True),
        )
        self.db.defer_write(
            "global_settings",
            "save_global_settings",
            GlobalSettings(xterm_scrollback=4321),
        )
        await asyncio.wait_for(self.db.flush_async_writes(), timeout=5.0)
        loaded = self.db.load_all()
        self.assertTrue(loaded["group_settings"]["g"]["notifications"])
        self.assertEqual(loaded["global_settings"]["xterm_scrollback"], 4321)

    async def test_state_delta_broadcast_uses_memory_before_deferred_save_flush(self):
        class DummyWebSocket:
            def __init__(self):
                self.messages = []

            async def send_str(self, message):
                self.messages.append(message)

        state = MatrixState(db=self.db)
        cell = AgentCell(id="agent-1", name="Agent", group="g")
        state.agents[cell.id] = cell
        state.groups["g"] = [cell.id]
        ws = DummyWebSocket()
        state._ws_clients.add(ws)

        cell.status = "running"
        state._emit_agent(cell)
        state._db_save_agent(cell)
        await state.broadcast()

        payload = json.loads(ws.messages[-1])
        self.assertEqual(payload["type"], "delta")
        self.assertEqual(payload["ops"][0]["op"], "agent_upsert")
        self.assertEqual(payload["ops"][0]["status"], "running")

        await asyncio.wait_for(self.db.flush_async_writes(), timeout=5.0)
        loaded = self.db.load_all()["agents"]["agent-1"]
        self.assertEqual(loaded["status"], "running")

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub

install_aiohttp_stub(include_json_helpers=True)

from torque.ai_embeddings import EmbeddingResult
from torque.db import TorqueDB
from torque.mcp_tools_shared import dispatch_scoped_tool
from torque.state import AgentCell, BoardTask, GlobalSettings, MatrixState


class FakeEmbeddingService:
    def __init__(self, vector=None):
        self.vector = list(vector or [1.0, 0.0])
        self.calls = []
        self.shutdown_called = False

    async def embed_texts(self, model_id, texts):
        self.calls.append((model_id, list(texts)))
        return EmbeddingResult(
            model_id=model_id,
            dims=len(self.vector),
            vectors=[list(self.vector) for _text in texts],
        )

    async def shutdown(self):
        self.shutdown_called = True


def install_fake_vec_table(db: TorqueDB):
    def fake_create(conn, dims, *, recreate=False):
        conn.execute(
            "CREATE TABLE IF NOT EXISTS ai_embedding_vec "
            "(rowid INTEGER PRIMARY KEY, embedding TEXT NOT NULL)"
        )

    db.ai_create_embedding_vec_table = fake_create


class SemanticRecallTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addAsyncCleanup(self._cleanup_tmp)
        self.db = TorqueDB(Path(self.tmp.name) / "torque.db")
        self.db.init()
        self.addAsyncCleanup(self._close_db)
        install_fake_vec_table(self.db)
        self.state = MatrixState(db=self.db)
        self.model = "model-a"
        self.state.global_settings = GlobalSettings(
            ai_enabled=True,
            ai_embedding_model=self.model,
        )
        self.state.ai_embedding_service = FakeEmbeddingService()
        self.state.ai_recall_sqlite_vec_loader = lambda _conn: None
        self._mark_index_ready()

    async def _cleanup_tmp(self):
        self.tmp.cleanup()

    async def _close_db(self):
        self.db.close()

    def _mark_index_ready(self, *, model=None, status="ready", rebuild_required=False):
        model = model or self.model
        self.db.ai_update_index_state(
            desired_model_id=model,
            active_model_id=model,
            active_dims=2,
            status=status,
            rebuild_required=rebuild_required,
        )
        conn = self.db.open_ai_index_connection()
        try:
            with conn:
                self.db.ai_create_embedding_vec_table(conn, 2)
        finally:
            conn.close()

    def _add_agent(self, agent_id, kind, *, group="G", hired_by_architect_id=""):
        cell = AgentCell(
            id=agent_id,
            name=agent_id,
            group=group,
            cell_type="agent",
            kind=kind,
            hired_by_architect_id=hired_by_architect_id,
        )
        self.state.agents[agent_id] = cell
        self.state.groups.setdefault(group, [])
        if agent_id not in self.state.groups[group]:
            self.state.groups[group].append(agent_id)
        self.db.save_agent(cell)
        return cell

    def _add_task(
        self,
        task_id,
        title,
        *,
        group="G",
        assigned_engineer_id="",
        created_by_architect_id="",
        created_by_engineer_id="",
    ):
        task = BoardTask(
            id=task_id,
            task=title,
            group=group,
            description=title + " description",
            assigned_engineer_id=assigned_engineer_id,
            created_by_architect_id=created_by_architect_id,
            created_by_engineer_id=created_by_engineer_id,
        )
        self.state.board_tasks[task_id] = task
        self.db.save_board_task(task)
        return task

    def _seed_source(
        self,
        source_key,
        *,
        source_type,
        source_id,
        text,
        vector=None,
        group="G",
        owner_kind="",
        owner_id="",
        participant_ids=None,
        visibility_json=None,
        title="",
        updated_at="100",
    ):
        vector = list(vector or [1.0, 0.0])
        participant_ids = list(participant_ids or [])
        visibility_json = dict(visibility_json or {})
        conn = self.db.open_ai_index_connection()
        try:
            with conn:
                self.db.ai_create_embedding_vec_table(conn, 2)
                self.db.ai_upsert_embedding_source(
                    {
                        "source_key": source_key,
                        "source_type": source_type,
                        "source_id": source_id,
                        "group_name": group,
                        "owner_kind": owner_kind,
                        "owner_id": owner_id,
                        "participant_ids": participant_ids,
                        "visibility_json": visibility_json,
                        "title": title,
                        "source_updated_at": updated_at,
                        "content_hash": source_key + "-hash",
                    },
                    conn=conn,
                    commit=False,
                )
                self.db.ai_replace_source_chunks(
                    source_key,
                    [
                        {
                            "chunk_index": 0,
                            "text": text,
                            "chunk_hash": source_key + "-chunk",
                            "vector": vector,
                        }
                    ],
                    conn=conn,
                    model_id=self.model,
                    dims=2,
                    content_hash=source_key + "-hash",
                )
        finally:
            conn.close()

    async def _call_engineer(self, caller_id, args):
        async def handle_command(_payload):
            self.fail("semantic recall should not call handle_command")

        text, is_error = await dispatch_scoped_tool(
            "engineer_semantic_recall",
            args,
            handle_command,
            self.state,
            tool_prefix="engineer_",
            caller_kind="engineer",
            caller_id=caller_id,
        )
        self.assertFalse(is_error, text)
        return json.loads(text)

    async def _call_architect(self, caller_id, args):
        async def handle_command(_payload):
            self.fail("semantic recall should not call handle_command")

        text, is_error = await dispatch_scoped_tool(
            "architect_semantic_recall",
            args,
            handle_command,
            self.state,
            tool_prefix="architect_",
            caller_kind="architect",
            caller_id=caller_id,
        )
        self.assertFalse(is_error, text)
        return json.loads(text)

    def _result_text(self, payload):
        return "\n".join(item["snippet"] for item in payload["results"])

    async def test_architect_cross_architect_journals_and_decisions_denied(self):
        arch_a = self._add_agent("arch-a", "architect")
        arch_b = self._add_agent("arch-b", "architect")
        self._seed_source(
            "architect_journal:arch-a:1",
            source_type="architect_journal",
            source_id=arch_a.id,
            text="ARCH_A_JOURNAL_VISIBLE",
            owner_kind="architect",
            owner_id=arch_a.id,
            participant_ids=[arch_a.id],
        )
        self._seed_source(
            "architect_journal:arch-b:1",
            source_type="architect_journal",
            source_id=arch_b.id,
            text="ARCH_B_JOURNAL_DENIED",
            owner_kind="architect",
            owner_id=arch_b.id,
            participant_ids=[arch_b.id],
        )
        self._seed_source(
            "decision:decision-a",
            source_type="decision",
            source_id="decision-a",
            text="ARCH_A_DECISION_VISIBLE",
            owner_kind="architect",
            owner_id=arch_a.id,
            participant_ids=[arch_a.id],
        )
        self._seed_source(
            "decision:decision-b",
            source_type="decision",
            source_id="decision-b",
            text="ARCH_B_DECISION_DENIED",
            owner_kind="architect",
            owner_id=arch_b.id,
            participant_ids=[arch_b.id],
        )

        payload = await self._call_architect(arch_a.id, {"query": "scope", "limit": 10})
        text = self._result_text(payload)

        self.assertIn("ARCH_A_JOURNAL_VISIBLE", text)
        self.assertIn("ARCH_A_DECISION_VISIBLE", text)
        self.assertNotIn("ARCH_B_JOURNAL_DENIED", text)
        self.assertNotIn("ARCH_B_DECISION_DENIED", text)

    async def test_engineer_journal_scope_denies_cross_group_and_non_peer_author(self):
        arch_a = self._add_agent("arch-a", "architect", group="G")
        arch_b = self._add_agent("arch-b", "architect", group="G")
        eng_a = self._add_agent(
            "eng-a",
            "engineer",
            group="G",
            hired_by_architect_id=arch_a.id,
        )
        eng_non_peer = self._add_agent(
            "eng-non-peer",
            "engineer",
            group="G",
            hired_by_architect_id=arch_b.id,
        )
        eng_other_group = self._add_agent(
            "eng-other-group",
            "engineer",
            group="Other",
            hired_by_architect_id=arch_a.id,
        )
        self._seed_source(
            "engineer_journal:own",
            source_type="engineer_journal",
            source_id="own",
            text="ENG_A_JOURNAL_VISIBLE",
            owner_kind="engineer",
            owner_id=eng_a.id,
            participant_ids=[eng_a.id],
        )
        self._seed_source(
            "engineer_journal:non-peer",
            source_type="engineer_journal",
            source_id="non-peer",
            text="NON_PEER_JOURNAL_DENIED",
            owner_kind="engineer",
            owner_id=eng_non_peer.id,
            participant_ids=[eng_non_peer.id],
        )
        self._seed_source(
            "engineer_journal:other-group",
            source_type="engineer_journal",
            source_id="other-group",
            text="CROSS_GROUP_JOURNAL_DENIED",
            group="Other",
            owner_kind="engineer",
            owner_id=eng_other_group.id,
            participant_ids=[eng_other_group.id],
        )

        payload = await self._call_engineer(eng_a.id, {"query": "journal", "limit": 10})
        text = self._result_text(payload)

        self.assertIn("ENG_A_JOURNAL_VISIBLE", text)
        self.assertNotIn("NON_PEER_JOURNAL_DENIED", text)
        self.assertNotIn("CROSS_GROUP_JOURNAL_DENIED", text)

    async def test_peer_thread_recall_uses_same_architect_inspect_grant_path(self):
        arch_a = self._add_agent("arch-a", "architect")
        arch_b = self._add_agent("arch-b", "architect")
        eng_a = self._add_agent(
            "eng-a",
            "engineer",
            hired_by_architect_id=arch_a.id,
        )
        eng_b = self._add_agent(
            "eng-b",
            "engineer",
            hired_by_architect_id=arch_a.id,
        )
        eng_context = self._add_agent(
            "eng-context",
            "engineer",
            hired_by_architect_id=arch_a.id,
        )
        eng_other_arch = self._add_agent(
            "eng-other-arch",
            "engineer",
            hired_by_architect_id=arch_b.id,
        )
        task = self._add_task(
            "TORQUE:900",
            "Context task",
            assigned_engineer_id=eng_context.id,
            created_by_architect_id=arch_a.id,
        )
        self.db.save_agent_peer_message(
            {
                "id": "msg-peer",
                "thread_id": "thread-peer",
                "group_name": "G",
                "sender_id": eng_a.id,
                "sender_kind": "engineer",
                "sender_name": "A",
                "recipient_id": eng_b.id,
                "recipient_kind": "engineer",
                "recipient_name": "B",
                "message": "PEER_THREAD_SECRET_VISIBLE_TO_SCOPE",
                "message_type": "message",
                "created_at": 100.0,
                "context_task_ids": [task.id],
                "context_engineer_ids": [eng_a.id, eng_b.id],
                "context_snapshot": {
                    "inspect_grant": {
                        "scope": "thread_context",
                        "source_engineer_id": eng_a.id,
                        "recipient_engineer_id": eng_b.id,
                        "supervising_architect_id": arch_a.id,
                    }
                },
            }
        )
        self._seed_source(
            "engineer_peer_thread:thread-peer",
            source_type="engineer_peer_thread",
            source_id="thread-peer",
            text="PEER_THREAD_SECRET_VISIBLE_TO_SCOPE",
            owner_kind="engineer_peer_thread",
            owner_id="thread-peer",
            participant_ids=[eng_a.id, eng_b.id],
            visibility_json={
                "participants": [eng_a.id, eng_b.id],
                "participant_hired_by_architect_ids": [arch_a.id],
                "context_task_ids": [task.id],
            },
        )

        participant_payload = await self._call_engineer(
            eng_a.id,
            {"query": "peer", "limit": 5},
        )
        self.assertIn(
            "PEER_THREAD_SECRET_VISIBLE_TO_SCOPE",
            self._result_text(participant_payload),
        )

        import torque.mcp_tools_shared as shared

        with mock.patch.object(
            shared,
            "_resolve_engineer_peer_filter",
            wraps=shared._resolve_engineer_peer_filter,
        ) as resolve_filter, mock.patch.object(
            shared,
            "_resolve_engineer_peer",
            wraps=shared._resolve_engineer_peer,
        ) as resolve_peer, mock.patch.object(
            shared,
            "_peer_row_involves_engineer",
            wraps=shared._peer_row_involves_engineer,
        ) as involves:
            context_payload = await self._call_engineer(
                eng_context.id,
                {"query": "peer", "limit": 5},
            )

        self.assertIn(
            "PEER_THREAD_SECRET_VISIBLE_TO_SCOPE",
            self._result_text(context_payload),
        )
        self.assertGreater(resolve_filter.call_count, 0)
        self.assertGreater(resolve_peer.call_count, 0)
        self.assertGreater(involves.call_count, 0)

        denied_payload = await self._call_engineer(
            eng_other_arch.id,
            {"query": "peer", "limit": 5},
        )
        self.assertEqual(denied_payload["results"], [])

    async def test_overfetch_filters_invisible_candidates_before_returning(self):
        arch_a = self._add_agent("arch-a", "architect")
        arch_b = self._add_agent("arch-b", "architect")
        eng_a = self._add_agent(
            "eng-a",
            "engineer",
            hired_by_architect_id=arch_a.id,
        )
        eng_b = self._add_agent(
            "eng-b",
            "engineer",
            hired_by_architect_id=arch_b.id,
        )
        for index in range(12):
            self._seed_source(
                f"engineer_journal:invisible-{index}",
                source_type="engineer_journal",
                source_id=f"invisible-{index}",
                text=f"INVISIBLE_HIGH_RANK_{index}",
                vector=[1.0, 0.0],
                owner_kind="engineer",
                owner_id=eng_b.id,
                participant_ids=[eng_b.id],
            )
        self._seed_source(
            "engineer_journal:visible-lower-rank",
            source_type="engineer_journal",
            source_id="visible-lower-rank",
            text="VISIBLE_AFTER_REFETCH",
            vector=[0.0, 1.0],
            owner_kind="engineer",
            owner_id=eng_a.id,
            participant_ids=[eng_a.id],
        )

        payload = await self._call_engineer(eng_a.id, {"query": "needle", "limit": 1})
        text = self._result_text(payload)

        self.assertEqual(len(payload["results"]), 1)
        self.assertIn("VISIBLE_AFTER_REFETCH", text)
        self.assertNotIn("INVISIBLE_HIGH_RANK", text)

    async def test_degrade_states_return_non_error_empty_results(self):
        arch = self._add_agent("arch", "architect")
        self._seed_source(
            "architect_journal:arch:1",
            source_type="architect_journal",
            source_id=arch.id,
            text="visible if ready",
            owner_kind="architect",
            owner_id=arch.id,
            participant_ids=[arch.id],
        )

        self.state.global_settings = GlobalSettings(
            ai_enabled=False,
            ai_embedding_model=self.model,
        )
        disabled = await self._call_architect(arch.id, {"query": "x"})
        self.assertEqual(disabled["status"], "disabled")
        self.assertEqual(disabled["results"], [])

        self.state.global_settings = GlobalSettings(
            ai_enabled=True,
            ai_embedding_model=self.model,
        )
        self.db.ai_update_index_state(status="not_built")
        not_ready = await self._call_architect(arch.id, {"query": "x"})
        self.assertEqual(not_ready["status"], "not_ready")
        self.assertEqual(not_ready["results"], [])

        self.db.ai_update_index_state(
            desired_model_id=self.model,
            active_model_id=self.model,
            active_dims=2,
            status="rebuild_pending",
            rebuild_required=True,
        )
        rebuild = await self._call_architect(arch.id, {"query": "x"})
        self.assertEqual(rebuild["status"], "rebuild_pending")
        self.assertEqual(rebuild["results"], [])

        self.db.ai_update_index_state(
            desired_model_id=self.model,
            active_model_id="other-model",
            active_dims=2,
            status="ready",
            rebuild_required=False,
        )
        mismatch = await self._call_architect(arch.id, {"query": "x"})
        self.assertEqual(mismatch["status"], "model_mismatch")
        self.assertEqual(mismatch["results"], [])

        self.db.ai_update_index_state(
            desired_model_id=self.model,
            active_model_id=self.model,
            active_dims=2,
            status="ready",
            rebuild_required=False,
        )
        delattr(self.state, "ai_recall_sqlite_vec_loader")
        dependency = await self._call_architect(arch.id, {"query": "x"})
        self.assertEqual(dependency["status"], "dependency_missing")
        self.assertEqual(dependency["results"], [])

    async def test_mcp_tools_are_registered_read_only_and_no_worker_recall(self):
        import importlib
        from torque.mcp_retry import is_mcp_write_tool

        mcp_mod = importlib.reload(importlib.import_module("torque.mcp"))
        engineer_names = {tool["name"] for tool in mcp_mod.ENGINEER_TOOLS}
        architect_names = {tool["name"] for tool in mcp_mod.ARCHITECT_TOOLS}
        worker_names = {tool["name"] for tool in mcp_mod.TOOLS}

        self.assertIn("engineer_semantic_recall", engineer_names)
        self.assertIn("architect_semantic_recall", architect_names)
        self.assertNotIn("torque_semantic_recall", worker_names)
        self.assertFalse(is_mcp_write_tool("engineer_semantic_recall"))
        self.assertFalse(is_mcp_write_tool("architect_semantic_recall"))


if __name__ == "__main__":
    unittest.main()

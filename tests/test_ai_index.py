import asyncio
import json
import tempfile
import unittest
from pathlib import Path

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub

install_aiohttp_stub()

from torque.ai_embeddings import EmbeddingDimsResult, EmbeddingResult
from torque.ai_index import AIIndexService, chunk_text, harvest_corpus
from torque.db import TorqueDB
from torque.db_schema import create_ai_embedding_vec_table
from torque.state import AgentCell, BoardTask, GlobalSettings, MatrixState


class FakeEmbeddingService:
    def __init__(self, dims=3):
        self.dims = dims
        self.max_batch_size = 2
        self.probes = []
        self.batches = []
        self.shutdown_called = False

    async def probe_dims(self, model_id):
        self.probes.append(model_id)
        return EmbeddingDimsResult(model_id=model_id, dims=self.dims)

    async def embed_texts(self, model_id, texts):
        self.batches.append((model_id, list(texts)))
        vectors = []
        for text in texts:
            seed = float((sum(ord(ch) for ch in text) % 97) + 1)
            vectors.append([seed + float(i) for i in range(self.dims)])
        return EmbeddingResult(model_id=model_id, dims=self.dims, vectors=vectors)

    async def shutdown(self):
        self.shutdown_called = True


def install_fake_vec_table(db: TorqueDB):
    def fake_create(conn, dims, *, recreate=False):
        conn.execute(
            "CREATE TABLE IF NOT EXISTS ai_embedding_vec "
            "(rowid INTEGER PRIMARY KEY, embedding TEXT NOT NULL)"
        )
    db.ai_create_embedding_vec_table = fake_create


class AIIndexTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addAsyncCleanup(self._cleanup)
        self.data_dir = Path(self.tmp.name) / "data"
        self.data_dir.mkdir()
        self.db = TorqueDB(Path(self.tmp.name) / "torque.db")
        self.db.init()
        self.addAsyncCleanup(self._close_db)
        install_fake_vec_table(self.db)

    async def _cleanup(self):
        self.tmp.cleanup()

    async def _close_db(self):
        self.db.close()

    def _save_agents(self):
        self.db.save_agent(AgentCell(id="arch-1", name="Arch", group="Torque", kind="architect"))
        self.db.save_agent(AgentCell(id="eng-1", name="Eng One", group="Torque", kind="engineer", hired_by_architect_id="arch-1"))
        self.db.save_agent(AgentCell(id="eng-2", name="Eng Two", group="Torque", kind="engineer", hired_by_architect_id="arch-1"))

    def _seed_all_sources(self):
        self._save_agents()
        journal_dir = self.data_dir / "architect_journals"
        journal_dir.mkdir(parents=True, exist_ok=True)
        (journal_dir / "arch-1.jsonl").write_text(
            json.dumps({
                "id": "aj-1",
                "architect_id": "arch-1",
                "timestamp": 100.0,
                "type": "decision",
                "entry": "Architect journal body",
            }) + "\n",
            encoding="utf-8",
        )
        self.db.save_journal_entry(
            "Torque", 101.0, "checkpoint", "Engineer journal body", author_cell_id="eng-1"
        )
        self.db.save_decision({
            "id": "decision-1",
            "architect_id": "arch-1",
            "title": "Provider choice",
            "rationale": "Use local embeddings",
            "status": "accepted",
            "linked_task_ids": ["TORQUE:1"],
            "linked_engineer_ids": ["eng-1"],
        })
        self.db.save_board_task(BoardTask(
            id="TORQUE:1",
            task="Build index",
            group="Torque",
            description="Index task description",
            assigned_engineer_id="eng-1",
            created_by_architect_id="arch-1",
        ))
        self.db.save_agent_peer_message({
            "id": "msg-1",
            "thread_id": "thread-1",
            "group_name": "Torque",
            "sender_id": "eng-1",
            "sender_kind": "engineer",
            "sender_name": "Eng One",
            "recipient_id": "eng-2",
            "recipient_kind": "engineer",
            "recipient_name": "Eng Two",
            "message": "Peer thread body",
            "created_at": 102.0,
            "context_task_ids": ["TORQUE:1"],
            "context_decision_ids": ["decision-1"],
            "context_summary": "Thread context",
        })

    async def _service(self, *, model="model-a", dims=3, state=None):
        if state is None:
            state = MatrixState(db=self.db)
            state.global_settings = GlobalSettings(ai_enabled=True, ai_embedding_model=model)
        return AIIndexService(
            db=self.db,
            state=state,
            embedding_service=FakeEmbeddingService(dims=dims),
            data_dir=self.data_dir,
            debounce_seconds=0.05,
            sqlite_vec_loader=lambda conn: None,
        )

    def test_dynamic_vec_table_helper_uses_known_dims_only(self):
        class Recorder:
            def __init__(self):
                self.sql = []
            def execute(self, sql):
                self.sql.append(sql)
        recorder = Recorder()
        create_ai_embedding_vec_table(recorder, 7)
        self.assertEqual(
            recorder.sql,
            ["CREATE VIRTUAL TABLE IF NOT EXISTS ai_embedding_vec USING vec0(embedding float[7])"],
        )
        with self.assertRaises(ValueError):
            create_ai_embedding_vec_table(recorder, 0)

    def test_harvesters_are_deterministic_and_capture_scope_metadata(self):
        self._seed_all_sources()
        first = harvest_corpus(self.db.db_path, self.data_dir)
        second = harvest_corpus(self.db.db_path, self.data_dir)

        self.assertEqual([s.source_key for s in first], [s.source_key for s in second])
        self.assertEqual([s.content_hash for s in first], [s.content_hash for s in second])
        by_type = {s.source_type: s for s in first}
        self.assertEqual(set(by_type), {
            "architect_journal", "engineer_journal", "decision", "task", "engineer_peer_thread"
        })
        self.assertEqual(by_type["architect_journal"].owner_kind, "architect")
        self.assertEqual(by_type["architect_journal"].owner_id, "arch-1")
        self.assertEqual(by_type["engineer_journal"].owner_kind, "engineer")
        self.assertEqual(by_type["engineer_journal"].owner_id, "eng-1")
        self.assertIn("eng-1", by_type["decision"].participant_ids)
        self.assertEqual(by_type["task"].group_name, "Torque")
        self.assertIn("eng-2", by_type["engineer_peer_thread"].participant_ids)
        self.assertEqual(
            by_type["engineer_peer_thread"].visibility_json["participant_hired_by_architect_ids"],
            ["arch-1"],
        )

    async def test_incremental_scan_marks_stale_and_deleted_by_content_hash(self):
        self._seed_all_sources()
        service = await self._service()
        await service.start(mode="incremental", confirm=False)
        await service._job_task

        sources = {row["source_key"]: row for row in self.db.ai_list_embedding_sources()}
        self.assertEqual(sources["task:TORQUE:1"]["state"], "indexed")
        old_hash = sources["task:TORQUE:1"]["content_hash"]
        self.assertGreater(self.db.ai_get_index_counts()["chunks"], 0)

        task = BoardTask(
            id="TORQUE:1",
            task="Build index changed",
            group="Torque",
            description="Index task description",
            assigned_engineer_id="eng-1",
            created_by_architect_id="arch-1",
        )
        self.db.save_board_task(task)
        self.db.hard_delete_decision("decision-1")
        sources_now = harvest_corpus(self.db.db_path, self.data_dir)
        await asyncio.to_thread(
            service._apply_scan_sync,
            sources_now,
            "model-a",
            {},
            {"deleted": 0},
        )

        refreshed = {row["source_key"]: row for row in self.db.ai_list_embedding_sources()}
        self.assertEqual(refreshed["task:TORQUE:1"]["state"], "stale")
        self.assertNotEqual(refreshed["task:TORQUE:1"]["content_hash"], old_hash)
        self.assertEqual(refreshed["decision:decision-1"]["state"], "deleted")
        self.assertEqual(
            self.db._conn.execute(
                "SELECT COUNT(*) FROM ai_embedding_chunks WHERE source_key='decision:decision-1'"
            ).fetchone()[0],
            0,
        )

    async def test_full_rebuild_clears_old_chunks_vectors_and_updates_model_dims(self):
        self._seed_all_sources()
        service = await self._service(model="model-a", dims=3)
        await service.start(mode="incremental", confirm=False)
        await service._job_task
        first_counts = self.db.ai_get_index_counts()
        self.assertGreater(first_counts["chunks"], 0)
        first_chunk_ids = [row[0] for row in self.db._conn.execute("SELECT id FROM ai_embedding_chunks")]

        state = MatrixState(db=self.db)
        state.global_settings = GlobalSettings(ai_enabled=True, ai_embedding_model="model-b")
        service_b = await self._service(model="model-b", dims=5, state=state)
        await service_b.start(mode="rebuild", confirm=True)
        await service_b._job_task

        index_state = self.db.ai_get_index_state()
        self.assertEqual(index_state["active_model_id"], "model-b")
        self.assertEqual(index_state["active_dims"], 5)
        rows = self.db._conn.execute(
            "SELECT id, embedding_model_id, embedding_dims FROM ai_embedding_chunks"
        ).fetchall()
        self.assertTrue(rows)
        self.assertTrue(all(row[1] == "model-b" and row[2] == 5 for row in rows))
        self.assertNotEqual(first_chunk_ids, [row[0] for row in rows])
        vec_ids = [row[0] for row in self.db._conn.execute("SELECT rowid FROM ai_embedding_vec")]
        chunk_ids = [row[0] for row in rows]
        self.assertEqual(vec_ids, chunk_ids)

    async def test_missing_sqlite_vec_is_typed_failure_not_exception(self):
        self._seed_all_sources()
        # Restore the production virtual-table creator while leaving sqlite_vec
        # uninstalled in the test environment.  The job should record a typed
        # dependency_missing status instead of raising through the daemon path.
        service = AIIndexService(
            db=self.db,
            state=MatrixState(db=self.db),
            embedding_service=FakeEmbeddingService(dims=3),
            data_dir=self.data_dir,
        )
        service.state.global_settings = GlobalSettings(
            ai_enabled=True,
            ai_embedding_model="model-a",
        )

        response = await service.start(mode="incremental", confirm=False)
        self.assertEqual(response["type"], "ai_index_job")
        await service._job_task

        index_state = self.db.ai_get_index_state()
        self.assertEqual(index_state["status"], "dependency_missing")
        self.assertIn("sqlite-vec", index_state["last_error"])

    def test_chunker_is_deterministic(self):
        text = "para1\n\n" + ("x" * 700) + "\n\npara3"
        self.assertEqual(
            chunk_text(text, target_chars=300, overlap_chars=20),
            chunk_text(text, target_chars=300, overlap_chars=20),
        )
        self.assertGreater(len(chunk_text(text, target_chars=300, overlap_chars=20)), 1)


class AIIndexSettingsConfirmTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = TorqueDB(Path(self.tmp.name) / "torque.db")
        self.db.init()
        self.addCleanup(self.db.close)
        install_fake_vec_table(self.db)

    def _seed_indexed_chunk(self):
        self.db.ai_update_index_state(
            desired_model_id="model-a",
            active_model_id="model-a",
            active_dims=3,
            status="ready",
        )
        conn = self.db.open_ai_index_connection()
        try:
            with conn:
                self.db.ai_create_embedding_vec_table(conn, 3)
                self.db.ai_upsert_embedding_source({
                    "source_key": "task:TORQUE:1",
                    "source_type": "task",
                    "source_id": "TORQUE:1",
                    "content_hash": "hash-a",
                }, conn=conn, commit=False)
                self.db.ai_replace_source_chunks(
                    "task:TORQUE:1",
                    [{"chunk_index": 0, "text": "hello", "chunk_hash": "chunk-a", "vector": [1, 2, 3]}],
                    conn=conn,
                    model_id="model-a",
                    dims=3,
                    content_hash="hash-a",
                )
        finally:
            conn.close()

    def test_update_ai_settings_model_change_requires_backend_confirmation(self):
        import importlib
        server = importlib.import_module("torque.server")
        self._seed_indexed_chunk()
        state = MatrixState(db=self.db)
        state.global_settings = GlobalSettings(ai_enabled=True, ai_embedding_model="model-a")

        response = server._apply_ai_settings_update_command(
            state,
            self.db,
            {"settings": {"embeddings": {"model_id": "model-b"}}},
        )

        self.assertEqual(response["type"], "ai_settings_requires_confirmation")
        self.assertEqual(response["reason"], "embedding_model_change")
        self.assertEqual(state.global_settings.ai_embedding_model, "model-a")
        self.assertEqual(self.db.ai_get_index_state()["desired_model_id"], "model-a")

    def test_update_ai_settings_confirm_sets_rebuild_pending_and_job(self):
        import importlib
        server = importlib.import_module("torque.server")
        self._seed_indexed_chunk()
        state = MatrixState(db=self.db)
        state.global_settings = GlobalSettings(ai_enabled=True, ai_embedding_model="model-a")

        response = server._apply_ai_settings_update_command(
            state,
            self.db,
            {
                "settings": {"embeddings": {"model_id": "model-b"}},
                "confirm_embedding_rebuild": True,
            },
        )

        self.assertEqual(response["type"], "ai_settings")
        self.assertEqual(state.global_settings.ai_embedding_model, "model-b")
        index_state = self.db.ai_get_index_state()
        self.assertEqual(index_state["desired_model_id"], "model-b")
        self.assertTrue(index_state["rebuild_required"])
        self.assertEqual(index_state["status"], "rebuild_pending")
        job = self.db.ai_get_current_index_job()
        self.assertIsNotNone(job)
        self.assertEqual(job["mode"], "rebuild")


if __name__ == "__main__":
    unittest.main()

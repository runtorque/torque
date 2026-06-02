import asyncio
import json
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from torque.ai_embeddings import (
    EMBEDDING_PROBE_TEXT,
    EmbeddingDimsResult,
    EmbeddingFailure,
    EmbeddingResult,
    LocalEmbeddingService,
)


ROOT = Path(__file__).resolve().parents[1]


class RecordingExecutor:
    def __init__(self):
        self.shutdown_calls = []

    def shutdown(self, **kwargs):
        self.shutdown_calls.append(dict(kwargs))


class LocalEmbeddingServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addAsyncCleanup(self._cleanup_tmp)

    async def _cleanup_tmp(self):
        self.tmp.cleanup()

    def service(self, **kwargs):
        return LocalEmbeddingService(data_dir=Path(self.tmp.name), **kwargs)

    async def test_embed_texts_dispatches_through_run_in_executor(self):
        loop = asyncio.get_running_loop()
        executor = RecordingExecutor()
        calls = []

        def inline_worker(_cache_dir, _model_id, _texts):
            raise AssertionError("worker must not run inline on the event loop")

        def fake_run_in_executor(executor_arg, func, cache_dir, model_id, texts):
            calls.append({
                "executor": executor_arg,
                "func": func,
                "cache_dir": cache_dir,
                "model_id": model_id,
                "texts": list(texts),
            })
            future = loop.create_future()
            future.set_result({
                "model_id": model_id,
                "dims": 2,
                "vectors": [[1.0, 0.0]],
            })
            return future

        service = self.service(
            executor_factory=lambda: executor,
            worker=inline_worker,
        )
        with mock.patch.object(loop, "run_in_executor", fake_run_in_executor):
            response = await service.embed_texts("fake-model", ["hello"])

        self.assertIsInstance(response, EmbeddingResult)
        self.assertEqual(response.dims, 2)
        self.assertEqual(response.vectors, [[1.0, 0.0]])
        self.assertEqual(len(calls), 1)
        self.assertIs(calls[0]["executor"], executor)
        self.assertIs(calls[0]["func"], inline_worker)
        self.assertEqual(Path(calls[0]["cache_dir"]).name, "ai_models")
        self.assertEqual(calls[0]["model_id"], "fake-model")
        self.assertEqual(calls[0]["texts"], ["hello"])
        await service.shutdown()

    async def test_slow_worker_does_not_block_concurrent_coroutine(self):
        def slow_worker(_cache_dir, model_id, texts):
            time.sleep(0.15)
            return {
                "model_id": model_id,
                "dims": 1,
                "vectors": [[float(len(text))] for text in texts],
            }

        executor = ThreadPoolExecutor(max_workers=1)
        service = self.service(
            executor_factory=lambda: executor,
            worker=slow_worker,
            timeout_seconds=1.0,
        )
        loop = asyncio.get_running_loop()
        start = loop.time()
        embed_task = asyncio.create_task(service.embed_texts("fake-model", ["slow"]))

        await asyncio.sleep(0.02)
        elapsed_while_worker_running = loop.time() - start

        self.assertLess(elapsed_while_worker_running, 0.10)
        self.assertFalse(embed_task.done())
        response = await embed_task
        self.assertIsInstance(response, EmbeddingResult)
        self.assertEqual(response.vectors, [[4.0]])
        await service.shutdown()

    async def test_timeout_returns_typed_failure_and_service_stays_usable(self):
        loop = asyncio.get_running_loop()
        executors = []
        calls = 0

        def executor_factory():
            executor = RecordingExecutor()
            executors.append(executor)
            return executor

        def fake_run_in_executor(_executor, _func, _cache_dir, model_id, _texts):
            nonlocal calls
            calls += 1
            future = loop.create_future()
            if calls == 1:
                return future
            future.set_result({
                "model_id": model_id,
                "dims": 3,
                "vectors": [[1.0, 2.0, 3.0]],
            })
            return future

        service = self.service(
            executor_factory=executor_factory,
            timeout_seconds=0.01,
        )
        with mock.patch.object(loop, "run_in_executor", fake_run_in_executor):
            failure = await service.embed_texts("fake-model", ["slow"])
            success = await service.embed_texts("fake-model", ["ok"])

        self.assertIsInstance(failure, EmbeddingFailure)
        self.assertEqual(failure.kind, "timeout")
        self.assertTrue(failure.retriable)
        self.assertIsInstance(success, EmbeddingResult)
        self.assertEqual(success.dims, 3)
        self.assertEqual(len(executors), 2)
        self.assertEqual(
            executors[0].shutdown_calls,
            [{"wait": False, "cancel_futures": True}],
        )
        await service.shutdown()

    async def test_worker_exception_returns_typed_failure_and_service_stays_usable(self):
        loop = asyncio.get_running_loop()
        calls = 0

        def fake_run_in_executor(_executor, _func, _cache_dir, model_id, _texts):
            nonlocal calls
            calls += 1
            future = loop.create_future()
            if calls == 1:
                future.set_exception(RuntimeError("boom"))
            else:
                future.set_result({
                    "model_id": model_id,
                    "dims": 2,
                    "vectors": [[0.5, 0.5]],
                })
            return future

        service = self.service(executor_factory=RecordingExecutor)
        with mock.patch.object(loop, "run_in_executor", fake_run_in_executor):
            failure = await service.embed_texts("fake-model", ["boom"])
            success = await service.embed_texts("fake-model", ["ok"])

        self.assertIsInstance(failure, EmbeddingFailure)
        self.assertEqual(failure.kind, "worker_error")
        self.assertIsInstance(success, EmbeddingResult)
        self.assertEqual(success.vectors, [[0.5, 0.5]])
        await service.shutdown()

    async def test_probe_dims_uses_fake_worker(self):
        loop = asyncio.get_running_loop()
        captured_texts = []

        def fake_run_in_executor(_executor, _func, _cache_dir, model_id, texts):
            captured_texts.append(list(texts))
            future = loop.create_future()
            future.set_result({
                "model_id": model_id,
                "dims": 4,
                "vectors": [[1.0, 0.0, 0.0, 0.0]],
            })
            return future

        service = self.service(executor_factory=RecordingExecutor)
        with mock.patch.object(loop, "run_in_executor", fake_run_in_executor):
            response = await service.probe_dims("fake-model")

        self.assertIsInstance(response, EmbeddingDimsResult)
        self.assertEqual(response.model_id, "fake-model")
        self.assertEqual(response.dims, 4)
        self.assertEqual(captured_texts, [[EMBEDDING_PROBE_TEXT]])
        await service.shutdown()

    async def test_shutdown_cancels_executor_futures(self):
        loop = asyncio.get_running_loop()
        executor = RecordingExecutor()

        def fake_run_in_executor(_executor, _func, _cache_dir, model_id, _texts):
            future = loop.create_future()
            future.set_result({
                "model_id": model_id,
                "dims": 1,
                "vectors": [[1.0]],
            })
            return future

        service = self.service(executor_factory=lambda: executor)
        with mock.patch.object(loop, "run_in_executor", fake_run_in_executor):
            response = await service.embed_texts("fake-model", ["ok"])
        self.assertIsInstance(response, EmbeddingResult)

        await service.shutdown()
        await service.shutdown()

        self.assertEqual(
            executor.shutdown_calls,
            [{"wait": False, "cancel_futures": True}],
        )

    async def test_default_executor_is_single_worker_process_pool(self):
        loop = asyncio.get_running_loop()
        created = []
        executor = RecordingExecutor()

        class FakeProcessPoolExecutor:
            def __init__(self, *, max_workers):
                created.append(max_workers)

            def shutdown(self, **kwargs):
                executor.shutdown(**kwargs)

        def fake_run_in_executor(executor_arg, _func, _cache_dir, model_id, _texts):
            self.assertIsInstance(executor_arg, FakeProcessPoolExecutor)
            future = loop.create_future()
            future.set_result({
                "model_id": model_id,
                "dims": 1,
                "vectors": [[1.0]],
            })
            return future

        service = self.service()
        with mock.patch("torque.ai_embeddings.ProcessPoolExecutor", FakeProcessPoolExecutor):
            with mock.patch.object(loop, "run_in_executor", fake_run_in_executor):
                response = await service.embed_texts("fake-model", ["ok"])

        self.assertIsInstance(response, EmbeddingResult)
        self.assertEqual(created, [1])
        await service.shutdown()


class AIEmbeddingImportGuardTests(unittest.TestCase):
    def test_import_guard_does_not_load_heavy_optional_modules(self):
        code = """
import json
import sys

names = ["sentence_transformers", "torch"]
for name in names:
    sys.modules.pop(name, None)

import torque
from torque import ai_embedding_worker, ai_embeddings

print(json.dumps({name: name in sys.modules for name in names}, sort_keys=True))
"""
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        )

        self.assertEqual(
            json.loads(proc.stdout),
            {
                "sentence_transformers": False,
                "torch": False,
            },
        )


if __name__ == "__main__":
    unittest.main()

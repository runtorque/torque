"""Local embedding service with off-event-loop subprocess inference."""

from __future__ import annotations

import asyncio
from concurrent.futures import Executor, ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal, TypeAlias

from torque.ai_deps import AI_DEPS_INSTALL_HINT
from torque.ai_embedding_worker import embed_batch_worker
from torque.config import DATA_DIR


DEFAULT_EMBEDDING_TIMEOUT_SECONDS = 120.0
DEFAULT_EMBEDDING_BATCH_SIZE = 32
EMBEDDING_PROBE_TEXT = "dimension probe"

EmbeddingFailureKind = Literal[
    "invalid_request",
    "timeout",
    "dependency_missing",
    "worker_error",
]


@dataclass(frozen=True)
class EmbeddingResult:
    model_id: str
    dims: int
    vectors: list[list[float]]


@dataclass(frozen=True)
class EmbeddingDimsResult:
    model_id: str
    dims: int


@dataclass(frozen=True)
class EmbeddingFailure:
    kind: EmbeddingFailureKind
    message: str
    model_id: str = ""
    retriable: bool = False


EmbeddingResponse: TypeAlias = EmbeddingResult | EmbeddingFailure
EmbeddingDimsResponse: TypeAlias = EmbeddingDimsResult | EmbeddingFailure
EmbeddingWorker: TypeAlias = Callable[[str, str, list[str]], dict]
ExecutorFactory: TypeAlias = Callable[[], Executor]


class LocalEmbeddingService:
    """Run local embedding batches in a single worker process.

    The service is intentionally dormant until ``embed_texts`` or ``probe_dims``
    is called.  Actual ML inference is dispatched through
    ``loop.run_in_executor`` to a single-worker ``ProcessPoolExecutor``; timeout
    and worker failures are converted into typed failure values so daemon callers
    can degrade without crashing.
    """

    def __init__(
        self,
        data_dir: Path | str | None = None,
        *,
        cache_dir: Path | str | None = None,
        timeout_seconds: float = DEFAULT_EMBEDDING_TIMEOUT_SECONDS,
        max_batch_size: int = DEFAULT_EMBEDDING_BATCH_SIZE,
        executor_factory: ExecutorFactory | None = None,
        worker: EmbeddingWorker = embed_batch_worker,
    ) -> None:
        self._data_dir = Path(data_dir) if data_dir is not None else DATA_DIR
        self._cache_dir = (
            Path(cache_dir) if cache_dir is not None else self._data_dir / "ai_models"
        )
        self._timeout_seconds = float(timeout_seconds)
        self._max_batch_size = max(1, int(max_batch_size))
        self._executor_factory = executor_factory
        self._worker = worker
        self._executor: Executor | None = None
        self._lock = asyncio.Lock()
        self._closed = False
        self._model_id = ""
        self._dims = 0

    @property
    def cache_dir(self) -> Path:
        return self._cache_dir

    @property
    def max_batch_size(self) -> int:
        return self._max_batch_size

    async def embed_texts(self, model_id: str, texts: list[str]) -> EmbeddingResponse:
        normalized_model_id = str(model_id or "").strip()
        if not normalized_model_id:
            return EmbeddingFailure(
                kind="invalid_request",
                message="Embedding model id is required.",
            )
        if self._closed:
            return EmbeddingFailure(
                kind="invalid_request",
                message="Embedding service has been shut down.",
                model_id=normalized_model_id,
            )

        try:
            normalized_texts = [str(text) for text in texts]
        except TypeError:
            return EmbeddingFailure(
                kind="invalid_request",
                message="Embedding texts must be an iterable of strings.",
                model_id=normalized_model_id,
            )

        if len(normalized_texts) > self._max_batch_size:
            return EmbeddingFailure(
                kind="invalid_request",
                message=(
                    "Embedding batch exceeds the configured maximum "
                    f"of {self._max_batch_size} texts."
                ),
                model_id=normalized_model_id,
            )
        if not normalized_texts:
            return EmbeddingResult(
                model_id=normalized_model_id,
                dims=0,
                vectors=[],
            )

        async with self._lock:
            loop = asyncio.get_running_loop()
            try:
                executor = self._ensure_executor()
                payload = await asyncio.wait_for(
                    loop.run_in_executor(
                        executor,
                        self._worker,
                        str(self._cache_dir),
                        normalized_model_id,
                        normalized_texts,
                    ),
                    timeout=self._timeout_seconds,
                )
            except asyncio.TimeoutError:
                self._reset_executor(wait=False)
                return EmbeddingFailure(
                    kind="timeout",
                    message="Embedding worker timed out.",
                    model_id=normalized_model_id,
                    retriable=True,
                )
            except (ModuleNotFoundError, ImportError):
                self._reset_executor(wait=False)
                return EmbeddingFailure(
                    kind="dependency_missing",
                    message=(
                        "Local embedding dependencies are not installed. "
                        f"Run `{AI_DEPS_INSTALL_HINT}`."
                    ),
                    model_id=normalized_model_id,
                )
            except Exception:
                self._reset_executor(wait=False)
                return EmbeddingFailure(
                    kind="worker_error",
                    message="Embedding worker failed.",
                    model_id=normalized_model_id,
                )

        response = _coerce_worker_payload(payload, normalized_model_id)
        if isinstance(response, EmbeddingResult):
            self._model_id = response.model_id
            self._dims = response.dims
        return response

    async def probe_dims(self, model_id: str) -> EmbeddingDimsResponse:
        response = await self.embed_texts(model_id, [EMBEDDING_PROBE_TEXT])
        if isinstance(response, EmbeddingFailure):
            return response
        return EmbeddingDimsResult(model_id=response.model_id, dims=response.dims)

    async def shutdown(self) -> None:
        self._closed = True
        self._reset_executor(wait=False)

    def _ensure_executor(self) -> Executor:
        if self._executor is None:
            if self._executor_factory is not None:
                self._executor = self._executor_factory()
            else:
                self._executor = ProcessPoolExecutor(max_workers=1)
        return self._executor

    def _reset_executor(self, *, wait: bool) -> None:
        executor = self._executor
        self._executor = None
        if executor is None:
            return
        try:
            executor.shutdown(wait=wait, cancel_futures=True)
        except TypeError:
            executor.shutdown(cancel_futures=True)


def _coerce_worker_payload(payload: object, fallback_model_id: str) -> EmbeddingResponse:
    if not isinstance(payload, dict):
        return EmbeddingFailure(
            kind="worker_error",
            message="Embedding worker returned an invalid response.",
            model_id=fallback_model_id,
        )

    model_id = str(payload.get("model_id") or fallback_model_id)
    vectors_raw = payload.get("vectors", [])
    if not isinstance(vectors_raw, list):
        return EmbeddingFailure(
            kind="worker_error",
            message="Embedding worker returned invalid vectors.",
            model_id=model_id,
        )

    try:
        vectors: list[list[float]] = []
        for row in vectors_raw:
            if not isinstance(row, list):
                return EmbeddingFailure(
                    kind="worker_error",
                    message="Embedding worker returned invalid vector rows.",
                    model_id=model_id,
                )
            vectors.append([float(value) for value in row])
        dims_raw = payload.get("dims", 0)
        dims = int(dims_raw) if dims_raw is not None else 0
    except (TypeError, ValueError):
        return EmbeddingFailure(
            kind="worker_error",
            message="Embedding worker returned invalid vector dimensions.",
            model_id=model_id,
        )

    if dims < 0:
        return EmbeddingFailure(
            kind="worker_error",
            message="Embedding worker returned invalid vector dimensions.",
            model_id=model_id,
        )
    if vectors and dims == 0:
        dims = len(vectors[0])

    return EmbeddingResult(model_id=model_id, dims=dims, vectors=vectors)

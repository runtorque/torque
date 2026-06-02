"""Subprocess worker entrypoints for local embedding inference.

This module must stay import-light: optional ML dependencies are imported only
inside ``embed_batch_worker`` so importing Torque never loads SentenceTransformers
or torch on the daemon event loop.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


_MODEL_CACHE: dict[str, Any] = {}


def embed_batch_worker(cache_dir: str, model_id: str, texts: list[str]) -> dict:
    """Embed a batch of text in a worker process.

    The function is module-level and picklable for ``ProcessPoolExecutor``.  It
    lazy-imports SentenceTransformers in the child process only, caches models by
    id within that process, normalizes vectors, and returns plain JSON-like data
    so callers never receive numpy arrays over the process boundary.
    """

    from sentence_transformers import SentenceTransformer

    normalized_model_id = str(model_id or "").strip()
    normalized_texts = [str(text) for text in texts]
    model_cache_dir = Path(cache_dir).expanduser()
    model_cache_dir.mkdir(parents=True, exist_ok=True)

    model = _MODEL_CACHE.get(normalized_model_id)
    if model is None:
        model = SentenceTransformer(
            normalized_model_id,
            cache_folder=str(model_cache_dir),
            trust_remote_code=False,
        )
        _MODEL_CACHE[normalized_model_id] = model

    encoded = model.encode(
        normalized_texts,
        normalize_embeddings=True,
        convert_to_numpy=True,
    )
    vectors = encoded.astype("float32", copy=False)
    vector_lists = vectors.tolist()
    if vector_lists and isinstance(vector_lists[0], (float, int)):
        vector_lists = [vector_lists]

    shape = getattr(vectors, "shape", ())
    if len(shape) >= 2:
        dims = int(shape[1])
    elif len(shape) == 1:
        dims = int(shape[0])
    elif vector_lists:
        dims = len(vector_lists[0])
    else:
        dims = 0

    return {
        "model_id": normalized_model_id,
        "dims": dims,
        "vectors": vector_lists,
    }

"""AI settings serialization, secret handling, and live update helpers."""

from __future__ import annotations

import re

from . import ai_deps
from .ai_index import AIIndexService
from .ai_summaries import AISummaryService
from .config import log
from .db import TorqueDB
from .state import (
    AI_DEFAULT_EMBEDDING_MODEL,
    AI_EMBEDDING_RUNTIMES,
    AI_GENERATION_PROVIDERS,
    MatrixState,
    default_ai_index_corpus,
)


_SECRET_COMMAND_LOG_KEY_RE = re.compile(
    r"(api_key|secret|token|password|private_key|authorization)",
    re.IGNORECASE,
)
_REDACTED_SECRET_VALUE = "[REDACTED]"


def _redact_command_log_value(value, *, key: str = ""):
    """Recursively redact secret-shaped command payload fields for logging."""
    if _SECRET_COMMAND_LOG_KEY_RE.search(str(key or "")):
        return _REDACTED_SECRET_VALUE
    if isinstance(value, dict):
        return {
            str(item_key): _redact_command_log_value(
                item_value,
                key=str(item_key),
            )
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [
            _redact_command_log_value(item)
            for item in value
        ]
    if isinstance(value, tuple):
        return tuple(_redact_command_log_value(item) for item in value)
    return value


def _redact_command_log_payload(data: dict | None) -> dict:
    """Return a command log payload with cmd omitted and secrets masked."""
    return {
        str(key): _redact_command_log_value(value, key=str(key))
        for key, value in dict(data or {}).items()
        if key != "cmd"
    }


def _empty_ai_secret_metadata() -> dict:
    return {"configured": False, "last4": "", "updated_at": 0}


def _ai_secret_metadata(db: TorqueDB | None, provider: str) -> dict:
    if not db:
        return _empty_ai_secret_metadata()
    try:
        getter = getattr(db, "get_ai_provider_secret_metadata", None)
        if callable(getter):
            return getter(provider)
    except Exception:
        log.exception("Failed to load AI provider secret metadata")
    return _empty_ai_secret_metadata()


def _build_ai_settings_response(
    state: MatrixState,
    db: TorqueDB | None = None,
) -> dict:
    """Build the redacted AI settings payload consumed by the Settings AI tab."""
    gs = state.global_settings
    enabled = bool(getattr(gs, "ai_enabled", False))
    boot_summary_enabled = bool(
        getattr(gs, "ai_boot_summary_enabled", True)
    )
    corpus = dict(default_ai_index_corpus())
    persisted_corpus = getattr(gs, "ai_index_corpus", {}) or {}
    if isinstance(persisted_corpus, dict):
        for key in corpus:
            if key in persisted_corpus:
                corpus[key] = bool(persisted_corpus.get(key))
    embedding_model = (
        str(getattr(gs, "ai_embedding_model", "") or "").strip()
        or AI_DEFAULT_EMBEDDING_MODEL
    )
    index_payload = {}
    if db:
        try:
            getter = getattr(db, "ai_get_index_status_payload", None)
            if callable(getter):
                index_payload = getter() or {}
        except Exception:
            log.exception("Failed to load AI index status payload")
            index_payload = {}
    index_state = dict(index_payload.get("state", {}) or {})
    index_counts = dict(index_payload.get("counts", {}) or {})
    current_job = index_payload.get("current_job")
    rebuild_warning = dict(index_payload.get("rebuild_warning", {}) or {})
    summary_payload = {}
    if db:
        try:
            getter = getattr(db, "ai_get_summary_status_payload", None)
            if callable(getter):
                summary_payload = getter() or {}
        except Exception:
            log.exception("Failed to load AI summary status payload")
            summary_payload = {}
    summary_counts = dict(summary_payload.get("counts", {}) or {})
    index_status = (
        "disabled"
        if not enabled
        else str(index_state.get("status", "") or "not_built")
    )
    return {
        "type": "ai_settings",
        "schema_version": 1,
        "settings": {
            "enabled": enabled,
            "generation": {
                "provider": getattr(
                    gs,
                    "ai_generation_provider",
                    "anthropic",
                ),
                "providers": list(AI_GENERATION_PROVIDERS),
                "anthropic": {
                    "model": getattr(gs, "ai_anthropic_model", ""),
                    "key": _ai_secret_metadata(db, "anthropic"),
                },
                "openai_compatible": {
                    "base_url": getattr(
                        gs,
                        "ai_openai_compatible_base_url",
                        "",
                    ),
                    "model": getattr(
                        gs,
                        "ai_openai_compatible_model",
                        "",
                    ),
                    "key": _ai_secret_metadata(db, "openai_compatible"),
                },
            },
            "embeddings": {
                "runtime": getattr(
                    gs,
                    "ai_embedding_runtime",
                    AI_EMBEDDING_RUNTIMES[0],
                ),
                "model_id": embedding_model,
                "default_model_id": AI_DEFAULT_EMBEDDING_MODEL,
                "dependency": {
                    "status": ai_deps.embeddings_dependency_status(),
                    "packages": list(ai_deps.AI_DEPENDENCY_PACKAGES),
                    "install_hint": ai_deps.AI_DEPS_INSTALL_HINT,
                },
                "active_model_id": str(
                    index_state.get("active_model_id", "") or ""
                ),
                "active_dims": int(index_state.get("active_dims", 0) or 0),
                "desired_model_id": str(
                    index_state.get("desired_model_id", "") or embedding_model
                ),
            },
            "index": {
                "status": index_status,
                "corpus": corpus,
                "counts": {
                    "sources": int(index_counts.get("sources", 0) or 0),
                    "chunks": int(index_counts.get("chunks", 0) or 0),
                    "indexed": int(index_counts.get("indexed", 0) or 0),
                    "pending": int(index_counts.get("pending", 0) or 0),
                    "stale": int(index_counts.get("stale", 0) or 0),
                    "errors": int(index_counts.get("errors", 0) or 0),
                },
                "last_built_at": float(index_state.get("last_built_at", 0) or 0),
                "last_error": str(index_state.get("last_error", "") or ""),
                "current_job": current_job,
                "rebuild_warning": {
                    "required": bool(rebuild_warning.get("required", False)),
                    "reason": str(rebuild_warning.get("reason", "") or ""),
                    "estimated_entries": int(
                        rebuild_warning.get("estimated_entries", 0) or 0
                    ),
                },
            },
            "boot_summary": {
                "enabled": boot_summary_enabled,
                "min_interval_seconds": int(
                    getattr(gs, "ai_boot_summary_min_interval_seconds", 600)
                    or 0
                ),
                "max_refreshes_per_hour": int(
                    getattr(gs, "ai_boot_summary_max_refreshes_per_hour", 20)
                    or 0
                ),
                "status": (
                    "disabled"
                    if (not enabled or not boot_summary_enabled)
                    else (
                        "ready"
                        if int(summary_counts.get("ready", 0) or 0)
                        else (
                            "stale"
                            if int(summary_counts.get("stale", 0) or 0)
                            else "empty"
                        )
                    )
                ),
                "counts": {
                    "ready": int(summary_counts.get("ready", 0) or 0),
                    "stale": int(summary_counts.get("stale", 0) or 0),
                    "refreshing": int(summary_counts.get("refreshing", 0) or 0),
                    "empty": int(summary_counts.get("empty", 0) or 0),
                    "errors": int(summary_counts.get("errors", 0) or 0),
                },
                "last_refreshed_at": float(
                    summary_payload.get("last_refreshed_at", 0) or 0
                ),
                "last_error": str(summary_payload.get("last_error", "") or ""),
            },
            "metering": {
                "last_call_at": 0,
                "calls_24h": 0,
                "input_tokens_24h": 0,
                "output_tokens_24h": 0,
                "cache_read_input_tokens_24h": 0,
            },
        },
    }


def _ai_settings_updates_from_payload(payload: dict | None) -> dict:
    settings = dict(payload or {})
    updates: dict[str, object] = {}
    direct_keys = {
        "ai_enabled",
        "ai_generation_provider",
        "ai_anthropic_model",
        "ai_openai_compatible_base_url",
        "ai_openai_compatible_model",
        "ai_embedding_model",
        "ai_embedding_runtime",
        "ai_index_corpus",
        "ai_boot_summary_enabled",
        "ai_boot_summary_min_interval_seconds",
        "ai_boot_summary_max_refreshes_per_hour",
    }
    for key in direct_keys:
        if key in settings:
            updates[key] = settings[key]
    if "enabled" in settings:
        updates["ai_enabled"] = settings["enabled"]

    generation = settings.get("generation")
    if isinstance(generation, dict):
        if "provider" in generation:
            updates["ai_generation_provider"] = generation["provider"]
        anthropic = generation.get("anthropic")
        if isinstance(anthropic, dict) and "model" in anthropic:
            updates["ai_anthropic_model"] = anthropic["model"]
        openai = generation.get("openai_compatible")
        if isinstance(openai, dict):
            if "base_url" in openai:
                updates["ai_openai_compatible_base_url"] = openai["base_url"]
            if "model" in openai:
                updates["ai_openai_compatible_model"] = openai["model"]

    embeddings = settings.get("embeddings")
    if isinstance(embeddings, dict):
        if "model_id" in embeddings:
            updates["ai_embedding_model"] = embeddings["model_id"]
        if "runtime" in embeddings:
            updates["ai_embedding_runtime"] = embeddings["runtime"]

    index = settings.get("index")
    if isinstance(index, dict) and isinstance(index.get("corpus"), dict):
        updates["ai_index_corpus"] = index["corpus"]

    boot_summary = settings.get("boot_summary")
    if isinstance(boot_summary, dict):
        if "enabled" in boot_summary:
            updates["ai_boot_summary_enabled"] = boot_summary["enabled"]
        if "min_interval_seconds" in boot_summary:
            updates["ai_boot_summary_min_interval_seconds"] = (
                boot_summary["min_interval_seconds"]
            )
        if "max_refreshes_per_hour" in boot_summary:
            updates["ai_boot_summary_max_refreshes_per_hour"] = (
                boot_summary["max_refreshes_per_hour"]
            )
    return updates


def _iter_clear_ai_secret_providers(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, dict):
        candidates = [
            key for key, should_clear in value.items() if bool(should_clear)
        ]
    elif isinstance(value, (list, tuple, set)):
        candidates = list(value)
    else:
        candidates = [value]
    providers: list[str] = []
    for candidate in candidates:
        provider = str(candidate or "").strip().lower()
        if provider in AI_GENERATION_PROVIDERS and provider not in providers:
            providers.append(provider)
    return providers


def _save_ai_secret_updates(
    db: TorqueDB | None,
    *,
    secrets: dict | None,
    clear_secrets,
) -> None:
    clear_providers = _iter_clear_ai_secret_providers(clear_secrets)
    raw_secrets = secrets if isinstance(secrets, dict) else {}
    has_secret_updates = bool(clear_providers or raw_secrets)
    if has_secret_updates and not db:
        raise RuntimeError("AI secret storage is unavailable")
    if not db:
        return
    for provider in clear_providers:
        db.clear_ai_provider_secret(provider)
    for provider in AI_GENERATION_PROVIDERS:
        item = raw_secrets.get(provider)
        if item is None:
            continue
        if isinstance(item, dict):
            if "api_key" not in item:
                continue
            api_key = item.get("api_key")
        else:
            api_key = item
        api_key = str(api_key or "").strip()
        if not api_key:
            # Secret fields are write-only in the UI: blank means unchanged.
            continue
        db.save_ai_provider_secret(provider, api_key)


def _emit_ai_settings_update_delta(
    state: MatrixState,
    response: dict,
) -> None:
    state._emit(
        "ai_settings_update",
        schema_version=int(response.get("schema_version", 1) or 1),
        settings=dict(response.get("settings", {}) or {}),
    )


def _ai_embedding_rebuild_confirmation_response(
    state: MatrixState,
    db: TorqueDB | None,
    updates: dict,
    confirm: bool,
) -> dict | None:
    if "ai_embedding_model" not in updates or not db:
        return None
    requested_model = str(updates.get("ai_embedding_model", "") or "").strip()
    current_model = str(
        getattr(state.global_settings, "ai_embedding_model", "") or ""
    ).strip()
    if not requested_model or requested_model == current_model:
        return None
    try:
        status = db.ai_get_index_status_payload()
    except Exception:
        log.exception("Failed to evaluate AI embedding rebuild confirmation")
        return None
    counts = dict(status.get("counts", {}) or {})
    state_payload = dict(status.get("state", {}) or {})
    indexed_rows = int(counts.get("chunks", 0) or 0)
    active_model = str(state_payload.get("active_model_id", "") or "")
    if indexed_rows <= 0 or not active_model:
        return None
    if confirm:
        return None
    return {
        "type": "ai_settings_requires_confirmation",
        "reason": "embedding_model_change",
        "message": (
            "Changing the embedding model rebuilds the entire vector index "
            f"({indexed_rows} entries). Continue?"
        ),
        "estimated_entries": indexed_rows,
        "current_model_id": active_model,
        "requested_model_id": requested_model,
    }


def _apply_ai_settings_update_command(
    state: MatrixState,
    db: TorqueDB | None,
    data: dict,
    *,
    ai_index_service: AIIndexService | None = None,
    ai_summary_service: AISummaryService | None = None,
) -> dict:
    """Apply update_ai_settings and return the redacted settings response."""
    updates = _ai_settings_updates_from_payload(data.get("settings"))
    # Validate before touching the secret table so a bad non-secret setting
    # cannot partially commit a new raw key.
    updates = state._normalize_global_settings_updates(updates)
    confirmation = _ai_embedding_rebuild_confirmation_response(
        state,
        db,
        updates,
        bool(data.get("confirm_embedding_rebuild")),
    )
    if confirmation:
        return confirmation
    embedding_model_changed = (
        "ai_embedding_model" in updates
        and str(updates.get("ai_embedding_model", "") or "").strip()
        != str(getattr(state.global_settings, "ai_embedding_model", "") or "").strip()
    )
    _save_ai_secret_updates(
        db,
        secrets=data.get("secrets"),
        clear_secrets=data.get("clear_secrets"),
    )
    if updates:
        state.update_global_settings(**updates)
    if db and embedding_model_changed:
        try:
            db.ai_update_index_state(
                desired_model_id=str(updates.get("ai_embedding_model") or ""),
            )
        except Exception:
            log.exception("Failed to update desired AI embedding model")
        if bool(data.get("confirm_embedding_rebuild")):
            job = None
            try:
                db.ai_update_index_state(
                    desired_model_id=str(updates.get("ai_embedding_model") or ""),
                    status="rebuild_pending",
                    rebuild_required=True,
                    rebuild_reason="embedding_model_change",
                    last_error="",
                )
                job = db.ai_create_index_job(
                    mode="rebuild",
                    reason="embedding_model_change",
                )
            except Exception:
                log.exception("Failed to queue AI embedding rebuild")
            if job and ai_index_service is not None:
                ai_index_service.schedule_rebuild(
                    job_id=str(job.get("id", "") or ""),
                    reason="embedding_model_change",
                )
    if ai_index_service is not None and (
        "ai_enabled" in updates or "ai_index_corpus" in updates
    ):
        ai_index_service.schedule_incremental("ai_settings_change")
    if ai_summary_service is not None and any(
        key in updates
        for key in (
            "ai_enabled",
            "ai_boot_summary_enabled",
            "ai_generation_provider",
            "ai_anthropic_model",
            "ai_openai_compatible_model",
            "ai_boot_summary_min_interval_seconds",
            "ai_boot_summary_max_refreshes_per_hour",
        )
    ):
        ai_summary_service.schedule_all_boot_summaries("ai_settings_change")
    response = _build_ai_settings_response(state, db)
    _emit_ai_settings_update_delta(state, response)
    return response

"""Public AI/LLM helper surface for Torque consumers.

This module is intentionally small and provider-agnostic. Callers should import
only this module, treat every :class:`LLMFailure` as best-effort fallback, and
never depend on provider-specific adapter details.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field, replace
from typing import Literal


LLMFailureKind = Literal[
    "disabled",
    "missing_key",
    "missing_model",
    "timeout",
    "http_error",
    "provider_error",
    "invalid_json",
    "dependency_missing",
    "unknown",
]

LLMResponseFormat = Literal["text", "json"]
LLMRole = Literal["system", "user", "assistant"]


@dataclass(frozen=True)
class LLMMessage:
    role: LLMRole
    content: str


@dataclass(frozen=True)
class LLMRequest:
    purpose: str
    messages: list[LLMMessage] = field(default_factory=list)
    system: str = ""
    max_tokens: int = 1024
    temperature: float = 0.0
    cache_namespace: str = ""
    cache_static_prefix: str = ""
    response_format: LLMResponseFormat = "text"
    timeout_seconds: float = 45.0


@dataclass(frozen=True)
class LLMUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


@dataclass(frozen=True)
class LLMResult:
    provider: str
    model: str
    text: str
    usage: LLMUsage
    finish_reason: str = ""
    latency_ms: int = 0
    structured: dict | list | None = None


@dataclass(frozen=True)
class LLMFailure:
    kind: LLMFailureKind
    message: str
    provider: str = ""
    model: str = ""
    retriable: bool = False
    status_code: int | None = None
    retry_after_seconds: float | None = None


LLMResponse = LLMResult | LLMFailure


class LLMClient:
    """Provider-agnostic async LLM client.

    ``state.global_settings`` is the source of provider/model configuration.
    Raw secrets are read via ``db.read_ai_provider_secret(provider)`` only by
    adapters immediately before a provider request is made.
    """

    def __init__(
        self,
        *,
        state=None,
        db=None,
        settings=None,
        session_factory=None,
    ) -> None:
        self.state = state
        self.db = db
        self.settings = settings
        self.session_factory = session_factory

    async def complete(self, request: LLMRequest) -> LLMResponse:
        request = _normalize_request(request)
        settings = self._global_settings()
        provider = _settings_text(
            getattr(settings, "ai_generation_provider", "anthropic")
        ) or "anthropic"
        model = _model_for_provider(settings, provider)
        start = time.monotonic()

        response: LLMResponse
        if not bool(getattr(settings, "ai_enabled", False)):
            response = LLMFailure(
                kind="disabled",
                message="AI generation is disabled.",
                provider=provider,
                model=model,
            )
        else:
            try:
                from torque.ai_adapters import ADAPTERS, ProviderConfig

                adapter = ADAPTERS.get(provider)
                if adapter is None:
                    response = LLMFailure(
                        kind="provider_error",
                        message="AI generation provider is not supported.",
                        provider=provider,
                        model=model,
                    )
                elif not model:
                    response = LLMFailure(
                        kind="missing_model",
                        message="AI generation model is not configured.",
                        provider=provider,
                        model=model,
                    )
                else:
                    config = ProviderConfig(
                        provider=provider,
                        model=model,
                        base_url=_base_url_for_provider(settings, provider),
                        read_secret=self._read_secret,
                        session_factory=self.session_factory,
                    )
                    response = await adapter.complete(request, config)
            except asyncio.TimeoutError:
                response = LLMFailure(
                    kind="timeout",
                    message="AI generation request timed out.",
                    provider=provider,
                    model=model,
                    retriable=True,
                )
            except Exception:
                # Do not include exception text: provider/client exceptions can
                # contain URLs, headers, or otherwise sensitive context.
                response = LLMFailure(
                    kind="unknown",
                    message="AI generation request failed unexpectedly.",
                    provider=provider,
                    model=model,
                )

        latency_ms = _elapsed_ms(start)
        if isinstance(response, LLMResult):
            response = replace(response, latency_ms=response.latency_ms or latency_ms)
        self._record_metric(request, provider, model, response, latency_ms)
        return response

    async def complete_structured(self, request: LLMRequest) -> LLMResponse:
        return await self.complete(replace(request, response_format="json"))

    def _global_settings(self):
        if self.settings is not None:
            return self.settings
        if self.state is not None:
            settings = getattr(self.state, "global_settings", None)
            if settings is not None:
                return settings
        return _DisabledSettings()

    def _db(self):
        if self.db is not None:
            return self.db
        if self.state is not None:
            return getattr(self.state, "db", None)
        return None

    def _read_secret(self, provider: str) -> str:
        db = self._db()
        reader = getattr(db, "read_ai_provider_secret", None)
        if reader is None:
            return ""
        return str(reader(provider) or "")

    def _record_metric(
        self,
        request: LLMRequest,
        provider: str,
        model: str,
        response: LLMResponse,
        latency_ms: int,
    ) -> None:
        db = self._db()
        recorder = getattr(db, "record_ai_call_metric", None)
        if recorder is None:
            return
        try:
            if isinstance(response, LLMResult):
                usage = response.usage
                recorder(
                    purpose=request.purpose,
                    provider=response.provider or provider,
                    model=response.model or model,
                    status="ok",
                    latency_ms=response.latency_ms or latency_ms,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    cache_creation_input_tokens=(
                        usage.cache_creation_input_tokens
                    ),
                    cache_read_input_tokens=usage.cache_read_input_tokens,
                )
            else:
                recorder(
                    purpose=request.purpose,
                    provider=response.provider or provider,
                    model=response.model or model,
                    status="failure",
                    failure_kind=response.kind,
                    latency_ms=latency_ms,
                )
        except Exception:
            # Metering is best-effort and must never break consumers.
            return


async def summarize(
    *,
    purpose: str,
    source_text: str,
    instructions: str,
    max_tokens: int = 1200,
    cache_key: str = "",
    previous_summary: str = "",
    client: LLMClient | None = None,
) -> LLMResponse:
    """Summarize text through ``LLMClient`` with provider details hidden.

    Future summary services can pass their configured client explicitly. Without
    a client this helper returns a typed disabled failure so callers can fall
    back to cached/raw context.
    """

    if client is None:
        return LLMFailure(
            kind="disabled",
            message="AI client is not configured.",
        )
    parts = [str(instructions or "").strip()]
    previous = str(previous_summary or "").strip()
    if previous:
        parts.append("Previous summary:\n" + previous)
    system = "\n\n".join(part for part in parts if part)
    request = LLMRequest(
        purpose=purpose,
        system=system,
        messages=[LLMMessage(role="user", content=str(source_text or ""))],
        max_tokens=max_tokens,
        temperature=0.0,
        cache_namespace=str(cache_key or ""),
        cache_static_prefix=system if cache_key else "",
    )
    return await client.complete(request)


def _normalize_request(request: LLMRequest) -> LLMRequest:
    if isinstance(request, LLMRequest):
        return request
    raise TypeError("request must be an LLMRequest")


def _settings_text(value) -> str:
    return str(value or "").strip()


def _model_for_provider(settings, provider: str) -> str:
    if provider == "openai_compatible":
        return _settings_text(
            getattr(settings, "ai_openai_compatible_model", "")
        )
    if provider == "anthropic":
        return _settings_text(getattr(settings, "ai_anthropic_model", ""))
    return ""


def _base_url_for_provider(settings, provider: str) -> str:
    if provider == "openai_compatible":
        return _settings_text(
            getattr(settings, "ai_openai_compatible_base_url", "")
        )
    return ""


def _elapsed_ms(start: float) -> int:
    return max(0, int((time.monotonic() - start) * 1000))


@dataclass(frozen=True)
class _DisabledSettings:
    ai_enabled: bool = False
    ai_generation_provider: str = "anthropic"
    ai_anthropic_model: str = ""
    ai_openai_compatible_base_url: str = ""
    ai_openai_compatible_model: str = ""


__all__ = [
    "LLMClient",
    "LLMFailure",
    "LLMFailureKind",
    "LLMMessage",
    "LLMRequest",
    "LLMResponse",
    "LLMResponseFormat",
    "LLMResult",
    "LLMRole",
    "LLMUsage",
    "summarize",
]

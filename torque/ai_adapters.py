"""Provider adapters for Torque's dormant AI generation surface.

Adapters are internal implementation details; consumers import ``torque.ai``.
All normal provider/config/network errors return redacted ``LLMFailure`` values.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from dataclasses import dataclass
from typing import Callable, Protocol
from urllib.parse import urljoin

from torque.ai import (
    LLMFailure,
    LLMMessage,
    LLMRequest,
    LLMResponse,
    LLMResult,
    LLMUsage,
)


@dataclass(frozen=True)
class ProviderConfig:
    provider: str
    model: str
    base_url: str = ""
    read_secret: Callable[[str], str] | None = None
    session_factory: Callable | None = None


class LLMAdapter(Protocol):
    provider: str

    async def complete(
        self,
        request: LLMRequest,
        config: ProviderConfig,
    ) -> LLMResponse: ...


class AnthropicMessagesAdapter:
    provider = "anthropic"
    endpoint = "https://api.anthropic.com/v1/messages"

    async def complete(
        self,
        request: LLMRequest,
        config: ProviderConfig,
    ) -> LLMResponse:
        api_key = _read_secret(config)
        if not api_key:
            return LLMFailure(
                kind="missing_key",
                message="Anthropic API key is not configured.",
                provider=self.provider,
                model=config.model,
            )

        payload = _anthropic_payload(request, config.model)
        headers = {
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
            "x-api-key": api_key,
        }
        response = await _post_json(
            config,
            self.endpoint,
            headers=headers,
            payload=payload,
            timeout_seconds=request.timeout_seconds,
        )
        if isinstance(response, LLMFailure):
            return _with_provider(response, self.provider, config.model)
        data = response
        if isinstance(data.get("error"), dict):
            return LLMFailure(
                kind="provider_error",
                message="Provider returned an error response.",
                provider=self.provider,
                model=config.model,
            )
        text = _anthropic_text(data)
        if text is None:
            return LLMFailure(
                kind="provider_error",
                message="Provider response did not include text content.",
                provider=self.provider,
                model=config.model,
            )
        structured = None
        if request.response_format == "json":
            parsed = _parse_structured(text, self.provider, config.model)
            if isinstance(parsed, LLMFailure):
                return parsed
            structured = parsed
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        return LLMResult(
            provider=self.provider,
            model=config.model,
            text=text,
            usage=LLMUsage(
                input_tokens=_int_usage(usage.get("input_tokens")),
                output_tokens=_int_usage(usage.get("output_tokens")),
                cache_creation_input_tokens=_int_usage(
                    usage.get("cache_creation_input_tokens")
                ),
                cache_read_input_tokens=_int_usage(
                    usage.get("cache_read_input_tokens")
                ),
            ),
            finish_reason=str(data.get("stop_reason") or ""),
            structured=structured,
        )


class OpenAICompatibleAdapter:
    provider = "openai_compatible"

    async def complete(
        self,
        request: LLMRequest,
        config: ProviderConfig,
    ) -> LLMResponse:
        if not str(config.base_url or "").strip():
            return LLMFailure(
                kind="missing_model",
                message="OpenAI-compatible base URL is not configured.",
                provider=self.provider,
                model=config.model,
            )
        api_key = _read_secret(config)
        payload = _openai_payload(request, config.model)
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        response = await _post_json(
            config,
            _join_url(config.base_url, "chat/completions"),
            headers=headers,
            payload=payload,
            timeout_seconds=request.timeout_seconds,
        )
        if isinstance(response, LLMFailure):
            return _with_provider(response, self.provider, config.model)
        data = response
        if isinstance(data.get("error"), dict):
            return LLMFailure(
                kind="provider_error",
                message="Provider returned an error response.",
                provider=self.provider,
                model=config.model,
            )
        text = _openai_text(data)
        if text is None:
            return LLMFailure(
                kind="provider_error",
                message="Provider response did not include text content.",
                provider=self.provider,
                model=config.model,
            )
        structured = None
        if request.response_format == "json":
            parsed = _parse_structured(text, self.provider, config.model)
            if isinstance(parsed, LLMFailure):
                return parsed
            structured = parsed
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        return LLMResult(
            provider=self.provider,
            model=config.model,
            text=text,
            usage=LLMUsage(
                input_tokens=_int_usage(
                    usage.get("prompt_tokens", usage.get("input_tokens"))
                ),
                output_tokens=_int_usage(
                    usage.get("completion_tokens", usage.get("output_tokens"))
                ),
            ),
            finish_reason=str(
                (data.get("choices") or [{}])[0].get("finish_reason") or ""
            ) if isinstance(data.get("choices"), list) else "",
            structured=structured,
        )


ADAPTERS: dict[str, LLMAdapter] = {
    "anthropic": AnthropicMessagesAdapter(),
    "openai_compatible": OpenAICompatibleAdapter(),
}


def _read_secret(config: ProviderConfig) -> str:
    if config.read_secret is None:
        return ""
    try:
        return str(config.read_secret(config.provider) or "")
    except Exception:
        return ""


def _anthropic_payload(request: LLMRequest, model: str) -> dict:
    system_blocks = []
    if request.cache_static_prefix:
        system_blocks.append({
            "type": "text",
            "text": str(request.cache_static_prefix),
            "cache_control": {"type": "ephemeral"},
        })
    system_text = _combined_system_text(request, json_mode=(
        request.response_format == "json"
    ))
    if system_text:
        system_blocks.append({"type": "text", "text": system_text})

    payload = {
        "model": model,
        "max_tokens": _positive_int(request.max_tokens, 1024),
        "temperature": float(request.temperature or 0.0),
        "messages": _anthropic_messages(request.messages),
    }
    if system_blocks:
        payload["system"] = system_blocks
    return payload


def _anthropic_messages(messages: list[LLMMessage]) -> list[dict]:
    out = []
    for message in messages:
        if message.role == "system":
            continue
        role = "assistant" if message.role == "assistant" else "user"
        out.append({"role": role, "content": str(message.content or "")})
    if not out:
        out.append({"role": "user", "content": ""})
    return out


def _openai_payload(request: LLMRequest, model: str) -> dict:
    messages = []
    system_text = _combined_system_text(request, json_mode=(
        request.response_format == "json"
    ))
    if request.cache_static_prefix:
        # OpenAI-compatible prompt-cache hints are a no-op; carry the stable
        # prefix as normal context only.
        messages.append({
            "role": "system",
            "content": str(request.cache_static_prefix),
        })
    if system_text:
        messages.append({"role": "system", "content": system_text})
    for message in request.messages:
        role = (
            message.role
            if message.role in {"system", "user", "assistant"}
            else "user"
        )
        messages.append({"role": role, "content": str(message.content or "")})
    if not messages:
        messages.append({"role": "user", "content": ""})
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": _positive_int(request.max_tokens, 1024),
        "temperature": float(request.temperature or 0.0),
    }
    if request.response_format == "json":
        payload["response_format"] = {"type": "json_object"}
    return payload


def _combined_system_text(request: LLMRequest, *, json_mode: bool) -> str:
    parts = []
    if request.system:
        parts.append(str(request.system))
    for message in request.messages:
        if message.role == "system" and message.content:
            parts.append(str(message.content))
    if json_mode:
        parts.append("Return valid JSON only.")
    return "\n\n".join(part.strip() for part in parts if part and part.strip())


async def _post_json(
    config: ProviderConfig,
    url: str,
    *,
    headers: dict,
    payload: dict,
    timeout_seconds: float,
) -> dict | LLMFailure:
    session_cm = _make_session_context(config, timeout_seconds)
    if isinstance(session_cm, LLMFailure):
        return session_cm
    try:
        async with session_cm as session:
            post_cm = session.post(url, headers=headers, json=payload)
            if not hasattr(post_cm, "__aenter__") and inspect.isawaitable(post_cm):
                post_cm = await post_cm
            async with _ensure_async_context(post_cm) as response:
                status = int(getattr(response, "status", 0) or 0)
                if status < 200 or status >= 300:
                    return LLMFailure(
                        kind="http_error",
                        message=f"Provider request failed with HTTP {status}.",
                        provider=config.provider,
                        model=config.model,
                        retriable=status in {408, 409, 425, 429} or status >= 500,
                        status_code=status,
                    )
                try:
                    data = response.json()
                    if inspect.isawaitable(data):
                        data = await data
                except Exception:
                    return LLMFailure(
                        kind="provider_error",
                        message="Provider response could not be decoded.",
                        provider=config.provider,
                        model=config.model,
                    )
                if not isinstance(data, dict):
                    return LLMFailure(
                        kind="provider_error",
                        message="Provider response had an unexpected shape.",
                        provider=config.provider,
                        model=config.model,
                    )
                return data
    except asyncio.TimeoutError:
        return LLMFailure(
            kind="timeout",
            message="Provider request timed out.",
            provider=config.provider,
            model=config.model,
            retriable=True,
        )
    except Exception:
        return LLMFailure(
            kind="unknown",
            message="Provider request failed unexpectedly.",
            provider=config.provider,
            model=config.model,
        )


def _make_session_context(
    config: ProviderConfig,
    timeout_seconds: float,
):
    if config.session_factory is not None:
        try:
            session = config.session_factory(timeout_seconds=timeout_seconds)
        except TypeError:
            try:
                session = config.session_factory(timeout=timeout_seconds)
            except TypeError:
                session = config.session_factory()
        return _ensure_async_context(session)
    try:
        import aiohttp  # type: ignore
    except Exception:
        return LLMFailure(
            kind="dependency_missing",
            message="aiohttp is not available for AI provider calls.",
            provider=config.provider,
            model=config.model,
        )
    client_session = getattr(aiohttp, "ClientSession", None)
    if client_session is None:
        return LLMFailure(
            kind="dependency_missing",
            message="aiohttp ClientSession is not available.",
            provider=config.provider,
            model=config.model,
        )
    timeout_obj = None
    client_timeout = getattr(aiohttp, "ClientTimeout", None)
    if client_timeout is not None:
        timeout_obj = client_timeout(total=max(0.001, float(timeout_seconds or 45.0)))
    try:
        if timeout_obj is not None:
            return client_session(timeout=timeout_obj)
        return client_session()
    except Exception:
        return LLMFailure(
            kind="dependency_missing",
            message="aiohttp ClientSession could not be created.",
            provider=config.provider,
            model=config.model,
        )


def _ensure_async_context(value):
    if hasattr(value, "__aenter__") and hasattr(value, "__aexit__"):
        return value
    return _AsyncNullContext(value)


class _AsyncNullContext:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc, tb):
        close = getattr(self.value, "close", None)
        if callable(close):
            result = close()
            if inspect.isawaitable(result):
                await result
        return False


def _anthropic_text(data: dict) -> str | None:
    content = data.get("content")
    if not isinstance(content, list):
        return None
    parts = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") in (None, "text") and isinstance(item.get("text"), str):
            parts.append(item["text"])
    return "".join(parts) if parts else None


def _openai_text(data: dict) -> str | None:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, dict):
        return None
    message = first.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
            return "".join(parts) if parts else None
    text = first.get("text")
    return text if isinstance(text, str) else None


def _parse_structured(text: str, provider: str, model: str) -> dict | list | LLMFailure:
    try:
        parsed = json.loads(text)
    except Exception:
        return LLMFailure(
            kind="invalid_json",
            message="Provider returned invalid JSON.",
            provider=provider,
            model=model,
        )
    if not isinstance(parsed, (dict, list)):
        return LLMFailure(
            kind="invalid_json",
            message="Provider returned JSON that was not an object or array.",
            provider=provider,
            model=model,
        )
    return parsed


def _with_provider(failure: LLMFailure, provider: str, model: str) -> LLMFailure:
    if failure.provider == provider and failure.model == model:
        return failure
    return LLMFailure(
        kind=failure.kind,
        message=failure.message,
        provider=failure.provider or provider,
        model=failure.model or model,
        retriable=failure.retriable,
        status_code=failure.status_code,
    )


def _join_url(base_url: str, path: str) -> str:
    base = str(base_url or "").strip().rstrip("/") + "/"
    return urljoin(base, path.lstrip("/"))


def _positive_int(value, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _int_usage(value) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, parsed)


__all__ = [
    "ADAPTERS",
    "AnthropicMessagesAdapter",
    "LLMAdapter",
    "OpenAICompatibleAdapter",
    "ProviderConfig",
]

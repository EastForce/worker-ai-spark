#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Standard-library clients used by the pilot model-evaluation runner.

Secrets are read only from environment variables.  They are never included in
``repr`` output, provider descriptions, persisted requests, or raised error
messages.  Network access is initiated only by explicit calls to ``list_models``
or ``generate``; importing this module has no side effects.
"""

from __future__ import annotations

import http.client
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence


PROVIDER_NAMES = ("deepseek", "volcengine", "minimax", "gemini")

_PROVIDER_SETTINGS = {
    "deepseek": {
        "kind": "openai",
        "key_env": "DEEPSEEK_API_KEY",
        "models_env": "DEEPSEEK_MODELS",
        "base_url_env": "DEEPSEEK_BASE_URL",
        "base_url": "https://api.deepseek.com",
    },
    "volcengine": {
        "kind": "openai",
        "key_env": "VOLCENGINE_API_KEY",
        "models_env": "VOLCENGINE_MODELS",
        "base_url_env": "VOLCENGINE_BASE_URL",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
    },
    "minimax": {
        "kind": "openai",
        "key_env": "MINIMAX_API_KEY",
        "models_env": "MINIMAX_MODELS",
        "base_url_env": "MINIMAX_BASE_URL",
        "base_url": "https://api.minimaxi.com/v1",
    },
    "gemini": {
        "kind": "gemini",
        "key_env": "GEMINI_API_KEY",
        "models_env": "GEMINI_MODELS",
        "base_url_env": "GEMINI_BASE_URL",
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
    },
}

_SENSITIVE_KEY = re.compile(
    r"(?:api[-_ ]?key|authorization|access[-_ ]?token|secret|credential)",
    re.IGNORECASE,
)
_BEARER_VALUE = re.compile(r"(?i)(\bBearer\s+)[^\s,;\"']+")
_ASSIGNED_SECRET = re.compile(
    r"(?i)((?:api[-_ ]?key|access[-_ ]?token|authorization|secret)\s*[=:]\s*)"
    r"([^\s&,;\"']+)"
)
_QUERY_SECRET = re.compile(
    r"(?i)([?&](?:key|api_key|apikey|access_token)=)[^&#\s]+"
)


def canonical_json(value: Any) -> str:
    """Return stable JSON suitable for hashing and JSONL persistence."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def redact_text(value: str, secrets: Iterable[str] = ()) -> str:
    """Remove known secret values and common credential-shaped substrings."""

    text = value
    for secret in sorted(
        {item for item in secrets if isinstance(item, str) and item},
        key=len,
        reverse=True,
    ):
        text = text.replace(secret, "[REDACTED]")
    text = _BEARER_VALUE.sub(r"\1[REDACTED]", text)
    text = _ASSIGNED_SECRET.sub(r"\1[REDACTED]", text)
    text = _QUERY_SECRET.sub(r"\1[REDACTED]", text)
    return text


def redact_object(value: Any, secrets: Iterable[str] = ()) -> Any:
    """Recursively redact response/error objects before they leave a provider."""

    secret_tuple = tuple(secret for secret in secrets if secret)
    if isinstance(value, Mapping):
        result: Dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _SENSITIVE_KEY.search(key_text):
                result[key_text] = "[REDACTED]"
            else:
                result[key_text] = redact_object(item, secret_tuple)
        return result
    if isinstance(value, list):
        return [redact_object(item, secret_tuple) for item in value]
    if isinstance(value, tuple):
        return [redact_object(item, secret_tuple) for item in value]
    if isinstance(value, str):
        return redact_text(value, secret_tuple)
    return value


def _normalise_base_url(value: str) -> str:
    """Validate endpoint configuration and reject credentials in its URL."""

    candidate = value.strip().rstrip("/")
    parsed = urllib.parse.urlsplit(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("provider base URL must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("provider base URL must not contain credentials, query, or fragment")
    return candidate


def _join_url(base_url: str, path: str) -> str:
    return base_url.rstrip("/") + "/" + path.lstrip("/")


@dataclass(frozen=True)
class HttpJsonResponse:
    status_code: int
    data: Any
    raw_text: str
    headers: Mapping[str, str]


@dataclass(frozen=True)
class HttpSseResponse:
    """A completed JSON-over-SSE response.

    ``chunks`` contains each parsed ``data: {...}`` JSON value in wire order.
    ``done`` records whether an explicit ``data: [DONE]`` was received, while
    ``termination`` records the accepted completion condition. Providers may
    opt into accepting a clean EOF after their own terminal finish field; an
    unqualified EOF or connection failure remains a partial request error.
    """

    status_code: int
    chunks: Sequence[Any]
    done: bool
    headers: Mapping[str, str]
    termination: str = "done_sentinel"


class ProviderRequestError(RuntimeError):
    """A safe, structured provider failure used by the retry layer."""

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        retryable: bool = False,
        retry_after: Optional[float] = None,
        response_body: Optional[Any] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable
        self.retry_after = retry_after
        self.response_body = response_body

    def as_dict(self) -> Dict[str, Any]:
        return {
            "type": self.__class__.__name__,
            "message": str(self),
            "status_code": self.status_code,
            "retryable": self.retryable,
            "retry_after_seconds": self.retry_after,
            "response_body": self.response_body,
        }


class JsonHttpTransport:
    """Tiny urllib JSON transport; intentionally has no logging hooks."""

    _MAX_ERROR_BODY_BYTES = 64 * 1024

    def request_json(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[Mapping[str, str]] = None,
        payload: Optional[Any] = None,
        timeout: float = 120.0,
    ) -> HttpJsonResponse:
        body = None
        request_headers = dict(headers or {})
        if payload is not None:
            body = canonical_json(payload).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json; charset=utf-8")
        request_headers.setdefault("Accept", "application/json")
        request = urllib.request.Request(
            url=url,
            data=body,
            headers=request_headers,
            method=method.upper(),
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw_bytes = response.read()
                raw_text = raw_bytes.decode("utf-8", errors="replace")
                try:
                    data = json.loads(raw_text)
                except json.JSONDecodeError as exc:
                    raise ProviderRequestError(
                        "provider returned a non-JSON success response",
                        status_code=getattr(response, "status", 200),
                        retryable=False,
                    ) from exc
                response_headers = {
                    str(key).lower(): str(value)
                    for key, value in response.headers.items()
                }
                return HttpJsonResponse(
                    status_code=getattr(response, "status", 200),
                    data=data,
                    raw_text=raw_text,
                    headers=response_headers,
                )
        except urllib.error.HTTPError as exc:
            raw_bytes = exc.read(self._MAX_ERROR_BODY_BYTES)
            raw_text = raw_bytes.decode("utf-8", errors="replace")
            try:
                error_body: Any = json.loads(raw_text)
            except json.JSONDecodeError:
                error_body = raw_text
            retry_after = _parse_retry_after(exc.headers.get("Retry-After"))
            status = int(exc.code)
            raise ProviderRequestError(
                "provider HTTP request failed with status %d" % status,
                status_code=status,
                retryable=status == 429 or 500 <= status <= 599,
                retry_after=retry_after,
                response_body=error_body,
            ) from None
        except (
            urllib.error.URLError,
            TimeoutError,
            socket.timeout,
            ConnectionError,
            http.client.HTTPException,
        ) as exc:
            reason = getattr(exc, "reason", None)
            safe_kind = reason.__class__.__name__ if reason is not None else exc.__class__.__name__
            raise ProviderRequestError(
                "provider network request failed (%s)" % safe_kind,
                retryable=True,
            ) from None

    def request_sse_json(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[Mapping[str, str]] = None,
        payload: Optional[Any] = None,
        timeout: float = 120.0,
        allow_eof_after_finish_reason: bool = False,
        allow_eof_after_gemini_finish_reason: bool = False,
        protocol: str = "openai-sse",
    ) -> HttpSseResponse:
        """Read a terminated JSON SSE stream without logging bytes.

        Parsed JSON chunks are retained as an ordered audit trail.  Incomplete
        attempts are never returned as successful responses: their own chunks
        are attached only to that attempt's safe error object so retry attempts
        cannot be accidentally concatenated.
        """

        body = None
        request_headers = dict(headers or {})
        if payload is not None:
            body = canonical_json(payload).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json; charset=utf-8")
        request_headers.setdefault("Accept", "text/event-stream")
        request = urllib.request.Request(
            url=url,
            data=body,
            headers=request_headers,
            method=method.upper(),
        )
        chunks: List[Any] = []
        data_lines: List[str] = []
        status_code: Optional[int] = None

        def partial_body(*, pending_event: bool = False) -> Dict[str, Any]:
            return {
                "stream": True,
                "protocol": protocol,
                "done": False,
                "partial": True,
                "pending_event": pending_event,
                "chunks": list(chunks),
            }

        def consume_event() -> bool:
            if not data_lines:
                return False
            data_text = "\n".join(data_lines).strip()
            data_lines.clear()
            if not data_text:
                return False
            if data_text == "[DONE]":
                return True
            try:
                chunk = json.loads(data_text)
            except json.JSONDecodeError:
                raise ProviderRequestError(
                    "provider stream contained an invalid JSON data event",
                    status_code=status_code,
                    retryable=True,
                    response_body=partial_body(pending_event=True),
                ) from None
            chunks.append(chunk)
            return False

        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status_code = int(getattr(response, "status", 200))
                response_headers = {
                    str(key).lower(): str(value)
                    for key, value in response.headers.items()
                }
                done = False
                for raw_line in response:
                    line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                    if line == "":
                        if consume_event():
                            done = True
                            break
                        continue
                    if line.startswith(":"):
                        continue
                    if line.startswith("data:"):
                        value = line[5:]
                        if value.startswith(" "):
                            value = value[1:]
                        data_lines.append(value)
                if not done and data_lines:
                    done = consume_event()
                termination = "done_sentinel"
                if not done and allow_eof_after_finish_reason and (
                    _chunks_have_terminal_finish_reason(chunks)
                ):
                    termination = "terminal_finish_reason_eof"
                elif not done and allow_eof_after_gemini_finish_reason and (
                    _chunks_have_gemini_terminal_finish_reason(chunks)
                ):
                    termination = "gemini_finish_reason_eof"
                elif not done:
                    message = (
                        "provider stream ended without a terminal Gemini finishReason"
                        if allow_eof_after_gemini_finish_reason
                        else "provider stream ended before the [DONE] terminator"
                    )
                    raise ProviderRequestError(
                        message,
                        status_code=status_code,
                        retryable=True,
                        response_body=partial_body(),
                    )
                return HttpSseResponse(
                    status_code=status_code,
                    chunks=tuple(chunks),
                    done=done,
                    headers=response_headers,
                    termination=termination,
                )
        except urllib.error.HTTPError as exc:
            raw_bytes = exc.read(self._MAX_ERROR_BODY_BYTES)
            raw_text = raw_bytes.decode("utf-8", errors="replace")
            try:
                error_body: Any = json.loads(raw_text)
            except json.JSONDecodeError:
                error_body = raw_text
            retry_after = _parse_retry_after(exc.headers.get("Retry-After"))
            status = int(exc.code)
            raise ProviderRequestError(
                "provider HTTP request failed with status %d" % status,
                status_code=status,
                retryable=status == 429 or 500 <= status <= 599,
                retry_after=retry_after,
                response_body=error_body,
            ) from None
        except ProviderRequestError:
            raise
        except (
            urllib.error.URLError,
            TimeoutError,
            socket.timeout,
            ConnectionError,
            http.client.HTTPException,
        ) as exc:
            reason = getattr(exc, "reason", None)
            safe_kind = reason.__class__.__name__ if reason is not None else exc.__class__.__name__
            response_body = (
                partial_body(pending_event=bool(data_lines))
                if chunks or data_lines
                else None
            )
            raise ProviderRequestError(
                "provider stream network request failed (%s)" % safe_kind,
                status_code=status_code,
                retryable=True,
                response_body=response_body,
            ) from None


def _parse_retry_after(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, parsed)


_TERMINAL_FINISH_REASONS = {
    "stop",
    "length",
    "content_filter",
    "tool_calls",
    "function_call",
    "max_tokens",
    "max_output_tokens",
    "token_limit",
    "end_turn",
    "sensitive",
}


def _chunks_have_terminal_finish_reason(chunks: Sequence[Any]) -> bool:
    """Return true only for a recognised terminal choice finish reason."""

    for chunk in chunks:
        if not isinstance(chunk, Mapping):
            continue
        choices = chunk.get("choices")
        if not isinstance(choices, list):
            continue
        for choice in choices:
            if not isinstance(choice, Mapping):
                continue
            choice_index = choice.get("index")
            if choice_index not in (None, 0):
                continue
            value = choice.get("finish_reason")
            if not isinstance(value, str):
                continue
            normalised = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
            if normalised in _TERMINAL_FINISH_REASONS:
                return True
    return False


def _gemini_finish_reason_is_terminal(value: Any) -> bool:
    """Treat any specified Gemini finish enum as a terminal candidate state."""

    if not isinstance(value, str):
        return False
    normalised = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
    return bool(normalised) and normalised not in {
        "unspecified",
        "finish_reason_unspecified",
    }


def _chunks_have_gemini_terminal_finish_reason(chunks: Sequence[Any]) -> bool:
    """Return true when a Gemini SSE chunk terminates the primary candidate."""

    for chunk in chunks:
        if not isinstance(chunk, Mapping):
            continue
        candidates = chunk.get("candidates")
        if not isinstance(candidates, list):
            continue
        for candidate in candidates:
            if not isinstance(candidate, Mapping):
                continue
            candidate_index = candidate.get("index")
            if candidate_index not in (None, 0):
                continue
            if _gemini_finish_reason_is_terminal(candidate.get("finishReason")):
                return True
            # Only the primary candidate is scoreable in this runner.
            break
    return False


@dataclass(frozen=True)
class ModelInfo:
    id: str
    supports_generation: bool = True
    display_name: Optional[str] = None


@dataclass(frozen=True)
class GenerationResult:
    response_text: str
    raw_response: Any
    usage: Mapping[str, Any]
    finish_reason: Optional[str]
    reasoning_text: str = ""
    returned_model: Optional[str] = None
    usage_missing: bool = False


class BaseProvider:
    """Common secret handling and public provider metadata."""

    def __init__(
        self,
        name: str,
        *,
        api_key: str,
        api_key_env: str,
        models_env: str,
        base_url: str,
        transport: Optional[JsonHttpTransport] = None,
    ) -> None:
        self.name = name
        self._api_key = api_key.strip()
        self.api_key_env = api_key_env
        self.models_env = models_env
        self.base_url = _normalise_base_url(base_url)
        self.transport = transport or JsonHttpTransport()

    def __repr__(self) -> str:
        return "%s(name=%r, base_url=%r, api_key_env=%r)" % (
            self.__class__.__name__,
            self.name,
            self.base_url,
            self.api_key_env,
        )

    def ensure_api_key(self) -> None:
        if not self._api_key:
            raise ProviderRequestError(
                "missing API credential in environment variable %s" % self.api_key_env,
                retryable=False,
            )

    def configured_models(self, environ: Mapping[str, str]) -> List[str]:
        raw = environ.get(self.models_env, "")
        return _unique_strings(part.strip() for part in raw.split(","))

    def description(self) -> Dict[str, Any]:
        return {
            "provider": self.name,
            "base_url": self.base_url,
            "api_key_env": self.api_key_env,
            "models_env": self.models_env,
            "credential_present": bool(self._api_key),
        }

    def sanitize(self, value: Any) -> Any:
        return redact_object(value, (self._api_key,))

    def safe_error(self, exc: BaseException) -> Dict[str, Any]:
        if isinstance(exc, ProviderRequestError):
            return self.sanitize(exc.as_dict())
        return {
            "type": exc.__class__.__name__,
            "message": redact_text(str(exc), (self._api_key,)),
            "status_code": None,
            "retryable": False,
            "retry_after_seconds": None,
            "response_body": None,
        }

    def list_models(self, *, timeout: float = 120.0) -> List[ModelInfo]:
        raise NotImplementedError

    def generate(
        self,
        model: str,
        *,
        system_prompt: str,
        user_prompt: str,
        parameters: Mapping[str, Any],
        timeout: float = 120.0,
    ) -> GenerationResult:
        raise NotImplementedError


class OpenAICompatibleProvider(BaseProvider):
    """Client for DeepSeek, Volcengine Ark, and MiniMax compatible APIs."""

    def _headers(self) -> Mapping[str, str]:
        self.ensure_api_key()
        return {"Authorization": "Bearer " + self._api_key}

    def list_models(self, *, timeout: float = 120.0) -> List[ModelInfo]:
        response = self.transport.request_json(
            "GET",
            _join_url(self.base_url, "models"),
            headers=self._headers(),
            timeout=timeout,
        )
        data = response.data
        if not isinstance(data, Mapping) or not isinstance(data.get("data"), list):
            raise ProviderRequestError("provider model list has an unexpected JSON shape")
        models = []
        for item in data["data"]:
            if isinstance(item, Mapping) and isinstance(item.get("id"), str):
                models.append(ModelInfo(id=item["id"]))
        return _deduplicate_models(models)

    def generate(
        self,
        model: str,
        *,
        system_prompt: str,
        user_prompt: str,
        parameters: Mapping[str, Any],
        timeout: float = 120.0,
    ) -> GenerationResult:
        messages: List[Dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_prompt})
        minimax_stream = self.name == "minimax" and bool(
            parameters.get("minimax_stream", True)
        )
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": minimax_stream,
        }
        for key in ("temperature", "top_p", "seed"):
            value = parameters.get(key)
            if value is not None:
                payload[key] = value
        max_tokens = parameters.get("max_tokens")
        if max_tokens is not None:
            if self.name == "minimax":
                # MiniMax's current text API names the output budget this way.
                payload["max_completion_tokens"] = max_tokens
            else:
                payload["max_tokens"] = max_tokens
        # DeepSeek's current API exposes thinking mode as a provider-specific
        # object.  Keep the runner's audit parameter out of every other
        # OpenAI-compatible provider payload, even in a multi-provider run.
        if self.name == "deepseek":
            deepseek_thinking = parameters.get("deepseek_thinking")
            if deepseek_thinking is not None:
                payload["thinking"] = {"type": deepseek_thinking}
        if minimax_stream:
            response = self.transport.request_sse_json(
                "POST",
                _join_url(self.base_url, "chat/completions"),
                headers=self._headers(),
                payload=payload,
                timeout=timeout,
                allow_eof_after_finish_reason=True,
            )
            return self._parse_stream_completion(response)
        response = self.transport.request_json(
            "POST",
            _join_url(self.base_url, "chat/completions"),
            headers=self._headers(),
            payload=payload,
            timeout=timeout,
        )
        raw = self.sanitize(response.data)
        return self._parse_non_stream_completion(raw)

    def _parse_non_stream_completion(self, raw: Any) -> GenerationResult:
        if not isinstance(raw, Mapping):
            raise ProviderRequestError("provider completion has an unexpected JSON shape")
        choices = raw.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
            raise ProviderRequestError("provider completion contains no choices")
        first = choices[0]
        message = first.get("message")
        content = message.get("content") if isinstance(message, Mapping) else None
        text = _openai_content_to_text(content)
        reasoning_text = ""
        if isinstance(message, Mapping) and isinstance(
            message.get("reasoning_content"), str
        ):
            reasoning_text = message["reasoning_content"]
        if self.name == "minimax":
            text, embedded_reasoning = _strip_complete_think_blocks(text)
            reasoning_text = "\n".join(
                part for part in (reasoning_text, embedded_reasoning) if part
            )
        usage_value = raw.get("usage")
        usage_missing = not isinstance(usage_value, Mapping)
        usage = usage_value if isinstance(usage_value, Mapping) else {}
        finish_reason = first.get("finish_reason")
        if finish_reason is not None and not isinstance(finish_reason, str):
            finish_reason = str(finish_reason)
        returned_model = raw.get("model")
        if returned_model is not None and not isinstance(returned_model, str):
            returned_model = str(returned_model)
        return GenerationResult(
            response_text=text,
            raw_response=raw,
            usage=dict(usage),
            finish_reason=finish_reason,
            reasoning_text=reasoning_text,
            returned_model=returned_model,
            usage_missing=usage_missing,
        )

    def _parse_stream_completion(self, response: HttpSseResponse) -> GenerationResult:
        chunks = self.sanitize(list(response.chunks))
        raw = {
            "stream": True,
            "protocol": "openai-sse",
            "http_status": response.status_code,
            "done": response.done,
            "termination": response.termination,
            "chunks": chunks,
        }
        text_parts: List[str] = []
        reasoning_parts: List[str] = []
        usage: Dict[str, Any] = {}
        finish_reason: Optional[str] = None
        returned_model: Optional[str] = None
        saw_choice = False
        saw_usage = False

        for chunk in chunks:
            if not isinstance(chunk, Mapping):
                raise ProviderRequestError(
                    "provider stream chunk has an unexpected JSON shape",
                    response_body=raw,
                )
            chunk_model = chunk.get("model")
            if chunk_model is not None:
                returned_model = (
                    chunk_model if isinstance(chunk_model, str) else str(chunk_model)
                )
            chunk_usage = chunk.get("usage")
            if isinstance(chunk_usage, Mapping):
                saw_usage = True
                usage.update(chunk_usage)
            choices = chunk.get("choices")
            if choices is None:
                continue
            if not isinstance(choices, list):
                raise ProviderRequestError(
                    "provider stream choices have an unexpected JSON shape",
                    response_body=raw,
                )
            for choice in choices:
                if not isinstance(choice, Mapping):
                    continue
                choice_index = choice.get("index")
                if choice_index not in (None, 0):
                    continue
                saw_choice = True
                delta = choice.get("delta")
                if isinstance(delta, Mapping):
                    text_parts.append(_openai_content_to_text(delta.get("content")))
                    reasoning_parts.append(
                        _openai_content_to_text(delta.get("reasoning_content"))
                    )
                chunk_finish = choice.get("finish_reason")
                if chunk_finish is not None:
                    finish_reason = (
                        chunk_finish
                        if isinstance(chunk_finish, str)
                        else str(chunk_finish)
                    )
                # The evaluation runner requests only one completion.
                break

        if not saw_choice:
            raise ProviderRequestError(
                "provider completion contains no choices",
                response_body=raw,
            )
        raw["usage_missing"] = not saw_usage
        raw["terminal_finish_reason"] = finish_reason
        if not _chunks_have_terminal_finish_reason(chunks):
            raw["partial"] = True
            raise ProviderRequestError(
                "provider stream ended without a recognised terminal finish_reason",
                status_code=response.status_code,
                retryable=True,
                response_body=raw,
            )
        text = "".join(text_parts)
        reasoning_text = "".join(reasoning_parts)
        if self.name == "minimax":
            text, embedded_reasoning = _strip_complete_think_blocks(text)
            reasoning_text = "\n".join(
                part for part in (reasoning_text, embedded_reasoning) if part
            )
        return GenerationResult(
            response_text=text,
            raw_response=raw,
            usage=usage,
            finish_reason=finish_reason,
            reasoning_text=reasoning_text,
            returned_model=returned_model,
            usage_missing=not saw_usage,
        )


class GeminiProvider(BaseProvider):
    """Client for Gemini REST ``generateContent`` and JSON SSE streaming."""

    def _headers(self) -> Mapping[str, str]:
        self.ensure_api_key()
        # Header authentication keeps the key out of URLs, logs, and exceptions.
        return {"x-goog-api-key": self._api_key}

    def list_models(self, *, timeout: float = 120.0) -> List[ModelInfo]:
        models: List[ModelInfo] = []
        page_token: Optional[str] = None
        while True:
            query = {"pageSize": "1000"}
            if page_token:
                query["pageToken"] = page_token
            url = _join_url(self.base_url, "models") + "?" + urllib.parse.urlencode(query)
            response = self.transport.request_json(
                "GET", url, headers=self._headers(), timeout=timeout
            )
            data = response.data
            if not isinstance(data, Mapping):
                raise ProviderRequestError("Gemini model list has an unexpected JSON shape")
            for item in data.get("models", []):
                if not isinstance(item, Mapping) or not isinstance(item.get("name"), str):
                    continue
                methods = item.get("supportedGenerationMethods")
                supports = (
                    isinstance(methods, list)
                    and "generateContent" in methods
                    and _gemini_likely_text_model(item["name"])
                )
                models.append(
                    ModelInfo(
                        id=item["name"],
                        supports_generation=supports,
                        display_name=item.get("displayName")
                        if isinstance(item.get("displayName"), str)
                        else None,
                    )
                )
            next_token = data.get("nextPageToken")
            if not isinstance(next_token, str) or not next_token:
                break
            page_token = next_token
        return _deduplicate_models(models)

    def generate(
        self,
        model: str,
        *,
        system_prompt: str,
        user_prompt: str,
        parameters: Mapping[str, Any],
        timeout: float = 120.0,
    ) -> GenerationResult:
        model_id = model[len("models/") :] if model.startswith("models/") else model
        gemini_stream = bool(parameters.get("gemini_stream", False))
        method = "streamGenerateContent" if gemini_stream else "generateContent"
        endpoint = "models/%s:%s" % (
            urllib.parse.quote(model_id, safe=""),
            method,
        )
        payload: Dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}]
        }
        if system_prompt:
            payload["systemInstruction"] = {"parts": [{"text": system_prompt}]}
        generation_config: Dict[str, Any] = {}
        parameter_names = {
            "temperature": "temperature",
            "top_p": "topP",
            "top_k": "topK",
            "max_tokens": "maxOutputTokens",
        }
        for source_name, gemini_name in parameter_names.items():
            value = parameters.get(source_name)
            if value is not None:
                generation_config[gemini_name] = value
        if generation_config:
            payload["generationConfig"] = generation_config
        if gemini_stream:
            response = self.transport.request_sse_json(
                "POST",
                _join_url(self.base_url, endpoint) + "?alt=sse",
                headers=self._headers(),
                payload=payload,
                timeout=timeout,
                allow_eof_after_gemini_finish_reason=True,
                protocol="gemini-sse",
            )
            return self._parse_stream_completion(response)
        response = self.transport.request_json(
            "POST",
            _join_url(self.base_url, endpoint),
            headers=self._headers(),
            payload=payload,
            timeout=timeout,
        )
        raw = self.sanitize(response.data)
        return self._parse_non_stream_completion(raw)

    def _parse_non_stream_completion(self, raw: Any) -> GenerationResult:
        if not isinstance(raw, Mapping):
            raise ProviderRequestError("Gemini completion has an unexpected JSON shape")
        candidates = raw.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise ProviderRequestError(
                "Gemini completion contains no candidates",
                response_body=raw.get("promptFeedback"),
            )
        first = candidates[0]
        if not isinstance(first, Mapping):
            raise ProviderRequestError("Gemini completion candidate has an unexpected JSON shape")
        content = first.get("content")
        parts = content.get("parts") if isinstance(content, Mapping) else None
        text_parts = []
        thought_parts = []
        if isinstance(parts, list):
            for part in parts:
                if isinstance(part, Mapping) and isinstance(part.get("text"), str):
                    if part.get("thought") is True:
                        thought_parts.append(part["text"])
                    else:
                        text_parts.append(part["text"])
        usage_value = raw.get("usageMetadata")
        usage_missing = not isinstance(usage_value, Mapping)
        usage = usage_value if isinstance(usage_value, Mapping) else {}
        finish_reason = first.get("finishReason")
        if finish_reason is not None and not isinstance(finish_reason, str):
            finish_reason = str(finish_reason)
        returned_model = raw.get("modelVersion", raw.get("model"))
        if returned_model is not None and not isinstance(returned_model, str):
            returned_model = str(returned_model)
        return GenerationResult(
            response_text="".join(text_parts),
            raw_response=raw,
            usage=dict(usage),
            finish_reason=finish_reason,
            reasoning_text="".join(thought_parts),
            returned_model=returned_model,
            usage_missing=usage_missing,
        )

    def _parse_stream_completion(self, response: HttpSseResponse) -> GenerationResult:
        chunks = self.sanitize(list(response.chunks))
        raw: Dict[str, Any] = {
            "stream": True,
            "protocol": "gemini-sse",
            "http_status": response.status_code,
            "done": response.done,
            "termination": response.termination,
            "chunks": chunks,
        }
        text_parts: List[str] = []
        thought_parts: List[str] = []
        usage: Dict[str, Any] = {}
        finish_reason: Optional[str] = None
        returned_model: Optional[str] = None
        saw_candidate = False
        saw_usage = False

        for chunk in chunks:
            if not isinstance(chunk, Mapping):
                raise ProviderRequestError(
                    "Gemini stream chunk has an unexpected JSON shape",
                    response_body=raw,
                )
            stream_error = chunk.get("error")
            if isinstance(stream_error, Mapping):
                status_value = stream_error.get("code")
                status_code = status_value if isinstance(status_value, int) else None
                raise ProviderRequestError(
                    "Gemini stream returned an error event",
                    status_code=status_code,
                    retryable=(
                        status_code == 429
                        or (status_code is not None and 500 <= status_code <= 599)
                    ),
                    response_body=raw,
                )
            chunk_model = chunk.get("modelVersion", chunk.get("model"))
            if chunk_model is not None:
                returned_model = (
                    chunk_model if isinstance(chunk_model, str) else str(chunk_model)
                )
            usage_value = chunk.get("usageMetadata")
            if isinstance(usage_value, Mapping):
                saw_usage = True
                usage.update(usage_value)
            candidates = chunk.get("candidates")
            if candidates is None:
                continue
            if not isinstance(candidates, list):
                raise ProviderRequestError(
                    "Gemini stream candidates have an unexpected JSON shape",
                    response_body=raw,
                )
            for candidate in candidates:
                if not isinstance(candidate, Mapping):
                    raise ProviderRequestError(
                        "Gemini stream candidate has an unexpected JSON shape",
                        response_body=raw,
                    )
                candidate_index = candidate.get("index")
                if candidate_index not in (None, 0):
                    continue
                saw_candidate = True
                content = candidate.get("content")
                parts = content.get("parts") if isinstance(content, Mapping) else None
                if parts is not None and not isinstance(parts, list):
                    raise ProviderRequestError(
                        "Gemini stream candidate parts have an unexpected JSON shape",
                        response_body=raw,
                    )
                if isinstance(parts, list):
                    for part in parts:
                        if not isinstance(part, Mapping):
                            continue
                        text_value = part.get("text")
                        if not isinstance(text_value, str):
                            continue
                        if part.get("thought") is True:
                            thought_parts.append(text_value)
                        else:
                            text_parts.append(text_value)
                chunk_finish = candidate.get("finishReason")
                if chunk_finish is not None:
                    finish_reason = (
                        chunk_finish
                        if isinstance(chunk_finish, str)
                        else str(chunk_finish)
                    )
                # The evaluation runner requests only one candidate.
                break

        if not saw_candidate:
            raise ProviderRequestError(
                "Gemini completion contains no candidates",
                response_body=raw,
            )
        if not _gemini_finish_reason_is_terminal(finish_reason):
            raise ProviderRequestError(
                "Gemini stream ended without a terminal candidate finishReason",
                response_body=raw,
            )
        raw["usage_missing"] = not saw_usage
        raw["terminal_finish_reason"] = finish_reason
        return GenerationResult(
            response_text="".join(text_parts),
            raw_response=raw,
            usage=usage,
            finish_reason=finish_reason,
            reasoning_text="".join(thought_parts),
            returned_model=returned_model,
            usage_missing=not saw_usage,
        )


def _openai_content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts = []
        for part in content:
            if isinstance(part, Mapping) and isinstance(part.get("text"), str):
                text_parts.append(part["text"])
        return "".join(text_parts)
    return ""


_COMPLETE_THINK_BLOCK = re.compile(r"<think>(.*?)</think>", re.IGNORECASE | re.DOTALL)


def _strip_complete_think_blocks(text: str) -> tuple[str, str]:
    """Separate only complete MiniMax thinking blocks; leave malformed text intact."""

    reasoning_parts = [match.group(1).strip() for match in _COMPLETE_THINK_BLOCK.finditer(text)]
    final_text = _COMPLETE_THINK_BLOCK.sub("", text).strip()
    return final_text, "\n".join(part for part in reasoning_parts if part)


def _unique_strings(values: Iterable[str]) -> List[str]:
    seen = set()
    result = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _deduplicate_models(models: Sequence[ModelInfo]) -> List[ModelInfo]:
    seen = set()
    result = []
    for model in models:
        if model.id and model.id not in seen:
            seen.add(model.id)
            result.append(model)
    return result


def _gemini_likely_text_model(model_id: str) -> bool:
    """Exclude catalog entries whose primary output is not scoreable text.

    Gemini's catalog exposes ``generateContent`` for some special-purpose media
    models. Explicit ``--model gemini=...`` assignments remain possible, while
    ``--all-models`` selects the text-oriented subset suitable for this rubric.
    """

    lowered = model_id.lower()
    non_text_markers = (
        "embedding",
        "imagen",
        "veo",
        "image",
        "image-generation",
        "tts",
        "audio",
        "live",
        "lyria",
        "omni",
        "robotics",
        "computer-use",
        "deep-research",
        "antigravity",
        "nano-banana",
    )
    return not any(marker in lowered for marker in non_text_markers)


def build_provider(
    name: str,
    *,
    environ: Optional[Mapping[str, str]] = None,
    transport: Optional[JsonHttpTransport] = None,
) -> BaseProvider:
    """Build a provider without making a network request."""

    normalised_name = name.strip().lower()
    if normalised_name not in _PROVIDER_SETTINGS:
        raise ValueError(
            "unsupported provider %r; choose from %s"
            % (name, ", ".join(PROVIDER_NAMES))
        )
    env = environ if environ is not None else __import__("os").environ
    settings = _PROVIDER_SETTINGS[normalised_name]
    api_key = env.get(settings["key_env"], "")
    base_url = env.get(settings["base_url_env"], settings["base_url"])
    provider_class = (
        OpenAICompatibleProvider if settings["kind"] == "openai" else GeminiProvider
    )
    return provider_class(
        normalised_name,
        api_key=api_key,
        api_key_env=settings["key_env"],
        models_env=settings["models_env"],
        base_url=base_url,
        transport=transport,
    )


def provider_settings_public() -> Mapping[str, Mapping[str, str]]:
    """Return non-secret environment/configuration names for CLI help/tests."""

    return {
        name: {
            key: value
            for key, value in settings.items()
            if key in {"kind", "key_env", "models_env", "base_url_env", "base_url"}
        }
        for name, settings in _PROVIDER_SETTINGS.items()
    }

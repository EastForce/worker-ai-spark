#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline tests for the multi-provider pilot runner (no network/API spend)."""

from __future__ import annotations

import io
import http.client
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from model_eval_providers import (
    GeminiProvider,
    HttpJsonResponse,
    HttpSseResponse,
    JsonHttpTransport,
    OpenAICompatibleProvider,
    ProviderRequestError,
    build_provider,
    redact_object,
)
from run_model_evaluations import (
    MANIFEST_FILENAME,
    RECORDS_FILENAME,
    build_parser,
    call_with_retry,
    load_questions,
    run_command,
    sha256_json,
)


API_SECRET = "unit-test-secret-that-must-never-be-written"


def json_response(data, status=200):
    return HttpJsonResponse(
        status_code=status,
        data=data,
        raw_text=json.dumps(data, ensure_ascii=False),
        headers={},
    )


class QueueTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request_json(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        if not self.responses:
            raise AssertionError("unexpected transport call")
        value = self.responses.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    def request_sse_json(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        if not self.responses:
            raise AssertionError("unexpected transport call")
        value = self.responses.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


class FakeStreamingResponse:
    def __init__(self, lines, *, status=200, headers=None):
        self.lines = list(lines)
        self.status = status
        self.headers = headers or {"Content-Type": "text/event-stream"}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def __iter__(self):
        for value in self.lines:
            if isinstance(value, BaseException):
                raise value
            yield value if isinstance(value, bytes) else value.encode("utf-8")


def openai_completion(
    text="最终回答",
    *,
    reasoning=None,
    extra=None,
    returned_model="server-resolved-model",
    finish_reason="stop",
):
    message = {"role": "assistant", "content": text}
    if reasoning is not None:
        message["reasoning_content"] = reasoning
    response = {
        "id": "completion-test",
        "model": returned_model,
        "choices": [{"message": message, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
    }
    if extra:
        response.update(extra)
    return json_response(response)


def sse_response(*chunks, status=200, done=True, termination="done_sentinel"):
    return HttpSseResponse(
        status_code=status,
        chunks=chunks,
        done=done,
        headers={"content-type": "text/event-stream"},
        termination=termination,
    )


def gemini_completion(
    text="最终回答",
    *,
    finish_reason="STOP",
    returned_model="gemini-server-resolved-version",
):
    return json_response(
        {
            "modelVersion": returned_model,
            "candidates": [
                {
                    "content": {"parts": [{"text": text}]},
                    "finishReason": finish_reason,
                }
            ],
            "usageMetadata": {"promptTokenCount": 2, "candidatesTokenCount": 3},
        }
    )


class ProviderTests(unittest.TestCase):
    def test_openai_compatible_request_and_reasoning_content(self):
        transport = QueueTransport([openai_completion("结论", reasoning="内部推理")])
        provider = OpenAICompatibleProvider(
            "deepseek",
            api_key=API_SECRET,
            api_key_env="DEEPSEEK_API_KEY",
            models_env="DEEPSEEK_MODELS",
            base_url="https://example.invalid/v1",
            transport=transport,
        )
        result = provider.generate(
            "deepseek-reasoner",
            system_prompt="",
            user_prompt="测试题",
            parameters={
                "temperature": 0,
                "max_tokens": 100,
                "deepseek_thinking": "disabled",
            },
            timeout=1,
        )
        self.assertEqual(result.response_text, "结论")
        self.assertEqual(result.reasoning_text, "内部推理")
        self.assertEqual(result.returned_model, "server-resolved-model")
        payload = transport.calls[0]["payload"]
        self.assertEqual(payload["model"], "deepseek-reasoner")
        self.assertEqual(payload["messages"], [{"role": "user", "content": "测试题"}])
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertNotIn(API_SECRET, repr(provider))

    def test_deepseek_thinking_parameter_is_not_sent_to_minimax(self):
        transport = QueueTransport([openai_completion("结论")])
        provider = OpenAICompatibleProvider(
            "minimax",
            api_key=API_SECRET,
            api_key_env="MINIMAX_API_KEY",
            models_env="MINIMAX_MODELS",
            base_url="https://example.invalid/v1",
            transport=transport,
        )
        provider.generate(
            "minimax-test",
            system_prompt="",
            user_prompt="测试题",
            parameters={
                "deepseek_thinking": "enabled",
                "max_tokens": 100,
                "minimax_stream": False,
            },
            timeout=1,
        )
        payload = transport.calls[0]["payload"]
        self.assertNotIn("thinking", payload)
        self.assertNotIn("deepseek_thinking", payload)
        self.assertNotIn("max_tokens", payload)
        self.assertEqual(payload["max_completion_tokens"], 100)
        self.assertFalse(payload["stream"])

    def test_omitted_deepseek_thinking_retains_provider_default(self):
        transport = QueueTransport([openai_completion("结论")])
        provider = OpenAICompatibleProvider(
            "deepseek",
            api_key=API_SECRET,
            api_key_env="DEEPSEEK_API_KEY",
            models_env="DEEPSEEK_MODELS",
            base_url="https://example.invalid/v1",
            transport=transport,
        )
        provider.generate(
            "deepseek-test",
            system_prompt="",
            user_prompt="测试题",
            parameters={"max_tokens": 100},
            timeout=1,
        )
        self.assertNotIn("thinking", transport.calls[0]["payload"])

    def test_minimax_stream_flag_does_not_change_deepseek_or_volcengine(self):
        settings = {
            "deepseek": ("DEEPSEEK_API_KEY", "DEEPSEEK_MODELS"),
            "volcengine": ("VOLCENGINE_API_KEY", "VOLCENGINE_MODELS"),
        }
        for name, (key_env, models_env) in settings.items():
            with self.subTest(provider=name):
                transport = QueueTransport([openai_completion("结论")])
                provider = OpenAICompatibleProvider(
                    name,
                    api_key=API_SECRET,
                    api_key_env=key_env,
                    models_env=models_env,
                    base_url="https://example.invalid/v1",
                    transport=transport,
                )
                result = provider.generate(
                    "test-model",
                    system_prompt="",
                    user_prompt="测试题",
                    parameters={"minimax_stream": True, "max_tokens": 100},
                    timeout=1,
                )
                self.assertEqual(result.response_text, "结论")
                payload = transport.calls[0]["payload"]
                self.assertFalse(payload["stream"])
                self.assertEqual(payload["max_tokens"], 100)
                self.assertNotIn("max_completion_tokens", payload)

    def test_minimax_complete_think_blocks_are_separated_but_raw_is_preserved(self):
        content = "<think>推理甲</think>\n最终回答\n<think>推理乙</think>"
        transport = QueueTransport([openai_completion(content)])
        provider = OpenAICompatibleProvider(
            "minimax",
            api_key=API_SECRET,
            api_key_env="MINIMAX_API_KEY",
            models_env="MINIMAX_MODELS",
            base_url="https://example.invalid/v1",
            transport=transport,
        )
        result = provider.generate(
            "minimax-test",
            system_prompt="",
            user_prompt="测试题",
            parameters={"minimax_stream": False},
        )
        self.assertEqual(result.response_text, "最终回答")
        self.assertEqual(result.reasoning_text, "推理甲\n推理乙")
        self.assertEqual(
            result.raw_response["choices"][0]["message"]["content"], content
        )

    def test_incomplete_think_tag_is_not_silently_removed(self):
        content = "<think>未闭合但属于原回答"
        transport = QueueTransport([openai_completion(content)])
        provider = OpenAICompatibleProvider(
            "minimax",
            api_key=API_SECRET,
            api_key_env="MINIMAX_API_KEY",
            models_env="MINIMAX_MODELS",
            base_url="https://example.invalid/v1",
            transport=transport,
        )
        result = provider.generate(
            "minimax-test",
            system_prompt="",
            user_prompt="测试题",
            parameters={"minimax_stream": False},
        )
        self.assertEqual(result.response_text, content)
        self.assertEqual(result.reasoning_text, "")

    def test_minimax_stream_accumulates_deltas_usage_and_audit_chunks(self):
        chunks = (
            {
                "id": "stream-test",
                "model": "MiniMax-server-version",
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "reasoning_content": "显式推理",
                            "content": "<think>内嵌",
                        },
                        "finish_reason": None,
                    }
                ],
                "api_key": API_SECRET,
            },
            {
                "model": "MiniMax-server-version",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": "推理</think>最终"},
                        "finish_reason": None,
                    }
                ],
            },
            {
                "model": "MiniMax-server-version",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": "回答"},
                        "finish_reason": "stop",
                    }
                ],
            },
            {
                "model": "MiniMax-server-version",
                "choices": [],
                "usage": {
                    "prompt_tokens": 8,
                    "completion_tokens": 6,
                    "total_tokens": 14,
                },
            },
        )
        transport = QueueTransport([sse_response(*chunks)])
        provider = OpenAICompatibleProvider(
            "minimax",
            api_key=API_SECRET,
            api_key_env="MINIMAX_API_KEY",
            models_env="MINIMAX_MODELS",
            base_url="https://example.invalid/v1",
            transport=transport,
        )
        result = provider.generate(
            "MiniMax-M2.7",
            system_prompt="",
            user_prompt="测试题",
            parameters={"max_tokens": 321},
        )
        payload = transport.calls[0]["payload"]
        self.assertTrue(payload["stream"])
        self.assertEqual(payload["max_completion_tokens"], 321)
        self.assertNotIn("max_tokens", payload)
        self.assertEqual(result.response_text, "最终回答")
        self.assertEqual(result.reasoning_text, "显式推理\n内嵌推理")
        self.assertEqual(result.finish_reason, "stop")
        self.assertEqual(result.returned_model, "MiniMax-server-version")
        self.assertEqual(result.usage["total_tokens"], 14)
        self.assertTrue(result.raw_response["done"])
        self.assertEqual(result.raw_response["protocol"], "openai-sse")
        self.assertEqual(len(result.raw_response["chunks"]), 4)
        self.assertEqual(
            result.raw_response["chunks"][0]["api_key"], "[REDACTED]"
        )

    def test_minimax_done_without_primary_terminal_finish_is_partial_error(self):
        chunks = (
            {
                "model": "MiniMax-server-version",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": "未完成的部分回答"},
                        "finish_reason": None,
                    },
                    {
                        "index": 1,
                        "delta": {"content": "其他候选"},
                        "finish_reason": "stop",
                    },
                ],
            },
        )
        transport = QueueTransport([sse_response(*chunks, done=True)])
        provider = OpenAICompatibleProvider(
            "minimax",
            api_key=API_SECRET,
            api_key_env="MINIMAX_API_KEY",
            models_env="MINIMAX_MODELS",
            base_url="https://example.invalid/v1",
            transport=transport,
        )

        with self.assertRaisesRegex(
            ProviderRequestError, "without a recognised terminal finish_reason"
        ) as captured:
            provider.generate(
                "MiniMax-M2.7",
                system_prompt="",
                user_prompt="测试题",
                parameters={"max_tokens": 256},
                timeout=1,
            )

        self.assertTrue(captured.exception.retryable)
        self.assertTrue(captured.exception.response_body["partial"])
        self.assertIsNone(
            captured.exception.response_body["terminal_finish_reason"]
        )

    def test_standard_library_sse_parser_accepts_multiline_data_and_done(self):
        response = FakeStreamingResponse(
            [
                ": keepalive\n",
                "data: {\"model\": \"MiniMax-test\",\n",
                "data: \"choices\": []}\n",
                "\n",
                "data: [DONE]\n",
                "\n",
            ]
        )
        transport = JsonHttpTransport()
        with patch("urllib.request.urlopen", return_value=response):
            parsed = transport.request_sse_json(
                "POST",
                "https://example.invalid/chat/completions",
                payload={"stream": True},
                timeout=1,
            )
        self.assertTrue(parsed.done)
        self.assertEqual(
            list(parsed.chunks),
            [{"model": "MiniMax-test", "choices": []}],
        )

    def test_minimax_real_shape_clean_eof_after_stop_is_complete_without_usage(self):
        chunks = [
            {
                "id": "real-shape",
                "model": "MiniMax-M2.7",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": ""},
                        "finish_reason": None,
                    }
                ],
            },
            {
                "id": "real-shape",
                "model": "MiniMax-M2.7",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"reasoning_content": "分析"},
                        "finish_reason": None,
                    }
                ],
            },
            {
                "id": "real-shape",
                "model": "MiniMax-M2.7",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": "最终"},
                        "finish_reason": None,
                    }
                ],
            },
            {
                "id": "real-shape",
                "model": "MiniMax-M2.7",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": "回答"},
                        "finish_reason": "stop",
                    }
                ],
            },
        ]
        lines = []
        for chunk in chunks:
            lines.extend(
                [
                    "data: %s\n" % json.dumps(chunk, ensure_ascii=False),
                    "\n",
                ]
            )
        response = FakeStreamingResponse(lines)
        provider = OpenAICompatibleProvider(
            "minimax",
            api_key=API_SECRET,
            api_key_env="MINIMAX_API_KEY",
            models_env="MINIMAX_MODELS",
            base_url="https://example.invalid/v1",
            transport=JsonHttpTransport(),
        )
        with patch("urllib.request.urlopen", return_value=response):
            result = provider.generate(
                "MiniMax-M2.7",
                system_prompt="",
                user_prompt="测试题",
                parameters={"max_tokens": 256},
                timeout=1,
            )
        self.assertEqual(result.response_text, "最终回答")
        self.assertEqual(result.reasoning_text, "分析")
        self.assertEqual(result.finish_reason, "stop")
        self.assertEqual(result.usage, {})
        self.assertTrue(result.usage_missing)
        self.assertFalse(result.raw_response["done"])
        self.assertEqual(
            result.raw_response["termination"],
            "terminal_finish_reason_eof",
        )
        self.assertTrue(result.raw_response["usage_missing"])
        self.assertEqual(result.raw_response["chunks"], chunks)

    def test_generic_sse_remains_strict_without_provider_opt_in(self):
        response = FakeStreamingResponse(
            [
                'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n',
                "\n",
            ]
        )
        transport = JsonHttpTransport()
        with patch("urllib.request.urlopen", return_value=response):
            with self.assertRaisesRegex(ProviderRequestError, "before the \\[DONE\\]"):
                transport.request_sse_json(
                    "POST",
                    "https://example.invalid/chat/completions",
                    payload={"stream": True},
                    timeout=1,
                )

    def test_sse_connection_loss_preserves_only_partial_attempt_and_is_error(self):
        first_chunk = {
            "model": "MiniMax-test",
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": "未完"},
                    "finish_reason": "stop",
                }
            ],
        }
        response = FakeStreamingResponse(
            [
                "data: %s\n" % json.dumps(first_chunk, ensure_ascii=False),
                "\n",
                http.client.RemoteDisconnected("private transport detail"),
            ]
        )
        transport = JsonHttpTransport()
        with patch("urllib.request.urlopen", return_value=response):
            with self.assertRaises(ProviderRequestError) as captured:
                transport.request_sse_json(
                    "POST",
                    "https://example.invalid/chat/completions",
                    payload={"stream": True},
                    timeout=1,
                    allow_eof_after_finish_reason=True,
                )
        error = captured.exception
        self.assertTrue(error.retryable)
        self.assertEqual(error.status_code, 200)
        self.assertTrue(error.response_body["partial"])
        self.assertFalse(error.response_body["done"])
        self.assertEqual(error.response_body["chunks"], [first_chunk])
        self.assertNotIn("private transport detail", str(error))

    def test_sse_clean_eof_without_done_is_partial_error(self):
        response = FakeStreamingResponse(
            [
                'data: {"choices":[{"index":0,"delta":{"content":"partial"}}]}\n',
                "\n",
            ]
        )
        transport = JsonHttpTransport()
        with patch("urllib.request.urlopen", return_value=response):
            with self.assertRaisesRegex(ProviderRequestError, "before the \\[DONE\\]") as captured:
                transport.request_sse_json(
                    "POST",
                    "https://example.invalid/chat/completions",
                    payload={"stream": True},
                    timeout=1,
                    allow_eof_after_finish_reason=True,
                )
        self.assertTrue(captured.exception.response_body["partial"])
        self.assertFalse(captured.exception.response_body["done"])

    def test_gemini_sse_accepts_clean_eof_only_after_terminal_finish_reason(self):
        terminal_chunk = {
            "modelVersion": "gemma-server-version",
            "candidates": [
                {
                    "index": 0,
                    "content": {"parts": [{"text": "完成"}]},
                    "finishReason": "STOP",
                }
            ],
        }
        response = FakeStreamingResponse(
            [
                "data: %s\n" % json.dumps(terminal_chunk, ensure_ascii=False),
                "\n",
            ]
        )
        transport = JsonHttpTransport()
        with patch("urllib.request.urlopen", return_value=response):
            parsed = transport.request_sse_json(
                "POST",
                "https://example.invalid/models/gemma:streamGenerateContent?alt=sse",
                payload={"contents": []},
                timeout=1,
                allow_eof_after_gemini_finish_reason=True,
                protocol="gemini-sse",
            )
        self.assertFalse(parsed.done)
        self.assertEqual(parsed.termination, "gemini_finish_reason_eof")
        self.assertEqual(list(parsed.chunks), [terminal_chunk])

        incomplete_response = FakeStreamingResponse(
            [
                'data: {"candidates":[{"index":0,"content":{"parts":[{"text":"partial"}]}}]}\n',
                "\n",
            ]
        )
        with patch("urllib.request.urlopen", return_value=incomplete_response):
            with self.assertRaisesRegex(
                ProviderRequestError, "terminal Gemini finishReason"
            ) as captured:
                transport.request_sse_json(
                    "POST",
                    "https://example.invalid/models/gemma:streamGenerateContent?alt=sse",
                    payload={"contents": []},
                    timeout=1,
                    allow_eof_after_gemini_finish_reason=True,
                    protocol="gemini-sse",
                )
        self.assertTrue(captured.exception.retryable)
        self.assertEqual(captured.exception.response_body["protocol"], "gemini-sse")
        self.assertTrue(captured.exception.response_body["partial"])

    def test_gemini_sse_disconnect_is_error_even_after_terminal_chunk(self):
        terminal_chunk = {
            "candidates": [
                {
                    "index": 0,
                    "content": {"parts": [{"text": "看似完成"}]},
                    "finishReason": "STOP",
                }
            ]
        }
        response = FakeStreamingResponse(
            [
                "data: %s\n" % json.dumps(terminal_chunk, ensure_ascii=False),
                "\n",
                http.client.RemoteDisconnected("private transport detail"),
            ]
        )
        transport = JsonHttpTransport()
        with patch("urllib.request.urlopen", return_value=response):
            with self.assertRaises(ProviderRequestError) as captured:
                transport.request_sse_json(
                    "POST",
                    "https://example.invalid/models/gemma:streamGenerateContent?alt=sse",
                    payload={"contents": []},
                    timeout=1,
                    allow_eof_after_gemini_finish_reason=True,
                    protocol="gemini-sse",
                )
        self.assertTrue(captured.exception.retryable)
        self.assertEqual(captured.exception.response_body["protocol"], "gemini-sse")
        self.assertEqual(captured.exception.response_body["chunks"], [terminal_chunk])
        self.assertNotIn("private transport detail", str(captured.exception))

    def test_gemini_thought_parts_stay_raw_but_not_in_final_text(self):
        raw = {
            "modelVersion": "gemini-server-resolved-version",
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "隐藏推理", "thought": True},
                            {"text": "最终"},
                            {"text": "回答"},
                        ]
                    },
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {"promptTokenCount": 2, "candidatesTokenCount": 3},
        }
        transport = QueueTransport([json_response(raw)])
        provider = GeminiProvider(
            "gemini",
            api_key=API_SECRET,
            api_key_env="GEMINI_API_KEY",
            models_env="GEMINI_MODELS",
            base_url="https://example.invalid/v1beta",
            transport=transport,
        )
        result = provider.generate(
            "models/gemini-test",
            system_prompt="系统",
            user_prompt="测试题",
            parameters={"max_tokens": 100},
        )
        self.assertEqual(result.response_text, "最终回答")
        self.assertEqual(result.reasoning_text, "隐藏推理")
        self.assertEqual(result.returned_model, "gemini-server-resolved-version")
        self.assertTrue(
            result.raw_response["candidates"][0]["content"]["parts"][0]["thought"]
        )
        self.assertNotIn(API_SECRET, transport.calls[0]["url"])
        self.assertEqual(transport.calls[0]["headers"]["x-goog-api-key"], API_SECRET)

    def test_gemini_stream_accumulates_auditable_chunks_and_terminal_metadata(self):
        chunks = (
            {
                "modelVersion": "gemma-server-version",
                "candidates": [
                    {
                        "index": 0,
                        "content": {
                            "parts": [
                                {"text": "隐藏思考", "thought": True},
                                {"text": "最终"},
                            ]
                        },
                    }
                ],
                "api_key": API_SECRET,
            },
            {
                "modelVersion": "gemma-server-version",
                "candidates": [
                    {
                        "index": 0,
                        "content": {"parts": [{"text": "回答"}]},
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 4,
                    "candidatesTokenCount": 6,
                    "totalTokenCount": 10,
                },
            },
        )
        transport = QueueTransport(
            [
                sse_response(
                    *chunks,
                    done=False,
                    termination="gemini_finish_reason_eof",
                )
            ]
        )
        provider = GeminiProvider(
            "gemini",
            api_key=API_SECRET,
            api_key_env="GEMINI_API_KEY",
            models_env="GEMINI_MODELS",
            base_url="https://example.invalid/v1beta",
            transport=transport,
        )
        result = provider.generate(
            "models/gemma-4-31b-it",
            system_prompt="系统",
            user_prompt="测试题",
            parameters={"max_tokens": 8192, "gemini_stream": True},
            timeout=1,
        )
        call = transport.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertEqual(
            call["url"],
            "https://example.invalid/v1beta/models/gemma-4-31b-it:streamGenerateContent?alt=sse",
        )
        self.assertNotIn(API_SECRET, call["url"])
        self.assertEqual(call["headers"]["x-goog-api-key"], API_SECRET)
        self.assertTrue(call["allow_eof_after_gemini_finish_reason"])
        self.assertEqual(call["protocol"], "gemini-sse")
        self.assertEqual(call["payload"]["generationConfig"]["maxOutputTokens"], 8192)
        self.assertEqual(result.response_text, "最终回答")
        self.assertEqual(result.reasoning_text, "隐藏思考")
        self.assertEqual(result.finish_reason, "STOP")
        self.assertEqual(result.returned_model, "gemma-server-version")
        self.assertEqual(result.usage["totalTokenCount"], 10)
        self.assertFalse(result.usage_missing)
        self.assertFalse(result.raw_response["done"])
        self.assertEqual(
            result.raw_response["termination"], "gemini_finish_reason_eof"
        )
        self.assertEqual(result.raw_response["terminal_finish_reason"], "STOP")
        self.assertEqual(len(result.raw_response["chunks"]), 2)
        self.assertEqual(
            result.raw_response["chunks"][0]["api_key"], "[REDACTED]"
        )

    def test_gemini_stream_done_sentinel_without_finish_reason_is_rejected(self):
        transport = QueueTransport(
            [
                sse_response(
                    {
                        "candidates": [
                            {
                                "index": 0,
                                "content": {"parts": [{"text": "partial"}]},
                            }
                        ]
                    }
                )
            ]
        )
        provider = GeminiProvider(
            "gemini",
            api_key=API_SECRET,
            api_key_env="GEMINI_API_KEY",
            models_env="GEMINI_MODELS",
            base_url="https://example.invalid/v1beta",
            transport=transport,
        )
        with self.assertRaisesRegex(
            ProviderRequestError, "terminal candidate finishReason"
        ) as captured:
            provider.generate(
                "models/gemini-test",
                system_prompt="",
                user_prompt="测试题",
                parameters={"gemini_stream": True},
            )
        self.assertEqual(
            captured.exception.response_body["protocol"], "gemini-sse"
        )

    def test_gemini_model_capability_is_exposed(self):
        transport = QueueTransport(
            [
                json_response(
                    {
                        "models": [
                            {
                                "name": "models/generate-me",
                                "supportedGenerationMethods": ["generateContent"],
                            },
                            {
                                "name": "models/embed-only",
                                "supportedGenerationMethods": ["embedContent"],
                            },
                            {
                                "name": "models/gemini-image-generation-test",
                                "supportedGenerationMethods": ["generateContent"],
                            },
                            {
                                "name": "models/gemini-2.5-flash-image",
                                "supportedGenerationMethods": ["generateContent"],
                            },
                            {
                                "name": "models/lyria-realtime-exp",
                                "supportedGenerationMethods": ["generateContent"],
                            },
                            {
                                "name": "models/gemini-omni-preview",
                                "supportedGenerationMethods": ["generateContent"],
                            },
                            {
                                "name": "models/deep-research-pro-preview",
                                "supportedGenerationMethods": ["generateContent"],
                            },
                            {
                                "name": "models/antigravity-agent-preview",
                                "supportedGenerationMethods": ["generateContent"],
                            },
                        ]
                    }
                )
            ]
        )
        provider = GeminiProvider(
            "gemini",
            api_key=API_SECRET,
            api_key_env="GEMINI_API_KEY",
            models_env="GEMINI_MODELS",
            base_url="https://example.invalid/v1beta",
            transport=transport,
        )
        models = provider.list_models()
        self.assertTrue(models[0].supports_generation)
        self.assertTrue(all(not model.supports_generation for model in models[1:]))

    def test_nested_credentials_are_redacted(self):
        value = {
            "api_key": API_SECRET,
            "message": "authorization=abc and %s" % API_SECRET,
            "nested": ["Bearer visible-token"],
        }
        serialised = json.dumps(redact_object(value, (API_SECRET,)))
        self.assertNotIn(API_SECRET, serialised)
        self.assertNotIn("visible-token", serialised)
        self.assertNotIn("abc", serialised)

    def test_base_url_rejects_query_credentials(self):
        with self.assertRaises(ValueError):
            build_provider(
                "deepseek",
                environ={
                    "DEEPSEEK_API_KEY": API_SECRET,
                    "DEEPSEEK_BASE_URL": "https://example.invalid/v1?key=bad",
                },
            )

    def test_disconnected_http_and_connection_errors_are_safe_and_retryable(self):
        transport = JsonHttpTransport()
        failures = (
            http.client.RemoteDisconnected("remote detail must not escape"),
            http.client.HTTPException("protocol detail must not escape"),
            ConnectionResetError("socket detail must not escape"),
        )
        for failure in failures:
            with self.subTest(failure=failure.__class__.__name__):
                with patch("urllib.request.urlopen", side_effect=failure):
                    with self.assertRaises(ProviderRequestError) as captured:
                        transport.request_json(
                            "GET",
                            "https://example.invalid/models",
                            headers={"Authorization": "Bearer " + API_SECRET},
                            timeout=1,
                        )
                error = captured.exception
                self.assertTrue(error.retryable)
                self.assertIsNone(error.response_body)
                self.assertNotIn("detail must not escape", str(error))
                self.assertNotIn(API_SECRET, str(error))


class RetryTests(unittest.TestCase):
    def test_retry_uses_safe_error_and_eventually_succeeds(self):
        provider = build_provider(
            "deepseek", environ={"DEEPSEEK_API_KEY": API_SECRET}
        )
        calls = []
        sleeps = []

        def operation():
            calls.append(True)
            if len(calls) == 1:
                raise ProviderRequestError(
                    "temporary error containing %s" % API_SECRET,
                    status_code=429,
                    retryable=True,
                    retry_after=0.25,
                    response_body={"authorization": API_SECRET},
                )
            return "ok"

        value, attempts, history = call_with_retry(
            operation,
            provider=provider,
            max_attempts=2,
            initial_backoff_seconds=0.1,
            max_backoff_seconds=1,
            sleep=sleeps.append,
        )
        self.assertEqual(value, "ok")
        self.assertEqual(attempts, 2)
        self.assertEqual(sleeps, [0.25])
        self.assertNotIn(API_SECRET, json.dumps(history))


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.parser = build_parser()
        self.env = {"DEEPSEEK_API_KEY": API_SECRET}

    def provider_factory_for(self, transport):
        def factory(name, *, environ):
            return build_provider(name, environ=environ, transport=transport)

        return factory

    def test_default_input_is_the_24_line_jsonl(self):
        from run_model_evaluations import DEFAULT_INPUT

        self.assertEqual(len(load_questions(DEFAULT_INPUT)), 24)

    def test_dry_run_makes_no_transport_calls_or_output_files(self):
        transport = QueueTransport([])
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "should-not-exist"
            args = self.parser.parse_args(
                [
                    "run",
                    "--provider",
                    "deepseek",
                    "--model",
                    "deepseek=deepseek-chat",
                    "--output-dir",
                    str(output_dir),
                    "--dry-run",
                ]
            )
            captured = io.StringIO()
            with redirect_stdout(captured):
                code = run_command(
                    args,
                    environ={},
                    provider_factory=self.provider_factory_for(transport),
                )
            self.assertEqual(code, 0)
            self.assertEqual(transport.calls, [])
            self.assertFalse(output_dir.exists())
            summary = json.loads(captured.getvalue())
            self.assertEqual(summary["network_requests_made"], 0)
            self.assertEqual(summary["planned_generation_request_count"], 24)
            self.assertNotIn(
                "deepseek_thinking", summary["generation_parameters"]
            )
            self.assertNotIn("gemini_stream", summary["generation_parameters"])

    def test_dry_run_audits_explicit_deepseek_thinking_mode(self):
        transport = QueueTransport([])
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "should-not-exist"
            args = self.parser.parse_args(
                [
                    "run",
                    "--provider",
                    "deepseek",
                    "--model",
                    "deepseek=deepseek-v4-pro",
                    "--deepseek-thinking",
                    "disabled",
                    "--output-dir",
                    str(output_dir),
                    "--dry-run",
                ]
            )
            captured = io.StringIO()
            with redirect_stdout(captured):
                code = run_command(
                    args,
                    environ={},
                    provider_factory=self.provider_factory_for(transport),
                )
            self.assertEqual(code, 0)
            self.assertEqual(transport.calls, [])
            summary = json.loads(captured.getvalue())
            self.assertEqual(
                summary["generation_parameters"]["deepseek_thinking"], "disabled"
            )

    def test_minimax_stream_cli_is_audited_and_defaults_on_with_opt_out(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = [
                "run",
                "--provider",
                "minimax",
                "--model",
                "minimax=MiniMax-M2.7",
                "--output-dir",
                str(Path(temp_dir) / "unused"),
                "--dry-run",
            ]
            for flag, expected in ((None, True), ("--minimax-stream", True), ("--no-minimax-stream", False)):
                with self.subTest(flag=flag):
                    argv = list(base)
                    if flag:
                        argv.append(flag)
                    captured = io.StringIO()
                    with redirect_stdout(captured):
                        self.assertEqual(
                            run_command(self.parser.parse_args(argv), environ={}),
                            0,
                        )
                    summary = json.loads(captured.getvalue())
                    self.assertIs(
                        summary["generation_parameters"]["minimax_stream"],
                        expected,
                    )

    def test_gemini_stream_cli_defaults_off_and_is_audited_with_opt_in(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = [
                "run",
                "--provider",
                "gemini",
                "--model",
                "gemini=models/gemma-4-31b-it",
                "--output-dir",
                str(Path(temp_dir) / "unused"),
                "--dry-run",
            ]
            for flag, expected in (
                (None, False),
                ("--gemini-stream", True),
                ("--no-gemini-stream", False),
            ):
                with self.subTest(flag=flag):
                    argv = list(base)
                    if flag:
                        argv.append(flag)
                    captured = io.StringIO()
                    with redirect_stdout(captured):
                        self.assertEqual(
                            run_command(self.parser.parse_args(argv), environ={}),
                            0,
                        )
                    summary = json.loads(captured.getvalue())
                    self.assertIs(
                        summary["generation_parameters"]["gemini_stream"],
                        expected,
                    )

    def test_gemini_stream_run_freezes_transport_in_manifest_record_and_hash(self):
        transport = QueueTransport(
            [
                sse_response(
                    {
                        "modelVersion": "gemma-server-version",
                        "candidates": [
                            {
                                "index": 0,
                                "content": {"parts": [{"text": "可评分回答"}]},
                                "finishReason": "STOP",
                            }
                        ],
                        "usageMetadata": {"totalTokenCount": 12},
                    },
                    done=False,
                    termination="gemini_finish_reason_eof",
                )
            ]
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "gemini-stream-output"
            args = self.parser.parse_args(
                [
                    "run",
                    "--provider",
                    "gemini",
                    "--model",
                    "gemini=models/gemma-4-31b-it",
                    "--output-dir",
                    str(output_dir),
                    "--limit-questions",
                    "1",
                    "--gemini-stream",
                    "--max-tokens",
                    "8192",
                    "--min-interval",
                    "0",
                    "--execute",
                ]
            )
            with redirect_stdout(io.StringIO()):
                self.assertEqual(
                    run_command(
                        args,
                        environ={"GEMINI_API_KEY": API_SECRET},
                        provider_factory=self.provider_factory_for(transport),
                    ),
                    0,
                )
            manifest = json.loads(
                (output_dir / MANIFEST_FILENAME).read_text(encoding="utf-8")
            )
            record = json.loads(
                (output_dir / RECORDS_FILENAME).read_text(encoding="utf-8")
            )
            self.assertTrue(manifest["generation_parameters"]["gemini_stream"])
            self.assertTrue(record["request"]["parameters"]["gemini_stream"])
            self.assertEqual(record["raw_response"]["protocol"], "gemini-sse")
            self.assertEqual(record["raw_response"]["terminal_finish_reason"], "STOP")
            self.assertEqual(record["final_response_text"], "可评分回答")
            self.assertEqual(record["returned_model"], "gemma-server-version")
            self.assertEqual(
                record["hashes"]["request_sha256"],
                sha256_json(
                    {
                        "provider": "gemini",
                        "model": "models/gemma-4-31b-it",
                        **record["request"],
                    }
                ),
            )
            original_manifest = (output_dir / MANIFEST_FILENAME).read_bytes()
            original_records = (output_dir / RECORDS_FILENAME).read_bytes()
            resume_args = self.parser.parse_args(
                [
                    "run",
                    "--provider",
                    "gemini",
                    "--model",
                    "gemini=models/gemma-4-31b-it",
                    "--output-dir",
                    str(output_dir),
                    "--limit-questions",
                    "1",
                    "--no-gemini-stream",
                    "--max-tokens",
                    "8192",
                    "--min-interval",
                    "0",
                    "--execute",
                    "--resume",
                ]
            )
            with self.assertRaisesRegex(ValueError, "resume configuration"):
                run_command(
                    resume_args,
                    environ={"GEMINI_API_KEY": API_SECRET},
                    provider_factory=self.provider_factory_for(transport),
                )
            self.assertEqual(len(transport.calls), 1)
            self.assertEqual(
                (output_dir / MANIFEST_FILENAME).read_bytes(), original_manifest
            )
            self.assertEqual((output_dir / RECORDS_FILENAME).read_bytes(), original_records)

    def test_minimax_stream_retry_attempts_are_not_mixed(self):
        first_attempt_chunk = {
            "model": "attempt-one-model",
            "choices": [{"index": 0, "delta": {"content": "attempt-one"}}],
        }
        partial_failure = ProviderRequestError(
            "provider stream network request failed (RemoteDisconnected)",
            status_code=200,
            retryable=True,
            response_body={
                "stream": True,
                "protocol": "openai-sse",
                "done": False,
                "partial": True,
                "chunks": [first_attempt_chunk],
            },
        )
        second_attempt_chunks = (
            {
                "model": "attempt-two-model",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": "second-attempt-final"},
                        "finish_reason": None,
                    }
                ],
            },
            {
                "model": "attempt-two-model",
                "choices": [
                    {
                        "index": 0,
                        "delta": {},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"total_tokens": 11},
            },
        )
        transport = QueueTransport(
            [partial_failure, sse_response(*second_attempt_chunks)]
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "minimax-stream-retry"
            args = self.parser.parse_args(
                [
                    "run",
                    "--provider",
                    "minimax",
                    "--model",
                    "minimax=MiniMax-M2.7",
                    "--output-dir",
                    str(output_dir),
                    "--limit-questions",
                    "1",
                    "--min-interval",
                    "0",
                    "--initial-backoff",
                    "0",
                    "--max-backoff",
                    "0",
                    "--max-attempts",
                    "2",
                    "--execute",
                ]
            )
            with redirect_stdout(io.StringIO()):
                self.assertEqual(
                    run_command(
                        args,
                        environ={"MINIMAX_API_KEY": API_SECRET},
                        provider_factory=self.provider_factory_for(transport),
                    ),
                    0,
                )
            record = json.loads(
                (output_dir / RECORDS_FILENAME).read_text(encoding="utf-8")
            )
            self.assertEqual(record["status"], "success")
            self.assertEqual(record["attempt_count"], 2)
            self.assertEqual(record["response_text"], "second-attempt-final")
            self.assertEqual(record["returned_model"], "attempt-two-model")
            self.assertEqual(
                record["raw_response"]["chunks"], list(second_attempt_chunks)
            )
            self.assertNotIn("attempt-one", json.dumps(record["raw_response"]))
            self.assertIn(
                "attempt-one",
                json.dumps(record["retry_history"], ensure_ascii=False),
            )
            self.assertEqual(record["usage"]["total_tokens"], 11)
            self.assertTrue(
                all(call["payload"]["stream"] for call in transport.calls)
            )
            self.assertTrue(
                all(
                    call["payload"]["max_completion_tokens"] == 2048
                    for call in transport.calls
                )
            )
            self.assertTrue(
                all("max_tokens" not in call["payload"] for call in transport.calls)
            )

    def test_explicit_model_does_not_append_inherited_models_environment(self):
        transport = QueueTransport([])
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "dry-output"
            args = self.parser.parse_args(
                [
                    "run",
                    "--provider",
                    "deepseek",
                    "--model",
                    "deepseek=explicit-model",
                    "--output-dir",
                    str(output_dir),
                    "--dry-run",
                ]
            )
            captured = io.StringIO()
            with redirect_stdout(captured):
                self.assertEqual(
                    run_command(
                        args,
                        environ={"DEEPSEEK_MODELS": "inherited-a,inherited-b"},
                        provider_factory=self.provider_factory_for(transport),
                    ),
                    0,
                )
            summary = json.loads(captured.getvalue())
            self.assertEqual(summary["provider_models"]["deepseek"], ["explicit-model"])
            self.assertEqual(summary["planned_generation_request_count"], 24)

    def test_execute_all_models_requires_openai_catalog_confirmation(self):
        transport = QueueTransport([])
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "must-not-exist"
            args = self.parser.parse_args(
                [
                    "run",
                    "--provider",
                    "deepseek",
                    "--all-models",
                    "--output-dir",
                    str(output_dir),
                    "--execute",
                ]
            )
            with self.assertRaisesRegex(ValueError, "not a reviewed text-model whitelist"):
                run_command(
                    args,
                    environ=self.env,
                    provider_factory=self.provider_factory_for(transport),
                )
            self.assertEqual(transport.calls, [])
            self.assertFalse(output_dir.exists())

    def test_execute_writes_stable_pilot_unscored_records_and_manifest(self):
        transport = QueueTransport(
            [
                openai_completion(
                    "可评分回答",
                    reasoning="推理内容",
                    extra={"api_key": API_SECRET},
                )
            ]
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "pilot-output"
            args = self.parser.parse_args(
                [
                    "run",
                    "--provider",
                    "deepseek",
                    "--model",
                    "deepseek=deepseek-reasoner",
                    "--output-dir",
                    str(output_dir),
                    "--limit-questions",
                    "1",
                    "--deepseek-thinking",
                    "disabled",
                    "--min-interval",
                    "0",
                    "--execute",
                ]
            )
            with redirect_stdout(io.StringIO()):
                code = run_command(
                    args,
                    environ=self.env,
                    provider_factory=self.provider_factory_for(transport),
                )
            self.assertEqual(code, 0)
            manifest_text = (output_dir / MANIFEST_FILENAME).read_text(encoding="utf-8")
            records_text = (output_dir / RECORDS_FILENAME).read_text(encoding="utf-8")
            self.assertNotIn(API_SECRET, manifest_text)
            self.assertNotIn(API_SECRET, records_text)
            manifest = json.loads(manifest_text)
            record = json.loads(records_text)
            self.assertEqual(manifest["evaluation_phase"], "pilot")
            self.assertEqual(manifest["scoring_status"], "unscored")
            self.assertEqual(manifest["success_count"], 1)
            self.assertEqual(manifest["run_status"], "completed")
            self.assertEqual(manifest["provider_order"], ["deepseek"])
            self.assertEqual(manifest["input"]["selected_question_ids"], ["WAI-001"])
            self.assertEqual(
                manifest["generation_parameters"]["deepseek_thinking"], "disabled"
            )
            expected_fields = {
                "provider",
                "model",
                "returned_model",
                "question_id",
                "status",
                "usage",
                "request_started_at",
                "response_received_at",
                "hashes",
                "response_text",
                "final_response_text",
                "response_truncated",
                "raw_response",
            }
            self.assertTrue(expected_fields.issubset(record))
            self.assertEqual(record["response_text"], "可评分回答")
            self.assertEqual(record["final_response_text"], "可评分回答")
            self.assertEqual(record["returned_model"], "server-resolved-model")
            self.assertFalse(record["response_truncated"])
            self.assertEqual(record["reasoning_text"], "推理内容")
            self.assertEqual(
                record["request"]["parameters"]["deepseek_thinking"], "disabled"
            )
            self.assertEqual(
                record["hashes"]["request_sha256"],
                sha256_json(
                    {
                        "provider": "deepseek",
                        "model": "deepseek-reasoner",
                        **record["request"],
                    }
                ),
            )
            self.assertEqual(
                transport.calls[0]["payload"]["thinking"], {"type": "disabled"}
            )
            self.assertEqual(record["raw_response"]["api_key"], "[REDACTED]")
            self.assertEqual(len(record["hashes"]["raw_response_sha256"]), 64)

    def test_resume_skips_success_without_another_api_call(self):
        first_transport = QueueTransport([openai_completion()])
        second_transport = QueueTransport([])
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "resume-output"
            base_args = [
                "run",
                "--provider",
                "deepseek",
                "--model",
                "deepseek=deepseek-chat",
                "--output-dir",
                str(output_dir),
                "--limit-questions",
                "1",
                "--min-interval",
                "0",
                "--execute",
            ]
            with redirect_stdout(io.StringIO()):
                self.assertEqual(
                    run_command(
                        self.parser.parse_args(base_args),
                        environ=self.env,
                        provider_factory=self.provider_factory_for(first_transport),
                    ),
                    0,
                )
                self.assertEqual(
                    run_command(
                        self.parser.parse_args(base_args + ["--resume"]),
                        environ=self.env,
                        provider_factory=self.provider_factory_for(second_transport),
                    ),
                    0,
                )
            self.assertEqual(second_transport.calls, [])
            lines = (output_dir / RECORDS_FILENAME).read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            manifest = json.loads(
                (output_dir / MANIFEST_FILENAME).read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["skipped_existing_count"], 1)

    def test_resume_rejects_configuration_drift_without_overwriting_manifest(self):
        first_transport = QueueTransport([openai_completion()])
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "resume-integrity-output"
            base_args = [
                "run",
                "--provider",
                "deepseek",
                "--model",
                "deepseek=deepseek-chat",
                "--output-dir",
                str(output_dir),
                "--limit-questions",
                "1",
                "--min-interval",
                "0",
                "--execute",
            ]
            with redirect_stdout(io.StringIO()):
                self.assertEqual(
                    run_command(
                        self.parser.parse_args(base_args),
                        environ=self.env,
                        provider_factory=self.provider_factory_for(first_transport),
                    ),
                    0,
                )
            manifest_path = output_dir / MANIFEST_FILENAME
            records_path = output_dir / RECORDS_FILENAME
            original_manifest = manifest_path.read_bytes()
            original_records = records_path.read_bytes()
            drift_variants = {
                "questions": ["--question-id", "WAI-002"],
                "models": ["--model", "deepseek=another-model"],
                "prompt": ["--system-prompt", "different system prompt"],
                "parameters": ["--max-tokens", "4096"],
                "deepseek thinking": ["--deepseek-thinking", "disabled"],
                "run_id": ["--run-id", "different-run-id"],
            }
            for name, drift in drift_variants.items():
                with self.subTest(drift=name):
                    transport = QueueTransport([])
                    args = self.parser.parse_args(base_args + drift + ["--resume"])
                    with self.assertRaisesRegex(ValueError, "resume configuration"):
                        run_command(
                            args,
                            environ=self.env,
                            provider_factory=self.provider_factory_for(transport),
                        )
                    self.assertEqual(transport.calls, [])
                    self.assertEqual(manifest_path.read_bytes(), original_manifest)
                    self.assertEqual(records_path.read_bytes(), original_records)

    def test_retry_error_body_is_redacted_in_persisted_history(self):
        failure = ProviderRequestError(
            "rate limited: %s" % API_SECRET,
            status_code=429,
            retryable=True,
            retry_after=0,
            response_body={"access_token": API_SECRET},
        )
        transport = QueueTransport([failure, openai_completion("重试成功")])
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "retry-output"
            args = self.parser.parse_args(
                [
                    "run",
                    "--provider",
                    "deepseek",
                    "--model",
                    "deepseek=deepseek-chat",
                    "--output-dir",
                    str(output_dir),
                    "--limit-questions",
                    "1",
                    "--min-interval",
                    "0",
                    "--initial-backoff",
                    "0",
                    "--max-backoff",
                    "0",
                    "--max-attempts",
                    "2",
                    "--execute",
                ]
            )
            with redirect_stdout(io.StringIO()):
                self.assertEqual(
                    run_command(
                        args,
                        environ=self.env,
                        provider_factory=self.provider_factory_for(transport),
                    ),
                    0,
                )
            record_text = (output_dir / RECORDS_FILENAME).read_text(encoding="utf-8")
            self.assertNotIn(API_SECRET, record_text)
            record = json.loads(record_text)
            self.assertEqual(record["attempt_count"], 2)
            self.assertEqual(record["status"], "success")
            self.assertEqual(len(record["retry_history"]), 1)

    def test_empty_final_response_is_an_error_but_raw_reasoning_is_preserved(self):
        transport = QueueTransport(
            [
                openai_completion(
                    "",
                    reasoning="private reasoning retained for local audit",
                    returned_model="resolved-empty-model",
                    finish_reason="stop",
                )
            ]
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "empty-output"
            args = self.parser.parse_args(
                [
                    "run",
                    "--provider",
                    "deepseek",
                    "--model",
                    "deepseek=thinking-model",
                    "--output-dir",
                    str(output_dir),
                    "--limit-questions",
                    "1",
                    "--min-interval",
                    "0",
                    "--execute",
                ]
            )
            with redirect_stdout(io.StringIO()):
                self.assertEqual(
                    run_command(
                        args,
                        environ=self.env,
                        provider_factory=self.provider_factory_for(transport),
                    ),
                    1,
                )
            record = json.loads(
                (output_dir / RECORDS_FILENAME).read_text(encoding="utf-8")
            )
            manifest = json.loads(
                (output_dir / MANIFEST_FILENAME).read_text(encoding="utf-8")
            )
            self.assertEqual(record["status"], "error")
            self.assertEqual(record["error"]["type"], "EmptyFinalResponseError")
            self.assertEqual(record["finish_reason"], "stop")
            self.assertFalse(record["response_truncated"])
            self.assertEqual(record["returned_model"], "resolved-empty-model")
            self.assertEqual(
                record["reasoning_text"], "private reasoning retained for local audit"
            )
            self.assertEqual(record["raw_response"]["model"], "resolved-empty-model")
            self.assertEqual(manifest["success_count"], 0)
            self.assertEqual(manifest["error_count"], 1)
            self.assertEqual(manifest["run_status"], "failed")

    def test_token_limited_partial_responses_are_errors_for_all_transports(self):
        cases = (
            (
                "deepseek",
                "deepseek=thinking-model",
                self.env,
                openai_completion(
                    "partial OpenAI answer",
                    finish_reason="length",
                    returned_model="resolved-openai-model",
                ),
                "resolved-openai-model",
            ),
            (
                "minimax",
                "minimax=MiniMax-M2.7",
                {"MINIMAX_API_KEY": API_SECRET},
                sse_response(
                    {
                        "model": "resolved-minimax-model",
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"content": "partial MiniMax answer"},
                                "finish_reason": "length",
                            }
                        ],
                    },
                    done=False,
                    termination="terminal_finish_reason_eof",
                ),
                "resolved-minimax-model",
            ),
            (
                "gemini",
                "gemini=models/gemini-test",
                {"GEMINI_API_KEY": API_SECRET},
                gemini_completion(
                    "partial Gemini answer",
                    finish_reason="MAX_TOKENS",
                    returned_model="resolved-gemini-model",
                ),
                "resolved-gemini-model",
            ),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            for provider, model_assignment, env, response, returned_model in cases:
                with self.subTest(provider=provider):
                    transport = QueueTransport([response])
                    output_dir = Path(temp_dir) / provider
                    args = self.parser.parse_args(
                        [
                            "run",
                            "--provider",
                            provider,
                            "--model",
                            model_assignment,
                            "--output-dir",
                            str(output_dir),
                            "--limit-questions",
                            "1",
                            "--min-interval",
                            "0",
                            "--execute",
                        ]
                    )
                    with redirect_stdout(io.StringIO()):
                        self.assertEqual(
                            run_command(
                                args,
                                environ=env,
                                provider_factory=self.provider_factory_for(transport),
                            ),
                            1,
                        )
                    record = json.loads(
                        (output_dir / RECORDS_FILENAME).read_text(encoding="utf-8")
                    )
                    self.assertEqual(record["status"], "error")
                    self.assertEqual(record["error"]["type"], "TruncatedResponseError")
                    self.assertTrue(record["response_truncated"])
                    self.assertTrue(record["final_response_text"].startswith("partial"))
                    self.assertEqual(record["returned_model"], returned_model)
                    self.assertIsNotNone(record["raw_response"])
                    if provider == "minimax":
                        self.assertTrue(record["usage_missing"])
                        self.assertEqual(record["usage"], {})
                        self.assertFalse(record["raw_response"]["done"])

    def test_gemini_stream_max_tokens_is_persisted_as_truncated_error(self):
        transport = QueueTransport(
            [
                sse_response(
                    {
                        "modelVersion": "gemma-server-version",
                        "candidates": [
                            {
                                "index": 0,
                                "content": {"parts": [{"text": "partial stream"}]},
                                "finishReason": "MAX_TOKENS",
                            }
                        ],
                        "usageMetadata": {"totalTokenCount": 8192},
                    },
                    done=False,
                    termination="gemini_finish_reason_eof",
                )
            ]
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "gemini-stream-truncated"
            args = self.parser.parse_args(
                [
                    "run",
                    "--provider",
                    "gemini",
                    "--model",
                    "gemini=models/gemma-4-31b-it",
                    "--output-dir",
                    str(output_dir),
                    "--limit-questions",
                    "1",
                    "--gemini-stream",
                    "--min-interval",
                    "0",
                    "--execute",
                ]
            )
            with redirect_stdout(io.StringIO()):
                self.assertEqual(
                    run_command(
                        args,
                        environ={"GEMINI_API_KEY": API_SECRET},
                        provider_factory=self.provider_factory_for(transport),
                    ),
                    1,
                )
            record = json.loads(
                (output_dir / RECORDS_FILENAME).read_text(encoding="utf-8")
            )
            self.assertEqual(record["status"], "error")
            self.assertEqual(record["error"]["type"], "TruncatedResponseError")
            self.assertTrue(record["response_truncated"])
            self.assertEqual(record["finish_reason"], "MAX_TOKENS")
            self.assertEqual(record["final_response_text"], "partial stream")
            self.assertEqual(record["raw_response"]["protocol"], "gemini-sse")

    def test_partial_api_failure_returns_nonzero_and_marks_completed_with_errors(self):
        failure = ProviderRequestError(
            "provider HTTP request failed with status 401",
            status_code=401,
            retryable=False,
        )
        transport = QueueTransport([openai_completion("first answer"), failure])
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "partial-output"
            args = self.parser.parse_args(
                [
                    "run",
                    "--provider",
                    "deepseek",
                    "--model",
                    "deepseek=deepseek-chat",
                    "--output-dir",
                    str(output_dir),
                    "--limit-questions",
                    "2",
                    "--min-interval",
                    "0",
                    "--execute",
                ]
            )
            with redirect_stdout(io.StringIO()):
                self.assertEqual(
                    run_command(
                        args,
                        environ=self.env,
                        provider_factory=self.provider_factory_for(transport),
                    ),
                    1,
                )
            manifest = json.loads(
                (output_dir / MANIFEST_FILENAME).read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["success_count"], 1)
            self.assertEqual(manifest["error_count"], 1)
            self.assertEqual(manifest["run_status"], "completed_with_errors")

    def test_unexpected_exception_marks_manifest_failed_before_propagating(self):
        transport = QueueTransport(
            [openai_completion("answer", extra={"non_finite": float("nan")})]
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "unexpected-output"
            args = self.parser.parse_args(
                [
                    "run",
                    "--provider",
                    "deepseek",
                    "--model",
                    "deepseek=deepseek-chat",
                    "--output-dir",
                    str(output_dir),
                    "--limit-questions",
                    "1",
                    "--min-interval",
                    "0",
                    "--execute",
                ]
            )
            with redirect_stdout(io.StringIO()):
                with self.assertRaises(ValueError):
                    run_command(
                        args,
                        environ=self.env,
                        provider_factory=self.provider_factory_for(transport),
                    )
            manifest = json.loads(
                (output_dir / MANIFEST_FILENAME).read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["run_status"], "failed")

    def test_volcengine_auth_failure_is_persisted_without_retry(self):
        failure = ProviderRequestError(
            "provider HTTP request failed with status 401",
            status_code=401,
            retryable=False,
            response_body={"error": {"message": "invalid credential"}},
        )
        transport = QueueTransport([failure])
        env = {"VOLCENGINE_API_KEY": API_SECRET}
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "volc-auth-output"
            args = self.parser.parse_args(
                [
                    "run",
                    "--provider",
                    "volcengine",
                    "--model",
                    "volcengine=endpoint-model-id",
                    "--output-dir",
                    str(output_dir),
                    "--limit-questions",
                    "1",
                    "--min-interval",
                    "0",
                    "--execute",
                ]
            )
            with redirect_stdout(io.StringIO()):
                self.assertEqual(
                    run_command(
                        args,
                        environ=env,
                        provider_factory=self.provider_factory_for(transport),
                    ),
                    1,
                )
            record = json.loads(
                (output_dir / RECORDS_FILENAME).read_text(encoding="utf-8")
            )
            self.assertEqual(record["provider"], "volcengine")
            self.assertEqual(record["status"], "error")
            self.assertEqual(record["attempt_count"], 1)
            self.assertEqual(record["error"]["status_code"], 401)
            self.assertFalse(record["error"]["retryable"])
            self.assertEqual(len(transport.calls), 1)
            manifest = json.loads(
                (output_dir / MANIFEST_FILENAME).read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["run_status"], "failed")


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_judge_evaluations.py 的离线测试；不联网、不产生 API 费用。"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from model_eval_providers import (
    HttpJsonResponse,
    HttpSseResponse,
    ProviderRequestError,
    build_provider,
)
from run_judge_evaluations import (
    MANIFEST_FILENAME,
    RECORDS_FILENAME,
    build_parser,
    effective_parameters,
    make_record_key,
    run_command,
)
from aggregate_model_evaluations import aggregate_evaluations
from score_model_evaluations import (
    DIMENSION_IDS,
    generate_judge_requests,
    import_judge_responses,
    sha256_text,
)


API_SECRET = "judge-unit-test-secret-never-persist"


class QueueTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request_json(self, method, url, **kwargs):
        self.calls.append({"transport": "json", "method": method, "url": url, **kwargs})
        if not self.responses:
            raise AssertionError("unexpected transport call")
        value = self.responses.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    def request_sse_json(self, method, url, **kwargs):
        self.calls.append({"transport": "sse", "method": method, "url": url, **kwargs})
        if not self.responses:
            raise AssertionError("unexpected transport call")
        value = self.responses.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


def completion(text="{\"request_id\":\"req-001\"}", reasoning="judge hidden reasoning"):
    return HttpJsonResponse(
        status_code=200,
        data={
            "id": "judge-completion",
            "api_key": API_SECRET,
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": text,
                        "reasoning_content": reasoning,
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
            "model": "judge-model-returned",
        },
        raw_text="unused",
        headers={},
    )


def stream_completion(text="{\"request_id\":\"req-001\"}"):
    return HttpSseResponse(
        status_code=200,
        chunks=(
            {
                "id": "judge-stream-completion",
                "model": "judge-model-returned",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": text},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 20,
                    "completion_tokens": 10,
                    "total_tokens": 30,
                },
            },
        ),
        done=True,
        headers={},
    )


def judge_request(
    scorer_type="ai",
    *,
    provider="deepseek",
    model=None,
    request_id="req-001",
    evaluation_id="eval-001",
):
    default_models = {
        "deepseek": "deepseek-chat",
        "volcengine": "doubao-test",
        "minimax": "MiniMax-M2.7",
        "gemini": "models/gemini-test",
    }
    return {
        "judge_request_schema_version": "0.1",
        "request_id": request_id,
        "evaluation_id": evaluation_id,
        "run_id": "tested-run",
        "tested_question_id": "WAI-001",
        "tested_response_sha256": "a" * 64,
        "judge": {
            "judge_id": "ai-judge-01",
            "scorer_type": scorer_type,
            "provider": provider,
            "model": model or default_models[provider],
            "independent": False,
            "blind_to_tested_model": True,
        },
        "notice": "AI 初评",
        "request": {
            "system_prompt": "只输出 JSON",
            "user_prompt": "评分请求",
            "parameters": {
                "temperature_recommendation": 0,
                "max_output_tokens_recommendation": 2500,
                "response_format": {"type": "json_schema"},
            },
        },
    }


def write_jsonl(path, records):
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def complete_score_payload(request_id):
    return {
        "request_id": request_id,
        "dimensions": {
            dimension_id: {
                "score": 3,
                "rationale": "端到端测试理由",
                "confidence": 0.8,
            }
            for dimension_id in DIMENSION_IDS
        },
        "major_risk": {
            "status": "not_flagged",
            "rubric_items": [],
            "labels": [],
            "evidence": [],
            "rationale": "端到端测试未发现风险信号",
        },
        "a_grade_eligible": False,
        "a_grade_rationale": "总分不在 A 区间",
        "overall_rationale": "端到端测试总体理由",
        "confidence": 0.8,
    }


class JudgeRunnerTests(unittest.TestCase):
    def setUp(self):
        self.parser = build_parser()
        self.environ = {"DEEPSEEK_API_KEY": API_SECRET}

    @staticmethod
    def provider_factory_for(transport):
        def factory(name, *, environ):
            return build_provider(name, environ=environ, transport=transport)

        return factory

    def test_request_recommendations_become_effective_parameters(self):
        parameters = effective_parameters(judge_request(), {})
        self.assertEqual(parameters, {"temperature": 0, "max_tokens": 2500})
        overridden = effective_parameters(
            judge_request(), {"temperature": 0.2, "max_tokens": 1000}
        )
        self.assertEqual(overridden, {"temperature": 0.2, "max_tokens": 1000})

    def test_provider_controls_change_only_matching_effective_parameters_and_hashes(self):
        overrides = {
            "deepseek_thinking": "disabled",
            "minimax_stream": True,
        }
        deepseek_request = judge_request()
        minimax_request = judge_request(provider="minimax")
        volcengine_request = judge_request(provider="volcengine")
        deepseek_parameters = effective_parameters(deepseek_request, overrides)
        minimax_parameters = effective_parameters(minimax_request, overrides)
        volcengine_parameters = effective_parameters(volcengine_request, overrides)
        self.assertEqual(deepseek_parameters["deepseek_thinking"], "disabled")
        self.assertNotIn("minimax_stream", deepseek_parameters)
        self.assertTrue(minimax_parameters["minimax_stream"])
        self.assertNotIn("deepseek_thinking", minimax_parameters)
        self.assertNotIn("deepseek_thinking", volcengine_parameters)
        self.assertNotIn("minimax_stream", volcengine_parameters)
        enabled_parameters = effective_parameters(
            deepseek_request,
            {"deepseek_thinking": "enabled", "minimax_stream": True},
        )
        self.assertNotEqual(
            make_record_key(
                deepseek_request,
                "deepseek",
                "deepseek-chat",
                deepseek_parameters,
            ),
            make_record_key(
                deepseek_request,
                "deepseek",
                "deepseek-chat",
                enabled_parameters,
            ),
        )

    def test_dry_run_never_builds_provider_or_writes_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "requests.jsonl"
            output_dir = root / "must-not-exist"
            write_jsonl(input_path, [judge_request()])
            args = self.parser.parse_args(
                ["--input", str(input_path), "--output-dir", str(output_dir), "--dry-run"]
            )

            def forbidden_provider_factory(*args, **kwargs):
                raise AssertionError("dry-run must not build a provider")

            captured = io.StringIO()
            with redirect_stdout(captured):
                code = run_command(args, environ={}, provider_factory=forbidden_provider_factory)
            self.assertEqual(code, 0)
            self.assertFalse(output_dir.exists())
            summary = json.loads(captured.getvalue())
            self.assertTrue(summary["dry_run"])
            self.assertEqual(summary["network_requests_made"], 0)
            self.assertEqual(summary["planned_generation_request_count"], 1)

    def test_invalid_retry_configuration_fails_before_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "requests.jsonl"
            output_dir = root / "must-not-exist"
            write_jsonl(input_path, [judge_request()])
            args = self.parser.parse_args(
                [
                    "--input",
                    str(input_path),
                    "--output-dir",
                    str(output_dir),
                    "--max-attempts",
                    "0",
                    "--execute",
                ]
            )
            with self.assertRaisesRegex(ValueError, "at least 1"):
                run_command(args, environ={}, provider_factory=lambda *args, **kwargs: None)
            self.assertFalse(output_dir.exists())

    def test_execute_persists_final_raw_usage_and_separate_reasoning(self):
        transport = QueueTransport([completion()])
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "requests.jsonl"
            output_dir = root / "judge-output"
            write_jsonl(input_path, [judge_request()])
            args = self.parser.parse_args(
                [
                    "--input",
                    str(input_path),
                    "--output-dir",
                    str(output_dir),
                    "--min-interval",
                    "0",
                    "--execute",
                ]
            )
            with redirect_stdout(io.StringIO()):
                code = run_command(
                    args,
                    environ=self.environ,
                    provider_factory=self.provider_factory_for(transport),
                )
            self.assertEqual(code, 0)
            records_text = (output_dir / RECORDS_FILENAME).read_text(encoding="utf-8")
            manifest_text = (output_dir / MANIFEST_FILENAME).read_text(encoding="utf-8")
            self.assertNotIn(API_SECRET, records_text)
            self.assertNotIn(API_SECRET, manifest_text)
            record = json.loads(records_text)
            self.assertEqual(record["request_id"], "req-001")
            self.assertEqual(record["evaluation_id"], "eval-001")
            self.assertEqual(record["status"], "success")
            self.assertEqual(record["final_response_text"], '{"request_id":"req-001"}')
            self.assertEqual(record["reasoning_text"], "judge hidden reasoning")
            self.assertTrue(record["reasoning_excluded_from_scoring"])
            self.assertEqual(record["raw_response"]["api_key"], "[REDACTED]")
            self.assertEqual(record["usage"]["total_tokens"], 30)
            self.assertEqual(record["returned_model"], "judge-model-returned")
            self.assertEqual(len(record["hashes"]["final_response_text_sha256"]), 64)
            payload = transport.calls[0]["payload"]
            self.assertEqual(payload["temperature"], 0)
            self.assertEqual(payload["max_tokens"], 2500)
            manifest = json.loads(manifest_text)
            self.assertEqual(manifest["success_count"], 1)
            self.assertFalse(manifest["formal_comparison_allowed"])

    def test_deepseek_disabled_thinking_is_audited_and_not_sent_to_volcengine(self):
        transport = QueueTransport(
            [
                completion('{"request_id":"req-deepseek"}'),
                completion('{"request_id":"req-volcengine"}'),
            ]
        )
        requests = [
            judge_request(
                provider="deepseek",
                request_id="req-deepseek",
                evaluation_id="eval-deepseek",
            ),
            judge_request(
                provider="volcengine",
                request_id="req-volcengine",
                evaluation_id="eval-volcengine",
            ),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "requests.jsonl"
            output_dir = root / "judge-output"
            write_jsonl(input_path, requests)
            args = self.parser.parse_args(
                [
                    "--input",
                    str(input_path),
                    "--output-dir",
                    str(output_dir),
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
                    environ={
                        "DEEPSEEK_API_KEY": API_SECRET,
                        "VOLCENGINE_API_KEY": API_SECRET,
                    },
                    provider_factory=self.provider_factory_for(transport),
                )
            self.assertEqual(code, 0)
            self.assertEqual(
                transport.calls[0]["payload"]["thinking"], {"type": "disabled"}
            )
            self.assertNotIn("thinking", transport.calls[1]["payload"])
            self.assertNotIn("deepseek_thinking", transport.calls[1]["payload"])
            records = [
                json.loads(line)
                for line in (output_dir / RECORDS_FILENAME)
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertEqual(
                records[0]["request"]["parameters"]["deepseek_thinking"],
                "disabled",
            )
            self.assertNotIn(
                "deepseek_thinking", records[1]["request"]["parameters"]
            )
            manifest = json.loads(
                (output_dir / MANIFEST_FILENAME).read_text(encoding="utf-8")
            )
            self.assertEqual(
                manifest["generation_parameter_overrides"]["deepseek_thinking"],
                "disabled",
            )

    def test_minimax_judge_defaults_to_stream_and_can_be_opted_out(self):
        transport = QueueTransport([stream_completion()])
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "requests.jsonl"
            output_dir = root / "judge-output"
            write_jsonl(input_path, [judge_request(provider="minimax")])
            base = ["--input", str(input_path), "--output-dir", str(output_dir)]
            args = self.parser.parse_args(base + ["--min-interval", "0", "--execute"])
            with redirect_stdout(io.StringIO()):
                code = run_command(
                    args,
                    environ={"MINIMAX_API_KEY": API_SECRET},
                    provider_factory=self.provider_factory_for(transport),
                )
            self.assertEqual(code, 0)
            self.assertEqual(transport.calls[0]["transport"], "sse")
            self.assertTrue(transport.calls[0]["payload"]["stream"])
            record = json.loads(
                (output_dir / RECORDS_FILENAME).read_text(encoding="utf-8")
            )
            self.assertTrue(record["request"]["parameters"]["minimax_stream"])
            manifest = json.loads(
                (output_dir / MANIFEST_FILENAME).read_text(encoding="utf-8")
            )
            self.assertTrue(
                manifest["generation_parameter_overrides"]["minimax_stream"]
            )

            dry_run_output = root / "dry-run-must-not-exist"
            opt_out_args = self.parser.parse_args(
                [
                    "--input",
                    str(input_path),
                    "--output-dir",
                    str(dry_run_output),
                    "--no-minimax-stream",
                    "--dry-run",
                ]
            )
            captured = io.StringIO()
            with redirect_stdout(captured):
                self.assertEqual(run_command(opt_out_args, environ={}), 0)
            self.assertFalse(
                json.loads(captured.getvalue())["generation_parameter_overrides"][
                    "minimax_stream"
                ]
            )
            self.assertFalse(dry_run_output.exists())

    def test_resume_rejects_changed_deepseek_thinking_control(self):
        first_transport = QueueTransport([completion()])
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "requests.jsonl"
            output_dir = root / "judge-output"
            write_jsonl(input_path, [judge_request()])
            base = [
                "--input",
                str(input_path),
                "--output-dir",
                str(output_dir),
                "--min-interval",
                "0",
                "--execute",
            ]
            with redirect_stdout(io.StringIO()):
                self.assertEqual(
                    run_command(
                        self.parser.parse_args(
                            base + ["--deepseek-thinking", "disabled"]
                        ),
                        environ=self.environ,
                        provider_factory=self.provider_factory_for(first_transport),
                    ),
                    0,
                )
            with self.assertRaisesRegex(
                ValueError, "generation parameter overrides"
            ):
                run_command(
                    self.parser.parse_args(
                        base
                        + [
                            "--deepseek-thinking",
                            "enabled",
                            "--resume",
                        ]
                    ),
                    environ=self.environ,
                    provider_factory=self.provider_factory_for(QueueTransport([])),
                )

    def test_resume_skips_success_without_transport_call(self):
        first_transport = QueueTransport([completion()])
        second_transport = QueueTransport([])
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "requests.jsonl"
            output_dir = root / "judge-output"
            write_jsonl(input_path, [judge_request()])
            base = [
                "--input",
                str(input_path),
                "--output-dir",
                str(output_dir),
                "--min-interval",
                "0",
                "--execute",
            ]
            with redirect_stdout(io.StringIO()):
                self.assertEqual(
                    run_command(
                        self.parser.parse_args(base),
                        environ=self.environ,
                        provider_factory=self.provider_factory_for(first_transport),
                    ),
                    0,
                )
                self.assertEqual(
                    run_command(
                        self.parser.parse_args(base + ["--resume"]),
                        environ=self.environ,
                        provider_factory=self.provider_factory_for(second_transport),
                    ),
                    0,
                )
            self.assertEqual(second_transport.calls, [])
            self.assertEqual(
                len((output_dir / RECORDS_FILENAME).read_text(encoding="utf-8").splitlines()),
                1,
            )

    def test_retry_history_and_errors_are_safely_redacted(self):
        failure = ProviderRequestError(
            "rate limited %s" % API_SECRET,
            status_code=429,
            retryable=True,
            retry_after=0,
            response_body={"authorization": API_SECRET},
        )
        transport = QueueTransport([failure, completion("retry success")])
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "requests.jsonl"
            output_dir = root / "judge-output"
            write_jsonl(input_path, [judge_request()])
            args = self.parser.parse_args(
                [
                    "--input",
                    str(input_path),
                    "--output-dir",
                    str(output_dir),
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
                        environ=self.environ,
                        provider_factory=self.provider_factory_for(transport),
                    ),
                    0,
                )
            record_text = (output_dir / RECORDS_FILENAME).read_text(encoding="utf-8")
            self.assertNotIn(API_SECRET, record_text)
            record = json.loads(record_text)
            self.assertEqual(record["attempt_count"], 2)
            self.assertEqual(len(record["retry_history"]), 1)

    def test_terminal_provider_error_is_persisted_and_returns_nonzero(self):
        failure = ProviderRequestError(
            "authentication failed",
            status_code=401,
            retryable=False,
            response_body={"api_key": API_SECRET},
        )
        transport = QueueTransport([failure])
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "requests.jsonl"
            output_dir = root / "judge-output"
            write_jsonl(input_path, [judge_request()])
            args = self.parser.parse_args(
                [
                    "--input",
                    str(input_path),
                    "--output-dir",
                    str(output_dir),
                    "--min-interval",
                    "0",
                    "--execute",
                ]
            )
            with redirect_stdout(io.StringIO()):
                code = run_command(
                    args,
                    environ=self.environ,
                    provider_factory=self.provider_factory_for(transport),
                )
            self.assertEqual(code, 1)
            record_text = (output_dir / RECORDS_FILENAME).read_text(encoding="utf-8")
            self.assertNotIn(API_SECRET, record_text)
            record = json.loads(record_text)
            self.assertEqual(record["status"], "error")
            self.assertEqual(record["error"]["status_code"], 401)
            manifest = json.loads(
                (output_dir / MANIFEST_FILENAME).read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["run_status"], "completed_with_errors")
            self.assertEqual(manifest["error_count"], 1)

    def test_reasoning_only_response_is_error_but_raw_audit_is_preserved(self):
        transport = QueueTransport([completion("", reasoning="只有推理，没有最终 JSON")])
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "requests.jsonl"
            output_dir = root / "judge-output"
            write_jsonl(input_path, [judge_request()])
            args = self.parser.parse_args(
                [
                    "--input",
                    str(input_path),
                    "--output-dir",
                    str(output_dir),
                    "--min-interval",
                    "0",
                    "--execute",
                ]
            )
            with redirect_stdout(io.StringIO()):
                code = run_command(
                    args,
                    environ=self.environ,
                    provider_factory=self.provider_factory_for(transport),
                )
            self.assertEqual(code, 1)
            record = json.loads(
                (output_dir / RECORDS_FILENAME).read_text(encoding="utf-8")
            )
            self.assertEqual(record["status"], "error")
            self.assertEqual(record["error"]["type"], "EmptyJudgeFinalResponseError")
            self.assertEqual(record["final_response_text"], "")
            self.assertEqual(record["reasoning_text"], "只有推理，没有最终 JSON")
            self.assertIsNotNone(record["raw_response"])

    def test_human_request_is_rejected_before_provider_creation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "requests.jsonl"
            output_dir = root / "judge-output"
            write_jsonl(input_path, [judge_request(scorer_type="human")])
            args = self.parser.parse_args(
                ["--input", str(input_path), "--output-dir", str(output_dir), "--execute"]
            )
            with self.assertRaisesRegex(ValueError, "must not impersonate"):
                run_command(args, environ={}, provider_factory=lambda *args, **kwargs: None)
            self.assertFalse(output_dir.exists())

    def test_generate_run_import_and_aggregate_are_schema_compatible(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            tested_responses = root / "tested-records.jsonl"
            tested_manifest = root / "tested-manifest.json"
            benchmark = root / "benchmark.jsonl"
            rubric = root / "rubric.md"
            judge_requests_path = root / "judge-requests.jsonl"
            judge_output = root / "judge-output"
            tested_text = "被测模型最终回答"
            write_jsonl(
                tested_responses,
                [
                    {
                        "run_id": "tested-run",
                        "record_key": "eval-001",
                        "provider": "deepseek",
                        "model": "tested-model",
                        "question_id": "WAI-001",
                        "question_version": "0.1",
                        "question_status": "draft",
                        "status": "success",
                        "request": {"system_prompt": "", "user_prompt": "问题", "parameters": {}},
                        "final_response_text": tested_text,
                        "reasoning_text": "不得进入评分",
                        "hashes": {"final_response_text_sha256": sha256_text(tested_text)},
                    }
                ],
            )
            tested_manifest.write_text(
                json.dumps({"run_id": "tested-run", "evaluation_phase": "pilot"}),
                encoding="utf-8",
            )
            write_jsonl(
                benchmark,
                [
                    {
                        "id": "WAI-001",
                        "version": "0.1",
                        "status": "draft",
                        "title": "测试",
                        "category": "测试",
                        "scenario": "假设劳动场景",
                        "prompt": "如何处理？",
                        "observation_points": ["识别风险"],
                        "severe_deductions": ["危险建议"],
                    }
                ],
            )
            rubric.write_text("五维评分，重大风险单列。", encoding="utf-8")
            requests, _ = generate_judge_requests(
                responses_path=tested_responses,
                benchmark_path=benchmark,
                rubric_path=rubric,
                judge_id="ai-judge-e2e",
                scorer_type="ai",
                judge_provider="deepseek",
                judge_model="deepseek-chat",
                manifest_path=tested_manifest,
            )
            write_jsonl(judge_requests_path, requests)
            payload = complete_score_payload(requests[0]["request_id"])
            transport = QueueTransport(
                [completion(json.dumps(payload, ensure_ascii=False), reasoning="隐藏 judge 推理")]
            )
            args = self.parser.parse_args(
                [
                    "--input",
                    str(judge_requests_path),
                    "--output-dir",
                    str(judge_output),
                    "--min-interval",
                    "0",
                    "--execute",
                ]
            )
            with redirect_stdout(io.StringIO()):
                self.assertEqual(
                    run_command(
                        args,
                        environ=self.environ,
                        provider_factory=self.provider_factory_for(transport),
                    ),
                    0,
                )
            scores, _ = import_judge_responses(
                responses_path=tested_responses,
                judge_response_paths=[judge_output / RECORDS_FILENAME],
                requests_path=judge_requests_path,
                manifest_path=tested_manifest,
            )
            self.assertEqual(scores[0]["score_status"], "valid")
            self.assertEqual(scores[0]["total_score"], 15)
            self.assertEqual(scores[0]["assessment_status"], "ai_preliminary")
            score_path = root / "scores.jsonl"
            write_jsonl(score_path, scores)
            summary = aggregate_evaluations(
                responses_path=tested_responses,
                score_paths=[score_path],
                manifest_path=tested_manifest,
            )
            self.assertEqual(summary["counts"]["valid_ai_preliminary_scores"], 1)
            self.assertFalse(summary["formal_readiness"]["ready_for_formal_comparison"])
            self.assertEqual(
                summary["answers"][0]["dimensions"][DIMENSION_IDS[0]]["median"], 3
            )


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""score_model_evaluations.py 的标准库单元测试。"""

import json
import os
import tempfile
import unittest
from pathlib import Path

from score_model_evaluations import (
    AI_PRELIMINARY_NOTICE,
    DIMENSION_IDS,
    PipelineError,
    build_parser,
    generate_judge_requests,
    import_judge_responses,
    sha256_text,
)


def write_jsonl(path, records):
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def benchmark_record():
    return {
        "id": "WAI-001",
        "version": "0.1",
        "status": "draft",
        "title": "测试题",
        "category": "测试",
        "scenario": "劳动者面对一个假设问题。",
        "prompt": "应如何处理？",
        "observation_points": ["识别风险"],
        "severe_deductions": ["鼓励危险行动"],
    }


def raw_record(status="success"):
    final_text = "这是可评分的最终回答。" if status == "success" else ""
    return {
        "record_schema_version": "0.1",
        "run_id": "run-test",
        "record_key": "record-001",
        "provider": "tested-provider",
        "model": "tested-model",
        "question_id": "WAI-001",
        "question_version": "0.1",
        "question_status": "draft",
        "status": status,
        "request": {"system_prompt": "", "user_prompt": "问题", "parameters": {}},
        "final_response_text": final_text,
        "response_text": "不应优先于 final_response_text",
        "reasoning_text": "私有推理不应进入 judge 提示。",
        "hashes": {"final_response_text_sha256": sha256_text(final_text)},
    }


def complete_payload(request_id, scores=None, risk_status="not_flagged"):
    scores = scores or {dimension_id: 3 for dimension_id in DIMENSION_IDS}
    return {
        "request_id": request_id,
        "dimensions": {
            dimension_id: {
                "score": scores[dimension_id],
                "rationale": "简明、可核查的理由",
                "confidence": 0.8,
            }
            for dimension_id in DIMENSION_IDS
        },
        "major_risk": {
            "status": risk_status,
            "rubric_items": [2] if risk_status == "flagged" else [],
            "labels": [],
            "evidence": ["回答片段"] if risk_status == "flagged" else [],
            "rationale": "发现风险信号" if risk_status == "flagged" else "未在本次初评中发现",
        },
        "a_grade_eligible": True,
        "a_grade_rationale": "未使用空洞口号",
        "overall_rationale": "总体初评理由",
        "confidence": 0.8,
    }


class ScoringPipelineTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.responses = self.root / "records.jsonl"
        self.manifest = self.root / "run-manifest.json"
        self.benchmark = self.root / "benchmark.jsonl"
        self.rubric = self.root / "rubric.md"
        write_jsonl(self.responses, [raw_record("success"), {**raw_record("error"), "record_key": "record-002"}])
        self.manifest.write_text(
            json.dumps({"run_id": "run-test"}, ensure_ascii=False), encoding="utf-8"
        )
        write_jsonl(self.benchmark, [benchmark_record()])
        self.rubric.write_text("五维各 0—4；重大风险单列。", encoding="utf-8")

    def tearDown(self):
        self.tempdir.cleanup()

    def generate(self, judge_id="ai-judge-1", judge_model="judge-model"):
        return generate_judge_requests(
            responses_path=self.responses,
            benchmark_path=self.benchmark,
            rubric_path=self.rubric,
            judge_id=judge_id,
            scorer_type="ai",
            judge_provider="judge-provider",
            judge_model=judge_model,
            manifest_path=self.manifest,
        )

    def test_generate_skips_failed_calls_and_excludes_reasoning(self):
        requests, audit = self.generate()
        self.assertEqual(len(requests), 1)
        self.assertEqual(audit["counts"]["skipped_failed_records"], 1)
        prompt = requests[0]["request"]["user_prompt"]
        self.assertIn("这是可评分的最终回答。", prompt)
        self.assertNotIn("私有推理不应进入 judge 提示。", prompt)
        self.assertNotIn("不应优先于 final_response_text", prompt)
        self.assertIn(AI_PRELIMINARY_NOTICE, requests[0]["notice"])
        self.assertNotIn("tested_model", requests[0])

    def test_generate_explicitly_constrains_not_flagged_risk_shape(self):
        requests, _ = self.generate()
        generated = requests[0]
        system_prompt = generated["request"]["system_prompt"]
        user_prompt = generated["request"]["user_prompt"]
        self.assertIn("status=not_flagged", system_prompt)
        self.assertIn("rubric_items、labels、evidence 必须全部是空数组", system_prompt)
        self.assertIn("不要用“无相关片段”", system_prompt)
        self.assertIn("status=not_flagged 时 rubric_items、labels、evidence 必须全部精确写为 []", user_prompt)
        self.assertIn('["无相关片段"]', user_prompt)
        self.assertIn("【not_flagged 的正确 major_risk 形状】", user_prompt)

        schema = generated["request"]["parameters"]["response_format"]["schema"]
        risk_schema = schema["properties"]["major_risk"]
        self.assertEqual(
            risk_schema["if"]["properties"]["status"]["enum"], ["not_flagged"]
        )
        for field in ("rubric_items", "labels", "evidence"):
            self.assertEqual(risk_schema["then"]["properties"][field]["maxItems"], 0)

    def test_generic_content_field_is_never_used_as_final_answer_fallback(self):
        record = raw_record("success")
        record.pop("final_response_text")
        record.pop("response_text")
        record["content"] = "<think>私有推理</think>"
        record["hashes"] = {"raw_response_sha256": sha256_text(record["content"])}
        write_jsonl(self.responses, [record])
        requests, audit = self.generate()
        self.assertEqual(requests, [])
        self.assertEqual(audit["counts"]["skipped_empty_responses"], 1)

    def test_response_hash_mismatch_stops_scoring(self):
        bad = raw_record("success")
        bad["hashes"]["final_response_text_sha256"] = "0" * 64
        write_jsonl(self.responses, [bad])
        with self.assertRaisesRegex(PipelineError, "SHA-256"):
            self.generate()

    def test_raw_response_hash_is_not_mistaken_for_final_text_hash(self):
        record = raw_record("success")
        record["hashes"] = {"raw_response_sha256": "0" * 64}
        write_jsonl(self.responses, [record])
        requests, _ = self.generate()
        self.assertEqual(
            requests[0]["tested_response_sha256"],
            sha256_text("这是可评分的最终回答。"),
        )

    def test_resume_history_uses_latest_record_without_losing_audit_counts(self):
        failed = raw_record("error")
        succeeded = raw_record("success")
        write_jsonl(self.responses, [failed, succeeded])
        requests, audit = self.generate()
        self.assertEqual(len(requests), 1)
        self.assertIn("这是可评分的最终回答。", requests[0]["request"]["user_prompt"])
        self.assertEqual(audit["counts"]["raw_record_lines"], 2)
        self.assertEqual(audit["counts"]["unique_latest_records"], 1)
        self.assertEqual(audit["counts"]["superseded_raw_records"], 1)

    def test_exact_same_model_is_not_allowed_to_score_itself(self):
        requests, audit = generate_judge_requests(
            responses_path=self.responses,
            benchmark_path=self.benchmark,
            rubric_path=self.rubric,
            judge_id="self-judge",
            scorer_type="ai",
            judge_provider="tested-provider",
            judge_model="tested-model",
            manifest_path=self.manifest,
        )
        self.assertEqual(requests, [])
        self.assertEqual(audit["counts"]["skipped_exact_self_judging"], 1)

    def test_only_tested_provider_filter_is_repeatable_and_audited(self):
        parsed = build_parser().parse_args(
            [
                "generate",
                "--responses",
                str(self.responses),
                "--judge-id",
                "j",
                "--judge-provider",
                "deepseek",
                "--judge-model",
                "m",
                "--only-tested-provider",
                "gemini",
                "--only-tested-provider",
                "minimax",
                "--output",
                str(self.root / "out.jsonl"),
            ]
        )
        self.assertEqual(parsed.only_tested_provider, ["gemini", "minimax"])
        requests, audit = generate_judge_requests(
            responses_path=self.responses,
            benchmark_path=self.benchmark,
            rubric_path=self.rubric,
            judge_id="cross-provider-judge",
            scorer_type="ai",
            judge_provider="deepseek",
            judge_model="deepseek-chat",
            manifest_path=self.manifest,
            only_tested_providers=["gemini", "minimax"],
        )
        self.assertEqual(requests, [])
        self.assertEqual(audit["tested_provider_filter"], ["gemini", "minimax"])
        self.assertEqual(audit["counts"]["skipped_tested_provider_filter"], 2)

        requests, audit = generate_judge_requests(
            responses_path=self.responses,
            benchmark_path=self.benchmark,
            rubric_path=self.rubric,
            judge_id="cross-provider-judge",
            scorer_type="ai",
            judge_provider="deepseek",
            judge_model="deepseek-chat",
            manifest_path=self.manifest,
            only_tested_providers=["TESTED-PROVIDER"],
        )
        self.assertEqual(len(requests), 1)
        self.assertEqual(audit["counts"]["skipped_tested_provider_filter"], 0)

    def test_only_tested_model_filter_is_repeatable_and_audited(self):
        parsed = build_parser().parse_args(
            [
                "generate",
                "--responses",
                str(self.responses),
                "--judge-id",
                "j",
                "--judge-provider",
                "deepseek",
                "--judge-model",
                "m",
                "--only-tested-model",
                "gemini=models/gemini-3.1-flash-lite",
                "--only-tested-model",
                "minimax=MiniMax-M3",
                "--output",
                str(self.root / "out.jsonl"),
            ]
        )
        self.assertEqual(
            parsed.only_tested_model,
            ["gemini=models/gemini-3.1-flash-lite", "minimax=MiniMax-M3"],
        )

        requests, audit = generate_judge_requests(
            responses_path=self.responses,
            benchmark_path=self.benchmark,
            rubric_path=self.rubric,
            judge_id="cross-provider-judge",
            scorer_type="ai",
            judge_provider="deepseek",
            judge_model="deepseek-chat",
            manifest_path=self.manifest,
            only_tested_models=["TESTED-PROVIDER=tested-model"],
        )
        self.assertEqual(len(requests), 1)
        self.assertEqual(
            audit["tested_model_filter"], ["tested-provider=tested-model"]
        )
        self.assertEqual(audit["counts"]["skipped_tested_model_filter"], 0)

        requests, audit = generate_judge_requests(
            responses_path=self.responses,
            benchmark_path=self.benchmark,
            rubric_path=self.rubric,
            judge_id="cross-provider-judge",
            scorer_type="ai",
            judge_provider="deepseek",
            judge_model="deepseek-chat",
            manifest_path=self.manifest,
            only_tested_models=["gemini=models/gemini-3.1-flash-lite"],
        )
        self.assertEqual(requests, [])
        self.assertEqual(audit["counts"]["skipped_tested_model_filter"], 2)

        with self.assertRaisesRegex(PipelineError, "PROVIDER=MODEL"):
            generate_judge_requests(
                responses_path=self.responses,
                benchmark_path=self.benchmark,
                rubric_path=self.rubric,
                judge_id="cross-provider-judge",
                scorer_type="ai",
                judge_provider="deepseek",
                judge_model="deepseek-chat",
                manifest_path=self.manifest,
                only_tested_models=["missing-provider-separator"],
            )

    def test_import_recomputes_total_and_marks_ai_preliminary(self):
        requests, _ = self.generate()
        request_file = self.root / "judge-requests.jsonl"
        response_file = self.root / "judge-responses.jsonl"
        write_jsonl(request_file, requests)
        payload = complete_payload(
            requests[0]["request_id"], {dimension_id: 4 for dimension_id in DIMENSION_IDS}
        )
        write_jsonl(
            response_file,
            [
                {
                    "status": "success",
                    "provider": "judge-provider",
                    "model": "judge-model",
                    "final_response_text": "```json\n%s\n```" % json.dumps(payload, ensure_ascii=False),
                }
            ],
        )
        scores, audit = import_judge_responses(
            responses_path=self.responses,
            judge_response_paths=[response_file],
            requests_path=request_file,
            manifest_path=self.manifest,
        )
        self.assertEqual(len(scores), 1)
        score = scores[0]
        self.assertEqual(score["score_status"], "valid")
        self.assertEqual(score["assessment_status"], "ai_preliminary")
        self.assertEqual(score["total_score"], 20)
        self.assertEqual(score["grade"], "A")
        self.assertTrue(score["major_risk"]["requires_human_review"])
        self.assertEqual(audit["counts"]["ai_preliminary_scores"], 1)

    def test_na_and_blank_are_not_zero_and_disable_total(self):
        requests, _ = self.generate()
        request_file = self.root / "judge-requests.jsonl"
        response_file = self.root / "judge-responses.jsonl"
        write_jsonl(request_file, requests)
        values = {dimension_id: 3 for dimension_id in DIMENSION_IDS}
        values[DIMENSION_IDS[0]] = "N/A"
        values[DIMENSION_IDS[1]] = None
        payload = complete_payload(requests[0]["request_id"], values)
        payload["dimensions"][DIMENSION_IDS[1]]["rationale"] = ""
        write_jsonl(response_file, [payload])
        scores, _ = import_judge_responses(
            responses_path=self.responses,
            judge_response_paths=[response_file],
            requests_path=request_file,
            manifest_path=self.manifest,
        )
        score = scores[0]
        self.assertEqual(score["not_applicable_dimension_count"], 1)
        self.assertEqual(score["blank_dimension_count"], 1)
        self.assertIsNone(score["total_score"])
        self.assertEqual(score["total_status"], "not_calculated_due_to_na")

    def test_contradictory_not_flagged_risk_metadata_is_preserved_as_uncertain(self):
        requests, _ = self.generate()
        request_file = self.root / "judge-requests.jsonl"
        response_file = self.root / "judge-responses.jsonl"
        write_jsonl(request_file, requests)
        payload = complete_payload(requests[0]["request_id"], risk_status="not_flagged")
        payload["major_risk"]["rubric_items"] = [2]
        payload["major_risk"]["evidence"] = ["回答中的风险片段"]
        write_jsonl(response_file, [payload])
        scores, audit = import_judge_responses(
            responses_path=self.responses,
            judge_response_paths=[response_file],
            requests_path=request_file,
            manifest_path=self.manifest,
        )
        self.assertEqual(scores[0]["score_status"], "invalid")
        self.assertEqual(scores[0]["major_risk"]["status"], "uncertain")
        self.assertTrue(scores[0]["major_risk"]["requires_human_review"])
        self.assertEqual(audit["counts"]["invalid_scores"], 1)

    def test_minimax_not_flagged_placeholder_evidence_shape_remains_invalid(self):
        requests, _ = self.generate()
        request_file = self.root / "judge-requests.jsonl"
        response_file = self.root / "judge-responses.jsonl"
        write_jsonl(request_file, requests)
        # 真实 MiniMax judge 烟测曾返回这一形状：JSON 可解析，但占位 evidence
        # 与 not_flagged 相矛盾。导入器必须继续严格拒绝，不能为适配模型而放宽。
        payload = complete_payload(requests[0]["request_id"], risk_status="not_flagged")
        payload["major_risk"]["evidence"] = ["无相关片段"]
        write_jsonl(response_file, [payload])
        scores, audit = import_judge_responses(
            responses_path=self.responses,
            judge_response_paths=[response_file],
            requests_path=request_file,
            manifest_path=self.manifest,
        )
        score = scores[0]
        self.assertEqual(score["score_status"], "invalid")
        self.assertEqual(score["major_risk"]["status"], "uncertain")
        self.assertEqual(score["major_risk"]["evidence"], ["无相关片段"])
        self.assertTrue(
            any("status=not_flagged" in error for error in score["validation_errors"])
        )
        self.assertEqual(audit["counts"]["invalid_scores"], 1)

    def test_multiple_judges_are_preserved_as_separate_rows(self):
        request_one, _ = self.generate("ai-judge-1", "judge-model-1")
        request_two, _ = self.generate("ai-judge-2", "judge-model-2")
        request_file = self.root / "judge-requests.jsonl"
        response_file = self.root / "judge-responses.jsonl"
        requests = request_one + request_two
        write_jsonl(request_file, requests)
        write_jsonl(
            response_file,
            [
                {
                    **complete_payload(requests[0]["request_id"], risk_status="flagged"),
                    "record_key": "record-001",
                },
                {
                    **complete_payload(requests[1]["request_id"], risk_status="not_flagged"),
                    "record_key": "record-001",
                },
            ],
        )
        scores, _ = import_judge_responses(
            responses_path=self.responses,
            judge_response_paths=[response_file],
            requests_path=request_file,
            manifest_path=self.manifest,
        )
        self.assertEqual(len(scores), 2)
        self.assertEqual({score["judge"]["judge_id"] for score in scores}, {"ai-judge-1", "ai-judge-2"})
        self.assertEqual({score["major_risk"]["status"] for score in scores}, {"flagged", "not_flagged"})

    def test_unknown_request_id_cannot_bypass_frozen_request_binding(self):
        requests, _ = self.generate()
        request_file = self.root / "judge-requests.jsonl"
        response_file = self.root / "judge-responses.jsonl"
        write_jsonl(request_file, requests)
        forged = complete_payload("unknown-request")
        forged["evaluation_id"] = requests[0]["evaluation_id"]
        write_jsonl(response_file, [forged])
        with self.assertRaisesRegex(PipelineError, "不在冻结 judge 请求"):
            import_judge_responses(
                responses_path=self.responses,
                judge_response_paths=[response_file],
                requests_path=request_file,
                manifest_path=self.manifest,
            )

    def test_stale_request_cannot_score_a_replaced_final_response(self):
        requests, _ = self.generate()
        request_file = self.root / "judge-requests.jsonl"
        response_file = self.root / "judge-responses.jsonl"
        write_jsonl(request_file, requests)
        changed = raw_record("success")
        changed["final_response_text"] = "后续重试产生的另一份最终回答。"
        changed["hashes"]["final_response_text_sha256"] = sha256_text(
            changed["final_response_text"]
        )
        write_jsonl(self.responses, [changed])
        write_jsonl(response_file, [complete_payload(requests[0]["request_id"])])
        scores, audit = import_judge_responses(
            responses_path=self.responses,
            judge_response_paths=[response_file],
            requests_path=request_file,
            manifest_path=self.manifest,
        )
        self.assertEqual(scores[0]["score_status"], "invalid")
        self.assertTrue(
            any("tested_response_sha256" in error for error in scores[0]["validation_errors"])
        )
        self.assertEqual(audit["counts"]["invalid_scores"], 1)

    def test_judge_error_is_preserved_as_audit_record(self):
        requests, _ = self.generate()
        request_file = self.root / "judge-requests.jsonl"
        response_file = self.root / "judge-responses.jsonl"
        write_jsonl(request_file, requests)
        write_jsonl(
            response_file,
            [{"request_id": requests[0]["request_id"], "status": "error", "error": {"message": "timeout"}}],
        )
        scores, audit = import_judge_responses(
            responses_path=self.responses,
            judge_response_paths=[response_file],
            requests_path=request_file,
            manifest_path=self.manifest,
        )
        self.assertEqual(scores[0]["score_status"], "judge_error")
        self.assertEqual(scores[0]["blank_dimension_count"], 5)
        self.assertEqual(audit["counts"]["judge_errors"], 1)

    def test_judge_resume_history_uses_latest_success_and_preserves_history(self):
        requests, _ = self.generate()
        request_file = self.root / "judge-requests.jsonl"
        response_file = self.root / "judge-responses.jsonl"
        write_jsonl(request_file, requests)
        payload = complete_payload(requests[0]["request_id"])
        stable_fields = {
            "judge_response_schema_version": "1.0",
            "record_key": "judge-record-001",
            "request_id": requests[0]["request_id"],
            "evaluation_id": requests[0]["evaluation_id"],
            "provider": "judge-provider",
            "model": "judge-model",
        }
        write_jsonl(
            response_file,
            [
                {**stable_fields, "status": "error", "error": {"message": "timeout"}},
                {
                    **stable_fields,
                    "status": "success",
                    "final_response_text": json.dumps(payload, ensure_ascii=False),
                },
            ],
        )
        scores, audit = import_judge_responses(
            responses_path=self.responses,
            judge_response_paths=[response_file],
            requests_path=request_file,
            manifest_path=self.manifest,
        )
        self.assertEqual(len(scores), 1)
        self.assertEqual(scores[0]["score_status"], "valid")
        self.assertEqual(audit["counts"]["judge_response_record_lines"], 2)
        self.assertEqual(audit["counts"]["unique_latest_judge_responses"], 1)
        self.assertEqual(audit["counts"]["superseded_judge_response_records"], 1)
        self.assertEqual(
            [entry["status"] for entry in scores[0]["source"]["judge_response_history"]],
            ["error", "success"],
        )

    def test_ai_payload_cannot_impersonate_an_independent_human_judge(self):
        requests, _ = self.generate()
        request_file = self.root / "judge-requests.jsonl"
        response_file = self.root / "judge-responses.jsonl"
        write_jsonl(request_file, requests)
        payload = complete_payload(requests[0]["request_id"])
        payload["judge"] = {
            "judge_id": "forged-human",
            "scorer_type": "human",
            "independent": True,
        }
        write_jsonl(response_file, [payload])
        scores, audit = import_judge_responses(
            responses_path=self.responses,
            judge_response_paths=[response_file],
            requests_path=request_file,
            manifest_path=self.manifest,
        )
        self.assertEqual(scores[0]["judge"]["judge_id"], "ai-judge-1")
        self.assertEqual(scores[0]["judge"]["scorer_type"], "ai")
        self.assertFalse(scores[0]["judge"]["independent"])
        self.assertEqual(scores[0]["assessment_status"], "ai_preliminary")
        self.assertEqual(audit["counts"]["independent_human_scores"], 0)
        self.assertTrue(
            any("scorer_type" in warning for warning in scores[0]["metadata_warnings"])
        )

    def test_judge_response_request_hash_mismatch_is_invalid(self):
        requests, _ = self.generate()
        request_file = self.root / "judge-requests.jsonl"
        response_file = self.root / "judge-responses.jsonl"
        write_jsonl(request_file, requests)
        payload = complete_payload(requests[0]["request_id"])
        write_jsonl(
            response_file,
            [
                {
                    "request_id": requests[0]["request_id"],
                    "evaluation_id": requests[0]["evaluation_id"],
                    "status": "success",
                    "final_response_text": json.dumps(payload, ensure_ascii=False),
                    "hashes": {"judge_request_sha256": "0" * 64},
                }
            ],
        )
        scores, audit = import_judge_responses(
            responses_path=self.responses,
            judge_response_paths=[response_file],
            requests_path=request_file,
            manifest_path=self.manifest,
        )
        self.assertEqual(scores[0]["score_status"], "invalid")
        self.assertTrue(
            any("judge_request_sha256" in item for item in scores[0]["validation_errors"])
        )
        self.assertEqual(audit["counts"]["invalid_scores"], 1)

    def test_judge_content_field_is_not_used_as_final_json_fallback(self):
        requests, _ = self.generate()
        request_file = self.root / "judge-requests.jsonl"
        response_file = self.root / "judge-responses.jsonl"
        write_jsonl(request_file, requests)
        payload = complete_payload(requests[0]["request_id"])
        write_jsonl(
            response_file,
            [
                {
                    "request_id": requests[0]["request_id"],
                    "evaluation_id": requests[0]["evaluation_id"],
                    "status": "success",
                    "content": json.dumps(payload, ensure_ascii=False),
                    "reasoning_text": json.dumps(payload, ensure_ascii=False),
                }
            ],
        )
        scores, audit = import_judge_responses(
            responses_path=self.responses,
            judge_response_paths=[response_file],
            requests_path=request_file,
            manifest_path=self.manifest,
        )
        self.assertEqual(scores[0]["score_status"], "invalid")
        self.assertEqual(audit["counts"]["valid_scores"], 0)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from build_model_evaluation_report import (
    ReportError,
    RunSpec,
    build_report,
    main,
    sha256_file,
    sha256_text,
)
from score_model_evaluations import DIMENSIONS


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def write_jsonl(path: Path, values):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(value, ensure_ascii=False) + "\n" for value in values),
        encoding="utf-8",
    )


def response_record(run_id, provider, model, question_id, *, status="success", error=None, returned=None):
    response_text = "回答-%s-%s" % (model, question_id) if status == "success" else ""
    return {
        "record_schema_version": "1.1",
        "run_id": run_id,
        "record_key": "eval-%s-%s-%s" % (provider, model, question_id),
        "provider": provider,
        "model": model,
        "returned_model": returned if returned is not None else model,
        "question_id": question_id,
        "question_version": "0.1",
        "question_status": "draft",
        "status": status,
        "final_response_text": response_text,
        "request": {"system_prompt": ""},
        "error": error,
        "attempt_count": 1,
    }


def manifest(run_id, provider_models, selected_ids, *, status="completed"):
    return {
        "manifest_schema_version": "1.1",
        "run_id": run_id,
        "run_status": status,
        "created_at": "2026-08-25T00:00:00Z",
        "completed_at": "2026-08-25T01:00:00Z" if status != "running" else None,
        "evaluation_phase": "pilot",
        "publication_status": "review_required",
        "formal_comparison_allowed": False,
        "provider_models": provider_models,
        "input": {
            "selected_question_ids": selected_ids,
            "question_count": len(selected_ids),
            "sha256": "benchmark-sha",
        },
        "prompt": {"system_prompt": "", "template": "Q={prompt}"},
        "generation_parameters": {"max_tokens": 8192, "api_key": "must-not-appear"},
        "planned_count": len(selected_ids) * sum(len(v) for v in provider_models.values()),
        "unique_record_count": 0,
        "success_count": 0,
    }


def aggregate_system(provider, model, config, *, ai_median=15, ai_answers=24):
    dimension = {
        "by_scorer_type": {
            "ai": {
                "answer_equal_weight_medians": {
                    "answer_count": ai_answers,
                    "median": 3,
                    "values": [3] * ai_answers,
                },
                "raw_judge_assessments": {"assessment_count": ai_answers},
            },
            "human": {
                "answer_equal_weight_medians": {"answer_count": 0, "median": None, "values": []},
                "raw_judge_assessments": {"assessment_count": 0},
            },
            "unknown": {
                "answer_equal_weight_medians": {"answer_count": 0, "median": None, "values": []},
                "raw_judge_assessments": {"assessment_count": 0},
            },
        }
    }
    return {
        "tested_system": {
            "provider": provider,
            "model": model,
            "configuration_id": config,
        },
        "answer_counts": {
            "all": ai_answers,
            "successful": ai_answers,
            "failed_or_missing": 0,
            "with_any_valid_score": ai_answers,
            "with_two_independent_human_scores": 0,
        },
        "dimensions": {dimension_id: dimension for dimension_id, _ in DIMENSIONS},
        "total": {
            "by_scorer_type": {
                "ai": {
                    "answer_equal_weight_medians": {
                        "answer_count": ai_answers,
                        "median": ai_median,
                        "values": [ai_median] * ai_answers,
                    },
                    "raw_judge_assessments": {"assessment_count": ai_answers},
                },
                "human": {
                    "answer_equal_weight_medians": {"answer_count": 0, "median": None, "values": []},
                    "raw_judge_assessments": {"assessment_count": 0},
                },
                "unknown": {
                    "answer_equal_weight_medians": {"answer_count": 0, "median": None, "values": []},
                    "raw_judge_assessments": {"assessment_count": 0},
                },
            }
        },
        "major_risk": {
            "flagged_evaluation_ids": ["eval-deepseek-model-a-WAI-001"],
            "uncertain_evaluation_ids": [],
        },
        "disagreements": {"answer_count": 1},
        "formal_readiness": {"ready_for_formal_comparison": False},
    }


class BuildModelEvaluationReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def make_main_fixture(self):
        run_dir = self.root / "run-main"
        responses = run_dir / "records.jsonl"
        manifest_path = run_dir / "run-manifest.json"
        question_ids = ["WAI-%03d" % index for index in range(1, 25)]
        records = [
            response_record(
                "run-main",
                "deepseek",
                "model-a",
                question_id,
                returned="model-a-202608" if question_id == "WAI-001" else "model-a",
            )
            for question_id in question_ids
        ]
        records.extend(
            response_record("run-main", "gemini", "model-b", question_id)
            for question_id in question_ids[:20]
        )
        records.extend(
            [
                response_record(
                    "run-main",
                    "gemini",
                    "model-b",
                    "WAI-021",
                    status="error",
                    error={"type": "HTTPError", "status_code": 429, "message": "quota"},
                ),
                response_record(
                    "run-main",
                    "gemini",
                    "model-b",
                    "WAI-022",
                    status="error",
                    error={"type": "AuthenticationError", "status_code": 401},
                ),
                response_record(
                    "run-main",
                    "gemini",
                    "model-b",
                    "WAI-023",
                    status="error",
                    error={"type": "RemoteDisconnected", "retryable": True},
                ),
            ]
        )
        write_jsonl(responses, records)
        manifest_value = manifest(
            "run-main",
            {"deepseek": ["model-a"], "gemini": ["model-b"]},
            question_ids,
        )
        manifest_value["unique_record_count"] = len(records)
        manifest_value["success_count"] = 44
        write_json(manifest_path, manifest_value)
        return RunSpec(responses, manifest_path), records

    def test_report_separates_coverage_errors_scores_and_review_registers(self):
        spec, _ = self.make_main_fixture()
        config = "system-prompt-sha256:" + sha256_text("")
        aggregate_path = self.root / "score-summary.json"
        aggregation = {
            "aggregate_schema_version": "0.1",
            "source": {
                "run_id": "run-main",
                "responses_sha256": sha256_file(spec.responses_path),
                "manifest_sha256": sha256_file(spec.manifest_path),
            },
            "counts": {
                "valid_ai_preliminary_scores": 24,
                "valid_human_scores": 0,
                "invalid_or_judge_error_scores": 1,
            },
            "formal_readiness": {"ready_for_formal_comparison": False},
            "tested_systems": [aggregate_system("deepseek", "model-a", config)],
            "major_risk_register": {
                "entry_count": 1,
                "entries": [
                    {
                        "evaluation_id": "eval-deepseek-model-a-WAI-001",
                        "question_id": "WAI-001",
                        "tested_system": {
                            "provider": "deepseek",
                            "model": "model-a",
                            "configuration_id": config,
                        },
                        "risk_status": "uncertain",
                        "rubric_items": [2],
                        "labels": ["待核事实"],
                        "judge": {"scorer_type": "ai", "judge_id": "judge-a"},
                        "score_status": "invalid",
                    }
                ],
            },
            "disagreement_register": {
                "affected_evaluation_count": 1,
                "entries": [
                    {
                        "evaluation_id": "eval-deepseek-model-a-WAI-002",
                        "question_id": "WAI-002",
                        "tested_system": {
                            "provider": "deepseek",
                            "model": "model-a",
                            "configuration_id": config,
                        },
                        "triggers": [
                            {"type": "dimension_gap_at_least_2", "dimension": "factual_reliability"}
                        ],
                    }
                ],
            },
            "answers": [
                {
                    "evaluation_id": "eval-deepseek-model-a-WAI-001",
                    "question_id": "WAI-001",
                    "tested_system": {
                        "provider": "deepseek",
                        "model": "model-a",
                        "configuration_id": config,
                    },
                    "assessment_counts": {"invalid": 1, "judge_error": 0},
                    "assessments": [
                        {
                            "score_status": "invalid",
                            "validation_errors": ["风险字段互相矛盾"],
                        }
                    ],
                }
            ],
        }
        write_json(aggregate_path, aggregation)

        report = build_report([spec], aggregation_paths=[aggregate_path])

        self.assertIn("完整24题", report)
        self.assertIn("部分（20/24）", report)
        self.assertIn("配额/限流", report)
        self.assertIn("鉴权", report)
        self.assertIn("传输", report)
        self.assertIn("计划记录缺失", report)
        self.assertIn("model-a-202608", report)
        self.assertIn("与请求名不同", report)
        self.assertIn("15/20（24答）", report)
        self.assertIn("仅 AI 初评", report)
        self.assertIn("uncertain", report)
        self.assertIn("风险字段互相矛盾", report)
        self.assertIn("dimension_gap_at_least_2@factual_reliability", report)
        self.assertIn("<已脱敏>", report)
        self.assertNotIn("must-not-appear", report)
        self.assertIn(sha256_file(spec.responses_path), report)
        self.assertIn("不计算平均分，也不按分数排序", report)

    def test_diagnostic_run_is_excluded_by_default(self):
        main_spec, _ = self.make_main_fixture()
        smoke_dir = self.root / "smoke-provider"
        smoke_responses = smoke_dir / "records.jsonl"
        smoke_manifest = smoke_dir / "run-manifest.json"
        write_jsonl(
            smoke_responses,
            [response_record("smoke-provider", "volcengine", "smoke-only-model", "WAI-001")],
        )
        smoke_manifest_value = manifest(
            "smoke-provider", {"volcengine": ["smoke-only-model"]}, ["WAI-001"], status="failed"
        )
        smoke_manifest_value["unique_record_count"] = 1
        smoke_manifest_value["success_count"] = 1
        write_json(smoke_manifest, smoke_manifest_value)

        report = build_report(
            [main_spec, RunSpec(smoke_responses, smoke_manifest)],
        )

        self.assertIn("默认排除了 1 个", report)
        self.assertIn("## 默认排除的诊断运行", report)
        coverage_section = report.split("## 覆盖率决定", 1)[1].split("## 调用失败", 1)[0]
        self.assertNotIn("smoke-only-model", coverage_section)
        self.assertIn("smoke-only-model", report.split("## 默认排除的诊断运行", 1)[1])
        self.assertIn("仅诊断，不计分/不横比", report)

    def test_all_diagnostics_require_explicit_opt_in(self):
        run_dir = self.root / "probe-only"
        responses = run_dir / "records.jsonl"
        manifest_path = run_dir / "run-manifest.json"
        write_jsonl(responses, [response_record("probe-only", "x", "m", "WAI-001")])
        value = manifest("probe-only", {"x": ["m"]}, ["WAI-001"])
        value["unique_record_count"] = 1
        value["success_count"] = 1
        write_json(manifest_path, value)
        spec = RunSpec(responses, manifest_path)

        with self.assertRaises(ReportError):
            build_report([spec])
        report = build_report([spec], include_diagnostics=True, full_question_count=1)
        self.assertIn("仅单独查看", report)

    def test_alias_and_version_collapse_is_not_treated_as_independent_models(self):
        run_dir = self.root / "run-gemini-aliases"
        responses = run_dir / "records.jsonl"
        manifest_path = run_dir / "run-manifest.json"
        records = [
            response_record(
                "run-gemini-aliases",
                "gemini",
                "models/gemini-3.1-flash-lite",
                "WAI-001",
                returned="gemini-3.1-flash-lite",
            ),
            response_record(
                "run-gemini-aliases",
                "gemini",
                "models/gemini-3.1-flash-lite-preview",
                "WAI-001",
                returned="gemini-3.1-flash-lite",
            ),
        ]
        write_jsonl(responses, records)
        value = manifest(
            "run-gemini-aliases",
            {
                "gemini": [
                    "models/gemini-3.1-flash-lite",
                    "models/gemini-3.1-flash-lite-preview",
                ]
            },
            ["WAI-001"],
        )
        value["unique_record_count"] = 2
        value["success_count"] = 2
        write_json(manifest_path, value)

        report = build_report(
            [RunSpec(responses, manifest_path)],
            full_question_count=1,
        )

        self.assertIn("请求名与返回版本存在 alias/version 映射", report)
        self.assertIn("models/gemini-3.1-flash-lite-preview", report)
        self.assertIn("gemini-3.1-flash-lite", report)
        self.assertIn("不得当作多个独立底层模型排名", report)

    def test_score_jsonl_is_aggregated_offline_and_ai_is_not_human(self):
        run_dir = self.root / "run-scored"
        responses = run_dir / "records.jsonl"
        manifest_path = run_dir / "run-manifest.json"
        score_path = run_dir / "scores.jsonl"
        raw = response_record("run-scored", "deepseek", "model-c", "WAI-001")
        raw["question_status"] = "stable"
        write_jsonl(responses, [raw])
        value = manifest("run-scored", {"deepseek": ["model-c"]}, ["WAI-001"])
        value["unique_record_count"] = 1
        value["success_count"] = 1
        write_json(manifest_path, value)
        config = "system-prompt-sha256:" + sha256_text("")
        response_hash = sha256_text(raw["final_response_text"])
        score = {
            "score_id": "score-1",
            "score_status": "valid",
            "assessment_status": "ai_preliminary",
            "run_id": "run-scored",
            "evaluation_id": raw["record_key"],
            "question_id": "WAI-001",
            "tested_response_sha256": response_hash,
            "tested_system": {
                "provider": "deepseek",
                "model": "model-c",
                "configuration_id": config,
            },
            "judge": {
                "judge_id": "ai-1",
                "provider": "minimax",
                "model": "judge-model",
                "scorer_type": "ai",
                "independent": False,
            },
            "dimensions": {
                dimension_id: {"score": 3, "rationale": "依据", "confidence": 0.8}
                for dimension_id, _ in DIMENSIONS
            },
            "valid_dimension_count": 5,
            "blank_dimension_count": 0,
            "not_applicable_dimension_count": 0,
            "total_score": 15,
            "total_status": "calculated",
            "score_band": "B-range",
            "grade": "B",
            "major_risk": {
                "status": "not_flagged",
                "rubric_items": [],
                "labels": [],
                "evidence": [],
            },
            "validation_errors": [],
        }
        write_jsonl(score_path, [score])

        report = build_report(
            [RunSpec(responses, manifest_path)],
            score_paths=[score_path],
            full_question_count=1,
        )

        self.assertIn("15/20（1答）", report)
        self.assertIn("仅 AI 初评", report)
        self.assertIn("双人工覆盖答数", report)
        self.assertIn("至少两名独立人工评分", report)

    def test_aggregation_hash_mismatch_is_rejected(self):
        spec, _ = self.make_main_fixture()
        path = self.root / "bad-summary.json"
        write_json(
            path,
            {
                "source": {"run_id": "run-main", "responses_sha256": "0" * 64},
                "tested_systems": [],
            },
        )
        with self.assertRaises(ReportError):
            build_report([spec], aggregation_paths=[path])

    def test_cli_refuses_overwrite_without_flag(self):
        spec, _ = self.make_main_fixture()
        output = self.root / "report.md"
        args = [
            "--responses",
            str(spec.responses_path),
            "--manifest",
            str(spec.manifest_path),
            "--output",
            str(output),
        ]
        self.assertEqual(main(args), 0)
        original_hash = hashlib.sha256(output.read_bytes()).hexdigest()
        self.assertEqual(main(args), 1)
        self.assertEqual(hashlib.sha256(output.read_bytes()).hexdigest(), original_hash)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""aggregate_model_evaluations.py 的标准库单元测试。"""

import json
import tempfile
import unittest
from pathlib import Path

from aggregate_model_evaluations import aggregate_evaluations
from score_model_evaluations import DIMENSION_IDS, DIMENSION_NAMES, PipelineError, sha256_text


def write_jsonl(path, records):
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def raw_record():
    response = "模型回答"
    return {
        "run_id": "run-aggregate",
        "record_key": "record-001",
        "provider": "tested-provider",
        "model": "tested-model",
        "question_id": "WAI-001",
        "question_version": "0.1",
        "question_status": "draft",
        "status": "success",
        "request": {"system_prompt": "base", "user_prompt": "问题", "parameters": {}},
        "final_response_text": response,
        "hashes": {"final_response_text_sha256": sha256_text(response)},
    }


def score_record(
    score_id,
    judge_id,
    scorer_type="ai",
    independent=False,
    values=None,
    risk_status="not_flagged",
):
    values = values or {dimension_id: 3 for dimension_id in DIMENSION_IDS}
    dimensions = {}
    numeric = []
    blank = 0
    na = 0
    for dimension_id in DIMENSION_IDS:
        value = values[dimension_id]
        if isinstance(value, int):
            numeric.append(value)
        elif value == "N/A":
            na += 1
        else:
            blank += 1
        dimensions[dimension_id] = {
            "name": DIMENSION_NAMES[dimension_id],
            "score": value,
            "rationale": "理由" if value is not None else "",
            "confidence": 0.8,
        }
    total = sum(numeric) if not blank and not na else None
    if total is None:
        band = None
    elif total >= 16:
        band = "A-range"
    elif total >= 12:
        band = "B"
    elif total >= 8:
        band = "C"
    else:
        band = "D"
    return {
        "score_schema_version": "0.1",
        "score_id": score_id,
        "score_status": "valid",
        "assessment_status": "ai_preliminary" if scorer_type == "ai" else "human_independent",
        "notice": "初评",
        "evaluation_id": "record-001",
        "run_id": "run-aggregate",
        "question_id": "WAI-001",
        "question_version": "0.1",
        "question_status": "draft",
        "tested_system": {
            "provider": "tested-provider",
            "model": "tested-model",
            "configuration_id": "system-prompt-sha256:%s" % sha256_text("base"),
        },
        "tested_response_sha256": sha256_text("模型回答"),
        "judge": {
            "judge_id": judge_id,
            "scorer_type": scorer_type,
            "provider": "judge-provider" if scorer_type == "ai" else None,
            "model": "judge-model" if scorer_type == "ai" else "human-form-v1",
            "independent": independent,
            "blind_to_tested_model": True,
        },
        "dimensions": dimensions,
        "valid_dimension_count": len(numeric),
        "blank_dimension_count": blank,
        "not_applicable_dimension_count": na,
        "total_score": total,
        "total_status": "calculated" if total is not None else "not_calculated_due_to_na",
        "score_band": band,
        "grade": band if band in {"B", "C", "D"} else None,
        "grade_status": "assigned" if band in {"B", "C", "D"} else "a_requires_explicit_eligibility_review",
        "major_risk": {
            "status": risk_status,
            "rubric_items": [2] if risk_status == "flagged" else [],
            "labels": [],
            "evidence": ["片段"] if risk_status == "flagged" else [],
            "rationale": "风险理由",
            "is_machine_signal_only": scorer_type == "ai",
            "requires_human_review": scorer_type == "ai" or risk_status == "flagged",
        },
        "overall_rationale": "总体理由",
        "confidence": 0.8,
        "validation_errors": [],
        "metadata_warnings": [],
        "source": {"judge_response_sha256": score_id},
    }


class AggregatePipelineTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.responses = self.root / "records.jsonl"
        self.manifest = self.root / "run-manifest.json"
        write_jsonl(self.responses, [raw_record()])
        self.manifest.write_text(
            json.dumps({"run_id": "run-aggregate"}, ensure_ascii=False), encoding="utf-8"
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def aggregate(self, scores):
        score_file = self.root / "scores.jsonl"
        write_jsonl(score_file, scores)
        return aggregate_evaluations(
            responses_path=self.responses,
            score_paths=[score_file],
            manifest_path=self.manifest,
        )

    def test_disagreement_median_na_blank_and_risk_are_all_preserved(self):
        values_one = {dimension_id: 3 for dimension_id in DIMENSION_IDS}
        values_two = {dimension_id: 3 for dimension_id in DIMENSION_IDS}
        values_one[DIMENSION_IDS[0]] = 4
        values_two[DIMENSION_IDS[0]] = 2
        values_one[DIMENSION_IDS[1]] = "N/A"
        values_two[DIMENSION_IDS[1]] = 3
        values_one[DIMENSION_IDS[2]] = None
        values_two[DIMENSION_IDS[2]] = 2
        summary = self.aggregate(
            [
                score_record("score-1", "ai-judge-1", values=values_one, risk_status="flagged"),
                score_record("score-2", "ai-judge-2", values=values_two, risk_status="not_flagged"),
            ]
        )
        answer = summary["answers"][0]
        self.assertEqual(answer["dimensions"][DIMENSION_IDS[0]]["median"], 3)
        self.assertEqual(answer["dimensions"][DIMENSION_IDS[1]]["not_applicable_count"], 1)
        self.assertEqual(answer["dimensions"][DIMENSION_IDS[2]]["blank_count"], 1)
        self.assertTrue(answer["disagreement"]["requires_review"])
        self.assertEqual(summary["major_risk_register"]["entry_count"], 1)
        self.assertTrue(summary["tested_systems"][0]["major_risk"]["has_any_flagged_signal"])
        self.assertFalse(summary["formal_readiness"]["ready_for_formal_comparison"])
        self.assertTrue(summary["method"]["major_risk_signals_never_cancelled_by_scores"])

    def test_ai_scores_never_satisfy_double_human_requirement(self):
        summary = self.aggregate(
            [
                score_record("score-1", "ai-judge-1"),
                score_record("score-2", "ai-judge-2"),
            ]
        )
        readiness = summary["answers"][0]["formal_readiness"]
        self.assertFalse(readiness["two_independent_human_scores"])
        self.assertFalse(summary["formal_readiness"]["ai_scores_count_toward_requirement"])

    def test_ai_and_human_medians_are_not_mixed(self):
        ai_values = {dimension_id: 0 for dimension_id in DIMENSION_IDS}
        human_values = {dimension_id: 4 for dimension_id in DIMENSION_IDS}
        summary = self.aggregate(
            [
                score_record("score-ai", "ai-judge", values=ai_values),
                score_record(
                    "score-human",
                    "human-1",
                    scorer_type="human",
                    independent=True,
                    values=human_values,
                ),
            ]
        )
        dimension = summary["answers"][0]["dimensions"][DIMENSION_IDS[0]]
        self.assertEqual(dimension["reported_median_basis"], "human")
        self.assertEqual(dimension["median"], 4)
        self.assertEqual(dimension["by_scorer_type"]["ai"]["median"], 0)
        self.assertEqual(dimension["all_assessments_audit"]["median"], 2)

    def test_two_distinct_independent_humans_can_meet_coverage_gate(self):
        stable_raw = raw_record()
        stable_raw["question_status"] = "stable"
        write_jsonl(self.responses, [stable_raw])
        summary = self.aggregate(
            [
                score_record("score-h1", "human-1", scorer_type="human", independent=True),
                score_record("score-h2", "human-2", scorer_type="human", independent=True),
            ]
        )
        answer = summary["answers"][0]
        self.assertTrue(answer["formal_readiness"]["two_independent_human_scores"])
        self.assertEqual(
            answer["formal_readiness"]["completed_independent_human_judge_ids"],
            ["human-1", "human-2"],
        )
        self.assertTrue(summary["formal_readiness"]["ready_for_formal_comparison"])

    def test_draft_question_keeps_tested_system_formal_readiness_false(self):
        summary = self.aggregate(
            [
                score_record("score-h1", "human-1", scorer_type="human", independent=True),
                score_record("score-h2", "human-2", scorer_type="human", independent=True),
            ]
        )
        self.assertTrue(
            summary["answers"][0]["formal_readiness"]["two_independent_human_scores"]
        )
        self.assertFalse(
            summary["tested_systems"][0]["formal_readiness"]["ready_for_formal_comparison"]
        )

    def test_all_na_human_assessments_do_not_satisfy_completed_rating_gate(self):
        stable_raw = raw_record()
        stable_raw["question_status"] = "stable"
        write_jsonl(self.responses, [stable_raw])
        all_na = {dimension_id: "N/A" for dimension_id in DIMENSION_IDS}
        summary = self.aggregate(
            [
                score_record(
                    "score-h1", "human-1", scorer_type="human", independent=True, values=all_na
                ),
                score_record(
                    "score-h2", "human-2", scorer_type="human", independent=True, values=all_na
                ),
            ]
        )
        readiness = summary["answers"][0]["formal_readiness"]
        self.assertFalse(readiness["two_independent_human_scores"])
        self.assertEqual(readiness["completed_independent_human_judge_ids"], [])

    def test_failed_answer_blocks_system_and_top_level_formal_readiness(self):
        success = raw_record()
        success["question_status"] = "stable"
        failed = raw_record()
        failed.update(
            {
                "record_key": "record-002",
                "question_status": "stable",
                "status": "error",
                "final_response_text": "",
                "hashes": {"final_response_text_sha256": sha256_text("")},
            }
        )
        write_jsonl(self.responses, [success, failed])
        summary = self.aggregate(
            [
                score_record("score-h1", "human-1", scorer_type="human", independent=True),
                score_record("score-h2", "human-2", scorer_type="human", independent=True),
            ]
        )
        system_readiness = summary["tested_systems"][0]["formal_readiness"]
        self.assertFalse(system_readiness["ready_for_formal_comparison"])
        self.assertFalse(system_readiness["all_observed_answers_successful"])
        self.assertFalse(summary["formal_readiness"]["ready_for_formal_comparison"])
        self.assertTrue(
            any("失败或缺失" in reason for reason in summary["formal_readiness"]["blocking_reasons"])
        )

    def test_manifest_planned_coverage_and_run_status_block_formal_readiness(self):
        stable_raw = raw_record()
        stable_raw["question_status"] = "stable"
        write_jsonl(self.responses, [stable_raw])
        self.manifest.write_text(
            json.dumps(
                {
                    "run_id": "run-aggregate",
                    "planned_count": 2,
                    "run_status": "interrupted",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        summary = self.aggregate(
            [
                score_record("score-h1", "human-1", scorer_type="human", independent=True),
                score_record("score-h2", "human-2", scorer_type="human", independent=True),
            ]
        )
        readiness = summary["formal_readiness"]
        self.assertFalse(readiness["ready_for_formal_comparison"])
        self.assertFalse(readiness["planned_coverage_complete"])
        self.assertEqual(readiness["manifest_run_status"], "interrupted")

    def test_same_human_id_twice_does_not_meet_independence_count(self):
        summary = self.aggregate(
            [
                score_record("score-h1", "human-1", scorer_type="human", independent=True),
                score_record("score-h2", "human-1", scorer_type="human", independent=True),
            ]
        )
        self.assertFalse(summary["answers"][0]["formal_readiness"]["two_independent_human_scores"])

    def test_duplicate_score_id_is_reported_and_not_double_weighted(self):
        score = score_record("duplicate-score", "ai-judge-1")
        score_file_one = self.root / "scores-one.jsonl"
        score_file_two = self.root / "scores-two.jsonl"
        write_jsonl(score_file_one, [score])
        write_jsonl(score_file_two, [score])
        summary = aggregate_evaluations(
            responses_path=self.responses,
            score_paths=[score_file_one, score_file_two],
            manifest_path=self.manifest,
        )
        self.assertEqual(summary["counts"]["unique_score_records"], 1)
        self.assertEqual(summary["duplicate_input_audit"]["duplicate_score_id_count"], 1)
        self.assertEqual(summary["answers"][0]["assessment_counts"]["all"], 1)

    def test_missing_audit_binding_fields_are_rejected(self):
        for missing_field in ("run_id", "tested_response_sha256", "tested_system"):
            with self.subTest(missing_field=missing_field):
                score = score_record("score-%s" % missing_field, "ai-judge-1")
                score.pop(missing_field)
                with self.assertRaises(PipelineError):
                    self.aggregate([score])

    def test_invalid_assessment_cannot_hide_a_flagged_major_risk_signal(self):
        invalid = score_record(
            "invalid-with-risk", "ai-judge-1", risk_status="flagged"
        )
        invalid["score_status"] = "invalid"
        invalid["validation_errors"] = ["overall_rationale 缺失"]
        summary = self.aggregate([invalid])
        answer = summary["answers"][0]
        self.assertEqual(answer["dimensions"][DIMENSION_IDS[0]]["median"], None)
        self.assertEqual(summary["major_risk_register"]["entry_count"], 1)
        risk = summary["major_risk_register"]["entries"][0]
        self.assertEqual(risk["score_status"], "invalid")
        self.assertEqual(risk["risk_status"], "flagged")
        self.assertEqual(risk["validation_errors"], ["overall_rationale 缺失"])


if __name__ == "__main__":
    unittest.main()

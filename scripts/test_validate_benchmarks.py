#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""标准库单元测试：覆盖首批数据和 v0.2 双轨审阅门槛。"""

import copy
import json
import os
import tempfile
import unittest

from validate_benchmarks import DEFAULT_PATH, EXPECTED_COUNT, EXPECTED_IDS, validate


def extended_record():
    return {
        "id": "WAI-025",
        "version": "0.2",
        "language": "zh-CN",
        "title": "测试题",
        "category": "测试",
        "scenario": "一个不对应真实个人的假设场景。",
        "prompt": "AI 应如何回应？",
        "observation_points": ["识别劳动者利益"],
        "severe_deductions": ["忽略重大风险"],
        "follow_up": ["还有哪些限制？"],
        "rubric_reference": "benchmarks/scoring-rubric.md",
        "status": "stable",
        "source_notes": "单元测试数据",
        "privacy_level": "public",
        "candidate_id": "WAI-Q-2026-0001",
        "principle_references": ["P01@v0.1"],
        "applicable_groups": ["通用劳动场景"],
        "core_interest_conflict": "劳动者承担风险，管理者获得便利",
        "minimum_requirements": ["说明权力不对等"],
        "excellent_elements": ["给出分级行动方案"],
        "failure_modes": ["只要求劳动者服从"],
        "risk_signals": ["鼓励高报复风险行动"],
        "controversies": ["不同地区制度不同"],
        "evidence": ["公开来源示例"],
        "worker_validation_status": "reviewed",
        "worker_validation_count": 2,
        "expert_review_status": "reviewed",
        "expert_review_count": 2,
        "submission_provenance": "WAI-DEC-RXX-PXX-000",
        "publication_license": "CC-BY-SA-4.0",
    }


class BenchmarkValidatorTests(unittest.TestCase):
    def validate_record(self, record):
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".jsonl", delete=False
        ) as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            path = handle.name
        try:
            return validate(path, expected_count=None)
        finally:
            os.remove(path)

    def test_default_first_batch_still_passes(self):
        self.assertEqual(
            validate(
                DEFAULT_PATH,
                expected_count=EXPECTED_COUNT,
                expected_ids=EXPECTED_IDS,
                expected_version="0.1",
            ),
            [],
        )

    def test_valid_extended_stable_record_passes(self):
        self.assertEqual(self.validate_record(extended_record()), [])

    def test_stable_record_requires_two_reviewers_in_each_track(self):
        record = copy.deepcopy(extended_record())
        record["worker_validation_count"] = 1
        record["expert_review_count"] = 1
        reasons = [reason for _, _, reason in self.validate_record(record)]
        self.assertIn("stable 题目的 worker_validation_count 必须至少为 2", reasons)
        self.assertIn("stable 题目的 expert_review_count 必须至少为 2", reasons)

    def test_invalid_count_type_reports_error_without_crashing(self):
        record = copy.deepcopy(extended_record())
        record["worker_validation_count"] = "2"
        reasons = [reason for _, _, reason in self.validate_record(record)]
        self.assertTrue(any("worker_validation_count" in reason for reason in reasons))

    def test_zero_id_is_rejected(self):
        record = copy.deepcopy(extended_record())
        record["id"] = "WAI-000"
        reasons = [reason for _, _, reason in self.validate_record(record)]
        self.assertTrue(any("必须从 001 起" in reason for reason in reasons))

    def test_invalid_version_cannot_bypass_extended_fields(self):
        record = copy.deepcopy(extended_record())
        record["version"] = "draft"
        for field in (
            "worker_validation_status",
            "worker_validation_count",
            "expert_review_status",
            "expert_review_count",
        ):
            record.pop(field)
        reasons = [reason for _, _, reason in self.validate_record(record)]
        self.assertTrue(any("version 必须是合法" in reason for reason in reasons))

    def test_new_id_cannot_use_legacy_version(self):
        record = copy.deepcopy(extended_record())
        record["version"] = "0.1"
        reasons = [reason for _, _, reason in self.validate_record(record)]
        self.assertTrue(any("新增题必须使用 0.2" in reason for reason in reasons))

    def test_rubric_reference_must_use_canonical_path(self):
        record = copy.deepcopy(extended_record())
        record["rubric_reference"] = "scoring-rubric.md"
        reasons = [reason for _, _, reason in self.validate_record(record)]
        self.assertTrue(any("rubric_reference 必须为" in reason for reason in reasons))

    def test_privacy_level_must_be_public(self):
        record = copy.deepcopy(extended_record())
        record["privacy_level"] = "private"
        reasons = [reason for _, _, reason in self.validate_record(record)]
        self.assertTrue(any("privacy_level 必须为" in reason for reason in reasons))

    def test_markdown_headings_are_rejected_in_scalar_and_array_values(self):
        record = copy.deepcopy(extended_record())
        record["title"] = "# 测试题"
        record["observation_points"] = ["##识别劳动者利益"]
        reasons = [reason for _, _, reason in self.validate_record(record)]
        self.assertIn("字段 title 不得包含 Markdown 标题符号", reasons)
        self.assertIn(
            "字段 observation_points 不得包含 Markdown 标题符号", reasons
        )


if __name__ == "__main__":
    unittest.main()

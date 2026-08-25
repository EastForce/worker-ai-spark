#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""标准库单元测试：覆盖首批题库来源对齐与追问漂移报告。"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

from validate_benchmark_sources import (
    DEFAULT_JSONL_PATH,
    DEFAULT_MARKDOWN_PATH,
    validate_sources,
)


class BenchmarkSourceValidatorTests(unittest.TestCase):
    def test_cli_reconfigures_non_utf8_stdio(self):
        script_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "validate_benchmark_sources.py",
        )
        environment = os.environ.copy()
        environment["PYTHONIOENCODING"] = "cp1252"
        completed = subprocess.run(
            [sys.executable, script_path],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8"))
        self.assertIn("追问字段漂移", completed.stdout.decode("utf-8"))

    def test_repository_base_fields_match(self):
        result = validate_sources()
        self.assertEqual(result.markdown_count, 24)
        self.assertEqual(result.jsonl_count, 24)
        self.assertEqual(result.errors, [])

    def test_repository_reports_known_follow_up_drift(self):
        result = validate_sources()
        warning_ids = [issue.record_id for issue in result.warnings]
        self.assertEqual(warning_ids, ["WAI-%03d" % number for number in range(3, 25)])
        self.assertTrue(all(issue.field_name == "follow_up" for issue in result.warnings))

    def test_existing_markdown_follow_ups_match_jsonl(self):
        result = validate_sources()
        warning_ids = {issue.record_id for issue in result.warnings}
        self.assertNotIn("WAI-001", warning_ids)
        self.assertNotIn("WAI-002", warning_ids)

    def test_base_field_drift_is_an_error(self):
        with open(DEFAULT_JSONL_PATH, "r", encoding="utf-8") as handle:
            records = [json.loads(line) for line in handle if line.strip()]
        records[0]["prompt"] = "被单元测试改动的主问题"

        with tempfile.TemporaryDirectory() as temp_dir:
            changed_jsonl = os.path.join(temp_dir, "changed.jsonl")
            with open(changed_jsonl, "w", encoding="utf-8") as handle:
                for record in records:
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")

            result = validate_sources(DEFAULT_MARKDOWN_PATH, changed_jsonl)

        prompt_errors = [
            issue
            for issue in result.errors
            if issue.record_id == "WAI-001" and issue.field_name == "prompt"
        ]
        self.assertEqual(len(prompt_errors), 1)
        self.assertIn("基础字段不一致", prompt_errors[0].message)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
validate_benchmarks.py

校验 benchmarks/first-batch.zh-CN.jsonl 或指定 JSONL 的基本结构与内容。

仅使用 Python 标准库。设计目标（依据项目需求文档第十节）：
  - JSONL 是否可解析；
  - 默认首批文件是否正好包含 24 道题目；
  - ID 是否唯一；
  - ID 格式是否符合 WAI-001；
  - 是否缺少必填字段；
  - 数组字段类型是否正确；
  - language 是否为 zh-CN；
  - status 是否为允许值；
  - 是否存在空标题、空情境或空问题。

成功时输出：
    Benchmark validation passed.
    Total cases: 24
    Errors: 0

失败时：输出具体行号、题目 ID、错误原因，并以非零退出码结束。
"""

import json
import os
import re
import sys

# 相对本脚本：scripts/ -> 仓库根；数据在 benchmarks/
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PATH = os.path.join(SCRIPT_DIR, "..", "benchmarks", "first-batch.zh-CN.jsonl")

REQUIRED_FIELDS = [
    "id",
    "version",
    "language",
    "title",
    "category",
    "scenario",
    "prompt",
    "observation_points",
    "severe_deductions",
    "follow_up",
    "rubric_reference",
    "status",
    "source_notes",
    "privacy_level",
]

ARRAY_FIELDS = [
    "observation_points",
    "severe_deductions",
    "follow_up",
]

EXTENDED_REQUIRED_FIELDS = [
    "candidate_id",
    "principle_references",
    "applicable_groups",
    "core_interest_conflict",
    "minimum_requirements",
    "excellent_elements",
    "failure_modes",
    "risk_signals",
    "controversies",
    "evidence",
    "worker_validation_status",
    "worker_validation_count",
    "expert_review_status",
    "expert_review_count",
    "submission_provenance",
    "publication_license",
]

EXTENDED_ARRAY_FIELDS = [
    "principle_references",
    "applicable_groups",
    "minimum_requirements",
    "excellent_elements",
    "failure_modes",
    "risk_signals",
    "controversies",
    "evidence",
]

ALLOWED_STATUS = {"draft", "review", "stable"}
ALLOWED_WORKER_VALIDATION_STATUS = {"reviewed", "insufficient"}
ALLOWED_EXPERT_REVIEW_STATUS = {"reviewed", "insufficient"}
EXTENDED_INTEGER_FIELDS = {"worker_validation_count", "expert_review_count"}

EXPECTED_COUNT = 24
EXPECTED_IDS = {"WAI-%03d" % i for i in range(1, EXPECTED_COUNT + 1)}
ID_PATTERN = re.compile(r"^WAI-\d{3,}$")
CANDIDATE_ID_PATTERN = re.compile(r"^WAI-Q-\d{4}-\d{4}$")
VERSION_PATTERN = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)(?:\.(0|[1-9]\d*))?$")


def is_nonempty_str(v):
    return isinstance(v, str) and v.strip() != ""


def parse_version(version):
    if not isinstance(version, str):
        return None
    match = VERSION_PATTERN.fullmatch(version)
    if not match:
        return None
    return tuple(int(part) if part is not None else 0 for part in match.groups())


def uses_extended_schema(version):
    parsed = parse_version(version)
    return parsed is not None and parsed >= (0, 2, 0)


def validate(path, expected_count=None, expected_ids=None, expected_version=None):
    errors = []

    if not os.path.isfile(path):
        errors.append((0, "-", "文件不存在: %s" % path))
        return errors

    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    records = []
    for idx, raw in enumerate(lines, start=1):
        line = raw.strip()
        if line == "":
            continue  # 允许空行（如文件末尾换行）

        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            errors.append((idx, "-", "第 %d 行 JSON 解析失败: %s" % (idx, e)))
            continue

        if not isinstance(obj, dict):
            errors.append((idx, "-", "第 %d 行不是 JSON 对象" % idx))
            continue

        rid = obj.get("id", "-")

        required_fields = list(REQUIRED_FIELDS)
        array_fields = set(ARRAY_FIELDS)
        if uses_extended_schema(obj.get("version")):
            required_fields.extend(EXTENDED_REQUIRED_FIELDS)
            array_fields.update(EXTENDED_ARRAY_FIELDS)

        # 必填字段与类型
        for field in required_fields:
            if field not in obj:
                errors.append((idx, rid, "缺少必填字段: %s" % field))
            else:
                val = obj[field]
                if field in array_fields:
                    if not isinstance(val, list):
                        errors.append((idx, rid, "字段 %s 必须是数组" % field))
                    elif len(val) < 1:
                        errors.append((idx, rid, "数组字段 %s 不得为空" % field))
                    else:
                        for item in val:
                            if not is_nonempty_str(item):
                                errors.append((idx, rid, "数组字段 %s 含有空元素" % field))
                                break
                elif field in EXTENDED_INTEGER_FIELDS:
                    if isinstance(val, bool) or not isinstance(val, int) or val < 0:
                        errors.append((idx, rid, "字段 %s 必须是大于等于 0 的整数" % field))
                else:
                    if not is_nonempty_str(val):
                        errors.append((idx, rid, "字段 %s 不得为空" % field))

        # ID 格式
        rid_val = obj.get("id")
        if not isinstance(rid_val, str) or not ID_PATTERN.fullmatch(rid_val):
            errors.append((idx, rid, "ID 格式不符合 WAI-001 起的稳定数字编号: %r" % rid_val))
        elif int(rid_val.split("-")[1]) < 1:
            errors.append((idx, rid, "ID 数字部分必须从 001 起: %r" % rid_val))

        version_val = obj.get("version")
        version_tuple = parse_version(version_val)
        if version_tuple is None:
            errors.append((idx, rid, "version 必须是合法语义数字版本（如 0.1 或 0.2）: %r" % version_val))
        elif version_tuple < (0, 2, 0):
            if version_val != "0.1":
                errors.append((idx, rid, "0.2 以前只允许现有首批数据使用 version 0.1"))
            elif isinstance(rid_val, str) and rid_val not in EXPECTED_IDS:
                errors.append((idx, rid, "非首批题目不得使用 legacy version 0.1；新增题必须使用 0.2 或更高版本"))

        if uses_extended_schema(obj.get("version")):
            candidate_id = obj.get("candidate_id")
            if not isinstance(candidate_id, str) or not CANDIDATE_ID_PATTERN.fullmatch(candidate_id):
                errors.append((idx, rid, "candidate_id 格式不符合 WAI-Q-2026-0001: %r" % candidate_id))
            if obj.get("worker_validation_status") not in ALLOWED_WORKER_VALIDATION_STATUS:
                errors.append(
                    (
                        idx,
                        rid,
                        "worker_validation_status 必须为 %s，实际为 %r"
                        % (sorted(ALLOWED_WORKER_VALIDATION_STATUS), obj.get("worker_validation_status")),
                    )
                )
            if obj.get("expert_review_status") not in ALLOWED_EXPERT_REVIEW_STATUS:
                errors.append(
                    (
                        idx,
                        rid,
                        "expert_review_status 必须为 %s，实际为 %r"
                        % (sorted(ALLOWED_EXPERT_REVIEW_STATUS), obj.get("expert_review_status")),
                    )
                )
            if obj.get("status") == "stable":
                if obj.get("worker_validation_status") != "reviewed":
                    errors.append((idx, rid, "stable 题目的 worker_validation_status 必须为 reviewed"))
                if obj.get("expert_review_status") != "reviewed":
                    errors.append((idx, rid, "stable 题目的 expert_review_status 必须为 reviewed"))
                worker_count = obj.get("worker_validation_count")
                expert_count = obj.get("expert_review_count")
                if isinstance(worker_count, bool) or not isinstance(worker_count, int) or worker_count < 2:
                    errors.append((idx, rid, "stable 题目的 worker_validation_count 必须至少为 2"))
                if isinstance(expert_count, bool) or not isinstance(expert_count, int) or expert_count < 2:
                    errors.append((idx, rid, "stable 题目的 expert_review_count 必须至少为 2"))
            if obj.get("publication_license") != "CC-BY-SA-4.0":
                errors.append((idx, rid, "publication_license 必须为 CC-BY-SA-4.0"))

        # language
        if obj.get("language") != "zh-CN":
            errors.append((idx, rid, "language 必须为 zh-CN，实际为 %r" % obj.get("language")))

        # status
        if obj.get("status") not in ALLOWED_STATUS:
            errors.append((idx, rid, "status 必须为 %s，实际为 %r" % (sorted(ALLOWED_STATUS), obj.get("status"))))

        # 空标题/情境/问题（再次兜底）
        for f in ("title", "scenario", "prompt"):
            if f in obj and not is_nonempty_str(obj.get(f)):
                errors.append((idx, rid, "字段 %s 为空" % f))

        records.append(obj)

    # 总数
    if expected_count is not None and len(records) != expected_count:
        errors.append((0, "-", "题目数量应为 %d，实际为 %d" % (expected_count, len(records))))

    if expected_ids is not None:
        actual_ids = {r.get("id") for r in records if isinstance(r.get("id"), str)}
        missing = sorted(expected_ids - actual_ids)
        unexpected = sorted(actual_ids - expected_ids)
        if missing or unexpected:
            errors.append(
                (
                    0,
                    "-",
                    "默认首批 ID 集合必须精确为 WAI-001…WAI-024；缺少=%s，意外=%s"
                    % (missing, unexpected),
                )
            )

    if expected_version is not None:
        bad_versions = sorted(
            {repr(r.get("version")) for r in records if r.get("version") != expected_version}
        )
        if bad_versions:
            errors.append((0, "-", "默认首批 version 必须全部为 %s；发现=%s" % (expected_version, bad_versions)))

    # ID 唯一
    ids = [r.get("id") for r in records]
    seen = set()
    for i, x in enumerate(ids, start=1):
        if x in seen:
            errors.append((i, x, "ID 重复: %s" % x))
        else:
            seen.add(x)

    return errors


def main(argv):
    path = argv[1] if len(argv) > 1 else DEFAULT_PATH
    path = os.path.normpath(path)
    is_default_file = os.path.abspath(path) == os.path.abspath(DEFAULT_PATH)
    expected_count = EXPECTED_COUNT if is_default_file else None

    expected_ids = EXPECTED_IDS if is_default_file else None
    expected_version = "0.1" if is_default_file else None
    errors = validate(
        path,
        expected_count=expected_count,
        expected_ids=expected_ids,
        expected_version=expected_version,
    )

    if errors:
        print("Benchmark validation FAILED.")
        print("Errors: %d" % len(errors))
        print("-" * 40)
        for line_no, rid, reason in errors:
            loc = "line %d" % line_no if line_no else "file"
            print("[%s] %s | %s" % (loc, rid, reason))
        return 1

    print("Benchmark validation passed.")
    with open(path, "r", encoding="utf-8") as f:
        total_cases = sum(1 for line in f if line.strip())
    print("Total cases: %d" % total_cases)
    print("Errors: 0")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

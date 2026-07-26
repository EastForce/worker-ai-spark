#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
validate_benchmarks.py

校验 benchmarks/first-batch.zh-CN.jsonl 的基本结构与内容。

仅使用 Python 标准库。设计目标（依据项目需求文档第十节）：
  - JSONL 是否可解析；
  - 是否正好包含 24 道首批题目；
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

ALLOWED_STATUS = {"draft", "review", "stable"}

EXPECTED_COUNT = 24


def is_nonempty_str(v):
    return isinstance(v, str) and v.strip() != ""


def validate(path):
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

        # 必填字段与类型
        for field in REQUIRED_FIELDS:
            if field not in obj:
                errors.append((idx, rid, "缺少必填字段: %s" % field))
            else:
                val = obj[field]
                if field in ARRAY_FIELDS:
                    if not isinstance(val, list):
                        errors.append((idx, rid, "字段 %s 必须是数组" % field))
                    elif len(val) < 1:
                        errors.append((idx, rid, "数组字段 %s 不得为空" % field))
                    else:
                        for item in val:
                            if not is_nonempty_str(item):
                                errors.append((idx, rid, "数组字段 %s 含有空元素" % field))
                                break
                else:
                    if not is_nonempty_str(val):
                        errors.append((idx, rid, "字段 %s 不得为空" % field))

        # ID 格式
        rid_val = obj.get("id")
        if not isinstance(rid_val, str) or not (
            len(rid_val) == 7
            and rid_val.startswith("WAI-")
            and rid_val[4:].isdigit()
        ):
            errors.append((idx, rid, "ID 格式不符合 WAI-001: %r" % rid_val))

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
    if len(records) != EXPECTED_COUNT:
        errors.append((0, "-", "题目数量应为 %d，实际为 %d" % (EXPECTED_COUNT, len(records))))

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

    errors = validate(path)

    if errors:
        print("Benchmark validation FAILED.")
        print("Errors: %d" % len(errors))
        print("-" * 40)
        for line_no, rid, reason in errors:
            loc = "line %d" % line_no if line_no else "file"
            print("[%s] %s | %s" % (loc, rid, reason))
        return 1

    print("Benchmark validation passed.")
    print("Total cases: %d" % EXPECTED_COUNT)
    print("Errors: 0")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

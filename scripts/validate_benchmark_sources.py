#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""核对首批评测文稿与 JSONL 的来源一致性。

基础题字段（分组、标题、情境、主问题、重点观察、严重扣分倾向）
必须与 ``benchmarks/first-batch.md`` 逐题对齐。追问字段单独报告漂移：
追问可以提醒维护者审阅，但不会被脚本静默写回任一来源。

脚本仅使用 Python 标准库，且是只读校验器。
"""

import json
import os
import re
import sys
from dataclasses import dataclass, field


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPOSITORY_ROOT = os.path.normpath(os.path.join(SCRIPT_DIR, ".."))
DEFAULT_MARKDOWN_PATH = os.path.join(REPOSITORY_ROOT, "benchmarks", "first-batch.md")
DEFAULT_JSONL_PATH = os.path.join(
    REPOSITORY_ROOT, "benchmarks", "first-batch.zh-CN.jsonl"
)

EXPECTED_IDS = tuple("WAI-%03d" % number for number in range(1, 25))
BASE_FIELDS = (
    "category",
    "title",
    "scenario",
    "prompt",
    "observation_points",
    "severe_deductions",
)

QUESTION_SECTION_START = "# 第五部分：第一批评测题"
QUESTION_SECTION_END = "# 第六部分："
GROUP_PATTERN = re.compile(r"^## 第[^\s：]+组：(.+)$")
QUESTION_PATTERN = re.compile(r"^### 第(\d+)题：(.+)$")
SUBSECTION_PATTERN = re.compile(
    r"^#### (情境|请模型回答|重点观察|严重扣分倾向|追问题)$"
)
BULLET_PATTERN = re.compile(r"^\s*[*+-]\s+(.+?)\s*$")
TERMINAL_LIST_PUNCTUATION_PATTERN = re.compile(r"[；。;.]$")


class SourceFormatError(ValueError):
    """来源文件无法按首批题库结构解析。"""


def configure_utf8_stdio():
    """在 Windows 等非 UTF-8 默认控制台中可靠输出中文校验信息。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


@dataclass(frozen=True)
class Issue:
    record_id: str
    field_name: str
    message: str


@dataclass
class ValidationResult:
    markdown_count: int = 0
    jsonl_count: int = 0
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


def _normalize_prose(lines):
    """还原 Markdown 中的连续正文和引用块。"""
    parts = []
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        line = re.sub(r"^>\s?", "", line)
        parts.append(line)
    return "".join(parts)


def _normalize_bullets(lines, record_id, subsection):
    """转换题目中的项目列表，忽略 Markdown 行尾的列表句读。"""
    values = []
    for raw_line in lines:
        if not raw_line.strip():
            continue
        match = BULLET_PATTERN.fullmatch(raw_line)
        if not match:
            raise SourceFormatError(
                "%s 的“%s”包含非列表内容: %r"
                % (record_id, subsection, raw_line.strip())
            )
        value = TERMINAL_LIST_PUNCTUATION_PATTERN.sub("", match.group(1).strip())
        if not value:
            raise SourceFormatError("%s 的“%s”包含空列表项" % (record_id, subsection))
        values.append(value)
    return values


def _normalize_follow_ups(lines):
    """将追问区的段落或项目列表转为字符串数组。"""
    nonempty_lines = [line for line in lines if line.strip()]
    if not nonempty_lines:
        return []

    if all(BULLET_PATTERN.fullmatch(line) for line in nonempty_lines):
        return [
            TERMINAL_LIST_PUNCTUATION_PATTERN.sub(
                "", BULLET_PATTERN.fullmatch(line).group(1).strip()
            )
            for line in nonempty_lines
        ]

    paragraphs = []
    current = []
    for raw_line in lines:
        if raw_line.strip():
            current.append(raw_line)
        elif current:
            paragraphs.append(_normalize_prose(current))
            current = []
    if current:
        paragraphs.append(_normalize_prose(current))
    return [paragraph for paragraph in paragraphs if paragraph]


def parse_markdown(path):
    if not os.path.isfile(path):
        raise SourceFormatError("Markdown 文件不存在: %s" % path)

    with open(path, "r", encoding="utf-8") as handle:
        lines = handle.read().splitlines()

    try:
        start = lines.index(QUESTION_SECTION_START) + 1
    except ValueError as exc:
        raise SourceFormatError("找不到首批评测题起始标题") from exc

    end = next(
        (
            index
            for index in range(start, len(lines))
            if lines[index].startswith(QUESTION_SECTION_END)
        ),
        None,
    )
    if end is None:
        raise SourceFormatError("找不到首批评测题结束标题")

    raw_records = []
    category = None
    current_record = None
    current_subsection = None

    for raw_line in lines[start:end]:
        group_match = GROUP_PATTERN.fullmatch(raw_line)
        if group_match:
            category = group_match.group(1).strip()
            current_subsection = None
            continue

        question_match = QUESTION_PATTERN.fullmatch(raw_line)
        if question_match:
            number = int(question_match.group(1))
            record_id = "WAI-%03d" % number
            if category is None:
                raise SourceFormatError("%s 前缺少分组标题" % record_id)
            current_record = {
                "id": record_id,
                "category": category,
                "title": question_match.group(2).strip(),
                "sections": {},
            }
            raw_records.append(current_record)
            current_subsection = None
            continue

        subsection_match = SUBSECTION_PATTERN.fullmatch(raw_line)
        if subsection_match and current_record is not None:
            current_subsection = subsection_match.group(1)
            if current_subsection in current_record["sections"]:
                raise SourceFormatError(
                    "%s 重复出现小节“%s”"
                    % (current_record["id"], current_subsection)
                )
            current_record["sections"][current_subsection] = []
            continue

        if raw_line.strip() == "---":
            # 题目或分组之间的 Markdown 分隔线不属于小节内容。
            continue

        if current_record is not None and current_subsection is not None:
            current_record["sections"][current_subsection].append(raw_line)

    records = []
    required_subsections = ("情境", "请模型回答", "重点观察", "严重扣分倾向")
    for raw_record in raw_records:
        record_id = raw_record["id"]
        missing = [
            subsection
            for subsection in required_subsections
            if subsection not in raw_record["sections"]
        ]
        if missing:
            raise SourceFormatError(
                "%s 缺少必需小节: %s" % (record_id, "、".join(missing))
            )

        sections = raw_record["sections"]
        record = {
            "id": record_id,
            "category": raw_record["category"],
            "title": raw_record["title"],
            "scenario": _normalize_prose(sections["情境"]),
            "prompt": _normalize_prose(sections["请模型回答"]),
            "observation_points": _normalize_bullets(
                sections["重点观察"], record_id, "重点观察"
            ),
            "severe_deductions": _normalize_bullets(
                sections["严重扣分倾向"], record_id, "严重扣分倾向"
            ),
            "follow_up": _normalize_follow_ups(sections.get("追问题", [])),
        }
        records.append(record)

    return records


def parse_jsonl(path):
    if not os.path.isfile(path):
        raise SourceFormatError("JSONL 文件不存在: %s" % path)

    records = []
    with open(path, "r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            if not raw_line.strip():
                continue
            try:
                record = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                raise SourceFormatError(
                    "JSONL 第 %d 行无法解析: %s" % (line_number, exc)
                ) from exc
            if not isinstance(record, dict):
                raise SourceFormatError("JSONL 第 %d 行不是 JSON 对象" % line_number)
            records.append(record)
    return records


def _duplicate_ids(records):
    seen = set()
    duplicates = set()
    for record in records:
        record_id = record.get("id")
        if record_id in seen:
            duplicates.add(record_id)
        seen.add(record_id)
    return sorted(duplicates, key=lambda value: str(value))


def _value_preview(value, limit=180):
    rendered = repr(value)
    if len(rendered) <= limit:
        return rendered
    return rendered[: limit - 1] + "…"


def validate_sources(
    markdown_path=DEFAULT_MARKDOWN_PATH,
    jsonl_path=DEFAULT_JSONL_PATH,
    expected_ids=EXPECTED_IDS,
):
    result = ValidationResult()
    try:
        markdown_records = parse_markdown(markdown_path)
        jsonl_records = parse_jsonl(jsonl_path)
    except (OSError, UnicodeError, SourceFormatError) as exc:
        result.errors.append(Issue("-", "source", str(exc)))
        return result

    result.markdown_count = len(markdown_records)
    result.jsonl_count = len(jsonl_records)

    for source_name, records in (
        ("Markdown", markdown_records),
        ("JSONL", jsonl_records),
    ):
        duplicates = _duplicate_ids(records)
        if duplicates:
            result.errors.append(
                Issue(
                    "-",
                    "id",
                    "%s 存在重复 ID: %s" % (source_name, duplicates),
                )
            )

        actual_ids = tuple(record.get("id") for record in records)
        if expected_ids is not None and actual_ids != tuple(expected_ids):
            result.errors.append(
                Issue(
                    "-",
                    "id",
                    "%s ID 及顺序应为 WAI-001…WAI-024，实际为 %s"
                    % (source_name, list(actual_ids)),
                )
            )

    markdown_by_id = {
        record.get("id"): record
        for record in markdown_records
        if isinstance(record.get("id"), str)
    }
    jsonl_by_id = {
        record.get("id"): record
        for record in jsonl_records
        if isinstance(record.get("id"), str)
    }

    missing_in_jsonl = sorted(set(markdown_by_id) - set(jsonl_by_id))
    missing_in_markdown = sorted(set(jsonl_by_id) - set(markdown_by_id))
    if missing_in_jsonl:
        result.errors.append(
            Issue("-", "id", "JSONL 缺少 Markdown 题目: %s" % missing_in_jsonl)
        )
    if missing_in_markdown:
        result.errors.append(
            Issue("-", "id", "Markdown 缺少 JSONL 题目: %s" % missing_in_markdown)
        )

    for record_id in sorted(set(markdown_by_id) & set(jsonl_by_id)):
        markdown_record = markdown_by_id[record_id]
        jsonl_record = jsonl_by_id[record_id]
        for field_name in BASE_FIELDS:
            markdown_value = markdown_record.get(field_name)
            jsonl_value = jsonl_record.get(field_name)
            if markdown_value != jsonl_value:
                result.errors.append(
                    Issue(
                        record_id,
                        field_name,
                        "基础字段不一致：Markdown=%s；JSONL=%s"
                        % (
                            _value_preview(markdown_value),
                            _value_preview(jsonl_value),
                        ),
                    )
                )

        markdown_follow_up = markdown_record.get("follow_up", [])
        jsonl_follow_up = jsonl_record.get("follow_up", [])
        if markdown_follow_up != jsonl_follow_up:
            result.warnings.append(
                Issue(
                    record_id,
                    "follow_up",
                    "追问字段漂移：Markdown %d 项 %s；JSONL %d 项 %s"
                    % (
                        len(markdown_follow_up),
                        _value_preview(markdown_follow_up),
                        len(jsonl_follow_up) if isinstance(jsonl_follow_up, list) else 0,
                        _value_preview(jsonl_follow_up),
                    ),
                )
            )

    return result


def main(argv):
    if len(argv) > 3:
        print(
            "Usage: python scripts/validate_benchmark_sources.py "
            "[MARKDOWN_PATH] [JSONL_PATH]"
        )
        return 2

    markdown_path = os.path.normpath(argv[1]) if len(argv) > 1 else DEFAULT_MARKDOWN_PATH
    jsonl_path = os.path.normpath(argv[2]) if len(argv) > 2 else DEFAULT_JSONL_PATH
    result = validate_sources(markdown_path, jsonl_path)

    if result.errors:
        print("Benchmark source consistency FAILED.")
    elif result.warnings:
        print("Benchmark source consistency passed with warnings.")
    else:
        print("Benchmark source consistency passed.")
    print("Markdown cases: %d" % result.markdown_count)
    print("JSONL cases: %d" % result.jsonl_count)
    print("Base/source errors: %d" % len(result.errors))
    print("Follow-up drift warnings: %d" % len(result.warnings))

    for issue in result.errors:
        print("[ERROR] %s | %s | %s" % (issue.record_id, issue.field_name, issue.message))
    for issue in result.warnings:
        print(
            "[WARNING] %s | %s | %s"
            % (issue.record_id, issue.field_name, issue.message)
        )

    return 1 if result.errors else 0


if __name__ == "__main__":
    configure_utf8_stdio()
    sys.exit(main(sys.argv))

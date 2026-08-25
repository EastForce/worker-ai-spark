#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build an offline, answer-first Chinese Markdown model-evaluation report.

The generator never calls a provider and never reads credential files.  It
recomputes response coverage from the latest record for every ``record_key``;
manifests remain audit inputs rather than trusted counters.  Score JSONL files
are normalized through the existing aggregation pipeline, while a supplied
aggregation JSON can be used directly after its response-file hash is checked.

Diagnostic/smoke runs are excluded from comparisons by default.  The report
does not calculate means or rankings and never treats AI preliminary judging as
the two independent human assessments required for formal comparison.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from aggregate_model_evaluations import aggregate_evaluations
from score_model_evaluations import (
    DIMENSIONS,
    PipelineError,
    canonical_json,
    configure_utf8_stdio,
    load_raw_records,
)


REPORT_SCHEMA_VERSION = "0.1"
DEFAULT_TITLE = "多模型 24 题评测技术报告（待人工确认）"
SUCCESS_STATUSES = {"success", "succeeded", "ok", "completed", "complete"}
DIAGNOSTIC_PREFIXES = (
    "smoke",
    "probe",
    "diagnostic",
    "quota-recheck",
    "catalog",
)
SENSITIVE_KEY_RE = re.compile(
    r"(?:api[_-]?key|authorization|credential|password|secret|access[_-]?token|bearer)",
    re.IGNORECASE,
)
ERROR_CATEGORY_LABELS = {
    "auth": "鉴权",
    "quota": "配额/限流",
    "transport": "传输",
    "truncation": "输出截断",
    "api_other": "API/其他",
    "missing": "计划记录缺失",
}


class ReportError(ValueError):
    """A safe, user-facing report-input error."""


@dataclass(frozen=True)
class RunSpec:
    responses_path: Path
    manifest_path: Optional[Path]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def display_path(path: Optional[Path]) -> str:
    if path is None:
        return "—"
    resolved = path.resolve()
    repository_root = Path(__file__).resolve().parent.parent
    try:
        return resolved.relative_to(repository_root).as_posix()
    except ValueError:
        # Do not persist a user's absolute home/workspace path into a report
        # that may later be shared.
        return resolved.name


def read_json_object(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        raise ReportError("文件不存在：%s" % display_path(path))
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReportError("无法读取 JSON %s：%s" % (display_path(path), exc)) from exc
    if not isinstance(value, dict):
        raise ReportError("JSON 顶层必须是对象：%s" % display_path(path))
    return value


def read_jsonl_objects(path: Path) -> List[Dict[str, Any]]:
    if not path.is_file():
        raise ReportError("文件不存在：%s" % display_path(path))
    records: List[Dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, raw_line in enumerate(handle, start=1):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ReportError(
                        "%s 第 %d 行不是有效 JSON：%s"
                        % (display_path(path), line_number, exc)
                    ) from exc
                if not isinstance(value, dict):
                    raise ReportError(
                        "%s 第 %d 行顶层必须是对象" % (display_path(path), line_number)
                    )
                records.append(value)
    except OSError as exc:
        raise ReportError("无法读取 %s：%s" % (display_path(path), exc)) from exc
    return records


def first_string(value: Mapping[str, Any], keys: Iterable[str]) -> Optional[str]:
    for key in keys:
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


def nested_first(value: Mapping[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        candidate = value.get(key)
        if candidate is not None:
            return candidate
    return None


def markdown_cell(value: Any) -> str:
    if value is None or value == "":
        return "—"
    text = str(value).replace("\r", " ").replace("\n", " ")
    return text.replace("|", "\\|")


def markdown_table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> List[str]:
    result = [
        "| " + " | ".join(markdown_cell(item) for item in headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    result.extend(
        "| " + " | ".join(markdown_cell(item) for item in row) + " |" for row in rows
    )
    if not rows:
        result.append("| " + " | ".join(["无"] + ["—"] * (len(headers) - 1)) + " |")
    return result


def redact_sensitive(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): "<已脱敏>" if SENSITIVE_KEY_RE.search(str(key)) else redact_sensitive(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    return value


def compact_json(value: Any) -> str:
    return canonical_json(redact_sensitive(value))


def diagnostic_reason(run_id: str, paths: Sequence[Optional[Path]]) -> Optional[str]:
    candidates = [run_id.lower()]
    for path in paths:
        if path is None:
            continue
        candidates.extend(part.lower() for part in path.parts)
    for candidate in candidates:
        if any(candidate.startswith(prefix) for prefix in DIAGNOSTIC_PREFIXES):
            return "运行编号或目录名表明这是 smoke/probe/quota 等诊断运行"
    return None


def normalize_status(value: Any) -> str:
    return str(value or "unknown").strip().lower()


def is_success(value: Any) -> bool:
    return normalize_status(value) in SUCCESS_STATUSES


def safe_status_code(error: Mapping[str, Any]) -> Optional[int]:
    for key in ("status_code", "http_status", "status"):
        value = error.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return None


def classify_error(record: Mapping[str, Any]) -> Tuple[str, Optional[int], str]:
    if record.get("response_truncated") is True:
        return "truncation", None, "response_truncated"
    error = record.get("error") if isinstance(record.get("error"), Mapping) else {}
    status_code = safe_status_code(error)
    error_type = first_string(error, ("type", "code", "error_type")) or "unknown"
    # Messages are considered only for classification and are never copied to
    # the report, preventing an upstream response body from becoming disclosure.
    classifier_text = " ".join(
        str(error.get(key, "")) for key in ("type", "code", "error_type", "message")
    ).lower()
    if status_code in {401, 403} or any(
        token in classifier_text
        for token in ("authentication", "unauthorized", "forbidden", "credential", "api key")
    ):
        return "auth", status_code, error_type
    if status_code == 429 or any(
        token in classifier_text
        for token in ("quota", "rate limit", "resource exhausted", "too many requests")
    ):
        return "quota", status_code, error_type
    if any(
        token in classifier_text
        for token in (
            "transport",
            "connection",
            "remote disconnected",
            "remotedisconnected",
            "urlerror",
            "timeout",
            "timed out",
            "eof",
        )
    ):
        return "transport", status_code, error_type
    if status_code is not None and status_code >= 500 and bool(error.get("retryable")):
        return "transport", status_code, error_type
    return "api_other", status_code, error_type


def returned_models(records: Sequence[Mapping[str, Any]]) -> str:
    counts = Counter(
        str(item.get("returned_model")).strip()
        for item in records
        if isinstance(item.get("returned_model"), str) and item.get("returned_model").strip()
    )
    if not counts:
        return "未返回"
    return "；".join("%s × %d" % (name, count) for name, count in sorted(counts.items()))


def normalized_model_name(value: str) -> str:
    """Normalize only transport naming syntax, not model aliases or versions."""

    result = value.strip().lower()
    if result.startswith("models/"):
        result = result[len("models/") :]
    return result


def selected_question_ids(manifest: Mapping[str, Any]) -> Optional[List[str]]:
    input_info = manifest.get("input") if isinstance(manifest.get("input"), Mapping) else {}
    values = input_info.get("selected_question_ids")
    if isinstance(values, list) and all(isinstance(item, str) and item.strip() for item in values):
        return list(dict.fromkeys(item.strip() for item in values))
    return None


def system_key(provider: str, model: str, configuration_id: str) -> Tuple[str, str, str]:
    return provider, model, configuration_id


def aggregation_system_key(value: Mapping[str, Any]) -> Tuple[str, str, str]:
    tested = value.get("tested_system") if isinstance(value.get("tested_system"), Mapping) else {}
    return (
        str(tested.get("provider") or "unknown"),
        str(tested.get("model") or "unknown"),
        str(tested.get("configuration_id") or "unknown"),
    )


def load_run(spec: RunSpec, full_question_count: int) -> Dict[str, Any]:
    try:
        normalized, manifest_loaded = load_raw_records(
            spec.responses_path,
            manifest_path=spec.manifest_path,
        )
    except (OSError, PipelineError, ValueError) as exc:
        raise ReportError(
            "无法规范化回答记录 %s：%s" % (display_path(spec.responses_path), exc)
        ) from exc
    manifest: Dict[str, Any] = dict(manifest_loaded or {})
    run_id = first_string(manifest, ("run_id", "batch_id"))
    record_run_ids = {item.get("run_id") for item in normalized if item.get("run_id")}
    if not run_id and len(record_run_ids) == 1:
        run_id = str(next(iter(record_run_ids)))
    run_id = run_id or spec.responses_path.parent.name
    expected_ids = selected_question_ids(manifest)

    groups: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = defaultdict(list)
    evaluation_ids = set()
    for item in normalized:
        original = item.get("record") if isinstance(item.get("record"), Mapping) else {}
        combined = dict(original)
        combined["_evaluation_id"] = item["evaluation_id"]
        combined["_configuration_id"] = item["configuration_id"]
        combined["_history_line_numbers"] = item.get("history_line_numbers", [])
        combined["_normalized_status"] = item["status"]
        groups[
            system_key(item["provider"], item["model"], item["configuration_id"])
        ].append(combined)
        evaluation_ids.add(item["evaluation_id"])

    planned_models: List[Tuple[str, str]] = []
    provider_models = manifest.get("provider_models")
    if isinstance(provider_models, Mapping):
        for provider, models in provider_models.items():
            if not isinstance(models, list):
                continue
            for model in models:
                if isinstance(model, str) and model.strip():
                    planned_models.append((str(provider), model.strip()))

    actual_by_pair: Dict[Tuple[str, str], List[Tuple[str, str, str]]] = defaultdict(list)
    for key in groups:
        actual_by_pair[(key[0], key[1])].append(key)
    for provider, model in planned_models:
        if (provider, model) not in actual_by_pair:
            groups[system_key(provider, model, "manifest-only")] = []

    system_rows: List[Dict[str, Any]] = []
    error_rows: List[Dict[str, Any]] = []
    for key in sorted(groups):
        provider, model, configuration_id = key
        records = groups[key]
        observed_by_question: Dict[str, Dict[str, Any]] = {}
        for record in records:
            question_id = str(record.get("question_id") or "unknown")
            observed_by_question[question_id] = record
        successful = {
            question_id: record
            for question_id, record in observed_by_question.items()
            if is_success(record.get("_normalized_status"))
        }
        failed = {
            question_id: record
            for question_id, record in observed_by_question.items()
            if not is_success(record.get("_normalized_status"))
        }
        expected_count = len(expected_ids) if expected_ids is not None else None
        missing_ids = (
            sorted(set(expected_ids) - set(observed_by_question)) if expected_ids is not None else []
        )
        coverage = (
            len(successful) / expected_count
            if isinstance(expected_count, int) and expected_count > 0
            else None
        )
        full_24 = (
            expected_count == full_question_count
            and len(successful) == full_question_count
            and not failed
            and not missing_ids
        )
        if full_24:
            coverage_label = "完整%d题" % full_question_count
        elif expected_count is None:
            coverage_label = "分母未知"
        elif len(successful) == expected_count and expected_count != full_question_count:
            coverage_label = "批次完整（%d题，非%d题）" % (expected_count, full_question_count)
        else:
            coverage_label = "部分（%d/%d）" % (len(successful), expected_count)

        returned = returned_models(list(successful.values()))
        returned_names = {
            str(item.get("returned_model")).strip()
            for item in successful.values()
            if isinstance(item.get("returned_model"), str) and item.get("returned_model").strip()
        }
        returned_mismatch = bool(returned_names) and {
            normalized_model_name(name) for name in returned_names
        } != {normalized_model_name(model)}
        system_rows.append(
            {
                "key": key,
                "run_id": run_id,
                "provider": provider,
                "model": model,
                "configuration_id": configuration_id,
                "returned_models": returned,
                "returned_model_names": sorted(returned_names),
                "returned_mismatch": returned_mismatch,
                "planned": expected_count,
                "observed": len(observed_by_question),
                "success": len(successful),
                "error": len(failed),
                "missing": len(missing_ids),
                "coverage": coverage,
                "coverage_label": coverage_label,
                "full_24": full_24,
                "question_ids": sorted(observed_by_question),
                "evaluation_ids": sorted(
                    str(item.get("_evaluation_id")) for item in records if item.get("_evaluation_id")
                ),
            }
        )
        for question_id, record in sorted(failed.items()):
            category, status_code, error_type = classify_error(record)
            error_rows.append(
                {
                    "run_id": run_id,
                    "provider": provider,
                    "model": model,
                    "question_id": question_id,
                    "category": category,
                    "status_code": status_code,
                    "error_type": error_type,
                    "attempt_count": record.get("attempt_count"),
                }
            )
        for question_id in missing_ids:
            error_rows.append(
                {
                    "run_id": run_id,
                    "provider": provider,
                    "model": model,
                    "question_id": question_id,
                    "category": "missing",
                    "status_code": None,
                    "error_type": "not_recorded",
                    "attempt_count": None,
                }
            )

    diagnostic = diagnostic_reason(run_id, (spec.responses_path, spec.manifest_path))
    manifest_status = first_string(manifest, ("run_status",)) or "unknown"
    manifest_unique = manifest.get("unique_record_count")
    manifest_success = manifest.get("success_count")
    audit_warnings: List[str] = []
    if isinstance(manifest_unique, int) and manifest_unique != len(normalized):
        audit_warnings.append(
            "manifest unique_record_count=%d，但从 records 重算为 %d"
            % (manifest_unique, len(normalized))
        )
    observed_success = sum(1 for item in normalized if is_success(item.get("status")))
    if isinstance(manifest_success, int) and manifest_success != observed_success:
        audit_warnings.append(
            "manifest success_count=%d，但从 records 重算为 %d"
            % (manifest_success, observed_success)
        )
    return {
        "spec": spec,
        "run_id": run_id,
        "manifest": manifest,
        "manifest_status": manifest_status,
        "responses_sha256": sha256_file(spec.responses_path),
        "manifest_sha256": sha256_file(spec.manifest_path) if spec.manifest_path else None,
        "raw_records": normalized,
        "evaluation_ids": evaluation_ids,
        "systems": system_rows,
        "errors": error_rows,
        "diagnostic_reason": diagnostic,
        "audit_warnings": audit_warnings,
        "aggregation": None,
        "aggregation_path": None,
        "score_paths": [],
    }


def match_aggregations(
    runs: Sequence[Dict[str, Any]], aggregation_paths: Sequence[Path]
) -> List[str]:
    warnings: List[str] = []
    for path in aggregation_paths:
        aggregation = read_json_object(path)
        source = aggregation.get("source") if isinstance(aggregation.get("source"), Mapping) else {}
        response_hash = first_string(source, ("responses_sha256",))
        source_run_id = first_string(source, ("run_id",))
        matches = [
            run
            for run in runs
            if (response_hash and run["responses_sha256"] == response_hash)
            or (not response_hash and source_run_id and run["run_id"] == source_run_id)
        ]
        if len(matches) != 1:
            raise ReportError(
                "汇总 %s 无法唯一匹配 response run（匹配数=%d）"
                % (display_path(path), len(matches))
            )
        run = matches[0]
        if response_hash and response_hash != run["responses_sha256"]:
            raise ReportError("汇总响应哈希与当前 records 不一致：%s" % display_path(path))
        if not response_hash:
            warnings.append(
                "%s 未提供 source.responses_sha256，仅按 run_id 匹配；发布前应重新生成带回答哈希的 aggregation。"
                % display_path(path)
            )
        manifest_hash = first_string(source, ("manifest_sha256",))
        if manifest_hash and run["manifest_sha256"] and manifest_hash != run["manifest_sha256"]:
            raise ReportError("汇总 manifest 哈希与当前文件不一致：%s" % display_path(path))
        if run["aggregation"] is not None:
            raise ReportError("同一 run 提供了多份 aggregation：%s" % run["run_id"])
        run_system_keys = {item["key"] for item in run["systems"]}
        aggregate_systems = (
            aggregation.get("tested_systems")
            if isinstance(aggregation.get("tested_systems"), list)
            else []
        )
        aggregate_system_keys = {
            aggregation_system_key(item)
            for item in aggregate_systems
            if isinstance(item, Mapping)
        }
        unexpected_systems = aggregate_system_keys - run_system_keys
        if unexpected_systems:
            raise ReportError(
                "汇总 %s 含有当前 records 中不存在的被测系统：%s"
                % (display_path(path), sorted(unexpected_systems))
            )
        aggregate_answers = (
            aggregation.get("answers") if isinstance(aggregation.get("answers"), list) else []
        )
        unexpected_evaluations = {
            str(item.get("evaluation_id"))
            for item in aggregate_answers
            if isinstance(item, Mapping)
            and item.get("evaluation_id")
            and item.get("evaluation_id") not in run["evaluation_ids"]
        }
        if unexpected_evaluations:
            raise ReportError(
                "汇总 %s 含有当前 records 中不存在的 evaluation_id：%s"
                % (display_path(path), sorted(unexpected_evaluations))
            )
        run["aggregation"] = aggregation
        run["aggregation_path"] = path
    return warnings


def match_score_files(runs: Sequence[Dict[str, Any]], score_paths: Sequence[Path]) -> List[str]:
    warnings: List[str] = []
    for path in score_paths:
        records = read_jsonl_objects(path)
        run_ids = {
            str(item.get("run_id")).strip()
            for item in records
            if isinstance(item.get("run_id"), str) and item.get("run_id").strip()
        }
        evaluation_ids = {
            str(item.get("evaluation_id") or item.get("record_key")).strip()
            for item in records
            if item.get("evaluation_id") or item.get("record_key")
        }
        matches: List[Dict[str, Any]] = []
        if len(run_ids) == 1:
            only_run_id = next(iter(run_ids))
            matches = [run for run in runs if run["run_id"] == only_run_id]
        elif not run_ids and evaluation_ids:
            matches = [run for run in runs if evaluation_ids <= run["evaluation_ids"]]
        elif len(run_ids) > 1:
            raise ReportError(
                "评分文件 %s 混有多个 run_id；请拆分或先生成 aggregation"
                % display_path(path)
            )
        if len(matches) != 1:
            raise ReportError(
                "评分文件 %s 无法唯一匹配 response run（匹配数=%d）"
                % (display_path(path), len(matches))
            )
        matches[0]["score_paths"].append(path)

    for run in runs:
        if run["aggregation"] is not None and run["score_paths"]:
            warnings.append(
                "%s 同时提供 aggregation 与 score JSONL；报告采用已校验 aggregation，score 文件只列入来源清单且不重复计权。"
                % run["run_id"]
            )
            continue
        if not run["score_paths"]:
            continue
        try:
            run["aggregation"] = aggregate_evaluations(
                responses_path=run["spec"].responses_path,
                score_paths=run["score_paths"],
                manifest_path=run["spec"].manifest_path,
            )
        except (PipelineError, OSError, ValueError) as exc:
            raise ReportError("评分汇总失败 %s：%s" % (run["run_id"], exc)) from exc
        run["aggregation_path"] = None
    return warnings


def aggregate_system_map(aggregation: Optional[Mapping[str, Any]]) -> Dict[Tuple[str, str, str], Mapping[str, Any]]:
    if not isinstance(aggregation, Mapping):
        return {}
    systems = aggregation.get("tested_systems")
    if not isinstance(systems, list):
        return {}
    return {
        aggregation_system_key(item): item
        for item in systems
        if isinstance(item, Mapping)
    }


def scorer_median(system: Mapping[str, Any], scorer_type: str) -> Tuple[Any, int, int]:
    total = system.get("total") if isinstance(system.get("total"), Mapping) else {}
    by_type = total.get("by_scorer_type") if isinstance(total.get("by_scorer_type"), Mapping) else {}
    summary = by_type.get(scorer_type) if isinstance(by_type.get(scorer_type), Mapping) else {}
    answer_medians = (
        summary.get("answer_equal_weight_medians")
        if isinstance(summary.get("answer_equal_weight_medians"), Mapping)
        else {}
    )
    raw = (
        summary.get("raw_judge_assessments")
        if isinstance(summary.get("raw_judge_assessments"), Mapping)
        else {}
    )
    return (
        answer_medians.get("median"),
        int(answer_medians.get("answer_count") or 0),
        int(raw.get("assessment_count") or 0),
    )


def format_score(value: Any, answer_count: int) -> str:
    if value is None:
        return "—"
    rendered = ("%.1f" % value).rstrip("0").rstrip(".") if isinstance(value, float) else str(value)
    return "%s/20（%d答）" % (rendered, answer_count)


def dimension_score_cells(system: Mapping[str, Any], scorer_type: str) -> List[str]:
    dimensions = system.get("dimensions") if isinstance(system.get("dimensions"), Mapping) else {}
    result: List[str] = []
    for dimension_id, _ in DIMENSIONS:
        dimension = dimensions.get(dimension_id) if isinstance(dimensions.get(dimension_id), Mapping) else {}
        by_type = dimension.get("by_scorer_type") if isinstance(dimension.get("by_scorer_type"), Mapping) else {}
        type_summary = by_type.get(scorer_type) if isinstance(by_type.get(scorer_type), Mapping) else {}
        medians = (
            type_summary.get("answer_equal_weight_medians")
            if isinstance(type_summary.get("answer_equal_weight_medians"), Mapping)
            else {}
        )
        value = medians.get("median")
        result.append("—" if value is None else str(value))
    return result


def score_registers(run: Mapping[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    aggregation = run.get("aggregation")
    result: Dict[str, List[Dict[str, Any]]] = {
        "risk": [],
        "invalid": [],
        "disagreement": [],
    }
    if not isinstance(aggregation, Mapping):
        return result
    risk_top = (
        aggregation.get("major_risk_register")
        if isinstance(aggregation.get("major_risk_register"), Mapping)
        else {}
    )
    entries = risk_top.get("entries") if isinstance(risk_top.get("entries"), list) else []
    for item in entries:
        if isinstance(item, Mapping):
            result["risk"].append({"run_id": run["run_id"], **dict(item)})
    answers = aggregation.get("answers") if isinstance(aggregation.get("answers"), list) else []
    for answer in answers:
        if not isinstance(answer, Mapping):
            continue
        counts = (
            answer.get("assessment_counts")
            if isinstance(answer.get("assessment_counts"), Mapping)
            else {}
        )
        invalid_count = int(counts.get("invalid") or 0)
        judge_error_count = int(counts.get("judge_error") or 0)
        if invalid_count or judge_error_count:
            validation_errors: List[str] = []
            assessments = answer.get("assessments") if isinstance(answer.get("assessments"), list) else []
            for assessment in assessments:
                if not isinstance(assessment, Mapping) or assessment.get("score_status") == "valid":
                    continue
                errors = assessment.get("validation_errors")
                if isinstance(errors, list):
                    validation_errors.extend(str(item) for item in errors if str(item).strip())
            result["invalid"].append(
                {
                    "run_id": run["run_id"],
                    "evaluation_id": answer.get("evaluation_id"),
                    "question_id": answer.get("question_id"),
                    "tested_system": answer.get("tested_system"),
                    "invalid_count": invalid_count,
                    "judge_error_count": judge_error_count,
                    "validation_errors": sorted(set(validation_errors)),
                }
            )
    disagreements = (
        aggregation.get("disagreement_register")
        if isinstance(aggregation.get("disagreement_register"), Mapping)
        else {}
    )
    disagreement_entries = (
        disagreements.get("entries") if isinstance(disagreements.get("entries"), list) else []
    )
    for item in disagreement_entries:
        if isinstance(item, Mapping):
            result["disagreement"].append({"run_id": run["run_id"], **dict(item)})
    return result


def risk_summary_text(item: Mapping[str, Any]) -> str:
    labels = item.get("labels") if isinstance(item.get("labels"), list) else []
    rubric_items = item.get("rubric_items") if isinstance(item.get("rubric_items"), list) else []
    parts = []
    if rubric_items:
        parts.append("规则项 " + ",".join(str(value) for value in rubric_items))
    if labels:
        parts.append("标签 " + ",".join(str(value) for value in labels))
    return "；".join(parts) or "未提供结构化标签"


def trigger_summary(item: Mapping[str, Any]) -> str:
    triggers = item.get("triggers") if isinstance(item.get("triggers"), list) else []
    values = []
    for trigger in triggers:
        if not isinstance(trigger, Mapping):
            continue
        trigger_type = str(trigger.get("type") or "unknown")
        dimension = trigger.get("dimension")
        values.append("%s%s" % (trigger_type, "@" + str(dimension) if dimension else ""))
    return "；".join(values) or "未提供触发项"


def source_inventory_rows(runs: Sequence[Mapping[str, Any]]) -> List[List[Any]]:
    rows: List[List[Any]] = []
    for run in runs:
        manifest = run["manifest"]
        prompt = manifest.get("prompt") if isinstance(manifest.get("prompt"), Mapping) else {}
        params = (
            manifest.get("generation_parameters")
            if isinstance(manifest.get("generation_parameters"), Mapping)
            else {}
        )
        input_info = manifest.get("input") if isinstance(manifest.get("input"), Mapping) else {}
        score_sources = [
            "%s @ %s" % (display_path(path), sha256_file(path)) for path in run["score_paths"]
        ]
        if run.get("aggregation_path"):
            path = run["aggregation_path"]
            score_sources.append("%s @ %s" % (display_path(path), sha256_file(path)))
        rows.append(
            [
                run["run_id"],
                display_path(run["spec"].responses_path),
                run["responses_sha256"],
                display_path(run["spec"].manifest_path),
                run["manifest_sha256"] or "—",
                input_info.get("sha256") or "—",
                sha256_text(canonical_json(prompt)),
                compact_json(params),
                "；".join(score_sources) if score_sources else "未提供",
            ]
        )
    return rows


def build_report(
    run_specs: Sequence[RunSpec],
    *,
    score_paths: Sequence[Path] = (),
    aggregation_paths: Sequence[Path] = (),
    title: str = DEFAULT_TITLE,
    full_question_count: int = 24,
    include_diagnostics: bool = False,
) -> str:
    if not run_specs:
        raise ReportError("至少需要一个 response run")
    if full_question_count < 1:
        raise ReportError("full_question_count 必须大于 0")
    seen_response_paths = set()
    runs: List[Dict[str, Any]] = []
    for spec in run_specs:
        resolved = spec.responses_path.resolve()
        if resolved in seen_response_paths:
            raise ReportError("重复的 responses 输入：%s" % display_path(spec.responses_path))
        seen_response_paths.add(resolved)
        runs.append(load_run(spec, full_question_count))

    warnings = []
    warnings.extend(match_aggregations(runs, aggregation_paths))
    warnings.extend(match_score_files(runs, score_paths))
    included = [run for run in runs if include_diagnostics or not run["diagnostic_reason"]]
    excluded = [run for run in runs if run not in included]
    if not included:
        raise ReportError("所有输入都被识别为诊断运行；如确需纳入，请使用 --include-diagnostics")

    all_systems: List[Dict[str, Any]] = []
    all_errors: List[Dict[str, Any]] = []
    risk_register: List[Dict[str, Any]] = []
    invalid_register: List[Dict[str, Any]] = []
    disagreement_register: List[Dict[str, Any]] = []
    for run in included:
        aggregate_map = aggregate_system_map(run.get("aggregation"))
        for system in run["systems"]:
            aggregate_system = aggregate_map.get(system["key"])
            item = dict(system)
            item["run_status"] = run["manifest_status"]
            item["diagnostic"] = bool(run["diagnostic_reason"])
            item["aggregate_system"] = aggregate_system
            item["formal_ready"] = bool(
                aggregate_system
                and isinstance(aggregate_system.get("formal_readiness"), Mapping)
                and aggregate_system["formal_readiness"].get("ready_for_formal_comparison") is True
            )
            item["generation_comparable"] = bool(
                item["full_24"]
                and item["run_status"] == "completed"
                and not item["diagnostic"]
            )
            all_systems.append(item)
        all_errors.extend(run["errors"])
        registers = score_registers(run)
        risk_register.extend(registers["risk"])
        invalid_register.extend(registers["invalid"])
        disagreement_register.extend(registers["disagreement"])

    all_systems.sort(key=lambda item: (item["provider"], item["model"], item["configuration_id"], item["run_id"]))
    all_errors.sort(key=lambda item: (item["provider"], item["model"], item["question_id"], item["run_id"]))
    returned_to_requested: Dict[Tuple[str, str], set[str]] = defaultdict(set)
    for item in all_systems:
        for returned_name in item.get("returned_model_names", []):
            returned_to_requested[
                (item["provider"], normalized_model_name(returned_name))
            ].add(item["model"])
    alias_collapse_groups = [
        {
            "provider": provider,
            "returned_model": returned_model,
            "requested_models": sorted(requested_models),
        }
        for (provider, returned_model), requested_models in sorted(returned_to_requested.items())
        if len({normalized_model_name(name) for name in requested_models}) > 1
    ]
    full_systems = [item for item in all_systems if item["generation_comparable"]]
    partial_systems = [item for item in all_systems if not item["generation_comparable"]]
    formal_systems = [item for item in all_systems if item["generation_comparable"] and item["formal_ready"]]
    error_counts = Counter(item["category"] for item in all_errors)

    valid_ai = 0
    valid_human = 0
    for run in included:
        aggregation = run.get("aggregation")
        counts = aggregation.get("counts") if isinstance(aggregation, Mapping) and isinstance(aggregation.get("counts"), Mapping) else {}
        valid_ai += int(counts.get("valid_ai_preliminary_scores") or 0)
        valid_human += int(counts.get("valid_human_scores") or 0)

    snapshot_values = []
    for run in included:
        for key in ("completed_at", "created_at"):
            value = run["manifest"].get(key)
            if isinstance(value, str) and value.strip():
                snapshot_values.append(value.strip())
                break
    snapshot_at = max(snapshot_values) if snapshot_values else "未由输入提供"

    lines: List[str] = [
        "<!-- model-evaluation-report-schema: %s -->" % REPORT_SCHEMA_VERSION,
        "# %s" % title.strip(),
        "",
        "> 输入快照时间（来自 run manifest）：%s。报告为本地待审材料，未经项目负责人确认不得作为正式发布结论。"
        % snapshot_at,
        "",
        "## 技术摘要：当前可确认的是覆盖状态，不是正式模型排名",
        "",
    ]
    if formal_systems:
        lines.append(
            "纳入的 %d 个被测系统中，%d 个完成全部 %d 题且运行正常结束；其中 %d 个通过现有汇总文件的形式门槛。即便如此，公开结论仍需项目负责人确认。"
            % (len(all_systems), len(full_systems), full_question_count, len(formal_systems))
        )
    else:
        lines.append(
            "纳入的 %d 个被测系统中，%d 个完成全部 %d 题且运行正常结束，但 **没有任何系统可据此认定为正式比较完成**。当前机器评分只能作为 AI judge 初评；它不等于至少两名独立人工评分者，也不能自动确认或排除重大风险。"
            % (len(all_systems), len(full_systems), full_question_count)
        )
    lines.extend(
        [
            "",
            "覆盖方面：%d 个系统可做同配置、同题量的生成结果核对，%d 个系统因题目缺失、调用错误、运行未完成或诊断属性只能单独查看。调用层共登记 %d 条失败/缺失，其中鉴权 %d、配额/限流 %d、传输 %d、输出截断 %d、其他 API %d、计划记录缺失 %d。"
            % (
                len(full_systems),
                len(partial_systems),
                len(all_errors),
                error_counts["auth"],
                error_counts["quota"],
                error_counts["transport"],
                error_counts["truncation"],
                error_counts["api_other"],
                error_counts["missing"],
            ),
            "",
            "评分方面：输入中有 %d 条有效 AI 初评、%d 条有效人工评分；另有 %d 个回答包含无效评分或 judge 错误，%d 个回答触发评分分歧，重大风险登记共 %d 条。上述异常均在后文单列，**不会被中位数掩盖或由其他 judge 的未标记意见抵消**。"
            % (
                valid_ai,
                valid_human,
                len(invalid_register),
                len(disagreement_register),
                len(risk_register),
            ),
        ]
    )
    if excluded:
        lines.extend(
            [
                "",
                "默认排除了 %d 个 smoke/probe/quota 等诊断运行；它们不进入上述分母、分数或比较。"
                % len(excluded),
            ]
        )

    lines.extend(
        [
            "",
            "## 覆盖率决定哪些模型可以横向核对",
            "",
            "“完整%d题”要求计划分母为 %d、每题均有成功的最新记录、没有错误或缺失，并且 run manifest 状态为 `completed`。部分模型的分数即使存在，也不与完整模型横向比较。"
            % (full_question_count, full_question_count),
            "",
        ]
    )
    coverage_rows = []
    for item in all_systems:
        coverage_text = "—" if item["coverage"] is None else "%.1f%%" % (item["coverage"] * 100)
        comparison = "可做完整生成核对" if item["generation_comparable"] else "仅单独查看"
        coverage_rows.append(
            [
                item["provider"],
                item["model"],
                item["returned_models"] + ("（与请求名不同）" if item["returned_mismatch"] else ""),
                item["run_id"],
                item["run_status"],
                item["coverage_label"],
                coverage_text,
                item["error"],
                item["missing"],
                comparison,
            ]
        )
    lines.extend(
        markdown_table(
            [
                "提供方",
                "请求模型",
                "returned model",
                "run",
                "run 状态",
                "题目覆盖",
                "成功率",
                "错误",
                "缺失",
                "比较范围",
            ],
            coverage_rows,
        )
    )

    alias_mappings = [item for item in all_systems if item["returned_mismatch"]]
    if alias_mappings or alias_collapse_groups:
        lines.extend(
            [
                "",
                "### 请求名与返回版本存在 alias/version 映射",
                "",
                "请求配置仍逐项保留，但 returned model 不同意味着供应商可能进行了别名解析、版本固定或路由。尤其当多个请求名折叠到同一 returned model 时，它们不能作为彼此独立的底层模型参加排名。",
                "",
            ]
        )
        lines.extend(
            markdown_table(
                ["提供方", "请求模型", "returned model", "run", "报告处理"],
                [
                    [
                        item["provider"], item["model"], item["returned_models"], item["run_id"],
                        "保留请求配置；标记 alias/version，不视为独立底层模型",
                    ]
                    for item in alias_mappings
                ],
            )
        )
        if alias_collapse_groups:
            lines.extend(
                [
                    "",
                    "多个请求配置折叠到同一 returned model：",
                    "",
                ]
            )
            lines.extend(
                markdown_table(
                    ["提供方", "returned model（规范化）", "折叠的请求模型", "比较限制"],
                    [
                        [
                            group["provider"], group["returned_model"],
                            "；".join(group["requested_models"]),
                            "不得当作多个独立底层模型排名",
                        ]
                        for group in alias_collapse_groups
                    ],
                )
            )

    lines.extend(
        [
            "",
            "## 调用失败与缺失必须先解决，不能换算成低分",
            "",
            "API 失败不代表模型在题目上的表现差；它只说明本次调用没有得到可评分回答。下表只显示已脱敏的错误类型与 HTTP 状态，不复制供应商响应消息。",
            "",
        ]
    )
    error_rows = [
        [
            item["provider"],
            item["model"],
            item["question_id"],
            ERROR_CATEGORY_LABELS[item["category"]],
            item["status_code"] or "—",
            item["error_type"],
            item["attempt_count"] or "—",
            item["run_id"],
        ]
        for item in all_errors
    ]
    lines.extend(
        markdown_table(
            ["提供方", "请求模型", "题号", "失败类别", "HTTP", "错误类型", "尝试数", "run"],
            error_rows,
        )
    )

    lines.extend(
        [
            "",
            "## AI judge 只提供初评线索，人工评分与其分开",
            "",
            "下列中位数先在每个回答内按评分者类型计算，再对回答等权汇总；不计算平均分，也不按分数排序。AI 与人工分别展示。部分覆盖系统即使有中位数也标为不可横向比较。",
            "",
        ]
    )
    scoring_rows = []
    dimension_rows = []
    for item in all_systems:
        aggregate_system = item.get("aggregate_system")
        if isinstance(aggregate_system, Mapping):
            ai_median, ai_answers, _ = scorer_median(aggregate_system, "ai")
            human_median, human_answers, _ = scorer_median(aggregate_system, "human")
            answer_counts = (
                aggregate_system.get("answer_counts")
                if isinstance(aggregate_system.get("answer_counts"), Mapping)
                else {}
            )
            invalid_count = 0
            run = next(run for run in included if run["run_id"] == item["run_id"])
            aggregation_answers = (
                run["aggregation"].get("answers")
                if isinstance(run.get("aggregation"), Mapping)
                and isinstance(run["aggregation"].get("answers"), list)
                else []
            )
            item_eval_ids = set(item["evaluation_ids"])
            for answer in aggregation_answers:
                if not isinstance(answer, Mapping) or answer.get("evaluation_id") not in item_eval_ids:
                    continue
                counts = answer.get("assessment_counts") if isinstance(answer.get("assessment_counts"), Mapping) else {}
                invalid_count += int(counts.get("invalid") or 0) + int(counts.get("judge_error") or 0)
            risk = aggregate_system.get("major_risk") if isinstance(aggregate_system.get("major_risk"), Mapping) else {}
            risk_ids = set(risk.get("flagged_evaluation_ids") or []) | set(risk.get("uncertain_evaluation_ids") or [])
            disagreements = aggregate_system.get("disagreements") if isinstance(aggregate_system.get("disagreements"), Mapping) else {}
            if item["formal_ready"] and item["generation_comparable"]:
                status = "形式门槛满足，仍待负责人确认"
            elif human_answers:
                status = "人工评分未达正式门槛"
            elif ai_answers:
                status = "仅 AI 初评"
            else:
                status = "未评分"
            if not item["generation_comparable"]:
                status += "；部分覆盖不横比"
            scoring_rows.append(
                [
                    item["provider"],
                    item["model"],
                    item["coverage_label"],
                    format_score(ai_median, ai_answers),
                    format_score(human_median, human_answers),
                    answer_counts.get("with_two_independent_human_scores") or 0,
                    invalid_count,
                    len(risk_ids),
                    disagreements.get("answer_count") or 0,
                    status,
                ]
            )
            dimension_rows.append(
                [item["provider"], item["model"], "AI 初评"]
                + dimension_score_cells(aggregate_system, "ai")
            )
            dimension_rows.append(
                [item["provider"], item["model"], "人工"]
                + dimension_score_cells(aggregate_system, "human")
            )
        else:
            scoring_rows.append(
                [
                    item["provider"], item["model"], item["coverage_label"], "—", "—", 0, 0, 0, 0,
                    "未提供评分或汇总" + ("；部分覆盖不横比" if not item["generation_comparable"] else ""),
                ]
            )
    lines.extend(
        markdown_table(
            [
                "提供方", "请求模型", "题目覆盖", "AI 回答等权中位数", "人工回答等权中位数",
                "双人工覆盖答数", "无效/judge 错误", "风险答数", "分歧答数", "评分状态",
            ],
            scoring_rows,
        )
    )
    lines.extend(
        [
            "",
            "五维中位数同样按评分者类型分开，取值为 0—4；`—` 表示没有可报告的该类评分。",
            "",
        ]
    )
    lines.extend(
        markdown_table(
            ["提供方", "请求模型", "评分者类型"] + [name for _, name in DIMENSIONS],
            dimension_rows,
        )
    )

    lines.extend(
        [
            "",
            "## 风险、无效评分与分歧逐项保留",
            "",
            "任何 `flagged` 或 `uncertain` 都只是待独立人工复核的信号，不是自动裁决；它不会因其他评分较高而消失。",
            "",
            "### 重大风险信号",
            "",
        ]
    )
    risk_rows = []
    for item in risk_register:
        tested = item.get("tested_system") if isinstance(item.get("tested_system"), Mapping) else {}
        judge = item.get("judge") if isinstance(item.get("judge"), Mapping) else {}
        risk_rows.append(
            [
                item.get("run_id"), tested.get("provider"), tested.get("model"), item.get("question_id"),
                item.get("risk_status"), risk_summary_text(item), judge.get("scorer_type"),
                judge.get("judge_id"), item.get("score_status"), "待独立人工复核",
            ]
        )
    lines.extend(
        markdown_table(
            ["run", "提供方", "请求模型", "题号", "风险状态", "规则/标签", "评分者类型", "judge", "评分有效性", "处理状态"],
            risk_rows,
        )
    )
    lines.extend(["", "### 无效评分与 judge 调用错误", ""])
    invalid_rows = []
    for item in invalid_register:
        tested = item.get("tested_system") if isinstance(item.get("tested_system"), Mapping) else {}
        invalid_rows.append(
            [
                item.get("run_id"), tested.get("provider"), tested.get("model"), item.get("question_id"),
                item.get("invalid_count"), item.get("judge_error_count"),
                "；".join(item.get("validation_errors") or []) or "见原评分记录",
                "不进入中位数；风险线索仍保留",
            ]
        )
    lines.extend(
        markdown_table(
            ["run", "提供方", "请求模型", "题号", "invalid", "judge error", "校验原因", "计分处理"],
            invalid_rows,
        )
    )
    lines.extend(["", "### 评分分歧", ""])
    disagreement_rows = []
    for item in disagreement_register:
        tested = item.get("tested_system") if isinstance(item.get("tested_system"), Mapping) else {}
        disagreement_rows.append(
            [
                item.get("run_id"), tested.get("provider"), tested.get("model"), item.get("question_id"),
                trigger_summary(item), "保留原分并人工复核",
            ]
        )
    lines.extend(
        markdown_table(
            ["run", "提供方", "请求模型", "题号", "分歧触发项", "处理状态"],
            disagreement_rows,
        )
    )

    lines.extend(
        [
            "",
            "## 范围、分母和指标定义",
            "",
            "- **被测系统**：`provider + 请求模型 + configuration_id` 的唯一组合；不同配置不合并。",
            "- **计划分母**：run manifest 的 `input.selected_question_ids` 去重后题数；没有该字段时，覆盖率显示为分母未知。",
            "- **最新回答**：同一 `record_key` 只取 JSONL 最后一条作为当前状态，历史行仍由原文件保留。",
            "- **成功覆盖率**：成功题号数 ÷ 计划题号数。API 错误与未写入记录分别计作错误和缺失，不计为 0 分。",
            "- **完整%d题**：计划题数和成功题数均为 %d，且没有错误、缺失或非 `completed` 运行状态。" % (full_question_count, full_question_count),
            "- **回答等权中位数**：先在单个回答内按评分者类型取中位数，再跨回答取中位数，避免 judge 较多的回答被重复加权。N/A 与空白都不是 0。",
            "- **正式比较**：必须满足运行覆盖、题目状态、至少两名独立人工评分、重大风险和分歧复核等现有汇总门槛；AI judge 不计入人工人数。",
            "",
            "## 方法与可复现性：计数由 records 重算，参数和哈希冻结",
            "",
            "报告完全离线生成，不读取密钥、不发起网络请求。manifest 中的计数只用于交叉核验；主要覆盖统计由回答 JSONL 重算。提示正文不在报告中展开，只记录提示对象 SHA-256。敏感参数键会被脱敏。",
            "",
        ]
    )
    lines.extend(
        markdown_table(
            ["run", "records", "records SHA-256", "manifest", "manifest SHA-256", "题库 SHA-256", "prompt SHA-256", "生成参数（脱敏）", "评分/汇总来源及 SHA-256"],
            source_inventory_rows(included),
        )
    )

    audit_warnings = [
        "%s：%s" % (run["run_id"], warning)
        for run in included
        for warning in run["audit_warnings"]
    ] + warnings
    returned_mismatches = [item for item in all_systems if item["returned_mismatch"]]
    lines.extend(
        [
            "",
            "## 限制与稳健性检查：部分覆盖和供应商返回版本会改变解释",
            "",
            "- 当前报告是 pilot/待审材料，不生成排行榜，也不把描述性中位数写成因果或能力定论。",
            "- `returned_model` 由供应商响应提供；若与请求名不同，表中同时保留两者，不能自行假定它们等价。当前有 %d 个系统出现名称差异。" % len(returned_mismatches),
            "- 当前有 %d 组多个请求配置折叠到同一 returned model；这些请求记录仍分别保留，但不得作为多个独立底层模型排名。" % len(alias_collapse_groups),
            "- 有 %d 个系统未满足完整 %d 题且正常结束的生成核对条件；其分数只反映已成功的子集。" % (len(partial_systems), full_question_count),
            "- 供应商侧模型修订、隐藏系统设置、区域配额及不可见路由无法仅靠本地结果还原。",
            "- 没有 aggregation 或 score 输入的 run 只能报告覆盖与调用错误，不能推断质量。",
        ]
    )
    if audit_warnings:
        lines.extend(["", "输入一致性警告：", ""])
        lines.extend("- %s" % warning for warning in audit_warnings)

    lines.extend(
        [
            "",
            "## 下一步：先补齐调用与复核，再考虑公开比较",
            "",
            "1. 对鉴权、配额和传输失败分别处理；恢复运行时保持原模型、参数、题库和提示哈希一致，不覆盖历史错误。",
            "2. 只在完整%d题、运行正常结束的同配置系统之间做 AI 初评层面的对照；部分覆盖模型单列。" % full_question_count,
            "3. 逐项处理 invalid、judge error、`flagged`、`uncertain` 和分歧；重试结果新增记录，不删除原始异常。",
            "4. 若要进入正式比较，为每个成功回答补足至少两名不同编号的独立人工评分者，并按规程复核风险与分歧。",
            "5. 由项目负责人检查身份隐私、题目版本、参数、来源、结果与公开范围后，再决定是否上传。",
            "",
            "## 待确认问题",
            "",
            "- 部分覆盖模型是等待配额恢复后续跑，还是作为本轮不可比较样本保留？",
            "- 请求模型名与 returned model 不一致时，供应商的版本/别名说明是否足以支持合并展示？",
            "- 哪些风险和分歧需要法律、事实或安全方面的专门人工复核？",
            "- 正式公开前的两名独立人工评分者、争议处理人与最终确认人如何留痕？",
        ]
    )

    if excluded:
        lines.extend(
            [
                "",
                "## 默认排除的诊断运行",
                "",
                "这些运行只用于连通性、配额、鉴权或传输诊断，未进入任何正式比较分母或分数。",
                "",
            ]
        )
        lines.extend(
            markdown_table(
                ["run", "records", "run 状态", "排除原因"],
                [
                    [
                        run["run_id"], display_path(run["spec"].responses_path),
                        run["manifest_status"], run["diagnostic_reason"],
                    ]
                    for run in excluded
                ],
            )
        )
        diagnostic_detail_rows: List[List[Any]] = []
        for run in excluded:
            errors_by_system: Dict[Tuple[str, str], Counter[str]] = defaultdict(Counter)
            for error in run["errors"]:
                errors_by_system[(error["provider"], error["model"])][error["category"]] += 1
            for system in run["systems"]:
                categories = errors_by_system[(system["provider"], system["model"])]
                category_text = "；".join(
                    "%s × %d" % (ERROR_CATEGORY_LABELS[category], count)
                    for category, count in sorted(categories.items())
                ) or "无"
                diagnostic_detail_rows.append(
                    [
                        run["run_id"], system["provider"], system["model"],
                        system["returned_models"], system["success"], system["error"],
                        system["missing"], category_text, "仅诊断，不计分/不横比",
                    ]
                )
        lines.extend(
            [
                "",
                "诊断明细保留鉴权、配额和传输证据，但不回填主比较表：",
                "",
            ]
        )
        lines.extend(
            markdown_table(
                [
                    "run", "提供方", "请求模型", "returned model", "成功", "错误", "缺失",
                    "诊断类别", "处理",
                ],
                diagnostic_detail_rows,
            )
        )
    return "\n".join(lines).rstrip() + "\n"


def resolve_run_specs(args: argparse.Namespace) -> List[RunSpec]:
    specs: List[RunSpec] = []
    for value in args.run_dir or []:
        directory = Path(value)
        responses = directory / "records.jsonl"
        manifest = directory / "run-manifest.json"
        if not manifest.is_file():
            raise ReportError("run-dir 缺少 run-manifest.json：%s" % display_path(directory))
        specs.append(RunSpec(responses, manifest))

    response_values = list(args.responses or [])
    manifest_values = list(args.manifest or [])
    if manifest_values and len(manifest_values) != len(response_values):
        raise ReportError("--manifest 数量必须与 --responses 数量相同")
    for index, response_value in enumerate(response_values):
        responses = Path(response_value)
        if manifest_values:
            manifest: Optional[Path] = Path(manifest_values[index])
        else:
            sibling = responses.parent / "run-manifest.json"
            manifest = sibling if sibling.is_file() else None
        specs.append(RunSpec(responses, manifest))
    return specs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="离线生成中文多模型评测技术报告；默认排除 smoke/probe 等诊断运行。"
    )
    parser.add_argument("--run-dir", action="append", help="标准 run 目录；可重复")
    parser.add_argument("--responses", action="append", help="records.jsonl；可重复")
    parser.add_argument(
        "--manifest",
        action="append",
        help="与 --responses 按顺序一一对应；省略时自动查找同目录 run-manifest.json",
    )
    parser.add_argument("--scores", action="append", help="评分 JSONL；可重复，按 run_id 自动匹配")
    parser.add_argument("--aggregation", action="append", help="score-summary JSON；可重复，按响应哈希匹配")
    parser.add_argument("--output", required=True, help="输出 Markdown")
    parser.add_argument("--title", default=DEFAULT_TITLE)
    parser.add_argument("--full-question-count", type=int, default=24)
    parser.add_argument(
        "--include-diagnostics",
        action="store_true",
        help="显式把 smoke/probe/quota 等诊断运行纳入报告（仍标记为不可比较）",
    )
    parser.add_argument("--overwrite", action="store_true", help="明确允许覆盖已有报告")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    configure_utf8_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        specs = resolve_run_specs(args)
        report = build_report(
            specs,
            score_paths=[Path(value) for value in args.scores or []],
            aggregation_paths=[Path(value) for value in args.aggregation or []],
            title=args.title,
            full_question_count=args.full_question_count,
            include_diagnostics=args.include_diagnostics,
        )
        output = Path(args.output)
        if output.exists() and not args.overwrite:
            raise ReportError("输出已存在；如需覆盖请添加 --overwrite：%s" % display_path(output))
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(report, encoding="utf-8", newline="\n")
    except ReportError as exc:
        print("Report generation FAILED: %s" % exc, file=sys.stderr)
        return 1
    print("Model evaluation report generated.")
    print("Output: %s" % display_path(Path(args.output)))
    print("No network requests were made; no credential files were read.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

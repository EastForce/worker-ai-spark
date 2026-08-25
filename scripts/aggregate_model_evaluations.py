#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""汇总多 judge 的逐回答评分，同时保留分歧、缺失与重大风险信号。

只使用中位数与原始分布，不生成排行榜。任一评分者标记的重大风险都会进入
独立风险登记表，不会被其他分数、中位数或未标记意见抵消。AI 初评永远不计入
“至少两名独立人工评分者”的正式比较门槛。
"""

from __future__ import annotations

import argparse
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from score_model_evaluations import (
    AI_PRELIMINARY_NOTICE,
    DIMENSIONS,
    DIMENSION_IDS,
    PipelineError,
    SCHEMA_VERSION,
    canonical_json,
    configure_utf8_stdio,
    default_manifest_path,
    first_nonempty_string,
    load_raw_records,
    portable_audit_path,
    read_jsonl,
    sha256_file,
    utc_now,
    write_json,
)


def json_median(values: Sequence[float | int]) -> Optional[float | int]:
    if not values:
        return None
    result = statistics.median(values)
    if isinstance(result, float) and result.is_integer():
        return int(result)
    return result


def score_value_kind(value: Any) -> str:
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 4:
        return "numeric"
    if value == "N/A":
        return "not_applicable"
    return "blank"


def distribution(values: Iterable[int], minimum: int = 0, maximum: int = 4) -> Dict[str, int]:
    counts = Counter(values)
    return {str(value): counts.get(value, 0) for value in range(minimum, maximum + 1)}


def load_score_records(
    paths: Sequence[str | Path], raw_by_id: Mapping[str, Mapping[str, Any]]
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    duplicate_ids: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    seen_ids: Dict[str, Dict[str, Any]] = {}
    for path_like in paths:
        path = str(path_like)
        for line_number, record in read_jsonl(path):
            score_id = first_nonempty_string(record, ("score_id",))
            evaluation_id = first_nonempty_string(record, ("evaluation_id", "record_key"))
            if not score_id:
                raise PipelineError("%s 第 %d 行缺少 score_id" % (path, line_number))
            if not evaluation_id:
                raise PipelineError("%s 第 %d 行缺少 evaluation_id" % (path, line_number))
            if evaluation_id not in raw_by_id:
                raise PipelineError(
                    "%s 第 %d 行 evaluation_id=%s 不在原始回答中"
                    % (path, line_number, evaluation_id)
                )
            raw = raw_by_id[evaluation_id]
            score_run_id = first_nonempty_string(record, ("run_id",))
            if not score_run_id:
                raise PipelineError("%s 第 %d 行缺少 run_id" % (path, line_number))
            if raw.get("run_id") and score_run_id != raw["run_id"]:
                raise PipelineError(
                    "%s 第 %d 行 run_id 与原始回答不一致" % (path, line_number)
                )
            scored_response_hash = first_nonempty_string(
                record, ("tested_response_sha256", "response_sha256")
            )
            if not scored_response_hash:
                raise PipelineError(
                    "%s 第 %d 行缺少 tested_response_sha256，不能审计评分与原回答的绑定"
                    % (path, line_number)
                )
            if scored_response_hash.lower() != raw["response_sha256"].lower():
                raise PipelineError(
                    "%s 第 %d 行的被评分回答哈希与原始回答不一致" % (path, line_number)
                )
            tested_system = record.get("tested_system")
            if not isinstance(tested_system, Mapping):
                raise PipelineError("%s 第 %d 行缺少 tested_system" % (path, line_number))
            for field in ("provider", "model", "configuration_id"):
                value = tested_system.get(field)
                if value is None:
                    raise PipelineError(
                        "%s 第 %d 行缺少 tested_system.%s" % (path, line_number, field)
                    )
                if value != raw[field]:
                    raise PipelineError(
                        "%s 第 %d 行 tested_system.%s 与原始回答不一致"
                        % (path, line_number, field)
                    )
            item = dict(record)
            item["_input_path"] = path
            item["_input_line"] = line_number
            if score_id in seen_ids:
                duplicate_ids[score_id].append(
                    {"path": portable_audit_path(path), "line": line_number, "identical": canonical_json(record) == canonical_json({k: v for k, v in seen_ids[score_id].items() if not k.startswith("_input_")})}
                )
                # 同一 score_id 不重复加权，但重复位置会公开列出。
                continue
            seen_ids[score_id] = item
            records.append(item)
    duplicate_audit = {
        "duplicate_score_id_count": len(duplicate_ids),
        "duplicates": [
            {"score_id": score_id, "additional_occurrences": locations}
            for score_id, locations in sorted(duplicate_ids.items())
        ],
    }
    return records, duplicate_audit


def compact_assessment(record: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "score_id": record.get("score_id"),
        "score_status": record.get("score_status"),
        "assessment_status": record.get("assessment_status"),
        "notice": record.get("notice"),
        "judge": record.get("judge"),
        "dimensions": record.get("dimensions"),
        "valid_dimension_count": record.get("valid_dimension_count"),
        "blank_dimension_count": record.get("blank_dimension_count"),
        "not_applicable_dimension_count": record.get("not_applicable_dimension_count"),
        "total_score": record.get("total_score"),
        "total_status": record.get("total_status"),
        "score_band": record.get("score_band"),
        "grade": record.get("grade"),
        "grade_status": record.get("grade_status"),
        "major_risk": record.get("major_risk"),
        "overall_rationale": record.get("overall_rationale"),
        "confidence": record.get("confidence"),
        "validation_errors": record.get("validation_errors", []),
        "metadata_warnings": record.get("metadata_warnings", []),
        "source": record.get("source"),
    }


def normalized_scorer_type(value: Any) -> str:
    return value if value in {"human", "ai", "unknown"} else "unknown"


def _dimension_stats_from_entries(entries: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    numeric = [
        entry["score"]
        for entry in entries
        if score_value_kind(entry.get("score")) == "numeric"
    ]
    confidences = [
        float(entry["confidence"])
        for entry in entries
        if isinstance(entry.get("confidence"), (int, float))
        and not isinstance(entry.get("confidence"), bool)
        and 0 <= entry["confidence"] <= 1
    ]
    return {
        "assessment_count": len(entries),
        "valid_numeric_count": len(numeric),
        "blank_count": sum(
            1 for entry in entries if score_value_kind(entry.get("score")) == "blank"
        ),
        "not_applicable_count": sum(
            1
            for entry in entries
            if score_value_kind(entry.get("score")) == "not_applicable"
        ),
        "distribution": distribution(numeric),
        "median": json_median(numeric),
        "confidence_median": json_median(confidences),
    }


def dimension_summary(valid_scores: Sequence[Mapping[str, Any]], dimension_id: str) -> Dict[str, Any]:
    raw_entries: List[Dict[str, Any]] = []
    for score in valid_scores:
        dimensions = score.get("dimensions")
        dimension = dimensions.get(dimension_id) if isinstance(dimensions, Mapping) else None
        dimension = dimension if isinstance(dimension, Mapping) else {}
        value = dimension.get("score")
        confidence = dimension.get("confidence")
        judge = score.get("judge") if isinstance(score.get("judge"), Mapping) else {}
        raw_entries.append(
            {
                "score_id": score.get("score_id"),
                "judge_id": judge.get("judge_id"),
                "scorer_type": judge.get("scorer_type"),
                "judge_model": judge.get("model"),
                "score": value,
                "rationale": dimension.get("rationale", ""),
                "confidence": confidence,
            }
        )
    by_scorer_type = {
        scorer_type: _dimension_stats_from_entries(
            [entry for entry in raw_entries if normalized_scorer_type(entry.get("scorer_type")) == scorer_type]
        )
        for scorer_type in ("human", "ai", "unknown")
    }
    if by_scorer_type["human"]["assessment_count"]:
        reported_type = "human"
    elif by_scorer_type["ai"]["assessment_count"]:
        reported_type = "ai"
    else:
        reported_type = "unknown"
    reported = by_scorer_type[reported_type]
    return {
        "reported_median_basis": reported_type,
        "reported_statistics_are_preliminary": reported_type != "human",
        "valid_numeric_count": reported["valid_numeric_count"],
        "blank_count": reported["blank_count"],
        "not_applicable_count": reported["not_applicable_count"],
        "distribution": reported["distribution"],
        "median": reported["median"],
        "confidence_median": reported["confidence_median"],
        "by_scorer_type": by_scorer_type,
        "all_assessments_audit": _dimension_stats_from_entries(raw_entries),
        "raw_judge_scores": raw_entries,
    }


def _total_stats_from_entries(entries: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    totals = [
        entry["total_score"]
        for entry in entries
        if isinstance(entry.get("total_score"), int)
        and not isinstance(entry.get("total_score"), bool)
        and 0 <= entry["total_score"] <= 20
    ]
    return {
        "assessment_count": len(entries),
        "complete_total_count": len(totals),
        "not_calculated_count": len(entries) - len(totals),
        "distribution": {str(value): totals.count(value) for value in sorted(set(totals))},
        "median": json_median(totals),
    }


def total_summary(valid_scores: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    entries: List[Dict[str, Any]] = []
    for score in valid_scores:
        total = score.get("total_score")
        judge = score.get("judge") if isinstance(score.get("judge"), Mapping) else {}
        entries.append(
            {
                "score_id": score.get("score_id"),
                "judge_id": judge.get("judge_id"),
                "scorer_type": judge.get("scorer_type"),
                "judge_model": judge.get("model"),
                "total_score": total,
                "total_status": score.get("total_status"),
                "score_band": score.get("score_band"),
                "grade": score.get("grade"),
            }
        )
    by_scorer_type = {
        scorer_type: _total_stats_from_entries(
            [entry for entry in entries if normalized_scorer_type(entry.get("scorer_type")) == scorer_type]
        )
        for scorer_type in ("human", "ai", "unknown")
    }
    if by_scorer_type["human"]["assessment_count"]:
        reported_type = "human"
    elif by_scorer_type["ai"]["assessment_count"]:
        reported_type = "ai"
    else:
        reported_type = "unknown"
    reported = by_scorer_type[reported_type]
    return {
        "reported_median_basis": reported_type,
        "reported_statistics_are_preliminary": reported_type != "human",
        "complete_total_count": reported["complete_total_count"],
        "not_calculated_count": reported["not_calculated_count"],
        "distribution": reported["distribution"],
        "median": reported["median"],
        "by_scorer_type": by_scorer_type,
        "all_assessments_audit": _total_stats_from_entries(entries),
        "raw_judge_totals": entries,
    }


def major_risk_summary(scores: Sequence[Mapping[str, Any]], raw: Mapping[str, Any]) -> Dict[str, Any]:
    status_counts = Counter()
    register: List[Dict[str, Any]] = []
    for score in scores:
        risk = score.get("major_risk") if isinstance(score.get("major_risk"), Mapping) else {}
        status = risk.get("status") if risk.get("status") in {"flagged", "not_flagged", "uncertain", "blank"} else "blank"
        status_counts[status] += 1
        if status not in {"flagged", "uncertain"}:
            continue
        judge = score.get("judge") if isinstance(score.get("judge"), Mapping) else {}
        register.append(
            {
                "evaluation_id": raw["evaluation_id"],
                "run_id": raw["run_id"],
                "question_id": raw["question_id"],
                "tested_system": {
                    "provider": raw["provider"],
                    "model": raw["model"],
                    "configuration_id": raw["configuration_id"],
                },
                "tested_response_sha256": raw["response_sha256"],
                "score_id": score.get("score_id"),
                "score_status": score.get("score_status"),
                "judge": judge,
                "assessment_status": score.get("assessment_status"),
                "validation_errors": score.get("validation_errors", []),
                "risk_status": status,
                "rubric_items": risk.get("rubric_items", []),
                "labels": risk.get("labels", []),
                "evidence": risk.get("evidence", []),
                "rationale": risk.get("rationale", ""),
                "machine_signal_only": judge.get("scorer_type") == "ai",
                "not_adjudicated_by_pipeline": True,
            }
        )
    return {
        "status_counts": {
            "flagged": status_counts.get("flagged", 0),
            "not_flagged": status_counts.get("not_flagged", 0),
            "uncertain": status_counts.get("uncertain", 0),
            "blank": status_counts.get("blank", 0),
        },
        "any_flagged": status_counts.get("flagged", 0) > 0,
        "any_uncertain": status_counts.get("uncertain", 0) > 0,
        "risk_register": register,
        "not_cancelled_by_scores_or_medians": True,
        "adjudication_status": "requires_independent_human_review" if register else "not_adjudicated",
    }


def completed_independent_human_judges(valid_scores: Sequence[Mapping[str, Any]]) -> List[str]:
    judge_ids = set()
    for score in valid_scores:
        judge = score.get("judge") if isinstance(score.get("judge"), Mapping) else {}
        if judge.get("scorer_type") != "human" or judge.get("independent") is not True:
            continue
        if score.get("assessment_status") != "human_independent":
            continue
        dimensions = score.get("dimensions") if isinstance(score.get("dimensions"), Mapping) else {}
        dimension_values = [
            (dimensions.get(dimension_id) or {}).get("score")
            if isinstance(dimensions.get(dimension_id), Mapping)
            else None
            for dimension_id in DIMENSION_IDS
        ]
        if any(score_value_kind(value) == "blank" for value in dimension_values):
            continue
        # N/A 可以与有效维度共存，但五维全为 N/A 只表示无法评分，
        # 不应充当一份已完成的独立人工评分。
        if not any(score_value_kind(value) == "numeric" for value in dimension_values):
            continue
        risk = score.get("major_risk") if isinstance(score.get("major_risk"), Mapping) else {}
        if risk.get("status") == "blank":
            continue
        judge_id = judge.get("judge_id")
        if isinstance(judge_id, str) and judge_id.strip():
            judge_ids.add(judge_id.strip())
    return sorted(judge_ids)


def detect_disagreements(
    valid_scores: Sequence[Mapping[str, Any]],
    dimensions: Mapping[str, Mapping[str, Any]],
    total: Mapping[str, Any],
    risk: Mapping[str, Any],
) -> Dict[str, Any]:
    triggers: List[Dict[str, Any]] = []
    for dimension_id in DIMENSION_IDS:
        summary = dimensions[dimension_id]
        values = [
            entry["score"]
            for entry in summary["raw_judge_scores"]
            if score_value_kind(entry.get("score")) == "numeric"
        ]
        if len(values) >= 2 and max(values) - min(values) >= 2:
            triggers.append(
                {
                    "type": "dimension_gap_at_least_2",
                    "dimension": dimension_id,
                    "minimum": min(values),
                    "maximum": max(values),
                }
            )
        kinds = {score_value_kind(entry.get("score")) for entry in summary["raw_judge_scores"]}
        if len(kinds) >= 2:
            triggers.append(
                {
                    "type": "dimension_availability_disagreement",
                    "dimension": dimension_id,
                    "value_kinds": sorted(kinds),
                }
            )

    bands = {
        entry.get("score_band")
        for entry in total.get("raw_judge_totals", [])
        if entry.get("score_band") is not None
    }
    grades = {
        entry.get("grade")
        for entry in total.get("raw_judge_totals", [])
        if entry.get("grade") is not None
    }
    if len(bands) >= 2 or len(grades) >= 2:
        triggers.append(
            {"type": "grade_or_score_band_disagreement", "score_bands": sorted(bands), "grades": sorted(grades)}
        )
    risk_statuses = {
        (score.get("major_risk") or {}).get("status")
        for score in valid_scores
        if isinstance(score.get("major_risk"), Mapping)
        and (score.get("major_risk") or {}).get("status") != "blank"
    }
    if len(risk_statuses) >= 2:
        triggers.append(
            {"type": "major_risk_status_disagreement", "statuses": sorted(risk_statuses)}
        )
    return {
        "requires_review": bool(triggers),
        "triggers": triggers,
        "raw_scores_preserved": True,
    }


def summarize_answer(raw: Mapping[str, Any], scores: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    valid_scores = [score for score in scores if score.get("score_status") == "valid"]
    dimension_summaries = {
        dimension_id: dimension_summary(valid_scores, dimension_id)
        for dimension_id in DIMENSION_IDS
    }
    totals = total_summary(valid_scores)
    # 无效评分不进入中位数，但其中已经识别的 flagged/uncertain 风险
    # 仍必须进入风险登记表，避免因其他格式错误掩盖安全线索。
    risks = major_risk_summary(scores, raw)
    disagreement = detect_disagreements(valid_scores, dimension_summaries, totals, risks)
    human_judges = completed_independent_human_judges(valid_scores)
    scorer_type_counts = Counter(
        (score.get("judge") or {}).get("scorer_type", "unknown")
        for score in valid_scores
        if isinstance(score.get("judge"), Mapping)
    )
    formal_reasons: List[str] = []
    if raw["status"] not in {"success", "succeeded", "ok", "completed", "complete"}:
        formal_reasons.append("被测模型调用未成功")
    if raw.get("question_status") != "stable":
        formal_reasons.append("题目状态不是 stable；该结果只能作为 pilot 记录")
    if len(human_judges) < 2:
        formal_reasons.append("少于两名完成评分的独立人工评分者")
    if risks["risk_register"]:
        formal_reasons.append("存在重大风险信号，尚待独立人工复核")
    if disagreement["requires_review"]:
        formal_reasons.append("评分分歧触发复核")
    if any(score.get("score_status") != "valid" for score in scores):
        formal_reasons.append("存在无效评分或 judge 调用错误")

    return {
        "evaluation_id": raw["evaluation_id"],
        "run_id": raw["run_id"],
        "question_id": raw["question_id"],
        "question_version": raw["question_version"],
        "question_status": raw["question_status"],
        "tested_system": {
            "provider": raw["provider"],
            "model": raw["model"],
            "configuration_id": raw["configuration_id"],
        },
        "tested_response": {
            "status": raw["status"],
            "sha256": raw["response_sha256"],
            "history_record_count": len(raw.get("history_line_numbers", [raw.get("line_number")])),
            "history_statuses": raw.get("history_statuses", [raw["status"]]),
            "latest_source_line": raw.get("line_number"),
        },
        "assessment_counts": {
            "all": len(scores),
            "valid": len(valid_scores),
            "invalid": sum(1 for score in scores if score.get("score_status") == "invalid"),
            "judge_error": sum(1 for score in scores if score.get("score_status") == "judge_error"),
            "ai": scorer_type_counts.get("ai", 0),
            "human": scorer_type_counts.get("human", 0),
            "unknown_scorer_type": scorer_type_counts.get("unknown", 0),
        },
        "dimensions": dimension_summaries,
        "total": totals,
        "major_risk": risks,
        "disagreement": disagreement,
        "formal_readiness": {
            "two_independent_human_scores": len(human_judges) >= 2,
            "completed_independent_human_judge_ids": human_judges,
            "ready_for_formal_comparison": not formal_reasons,
            "blocking_reasons": formal_reasons,
        },
        "assessments": [compact_assessment(score) for score in scores],
    }


def tested_system_key(raw: Mapping[str, Any]) -> Tuple[str, str, str]:
    return raw["provider"], raw["model"], raw["configuration_id"]


def aggregate_dimension_across_answers(
    answers: Sequence[Mapping[str, Any]], dimension_id: str
) -> Dict[str, Any]:
    def for_type(scorer_type: str) -> Dict[str, Any]:
        entries = [
            entry
            for answer in answers
            for entry in answer["dimensions"][dimension_id]["raw_judge_scores"]
            if normalized_scorer_type(entry.get("scorer_type")) == scorer_type
        ]
        answer_medians = [
            answer["dimensions"][dimension_id]["by_scorer_type"][scorer_type]["median"]
            for answer in answers
            if answer["dimensions"][dimension_id]["by_scorer_type"][scorer_type]["median"]
            is not None
        ]
        return {
            "raw_judge_assessments": _dimension_stats_from_entries(entries),
            "answer_equal_weight_medians": {
                "answer_count": len(answer_medians),
                "median": json_median(answer_medians),
                "values": answer_medians,
            },
        }

    by_scorer_type = {scorer_type: for_type(scorer_type) for scorer_type in ("human", "ai", "unknown")}
    if by_scorer_type["human"]["raw_judge_assessments"]["assessment_count"]:
        reported_type = "human"
    elif by_scorer_type["ai"]["raw_judge_assessments"]["assessment_count"]:
        reported_type = "ai"
    else:
        reported_type = "unknown"
    all_entries = [
        entry
        for answer in answers
        for entry in answer["dimensions"][dimension_id]["raw_judge_scores"]
    ]
    return {
        "reported_median_basis": reported_type,
        "reported_statistics_are_preliminary": reported_type != "human",
        **by_scorer_type[reported_type],
        "by_scorer_type": by_scorer_type,
        "all_assessments_audit": _dimension_stats_from_entries(all_entries),
    }


def aggregate_totals_across_answers(answers: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    def for_type(scorer_type: str) -> Dict[str, Any]:
        entries = [
            entry
            for answer in answers
            for entry in answer["total"]["raw_judge_totals"]
            if normalized_scorer_type(entry.get("scorer_type")) == scorer_type
        ]
        answer_medians = [
            answer["total"]["by_scorer_type"][scorer_type]["median"]
            for answer in answers
            if answer["total"]["by_scorer_type"][scorer_type]["median"] is not None
        ]
        return {
            "raw_judge_assessments": _total_stats_from_entries(entries),
            "answer_equal_weight_medians": {
                "answer_count": len(answer_medians),
                "median": json_median(answer_medians),
                "values": answer_medians,
            },
        }

    by_scorer_type = {scorer_type: for_type(scorer_type) for scorer_type in ("human", "ai", "unknown")}
    if by_scorer_type["human"]["raw_judge_assessments"]["assessment_count"]:
        reported_type = "human"
    elif by_scorer_type["ai"]["raw_judge_assessments"]["assessment_count"]:
        reported_type = "ai"
    else:
        reported_type = "unknown"
    all_entries = [
        entry for answer in answers for entry in answer["total"]["raw_judge_totals"]
    ]
    return {
        "reported_median_basis": reported_type,
        "reported_statistics_are_preliminary": reported_type != "human",
        **by_scorer_type[reported_type],
        "by_scorer_type": by_scorer_type,
        "all_assessments_audit": _total_stats_from_entries(all_entries),
    }


def summarize_tested_system(
    key: Tuple[str, str, str], answers: Sequence[Mapping[str, Any]]
) -> Dict[str, Any]:
    provider, model, configuration_id = key
    risk_register = [
        risk
        for answer in answers
        for risk in answer["major_risk"]["risk_register"]
    ]
    flagged_evaluations = sorted(
        {
            risk["evaluation_id"]
            for risk in risk_register
            if risk.get("risk_status") == "flagged"
        }
    )
    uncertain_evaluations = sorted(
        {
            risk["evaluation_id"]
            for risk in risk_register
            if risk.get("risk_status") == "uncertain"
        }
    )
    successful_answers = [
        answer
        for answer in answers
        if answer["tested_response"]["status"] in {"success", "succeeded", "ok", "completed", "complete"}
    ]
    double_human = [
        answer
        for answer in successful_answers
        if answer["formal_readiness"]["two_independent_human_scores"]
    ]
    formally_ready_answers = [
        answer
        for answer in successful_answers
        if answer["formal_readiness"]["ready_for_formal_comparison"]
    ]
    all_observed_answers_successful = len(successful_answers) == len(answers)
    disagreement_ids = sorted(
        answer["evaluation_id"] for answer in answers if answer["disagreement"]["requires_review"]
    )
    return {
        "tested_system": {
            "provider": provider,
            "model": model,
            "configuration_id": configuration_id,
        },
        "answer_counts": {
            "all": len(answers),
            "successful": len(successful_answers),
            "failed_or_missing": len(answers) - len(successful_answers),
            "with_any_valid_score": sum(1 for answer in answers if answer["assessment_counts"]["valid"] > 0),
            "with_two_independent_human_scores": len(double_human),
        },
        "dimensions": {
            dimension_id: aggregate_dimension_across_answers(answers, dimension_id)
            for dimension_id in DIMENSION_IDS
        },
        "total": aggregate_totals_across_answers(answers),
        "major_risk": {
            "has_any_flagged_signal": bool(flagged_evaluations),
            "has_any_uncertain_signal": bool(uncertain_evaluations),
            "flagged_evaluation_ids": flagged_evaluations,
            "uncertain_evaluation_ids": uncertain_evaluations,
            "risk_register": risk_register,
            "not_cancelled_by_other_answers_or_medians": True,
            "adjudication_status": "requires_independent_human_review" if risk_register else "not_adjudicated",
        },
        "disagreements": {
            "answer_count": len(disagreement_ids),
            "evaluation_ids": disagreement_ids,
        },
        "formal_readiness": {
            "ready_for_formal_comparison": bool(answers)
            and all_observed_answers_successful
            and len(formally_ready_answers) == len(answers),
            "all_observed_answers_successful": all_observed_answers_successful,
            "successful_answers_all_double_human_scored": bool(successful_answers)
            and len(double_human) == len(successful_answers),
            "successful_answers_all_formally_ready": bool(successful_answers)
            and len(formally_ready_answers) == len(successful_answers),
            "notice": (
                "AI 初评不计入独立人工人数；重大风险与分歧需单独复核。"
            ),
        },
    }


def aggregate_evaluations(
    responses_path: str | Path,
    score_paths: Sequence[str | Path],
    manifest_path: Optional[str | Path] = None,
) -> Dict[str, Any]:
    raw_records, manifest = load_raw_records(responses_path, manifest_path=manifest_path)
    raw_by_id = {record["evaluation_id"]: record for record in raw_records}
    scores, duplicate_audit = load_score_records(score_paths, raw_by_id)
    scores_by_evaluation: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for score in scores:
        scores_by_evaluation[score["evaluation_id"]].append(score)

    answers = [
        summarize_answer(raw, scores_by_evaluation.get(raw["evaluation_id"], []))
        for raw in raw_records
    ]
    answers.sort(
        key=lambda item: (
            item["tested_system"]["provider"],
            item["tested_system"]["model"],
            item["tested_system"]["configuration_id"],
            item.get("question_id") or "",
            item["evaluation_id"],
        )
    )
    answer_groups: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = defaultdict(list)
    for answer in answers:
        system = answer["tested_system"]
        answer_groups[(system["provider"], system["model"], system["configuration_id"])].append(answer)
    systems = [
        summarize_tested_system(key, grouped_answers)
        for key, grouped_answers in sorted(answer_groups.items())
    ]
    top_risk_register = [
        risk
        for answer in answers
        for risk in answer["major_risk"]["risk_register"]
    ]
    disagreement_register = [
        {
            "evaluation_id": answer["evaluation_id"],
            "run_id": answer["run_id"],
            "question_id": answer["question_id"],
            "tested_system": answer["tested_system"],
            "triggers": answer["disagreement"]["triggers"],
        }
        for answer in answers
        if answer["disagreement"]["requires_review"]
    ]
    successful_answers = [
        answer
        for answer in answers
        if answer["tested_response"]["status"] in {"success", "succeeded", "ok", "completed", "complete"}
    ]
    ready_answers = [
        answer for answer in answers if answer["formal_readiness"]["ready_for_formal_comparison"]
    ]
    ai_score_count = sum(
        1
        for score in scores
        if isinstance(score.get("judge"), Mapping)
        and score["judge"].get("scorer_type") == "ai"
        and score.get("score_status") == "valid"
    )
    human_score_count = sum(
        1
        for score in scores
        if isinstance(score.get("judge"), Mapping)
        and score["judge"].get("scorer_type") == "human"
        and score.get("score_status") == "valid"
    )
    formal_reasons: List[str] = []
    if not successful_answers:
        formal_reasons.append("没有成功的被测模型回答")
    if len(successful_answers) != len(answers):
        formal_reasons.append("存在失败或缺失的被测回答")
    if len(ready_answers) != len(answers):
        formal_reasons.append("并非每个计划内回答都满足独立人工双评分及复核要求")
    if top_risk_register:
        formal_reasons.append("存在未裁决的重大风险信号")
    if disagreement_register:
        formal_reasons.append("存在需要复核的评分分歧")
    if duplicate_audit["duplicate_score_id_count"]:
        formal_reasons.append("输入中存在重复 score_id，已避免重复加权但仍需检查来源")
    if isinstance(manifest, Mapping) and manifest.get("formal_comparison_allowed") is False:
        formal_reasons.append("运行 manifest 明确标记 formal_comparison_allowed=false")
    planned_count = manifest.get("planned_count") if isinstance(manifest, Mapping) else None
    coverage_complete: Optional[bool] = None
    if isinstance(planned_count, int) and not isinstance(planned_count, bool):
        coverage_complete = planned_count == len(raw_records)
        if not coverage_complete:
            formal_reasons.append(
                "运行 manifest 计划 %d 条，当前只有 %d 个唯一最新回答记录"
                % (planned_count, len(raw_records))
            )
    manifest_run_status = (
        first_nonempty_string(manifest, ("run_status",))
        if isinstance(manifest, Mapping)
        else None
    )
    if manifest_run_status and manifest_run_status != "completed":
        formal_reasons.append("run manifest 状态不是 completed: %s" % manifest_run_status)

    return {
        "aggregate_schema_version": SCHEMA_VERSION,
        "generated_at": utc_now(),
        "evaluation_phase": (manifest or {}).get("evaluation_phase", "pilot"),
        "publication_status": (manifest or {}).get("publication_status", "review_required"),
        "notice": AI_PRELIMINARY_NOTICE,
        "method": {
            "central_tendency": "median",
            "ai_and_human_statistics_separated": True,
            "reported_median_priority": ["human", "ai", "unknown"],
            "ai_median_is_preliminary_only": True,
            "n_a_and_blank_excluded_from_median": True,
            "blank_is_not_zero": True,
            "n_a_is_not_zero": True,
            "answer_equal_weight_medians_provided": True,
            "mean_not_calculated": True,
            "rankings_not_generated": True,
            "major_risk_signals_never_cancelled_by_scores": True,
        },
        "source": {
            "responses_path": portable_audit_path(responses_path),
            "responses_sha256": sha256_file(responses_path),
            "manifest_path": portable_audit_path(manifest_path) if manifest_path else None,
            "manifest_sha256": sha256_file(manifest_path) if manifest_path else None,
            "score_files": [
                {"path": portable_audit_path(path), "sha256": sha256_file(path)}
                for path in score_paths
            ],
            "run_id": first_nonempty_string(manifest or {}, ("run_id", "batch_id")),
        },
        "counts": {
            "raw_answer_record_lines": sum(
                len(item.get("history_line_numbers", [item.get("line_number")]))
                for item in raw_records
            ),
            "unique_latest_answer_records": len(raw_records),
            "superseded_answer_records": sum(
                max(0, len(item.get("history_line_numbers", [])) - 1)
                for item in raw_records
            ),
            "successful_answers": len(successful_answers),
            "failed_or_missing_answers": len(raw_records) - len(successful_answers),
            "unique_score_records": len(scores),
            "valid_ai_preliminary_scores": ai_score_count,
            "valid_human_scores": human_score_count,
            "invalid_or_judge_error_scores": sum(
                1 for score in scores if score.get("score_status") != "valid"
            ),
        },
        "duplicate_input_audit": duplicate_audit,
        "formal_readiness": {
            "ready_for_formal_comparison": not formal_reasons,
            "blocking_reasons": formal_reasons,
            "successful_answer_count": len(successful_answers),
            "fully_ready_answer_count": len(ready_answers),
            "required_human_scorers_per_answer": 2,
            "ai_scores_count_toward_requirement": False,
            "planned_record_count": planned_count,
            "observed_unique_latest_record_count": len(raw_records),
            "planned_coverage_complete": coverage_complete,
            "manifest_run_status": manifest_run_status,
        },
        "major_risk_register": {
            "entry_count": len(top_risk_register),
            "affected_evaluation_count": len({item["evaluation_id"] for item in top_risk_register}),
            "entries": top_risk_register,
            "not_adjudicated_by_pipeline": True,
        },
        "disagreement_register": {
            "affected_evaluation_count": len(disagreement_register),
            "entries": disagreement_register,
        },
        "tested_systems": systems,
        "answers": answers,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="离线汇总逐回答评分；保留多 judge 分歧、N/A/空白和重大风险信号。"
    )
    parser.add_argument("--responses", required=True, help="被测模型 records.jsonl")
    parser.add_argument("--manifest", help="被测模型 run-manifest.json")
    parser.add_argument(
        "--scores", required=True, action="append", help="评分 JSONL；可重复传入多个文件"
    )
    parser.add_argument("--output", required=True, help="汇总 JSON 输出")
    parser.add_argument("--overwrite", action="store_true", help="明确覆盖已有输出")
    parser.add_argument(
        "--fail-if-not-formal-ready",
        action="store_true",
        help="仍写出完整汇总，但不满足独立人工双评分/复核门槛时返回退出码 2",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    configure_utf8_stdio()
    args = build_parser().parse_args(argv)
    try:
        summary = aggregate_evaluations(
            responses_path=args.responses,
            score_paths=args.scores,
            manifest_path=args.manifest,
        )
        write_json(args.output, summary, overwrite=args.overwrite)
    except PipelineError as exc:
        print("Aggregation FAILED: %s" % exc, file=sys.stderr)
        return 1
    print("Evaluation aggregation completed.")
    print("Output: %s" % args.output)
    print("Successful answers: %d" % summary["counts"]["successful_answers"])
    print("Score records: %d" % summary["counts"]["unique_score_records"])
    print("Major-risk signals: %d" % summary["major_risk_register"]["entry_count"])
    print("Formal comparison ready: %s" % summary["formal_readiness"]["ready_for_formal_comparison"])
    print("Notice: %s" % summary["notice"])
    if args.fail_if_not_formal_ready and not summary["formal_readiness"]["ready_for_formal_comparison"]:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""可恢复的 AI judge 请求执行器。

默认只做 dry-run。只有显式传入 ``--execute`` 才会调用提供方。凭据仅由
``model_eval_providers`` 从当前进程环境变量读取；本脚本不读取密钥文件，
不持久化请求头或环境变量。

输入由 ``score_model_evaluations.py generate`` 产生。输出保留完整、已脱敏的
提供方原始响应，同时把可评分最终文本与 reasoning 分离。后续导入脚本只读取
``final_response_text``，不会把 reasoning 当作评分 JSON。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from model_eval_providers import (
    PROVIDER_NAMES,
    BaseProvider,
    GenerationResult,
    ProviderRequestError,
    build_provider,
    canonical_json,
)
from run_model_evaluations import (
    RUN_ID_PATTERN,
    RateLimiter,
    RetryFailure,
    append_jsonl,
    call_with_retry,
    display_path,
    file_sha256,
    parse_provider_intervals,
    read_existing_records,
    response_was_truncated,
    sha256_json,
    sha256_text,
    summarize_records,
    utc_now,
    validate_output_directory,
    write_json_atomic,
)


SCRIPT_DIR = Path(__file__).resolve().parent
REPOSITORY_ROOT = SCRIPT_DIR.parent
RECORDS_FILENAME = "judge-responses.jsonl"
MANIFEST_FILENAME = "judge-run-manifest.json"
RECORD_SCHEMA_VERSION = "1.0"
MANIFEST_SCHEMA_VERSION = "1.0"
EVALUATION_PHASE = "pilot"
SCORING_STATUS = "ai_preliminary_unimported"
PUBLICATION_STATUS = "review_required"


def configure_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


def unique(values: Iterable[str]) -> List[str]:
    result: List[str] = []
    seen = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _nonempty_string(value: Any) -> Optional[str]:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def load_judge_requests(path: Path) -> List[Dict[str, Any]]:
    if not path.is_file():
        raise ValueError("judge request JSONL does not exist: %s" % display_path(path))
    records: List[Dict[str, Any]] = []
    seen_request_ids = set()
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            if not raw_line.strip():
                continue
            try:
                record = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    "judge request line %d is not valid JSON: %s" % (line_number, exc)
                ) from exc
            if not isinstance(record, dict):
                raise ValueError("judge request line %d must be a JSON object" % line_number)
            request_id = _nonempty_string(record.get("request_id"))
            evaluation_id = _nonempty_string(record.get("evaluation_id"))
            judge = record.get("judge")
            request = record.get("request")
            if not request_id or not evaluation_id:
                raise ValueError(
                    "judge request line %d requires non-empty request_id and evaluation_id"
                    % line_number
                )
            if request_id in seen_request_ids:
                raise ValueError("duplicate judge request_id: %s" % request_id)
            seen_request_ids.add(request_id)
            if not isinstance(judge, Mapping):
                raise ValueError("judge request %s has no judge object" % request_id)
            scorer_type = _nonempty_string(judge.get("scorer_type"))
            provider = _nonempty_string(judge.get("provider"))
            model = _nonempty_string(judge.get("model"))
            if scorer_type != "ai":
                raise ValueError(
                    "judge request %s is not an AI scorer request; this runner must not impersonate a human scorer"
                    % request_id
                )
            if provider not in PROVIDER_NAMES:
                raise ValueError(
                    "judge request %s has unsupported provider %r" % (request_id, provider)
                )
            if not model:
                raise ValueError("judge request %s has no judge model" % request_id)
            if not isinstance(request, Mapping):
                raise ValueError("judge request %s has no request object" % request_id)
            for prompt_name in ("system_prompt", "user_prompt"):
                if not isinstance(request.get(prompt_name), str):
                    raise ValueError(
                        "judge request %s request.%s must be a string"
                        % (request_id, prompt_name)
                    )
            parameters = request.get("parameters")
            if parameters is not None and not isinstance(parameters, Mapping):
                raise ValueError(
                    "judge request %s request.parameters must be an object" % request_id
                )
            records.append(record)
    if not records:
        raise ValueError("judge request JSONL contains no records")
    return records


def filter_requests(
    requests: Sequence[Dict[str, Any]],
    request_ids: Optional[Sequence[str]],
    limit: Optional[int],
) -> List[Dict[str, Any]]:
    selected = list(requests)
    if request_ids:
        wanted = set(request_ids)
        selected = [record for record in selected if record["request_id"] in wanted]
        found = {record["request_id"] for record in selected}
        missing = sorted(wanted - found)
        if missing:
            raise ValueError("unknown request ids: %s" % ", ".join(missing))
    if limit is not None:
        if limit < 1:
            raise ValueError("--limit-requests must be at least 1")
        selected = selected[:limit]
    if not selected:
        raise ValueError("no judge requests selected")
    return selected


def provider_models(requests: Sequence[Mapping[str, Any]]) -> Dict[str, List[str]]:
    result: Dict[str, List[str]] = {}
    for request in requests:
        judge = request["judge"]
        provider = str(judge["provider"])
        model = str(judge["model"])
        result.setdefault(provider, [])
        if model not in result[provider]:
            result[provider].append(model)
    return result


def global_parameter_overrides(args: argparse.Namespace) -> Dict[str, Any]:
    values = {
        "temperature": args.temperature,
        "top_p": args.top_p,
        "top_k": args.top_k,
        "max_tokens": args.max_tokens,
        "seed": args.seed,
        "deepseek_thinking": args.deepseek_thinking,
        "minimax_stream": args.minimax_stream,
    }
    return {key: value for key, value in values.items() if value is not None}


def effective_parameters(
    request_record: Mapping[str, Any], overrides: Mapping[str, Any]
) -> Dict[str, Any]:
    request = request_record.get("request")
    raw_parameters = request.get("parameters") if isinstance(request, Mapping) else {}
    raw_parameters = raw_parameters if isinstance(raw_parameters, Mapping) else {}
    result: Dict[str, Any] = {}
    recommendation_aliases = {
        "temperature": ("temperature", "temperature_recommendation"),
        "top_p": ("top_p", "top_p_recommendation"),
        "top_k": ("top_k", "top_k_recommendation"),
        "max_tokens": (
            "max_tokens",
            "max_output_tokens",
            "max_tokens_recommendation",
            "max_output_tokens_recommendation",
        ),
        "seed": ("seed", "seed_recommendation"),
    }
    for target, aliases in recommendation_aliases.items():
        if target in overrides:
            result[target] = overrides[target]
            continue
        for alias in aliases:
            if raw_parameters.get(alias) is not None:
                result[target] = raw_parameters[alias]
                break
    judge = request_record.get("judge")
    provider = _nonempty_string(judge.get("provider")) if isinstance(judge, Mapping) else None
    if provider == "deepseek" and overrides.get("deepseek_thinking") is not None:
        result["deepseek_thinking"] = overrides["deepseek_thinking"]
    if provider == "minimax" and overrides.get("minimax_stream") is not None:
        result["minimax_stream"] = overrides["minimax_stream"]
    validate_parameters(result)
    return result


def validate_parameters(parameters: Mapping[str, Any]) -> None:
    for name in ("temperature", "top_p"):
        value = parameters.get(name)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
            raise ValueError("%s must be numeric" % name)
    for name in ("top_k", "max_tokens", "seed"):
        value = parameters.get(name)
        if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
            raise ValueError("%s must be an integer" % name)
    if parameters.get("max_tokens") is not None and parameters["max_tokens"] < 1:
        raise ValueError("max_tokens must be at least 1")
    if parameters.get("top_k") is not None and parameters["top_k"] < 1:
        raise ValueError("top_k must be at least 1")
    if parameters.get("deepseek_thinking") not in (None, "enabled", "disabled"):
        raise ValueError("deepseek_thinking must be enabled or disabled")
    if parameters.get("minimax_stream") is not None and not isinstance(
        parameters["minimax_stream"], bool
    ):
        raise ValueError("minimax_stream must be a boolean")


def validate_runtime_arguments(args: argparse.Namespace) -> None:
    if args.timeout <= 0:
        raise ValueError("--timeout must be greater than 0")
    if args.max_attempts < 1:
        raise ValueError("--max-attempts must be at least 1")
    if args.initial_backoff < 0 or args.max_backoff < 0:
        raise ValueError("backoff values must be non-negative")
    if args.min_interval < 0:
        raise ValueError("--min-interval must be non-negative")


def make_record_key(
    request_record: Mapping[str, Any], provider: str, model: str, parameters: Mapping[str, Any]
) -> str:
    return sha256_json(
        {
            "request_id": request_record["request_id"],
            "evaluation_id": request_record["evaluation_id"],
            "judge_request_sha256": sha256_json(request_record),
            "provider": provider,
            "model": model,
            "parameters": dict(parameters),
        }
    )


def completed_record_keys(
    records: Sequence[Mapping[str, Any]], *, skip_errors: bool
) -> set:
    statuses = {"success"}
    if skip_errors:
        statuses.add("error")
    return {
        record["record_key"]
        for record in records
        if record.get("status") in statuses
        and isinstance(record.get("record_key"), str)
    }


def create_manifest(
    *,
    run_id: str,
    created_at: str,
    input_path: Path,
    input_sha256: str,
    selected_requests: Sequence[Mapping[str, Any]],
    matrix: Mapping[str, Sequence[str]],
    parameter_overrides: Mapping[str, Any],
) -> Dict[str, Any]:
    return {
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "run_id": run_id,
        "evaluation_phase": EVALUATION_PHASE,
        "scoring_status": SCORING_STATUS,
        "publication_status": PUBLICATION_STATUS,
        "formal_comparison_allowed": False,
        "created_at": created_at,
        "completed_at": None,
        "run_status": "running",
        "input": {
            "path": display_path(input_path),
            "sha256": input_sha256,
            "selected_request_count": len(selected_requests),
            "selected_request_ids": [record["request_id"] for record in selected_requests],
        },
        "provider_order": list(matrix.keys()),
        "provider_models": {provider: list(models) for provider, models in matrix.items()},
        "generation_parameter_overrides": dict(parameter_overrides),
        "records_file": RECORDS_FILENAME,
        "planned_count": len(selected_requests),
        "unique_record_count": 0,
        "success_count": 0,
        "error_count": 0,
        "skipped_existing_count": 0,
        "limitations": [
            "AI judge output is preliminary and cannot replace two independent human scorers",
            "major-risk flags are signals requiring independent human review, not automatic adjudications",
            "provider-side model revisions and hidden system settings may be unavailable",
            "reasoning is excluded from final_response_text and downstream score import",
        ],
    }


def load_resume_manifest(path: Path, input_sha256: str) -> Mapping[str, Any]:
    if not path.is_file():
        raise ValueError("--resume requires an existing %s" % MANIFEST_FILENAME)
    with path.open("r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    if not isinstance(manifest, Mapping):
        raise ValueError("existing judge manifest must be a JSON object")
    input_info = manifest.get("input")
    if not isinstance(input_info, Mapping) or input_info.get("sha256") != input_sha256:
        raise ValueError("resume input SHA-256 does not match the existing judge manifest")
    return manifest


def validate_resume_configuration(
    manifest: Mapping[str, Any],
    *,
    requested_run_id: Optional[str],
    selected_requests: Sequence[Mapping[str, Any]],
    matrix: Mapping[str, Sequence[str]],
    parameter_overrides: Mapping[str, Any],
) -> None:
    mismatches: List[str] = []
    input_info = manifest.get("input")
    expected_ids = [record["request_id"] for record in selected_requests]
    if not isinstance(input_info, Mapping) or input_info.get("selected_request_ids") != expected_ids:
        mismatches.append("selected request ids/order")
    if manifest.get("provider_models") != {
        provider: list(models) for provider, models in matrix.items()
    }:
        mismatches.append("provider/model matrix")
    if manifest.get("provider_order") != list(matrix.keys()):
        mismatches.append("provider order")
    if manifest.get("generation_parameter_overrides") != dict(parameter_overrides):
        mismatches.append("generation parameter overrides")
    if manifest.get("planned_count") != len(selected_requests):
        mismatches.append("planned count")
    if manifest.get("manifest_schema_version") != MANIFEST_SCHEMA_VERSION:
        mismatches.append("manifest schema version")
    if manifest.get("records_file") != RECORDS_FILENAME:
        mismatches.append("records file")
    if manifest.get("scoring_status") != SCORING_STATUS:
        mismatches.append("scoring status")
    run_id = manifest.get("run_id")
    if not isinstance(run_id, str) or not RUN_ID_PATTERN.fullmatch(run_id):
        mismatches.append("run id")
    elif requested_run_id is not None and requested_run_id != run_id:
        mismatches.append("requested run id")
    if mismatches:
        raise ValueError(
            "resume configuration does not match the existing judge manifest (%s); "
            "use the original arguments or choose a new output directory"
            % ", ".join(mismatches)
        )


def build_response_record(
    *,
    run_id: str,
    request_record: Mapping[str, Any],
    provider_name: str,
    model: str,
    parameters: Mapping[str, Any],
    request_started_at: str,
    response_received_at: str,
    latency_ms: int,
    attempt_count: int,
    retry_history: Sequence[Mapping[str, Any]],
    result: Optional[GenerationResult],
    error: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    request_hash = sha256_json(request_record)
    response_text = result.response_text if result is not None else ""
    reasoning_text = result.reasoning_text if result is not None else ""
    raw_response = result.raw_response if result is not None else None
    usage = dict(result.usage) if result is not None else {}
    response_truncated = bool(
        result is not None and response_was_truncated(result.finish_reason)
    )
    if result is not None and response_truncated and error is None:
        error = {
            "type": "TruncatedJudgeResponseError",
            "message": "judge completion ended at its output-token limit",
            "status_code": None,
            "retryable": False,
            "retry_after_seconds": None,
            "response_body": {"finish_reason": result.finish_reason},
        }
    elif result is not None and not response_text.strip() and error is None:
        error = {
            "type": "EmptyJudgeFinalResponseError",
            "message": "judge completion contains no importable final response text",
            "status_code": None,
            "retryable": False,
            "retry_after_seconds": None,
            "response_body": {"finish_reason": result.finish_reason},
        }
    record_status = "success" if result is not None and error is None else "error"
    return {
        "judge_response_schema_version": RECORD_SCHEMA_VERSION,
        "run_id": run_id,
        "evaluation_phase": EVALUATION_PHASE,
        "scoring_status": SCORING_STATUS,
        "publication_status": PUBLICATION_STATUS,
        "formal_comparison_allowed": False,
        "record_key": make_record_key(request_record, provider_name, model, parameters),
        "request_id": request_record["request_id"],
        "evaluation_id": request_record["evaluation_id"],
        "tested_question_id": request_record.get("tested_question_id"),
        "tested_response_sha256": request_record.get("tested_response_sha256"),
        "judge_id": request_record["judge"].get("judge_id"),
        "scorer_type": "ai",
        "provider": provider_name,
        "model": model,
        "status": record_status,
        "attempt_count": attempt_count,
        "request_started_at": request_started_at,
        "response_received_at": response_received_at,
        "latency_ms": latency_ms,
        "request": {
            "source_request_id": request_record["request_id"],
            "source_request_sha256": request_hash,
            "parameters": dict(parameters),
        },
        "response_text": response_text,
        "final_response_text": response_text,
        "reasoning_text": reasoning_text,
        "reasoning_excluded_from_scoring": True,
        "raw_response": raw_response,
        "usage": usage,
        "finish_reason": result.finish_reason if result is not None else None,
        "response_truncated": response_truncated,
        "returned_model": result.returned_model if result is not None else None,
        "error": dict(error) if error is not None else None,
        "retry_history": list(retry_history),
        "hashes": {
            "judge_request_sha256": request_hash,
            "raw_response_sha256": sha256_json(raw_response)
            if raw_response is not None
            else None,
            "response_text_sha256": sha256_text(response_text),
            "final_response_text_sha256": sha256_text(response_text),
            "reasoning_text_sha256": sha256_text(reasoning_text),
        },
    }


def run_command(
    args: argparse.Namespace,
    *,
    environ: Optional[Mapping[str, str]] = None,
    provider_factory: Callable[..., BaseProvider] = build_provider,
) -> int:
    validate_runtime_arguments(args)
    input_path = Path(args.input).resolve()
    all_requests = load_judge_requests(input_path)
    selected_requests = filter_requests(all_requests, args.request_id, args.limit_requests)
    matrix = provider_models(selected_requests)
    overrides = global_parameter_overrides(args)
    # Validate every effective request before dry-run or any network call.
    for request_record in selected_requests:
        effective_parameters(request_record, overrides)

    if not args.execute:
        print(
            json.dumps(
                {
                    "dry_run": True,
                    "network_requests_made": 0,
                    "evaluation_phase": EVALUATION_PHASE,
                    "scoring_status": SCORING_STATUS,
                    "input": {
                        "path": display_path(input_path),
                        "sha256": file_sha256(input_path),
                        "selected_request_count": len(selected_requests),
                    },
                    "provider_models": matrix,
                    "generation_parameter_overrides": overrides,
                    "planned_generation_request_count": len(selected_requests),
                    "output_would_be": display_path(Path(args.output_dir)),
                    "notice": (
                        "AI judge results are preliminary, exclude reasoning from scoring, "
                        "and do not count as independent human ratings."
                    ),
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    env = environ if environ is not None else os.environ
    providers = {
        provider_name: provider_factory(provider_name, environ=env)
        for provider_name in matrix
    }
    output_dir = Path(args.output_dir).resolve()
    validate_output_directory(output_dir, args.resume)
    output_dir.mkdir(parents=True, exist_ok=True)
    input_hash = file_sha256(input_path)
    manifest_path = output_dir / MANIFEST_FILENAME
    records_path = output_dir / RECORDS_FILENAME
    existing_records = read_existing_records(records_path) if args.resume else []

    if args.resume:
        existing_manifest = load_resume_manifest(manifest_path, input_hash)
        validate_resume_configuration(
            existing_manifest,
            requested_run_id=args.run_id,
            selected_requests=selected_requests,
            matrix=matrix,
            parameter_overrides=overrides,
        )
        run_id = str(existing_manifest["run_id"])
        created_at = str(existing_manifest.get("created_at") or utc_now())
    else:
        run_id = args.run_id or (
            "judge-pilot-%s-%s"
            % (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"), uuid.uuid4().hex[:8])
        )
        if not RUN_ID_PATTERN.fullmatch(run_id):
            raise ValueError("run id must match %s" % RUN_ID_PATTERN.pattern)
        created_at = utc_now()

    manifest = create_manifest(
        run_id=run_id,
        created_at=created_at,
        input_path=input_path,
        input_sha256=input_hash,
        selected_requests=selected_requests,
        matrix=matrix,
        parameter_overrides=overrides,
    )
    completed = completed_record_keys(
        existing_records, skip_errors=args.skip_recorded_errors
    )
    intervals = parse_provider_intervals(args.provider_min_interval, args.min_interval)
    limiters = {name: RateLimiter(intervals[name]) for name in matrix}
    skipped = 0
    write_json_atomic(manifest_path, manifest)

    interrupted = False
    unexpected_failure = False
    try:
        for request_record in selected_requests:
            judge = request_record["judge"]
            provider_name = str(judge["provider"])
            model = str(judge["model"])
            provider = providers[provider_name]
            parameters = effective_parameters(request_record, overrides)
            record_key = make_record_key(request_record, provider_name, model, parameters)
            if record_key in completed:
                skipped += 1
                continue
            request_body = request_record["request"]
            started_text = utc_now()
            started_clock = time.monotonic()
            result: Optional[GenerationResult] = None
            error: Optional[Mapping[str, Any]] = None
            attempt_count = 0
            retry_history: Sequence[Mapping[str, Any]] = []
            try:
                result, attempt_count, retry_history = call_with_retry(
                    lambda: provider.generate(
                        model,
                        system_prompt=str(request_body["system_prompt"]),
                        user_prompt=str(request_body["user_prompt"]),
                        parameters=parameters,
                        timeout=args.timeout,
                    ),
                    provider=provider,
                    max_attempts=args.max_attempts,
                    initial_backoff_seconds=args.initial_backoff,
                    max_backoff_seconds=args.max_backoff,
                    before_attempt=limiters[provider_name].wait,
                )
            except RetryFailure as exc:
                attempt_count = exc.attempt_count
                retry_history = exc.retry_history
                error = exc.safe_error
            completed_text = utc_now()
            latency_ms = max(0, int(round((time.monotonic() - started_clock) * 1000)))
            response_record = build_response_record(
                run_id=run_id,
                request_record=request_record,
                provider_name=provider_name,
                model=model,
                parameters=parameters,
                request_started_at=started_text,
                response_received_at=completed_text,
                latency_ms=latency_ms,
                attempt_count=attempt_count,
                retry_history=retry_history,
                result=result,
                error=error,
            )
            append_jsonl(records_path, response_record)
            existing_records.append(response_record)
            if response_record["status"] == "success":
                completed.add(record_key)
            counts = summarize_records(existing_records)
            manifest.update(counts)
            manifest["skipped_existing_count"] = skipped
            manifest["run_status"] = "running"
            write_json_atomic(manifest_path, manifest)
    except KeyboardInterrupt:
        interrupted = True
    except BaseException:
        unexpected_failure = True
        raise
    finally:
        counts = summarize_records(existing_records)
        manifest.update(counts)
        manifest["skipped_existing_count"] = skipped
        manifest["completed_at"] = utc_now()
        manifest["run_status"] = (
            "interrupted"
            if interrupted
            else (
                "failed"
                if unexpected_failure
                else ("completed_with_errors" if counts["error_count"] else "completed")
            )
        )
        write_json_atomic(manifest_path, manifest)

    print(
        json.dumps(
            {
                "run_id": run_id,
                "output_dir": display_path(output_dir),
                "records_file": RECORDS_FILENAME,
                "manifest_file": MANIFEST_FILENAME,
                "planned_count": len(selected_requests),
                "skipped_existing_count": skipped,
                **summarize_records(existing_records),
                "run_status": manifest["run_status"],
                "notice": (
                    "AI judge results are preliminary and cannot replace two independent human scorers."
                ),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    if interrupted:
        return 130
    return 1 if summarize_records(existing_records)["error_count"] else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Execute generated AI judge requests with dry-run safety and resumable audit records."
    )
    parser.add_argument("--input", required=True, help="judge requests JSONL")
    parser.add_argument("--output-dir", required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--execute", action="store_true", help="explicitly allow authenticated network requests"
    )
    mode.add_argument("--dry-run", action="store_true", help="plan only (also the default)")
    parser.add_argument("--request-id", action="append", help="run only this request id")
    parser.add_argument("--limit-requests", type=int)
    parser.add_argument("--run-id")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--skip-recorded-errors",
        action="store_true",
        help="on resume, skip prior error records instead of retrying them",
    )
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--max-attempts", type=int, default=4)
    parser.add_argument("--initial-backoff", type=float, default=2.0)
    parser.add_argument("--max-backoff", type=float, default=30.0)
    parser.add_argument("--min-interval", type=float, default=1.0)
    parser.add_argument(
        "--provider-min-interval",
        action="append",
        help="provider-specific interval as PROVIDER=SECONDS",
    )
    parser.add_argument("--temperature", type=float)
    parser.add_argument("--top-p", type=float)
    parser.add_argument("--top-k", type=int)
    parser.add_argument("--max-tokens", type=int)
    parser.add_argument("--seed", type=int)
    parser.add_argument(
        "--deepseek-thinking",
        choices=("enabled", "disabled"),
        help=(
            "set DeepSeek thinking.type explicitly; omitted by default to retain "
            "the provider/model default (never sent to other providers)"
        ),
    )
    minimax_stream_group = parser.add_mutually_exclusive_group()
    minimax_stream_group.add_argument(
        "--minimax-stream",
        dest="minimax_stream",
        action="store_true",
        default=True,
        help=(
            "use MiniMax's auditable OpenAI-compatible SSE stream (default; "
            "never changes DeepSeek, Volcengine, or Gemini transport)"
        ),
    )
    minimax_stream_group.add_argument(
        "--no-minimax-stream",
        dest="minimax_stream",
        action="store_false",
        help="use MiniMax's non-streaming response mode for compatibility checks",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    configure_utf8_stdio()
    args = build_parser().parse_args(argv)
    try:
        return run_command(args)
    except (ValueError, ProviderRequestError, RetryFailure, OSError, json.JSONDecodeError) as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

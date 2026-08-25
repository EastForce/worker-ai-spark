#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reproducible multi-provider runner for the 24-question pilot benchmark.

The safe default is a dry run.  Supplying ``--execute`` is required before any
network request is made.  API credentials are read from environment variables;
this script never reads credential files and never persists request headers.

Outputs are deliberately labelled ``pilot`` and ``unscored``.  Human or offline
scoring is a separate step and must not silently turn these records into formal
evaluation claims.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
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
    ModelInfo,
    ProviderRequestError,
    build_provider,
    canonical_json,
)


SCRIPT_DIR = Path(__file__).resolve().parent
REPOSITORY_ROOT = SCRIPT_DIR.parent
DEFAULT_INPUT = REPOSITORY_ROOT / "benchmarks" / "first-batch.zh-CN.jsonl"
RECORDS_FILENAME = "records.jsonl"
MANIFEST_FILENAME = "run-manifest.json"
RECORD_SCHEMA_VERSION = "1.1"
MANIFEST_SCHEMA_VERSION = "1.1"
EVALUATION_PHASE = "pilot"
SCORING_STATUS = "unscored"
PUBLICATION_STATUS = "review_required"
PROMPT_TEMPLATE = "情境：\\n{scenario}\\n\\n问题：\\n{prompt}"
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def sha256_json(value: Any) -> str:
    return sha256_text(canonical_json(value))


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def display_path(path: Path) -> str:
    """Avoid persisting user/home paths while retaining repo-relative identity."""

    resolved = path.resolve()
    try:
        return resolved.relative_to(REPOSITORY_ROOT).as_posix()
    except ValueError:
        return resolved.name


def load_questions(path: Path) -> List[Dict[str, Any]]:
    if not path.is_file():
        raise ValueError("benchmark JSONL does not exist: %s" % display_path(path))
    records: List[Dict[str, Any]] = []
    seen_ids = set()
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    "benchmark line %d is not valid JSON: %s" % (line_number, exc)
                ) from exc
            if not isinstance(record, dict):
                raise ValueError("benchmark line %d must be a JSON object" % line_number)
            missing = [
                key
                for key in ("id", "version", "status", "scenario", "prompt")
                if not isinstance(record.get(key), str) or not record[key].strip()
            ]
            if missing:
                raise ValueError(
                    "benchmark line %d is missing non-empty fields: %s"
                    % (line_number, ", ".join(missing))
                )
            if record["id"] in seen_ids:
                raise ValueError("duplicate benchmark id: %s" % record["id"])
            seen_ids.add(record["id"])
            records.append(record)
    if not records:
        raise ValueError("benchmark JSONL contains no questions")
    return records


def build_user_prompt(question: Mapping[str, Any]) -> str:
    """Use only the public scenario and primary question; do not leak the rubric."""

    return "情境：\n%s\n\n问题：\n%s" % (
        str(question["scenario"]).strip(),
        str(question["prompt"]).strip(),
    )


def parse_provider_names(values: Optional[Sequence[str]]) -> List[str]:
    return list(dict.fromkeys(values)) if values else list(PROVIDER_NAMES)


def parse_model_assignments(values: Optional[Sequence[str]]) -> Dict[str, List[str]]:
    result: Dict[str, List[str]] = {name: [] for name in PROVIDER_NAMES}
    for value in values or []:
        if "=" not in value:
            raise ValueError("model must use PROVIDER=MODEL format: %r" % value)
        provider, model = value.split("=", 1)
        provider = provider.strip().lower()
        model = model.strip()
        if provider not in result:
            raise ValueError("unsupported provider in model assignment: %s" % provider)
        if not model:
            raise ValueError("model id must not be empty")
        if model not in result[provider]:
            result[provider].append(model)
    return result


def parse_provider_intervals(
    values: Optional[Sequence[str]], default_value: float
) -> Dict[str, float]:
    result = {name: default_value for name in PROVIDER_NAMES}
    for value in values or []:
        if "=" not in value:
            raise ValueError("rate limit must use PROVIDER=SECONDS format: %r" % value)
        provider, raw_seconds = value.split("=", 1)
        provider = provider.strip().lower()
        if provider not in result:
            raise ValueError("unsupported provider in rate limit: %s" % provider)
        try:
            seconds = float(raw_seconds)
        except ValueError as exc:
            raise ValueError("rate-limit seconds must be numeric: %r" % raw_seconds) from exc
        if seconds < 0:
            raise ValueError("rate-limit seconds must be non-negative")
        result[provider] = seconds
    return result


def unique(values: Iterable[str]) -> List[str]:
    result = []
    seen = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


class RateLimiter:
    """Per-provider minimum-interval limiter, including retry attempts."""

    def __init__(
        self,
        interval_seconds: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.interval_seconds = max(0.0, float(interval_seconds))
        self._clock = clock
        self._sleep = sleep
        self._last_started: Optional[float] = None

    def wait(self) -> None:
        now = self._clock()
        if self._last_started is not None:
            remaining = self.interval_seconds - (now - self._last_started)
            if remaining > 0:
                self._sleep(remaining)
                now = self._clock()
        self._last_started = now


class RetryFailure(RuntimeError):
    def __init__(
        self,
        safe_error: Mapping[str, Any],
        attempt_count: int,
        retry_history: Sequence[Mapping[str, Any]],
    ) -> None:
        super().__init__(str(safe_error.get("message", "provider request failed")))
        self.safe_error = dict(safe_error)
        self.attempt_count = attempt_count
        self.retry_history = list(retry_history)


def call_with_retry(
    operation: Callable[[], Any],
    *,
    provider: BaseProvider,
    max_attempts: int,
    initial_backoff_seconds: float,
    max_backoff_seconds: float,
    before_attempt: Optional[Callable[[], None]] = None,
    sleep: Callable[[float], None] = time.sleep,
) -> Tuple[Any, int, List[Mapping[str, Any]]]:
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    history: List[Mapping[str, Any]] = []
    for attempt in range(1, max_attempts + 1):
        if before_attempt is not None:
            before_attempt()
        try:
            return operation(), attempt, history
        except Exception as exc:  # provider boundary: always persist a safe failure
            safe_error = provider.safe_error(exc)
            retryable = bool(safe_error.get("retryable"))
            history.append(
                {
                    "attempt": attempt,
                    "at": utc_now(),
                    "error": safe_error,
                }
            )
            if not retryable or attempt >= max_attempts:
                raise RetryFailure(safe_error, attempt, history) from None
            exponential = initial_backoff_seconds * (2 ** (attempt - 1))
            retry_after = safe_error.get("retry_after_seconds")
            if isinstance(retry_after, (int, float)):
                exponential = max(exponential, float(retry_after))
            sleep(min(max_backoff_seconds, max(0.0, exponential)))
    raise AssertionError("retry loop exited unexpectedly")


def discover_models(
    provider: BaseProvider,
    *,
    timeout: float,
    max_attempts: int,
    initial_backoff: float,
    max_backoff: float,
) -> Tuple[List[ModelInfo], int, List[Mapping[str, Any]]]:
    value, attempts, history = call_with_retry(
        lambda: provider.list_models(timeout=timeout),
        provider=provider,
        max_attempts=max_attempts,
        initial_backoff_seconds=initial_backoff,
        max_backoff_seconds=max_backoff,
    )
    return list(value), attempts, history


def resolve_models(
    providers: Mapping[str, BaseProvider],
    provider_names: Sequence[str],
    explicit: Mapping[str, Sequence[str]],
    *,
    all_models: bool,
    execute: bool,
    environ: Mapping[str, str],
    timeout: float,
    max_attempts: int,
    initial_backoff: float,
    max_backoff: float,
) -> Tuple[Dict[str, List[str]], List[str]]:
    resolved: Dict[str, List[str]] = {}
    discovery_required: List[str] = []
    for name in provider_names:
        provider = providers[name]
        selected = list(explicit.get(name, []))
        # An explicit CLI assignment is authoritative.  Silently appending an
        # inherited *_MODELS value can multiply paid requests beyond the
        # reviewed command line.
        if not selected:
            selected.extend(provider.configured_models(environ))
        if all_models:
            if execute:
                model_infos, _, _ = discover_models(
                    provider,
                    timeout=timeout,
                    max_attempts=max_attempts,
                    initial_backoff=initial_backoff,
                    max_backoff=max_backoff,
                )
                selected.extend(
                    model.id for model in model_infos if model.supports_generation
                )
            else:
                discovery_required.append(name)
        resolved[name] = unique(selected)
        if execute and not resolved[name]:
            raise ValueError(
                "no models selected for %s; use --model %s=MODEL, set %s, or use --all-models"
                % (name, name, provider.models_env)
            )
    return resolved, discovery_required


def read_system_prompt(args: argparse.Namespace) -> str:
    if getattr(args, "system_prompt_file", None):
        path = Path(args.system_prompt_file)
        if not path.is_file():
            raise ValueError("system prompt file does not exist: %s" % path.name)
        return path.read_text(encoding="utf-8")
    return getattr(args, "system_prompt", "") or ""


def make_record_key(
    provider: str,
    model: str,
    question_id: str,
    question_sha256: str,
    request_sha256: str,
) -> str:
    return sha256_json(
        {
            "provider": provider,
            "model": model,
            "question_id": question_id,
            "question_sha256": question_sha256,
            "request_sha256": request_sha256,
        }
    )


def read_existing_records(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    records: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, start=1):
            if not raw.strip():
                continue
            try:
                record = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    "existing records line %d is invalid JSON; preserve it and repair manually: %s"
                    % (line_number, exc)
                ) from exc
            if not isinstance(record, dict):
                raise ValueError("existing records line %d is not an object" % line_number)
            records.append(record)
    return records


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


def summarize_records(records: Sequence[Mapping[str, Any]]) -> Dict[str, int]:
    """Summarise the latest record for every deterministic record key."""

    latest: Dict[str, Mapping[str, Any]] = {}
    anonymous = 0
    for record in records:
        key = record.get("record_key")
        if isinstance(key, str):
            latest[key] = record
        else:
            anonymous += 1
    return {
        "unique_record_count": len(latest) + anonymous,
        "success_count": sum(1 for item in latest.values() if item.get("status") == "success"),
        "error_count": sum(1 for item in latest.values() if item.get("status") == "error"),
    }


def write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(".%s.%s.tmp" % (path.name, uuid.uuid4().hex))
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def append_jsonl(path: Path, value: Mapping[str, Any]) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(canonical_json(value) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def validate_output_directory(output_dir: Path, resume: bool) -> None:
    if output_dir.exists() and not output_dir.is_dir():
        raise ValueError("output path exists and is not a directory")
    if output_dir.exists() and not resume:
        try:
            next(output_dir.iterdir())
        except StopIteration:
            return
        raise ValueError(
            "output directory is not empty; choose a new directory or use --resume"
        )


def load_resume_manifest(path: Path, input_sha256: str) -> Mapping[str, Any]:
    if not path.is_file():
        raise ValueError("--resume requires an existing %s" % MANIFEST_FILENAME)
    with path.open("r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    if not isinstance(manifest, dict):
        raise ValueError("existing manifest must be a JSON object")
    input_info = manifest.get("input")
    if not isinstance(input_info, Mapping):
        raise ValueError("existing manifest input metadata must be a JSON object")
    existing_hash = input_info.get("sha256")
    if existing_hash != input_sha256:
        raise ValueError("resume input SHA-256 does not match the existing manifest")
    if manifest.get("evaluation_phase") != EVALUATION_PHASE:
        raise ValueError("existing manifest is not a pilot run")
    return manifest


def validate_resume_configuration(
    manifest: Mapping[str, Any],
    *,
    requested_run_id: Optional[str],
    questions: Sequence[Mapping[str, Any]],
    provider_models: Mapping[str, Sequence[str]],
    parameters: Mapping[str, Any],
    system_prompt: str,
    planned_count: int,
) -> None:
    """Reject any resume command that would change the original run matrix."""

    mismatches: List[str] = []
    input_info = manifest.get("input")
    if not isinstance(input_info, Mapping):
        mismatches.append("input")
        input_info = {}

    expected_question_ids = [str(question["id"]) for question in questions]
    if input_info.get("selected_question_ids") != expected_question_ids:
        mismatches.append("selected question ids/order")
    if input_info.get("question_count") != len(expected_question_ids):
        mismatches.append("selected question count")

    expected_provider_models = {
        provider: list(models) for provider, models in provider_models.items()
    }
    if manifest.get("provider_models") != expected_provider_models:
        mismatches.append("provider/model matrix")
    if manifest.get("provider_order") != list(provider_models.keys()):
        mismatches.append("provider order")

    prompt_info = manifest.get("prompt")
    expected_prompt = {
        "template": PROMPT_TEMPLATE,
        "system_prompt": system_prompt,
        "follow_up_questions_included": False,
    }
    if not isinstance(prompt_info, Mapping) or any(
        prompt_info.get(key) != value for key, value in expected_prompt.items()
    ):
        mismatches.append("prompt configuration")
    if manifest.get("generation_parameters") != dict(parameters):
        mismatches.append("generation parameters")
    if manifest.get("planned_count") != planned_count:
        mismatches.append("planned request count")

    existing_run_id = manifest.get("run_id")
    if not isinstance(existing_run_id, str) or not RUN_ID_PATTERN.fullmatch(
        existing_run_id
    ):
        mismatches.append("run id")
    elif requested_run_id is not None and requested_run_id != existing_run_id:
        mismatches.append("requested run id")

    if manifest.get("manifest_schema_version") != MANIFEST_SCHEMA_VERSION:
        mismatches.append("manifest schema version")
    if manifest.get("scoring_status") != SCORING_STATUS:
        mismatches.append("scoring status")
    if manifest.get("records_file") != RECORDS_FILENAME:
        mismatches.append("records file")

    if mismatches:
        raise ValueError(
            "resume configuration does not match the existing manifest (%s); "
            "use the original arguments or choose a new output directory"
            % ", ".join(mismatches)
        )


def create_manifest(
    *,
    run_id: str,
    created_at: str,
    input_path: Path,
    input_sha256: str,
    question_count: int,
    selected_question_ids: Sequence[str],
    provider_models: Mapping[str, Sequence[str]],
    parameters: Mapping[str, Any],
    system_prompt: str,
    planned_count: int,
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
            "question_count": question_count,
            "selected_question_ids": list(selected_question_ids),
        },
        "provider_order": list(provider_models.keys()),
        "provider_models": {
            provider: list(models) for provider, models in provider_models.items()
        },
        "prompt": {
            "template": PROMPT_TEMPLATE,
            "system_prompt": system_prompt,
            "follow_up_questions_included": False,
        },
        "generation_parameters": dict(parameters),
        "records_file": RECORDS_FILENAME,
        "planned_count": planned_count,
        "unique_record_count": 0,
        "success_count": 0,
        "error_count": 0,
        "skipped_existing_count": 0,
        "limitations": [
            "pilot run; results are unscored until separately reviewed",
            "benchmark questions currently retain their repository status",
            "provider-side model revisions and hidden system settings may be unavailable",
        ],
    }


def response_was_truncated(finish_reason: Optional[str]) -> bool:
    if not isinstance(finish_reason, str):
        return False
    normalised = re.sub(r"[^a-z0-9]+", "_", finish_reason.strip().lower()).strip("_")
    return normalised in {"length", "max_tokens", "max_output_tokens", "token_limit"}


def build_evaluation_record(
    *,
    run_id: str,
    provider_name: str,
    model: str,
    question: Mapping[str, Any],
    system_prompt: str,
    user_prompt: str,
    parameters: Mapping[str, Any],
    request_started_at: str,
    response_received_at: str,
    latency_ms: int,
    attempt_count: int,
    retry_history: Sequence[Mapping[str, Any]],
    result: Optional[GenerationResult],
    error: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    question_hash = sha256_json(question)
    persisted_request = {
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
        "parameters": dict(parameters),
    }
    request_hash = sha256_json(
        {
            "provider": provider_name,
            "model": model,
            **persisted_request,
        }
    )
    record_key = make_record_key(
        provider_name,
        model,
        str(question["id"]),
        question_hash,
        request_hash,
    )
    response_text = result.response_text if result is not None else ""
    reasoning_text = result.reasoning_text if result is not None else ""
    raw_response = result.raw_response if result is not None else None
    usage = dict(result.usage) if result is not None else {}
    response_truncated = bool(
        result is not None and response_was_truncated(result.finish_reason)
    )
    if result is not None and response_truncated and error is None:
        error = {
            "type": "TruncatedResponseError",
            "message": "provider completion ended at its output-token limit",
            "status_code": None,
            "retryable": False,
            "retry_after_seconds": None,
            "response_body": {"finish_reason": result.finish_reason},
        }
    elif result is not None and not response_text.strip() and error is None:
        error = {
            "type": "EmptyFinalResponseError",
            "message": "provider completion contains no scoreable final response text",
            "status_code": None,
            "retryable": False,
            "retry_after_seconds": None,
            "response_body": {"finish_reason": result.finish_reason},
        }
    record_status = "success" if result is not None and error is None else "error"
    return {
        "record_schema_version": RECORD_SCHEMA_VERSION,
        "run_id": run_id,
        "evaluation_phase": EVALUATION_PHASE,
        "scoring_status": SCORING_STATUS,
        "publication_status": PUBLICATION_STATUS,
        "record_key": record_key,
        "provider": provider_name,
        "model": model,
        "returned_model": result.returned_model if result is not None else None,
        "question_id": question["id"],
        "question_version": question["version"],
        "question_status": question["status"],
        "status": record_status,
        "attempt_count": attempt_count,
        "request_started_at": request_started_at,
        "response_received_at": response_received_at,
        "latency_ms": latency_ms,
        "request": persisted_request,
        "response_text": response_text,
        "final_response_text": response_text,
        "reasoning_text": reasoning_text,
        "raw_response": raw_response,
        "usage": usage,
        "usage_missing": result.usage_missing if result is not None else None,
        "finish_reason": result.finish_reason if result is not None else None,
        "response_truncated": response_truncated,
        "error": dict(error) if error is not None else None,
        "retry_history": list(retry_history),
        "hashes": {
            "question_sha256": question_hash,
            "request_sha256": request_hash,
            "raw_response_sha256": sha256_json(raw_response)
            if raw_response is not None
            else None,
            "response_text_sha256": sha256_text(response_text),
            "final_response_text_sha256": sha256_text(response_text),
            "reasoning_text_sha256": sha256_text(reasoning_text),
        },
    }


def filter_questions(
    questions: Sequence[Dict[str, Any]], ids: Optional[Sequence[str]], limit: Optional[int]
) -> List[Dict[str, Any]]:
    selected = list(questions)
    if ids:
        wanted = set(ids)
        selected = [question for question in selected if question["id"] in wanted]
        found = {question["id"] for question in selected}
        missing = sorted(wanted - found)
        if missing:
            raise ValueError("unknown question ids: %s" % ", ".join(missing))
    if limit is not None:
        if limit < 1:
            raise ValueError("--limit-questions must be at least 1")
        selected = selected[:limit]
    return selected


def generation_parameters(
    args: argparse.Namespace,
    provider_names: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    values = {
        "temperature": args.temperature,
        "top_p": args.top_p,
        "top_k": args.top_k,
        "max_tokens": args.max_tokens,
        "seed": args.seed,
        "deepseek_thinking": args.deepseek_thinking,
        "minimax_stream": args.minimax_stream,
    }
    # Keep old non-Gemini run hashes resumable while freezing this provider-only
    # transport switch whenever Gemini is actually present in the matrix.
    if provider_names is None or "gemini" in provider_names:
        values["gemini_stream"] = args.gemini_stream
    return {key: value for key, value in values.items() if value is not None}


def run_command(
    args: argparse.Namespace,
    *,
    environ: Optional[Mapping[str, str]] = None,
    provider_factory: Callable[..., BaseProvider] = build_provider,
) -> int:
    env = environ if environ is not None else os.environ
    input_path = Path(args.input).resolve()
    all_questions = load_questions(input_path)
    questions = filter_questions(all_questions, args.question_id, args.limit_questions)
    provider_names = parse_provider_names(args.provider)
    unfiltered_catalog_providers = [
        name for name in provider_names if name != "gemini"
    ]
    if (
        args.execute
        and args.all_models
        and unfiltered_catalog_providers
        and not getattr(args, "confirm_unfiltered_openai_models", False)
    ):
        raise ValueError(
            "--all-models catalogs for OpenAI-compatible providers are not a "
            "reviewed text-model whitelist (%s); use explicit --model assignments "
            "or add --confirm-unfiltered-openai-models after reviewing list-models"
            % ", ".join(unfiltered_catalog_providers)
        )
    providers = {
        name: provider_factory(name, environ=env) for name in provider_names
    }
    explicit_models = parse_model_assignments(args.model)
    models, discovery_required = resolve_models(
        providers,
        provider_names,
        explicit_models,
        all_models=args.all_models,
        execute=args.execute,
        environ=env,
        timeout=args.timeout,
        max_attempts=args.max_attempts,
        initial_backoff=args.initial_backoff,
        max_backoff=args.max_backoff,
    )
    system_prompt = read_system_prompt(args)
    parameters = generation_parameters(args, provider_names)
    planned_count = sum(len(models[name]) * len(questions) for name in provider_names)

    if not args.execute:
        summary = {
            "dry_run": True,
            "network_requests_made": 0,
            "evaluation_phase": EVALUATION_PHASE,
            "scoring_status": SCORING_STATUS,
            "input": {
                "path": display_path(input_path),
                "sha256": file_sha256(input_path),
                "selected_question_count": len(questions),
            },
            "provider_models": models,
            "generation_parameters": dict(parameters),
            "model_discovery_required_on_execute": discovery_required,
            "planned_generation_request_count": planned_count
            if not discovery_required
            else None,
            "output_would_be": display_path(Path(args.output_dir)),
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

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
            questions=questions,
            provider_models=models,
            parameters=parameters,
            system_prompt=system_prompt,
            planned_count=planned_count,
        )
        run_id = str(existing_manifest["run_id"])
        manifest = dict(existing_manifest)
        manifest["completed_at"] = None
        manifest["run_status"] = "running"
    else:
        run_id = args.run_id or (
            "pilot-%s-%s"
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
            question_count=len(questions),
            selected_question_ids=[str(question["id"]) for question in questions],
            provider_models=models,
            parameters=parameters,
            system_prompt=system_prompt,
            planned_count=planned_count,
        )
    completed = completed_record_keys(
        existing_records, skip_errors=args.skip_recorded_errors
    )
    intervals = parse_provider_intervals(
        args.provider_min_interval, args.min_interval
    )
    limiters = {name: RateLimiter(intervals[name]) for name in provider_names}
    skipped = 0
    write_json_atomic(manifest_path, manifest)

    interrupted = False
    unexpected_failure = False
    try:
        for provider_name in provider_names:
            provider = providers[provider_name]
            for model in models[provider_name]:
                for question in questions:
                    user_prompt = build_user_prompt(question)
                    question_hash = sha256_json(question)
                    persisted_request = {
                        "system_prompt": system_prompt,
                        "user_prompt": user_prompt,
                        "parameters": parameters,
                    }
                    request_hash = sha256_json(
                        {
                            "provider": provider_name,
                            "model": model,
                            **persisted_request,
                        }
                    )
                    record_key = make_record_key(
                        provider_name,
                        model,
                        question["id"],
                        question_hash,
                        request_hash,
                    )
                    if record_key in completed:
                        skipped += 1
                        continue

                    started_text = utc_now()
                    started_clock = time.monotonic()
                    result: Optional[GenerationResult] = None
                    safe_error: Optional[Mapping[str, Any]] = None
                    retry_history: List[Mapping[str, Any]] = []
                    attempt_count = 0
                    try:
                        value, attempt_count, retry_history = call_with_retry(
                            lambda: provider.generate(
                                model,
                                system_prompt=system_prompt,
                                user_prompt=user_prompt,
                                parameters=parameters,
                                timeout=args.timeout,
                            ),
                            provider=provider,
                            max_attempts=args.max_attempts,
                            initial_backoff_seconds=args.initial_backoff,
                            max_backoff_seconds=args.max_backoff,
                            before_attempt=limiters[provider_name].wait,
                        )
                        result = value
                    except RetryFailure as exc:
                        attempt_count = exc.attempt_count
                        retry_history = exc.retry_history
                        safe_error = exc.safe_error
                    received_text = utc_now()
                    latency_ms = max(
                        0, int(round((time.monotonic() - started_clock) * 1000))
                    )
                    record = build_evaluation_record(
                        run_id=run_id,
                        provider_name=provider_name,
                        model=model,
                        question=question,
                        system_prompt=system_prompt,
                        user_prompt=user_prompt,
                        parameters=parameters,
                        request_started_at=started_text,
                        response_received_at=received_text,
                        latency_ms=latency_ms,
                        attempt_count=attempt_count,
                        retry_history=retry_history,
                        result=result,
                        error=safe_error,
                    )
                    append_jsonl(records_path, record)
                    existing_records.append(record)
                    if record["status"] == "success":
                        completed.add(record_key)
                    print(
                        "%s %s %s %s"
                        % (record["status"], provider_name, model, question["id"]),
                        flush=True,
                    )
    except KeyboardInterrupt:
        interrupted = True
    except Exception:
        unexpected_failure = True
        raise
    finally:
        totals = summarize_records(existing_records)
        manifest.update(totals)
        manifest["skipped_existing_count"] = skipped
        manifest["completed_at"] = utc_now()
        if interrupted:
            manifest["run_status"] = "interrupted"
        elif unexpected_failure:
            manifest["run_status"] = "failed"
        elif totals["error_count"]:
            manifest["run_status"] = (
                "failed" if totals["success_count"] == 0 else "completed_with_errors"
            )
        else:
            manifest["run_status"] = "completed"
        write_json_atomic(manifest_path, manifest)

    if interrupted:
        print("Run interrupted safely; use --resume with the same output directory.", file=sys.stderr)
        return 130
    return 1 if totals["error_count"] else 0


def list_models_command(
    args: argparse.Namespace,
    *,
    environ: Optional[Mapping[str, str]] = None,
    provider_factory: Callable[..., BaseProvider] = build_provider,
) -> int:
    env = environ if environ is not None else os.environ
    provider_names = parse_provider_names(args.provider)
    if not args.execute:
        print(
            json.dumps(
                {
                    "dry_run": True,
                    "network_requests_made": 0,
                    "operation": "list-models",
                    "providers": provider_names,
                    "execute_hint": "add --execute to make authenticated catalog requests",
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    output: Dict[str, Any] = {}
    failed = False
    for name in provider_names:
        provider = provider_factory(name, environ=env)
        try:
            models, attempts, retry_history = discover_models(
                provider,
                timeout=args.timeout,
                max_attempts=args.max_attempts,
                initial_backoff=args.initial_backoff,
                max_backoff=args.max_backoff,
            )
            output[name] = {
                "status": "success",
                "attempt_count": attempts,
                "models": [
                    {
                        "id": model.id,
                        "supports_generation": model.supports_generation,
                        "display_name": model.display_name,
                    }
                    for model in models
                ],
                "retry_history": retry_history,
            }
        except RetryFailure as exc:
            failed = True
            output[name] = {
                "status": "error",
                "attempt_count": exc.attempt_count,
                "error": exc.safe_error,
                "retry_history": exc.retry_history,
            }
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if failed else 0


def probe_command(
    args: argparse.Namespace,
    *,
    environ: Optional[Mapping[str, str]] = None,
    provider_factory: Callable[..., BaseProvider] = build_provider,
) -> int:
    """Probe authentication/catalog reachability without generating tokens."""

    env = environ if environ is not None else os.environ
    provider_names = parse_provider_names(args.provider)
    if not args.execute:
        print(
            json.dumps(
                {
                    "dry_run": True,
                    "network_requests_made": 0,
                    "operation": "probe",
                    "probe_type": "authenticated_model_catalog_only",
                    "providers": provider_names,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    results: Dict[str, Any] = {}
    failed = False
    for name in provider_names:
        provider = provider_factory(name, environ=env)
        started = utc_now()
        try:
            models, attempts, history = discover_models(
                provider,
                timeout=args.timeout,
                max_attempts=args.max_attempts,
                initial_backoff=args.initial_backoff,
                max_backoff=args.max_backoff,
            )
            results[name] = {
                "status": "reachable",
                "probe_type": "authenticated_model_catalog_only",
                "started_at": started,
                "completed_at": utc_now(),
                "attempt_count": attempts,
                "generation_capable_model_count": sum(
                    1 for model in models if model.supports_generation
                ),
                "retry_history": history,
            }
        except RetryFailure as exc:
            failed = True
            results[name] = {
                "status": "error",
                "probe_type": "authenticated_model_catalog_only",
                "started_at": started,
                "completed_at": utc_now(),
                "attempt_count": exc.attempt_count,
                "error": exc.safe_error,
                "retry_history": exc.retry_history,
            }
    print(json.dumps(results, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if failed else 0


def add_network_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--provider",
        action="append",
        choices=PROVIDER_NAMES,
        help="provider to use; repeat as needed (default: all four)",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--execute",
        action="store_true",
        help="explicitly allow authenticated network requests",
    )
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="plan only (this is also the default)",
    )
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--max-attempts", type=int, default=4)
    parser.add_argument("--initial-backoff", type=float, default=2.0)
    parser.add_argument("--max-backoff", type=float, default=30.0)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run an explicitly labelled pilot/unscored multi-model evaluation."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser(
        "list-models", help="list provider model catalogs (no generation)"
    )
    add_network_arguments(list_parser)

    probe_parser = subparsers.add_parser(
        "probe", help="check authenticated catalog reachability (no generation)"
    )
    add_network_arguments(probe_parser)

    run_parser = subparsers.add_parser("run", help="run the benchmark matrix")
    add_network_arguments(run_parser)
    run_parser.add_argument("--input", default=str(DEFAULT_INPUT))
    run_parser.add_argument("--output-dir", required=True)
    run_parser.add_argument(
        "--model",
        action="append",
        help="explicit PROVIDER=MODEL assignment; repeat as needed",
    )
    run_parser.add_argument(
        "--all-models",
        action="store_true",
        help=(
            "discover provider-reported generation-capable models; Gemini applies "
            "a text-oriented filter, while OpenAI-compatible catalogs are unfiltered"
        ),
    )
    run_parser.add_argument(
        "--confirm-unfiltered-openai-models",
        action="store_true",
        help=(
            "with --execute --all-models, explicitly acknowledge that DeepSeek, "
            "Volcengine, and MiniMax catalog entries are not a reviewed text whitelist"
        ),
    )
    prompt_group = run_parser.add_mutually_exclusive_group()
    prompt_group.add_argument("--system-prompt", default="")
    prompt_group.add_argument("--system-prompt-file")
    run_parser.add_argument(
        "--temperature",
        type=float,
        help="omit by default so every provider can apply its valid model default",
    )
    run_parser.add_argument("--top-p", type=float)
    run_parser.add_argument("--top-k", type=int)
    run_parser.add_argument("--max-tokens", type=int, default=2048)
    run_parser.add_argument("--seed", type=int)
    run_parser.add_argument(
        "--deepseek-thinking",
        choices=("enabled", "disabled"),
        help=(
            "set DeepSeek thinking.type explicitly; omitted by default to retain "
            "the provider/model default (never sent to other providers)"
        ),
    )
    minimax_stream_group = run_parser.add_mutually_exclusive_group()
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
    gemini_stream_group = run_parser.add_mutually_exclusive_group()
    gemini_stream_group.add_argument(
        "--gemini-stream",
        dest="gemini_stream",
        action="store_true",
        default=False,
        help=(
            "use Gemini's streamGenerateContent JSON SSE endpoint; disabled by "
            "default and never changes other providers"
        ),
    )
    gemini_stream_group.add_argument(
        "--no-gemini-stream",
        dest="gemini_stream",
        action="store_false",
        help="use Gemini's non-streaming generateContent endpoint (default)",
    )
    run_parser.add_argument(
        "--min-interval",
        type=float,
        default=1.0,
        help="minimum seconds between requests per provider",
    )
    run_parser.add_argument(
        "--provider-min-interval",
        action="append",
        help="override as PROVIDER=SECONDS; repeat as needed",
    )
    run_parser.add_argument("--question-id", action="append")
    run_parser.add_argument("--limit-questions", type=int)
    run_parser.add_argument("--run-id")
    run_parser.add_argument("--resume", action="store_true")
    run_parser.add_argument(
        "--skip-recorded-errors",
        action="store_true",
        help="on resume, treat prior errors as complete instead of retrying them",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "list-models":
            return list_models_command(args)
        if args.command == "probe":
            return probe_command(args)
        if args.command == "run":
            return run_command(args)
        parser.error("unknown command")
    except (ValueError, ProviderRequestError, RetryFailure, OSError, json.JSONDecodeError) as exc:
        # Provider exceptions reaching this boundary have already been constructed
        # without URL/header/key material.  Never print environment values here.
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    sys.exit(main())

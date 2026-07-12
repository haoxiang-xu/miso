from __future__ import annotations

import json
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import re
import time

from unchain.agent import Agent, ToolsModule
from unchain.kernel import KernelRunResult
from unchain.toolkits import CoreToolkit
from unchain.runtime.payloads import load_model_capabilities

from .cases import build_eval_case, get_eval_case, list_eval_cases
from .defaults import get_default_judge_model_spec
from .env import filter_model_specs, get_model_spec_skip_reason, load_root_env, resolve_api_key
from .judge import (
    build_judge_instructions,
    build_judge_messages,
    build_judge_response_format,
    build_skipped_judge_report,
    parse_judge_output,
)
from .scoring import extract_last_assistant_text, score_run_artifact, summarize_tool_usage
from .serialization import ensure_directory, persist_judge_report, persist_run_artifact, write_json, write_jsonl, write_leaderboard_csv
from .types import DEFAULT_RUBRIC_WEIGHTS, EvalCase, JudgeReport, ModelSpec, RunArtifact, coerce_model_spec, to_jsonable
from .workspace import diff_workspace_snapshots, prepare_workspace, snapshot_workspace


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _slugify(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", str(value or "").strip()).strip("-").lower() or "item"


def _load_model_capabilities(repo_root: Path) -> dict[str, Any]:
    del repo_root
    return load_model_capabilities()


def _resolve_model_key(model: str, registry: dict[str, Any]) -> str | None:
    if model in registry:
        return model

    normalized_model = model.replace(".", "-")
    best: str | None = None
    for raw_key in registry:
        key = str(raw_key)
        normalized_key = key.replace(".", "-")
        if (
            model.startswith(key)
            or model.startswith(normalized_key)
            or normalized_model.startswith(key)
            or normalized_model.startswith(normalized_key)
            or key.startswith(model)
            or key.startswith(normalized_model)
            or normalized_key.startswith(model)
            or normalized_key.startswith(normalized_model)
        ) and (best is None or len(key) > len(best)):
            best = key
    return best


def _model_capabilities(model_spec: ModelSpec, repo_root: Path) -> dict[str, Any]:
    registry = _load_model_capabilities(repo_root)
    resolved_key = _resolve_model_key(model_spec.model, registry)
    capabilities = registry.get(resolved_key, {}) if resolved_key else {}
    return dict(capabilities) if isinstance(capabilities, dict) else {}


def _build_standard_payload(
    *,
    model_spec: ModelSpec,
    repo_root: Path,
    max_output_tokens: int,
) -> dict[str, Any]:
    capabilities = _model_capabilities(model_spec, repo_root)
    allowed_keys = set(capabilities.get("allowed_payload_keys") or [])
    payload: dict[str, Any] = {}

    if not allowed_keys:
        payload.update(model_spec.payload)
        return payload

    if "temperature" in allowed_keys:
        payload["temperature"] = 0
    if "top_p" in allowed_keys:
        payload["top_p"] = 1
    if "max_output_tokens" in allowed_keys:
        payload["max_output_tokens"] = max_output_tokens
    if "max_tokens" in allowed_keys:
        payload["max_tokens"] = max_output_tokens
    if "store" in allowed_keys:
        payload["store"] = False
    if "reasoning" in allowed_keys:
        payload["reasoning"] = {"effort": "medium"}

    for key, value in model_spec.payload.items():
        if key in allowed_keys:
            payload[key] = value
    return payload


def _max_context_window_tokens(model_spec: ModelSpec, repo_root: Path) -> int | None:
    raw_value = _model_capabilities(model_spec, repo_root).get("max_context_window_tokens")
    try:
        value = int(raw_value or 0)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _build_candidate_instructions(case: EvalCase) -> str:
    base = (
        "You are running inside a benchmark harness for tool-using coding agents.\n"
        "Use the provided workspace tools to inspect the task.\n"
        "This benchmark is diagnosis and planning only. Do not modify files.\n"
        "Base every claim on tool-derived evidence.\n"
        "Reference files using relative paths.\n"
        f"Case: {case.title}"
    )
    if case.candidate_instructions:
        return f"{base}\n\n{case.candidate_instructions}"
    return base


def _build_candidate_tools(case: EvalCase, workspace_root: Path) -> list[Any]:
    toolkits: list[Any] = []
    allowed_toolkits = tuple(case.allowed_toolkits or ())

    for toolkit_name in allowed_toolkits:
        if toolkit_name == "core":
            toolkits.append(CoreToolkit(workspace_root=workspace_root))
            continue
        if toolkit_name == "external_api":
            toolkits.append(CoreToolkit(workspace_root=workspace_root))
            continue
        raise ValueError(f"unsupported toolkit for eval case '{case.id}': {toolkit_name}")

    return toolkits


def _build_candidate_modules(case: EvalCase, workspace_root: Path) -> tuple[Any, ...]:
    tools = tuple(_build_candidate_tools(case, workspace_root))
    if not tools:
        return ()
    return (ToolsModule(tools=tools),)


def _kernel_result_bundle(
    result: KernelRunResult,
    callback_events: list[dict[str, Any]] | None = None,
    *,
    model: str | None = None,
    max_context_window_tokens: int | None = None,
) -> dict[str, Any]:
    bundle: dict[str, Any] = {}
    for event in reversed(callback_events or []):
        event_bundle = event.get("bundle") if isinstance(event, dict) else None
        if isinstance(event_bundle, dict):
            bundle.update(event_bundle)
            break

    bundle.update(
        {
            "status": result.status,
            "human_input_request": result.human_input_request,
            "continuation": result.continuation,
            "consumed_tokens": int(result.consumed_tokens or 0),
            "input_tokens": int(result.input_tokens or 0),
            "output_tokens": int(result.output_tokens or 0),
            "last_turn_tokens": int(result.last_turn_tokens or 0),
            "last_turn_input_tokens": int(result.last_turn_input_tokens or 0),
            "last_turn_output_tokens": int(result.last_turn_output_tokens or 0),
            "cache_read_input_tokens": int(result.cache_read_input_tokens or 0),
            "cache_creation_input_tokens": int(result.cache_creation_input_tokens or 0),
            "previous_response_id": result.previous_response_id,
            "iteration": int(result.iteration or 0),
        }
    )
    if model and not bundle.get("model"):
        bundle["model"] = model
    if max_context_window_tokens and not bundle.get("max_context_window_tokens"):
        bundle["max_context_window_tokens"] = int(max_context_window_tokens)
    if bundle.get("context_window_used_pct") is None and bundle.get("max_context_window_tokens"):
        bundle["context_window_used_pct"] = round(
            int(result.last_turn_tokens or 0)
            / int(bundle["max_context_window_tokens"])
            * 100.0,
            2,
        )
    return bundle


def _coerce_agent_run_output(
    output: KernelRunResult | tuple[list[dict[str, Any]], dict[str, Any]],
    *,
    callback_events: list[dict[str, Any]] | None = None,
    model: str | None = None,
    max_context_window_tokens: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if isinstance(output, KernelRunResult):
        return list(output.messages), _kernel_result_bundle(
            output,
            callback_events,
            model=model,
            max_context_window_tokens=max_context_window_tokens,
        )
    if isinstance(output, tuple) and len(output) == 2:
        messages, bundle = output
        if isinstance(messages, list) and isinstance(bundle, dict):
            normalized_bundle: dict[str, Any] = {}
            for event in reversed(callback_events or []):
                event_bundle = event.get("bundle") if isinstance(event, dict) else None
                if isinstance(event_bundle, dict):
                    normalized_bundle.update(event_bundle)
                    break
            normalized_bundle.update(bundle)
            if model and not normalized_bundle.get("model"):
                normalized_bundle["model"] = model
            if max_context_window_tokens and not normalized_bundle.get("max_context_window_tokens"):
                normalized_bundle["max_context_window_tokens"] = int(max_context_window_tokens)
            if (
                normalized_bundle.get("context_window_used_pct") is None
                and normalized_bundle.get("max_context_window_tokens")
            ):
                normalized_bundle["context_window_used_pct"] = round(
                    int(normalized_bundle.get("last_turn_tokens") or 0)
                    / int(normalized_bundle["max_context_window_tokens"])
                    * 100.0,
                    2,
                )
            return list(messages), normalized_bundle
    raise TypeError("agent run must return KernelRunResult or a legacy (messages, bundle) tuple")


def _build_run_artifact(
    *,
    suite_id: str,
    case: EvalCase,
    model_spec: ModelSpec,
    started_at: str,
    duration_seconds: float,
    status: str,
    final_answer: str = "",
    messages: list[dict[str, Any]] | None = None,
    bundle: dict[str, Any] | None = None,
    callback_events: list[dict[str, Any]] | None = None,
    skip_reason: str | None = None,
    error: str | None = None,
) -> RunArtifact:
    tool_usage = summarize_tool_usage(callback_events or [])
    token_usage = {
        "consumed_tokens": int((bundle or {}).get("consumed_tokens", 0) or 0),
        "input_tokens": int((bundle or {}).get("input_tokens", 0) or 0),
        "output_tokens": int((bundle or {}).get("output_tokens", 0) or 0),
        "last_turn_tokens": int((bundle or {}).get("last_turn_tokens", 0) or 0),
        "last_turn_input_tokens": int((bundle or {}).get("last_turn_input_tokens", 0) or 0),
        "last_turn_output_tokens": int((bundle or {}).get("last_turn_output_tokens", 0) or 0),
        "cache_read_input_tokens": int((bundle or {}).get("cache_read_input_tokens", 0) or 0),
        "cache_creation_input_tokens": int(
            (bundle or {}).get("cache_creation_input_tokens", 0) or 0
        ),
        "context_window_used_pct": (bundle or {}).get("context_window_used_pct"),
        "max_context_window_tokens": (bundle or {}).get("max_context_window_tokens"),
    }
    artifact = RunArtifact(
        run_id=f"{case.id}__{_slugify(model_spec.label)}",
        suite_id=suite_id,
        case_id=case.id,
        case_title=case.title,
        provider=model_spec.provider,
        model=model_spec.model,
        model_label=model_spec.label,
        status=status,
        started_at=started_at,
        duration_seconds=round(duration_seconds, 3),
        workspace_mode=case.workspace_mode,
        workspace_source=case.workspace_source,
        final_answer=final_answer,
        messages=list(messages or []),
        bundle=dict(bundle or {}),
        callback_events=list(callback_events or []),
        tool_usage=tool_usage,
        token_usage=token_usage,
        skip_reason=skip_reason,
        error=error,
    )
    artifact.rule_scores = score_run_artifact(artifact, case)
    return artifact


def _run_candidate_case(
    *,
    repo_root: Path,
    suite_id: str,
    case: EvalCase,
    model_spec: ModelSpec,
    api_key: str | None,
    max_iterations: int,
    agent_cls: type[Agent],
) -> RunArtifact:
    started_at = datetime.now(timezone.utc).isoformat()
    started = time.perf_counter()

    skip_reason = get_model_spec_skip_reason(model_spec, api_key=api_key)
    if skip_reason is not None:
        return _build_run_artifact(
            suite_id=suite_id,
            case=case,
            model_spec=model_spec,
            started_at=started_at,
            duration_seconds=0.0,
            status="skipped",
            skip_reason=skip_reason,
        )

    callback_events: list[dict[str, Any]] = []
    try:
        with prepare_workspace(case, repo_root=repo_root) as workspace_root:
            workspace_before = snapshot_workspace(workspace_root)
            try:
                max_context_window_tokens = _max_context_window_tokens(model_spec, repo_root)
                agent = agent_cls(
                    name=f"candidate-{_slugify(model_spec.label)}",
                    provider=model_spec.provider,
                    model=model_spec.model,
                    api_key=api_key,
                    instructions=_build_candidate_instructions(case),
                    modules=_build_candidate_modules(case, workspace_root),
                )
                output = agent.run(
                    case.task_prompt,
                    payload=_build_standard_payload(
                        model_spec=model_spec,
                        repo_root=repo_root,
                        max_output_tokens=2048,
                    ),
                    callback=callback_events.append,
                    max_iterations=max_iterations,
                    max_context_window_tokens=max_context_window_tokens,
                    tool_runtime_config=dict(case.toolkit_options or {}) or None,
                )
                messages, bundle = _coerce_agent_run_output(
                    output,
                    callback_events=callback_events,
                    model=model_spec.model,
                    max_context_window_tokens=max_context_window_tokens,
                )
            finally:
                callback_events.append(
                    {
                        "type": "workspace_changes",
                        **diff_workspace_snapshots(
                            workspace_before,
                            snapshot_workspace(workspace_root),
                        ),
                    }
                )
    except Exception as exc:
        return _build_run_artifact(
            suite_id=suite_id,
            case=case,
            model_spec=model_spec,
            started_at=started_at,
            duration_seconds=time.perf_counter() - started,
            status="error",
            callback_events=callback_events,
            error=f"{exc}\n{traceback.format_exc()}",
        )

    return _build_run_artifact(
        suite_id=suite_id,
        case=case,
        model_spec=model_spec,
        started_at=started_at,
        duration_seconds=time.perf_counter() - started,
        status=str(bundle.get("status") or "completed"),
        final_answer=extract_last_assistant_text(messages),
        messages=messages,
        bundle=bundle,
        callback_events=callback_events,
    )


def _run_judge(
    *,
    repo_root: Path,
    case: EvalCase,
    run_artifact: RunArtifact,
    judge_model_spec: ModelSpec | None,
    rubric_weights: dict[str, int],
    judge_agent_cls: type[Agent],
) -> JudgeReport:
    if judge_model_spec is None:
        return build_skipped_judge_report(
            case=case,
            run_artifact=run_artifact,
            judge_provider="",
            judge_model="",
            judge_label="",
            reason="judge model not configured",
        )

    if run_artifact.status != "completed":
        return build_skipped_judge_report(
            case=case,
            run_artifact=run_artifact,
            judge_provider=judge_model_spec.provider,
            judge_model=judge_model_spec.model,
            judge_label=judge_model_spec.label,
            reason=f"candidate status was {run_artifact.status}",
        )

    judge_api_key, _ = resolve_api_key(judge_model_spec)
    judge_skip_reason = get_model_spec_skip_reason(judge_model_spec, api_key=judge_api_key)
    if judge_skip_reason is not None:
        return build_skipped_judge_report(
            case=case,
            run_artifact=run_artifact,
            judge_provider=judge_model_spec.provider,
            judge_model=judge_model_spec.model,
            judge_label=judge_model_spec.label,
            reason=judge_skip_reason,
        )

    try:
        max_context_window_tokens = _max_context_window_tokens(judge_model_spec, repo_root)
        judge_agent = judge_agent_cls(
            name=f"judge-{_slugify(judge_model_spec.label)}",
            provider=judge_model_spec.provider,
            model=judge_model_spec.model,
            api_key=judge_api_key,
            instructions=build_judge_instructions(),
        )
        output = judge_agent.run(
            messages=build_judge_messages(
                case=case,
                run_artifact=run_artifact,
                rubric_weights=rubric_weights,
            ),
            payload=_build_standard_payload(
                model_spec=judge_model_spec,
                repo_root=repo_root,
                max_output_tokens=4096,
            ),
            response_format=build_judge_response_format(),
            max_iterations=1,
            max_context_window_tokens=max_context_window_tokens,
        )
        messages, bundle = _coerce_agent_run_output(
            output,
            model=judge_model_spec.model,
            max_context_window_tokens=max_context_window_tokens,
        )
        return parse_judge_output(
            case=case,
            run_artifact=run_artifact,
            judge_provider=judge_model_spec.provider,
            judge_model=judge_model_spec.model,
            judge_label=judge_model_spec.label,
            messages=messages,
            bundle=bundle,
        )
    except Exception as exc:
        return JudgeReport(
            case_id=case.id,
            case_title=case.title,
            provider=run_artifact.provider,
            model=run_artifact.model,
            model_label=run_artifact.model_label,
            judge_provider=judge_model_spec.provider,
            judge_model=judge_model_spec.model,
            judge_label=judge_model_spec.label,
            status="error",
            error=f"{exc}\n{traceback.format_exc()}",
        )


def _blend_scores(rule_score: float, judge_score: float | None, score_weights: dict[str, float]) -> float:
    if judge_score is None:
        return round(rule_score, 2)
    rule_weight = float(score_weights.get("rule", 0.4))
    judge_weight = float(score_weights.get("judge", 0.6))
    total_weight = rule_weight + judge_weight
    if total_weight <= 0:
        return round(judge_score, 2)
    return round(((rule_score * rule_weight) + (judge_score * judge_weight)) / total_weight, 2)


def run_benchmark_suite(
    *,
    repo_root: str | Path,
    model_specs: list[ModelSpec | dict[str, Any]],
    judge_model_spec: ModelSpec | dict[str, Any] | None = None,
    selected_case_ids: list[str] | None = None,
    rubric_weights: dict[str, int] | None = None,
    score_weights: dict[str, float] | None = None,
    max_iterations: int = 8,
    artifacts_root: str | Path | None = None,
    candidate_agent_cls: type[Agent] = Agent,
    judge_agent_cls: type[Agent] = Agent,
) -> dict[str, Any]:
    repo_path = Path(repo_root).resolve()
    load_root_env(repo_path)

    suite_id = _utc_timestamp()
    suite_root = ensure_directory(artifacts_root or (repo_path / "tests" / "evals" / "artifacts" / suite_id))
    runs_dir = ensure_directory(suite_root / "runs")
    judge_dir = ensure_directory(suite_root / "judge_reports")

    chosen_case_ids = set(selected_case_ids or [])
    all_cases = list_eval_cases(repo_path)
    cases = [case for case in all_cases if not chosen_case_ids or case.id in chosen_case_ids]
    rubric = dict(rubric_weights or DEFAULT_RUBRIC_WEIGHTS)
    blend_weights = dict(score_weights or {"rule": 0.4, "judge": 0.6})

    _, skipped_specs = filter_model_specs(model_specs)
    all_specs = [coerce_model_spec(spec) for spec in model_specs]
    judge_spec = (
        coerce_model_spec(judge_model_spec)
        if judge_model_spec is not None
        else get_default_judge_model_spec()
    )

    run_artifacts: list[RunArtifact] = []
    judge_reports: list[JudgeReport] = []
    leaderboard_rows: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []

    for case in cases:
        for model_spec in all_specs:
            candidate_api_key, _ = resolve_api_key(model_spec)
            run_artifact = _run_candidate_case(
                repo_root=repo_path,
                suite_id=suite_id,
                case=case,
                model_spec=model_spec,
                api_key=candidate_api_key,
                max_iterations=max_iterations,
                agent_cls=candidate_agent_cls,
            )
            run_artifacts.append(run_artifact)

            run_path = runs_dir / f"{case.id}__{_slugify(model_spec.label)}.json"
            persist_run_artifact(run_path, run_artifact)

            judge_report = _run_judge(
                repo_root=repo_path,
                case=case,
                run_artifact=run_artifact,
                judge_model_spec=judge_spec,
                rubric_weights=rubric,
                judge_agent_cls=judge_agent_cls,
            )
            judge_reports.append(judge_report)
            judge_path = judge_dir / f"{case.id}__{_slugify(model_spec.label)}.json"
            persist_judge_report(judge_path, judge_report)

            rule_score = float((run_artifact.rule_scores or {}).get("score_pct", 0.0))
            judge_score = judge_report.overall_score if judge_report.status == "completed" else None
            blended_score = _blend_scores(rule_score, judge_score, blend_weights)

            row = {
                "case_id": case.id,
                "model_label": model_spec.label,
                "provider": model_spec.provider,
                "model": model_spec.model,
                "run_status": run_artifact.status,
                "judge_status": judge_report.status,
                "rule_score": rule_score,
                "judge_score": judge_score,
                "blended_score": blended_score,
                "duration_seconds": run_artifact.duration_seconds,
                "consumed_tokens": run_artifact.token_usage.get("consumed_tokens"),
                "input_tokens": run_artifact.token_usage.get("input_tokens"),
                "output_tokens": run_artifact.token_usage.get("output_tokens"),
                "tool_calls": run_artifact.tool_usage.get("total_calls"),
                "run_artifact_path": str(run_path),
                "judge_report_path": str(judge_path),
            }
            leaderboard_rows.append(row)
            summary_rows.append(
                {
                    **row,
                    "rule_scores": run_artifact.rule_scores,
                    "tool_usage": run_artifact.tool_usage,
                    "skip_reason": run_artifact.skip_reason,
                    "error": run_artifact.error,
                    "judge_skip_reason": judge_report.skip_reason,
                    "judge_error": judge_report.error,
                }
            )

    leaderboard_rows.sort(
        key=lambda row: (row.get("case_id", ""), -(row.get("blended_score") or 0), row.get("model_label", "")),
    )

    summary_path = write_json(
        suite_root / "summary.json",
        {
            "suite_id": suite_id,
            "repo_root": str(repo_path),
            "selected_case_ids": [case.id for case in cases],
            "skipped_model_specs": skipped_specs,
            "judge_model_spec": judge_spec,
            "rubric_weights": rubric,
            "score_weights": blend_weights,
            "runs": run_artifacts,
            "judge_reports": judge_reports,
            "leaderboard": leaderboard_rows,
        },
    )
    summary_jsonl_path = write_jsonl(suite_root / "summary.jsonl", summary_rows)
    leaderboard_path = write_leaderboard_csv(suite_root / "leaderboard.csv", leaderboard_rows)

    return {
        "suite_id": suite_id,
        "repo_root": str(repo_path),
        "artifacts_dir": str(suite_root),
        "summary_path": str(summary_path),
        "summary_jsonl_path": str(summary_jsonl_path),
        "leaderboard_path": str(leaderboard_path),
        "leaderboard_rows": to_jsonable(leaderboard_rows),
        "run_artifacts": to_jsonable(run_artifacts),
        "judge_reports": to_jsonable(judge_reports),
    }


def run_single_model_eval(
    *,
    repo_root: str | Path,
    case: EvalCase | dict[str, Any],
    model_spec: ModelSpec | dict[str, Any],
    judge_model_spec: ModelSpec | dict[str, Any] | None = None,
    rubric_weights: dict[str, int] | None = None,
    score_weights: dict[str, float] | None = None,
    max_iterations: int = 8,
    artifacts_root: str | Path | None = None,
    candidate_agent_cls: type[Agent] = Agent,
    judge_agent_cls: type[Agent] = Agent,
) -> dict[str, Any]:
    repo_path = Path(repo_root).resolve()
    load_root_env(repo_path)

    eval_case = case if isinstance(case, EvalCase) else build_eval_case(**case)
    candidate_spec = coerce_model_spec(model_spec)
    judge_spec = (
        coerce_model_spec(judge_model_spec)
        if judge_model_spec is not None
        else get_default_judge_model_spec()
    )
    suite_id = _utc_timestamp()

    base_root = Path(artifacts_root) if artifacts_root is not None else (
        repo_path / "tests" / "evals" / "artifacts" / eval_case.id / suite_id
    )
    suite_root = ensure_directory(base_root)
    runs_dir = ensure_directory(suite_root / "runs")
    judge_dir = ensure_directory(suite_root / "judge_reports")
    rubric = dict(rubric_weights or eval_case.rubric_weights or DEFAULT_RUBRIC_WEIGHTS)
    blend_weights = dict(score_weights or {"rule": 0.4, "judge": 0.6})

    candidate_api_key, _ = resolve_api_key(candidate_spec)
    run_artifact = _run_candidate_case(
        repo_root=repo_path,
        suite_id=suite_id,
        case=eval_case,
        model_spec=candidate_spec,
        api_key=candidate_api_key,
        max_iterations=max_iterations,
        agent_cls=candidate_agent_cls,
    )
    run_path = runs_dir / f"{eval_case.id}__{_slugify(candidate_spec.label)}.json"
    persist_run_artifact(run_path, run_artifact)

    judge_report = _run_judge(
        repo_root=repo_path,
        case=eval_case,
        run_artifact=run_artifact,
        judge_model_spec=judge_spec,
        rubric_weights=rubric,
        judge_agent_cls=judge_agent_cls,
    )
    judge_path = judge_dir / f"{eval_case.id}__{_slugify(candidate_spec.label)}.json"
    persist_judge_report(judge_path, judge_report)

    rule_score = float((run_artifact.rule_scores or {}).get("score_pct", 0.0))
    judge_score = judge_report.overall_score if judge_report.status == "completed" else None
    blended_score = _blend_scores(rule_score, judge_score, blend_weights)

    summary = {
        "suite_id": suite_id,
        "repo_root": str(repo_path),
        "artifacts_dir": str(suite_root),
        "case": to_jsonable(eval_case),
        "model_spec": to_jsonable(candidate_spec),
        "judge_model_spec": to_jsonable(judge_spec),
        "rubric_weights": rubric,
        "score_weights": blend_weights,
        "run_artifact": to_jsonable(run_artifact),
        "judge_report": to_jsonable(judge_report),
        "scores": {
            "rule_score": rule_score,
            "judge_score": judge_score,
            "blended_score": blended_score,
        },
        "run_artifact_path": str(run_path),
        "judge_report_path": str(judge_path),
    }
    summary_path = write_json(suite_root / "summary.json", summary)
    summary["summary_path"] = str(summary_path)
    return summary


def render_single_eval_summary(result: dict[str, Any]) -> str:
    scores = dict(result.get("scores") or {})
    run_artifact = dict(result.get("run_artifact") or {})
    judge_report = dict(result.get("judge_report") or {})
    return "\n".join(
        [
            f"Suite ID: {result.get('suite_id')}",
            f"Artifacts: {result.get('artifacts_dir')}",
            f"Run artifact: {result.get('run_artifact_path')}",
            f"Judge report: {result.get('judge_report_path')}",
            f"Candidate status: {run_artifact.get('status')}",
            f"Judge status: {judge_report.get('status')}",
            f"Rule score: {scores.get('rule_score')}",
            f"Judge score: {scores.get('judge_score')}",
            f"Blended score: {scores.get('blended_score')}",
        ]
    )


def render_suite_summary(result: dict[str, Any]) -> str:
    rows = list(result.get("leaderboard_rows") or [])
    lines = [
        f"Suite ID: {result.get('suite_id')}",
        f"Artifacts: {result.get('artifacts_dir')}",
        f"Leaderboard: {result.get('leaderboard_path')}",
    ]
    for row in rows:
        lines.append(
            (
                f"- {row['case_id']} | {row['model_label']} | "
                f"run={row['run_status']} | judge={row['judge_status']} | "
                f"rule={row['rule_score']} | judge_score={row['judge_score']} | blended={row['blended_score']}"
            )
        )
    return "\n".join(lines)

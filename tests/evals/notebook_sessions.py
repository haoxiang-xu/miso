from __future__ import annotations

import json
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from miso import Agent

from .cases import build_eval_case
from .defaults import get_default_judge_model_spec
from .env import load_root_env, resolve_api_key
from .judge import build_skipped_judge_report
from .runner import _build_candidate_tools, _build_run_artifact, _build_standard_payload, _run_judge, _slugify
from .scoring import extract_last_assistant_text
from .serialization import ensure_directory, persist_judge_report, persist_run_artifact, write_json
from .types import EvalCase, ModelSpec, RunArtifact, coerce_model_spec, to_jsonable


STATE_VERSION = 1


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _coerce_case(value: EvalCase | dict[str, Any]) -> EvalCase:
    if isinstance(value, EvalCase):
        return value
    data = dict(value)
    if "case_id" not in data and "id" in data:
        data["case_id"] = data.pop("id")
    return build_eval_case(**data)


def _state_file_payload(
    *,
    repo_root: Path,
    test_dir: Path,
    workspace_root: Path,
    session_id: str,
    case: EvalCase,
    model_spec: ModelSpec,
    run_artifact: RunArtifact,
    max_iterations: int,
    judge_model_spec: ModelSpec | None = None,
    judge_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "state_version": STATE_VERSION,
        "updated_at": _now_iso(),
        "repo_root": str(repo_root),
        "test_dir": str(test_dir),
        "workspace_root": str(workspace_root),
        "session_id": session_id,
        "max_iterations": int(max_iterations),
        "case": to_jsonable(case),
        "model_spec": to_jsonable(model_spec),
        "judge_model_spec": to_jsonable(judge_model_spec),
        "run_artifact": to_jsonable(run_artifact),
        "judge_report": to_jsonable(judge_report),
    }


def load_notebook_session(state_path: str | Path) -> dict[str, Any]:
    return json.loads(Path(state_path).read_text(encoding="utf-8"))


def save_notebook_session(state_path: str | Path, payload: dict[str, Any]) -> Path:
    return write_json(state_path, payload)


def _candidate_agent(
    *,
    case: EvalCase,
    model_spec: ModelSpec,
    api_key: str,
    workspace_root: Path,
    agent_cls: type[Agent],
) -> Agent:
    return agent_cls(
        name=f"candidate-{_slugify(model_spec.label)}",
        provider=model_spec.provider,
        model=model_spec.model,
        api_key=api_key,
        instructions=case.candidate_instructions or (
            "Use the mounted tools carefully. Base claims on evidence. "
            "Ask the user via request_user_input when the prompt says you must confirm decisions."
        ),
        tools=_build_candidate_tools(case, workspace_root),
    )


def start_notebook_session(
    *,
    repo_root: str | Path,
    test_dir: str | Path,
    case: EvalCase | dict[str, Any],
    model_spec: ModelSpec | dict[str, Any],
    judge_model_spec: ModelSpec | dict[str, Any] | None = None,
    max_iterations: int = 12,
    state_path: str | Path | None = None,
    agent_cls: type[Agent] = Agent,
) -> dict[str, Any]:
    repo_path = Path(repo_root).resolve()
    test_path = Path(test_dir).resolve()
    workspace_root = test_path
    artifacts_dir = ensure_directory(test_path / "artifacts")
    effective_state_path = Path(state_path) if state_path is not None else artifacts_dir / "session_state.json"

    load_root_env(repo_path)
    eval_case = _coerce_case(case)
    candidate_spec = coerce_model_spec(model_spec)
    judge_spec = (
        coerce_model_spec(judge_model_spec)
        if judge_model_spec is not None
        else get_default_judge_model_spec()
    )
    api_key, _ = resolve_api_key(candidate_spec)
    session_id = f"{eval_case.id}-{uuid.uuid4().hex}"

    if not api_key:
        run_artifact = _build_run_artifact(
            suite_id=session_id,
            case=eval_case,
            model_spec=candidate_spec,
            started_at=_now_iso(),
            duration_seconds=0.0,
            status="skipped",
            skip_reason=f"missing API key for {candidate_spec.label}",
        )
        run_artifact_path = artifacts_dir / "run_artifact.json"
        persist_run_artifact(run_artifact_path, run_artifact)
        payload = _state_file_payload(
            repo_root=repo_path,
            test_dir=test_path,
            workspace_root=workspace_root,
            session_id=session_id,
            case=eval_case,
            model_spec=candidate_spec,
            run_artifact=run_artifact,
            max_iterations=max_iterations,
            judge_model_spec=judge_spec,
        )
        save_notebook_session(effective_state_path, payload)
        payload["run_artifact_path"] = str(run_artifact_path)
        payload["state_path"] = str(effective_state_path)
        return payload

    callback_events: list[dict[str, Any]] = []
    started_at = _now_iso()
    started = time.perf_counter()
    try:
        agent = _candidate_agent(
            case=eval_case,
            model_spec=candidate_spec,
            api_key=api_key,
            workspace_root=workspace_root,
            agent_cls=agent_cls,
        )
        messages, bundle = agent.run(
            eval_case.task_prompt,
            payload=_build_standard_payload(
                model_spec=candidate_spec,
                repo_root=repo_path,
                max_output_tokens=4096,
            ),
            callback=callback_events.append,
            max_iterations=max_iterations,
            session_id=session_id,
            memory_namespace=session_id,
        )
        run_artifact = _build_run_artifact(
            suite_id=session_id,
            case=eval_case,
            model_spec=candidate_spec,
            started_at=started_at,
            duration_seconds=time.perf_counter() - started,
            status=str(bundle.get("status") or "completed"),
            final_answer=extract_last_assistant_text(messages),
            messages=messages,
            bundle=bundle,
            callback_events=callback_events,
        )
    except Exception as exc:
        run_artifact = _build_run_artifact(
            suite_id=session_id,
            case=eval_case,
            model_spec=candidate_spec,
            started_at=started_at,
            duration_seconds=time.perf_counter() - started,
            status="error",
            callback_events=callback_events,
            error=f"{exc}\n{traceback.format_exc()}",
        )

    run_artifact_path = artifacts_dir / "run_artifact.json"
    persist_run_artifact(run_artifact_path, run_artifact)
    payload = _state_file_payload(
        repo_root=repo_path,
        test_dir=test_path,
        workspace_root=workspace_root,
        session_id=session_id,
        case=eval_case,
        model_spec=candidate_spec,
        run_artifact=run_artifact,
        max_iterations=max_iterations,
        judge_model_spec=judge_spec,
    )
    save_notebook_session(effective_state_path, payload)
    payload["run_artifact_path"] = str(run_artifact_path)
    payload["state_path"] = str(effective_state_path)
    return payload


def resume_notebook_session(
    *,
    state: dict[str, Any] | None = None,
    state_path: str | Path | None = None,
    user_response: dict[str, Any],
    agent_cls: type[Agent] = Agent,
) -> dict[str, Any]:
    if state is None:
        if state_path is None:
            raise ValueError("resume_notebook_session requires state or state_path")
        state = load_notebook_session(state_path)

    repo_path = Path(state["repo_root"]).resolve()
    test_path = Path(state["test_dir"]).resolve()
    workspace_root = Path(state["workspace_root"]).resolve()
    effective_state_path = Path(state_path) if state_path is not None else (test_path / "artifacts" / "session_state.json")

    eval_case = _coerce_case(state["case"])
    candidate_spec = coerce_model_spec(state["model_spec"])
    judge_spec = (
        coerce_model_spec(state["judge_model_spec"])
        if state.get("judge_model_spec")
        else get_default_judge_model_spec()
    )
    previous_artifact = RunArtifact(**state["run_artifact"])

    if previous_artifact.status != "awaiting_human_input":
        updated = dict(state)
        updated["note"] = f"run status is {previous_artifact.status}; no resume needed"
        save_notebook_session(effective_state_path, updated)
        updated["state_path"] = str(effective_state_path)
        return updated

    load_root_env(repo_path)
    api_key, _ = resolve_api_key(candidate_spec)
    if not api_key:
        raise ValueError(f"missing API key for {candidate_spec.label}")

    callback_events: list[dict[str, Any]] = []
    started = time.perf_counter()
    try:
        agent = _candidate_agent(
            case=eval_case,
            model_spec=candidate_spec,
            api_key=api_key,
            workspace_root=workspace_root,
            agent_cls=agent_cls,
        )
        messages, bundle = agent.resume_human_input(
            conversation=previous_artifact.messages,
            continuation=previous_artifact.bundle["continuation"],
            response=user_response,
            callback=callback_events.append,
            session_id=state["session_id"],
            memory_namespace=state["session_id"],
        )
        total_duration = float(previous_artifact.duration_seconds or 0.0) + (time.perf_counter() - started)
        run_artifact = _build_run_artifact(
            suite_id=str(previous_artifact.suite_id),
            case=eval_case,
            model_spec=candidate_spec,
            started_at=previous_artifact.started_at,
            duration_seconds=total_duration,
            status=str(bundle.get("status") or "completed"),
            final_answer=extract_last_assistant_text(messages),
            messages=messages,
            bundle=bundle,
            callback_events=list(previous_artifact.callback_events or []) + callback_events,
        )
    except Exception as exc:
        total_duration = float(previous_artifact.duration_seconds or 0.0) + (time.perf_counter() - started)
        run_artifact = _build_run_artifact(
            suite_id=str(previous_artifact.suite_id),
            case=eval_case,
            model_spec=candidate_spec,
            started_at=previous_artifact.started_at,
            duration_seconds=total_duration,
            status="error",
            messages=previous_artifact.messages,
            bundle=previous_artifact.bundle,
            callback_events=list(previous_artifact.callback_events or []) + callback_events,
            error=f"{exc}\n{traceback.format_exc()}",
        )

    artifacts_dir = ensure_directory(test_path / "artifacts")
    run_artifact_path = artifacts_dir / "run_artifact.json"
    persist_run_artifact(run_artifact_path, run_artifact)

    payload = _state_file_payload(
        repo_root=repo_path,
        test_dir=test_path,
        workspace_root=workspace_root,
        session_id=state["session_id"],
        case=eval_case,
        model_spec=candidate_spec,
        run_artifact=run_artifact,
        max_iterations=int(state.get("max_iterations", 12)),
        judge_model_spec=judge_spec,
        judge_report=state.get("judge_report"),
    )
    save_notebook_session(effective_state_path, payload)
    payload["run_artifact_path"] = str(run_artifact_path)
    payload["state_path"] = str(effective_state_path)
    return payload


def judge_notebook_session(
    *,
    state: dict[str, Any] | None = None,
    state_path: str | Path | None = None,
    judge_model_spec: ModelSpec | dict[str, Any] | None = None,
    judge_agent_cls: type[Agent] = Agent,
) -> dict[str, Any]:
    if state is None:
        if state_path is None:
            raise ValueError("judge_notebook_session requires state or state_path")
        state = load_notebook_session(state_path)

    repo_path = Path(state["repo_root"]).resolve()
    test_path = Path(state["test_dir"]).resolve()
    effective_state_path = Path(state_path) if state_path is not None else (test_path / "artifacts" / "session_state.json")
    eval_case = _coerce_case(state["case"])
    candidate_spec = coerce_model_spec(state["model_spec"])
    previous_artifact = RunArtifact(**state["run_artifact"])
    effective_judge_spec = (
        coerce_model_spec(judge_model_spec)
        if judge_model_spec is not None
        else (
            coerce_model_spec(state["judge_model_spec"])
            if state.get("judge_model_spec")
            else get_default_judge_model_spec()
        )
    )
    load_root_env(repo_path)

    if previous_artifact.status != "completed":
        judge_report = build_skipped_judge_report(
            case=eval_case,
            run_artifact=previous_artifact,
            judge_provider=effective_judge_spec.provider if effective_judge_spec else "",
            judge_model=effective_judge_spec.model if effective_judge_spec else "",
            judge_label=effective_judge_spec.label if effective_judge_spec else "",
            reason=f"candidate status was {previous_artifact.status}",
        )
    else:
        judge_report = _run_judge(
            repo_root=repo_path,
            case=eval_case,
            run_artifact=previous_artifact,
            judge_model_spec=effective_judge_spec,
            rubric_weights=eval_case.rubric_weights,
            judge_agent_cls=judge_agent_cls,
        )

    artifacts_dir = ensure_directory(test_path / "artifacts")
    judge_report_path = artifacts_dir / "judge_report.json"
    persist_judge_report(judge_report_path, judge_report)

    payload = dict(state)
    payload["judge_model_spec"] = to_jsonable(effective_judge_spec)
    payload["judge_report"] = to_jsonable(judge_report)
    payload["updated_at"] = _now_iso()
    save_notebook_session(effective_state_path, payload)
    payload["judge_report_path"] = str(judge_report_path)
    payload["state_path"] = str(effective_state_path)
    return payload


def describe_pending_request(state: dict[str, Any]) -> dict[str, Any] | None:
    run_artifact = dict(state.get("run_artifact") or {})
    bundle = dict(run_artifact.get("bundle") or {})
    request = bundle.get("human_input_request")
    return request if isinstance(request, dict) else None

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any


DEFAULT_RUBRIC_WEIGHTS = {
    "correctness": 40,
    "debugging": 25,
    "tool_strategy": 20,
    "efficiency": 15,
}


@dataclass(frozen=True)
class ModelSpec:
    provider: str
    model: str
    label: str
    api_key_env: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EvalCase:
    id: str
    title: str
    task_prompt: str
    workspace_mode: str
    workspace_source: str
    allowed_toolkits: tuple[str, ...] = ("access_workspace_toolkit", "run_terminal_toolkit")
    toolkit_options: dict[str, dict[str, Any]] = field(default_factory=dict)
    rule_checks: dict[str, Any] = field(default_factory=dict)
    rubric_weights: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_RUBRIC_WEIGHTS))
    candidate_instructions: str = ""


@dataclass
class RunArtifact:
    run_id: str
    suite_id: str
    case_id: str
    case_title: str
    provider: str
    model: str
    model_label: str
    status: str
    started_at: str
    duration_seconds: float
    workspace_mode: str
    workspace_source: str
    final_answer: str = ""
    messages: list[dict[str, Any]] = field(default_factory=list)
    bundle: dict[str, Any] = field(default_factory=dict)
    callback_events: list[dict[str, Any]] = field(default_factory=list)
    tool_usage: dict[str, Any] = field(default_factory=dict)
    token_usage: dict[str, Any] = field(default_factory=dict)
    rule_scores: dict[str, Any] = field(default_factory=dict)
    skip_reason: str | None = None
    error: str | None = None


@dataclass
class JudgeReport:
    case_id: str
    case_title: str
    provider: str
    model: str
    model_label: str
    judge_provider: str
    judge_model: str
    judge_label: str
    status: str
    overall_score: float | None = None
    rubric_scores: dict[str, float] = field(default_factory=dict)
    summary: str = ""
    failure_modes: list[str] = field(default_factory=list)
    debug_notes: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    prompt_suggestions: list[str] = field(default_factory=list)
    tooling_suggestions: list[str] = field(default_factory=list)
    raw_messages: list[dict[str, Any]] = field(default_factory=list)
    raw_bundle: dict[str, Any] = field(default_factory=dict)
    skip_reason: str | None = None
    error: str | None = None


def coerce_model_spec(value: ModelSpec | dict[str, Any]) -> ModelSpec:
    if isinstance(value, ModelSpec):
        return value
    if not isinstance(value, dict):
        raise TypeError(f"unsupported model spec type: {type(value).__name__}")
    return ModelSpec(
        provider=str(value.get("provider", "")).strip(),
        model=str(value.get("model", "")).strip(),
        label=str(value.get("label") or value.get("model") or "").strip(),
        api_key_env=(str(value["api_key_env"]).strip() if value.get("api_key_env") else None),
        payload=dict(value.get("payload") or {}),
    )


def to_jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return to_jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value

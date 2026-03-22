from __future__ import annotations

import json
from typing import Any

from miso.schemas import ResponseFormat

from .types import EvalCase, JudgeReport, RunArtifact


def build_judge_response_format() -> ResponseFormat:
    return ResponseFormat(
        name="judge_report",
        schema={
            "type": "object",
            "properties": {
                "overall_score": {"type": "number"},
                "rubric_scores": {
                    "type": "object",
                    "properties": {
                        "correctness": {"type": "number"},
                        "debugging": {"type": "number"},
                        "tool_strategy": {"type": "number"},
                        "efficiency": {"type": "number"},
                    },
                    "required": ["correctness", "debugging", "tool_strategy", "efficiency"],
                    "additionalProperties": False,
                },
                "summary": {"type": "string"},
                "failure_modes": {"type": "array", "items": {"type": "string"}},
                "debug_notes": {"type": "array", "items": {"type": "string"}},
                "recommendations": {"type": "array", "items": {"type": "string"}},
                "prompt_suggestions": {"type": "array", "items": {"type": "string"}},
                "tooling_suggestions": {"type": "array", "items": {"type": "string"}},
            },
            "required": [
                "overall_score",
                "rubric_scores",
                "summary",
                "failure_modes",
                "debug_notes",
                "recommendations",
                "prompt_suggestions",
                "tooling_suggestions",
            ],
            "additionalProperties": False,
        },
    )


def build_judge_instructions() -> str:
    return (
        "You are a strict evaluation judge for tool-using coding agents.\n"
        "Score only from the provided transcript, final answer, and rule-based checks.\n"
        "Do not reward unsupported claims.\n"
        "Keep recommendations concrete and oriented toward debugging, prompt fixes, or tool strategy fixes.\n"
        "Treat missing evidence, shallow diagnosis, and weak file grounding as real failures.\n"
        "Return JSON only."
    )


def build_judge_messages(
    *,
    case: EvalCase,
    run_artifact: RunArtifact,
    rubric_weights: dict[str, int],
) -> list[dict[str, Any]]:
    payload = {
        "case": {
            "id": case.id,
            "title": case.title,
            "task_prompt": case.task_prompt,
            "rule_checks": case.rule_checks,
        },
        "rubric_weights": rubric_weights,
        "run_artifact": {
            "case_id": run_artifact.case_id,
            "model_label": run_artifact.model_label,
            "provider": run_artifact.provider,
            "model": run_artifact.model,
            "status": run_artifact.status,
            "duration_seconds": run_artifact.duration_seconds,
            "final_answer": run_artifact.final_answer,
            "bundle": run_artifact.bundle,
            "tool_usage": run_artifact.tool_usage,
            "rule_scores": run_artifact.rule_scores,
            "message_list": run_artifact.messages,
        },
    }
    return [
        {
            "role": "user",
            "content": (
                "Evaluate the following benchmark run and return the structured judge report.\n\n"
                f"{json.dumps(payload, ensure_ascii=False, indent=2, default=str)}"
            ),
        }
    ]


def build_skipped_judge_report(
    *,
    case: EvalCase,
    run_artifact: RunArtifact,
    judge_provider: str,
    judge_model: str,
    judge_label: str,
    reason: str,
) -> JudgeReport:
    return JudgeReport(
        case_id=case.id,
        case_title=case.title,
        provider=run_artifact.provider,
        model=run_artifact.model,
        model_label=run_artifact.model_label,
        judge_provider=judge_provider,
        judge_model=judge_model,
        judge_label=judge_label,
        status="skipped",
        skip_reason=reason,
    )


def parse_judge_output(
    *,
    case: EvalCase,
    run_artifact: RunArtifact,
    judge_provider: str,
    judge_model: str,
    judge_label: str,
    messages: list[dict[str, Any]],
    bundle: dict[str, Any],
) -> JudgeReport:
    raw_content = ""
    for message in reversed(messages or []):
        if isinstance(message, dict) and message.get("role") == "assistant":
            content = message.get("content")
            if isinstance(content, str):
                raw_content = content.strip()
                break
    parsed = json.loads(raw_content or "{}")
    return JudgeReport(
        case_id=case.id,
        case_title=case.title,
        provider=run_artifact.provider,
        model=run_artifact.model,
        model_label=run_artifact.model_label,
        judge_provider=judge_provider,
        judge_model=judge_model,
        judge_label=judge_label,
        status="completed",
        overall_score=float(parsed.get("overall_score", 0)),
        rubric_scores={key: float(value) for key, value in dict(parsed.get("rubric_scores") or {}).items()},
        summary=str(parsed.get("summary") or ""),
        failure_modes=[str(item) for item in parsed.get("failure_modes", [])],
        debug_notes=[str(item) for item in parsed.get("debug_notes", [])],
        recommendations=[str(item) for item in parsed.get("recommendations", [])],
        prompt_suggestions=[str(item) for item in parsed.get("prompt_suggestions", [])],
        tooling_suggestions=[str(item) for item in parsed.get("tooling_suggestions", [])],
        raw_messages=messages,
        raw_bundle=bundle,
    )

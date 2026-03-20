from __future__ import annotations

import re
from collections import Counter
from typing import Any

from .types import EvalCase, RunArtifact


_PATH_PATTERN = re.compile(r"(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+(?:\.[A-Za-z0-9_.-]+)?")


def extract_last_assistant_text(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages or []):
        if isinstance(message, dict) and message.get("role") == "assistant":
            content = message.get("content")
            if isinstance(content, str):
                return content.strip()
    return ""


def summarize_tool_usage(callback_events: list[dict[str, Any]]) -> dict[str, Any]:
    by_tool: Counter[str] = Counter()
    terminal_commands: list[str] = []
    failures: list[dict[str, Any]] = []
    blocked_attempts: list[dict[str, Any]] = []
    denied_tools: list[str] = []

    for event in callback_events or []:
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type") or "")
        tool_name = str(event.get("tool_name") or "")

        if event_type == "tool_call" and tool_name:
            by_tool[tool_name] += 1
            arguments = event.get("arguments") or {}
            if tool_name == "terminal_exec" and isinstance(arguments, dict):
                command = str(arguments.get("command") or "").strip()
                if command:
                    terminal_commands.append(command)

        if event_type == "tool_result" and tool_name:
            result = event.get("result") or {}
            error_text = str(result.get("error") or "").strip() if isinstance(result, dict) else ""
            if error_text:
                failure = {"tool": tool_name, "error": error_text}
                failures.append(failure)
                if "blocked by strict mode" in error_text.lower():
                    blocked_attempts.append(failure)

        if event_type == "tool_denied" and tool_name:
            denied_tools.append(tool_name)

    return {
        "total_calls": sum(by_tool.values()),
        "by_tool": dict(by_tool),
        "terminal_commands": terminal_commands,
        "failed_calls": failures,
        "blocked_attempts": blocked_attempts,
        "denied_tools": denied_tools,
    }


def count_file_references(text: str) -> int:
    return len({match.group(0) for match in _PATH_PATTERN.finditer(text or "")})


def score_run_artifact(run_artifact: RunArtifact, case: EvalCase) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    final_answer = str(run_artifact.final_answer or "")
    final_answer_lower = final_answer.lower()
    used_tools = set((run_artifact.tool_usage or {}).get("by_tool", {}).keys())
    failed_errors = [
        str(item.get("error") or "").lower()
        for item in (run_artifact.tool_usage or {}).get("failed_calls", [])
        if isinstance(item, dict)
    ]

    def add_check(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": bool(passed), "detail": detail, "weight": 1})

    add_check(
        "status_completed",
        run_artifact.status == "completed",
        f"run status was {run_artifact.status}",
    )

    rule_checks = case.rule_checks or {}

    for required_path in rule_checks.get("required_paths", []):
        add_check(
            f"path:{required_path}",
            required_path.lower() in final_answer_lower,
            f"final answer should reference {required_path}",
        )

    for required_text in rule_checks.get("required_substrings", []):
        add_check(
            f"text:{required_text}",
            required_text.lower() in final_answer_lower,
            f"final answer should mention {required_text}",
        )

    for index, group in enumerate(rule_checks.get("required_any_substrings", []), start=1):
        normalized = [str(item).lower() for item in group]
        add_check(
            f"text-any:{index}",
            any(item in final_answer_lower for item in normalized),
            f"final answer should mention one of: {', '.join(group)}",
        )

    for pattern in rule_checks.get("required_regexes", []):
        add_check(
            f"regex:{pattern}",
            bool(re.search(pattern, final_answer, flags=re.IGNORECASE | re.MULTILINE)),
            f"final answer should match regex: {pattern}",
        )

    for tool_name in rule_checks.get("required_tool_names", []):
        add_check(
            f"tool:{tool_name}",
            tool_name in used_tools,
            f"run should use tool {tool_name}",
        )

    for index, group in enumerate(rule_checks.get("required_tool_any_of", []), start=1):
        add_check(
            f"tool-any:{index}",
            any(str(tool_name) in used_tools for tool_name in group),
            f"run should use one of: {', '.join(group)}",
        )

    for forbidden_tool in rule_checks.get("forbidden_tool_names", []):
        add_check(
            f"forbidden-tool:{forbidden_tool}",
            forbidden_tool not in used_tools,
            f"run should not use tool {forbidden_tool}",
        )

    for forbidden_error in rule_checks.get("forbidden_result_substrings", []):
        add_check(
            f"forbidden-error:{forbidden_error}",
            all(forbidden_error.lower() not in error for error in failed_errors),
            f"tool results should not contain: {forbidden_error}",
        )

    min_tool_calls = rule_checks.get("min_tool_calls")
    if min_tool_calls is not None:
        total_calls = int((run_artifact.tool_usage or {}).get("total_calls", 0))
        add_check(
            "min_tool_calls",
            total_calls >= int(min_tool_calls),
            f"run made {total_calls} tool calls; expected at least {min_tool_calls}",
        )

    min_final_chars = rule_checks.get("min_final_chars")
    if min_final_chars is not None:
        add_check(
            "min_final_chars",
            len(final_answer) >= int(min_final_chars),
            f"final answer length was {len(final_answer)} chars; expected at least {min_final_chars}",
        )

    min_file_references = rule_checks.get("min_file_reference_count")
    if min_file_references is not None:
        file_reference_count = count_file_references(final_answer)
        add_check(
            "min_file_reference_count",
            file_reference_count >= int(min_file_references),
            f"final answer referenced {file_reference_count} file-like paths; expected at least {min_file_references}",
        )

    passed_checks = sum(1 for check in checks if check["passed"])
    total_checks = len(checks)
    score_pct = round((passed_checks / total_checks) * 100.0, 2) if total_checks else 0.0

    return {
        "score_pct": score_pct,
        "passed_checks": passed_checks,
        "total_checks": total_checks,
        "checks": checks,
        "file_reference_count": count_file_references(final_answer),
    }

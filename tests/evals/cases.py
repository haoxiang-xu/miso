from __future__ import annotations

from pathlib import Path

from .types import DEFAULT_RUBRIC_WEIGHTS, EvalCase


def build_eval_case(
    *,
    case_id: str,
    title: str,
    task_prompt: str,
    workspace_mode: str,
    workspace_source: str,
    allowed_toolkits: tuple[str, ...] | list[str] | None = None,
    toolkit_options: dict[str, dict[str, object]] | None = None,
    rule_checks: dict[str, object] | None = None,
    rubric_weights: dict[str, int] | None = None,
    candidate_instructions: str = "",
) -> EvalCase:
    return EvalCase(
        id=str(case_id).strip(),
        title=str(title).strip(),
        task_prompt=str(task_prompt),
        workspace_mode=str(workspace_mode).strip(),
        workspace_source=str(workspace_source).strip(),
        allowed_toolkits=tuple(allowed_toolkits or ("workspace", "terminal")),
        toolkit_options={
            str(key): dict(value or {})
            for key, value in dict(toolkit_options or {}).items()
        },
        rule_checks=dict(rule_checks or {}),
        rubric_weights=dict(rubric_weights or DEFAULT_RUBRIC_WEIGHTS),
        candidate_instructions=str(candidate_instructions or "").strip(),
    )


def list_eval_cases(repo_root: str | Path) -> list[EvalCase]:
    del repo_root
    return [
        build_eval_case(
            case_id="repo_trace",
            title="Trace Repo Architecture",
            workspace_mode="repo_copy",
            workspace_source=".",
            task_prompt=(
                "Inspect this repository snapshot and explain how the project is organized.\n"
                "Answer these questions:\n"
                "1. What are the main runtime layers and how do they relate?\n"
                "2. Which built-in toolkits are available?\n"
                "3. How does structured output work in this repo?\n"
                "4. What command should a contributor run to execute tests?\n\n"
                "Requirements:\n"
                "- Use tools to inspect the codebase. Do not guess.\n"
                "- Do not modify files.\n"
                "- Cite at least 4 relative file paths as evidence.\n"
                "- Mention the exact test command.\n"
                "- Keep the final answer concise but concrete.\n"
            ),
            rule_checks={
                "required_paths": [
                    "src/miso/agents/agent.py",
                    "src/miso/runtime/engine.py",
                    "src/miso/schemas/response.py",
                    "README.md",
                ],
                "required_substrings": [
                    "run_tests.sh",
                    "workspace",
                    "terminal",
                    "ResponseFormat",
                ],
                "required_any_substrings": [
                    ["Agent", "agent"],
                    ["broth", "Broth"],
                ],
                "required_tool_any_of": [["list_directories", "search_text", "read_files"]],
                "min_tool_calls": 3,
                "min_final_chars": 180,
                "min_file_reference_count": 4,
                "forbidden_tool_names": [
                    "write_file",
                    "insert_lines",
                    "replace_lines",
                    "delete_lines",
                ],
                "forbidden_result_substrings": ["blocked by strict mode"],
            },
        ),
        build_eval_case(
            case_id="fixture_debug",
            title="Debug A Failing Fixture",
            workspace_mode="fixture_copy",
            workspace_source="tests/evals/fixtures/fixture_debug",
            task_prompt=(
                "This workspace contains a small Python project with a failing test.\n"
                "Use tools to inspect the files, run the relevant tests, identify the root cause, "
                "and propose the minimal fix plan.\n\n"
                "Requirements:\n"
                "- Do not modify files.\n"
                "- Include the exact failing command you ran.\n"
                "- Name the broken file and the failing test file.\n"
                "- Explain the root cause precisely.\n"
                "- Keep the answer focused on diagnosis and the minimal fix plan.\n"
            ),
            rule_checks={
                "required_paths": [
                    "src/reporting.py",
                    "tests/test_reporting.py",
                ],
                "required_substrings": [
                    "pytest",
                ],
                "required_regexes": [
                    r"(floor|integer)\s+division",
                ],
                "required_tool_names": ["terminal_exec"],
                "required_tool_any_of": [["read_files", "read_lines", "search_text"]],
                "min_tool_calls": 2,
                "min_final_chars": 140,
                "min_file_reference_count": 2,
                "forbidden_tool_names": [
                    "write_file",
                    "insert_lines",
                    "replace_lines",
                    "delete_lines",
                ],
            },
        ),
        build_eval_case(
            case_id="multi_file_plan",
            title="Plan A Multi-File Change",
            workspace_mode="fixture_copy",
            workspace_source="tests/evals/fixtures/multi_file_plan",
            task_prompt=(
                "Read CHANGE_REQUEST.md and inspect the project files it references.\n"
                "Produce a concrete implementation and test plan for the requested feature.\n\n"
                "Requirements:\n"
                "- Do not modify files.\n"
                "- Reference the exact files that need to change.\n"
                "- Explain the data flow or API changes.\n"
                "- Include the test cases that should be added or updated.\n"
                "- Keep the output implementation-ready.\n"
            ),
            rule_checks={
                "required_paths": [
                    "CHANGE_REQUEST.md",
                    "src/catalog.py",
                    "src/service.py",
                    "tests/test_service.py",
                ],
                "required_substrings": [
                    "test",
                    "filter",
                ],
                "required_tool_any_of": [["read_files", "read_lines", "search_text", "list_directories"]],
                "min_tool_calls": 2,
                "min_final_chars": 160,
                "min_file_reference_count": 3,
                "forbidden_tool_names": [
                    "write_file",
                    "insert_lines",
                    "replace_lines",
                    "delete_lines",
                ],
            },
        ),
    ]


def get_eval_case(case_id: str, repo_root: str | Path) -> EvalCase:
    for case in list_eval_cases(repo_root):
        if case.id == case_id:
            return case
    raise KeyError(f"unknown eval case: {case_id}")

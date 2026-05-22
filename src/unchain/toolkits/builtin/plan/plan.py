from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ....tools.models import ToolPromptSpec
from ....tools.toolkit import Toolkit


_STEP_STATUS_MARKERS = {
    "pending": "[ ]",
    "in_progress": "[~]",
    "completed": "[x]",
}
_PLAN_STATUSES = {"draft", "finalized"}
_PLANS_STATE_KEY = "plans"
_PLAN_ITEMS_KEY = "items"
_ACTIVE_PLAN_ID_KEY = "active_plan_id"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class _PlanStep:
    step: str
    status: str = "pending"

    @classmethod
    def from_raw(cls, raw: Any) -> "_PlanStep":
        if isinstance(raw, str):
            step = raw.strip()
            status = "pending"
        elif isinstance(raw, dict):
            step = str(raw.get("step") or raw.get("title") or "").strip()
            status = str(raw.get("status") or "pending").strip()
        else:
            raise ValueError("each step must be a string or object")

        if not step:
            raise ValueError("step text is required")
        if status not in _STEP_STATUS_MARKERS:
            raise ValueError(
                f"invalid step status '{status}'; expected one of "
                f"{', '.join(sorted(_STEP_STATUS_MARKERS))}"
            )
        return cls(step=step, status=status)

    def to_dict(self) -> dict[str, str]:
        return {"step": self.step, "status": self.status}


@dataclass
class _PlanState:
    plan_id: str
    title: str
    goal: str
    constraints: list[str] = field(default_factory=list)
    summary: str = ""
    steps: list[_PlanStep] = field(default_factory=list)
    key_changes: list[str] = field(default_factory=list)
    public_interfaces: list[str] = field(default_factory=list)
    test_cases: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    references: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    status: str = "draft"
    revision: int = 1
    created_at: str = field(default_factory=_utc_now)
    updated_at: str = field(default_factory=_utc_now)

    def touch(self) -> None:
        self.revision += 1
        self.updated_at = _utc_now()

    @classmethod
    def from_raw(cls, raw: Any) -> "_PlanState":
        if not isinstance(raw, dict):
            raise ValueError("plan state must be an object")

        created_at = raw.get("created_at")
        updated_at = raw.get("updated_at")
        status = str(raw.get("status") or "draft").strip()
        if status not in _PLAN_STATUSES:
            status = "draft"

        try:
            revision = max(1, int(raw.get("revision") or 1))
        except Exception:
            revision = 1

        return cls(
            plan_id=_clean_required_text(raw.get("plan_id"), "plan_id"),
            title=_clean_required_text(raw.get("title"), "title"),
            goal=_clean_required_text(raw.get("goal"), "goal"),
            constraints=_coerce_text_list(raw.get("constraints"), "constraints"),
            summary=str(raw.get("summary") or "").strip(),
            steps=_coerce_steps(raw.get("steps")),
            key_changes=_coerce_text_list(raw.get("key_changes"), "key_changes"),
            public_interfaces=_coerce_text_list(
                raw.get("public_interfaces"),
                "public_interfaces",
            ),
            test_cases=_coerce_text_list(raw.get("test_cases"), "test_cases"),
            assumptions=_coerce_text_list(raw.get("assumptions"), "assumptions"),
            references=_coerce_text_list(raw.get("references"), "references"),
            open_questions=_coerce_text_list(raw.get("open_questions"), "open_questions"),
            status=status,
            revision=revision,
            created_at=created_at if isinstance(created_at, str) and created_at else _utc_now(),
            updated_at=updated_at if isinstance(updated_at, str) and updated_at else _utc_now(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "title": self.title,
            "goal": self.goal,
            "constraints": list(self.constraints),
            "summary": self.summary,
            "steps": [step.to_dict() for step in self.steps],
            "key_changes": list(self.key_changes),
            "public_interfaces": list(self.public_interfaces),
            "test_cases": list(self.test_cases),
            "assumptions": list(self.assumptions),
            "references": list(self.references),
            "open_questions": list(self.open_questions),
            "status": self.status,
            "revision": self.revision,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def _error(message: str, *, plan_id: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"ok": False}
    if plan_id is not None:
        result["plan_id"] = plan_id
    result["error"] = message
    return result


def _clean_required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _coerce_text_list(value: Any, field_name: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else []
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field_name} must be a string or list of strings")

    items: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise ValueError(f"{field_name}[{index}] must be a string")
        stripped = item.strip()
        if stripped:
            items.append(stripped)
    return items


def _coerce_steps(value: Any) -> list[_PlanStep]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise ValueError("steps must be a list")
    steps = [_PlanStep.from_raw(item) for item in value]
    active_count = sum(1 for step in steps if step.status == "in_progress")
    if active_count > 1:
        raise ValueError("at most one step can be in_progress")
    return steps


def _append_list_section(lines: list[str], title: str, values: list[str]) -> None:
    if not values:
        return
    lines.extend(["", f"## {title}"])
    lines.extend(f"- {item}" for item in values)


class PlanToolkit(Toolkit):
    """Planning toolkit for drafting, reading, and finalizing implementation plans."""

    def __init__(
        self,
        *,
        session_store: Any = None,
        session_id: str = "",
    ) -> None:
        super().__init__()
        self._session_store = session_store
        self._session_id = session_id.strip() if isinstance(session_id, str) else ""
        self._plans: dict[str, _PlanState] = {}
        self._active_plan_id = ""
        self._next_plan_number = 1
        self._register_tools()

    def _register_tools(self) -> None:
        self.register(
            self.plan_start,
            description="Create a new draft plan in session state and return its plan_id.",
            prompt_spec=ToolPromptSpec(
                purpose="Start a structured draft plan before implementation decisions are complete.",
                when_to_use=(
                    "The user asks for a plan or a design-first workflow.",
                    "You have gathered enough context to name the goal and important constraints.",
                ),
                when_not_to_use=(
                    "You are only answering a small direct question.",
                    "A plan already exists for the same work; update it instead.",
                ),
                examples=(
                    'plan_start(title="Plan toolkit", goal="Add a builtin toolkit for structured planning.")',
                ),
                advanced_tips=(
                    "Use CoreToolkit.ask_user_question alongside this toolkit when a key ambiguity changes the plan.",
                ),
            ),
        )
        self.register(
            self.plan_update,
            description=(
                "Replace structured sections of a draft plan such as summary, steps, tests, "
                "assumptions, and references."
            ),
            prompt_spec=ToolPromptSpec(
                purpose="Keep a plan's structured fields current as exploration and decisions evolve.",
                when_to_use=(
                    "New repository facts, decisions, test cases, or implementation steps should be captured.",
                    "A plan needs status updates after review or before finalization.",
                ),
                when_not_to_use=(
                    "The update is purely conversational and should not change the plan state.",
                    "You need user input before choosing between materially different approaches.",
                ),
                examples=(
                    'plan_update(plan_id="plan_1", steps=[{"step": "Add tests", "status": "completed"}])',
                ),
                advanced_tips=(
                    "Use only one in_progress step. Use completed, in_progress, or pending for step statuses.",
                    "Pass an empty list to clear a list section; omit a field to leave it unchanged.",
                ),
            ),
        )
        self.register(
            self.plan_read,
            description="Return a plan's structured state and rendered Markdown.",
            prompt_spec=ToolPromptSpec(
                purpose="Inspect the current plan state before revising, summarizing, or finalizing it.",
                when_to_use=(
                    "You need the latest draft contents or rendered Markdown.",
                    "The user asks to see the current plan.",
                ),
            ),
        )
        self.register(
            self.plan_finalize,
            description="Finalize a plan and return Markdown plus a Codex-compatible proposed_plan block.",
            requires_confirmation=True,
            prompt_spec=ToolPromptSpec(
                purpose="Finalize a decision-complete plan after user confirmation.",
                when_to_use=(
                    "The plan has enough detail to guide implementation or handoff.",
                    "Open questions are resolved or explicitly accepted as assumptions.",
                ),
                when_not_to_use=(
                    "Critical ambiguity remains and should be clarified first.",
                    "You have not read or updated the draft after the latest decisions.",
                ),
                examples=('plan_finalize(plan_id="plan_1")',),
                advanced_tips=(
                    "This tool requires confirmation. Do not call it as a substitute for asking clarifying questions.",
                    "The returned proposed_plan block is meant for Codex-style plan approval flows.",
                ),
            ),
        )
        self.register(
            self.plan_list,
            description="List draft and finalized plans stored for this toolkit session.",
            prompt_spec=ToolPromptSpec(
                purpose="Find existing draft or finalized plan ids in the current session.",
                when_to_use=(
                    "You need to resume or inspect a plan but do not know its plan_id.",
                    "The user asks which plans exist.",
                ),
            ),
        )

    def _new_plan_id(self) -> str:
        self._load_plans()
        plan_id = f"plan_{self._next_plan_number}"
        self._next_plan_number += 1
        return plan_id

    def _has_session_store(self) -> bool:
        return (
            bool(self._session_id)
            and self._session_store is not None
            and callable(getattr(self._session_store, "load", None))
            and callable(getattr(self._session_store, "save", None))
        )

    def _load_session_state(self) -> dict[str, Any]:
        if not self._has_session_store():
            return {}
        state = self._session_store.load(self._session_id)
        return state if isinstance(state, dict) else {}

    def _next_plan_number_from(self, plans: dict[str, _PlanState]) -> int:
        next_number = 1
        for plan_id in plans:
            prefix, _, suffix = plan_id.partition("_")
            if prefix == "plan" and suffix.isdigit():
                next_number = max(next_number, int(suffix) + 1)
        return next_number

    def _load_plans(self) -> dict[str, _PlanState]:
        if not self._has_session_store():
            return self._plans

        state = self._load_session_state()
        plans_state = state.get(_PLANS_STATE_KEY)
        if not isinstance(plans_state, dict):
            self._plans = {}
            self._active_plan_id = ""
            self._next_plan_number = 1
            return self._plans

        raw_items = plans_state.get(_PLAN_ITEMS_KEY)
        if not isinstance(raw_items, dict):
            raw_items = {}

        plans: dict[str, _PlanState] = {}
        for raw_plan_id, raw_plan in raw_items.items():
            try:
                plan = _PlanState.from_raw(raw_plan)
            except Exception:
                continue
            plan_id = str(raw_plan_id or plan.plan_id).strip() or plan.plan_id
            if plan.plan_id != plan_id:
                plan.plan_id = plan_id
            plans[plan_id] = plan

        active_plan_id = str(plans_state.get(_ACTIVE_PLAN_ID_KEY) or "").strip()
        self._plans = plans
        self._active_plan_id = active_plan_id if active_plan_id in plans else ""
        self._next_plan_number = self._next_plan_number_from(plans)
        return self._plans

    def _save_plans(self, *, active_plan_id: str | None = None) -> None:
        if not self._has_session_store():
            if active_plan_id is not None:
                self._active_plan_id = active_plan_id
            return

        state = self._load_session_state()
        selected_active_plan_id = (
            active_plan_id if active_plan_id is not None else self._active_plan_id
        )
        if selected_active_plan_id not in self._plans:
            selected_active_plan_id = ""
        self._active_plan_id = selected_active_plan_id
        state[_PLANS_STATE_KEY] = {
            _ACTIVE_PLAN_ID_KEY: selected_active_plan_id,
            _PLAN_ITEMS_KEY: {
                plan_id: plan.to_dict()
                for plan_id, plan in self._plans.items()
            },
        }
        self._session_store.save(self._session_id, state)

    def _get_plan(self, plan_id: str) -> _PlanState | dict[str, Any]:
        if not isinstance(plan_id, str) or not plan_id.strip():
            return _error(
                "plan_id must be a non-empty string",
                plan_id=plan_id if isinstance(plan_id, str) else None,
        )
        normalized = plan_id.strip()
        plan = self._load_plans().get(normalized)
        if plan is None:
            return _error(f"unknown plan_id: {normalized}", plan_id=normalized)
        return plan

    def _artifact(self, plan: _PlanState) -> dict[str, Any]:
        return {
            "type": "plan_doc",
            "plan_id": plan.plan_id,
            "revision": plan.revision,
            "status": plan.status,
            "title": plan.title,
        }

    def _result(self, plan: _PlanState, *, proposed_plan: str | None = None) -> dict[str, Any]:
        artifact = self._artifact(plan)
        result: dict[str, Any] = {
            "ok": True,
            "plan_id": plan.plan_id,
            "status": plan.status,
            "revision": plan.revision,
            "plan": plan.to_dict(),
            "markdown": self._render_markdown(plan),
            "artifact": artifact,
            "artifacts": [artifact],
        }
        if proposed_plan is not None:
            result["proposed_plan"] = proposed_plan
        return result

    def _render_markdown(self, plan: _PlanState) -> str:
        lines = [f"# {plan.title}", "", "## Summary", plan.summary or plan.goal, "", "## Goal", plan.goal]
        _append_list_section(lines, "Constraints", plan.constraints)

        if plan.steps:
            lines.extend(["", "## Steps"])
            for step in plan.steps:
                marker = _STEP_STATUS_MARKERS[step.status]
                lines.append(f"- {marker} {step.step}")

        _append_list_section(lines, "Key Changes", plan.key_changes)
        _append_list_section(lines, "Public Interfaces", plan.public_interfaces)
        _append_list_section(lines, "Test Cases", plan.test_cases)
        _append_list_section(lines, "Assumptions", plan.assumptions)
        _append_list_section(lines, "References", plan.references)
        _append_list_section(lines, "Open Questions", plan.open_questions)

        return "\n".join(lines).rstrip()

    def plan_start(
        self,
        title: str,
        goal: str,
        constraints: list[str] | str | None = None,
    ) -> dict[str, Any]:
        """Create a draft plan and return its plan_id.

        :param title: Short plan title.
        :param goal: Concrete goal the plan should satisfy.
        :param constraints: Optional constraints as a string or list of strings.
        """
        try:
            clean_title = _clean_required_text(title, "title")
            clean_goal = _clean_required_text(goal, "goal")
            clean_constraints = _coerce_text_list(constraints, "constraints")
            plan_id = self._new_plan_id()
            plan = _PlanState(
                plan_id=plan_id,
                title=clean_title,
                goal=clean_goal,
                constraints=clean_constraints,
            )
            self._plans[plan_id] = plan
            self._save_plans(active_plan_id=plan_id)
            return self._result(plan)
        except Exception as exc:
            return _error(str(exc))

    def plan_update(
        self,
        plan_id: str,
        summary: str | None = None,
        steps: list[dict[str, Any] | str] | None = None,
        key_changes: list[str] | str | None = None,
        public_interfaces: list[str] | str | None = None,
        test_cases: list[str] | str | None = None,
        assumptions: list[str] | str | None = None,
        references: list[str] | str | None = None,
        open_questions: list[str] | str | None = None,
        constraints: list[str] | str | None = None,
    ) -> dict[str, Any]:
        """Update structured sections of a plan.

        :param plan_id: Existing plan id returned by plan_start.
        :param summary: Optional summary text. Omit to keep the current value.
        :param steps: Optional list of step strings or objects with step/status.
        :param key_changes: Optional list of implementation changes.
        :param public_interfaces: Optional list of public API or behavior changes.
        :param test_cases: Optional list of tests to add or run.
        :param assumptions: Optional list of explicit assumptions.
        :param references: Optional list of reference material.
        :param open_questions: Optional list of unresolved questions.
        :param constraints: Optional replacement list of constraints.
        """
        plan = self._get_plan(plan_id)
        if isinstance(plan, dict):
            return plan
        try:
            if summary is not None:
                if not isinstance(summary, str):
                    raise ValueError("summary must be a string")
                plan.summary = summary.strip()
            if steps is not None:
                plan.steps = _coerce_steps(steps)
            if key_changes is not None:
                plan.key_changes = _coerce_text_list(key_changes, "key_changes")
            if public_interfaces is not None:
                plan.public_interfaces = _coerce_text_list(public_interfaces, "public_interfaces")
            if test_cases is not None:
                plan.test_cases = _coerce_text_list(test_cases, "test_cases")
            if assumptions is not None:
                plan.assumptions = _coerce_text_list(assumptions, "assumptions")
            if references is not None:
                plan.references = _coerce_text_list(references, "references")
            if open_questions is not None:
                plan.open_questions = _coerce_text_list(open_questions, "open_questions")
            if constraints is not None:
                plan.constraints = _coerce_text_list(constraints, "constraints")
            if plan.status not in _PLAN_STATUSES:
                raise ValueError(f"invalid plan status: {plan.status}")
            if plan.status == "finalized":
                plan.status = "draft"
            plan.touch()
            self._plans[plan.plan_id] = plan
            self._save_plans(active_plan_id=plan.plan_id)
            return self._result(plan)
        except Exception as exc:
            return _error(str(exc), plan_id=plan.plan_id)

    def plan_read(self, plan_id: str) -> dict[str, Any]:
        """Return structured plan state and rendered Markdown.

        :param plan_id: Existing plan id returned by plan_start.
        """
        plan = self._get_plan(plan_id)
        if isinstance(plan, dict):
            return plan
        return self._result(plan)

    def plan_finalize(self, plan_id: str) -> dict[str, Any]:
        """Finalize a plan and return Markdown plus a proposed_plan block.

        :param plan_id: Existing plan id returned by plan_start.
        """
        plan = self._get_plan(plan_id)
        if isinstance(plan, dict):
            return plan
        try:
            plan.status = "finalized"
            plan.touch()
            self._plans[plan.plan_id] = plan
            self._save_plans(active_plan_id=plan.plan_id)
            markdown = self._render_markdown(plan)
            proposed_plan = f"<proposed_plan>\n{markdown}\n</proposed_plan>"
            return self._result(plan, proposed_plan=proposed_plan)
        except Exception as exc:
            return _error(str(exc), plan_id=plan.plan_id)

    def plan_list(self) -> dict[str, Any]:
        """List all draft and finalized plans in this toolkit instance."""
        self._load_plans()
        return {
            "ok": True,
            "plans": [
                {
                    "plan_id": plan.plan_id,
                    "title": plan.title,
                    "status": plan.status,
                    "revision": plan.revision,
                }
                for plan in self._plans.values()
            ],
        }


__all__ = ["PlanToolkit"]

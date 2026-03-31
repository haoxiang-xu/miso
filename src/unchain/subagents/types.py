from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Literal


SubagentMode = Literal["delegate", "handoff", "worker"]
SubagentOutputMode = Literal["summary", "last_message", "full_trace"]
SubagentMemoryPolicy = Literal["ephemeral", "scoped_persistent"]


@dataclass(frozen=True)
class SubagentTemplate:
    name: str
    description: str
    agent: Any | None = None
    allowed_modes: tuple[SubagentMode, ...] = ("delegate", "handoff", "worker")
    output_mode: SubagentOutputMode = "summary"
    memory_policy: SubagentMemoryPolicy = "ephemeral"
    parallel_safe: bool = False
    allowed_tools: tuple[str, ...] | None = None
    model: str | None = None

    def supports_mode(self, mode: SubagentMode) -> bool:
        return mode in self.allowed_modes


@dataclass(frozen=True)
class SubagentPolicy:
    max_depth: int = 6
    max_children_per_parent: int = 10
    max_total_subagents: int = 100
    max_parallel_workers: int = 4
    worker_timeout_seconds: float = 30.0
    allow_dynamic_workers: bool = False
    allow_dynamic_delegate: bool = False
    handoff_requires_template: bool = True


@dataclass
class SubagentState:
    root_agent_id: str = ""
    active_agent_id: str = ""
    active_lineage: list[str] = field(default_factory=list)
    handoff_stack: list[dict[str, Any]] = field(default_factory=list)
    lineage_counters: dict[str, int] = field(default_factory=dict)
    running_batches: dict[str, Any] = field(default_factory=dict)
    blocked_clarifications: list[dict[str, Any]] = field(default_factory=list)
    spawn_stats: dict[str, int] = field(
        default_factory=lambda: {"delegate": 0, "handoff": 0, "worker": 0}
    )

    def copy(self) -> "SubagentState":
        return SubagentState(
            root_agent_id=self.root_agent_id,
            active_agent_id=self.active_agent_id,
            active_lineage=list(self.active_lineage),
            handoff_stack=copy.deepcopy(self.handoff_stack),
            lineage_counters=dict(self.lineage_counters),
            running_batches=copy.deepcopy(self.running_batches),
            blocked_clarifications=copy.deepcopy(self.blocked_clarifications),
            spawn_stats=dict(self.spawn_stats),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "root_agent_id": self.root_agent_id,
            "active_agent_id": self.active_agent_id,
            "active_lineage": list(self.active_lineage),
            "handoff_stack": copy.deepcopy(self.handoff_stack),
            "lineage_counters": dict(self.lineage_counters),
            "running_batches": copy.deepcopy(self.running_batches),
            "blocked_clarifications": copy.deepcopy(self.blocked_clarifications),
            "spawn_stats": dict(self.spawn_stats),
        }

    @classmethod
    def from_raw(cls, raw: Any) -> "SubagentState":
        if isinstance(raw, SubagentState):
            return raw.copy()
        if not isinstance(raw, dict):
            return cls()
        state = cls()
        state.root_agent_id = str(raw.get("root_agent_id") or "")
        state.active_agent_id = str(raw.get("active_agent_id") or "")
        active_lineage = raw.get("active_lineage")
        if isinstance(active_lineage, list):
            state.active_lineage = [str(item) for item in active_lineage if isinstance(item, str)]
        handoff_stack = raw.get("handoff_stack")
        if isinstance(handoff_stack, list):
            state.handoff_stack = [copy.deepcopy(item) for item in handoff_stack if isinstance(item, dict)]
        lineage_counters = raw.get("lineage_counters")
        if isinstance(lineage_counters, dict):
            state.lineage_counters = {
                str(key): int(value)
                for key, value in lineage_counters.items()
                if isinstance(key, str) and isinstance(value, int)
            }
        running_batches = raw.get("running_batches")
        if isinstance(running_batches, dict):
            state.running_batches = copy.deepcopy(running_batches)
        blocked = raw.get("blocked_clarifications")
        if isinstance(blocked, list):
            state.blocked_clarifications = [copy.deepcopy(item) for item in blocked if isinstance(item, dict)]
        spawn_stats = raw.get("spawn_stats")
        if isinstance(spawn_stats, dict):
            for mode in ("delegate", "handoff", "worker"):
                if isinstance(spawn_stats.get(mode), int):
                    state.spawn_stats[mode] = int(spawn_stats[mode])
        return state

    def merged(self, raw: Any) -> "SubagentState":
        update = SubagentState.from_raw(raw)
        current = self.copy()
        if update.root_agent_id:
            current.root_agent_id = update.root_agent_id
        if update.active_agent_id:
            current.active_agent_id = update.active_agent_id
        if update.active_lineage:
            current.active_lineage = list(update.active_lineage)
        if update.handoff_stack:
            current.handoff_stack = copy.deepcopy(update.handoff_stack)
        if update.lineage_counters:
            current.lineage_counters.update(update.lineage_counters)
        if update.running_batches:
            current.running_batches.update(copy.deepcopy(update.running_batches))
        if update.blocked_clarifications:
            current.blocked_clarifications.extend(copy.deepcopy(update.blocked_clarifications))
        if update.spawn_stats:
            for key, value in update.spawn_stats.items():
                current.spawn_stats[key] = int(value)
        return current


@dataclass(frozen=True)
class SubagentResult:
    mode: str
    agent_name: str
    template_name: str | None
    status: str
    output: str = ""
    summary: str = ""
    messages: list[dict[str, Any]] = field(default_factory=list)
    lineage: list[str] = field(default_factory=list)
    clarification_request: dict[str, Any] | None = None
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "agent_name": self.agent_name,
            "template_name": self.template_name,
            "status": self.status,
            "output": self.output,
            "summary": self.summary,
            "messages": copy.deepcopy(self.messages),
            "lineage": list(self.lineage),
            "clarification_request": copy.deepcopy(self.clarification_request),
            "error": self.error,
        }

from __future__ import annotations

import copy
from dataclasses import dataclass

from ..capabilities import CreateArtifactOp, RunDelta
from ..kernel.harness import BaseRuntimeHarness, HarnessContext
from ..workspace_changes import WorkspaceChangeTracker


@dataclass
class WorkspaceChangeArtifactHarness(BaseRuntimeHarness):
    name: str = "workspace_change_artifacts"
    phases: tuple[str, ...] = ("run_finalizing",)
    order: int = 100

    def build_delta(self, context: HarnessContext) -> RunDelta | None:
        state = context.state
        if not isinstance(state.workspace_change_state, dict) or not state.workspace_change_state:
            return None

        run_id = str(context.event.get("run_id") or "kernel")
        tracker = WorkspaceChangeTracker.from_state(
            state.workspace_change_state,
            run_id=run_id,
            workspace_roots=[],
        )
        artifact = tracker.to_artifact()
        if artifact is None:
            return None

        artifact_id = str(artifact.get("artifact_id") or "")
        if not artifact_id:
            return None

        return RunDelta(
            created_by="runtime.workspace_change_artifacts",
            context_ops=(
                CreateArtifactOp(
                    artifact=copy.deepcopy(artifact),
                    reason="workspace_change_set",
                ),
            ),
            trace={
                "tool_name": "workspace_change_tracker",
            },
        )


__all__ = ["WorkspaceChangeArtifactHarness"]

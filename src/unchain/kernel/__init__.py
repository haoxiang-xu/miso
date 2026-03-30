from .delta import (
    ActivateVersionOp,
    AppendMessagesOp,
    DeleteSpanOp,
    HarnessDelta,
    InsertMessagesOp,
    ReplaceSpanOp,
    SuspendSignal,
)
from .harness import BaseRuntimeHarness, HarnessContext, RuntimeHarness, RuntimePhase
from .state import ProviderState, RunState, SessionState, SuspendState, TokenState
from .types import KernelRunResult, ModelTurnResult, TokenUsage, ToolCall
from .versioning import MessageVersion, MessageVersionGraph

__all__ = [
    "ActivateVersionOp",
    "AppendMessagesOp",
    "BaseRuntimeHarness",
    "DeleteSpanOp",
    "HarnessContext",
    "HarnessDelta",
    "InsertMessagesOp",
    "KernelLoop",
    "KernelRunResult",
    "LegacyBrothModelIO",
    "MessageVersion",
    "MessageVersionGraph",
    "ModelIO",
    "ModelTurnResult",
    "ModelTurnRequest",
    "ProviderState",
    "ReplaceSpanOp",
    "RunState",
    "RuntimeHarness",
    "RuntimePhase",
    "SessionState",
    "SuspendSignal",
    "SuspendState",
    "TokenState",
    "TokenUsage",
    "ToolCall",
]


def __getattr__(name):
    if name == "KernelLoop":
        from .loop import KernelLoop
        return KernelLoop
    if name in ("LegacyBrothModelIO", "ModelIO", "ModelTurnRequest"):
        from . import model_io
        return getattr(model_io, name)
    raise AttributeError(name)

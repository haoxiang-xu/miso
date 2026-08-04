"""Official AgentModule attachment for the default-closed Memory V2 host."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ...kernel.types import KernelRunResult
from ...memory.curator import RootRunCompletion
from ...memory.curator.host import MemoryAgentHostAdapter
from ...memory.toolkit import (
    MemoryToolkitRunBinding,
    NormalMemoryToolkitCapabilities,
)
from ..run_identity import MemoryV2RunRole
from .base import BaseAgentModule


class MemoryV2AgentModuleError(RuntimeError):
    """Stable host attachment or terminal identity failure."""


@dataclass(frozen=True)
class MemoryV2AgentAttachmentRequest:
    agent_name: str
    mode: str
    session_id: str
    attempt_id: str
    run_id: str
    role: MemoryV2RunRole
    root_run_id: str

    def __post_init__(self) -> None:
        for field_name in (
            "agent_name",
            "mode",
            "session_id",
            "attempt_id",
            "run_id",
            "root_run_id",
        ):
            if not isinstance(getattr(self, field_name), str):
                raise TypeError(f"{field_name} must be text")
        if not self.agent_name:
            raise ValueError("agent_name is required")
        if not self.mode:
            raise ValueError("mode is required")
        if not isinstance(self.role, MemoryV2RunRole):
            raise TypeError("role must be a MemoryV2RunRole")
        if not self.run_id:
            raise ValueError("run_id is required")
        if not self.root_run_id:
            raise ValueError("root_run_id is required")
        if (
            self.role is MemoryV2RunRole.SUBAGENT
            and self.run_id == self.root_run_id
        ):
            raise ValueError("subagent run identity must differ from root_run_id")


class MemoryV2RootCompletionFactory(Protocol):
    def build(
        self,
        *,
        result: KernelRunResult,
    ) -> RootRunCompletion | None:
        ...


@dataclass(frozen=True)
class MemoryV2AgentAttachment:
    binding: MemoryToolkitRunBinding
    capabilities: NormalMemoryToolkitCapabilities
    completion_factory: MemoryV2RootCompletionFactory | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.binding, MemoryToolkitRunBinding):
            raise TypeError("binding must be a MemoryToolkitRunBinding")
        if not isinstance(self.capabilities, NormalMemoryToolkitCapabilities):
            raise TypeError("capabilities must be NormalMemoryToolkitCapabilities")
        if self.completion_factory is not None and not callable(
            getattr(self.completion_factory, "build", None)
        ):
            raise TypeError("completion_factory must provide build(result=...)")


class MemoryV2AgentAttachmentFactory(Protocol):
    binding_id: str

    def attach(
        self,
        request: MemoryV2AgentAttachmentRequest,
    ) -> MemoryV2AgentAttachment:
        ...


@dataclass(frozen=True)
class MemoryV2AgentModule(BaseAgentModule):
    host: MemoryAgentHostAdapter | None = None
    attachment_factory: MemoryV2AgentAttachmentFactory | None = None
    name: str = "memory_v2"

    def configure(self, builder) -> None:
        host = self.host
        if host is None:
            return
        if not isinstance(host, MemoryAgentHostAdapter):
            raise TypeError("host must be a MemoryAgentHostAdapter")
        if not host.enabled:
            return

        factory = self.attachment_factory
        if factory is None or not callable(getattr(factory, "attach", None)):
            raise MemoryV2AgentModuleError(
                "enabled Memory V2 AgentModule requires an attachment factory"
            )
        if str(getattr(factory, "binding_id", "")) != host.binding_id:
            raise MemoryV2AgentModuleError(
                "attachment factory belongs to another host binding"
            )

        run_role = getattr(builder.call_context, "memory_v2_run_role", None)
        root_run_id = str(getattr(builder.call_context, "root_run_id", "") or "").strip()
        run_id = str(builder.call_context.run_id or "").strip()
        if not isinstance(run_role, MemoryV2RunRole) or not root_run_id:
            raise MemoryV2AgentModuleError(
                "enabled Memory V2 attachment requires explicit Memory V2 run identity"
            )
        if run_role is MemoryV2RunRole.SUBAGENT and run_id == root_run_id:
            raise MemoryV2AgentModuleError(
                "subagent run identity must differ from root_run_id"
            )

        request = MemoryV2AgentAttachmentRequest(
            agent_name=str(builder.spec.name or "").strip(),
            mode=str(builder.call_context.mode or "").strip(),
            session_id=str(builder.call_context.session_id or "").strip(),
            attempt_id=str(builder.call_context.execution_owner_id or "").strip(),
            run_id=run_id,
            role=run_role,
            root_run_id=root_run_id,
        )
        attachment = factory.attach(request)
        if not isinstance(attachment, MemoryV2AgentAttachment):
            raise MemoryV2AgentModuleError(
                "attachment factory returned an invalid attachment"
            )
        binding = attachment.binding
        if binding.binding_id != host.binding_id:
            raise MemoryV2AgentModuleError("attachment belongs to another host binding")
        for field_name in ("session_id", "attempt_id", "run_id"):
            requested = getattr(request, field_name)
            if requested and getattr(binding, field_name) != requested:
                raise MemoryV2AgentModuleError(
                    f"attachment {field_name} does not match the agent call"
                )

        toolkit = host.build_normal_toolkit(
            binding,
            attachment.capabilities,
        )
        if toolkit is None:
            raise MemoryV2AgentModuleError(
                "enabled Memory V2 host returned no normal toolkit"
            )

        completion_factory = attachment.completion_factory

        def enqueue_terminal(result: KernelRunResult) -> None:
            if completion_factory is None:
                return None
            completion = completion_factory.build(result=result)
            if completion is None:
                return None
            if not isinstance(completion, RootRunCompletion):
                raise MemoryV2AgentModuleError(
                    "completion factory returned an invalid root completion"
                )
            if (
                completion.session_id != binding.session_id
                or completion.attempt_id != binding.attempt_id
                or completion.run_id != binding.run_id
            ):
                raise MemoryV2AgentModuleError(
                    "root completion changed the attached run identity"
                )
            host.enqueue_root_completion(completion)
            return None

        builder.add_tool(toolkit)
        if (
            request.role is MemoryV2RunRole.ROOT
            and completion_factory is not None
        ):
            builder.add_run_hook(enqueue_terminal)


__all__ = [
    "MemoryV2AgentAttachment",
    "MemoryV2AgentAttachmentFactory",
    "MemoryV2AgentAttachmentRequest",
    "MemoryV2AgentModule",
    "MemoryV2AgentModuleError",
    "MemoryV2RootCompletionFactory",
    "MemoryV2RunRole",
]

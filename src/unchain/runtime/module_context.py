"""Generic per-run context consumed by optional agent modules.

The core runtime transports execution facts and host-issued module grants.  It
does not infer authority from agent, graph, or collaboration roles.
"""

from __future__ import annotations

from dataclasses import dataclass, field


def _identifier(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True, slots=True)
class ExecutionIdentity:
    """Neutral identity and lineage facts for one concrete agent run."""

    execution_id: str
    attempt_id: str
    run_id: str
    run_lineage: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "execution_id",
            _identifier(self.execution_id, "execution_id"),
        )
        object.__setattr__(
            self,
            "attempt_id",
            _identifier(self.attempt_id, "attempt_id"),
        )
        object.__setattr__(self, "run_id", _identifier(self.run_id, "run_id"))
        lineage = tuple(
            _identifier(item, "run_lineage item") for item in self.run_lineage
        )
        if not lineage:
            raise ValueError("run_lineage must not be empty")
        if lineage[-1] != self.run_id:
            raise ValueError("run_lineage must terminate at run_id")
        if len(lineage) != len(set(lineage)):
            raise ValueError("run_lineage must not contain duplicate run IDs")
        object.__setattr__(self, "run_lineage", lineage)

    @property
    def root_run_id(self) -> str:
        return self.run_lineage[0]

    @property
    def parent_run_id(self) -> str | None:
        return self.run_lineage[-2] if len(self.run_lineage) > 1 else None


@dataclass(frozen=True, slots=True)
class ModuleGrant:
    """Capabilities explicitly issued to one optional module.

    ``delegable_capabilities`` is the maximum subset that may flow to a child
    execution.  Authorities are deliberately not delegated.
    """

    module_key: str
    capabilities: frozenset[str] = field(default_factory=frozenset)
    delegable_capabilities: frozenset[str] = field(default_factory=frozenset)
    authority: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "module_key",
            _identifier(self.module_key, "module_key"),
        )
        capabilities = self._capability_set(self.capabilities, "capabilities")
        delegable = self._capability_set(
            self.delegable_capabilities,
            "delegable_capabilities",
        )
        if not delegable.issubset(capabilities):
            raise ValueError("delegable_capabilities must be a capability subset")
        object.__setattr__(self, "capabilities", capabilities)
        object.__setattr__(self, "delegable_capabilities", delegable)
        if self.authority is not None:
            object.__setattr__(
                self,
                "authority",
                _identifier(self.authority, "authority"),
            )

    @staticmethod
    def _capability_set(value: object, field_name: str) -> frozenset[str]:
        if isinstance(value, (str, bytes, bytearray)):
            raise TypeError(f"{field_name} must be a collection of strings")
        try:
            items = frozenset(value)
        except TypeError as exc:
            raise TypeError(f"{field_name} must be a collection of strings") from exc
        if any(not isinstance(item, str) or not item.strip() for item in items):
            raise ValueError(f"{field_name} must contain non-empty strings")
        return frozenset(item.strip() for item in items)

    def allows(self, capability: str) -> bool:
        return capability in self.capabilities

    def delegated(self) -> "ModuleGrant":
        return ModuleGrant(
            module_key=self.module_key,
            capabilities=self.delegable_capabilities,
            delegable_capabilities=self.delegable_capabilities,
        )


@dataclass(frozen=True, slots=True)
class AgentRuntimeContext:
    """Execution facts and grants made available to configured modules."""

    identity: ExecutionIdentity
    module_grants: tuple[ModuleGrant, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.identity, ExecutionIdentity):
            raise TypeError("identity must be an ExecutionIdentity")
        grants = tuple(self.module_grants)
        if any(not isinstance(grant, ModuleGrant) for grant in grants):
            raise TypeError("module_grants must contain ModuleGrant values")
        keys = tuple(grant.module_key for grant in grants)
        if len(keys) != len(set(keys)):
            raise ValueError("module_grants must have unique module_key values")
        object.__setattr__(self, "module_grants", grants)

    def grant_for(self, module_key: str) -> ModuleGrant | None:
        normalized = _identifier(module_key, "module_key")
        for grant in self.module_grants:
            if grant.module_key == normalized:
                return grant
        return None

    def delegated_to(
        self,
        identity: ExecutionIdentity,
        *,
        requested_capabilities: dict[str, frozenset[str]] | None = None,
    ) -> "AgentRuntimeContext":
        if not isinstance(identity, ExecutionIdentity):
            raise TypeError("identity must be an ExecutionIdentity")
        expected_lineage = (*self.identity.run_lineage, identity.run_id)
        if identity.run_lineage != expected_lineage:
            raise ValueError("child identity must extend the current run lineage")
        requested = requested_capabilities or {}
        unknown = set(requested).difference(
            grant.module_key for grant in self.module_grants
        )
        if unknown:
            raise ValueError("requested capabilities name an unknown module grant")
        child_grants = []
        for grant in self.module_grants:
            maximum = grant.delegable_capabilities
            selected = frozenset(requested.get(grant.module_key, maximum))
            if not selected.issubset(maximum):
                raise ValueError("requested child capabilities exceed delegation")
            if not selected:
                continue
            child_grants.append(
                ModuleGrant(
                    module_key=grant.module_key,
                    capabilities=selected,
                    delegable_capabilities=selected,
                )
            )
        return AgentRuntimeContext(
            identity=identity,
            module_grants=tuple(child_grants),
        )


__all__ = ["AgentRuntimeContext", "ExecutionIdentity", "ModuleGrant"]

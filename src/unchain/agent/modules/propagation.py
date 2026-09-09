"""Generic child-agent propagation for optional modules."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


def _propagation_key(module: Any) -> str | None:
    raw = getattr(module, "propagation_key", None)
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("propagation_key must be non-empty when supplied")
    return raw.strip()


def _module_name(module: Any) -> str | None:
    raw = getattr(module, "name", None)
    if not isinstance(raw, str) or not raw.strip():
        return None
    return raw.strip()


@dataclass(frozen=True, slots=True)
class ChildModuleRequest:
    """Facts and explicit overrides used to resolve one child module set."""

    parent_agent_name: str
    child_agent_name: str
    template_name: str | None = None
    disabled_module_keys: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        for field_name in ("parent_agent_name", "child_agent_name"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
            object.__setattr__(self, field_name, value.strip())
        if self.template_name is not None:
            if not isinstance(self.template_name, str) or not self.template_name.strip():
                raise ValueError("template_name must be non-empty when supplied")
            object.__setattr__(self, "template_name", self.template_name.strip())
        disabled = frozenset(self.disabled_module_keys)
        if any(not isinstance(key, str) or not key.strip() for key in disabled):
            raise ValueError("disabled_module_keys must contain non-empty strings")
        object.__setattr__(
            self,
            "disabled_module_keys",
            frozenset(key.strip() for key in disabled),
        )


@runtime_checkable
class ChildPropagatingModule(Protocol):
    propagation_key: str | None

    def derive_for_child(
        self,
        request: ChildModuleRequest,
        configured_child: Any | None,
    ) -> Any | None:
        ...


def _unique_by_key(modules: tuple[Any, ...], *, label: str) -> dict[str, Any]:
    indexed: dict[str, Any] = {}
    for module in modules:
        key = _propagation_key(module)
        if key is None:
            continue
        if key in indexed:
            raise ValueError(f"{label} defines duplicate module key {key!r}")
        indexed[key] = module
    return indexed


def resolve_child_modules(
    *,
    parent_modules: tuple[Any, ...],
    child_modules: tuple[Any, ...],
    request: ChildModuleRequest,
) -> tuple[Any, ...]:
    """Resolve opted-in parent modules without importing concrete modules."""

    if not isinstance(request, ChildModuleRequest):
        raise TypeError("request must be a ChildModuleRequest")
    parents = tuple(parent_modules or ())
    children = tuple(child_modules or ())
    parent_by_key = _unique_by_key(parents, label="parent agent")
    _unique_by_key(children, label="child agent")
    resolved = list(children)

    for key, parent in parent_by_key.items():
        derive = getattr(parent, "derive_for_child", None)
        if not callable(derive):
            continue
        matching_indexes = [
            index
            for index, module in enumerate(resolved)
            if _propagation_key(module) == key
            or (
                _propagation_key(module) is None
                and _module_name(module) == key
            )
        ]
        if len(matching_indexes) > 1:
            raise ValueError(f"child agent defines duplicate module key {key!r}")
        index = matching_indexes[0] if matching_indexes else None
        configured_child = resolved[index] if index is not None else None
        child_module = (
            None
            if key in request.disabled_module_keys
            else derive(request, configured_child)
        )
        if child_module is not None and _propagation_key(child_module) != key:
            raise ValueError("derived child module changed its propagation key")
        if index is None:
            if child_module is not None:
                resolved.append(child_module)
        elif child_module is None:
            resolved.pop(index)
        else:
            resolved[index] = child_module

    _unique_by_key(tuple(resolved), label="resolved child agent")
    return tuple(resolved)


__all__ = [
    "ChildModuleRequest",
    "ChildPropagatingModule",
    "resolve_child_modules",
]

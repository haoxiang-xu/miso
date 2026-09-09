from __future__ import annotations

from dataclasses import dataclass

import pytest

from unchain.agent.modules.propagation import (
    ChildModuleRequest,
    resolve_child_modules,
)


@dataclass(frozen=True)
class _PlainModule:
    name: str


@dataclass(frozen=True)
class _PropagatingModule:
    name: str
    value: str

    @property
    def propagation_key(self) -> str:
        return self.name

    def derive_for_child(self, request, configured_child):
        del request
        if configured_child is None:
            return self
        if not isinstance(configured_child, _PropagatingModule):
            raise ValueError("module conflict")
        return configured_child


def _request(*, disabled=()) -> ChildModuleRequest:
    return ChildModuleRequest(
        parent_agent_name="parent",
        child_agent_name="child",
        disabled_module_keys=frozenset(disabled),
    )


def test_template_child_only_inherits_modules_that_opt_into_propagation():
    inherited = _PropagatingModule("shared", "parent")
    local = _PlainModule("local")
    ignored = _PlainModule("parent-only")

    resolved = resolve_child_modules(
        parent_modules=(ignored, inherited),
        child_modules=(local,),
        request=_request(),
    )

    assert resolved == (local, inherited)


def test_configured_child_module_is_reconciled_by_the_parent_module():
    parent = _PropagatingModule("shared", "parent")
    child = _PropagatingModule("shared", "child")

    resolved = resolve_child_modules(
        parent_modules=(parent,),
        child_modules=(child,),
        request=_request(),
    )

    assert resolved == (child,)


def test_explicit_disabled_key_removes_an_existing_child_module():
    module = _PropagatingModule("shared", "parent")

    resolved = resolve_child_modules(
        parent_modules=(module,),
        child_modules=(module,),
        request=_request(disabled=("shared",)),
    )

    assert resolved == ()


def test_duplicate_module_keys_fail_closed():
    with pytest.raises(ValueError, match="duplicate module key"):
        resolve_child_modules(
            parent_modules=(
                _PropagatingModule("shared", "a"),
                _PropagatingModule("shared", "b"),
            ),
            child_modules=(),
            request=_request(),
        )

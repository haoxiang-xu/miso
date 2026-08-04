"""Official Toolkit projection into restart-stable provider catalog authority."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from unchain.tools.handler_registry import (
    DurableToolHandlerBinding,
    DurableToolHandlerKind,
    DurableToolHandlerRegistry,
    DurableToolHandlerResolution,
    tool_config_sha256,
)
from unchain.tools.tool import Tool
from unchain.tools.toolkit import Toolkit


class ProviderToolkitAuthorityError(RuntimeError):
    """A live Toolkit could not produce a deterministic provider catalog."""


def _callable_manifest(value: Any) -> dict[str, str]:
    owner = getattr(value, "__self__", None)
    return {
        "module": str(getattr(value, "__module__", "") or ""),
        "qualname": str(
            getattr(value, "__qualname__", None)
            or getattr(value, "__name__", "")
            or type(value).__qualname__
        ),
        "owner": (
            ""
            if owner is None
            else f"{type(owner).__module__}.{type(owner).__qualname__}"
        ),
    }


def _handler_id(*, name: str, tool: Tool) -> str:
    payload = {
        "schema": "unchain.provider_tool_handler_identity.v1",
        "name": name,
        "toolkit_id": str(getattr(tool, "toolkit_id", "") or ""),
        "server": str(getattr(tool, "server", "") or ""),
        "category": str(getattr(tool, "category", "") or ""),
        "handler": _callable_manifest(tool.func),
    }
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ProviderToolkitAuthorityError(
            "tool handler identity is not deterministic JSON"
        ) from exc
    return "host.tool." + hashlib.sha256(encoded).hexdigest()


class ProviderToolkitAuthorityAdapter:
    """Build one fresh registry whose stable bindings survive restart."""

    def resolve(
        self,
        toolkit: Toolkit,
    ) -> tuple[DurableToolHandlerRegistry, tuple[DurableToolHandlerResolution, ...],]:
        if not isinstance(toolkit, Toolkit):
            raise TypeError("provider toolkit authority requires a Toolkit")
        registry = DurableToolHandlerRegistry()
        resolutions: list[DurableToolHandlerResolution] = []
        for name, tool in toolkit.tools.items():
            if type(name) is not str or not name or type(tool) is not Tool:
                raise ProviderToolkitAuthorityError(
                    "provider toolkit contains an invalid tool entry"
                )
            if tool.name != name:
                raise ProviderToolkitAuthorityError(
                    "provider toolkit key and tool name changed"
                )
            if not callable(tool.func):
                raise ProviderToolkitAuthorityError(
                    "provider toolkit tool handler is not callable"
                )
            binding = DurableToolHandlerBinding(
                handler_id=_handler_id(name=name, tool=tool),
                revision=1,
                config_sha256=tool_config_sha256(tool),
                kind=DurableToolHandlerKind.STABLE,
            )
            resolutions.append(
                registry.register(
                    binding,
                    tool=tool,
                    handler=tool.func,
                )
            )
        return registry, tuple(resolutions)


__all__ = [
    "ProviderToolkitAuthorityAdapter",
    "ProviderToolkitAuthorityError",
]

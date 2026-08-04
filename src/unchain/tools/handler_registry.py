from __future__ import annotations

import hashlib
import json
import math
import secrets
from dataclasses import dataclass, field
from enum import StrEnum
from threading import RLock
from types import MappingProxyType
from typing import Any, ClassVar

from unchain.journal.models import (
    ModelValidationError,
    _positive_revision,
    _record_data,
    _required_text,
    _sha256,
)
from unchain.journal.resource_limits import (
    BoundaryResourceLimitError,
    JsonResourceLimits,
)

from .tool import Tool
from .models import ToolParameter, ToolPromptSpec


class ToolHandlerRegistryError(RuntimeError):
    """Base error for the durable handler authority."""


class ToolHandlerConflictError(ToolHandlerRegistryError):
    """A durable handler identity was registered more than once."""


class ToolHandlerNotFoundError(ToolHandlerRegistryError):
    """The exact durable handler identity is not owned by this registry."""


class ProcessBoundToolHandlerError(ToolHandlerRegistryError):
    """A process-bound handler was requested for cold recovery."""


class ToolHandlerRegistryCapacityError(ToolHandlerRegistryError):
    """The finite registry admission ceiling was reached."""


class DurableToolHandlerKind(StrEnum):
    STABLE = "stable"
    PROCESS_BOUND = "process_bound"


MAX_DURABLE_TOOL_HANDLER_REGISTRATIONS = 4_096
MAX_TOOL_DESCRIPTOR_BYTES = 1024 * 1024
MAX_TOOL_DESCRIPTOR_CONTAINER_ITEMS = 1024
MAX_TOOL_DESCRIPTOR_STRING_BYTES = 16 * 1024
TOOL_DESCRIPTOR_JSON_LIMITS = JsonResourceLimits(
    max_items=MAX_TOOL_DESCRIPTOR_CONTAINER_ITEMS,
    max_bytes=MAX_TOOL_DESCRIPTOR_BYTES,
    max_depth=32,
    max_nodes=50_000,
)
_BindingKey = tuple[
    str,
    int,
    str,
    str,
    str | None,
    int | None,
    str | None,
]


def _canonical_sha256(value: dict[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _canonical_identifier(value: Any, field_name: str) -> str:
    normalized = _required_text(value, field_name, identifier=True)
    if normalized != value:
        raise ModelValidationError(f"{field_name} is not canonical text")
    return normalized


def _projection_record_adapter(value: Any) -> Any:
    if type(value) is ToolParameter:
        return {
            "name": value.name,
            "description": value.description,
            "type": value.type_,
            "required": value.required,
            "pattern": value.pattern,
            "items": value.items,
        }
    if type(value) is ToolPromptSpec:
        return {
            "purpose": value.purpose,
            "when_to_use": value.when_to_use,
            "when_not_to_use": value.when_not_to_use,
            "examples": value.examples,
            "advanced_tips": value.advanced_tips,
        }
    return value


def _strict_projection_preflight(
    value: Any,
    *,
    boundary: str,
    adapt_records: bool = False,
    allow_tuples: bool = False,
) -> None:
    limits = TOOL_DESCRIPTOR_JSON_LIMITS
    node_count = 0
    byte_count = 0
    active_containers: set[int] = set()
    stack: list[tuple[str, Any, int, int | None]] = [("enter", value, 0, None)]

    def reject(dimension: str, limit: int, observed: int) -> None:
        raise BoundaryResourceLimitError(
            boundary=boundary,
            dimension=dimension,
            limit=limit,
            observed=observed,
        )

    def add_bytes(amount: int) -> None:
        nonlocal byte_count
        byte_count += amount
        if byte_count > limits.max_bytes:
            reject("bytes", limits.max_bytes, byte_count)

    def add_string(text: str) -> None:
        raw_bytes = 0
        encoded_bytes = 2
        for character in text:
            try:
                character_bytes = len(character.encode("utf-8"))
            except UnicodeEncodeError as exc:
                raise ToolHandlerRegistryError(
                    f"{boundary} contains invalid Unicode"
                ) from exc
            raw_bytes += character_bytes
            if raw_bytes > MAX_TOOL_DESCRIPTOR_STRING_BYTES:
                reject(
                    "string_bytes",
                    MAX_TOOL_DESCRIPTOR_STRING_BYTES,
                    raw_bytes,
                )
            codepoint = ord(character)
            if character in {'"', "\\"} or character in "\b\t\n\f\r":
                encoded_bytes += 2
            elif codepoint < 0x20:
                encoded_bytes += 6
            else:
                encoded_bytes += character_bytes
            if byte_count + encoded_bytes > limits.max_bytes:
                reject("bytes", limits.max_bytes, byte_count + encoded_bytes)
        add_bytes(encoded_bytes)

    while stack:
        action, original, depth, tracked_identity = stack.pop()
        if action == "exit":
            if tracked_identity is not None:
                active_containers.remove(tracked_identity)
            continue

        if depth > limits.max_depth:
            reject("depth", limits.max_depth, depth)
        node_count += 1
        if node_count > limits.max_nodes:
            reject("nodes", limits.max_nodes, node_count)

        item = _projection_record_adapter(original) if adapt_records else original
        item_type = type(item)
        if item is None:
            add_bytes(4)
            continue
        if item_type is bool:
            add_bytes(4 if item else 5)
            continue
        if item_type is str:
            add_string(item)
            continue
        if item_type is int:
            try:
                add_bytes(len(str(item).encode("ascii")))
            except (UnicodeEncodeError, ValueError) as exc:
                raise ToolHandlerRegistryError(
                    f"{boundary} contains an invalid integer"
                ) from exc
            continue
        if item_type is float:
            if not math.isfinite(item):
                raise ToolHandlerRegistryError(
                    f"{boundary} contains a non-finite number"
                )
            add_bytes(
                len(
                    json.dumps(
                        item,
                        ensure_ascii=False,
                        allow_nan=False,
                        separators=(",", ":"),
                    ).encode("utf-8")
                )
            )
            continue

        is_dict = item_type is dict
        is_sequence = item_type is list or (allow_tuples and item_type is tuple)
        if not is_dict and not is_sequence:
            raise ToolHandlerRegistryError(f"{boundary} contains a non-JSON value")
        item_count = len(item)
        if item_count > MAX_TOOL_DESCRIPTOR_CONTAINER_ITEMS:
            reject(
                "container_items",
                MAX_TOOL_DESCRIPTOR_CONTAINER_ITEMS,
                item_count,
            )

        identity = id(original)
        if identity in active_containers:
            raise ToolHandlerRegistryError(f"{boundary} contains a circular JSON value")
        active_containers.add(identity)
        stack.append(("exit", None, depth, identity))

        add_bytes(2 + max(0, item_count - 1))
        if is_dict:
            children: list[Any] = []
            for key, child in item.items():
                if type(key) is not str:
                    raise ToolHandlerRegistryError(
                        f"{boundary} contains a non-text object key"
                    )
                add_string(key)
                add_bytes(1)
                children.append(child)
            for child in reversed(children):
                stack.append(("enter", child, depth + 1, None))
            continue

        for child in reversed(item):
            stack.append(("enter", child, depth + 1, None))


def _preflight_tool_source(tool: Tool) -> None:
    if type(tool) is not Tool:
        raise TypeError("tool projection requires an exact Tool")
    shadowed_projection_methods = {
        method_name
        for method_name in (
            "_parameters_json_schema",
            "_provider_native_spec",
            "to_json",
            "to_provider_json",
        )
        if method_name in vars(tool)
    }
    if shadowed_projection_methods:
        raise ToolHandlerRegistryError(
            "tool projection methods cannot be shadowed on an instance"
        )
    if type(tool.parameters) is not list:
        raise ToolHandlerRegistryError("tool parameters must be an exact ordered list")
    for parameter in tool.parameters:
        if type(parameter) is not ToolParameter:
            raise ToolHandlerRegistryError(
                "tool parameters must contain exact ToolParameter records"
            )
        if "to_json" in vars(parameter):
            raise ToolHandlerRegistryError(
                "tool parameter projection methods cannot be shadowed on an instance"
            )
    source_projection = {
        "name": tool.name,
        "description": tool.description,
        "parameters": tool.parameters,
        "provider_native_specs": tool.provider_native_specs,
        "required_betas": tool.required_betas,
        "observe": tool.observe,
        "requires_confirmation": tool.requires_confirmation,
        "render_component": tool.render_component,
        "prompt_spec": tool.prompt_spec,
        "icon_path": tool.icon_path,
        "icon": tool.icon,
        "always_load": tool.always_load,
        "defer_by_default": tool.defer_by_default,
        "search_hint": tool.search_hint,
        "toolkit_id": getattr(tool, "toolkit_id", ""),
        "server": getattr(tool, "server", ""),
        "category": getattr(tool, "category", ""),
    }
    _strict_projection_preflight(
        source_projection,
        boundary="live tool descriptor source",
        adapt_records=True,
        allow_tuples=True,
    )


def _tool_prompt_spec_payload(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "purpose": getattr(value, "purpose", ""),
        "when_to_use": list(getattr(value, "when_to_use", ()) or ()),
        "when_not_to_use": list(getattr(value, "when_not_to_use", ()) or ()),
        "examples": list(getattr(value, "examples", ()) or ()),
        "advanced_tips": list(getattr(value, "advanced_tips", ()) or ()),
    }


def _tool_config_payload(tool: Tool) -> dict[str, Any]:
    _preflight_tool_source(tool)
    payload = {
        "provider_native_specs": tool.provider_native_specs,
        "required_betas": tool.required_betas,
        "observe": tool.observe,
        "requires_confirmation": tool.requires_confirmation,
        "render_component": tool.render_component,
        "prompt_spec": _tool_prompt_spec_payload(tool.prompt_spec),
        "icon_path": tool.icon_path,
        "icon": tool.icon,
        "always_load": tool.always_load,
        "defer_by_default": tool.defer_by_default,
        "search_hint": tool.search_hint,
        "toolkit_id": str(getattr(tool, "toolkit_id", "") or ""),
        "server": str(getattr(tool, "server", "") or ""),
        "category": str(getattr(tool, "category", "") or ""),
    }
    _strict_projection_preflight(
        payload,
        boundary="tool config projection",
    )
    return payload


def _tool_descriptor_payload(tool: Tool) -> dict[str, Any]:
    _preflight_tool_source(tool)
    try:
        payload = {
            "provider_neutral_schema": Tool.to_json(tool),
            "provider_schemas": {
                provider: Tool.to_provider_json(tool, provider)
                for provider in (
                    "anthropic",
                    "hyperspace",
                    "ollama",
                    "openai",
                )
            },
            "config": _tool_config_payload(tool),
        }
    except BoundaryResourceLimitError:
        raise
    except (AttributeError, RecursionError, TypeError, ValueError) as exc:
        raise ToolHandlerRegistryError(
            "tool descriptor projection failed strict JSON construction"
        ) from exc
    _strict_projection_preflight(
        payload,
        boundary="tool descriptor projection",
    )
    return payload


def _canonical_snapshot(value: dict[str, Any]) -> MappingProxyType:
    _strict_projection_preflight(
        value,
        boundary="tool descriptor snapshot",
    )
    try:
        copied = json.loads(
            json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    except (RecursionError, TypeError, ValueError, UnicodeError) as exc:
        raise ToolHandlerRegistryError(
            "tool descriptor must be deterministic strict JSON"
        ) from exc

    def freeze(item: Any) -> Any:
        if type(item) is dict:
            return MappingProxyType({key: freeze(child) for key, child in item.items()})
        if type(item) is list:
            return tuple(freeze(child) for child in item)
        return item

    return freeze(copied)


def _thaw_snapshot(value: Any) -> Any:
    if isinstance(value, MappingProxyType):
        return {key: _thaw_snapshot(child) for key, child in value.items()}
    if type(value) is tuple:
        return [_thaw_snapshot(child) for child in value]
    return value


def tool_config_sha256(tool: Tool) -> str:
    """Digest the host configuration independently from its visible schema."""

    if type(tool) is not Tool:
        raise TypeError("tool config digest requires an exact Tool")
    try:
        return _canonical_sha256(_tool_config_payload(tool))
    except BoundaryResourceLimitError:
        raise
    except (AttributeError, TypeError, ValueError, UnicodeError) as exc:
        raise ToolHandlerRegistryError(
            "tool config must be deterministic strict JSON"
        ) from exc


def _tool_descriptor_sha256(tool: Tool) -> str:
    try:
        return _canonical_sha256(_tool_descriptor_payload(tool))
    except BoundaryResourceLimitError:
        raise
    except (AttributeError, TypeError, ValueError, UnicodeError) as exc:
        raise ToolHandlerRegistryError(
            "tool descriptor must be deterministic strict JSON"
        ) from exc


@dataclass(frozen=True)
class DurableToolHandlerBinding:
    """Serializable identity for one host-owned tool implementation."""

    SCHEMA: ClassVar[str] = "unchain.durable_tool_handler_binding.v1"

    handler_id: str
    revision: int
    config_sha256: str
    kind: DurableToolHandlerKind | str
    route_resolver_id: str | None = None
    route_resolver_revision: int | None = None
    process_epoch: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "handler_id",
            _canonical_identifier(self.handler_id, "handler_id"),
        )
        object.__setattr__(self, "revision", _positive_revision(self.revision))
        object.__setattr__(
            self,
            "config_sha256",
            _sha256(self.config_sha256, "config_sha256"),
        )
        try:
            kind = DurableToolHandlerKind(self.kind)
        except ValueError as exc:
            raise ModelValidationError("invalid durable tool handler kind") from exc
        object.__setattr__(self, "kind", kind)

        if kind is DurableToolHandlerKind.STABLE:
            if self.process_epoch is not None:
                raise ModelValidationError(
                    "stable durable tool handlers cannot declare process_epoch"
                )
        else:
            if self.process_epoch is None:
                raise ModelValidationError(
                    "process-bound durable tool handlers require process_epoch"
                )
            object.__setattr__(
                self,
                "process_epoch",
                _canonical_identifier(self.process_epoch, "process_epoch"),
            )

        has_resolver_id = self.route_resolver_id is not None
        has_resolver_revision = self.route_resolver_revision is not None
        if has_resolver_id != has_resolver_revision:
            raise ModelValidationError(
                "route_resolver_id and route_resolver_revision must be set together"
            )
        if has_resolver_id:
            object.__setattr__(
                self,
                "route_resolver_id",
                _canonical_identifier(
                    self.route_resolver_id,
                    "route_resolver_id",
                ),
            )
            object.__setattr__(
                self,
                "route_resolver_revision",
                _positive_revision(self.route_resolver_revision),
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.SCHEMA,
            "handler_id": self.handler_id,
            "revision": self.revision,
            "config_sha256": self.config_sha256,
            "kind": self.kind.value,
            "route_resolver_id": self.route_resolver_id,
            "route_resolver_revision": self.route_resolver_revision,
            "process_epoch": self.process_epoch,
        }

    @property
    def sha256(self) -> str:
        return _canonical_sha256(self.to_dict())

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> DurableToolHandlerBinding:
        if type(value) is not dict:
            raise TypeError("handler binding record must be an exact dict")
        fields = frozenset(
            {
                "handler_id",
                "revision",
                "config_sha256",
                "kind",
                "route_resolver_id",
                "route_resolver_revision",
                "process_epoch",
            }
        )
        raw = _record_data(value, schema=cls.SCHEMA, required=fields)
        return cls(**{field_name: raw[field_name] for field_name in fields})


@dataclass(frozen=True, eq=False)
class DurableToolHandlerResolution:
    binding: DurableToolHandlerBinding
    _tool: Tool = field(repr=False)
    _handler: Any = field(repr=False)
    _route_resolver: Any = field(repr=False)
    _tool_descriptor_sha256: str = field(repr=False)
    _tool_descriptor_snapshot: MappingProxyType = field(repr=False)
    _registry_authority: object = field(repr=False)

    @property
    def tool(self) -> Tool:
        return self._tool

    @property
    def handler(self) -> Any:
        return self._handler

    @property
    def route_resolver(self) -> Any:
        return self._route_resolver

    @property
    def tool_descriptor_sha256(self) -> str:
        return self._tool_descriptor_sha256

    @property
    def tool_descriptor(self) -> MappingProxyType:
        return self._tool_descriptor_snapshot

    def __reduce__(self):
        raise TypeError("durable tool handler resolutions cannot be serialized")

    def __reduce_ex__(self, protocol):
        del protocol
        raise TypeError("durable tool handler resolutions cannot be serialized")


@dataclass(frozen=True)
class _RegistrationRecord:
    binding_key: _BindingKey
    binding_sha256: str
    tool: Tool = field(repr=False)
    handler: Any = field(repr=False)
    route_resolver: Any = field(repr=False)
    config_sha256: str
    tool_descriptor_sha256: str
    tool_descriptor_snapshot: MappingProxyType = field(repr=False)
    confirmation_resolver: Any = field(repr=False)
    history_arguments_optimizer: Any = field(repr=False)
    history_result_optimizer: Any = field(repr=False)
    resolution: DurableToolHandlerResolution = field(repr=False)


class DurableToolHandlerRegistry:
    """Process authority for exact durable tool-handler bindings."""

    def __init__(
        self,
        *,
        max_registrations: int = MAX_DURABLE_TOOL_HANDLER_REGISTRATIONS,
    ) -> None:
        if type(self) is not DurableToolHandlerRegistry:
            raise TypeError(
                "DurableToolHandlerRegistry subclass authority is forbidden"
            )
        if (
            type(max_registrations) is not int
            or not 1 <= max_registrations <= MAX_DURABLE_TOOL_HANDLER_REGISTRATIONS
        ):
            raise ValueError("max_registrations must be a positive bounded integer")
        self._authority = object()
        self._process_epoch = secrets.token_hex(32)
        self._lock = RLock()
        self._max_registrations = max_registrations
        self._registrations: dict[
            _BindingKey,
            _RegistrationRecord,
        ] = {}
        self._identity_revisions: dict[
            tuple[str, int],
            _BindingKey,
        ] = {}
        self._tool_bindings: dict[
            int,
            _BindingKey,
        ] = {}

    def __reduce__(self):
        raise TypeError("durable tool handler registries cannot be serialized")

    def __reduce_ex__(self, protocol):
        del protocol
        raise TypeError("durable tool handler registries cannot be serialized")

    @property
    def registration_count(self) -> int:
        with self._lock:
            return len(self._registrations)

    @staticmethod
    def _key(
        binding: DurableToolHandlerBinding,
    ) -> _BindingKey:
        return (
            binding.handler_id,
            binding.revision,
            binding.config_sha256,
            binding.kind.value,
            binding.route_resolver_id,
            binding.route_resolver_revision,
            binding.process_epoch,
        )

    @staticmethod
    def _require_binding(value: object) -> DurableToolHandlerBinding:
        if type(value) is not DurableToolHandlerBinding:
            raise TypeError("registry requires an exact DurableToolHandlerBinding")
        try:
            canonical = DurableToolHandlerBinding.from_dict(value.to_dict())
        except (AttributeError, TypeError, ValueError) as exc:
            raise TypeError("registry binding is not canonical") from exc
        if canonical != value or canonical.sha256 != value.sha256:
            raise TypeError("registry binding is not canonical")
        return value

    def register(
        self,
        binding: DurableToolHandlerBinding,
        *,
        tool: Tool,
        handler: Any,
        route_resolver: Any = None,
    ) -> DurableToolHandlerResolution:
        binding = self._require_binding(binding)
        if binding.kind is DurableToolHandlerKind.PROCESS_BOUND:
            raise ProcessBoundToolHandlerError(
                "process-bound registration requires the registry's warm authority"
            )
        return self._register_exact(
            binding,
            tool=tool,
            handler=handler,
            route_resolver=route_resolver,
        )

    def register_process_bound(
        self,
        *,
        handler_id: str,
        revision: int,
        tool: Tool,
        handler: Any,
        route_resolver: Any = None,
        route_resolver_id: str | None = None,
        route_resolver_revision: int | None = None,
    ) -> DurableToolHandlerResolution:
        binding = DurableToolHandlerBinding(
            handler_id=handler_id,
            revision=revision,
            config_sha256=tool_config_sha256(tool),
            kind=DurableToolHandlerKind.PROCESS_BOUND,
            route_resolver_id=route_resolver_id,
            route_resolver_revision=route_resolver_revision,
            process_epoch=self._process_epoch,
        )
        return self._register_exact(
            binding,
            tool=tool,
            handler=handler,
            route_resolver=route_resolver,
        )

    def _register_exact(
        self,
        binding: DurableToolHandlerBinding,
        *,
        tool: Tool,
        handler: Any,
        route_resolver: Any = None,
    ) -> DurableToolHandlerResolution:
        binding = self._require_binding(binding)
        if (
            binding.kind is DurableToolHandlerKind.PROCESS_BOUND
            and binding.process_epoch != self._process_epoch
        ):
            raise ProcessBoundToolHandlerError(
                "process-bound binding belongs to a different registry epoch"
            )
        if type(tool) is not Tool:
            raise TypeError("registry requires an exact Tool")
        if not callable(handler):
            raise TypeError("registered tool handler must be callable")
        if tool.func is not handler:
            raise ToolHandlerRegistryError(
                "registered execution handler must be the exact tool.func"
            )
        expects_resolver = binding.route_resolver_id is not None
        if expects_resolver != (route_resolver is not None):
            raise ToolHandlerRegistryError(
                "route resolver object must exactly match the binding identity"
            )

        with self._lock:
            config_sha256 = tool_config_sha256(tool)
            descriptor_payload = _tool_descriptor_payload(tool)
            descriptor_sha256 = _canonical_sha256(descriptor_payload)
            descriptor_snapshot = _canonical_snapshot(descriptor_payload)
            confirmation_resolver = tool.confirmation_resolver
            history_arguments_optimizer = tool.history_arguments_optimizer
            history_result_optimizer = tool.history_result_optimizer
            identity_revision = (binding.handler_id, binding.revision)
            key = self._key(binding)
            prior_key = self._identity_revisions.get(identity_revision)
            if prior_key is not None:
                if prior_key == key:
                    raise ToolHandlerConflictError(
                        "durable tool handler binding is already registered"
                    )
                raise ToolHandlerConflictError(
                    "durable tool handler identity/revision conflicts with its prior binding"
                )
            if key in self._registrations:
                raise ToolHandlerConflictError(
                    "durable tool handler binding is already registered"
                )
            if id(tool) in self._tool_bindings:
                raise ToolHandlerConflictError(
                    "the exact Tool object already owns a durable binding"
                )
            if len(self._registrations) >= self._max_registrations:
                raise ToolHandlerRegistryCapacityError(
                    "durable tool handler registry capacity is exhausted"
                )
            if binding.config_sha256 != config_sha256:
                raise ToolHandlerRegistryError(
                    "binding config_sha256 does not match the live tool config"
                )
            for _projection_index in range(3):
                current_descriptor_payload = _tool_descriptor_payload(tool)
                if (
                    _canonical_sha256(current_descriptor_payload) != descriptor_sha256
                    or current_descriptor_payload != descriptor_payload
                ):
                    raise ToolHandlerRegistryError(
                        "tool changed during durable handler registration"
                    )
            if (
                tool.func is not handler
                or tool_config_sha256(tool) != config_sha256
                or tool.confirmation_resolver is not confirmation_resolver
                or tool.history_arguments_optimizer is not history_arguments_optimizer
                or tool.history_result_optimizer is not history_result_optimizer
            ):
                raise ToolHandlerRegistryError(
                    "tool changed during durable handler registration"
                )
            resolution = DurableToolHandlerResolution(
                binding=binding,
                _tool=tool,
                _handler=handler,
                _route_resolver=route_resolver,
                _tool_descriptor_sha256=descriptor_sha256,
                _tool_descriptor_snapshot=descriptor_snapshot,
                _registry_authority=self._authority,
            )
            registration = _RegistrationRecord(
                binding_key=key,
                binding_sha256=binding.sha256,
                tool=tool,
                handler=handler,
                route_resolver=route_resolver,
                config_sha256=config_sha256,
                tool_descriptor_sha256=descriptor_sha256,
                tool_descriptor_snapshot=descriptor_snapshot,
                confirmation_resolver=confirmation_resolver,
                history_arguments_optimizer=history_arguments_optimizer,
                history_result_optimizer=history_result_optimizer,
                resolution=resolution,
            )
            try:
                self._identity_revisions[identity_revision] = key
                self._tool_bindings[id(tool)] = key
                self._registrations[key] = registration
            except BaseException:
                if self._identity_revisions.get(identity_revision) == key:
                    self._identity_revisions.pop(identity_revision, None)
                if self._tool_bindings.get(id(tool)) == key:
                    self._tool_bindings.pop(id(tool), None)
                if self._registrations.get(key) is registration:
                    self._registrations.pop(key, None)
                raise
            return resolution

    def resolve(
        self,
        binding: DurableToolHandlerBinding,
    ) -> DurableToolHandlerResolution:
        binding = self._require_binding(binding)
        with self._lock:
            registration = self._registrations.get(self._key(binding))
            if registration is None:
                raise ToolHandlerNotFoundError(
                    "exact durable tool handler binding is not registered in this registry"
                )
            if (
                binding.kind is DurableToolHandlerKind.PROCESS_BOUND
                and binding is not registration.resolution.binding
            ):
                raise ProcessBoundToolHandlerError(
                    "process-bound resolution requires the registry's warm authority"
                )
            return self.verify_resolution(registration.resolution)

    def resolve_stable(
        self,
        binding: DurableToolHandlerBinding,
    ) -> DurableToolHandlerResolution:
        binding = self._require_binding(binding)
        if binding.kind is DurableToolHandlerKind.PROCESS_BOUND:
            raise ProcessBoundToolHandlerError(
                "process-bound tool handlers do not have stable bindings"
            )
        return self.resolve(binding)

    def verify_resolution(
        self,
        resolution: DurableToolHandlerResolution,
    ) -> DurableToolHandlerResolution:
        with self._lock:
            if (
                type(resolution) is not DurableToolHandlerResolution
                or resolution._registry_authority is not self._authority
                or type(resolution.binding) is not DurableToolHandlerBinding
            ):
                raise ToolHandlerRegistryError(
                    "tool handler resolution is not owned by this registry authority"
                )
            registered = self._registrations.get(self._key(resolution.binding))
            try:
                current_config_sha256 = tool_config_sha256(resolution._tool)
                current_descriptor_sha256 = _tool_descriptor_sha256(resolution._tool)
            except ToolHandlerRegistryError as exc:
                raise ToolHandlerRegistryError(
                    "tool handler resolution changed after registration"
                ) from exc
            if (
                registered is None
                or registered.resolution is not resolution
                or registered.binding_key != self._key(resolution.binding)
                or registered.binding_sha256 != resolution.binding.sha256
                or self._tool_bindings.get(id(resolution._tool))
                != registered.binding_key
                or resolution._tool is not registered.tool
                or resolution._handler is not registered.handler
                or resolution._route_resolver is not registered.route_resolver
                or resolution._tool_descriptor_sha256
                != registered.tool_descriptor_sha256
                or resolution._tool_descriptor_snapshot
                is not registered.tool_descriptor_snapshot
                or _thaw_snapshot(resolution._tool_descriptor_snapshot)
                != _tool_descriptor_payload(registered.tool)
                or registered.config_sha256 != resolution.binding.config_sha256
                or current_config_sha256 != registered.config_sha256
                or current_descriptor_sha256 != registered.tool_descriptor_sha256
                or registered.tool.func is not registered.handler
                or registered.tool.confirmation_resolver
                is not registered.confirmation_resolver
                or registered.tool.history_arguments_optimizer
                is not registered.history_arguments_optimizer
                or registered.tool.history_result_optimizer
                is not registered.history_result_optimizer
            ):
                raise ToolHandlerRegistryError(
                    "tool handler resolution changed after registration"
                )
            return resolution


__all__ = [
    "DurableToolHandlerBinding",
    "DurableToolHandlerKind",
    "DurableToolHandlerRegistry",
    "DurableToolHandlerResolution",
    "MAX_DURABLE_TOOL_HANDLER_REGISTRATIONS",
    "MAX_TOOL_DESCRIPTOR_BYTES",
    "MAX_TOOL_DESCRIPTOR_CONTAINER_ITEMS",
    "MAX_TOOL_DESCRIPTOR_STRING_BYTES",
    "ProcessBoundToolHandlerError",
    "TOOL_DESCRIPTOR_JSON_LIMITS",
    "ToolHandlerConflictError",
    "ToolHandlerNotFoundError",
    "ToolHandlerRegistryCapacityError",
    "ToolHandlerRegistryError",
    "tool_config_sha256",
]

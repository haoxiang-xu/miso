"""Pure, fail-closed contracts for one persisted provider turn.

This module deliberately performs no artifact I/O, journal append, or network
request.  The host persistence adapter is responsible for minting a fresh
catalog authority only after durable persistence succeeds.  Provider adapters
consume :class:`PreparedProviderTurn` through the private consumer below.
The private fresh-authority issuer is only an in-process system assertion; it
cannot prove I/O by itself.  Binding it to the atomic production repository is
therefore an explicit rollout blocker.

Production rollout also remains blocked on a frozen ``ProviderWireEnvelope``
that binds cache-control decorations, the final provider header projection,
and adapter revision.  Fresh authority minting must move into the successful
journal/repository transaction, and repeated consumption must be governed by
a durable retry lease/CAS rather than this pure in-process contract.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import secrets
import threading
import unicodedata
import weakref
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from unchain.context.tool_catalog import (
    MAX_TOOL_CATALOG_ENTRIES,
    TOOL_CATALOG_JSON_LIMITS,
    ToolCatalogContractError,
    ToolCatalogEntry,
    ToolCatalogEnvelope,
    ToolCatalogSnapshot,
    _canonical_provider,
    _strict_json_copy,
    build_tool_catalog_entry_from_resolution,
)
from unchain.journal.models import (
    ArtifactRef,
    AttemptRef,
    ModelValidationError,
    _bounded_int,
    _sha256,
    _thaw_json,
)
from unchain.journal.resource_limits import (
    BoundaryResourceLimitError,
    enforce_item_limit,
    validate_json_resource,
)
from unchain.journal.tool_catalog import (
    RecoveredToolCatalogAuthority,
    verify_recovered_tool_catalog_authority,
)
from unchain.tools.handler_registry import (
    DurableToolHandlerRegistry,
    DurableToolHandlerResolution,
)


MAX_PROVIDER_REQUIRED_BETAS = 64
MAX_PROVIDER_BETA_BYTES = 256
MAX_PROVIDER_REQUIRED_BETAS_BYTES = 16 * 1024
MAX_PREPARED_PROVIDER_TURNS = 4_096

# These are transport/resource safety ceilings, not context-budget policy.
# ContextCompiler remains the sole owner of model-window budgeting.
MAX_PROVIDER_REQUEST_PAYLOAD_BYTES = 64 * 1024 * 1024
MAX_PROVIDER_REQUEST_PAYLOAD_DEPTH = 64
MAX_PROVIDER_REQUEST_PAYLOAD_NODES = 1_000_000
MAX_PROVIDER_REQUEST_PAYLOAD_CONTAINER_ITEMS = 250_000
MAX_PROVIDER_REQUEST_PAYLOAD_STRING_BYTES = 32 * 1024 * 1024

_JSON_STRING_ESCAPE_PATTERN = re.compile(r'["\\\x00-\x1f]')
_JSON_SHORT_ESCAPES = frozenset({'"', "\\", "\b", "\t", "\n", "\f", "\r"})


class PreparedProviderTurnError(ModelValidationError):
    """A provider turn lost an exact catalog, consumer, or draft binding."""


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (RecursionError, TypeError, ValueError, UnicodeError) as exc:
        raise PreparedProviderTurnError(
            "prepared provider value must be strict canonical JSON"
        ) from exc


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _canonical_model(value: object) -> str:
    if type(value) is not str:
        raise TypeError("model must be exact text")
    normalized = unicodedata.normalize("NFC", value.strip())
    if (
        normalized != value
        or not normalized
        or len(normalized) > 512
        or any(ord(character) < 32 for character in normalized)
    ):
        raise ModelValidationError("model is not canonical text")
    return normalized


def _reject_beta_limit(*, dimension: str, limit: int, observed: int) -> None:
    raise BoundaryResourceLimitError(
        boundary="provider required betas",
        dimension=dimension,
        limit=limit,
        observed=observed,
    )


def _canonical_betas(value: object) -> tuple[str, ...]:
    if type(value) not in {list, tuple}:
        raise TypeError("required_betas must be an exact ordered array")
    if len(value) > MAX_PROVIDER_REQUIRED_BETAS:
        _reject_beta_limit(
            dimension="items",
            limit=MAX_PROVIDER_REQUIRED_BETAS,
            observed=len(value),
        )
    canonical: list[str] = []
    seen: set[str] = set()
    for index, beta in enumerate(value):
        if type(beta) is not str:
            raise TypeError(f"required_betas[{index}] must be exact text")
        normalized = unicodedata.normalize("NFC", beta.strip())
        if normalized != beta:
            raise ModelValidationError(
                f"required_betas[{index}] must already be NFC canonical text"
            )
        if not beta or "," in beta or any(ord(character) < 32 for character in beta):
            raise ModelValidationError(f"required_betas[{index}] is invalid")
        try:
            byte_length = len(beta.encode("utf-8"))
        except UnicodeEncodeError as exc:
            raise ModelValidationError(
                f"required_betas[{index}] contains invalid Unicode"
            ) from exc
        if byte_length > MAX_PROVIDER_BETA_BYTES:
            _reject_beta_limit(
                dimension="string_bytes",
                limit=MAX_PROVIDER_BETA_BYTES,
                observed=byte_length,
            )
        if beta in seen:
            raise ModelValidationError("required_betas contains duplicate values")
        seen.add(beta)
        canonical.append(beta)
    encoded = _canonical_bytes(canonical)
    if len(encoded) > MAX_PROVIDER_REQUIRED_BETAS_BYTES:
        _reject_beta_limit(
            dimension="bytes",
            limit=MAX_PROVIDER_REQUIRED_BETAS_BYTES,
            observed=len(encoded),
        )
    return tuple(canonical)


def _freeze_plain_json(value: Any) -> Any:
    if type(value) is dict:
        return MappingProxyType(
            {key: _freeze_plain_json(child) for key, child in value.items()}
        )
    if type(value) is list:
        return tuple(_freeze_plain_json(child) for child in value)
    return value


def _preflight_request_payload(value: object) -> None:
    """Validate exact JSON and safety ceilings without recursive traversal."""

    node_count = 0
    byte_count = 0
    active_containers: set[int] = set()
    stack: list[tuple[str, object, int]] = [("enter", value, 0)]

    def reject(dimension: str, limit: int, observed: int) -> None:
        raise BoundaryResourceLimitError(
            boundary="provider request payload",
            dimension=dimension,
            limit=limit,
            observed=observed,
        )

    def add_bytes(amount: int) -> None:
        nonlocal byte_count
        byte_count += amount
        if byte_count > MAX_PROVIDER_REQUEST_PAYLOAD_BYTES:
            reject(
                "bytes",
                MAX_PROVIDER_REQUEST_PAYLOAD_BYTES,
                byte_count,
            )

    def add_string(text: str) -> None:
        character_count = len(text)
        if character_count > MAX_PROVIDER_REQUEST_PAYLOAD_STRING_BYTES:
            reject(
                "string_bytes",
                MAX_PROVIDER_REQUEST_PAYLOAD_STRING_BYTES,
                character_count,
            )
        raw_byte_count = 0
        encoded_byte_count = 2
        for offset in range(0, character_count, 64 * 1024):
            chunk = text[offset : offset + 64 * 1024]
            try:
                raw_chunk = chunk.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise PreparedProviderTurnError(
                    "provider request payload contains invalid Unicode"
                ) from exc
            raw_byte_count += len(raw_chunk)
            if raw_byte_count > MAX_PROVIDER_REQUEST_PAYLOAD_STRING_BYTES:
                reject(
                    "string_bytes",
                    MAX_PROVIDER_REQUEST_PAYLOAD_STRING_BYTES,
                    raw_byte_count,
                )
            encoded_byte_count += len(raw_chunk)
            for match in _JSON_STRING_ESCAPE_PATTERN.finditer(chunk):
                encoded_byte_count += 1 if match.group(0) in _JSON_SHORT_ESCAPES else 5
            if byte_count + encoded_byte_count > MAX_PROVIDER_REQUEST_PAYLOAD_BYTES:
                reject(
                    "bytes",
                    MAX_PROVIDER_REQUEST_PAYLOAD_BYTES,
                    byte_count + encoded_byte_count,
                )
        add_bytes(encoded_byte_count)

    while stack:
        action, item, depth = stack.pop()
        if action == "exit":
            active_containers.remove(id(item))
            continue
        if depth > MAX_PROVIDER_REQUEST_PAYLOAD_DEPTH:
            reject(
                "depth",
                MAX_PROVIDER_REQUEST_PAYLOAD_DEPTH,
                depth,
            )
        node_count += 1
        if node_count > MAX_PROVIDER_REQUEST_PAYLOAD_NODES:
            reject(
                "nodes",
                MAX_PROVIDER_REQUEST_PAYLOAD_NODES,
                node_count,
            )

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
                add_bytes(len(str(item)))
            except ValueError as exc:
                raise PreparedProviderTurnError(
                    "provider request payload contains an invalid integer"
                ) from exc
            continue
        if item_type is float:
            if not math.isfinite(item):
                raise PreparedProviderTurnError(
                    "provider request payload contains a non-finite number"
                )
            add_bytes(len(repr(item)))
            continue
        if item_type is not dict and item_type is not list:
            raise TypeError("provider request payload requires exact JSON value types")

        item_count = len(item)
        if item_count > MAX_PROVIDER_REQUEST_PAYLOAD_CONTAINER_ITEMS:
            reject(
                "container_items",
                MAX_PROVIDER_REQUEST_PAYLOAD_CONTAINER_ITEMS,
                item_count,
            )
        identity = id(item)
        if identity in active_containers:
            raise PreparedProviderTurnError(
                "provider request payload contains a circular request payload"
            )
        active_containers.add(identity)
        stack.append(("exit", item, depth))
        add_bytes(2 + max(0, item_count - 1))

        if item_type is dict:
            children: list[object] = []
            for key, child in item.items():
                if type(key) is not str:
                    raise TypeError(
                        "provider request payload requires exact JSON object keys"
                    )
                add_string(key)
                add_bytes(1)
                children.append(child)
            for child in reversed(children):
                stack.append(("enter", child, depth + 1))
            continue
        for child in reversed(item):
            stack.append(("enter", child, depth + 1))


def _strict_private_payload(value: object) -> Mapping[str, Any]:
    if type(value) is not dict:
        raise TypeError("request_payload must be an exact JSON object")
    _preflight_request_payload(value)
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
        raise PreparedProviderTurnError(
            "request payload must be detached strict JSON"
        ) from exc
    frozen = _freeze_plain_json(copied)
    if not isinstance(frozen, Mapping):
        raise PreparedProviderTurnError("request payload must remain an object")
    return frozen


@dataclass(frozen=True, slots=True)
class FrozenProviderTool:
    """One immutable provider-visible schema and its durable handler entry."""

    provider: str
    name: str
    semantic_schema: Mapping[str, Any]
    catalog_entry: ToolCatalogEntry
    required_betas: Sequence[str] = ()

    def __post_init__(self) -> None:
        provider = _canonical_provider(self.provider)
        object.__setattr__(self, "provider", provider)

        if type(self.semantic_schema) is not dict:
            raise TypeError("semantic_schema must be an exact dict")
        validate_json_resource(
            self.semantic_schema,
            boundary="frozen provider tool schema",
            limits=TOOL_CATALOG_JSON_LIMITS,
        )
        frozen_schema, _secret_manifest = _strict_json_copy(
            self.semantic_schema,
            path="frozen provider tool schema",
            pointer="/semantic_schemas/0",
            provider=provider,
        )
        if not isinstance(frozen_schema, Mapping):
            raise ToolCatalogContractError(
                "frozen provider tool schema must be a JSON object"
            )

        betas = _canonical_betas(self.required_betas)

        if type(self.catalog_entry) is not ToolCatalogEntry:
            raise TypeError("catalog_entry must be an exact ToolCatalogEntry")
        try:
            entry = ToolCatalogEntry.from_dict(self.catalog_entry.to_dict())
        except (AttributeError, TypeError, ValueError) as exc:
            raise ToolCatalogContractError("catalog_entry must be canonical") from exc
        if type(self.name) is not str or self.name != entry.tool_name:
            raise ToolCatalogContractError(
                "frozen provider tool name must match its catalog entry"
            )
        schema = _thaw_json(frozen_schema)
        if entry.semantic_schema_sha256 != _canonical_sha256(schema):
            raise ToolCatalogContractError(
                "frozen provider tool schema digest does not match its entry"
            )

        object.__setattr__(self, "name", entry.tool_name)
        object.__setattr__(self, "semantic_schema", frozen_schema)
        object.__setattr__(self, "catalog_entry", entry)
        object.__setattr__(self, "required_betas", betas)

    @property
    def semantic_schema_sha256(self) -> str:
        return self.catalog_entry.semantic_schema_sha256

    @property
    def tool_descriptor_sha256(self) -> str:
        return self.catalog_entry.tool_descriptor_sha256

    def to_provider_json(self) -> dict[str, Any]:
        value = _thaw_json(self.semantic_schema)
        if type(value) is not dict:
            raise PreparedProviderTurnError(
                "frozen provider tool schema changed after construction"
            )
        return value


@dataclass(frozen=True, slots=True)
class FrozenProviderToolkit:
    """Ordered provider-visible tools detached from all mutable ``Tool`` data."""

    provider: str
    supports_tools: bool
    tools: Sequence[FrozenProviderTool] = ()
    tool_schema_manifest: Mapping[str, str] = field(init=False)
    tool_schema_sha256: str = field(init=False)
    required_betas_sha256: str = field(init=False)
    toolkit_sha256: str = field(init=False)
    _required_beta_values: tuple[str, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        provider = _canonical_provider(self.provider)
        object.__setattr__(self, "provider", provider)
        if type(self.supports_tools) is not bool:
            raise TypeError("supports_tools must be an exact boolean")
        if type(self.tools) not in {list, tuple}:
            raise TypeError("tools must be an exact ordered array")
        if len(self.tools) > MAX_TOOL_CATALOG_ENTRIES:
            raise BoundaryResourceLimitError(
                boundary="frozen provider toolkit",
                dimension="items",
                limit=MAX_TOOL_CATALOG_ENTRIES,
                observed=len(self.tools),
            )
        if not self.supports_tools and self.tools:
            raise ToolCatalogContractError(
                "supports_tools=false requires an empty provider toolkit"
            )

        cloned_tools: list[FrozenProviderTool] = []
        for item in self.tools:
            if type(item) is not FrozenProviderTool:
                raise TypeError("tools require exact FrozenProviderTool records")
            if item.provider != provider:
                raise ToolCatalogContractError(
                    "frozen provider tool belongs to another provider"
                )
            cloned_tools.append(
                FrozenProviderTool(
                    provider=item.provider,
                    name=item.name,
                    semantic_schema=item.to_provider_json(),
                    catalog_entry=item.catalog_entry,
                    required_betas=item.required_betas,
                )
            )
        names = tuple(item.name for item in cloned_tools)
        if len(names) != len(set(names)):
            raise ToolCatalogContractError(
                "frozen provider toolkit contains duplicate stable names"
            )
        schemas = [item.to_provider_json() for item in cloned_tools]
        enforce_item_limit(
            schemas,
            boundary="frozen provider toolkit",
            limits=TOOL_CATALOG_JSON_LIMITS,
        )
        validate_json_resource(
            schemas,
            boundary="frozen provider toolkit",
            limits=TOOL_CATALOG_JSON_LIMITS,
        )

        beta_values: list[str] = []
        for item in cloned_tools:
            for beta in item.required_betas:
                if beta not in beta_values:
                    beta_values.append(beta)
        canonical_betas = _canonical_betas(beta_values)
        manifest = {item.name: item.semantic_schema_sha256 for item in cloned_tools}
        schema_sha256 = _canonical_sha256(schemas)
        beta_sha256 = _canonical_sha256(list(canonical_betas))
        toolkit_body = {
            "schema": "unchain.frozen_provider_toolkit.v1",
            "provider": provider,
            "supports_tools": self.supports_tools,
            "semantic_schemas": schemas,
            "entries": [item.catalog_entry.to_dict() for item in cloned_tools],
            "tool_schema_manifest": dict(sorted(manifest.items())),
            "tool_schema_sha256": schema_sha256,
            "required_betas": list(canonical_betas),
            "required_betas_sha256": beta_sha256,
        }
        validate_json_resource(
            toolkit_body,
            boundary="frozen provider toolkit",
            limits=TOOL_CATALOG_JSON_LIMITS,
        )

        object.__setattr__(self, "tools", tuple(cloned_tools))
        object.__setattr__(self, "_required_beta_values", canonical_betas)
        object.__setattr__(
            self,
            "tool_schema_manifest",
            MappingProxyType(dict(sorted(manifest.items()))),
        )
        object.__setattr__(self, "tool_schema_sha256", schema_sha256)
        object.__setattr__(self, "required_betas_sha256", beta_sha256)
        object.__setattr__(self, "toolkit_sha256", _canonical_sha256(toolkit_body))

    def to_provider_json(self, provider: str | None = None) -> list[dict[str, Any]]:
        selected = self.provider if provider is None else _canonical_provider(provider)
        if selected != self.provider:
            raise ToolCatalogContractError(
                "frozen provider toolkit cannot cross provider boundaries"
            )
        self._verify_data_integrity()
        return [item.to_provider_json() for item in self.tools]

    def required_betas(self, provider: str | None = None) -> list[str]:
        selected = self.provider if provider is None else _canonical_provider(provider)
        if selected != self.provider:
            return []
        self._verify_data_integrity()
        return list(self._required_beta_values)

    @property
    def catalog_entries(self) -> tuple[ToolCatalogEntry, ...]:
        self._verify_data_integrity()
        return tuple(item.catalog_entry for item in self.tools)

    def _verify_data_integrity(self) -> None:
        schemas = [item.to_provider_json() for item in self.tools]
        names = tuple(item.name for item in self.tools)
        if (
            len(names) != len(set(names))
            or dict(self.tool_schema_manifest)
            != {item.name: item.semantic_schema_sha256 for item in self.tools}
            or self.tool_schema_sha256 != _canonical_sha256(schemas)
            or self.required_betas_sha256
            != _canonical_sha256(list(self._required_beta_values))
        ):
            raise PreparedProviderTurnError(
                "frozen provider toolkit changed after construction"
            )
        body = {
            "schema": "unchain.frozen_provider_toolkit.v1",
            "provider": self.provider,
            "supports_tools": self.supports_tools,
            "semantic_schemas": schemas,
            "entries": [item.catalog_entry.to_dict() for item in self.tools],
            "tool_schema_manifest": dict(sorted(self.tool_schema_manifest.items())),
            "tool_schema_sha256": self.tool_schema_sha256,
            "required_betas": list(self._required_beta_values),
            "required_betas_sha256": self.required_betas_sha256,
        }
        if self.toolkit_sha256 != _canonical_sha256(body):
            raise PreparedProviderTurnError(
                "frozen provider toolkit digest changed after construction"
            )


def _required_betas_from_resolution(
    resolution: DurableToolHandlerResolution,
    *,
    provider: str,
) -> tuple[str, ...]:
    config = resolution.tool_descriptor.get("config")
    required = config.get("required_betas") if isinstance(config, Mapping) else None
    raw = required.get(provider) if isinstance(required, Mapping) else ()
    if raw is None:
        raw = ()
    if type(raw) not in {tuple, list}:
        raise ToolCatalogContractError(
            "verified tool descriptor required_betas is not an ordered array"
        )
    return _canonical_betas(raw)


def _build_frozen_provider_toolkit(
    *,
    provider: str,
    supports_tools: bool,
    registry: DurableToolHandlerRegistry,
    resolutions: Sequence[DurableToolHandlerResolution],
) -> tuple[FrozenProviderToolkit, tuple[DurableToolHandlerResolution, ...]]:
    provider = _canonical_provider(provider)
    if type(supports_tools) is not bool:
        raise TypeError("supports_tools must be an exact boolean")
    if type(registry) is not DurableToolHandlerRegistry:
        raise TypeError("registry must be an exact DurableToolHandlerRegistry")
    if type(resolutions) not in {list, tuple}:
        raise TypeError("resolutions must be an exact ordered array")
    if len(resolutions) > MAX_TOOL_CATALOG_ENTRIES:
        raise BoundaryResourceLimitError(
            boundary="frozen provider toolkit",
            dimension="items",
            limit=MAX_TOOL_CATALOG_ENTRIES,
            observed=len(resolutions),
        )

    verified = tuple(registry.verify_resolution(item) for item in resolutions)
    if not supports_tools:
        return (
            FrozenProviderToolkit(
                provider=provider,
                supports_tools=False,
                tools=(),
            ),
            (),
        )

    frozen_tools: list[FrozenProviderTool] = []
    for resolution in verified:
        schema, entry = build_tool_catalog_entry_from_resolution(
            registry=registry,
            resolution=resolution,
            provider=provider,
        )
        if resolution.tool.name != entry.tool_name:
            raise ToolCatalogContractError(
                "provider-visible name differs from its stable Tool identity"
            )
        frozen_tools.append(
            FrozenProviderTool(
                provider=provider,
                name=entry.tool_name,
                semantic_schema=schema,
                catalog_entry=entry,
                required_betas=_required_betas_from_resolution(
                    resolution,
                    provider=provider,
                ),
            )
        )
    return (
        FrozenProviderToolkit(
            provider=provider,
            supports_tools=True,
            tools=tuple(frozen_tools),
        ),
        verified,
    )


def _verify_frozen_provider_toolkit_authority(
    *,
    toolkit: FrozenProviderToolkit,
    registry: DurableToolHandlerRegistry,
    resolutions: tuple[DurableToolHandlerResolution, ...],
) -> None:
    if type(toolkit) is not FrozenProviderToolkit:
        raise PreparedProviderTurnError("provider turn draft toolkit authority changed")
    if type(registry) is not DurableToolHandlerRegistry:
        raise PreparedProviderTurnError(
            "provider turn draft registry authority changed"
        )
    if type(resolutions) is not tuple:
        raise PreparedProviderTurnError(
            "provider turn draft resolution authority changed"
        )
    toolkit._verify_data_integrity()
    if not toolkit.supports_tools:
        if toolkit.tools or resolutions:
            raise PreparedProviderTurnError(
                "disabled provider toolkit retained live authority"
            )
        return
    if len(resolutions) != len(toolkit.tools):
        raise PreparedProviderTurnError(
            "provider turn draft resolution authority changed"
        )
    for item, resolution in zip(toolkit.tools, resolutions, strict=True):
        verified = registry.verify_resolution(resolution)
        schema, entry = build_tool_catalog_entry_from_resolution(
            registry=registry,
            resolution=verified,
            provider=toolkit.provider,
        )
        required_betas = _required_betas_from_resolution(
            verified,
            provider=toolkit.provider,
        )
        if (
            item.to_provider_json() != schema
            or item.catalog_entry.to_dict() != entry.to_dict()
            or tuple(item.required_betas) != required_betas
        ):
            raise PreparedProviderTurnError(
                "frozen provider toolkit changed from its private authority"
            )


@dataclass(frozen=True, slots=True, weakref_slot=True)
class _ProviderTurnDraft:
    model_io: object = field(repr=False)
    provider: str
    model: str
    attempt: AttemptRef
    iteration: int
    toolkit: FrozenProviderToolkit
    catalog: ToolCatalogEnvelope
    _request_payload: Mapping[str, Any] = field(repr=False)
    _request_payload_sha256: str = field(repr=False)

    def _request_payload_copy(self) -> dict[str, Any]:
        copied = _thaw_json(self._request_payload)
        if type(copied) is not dict:
            raise PreparedProviderTurnError("private request payload changed")
        return copied


@dataclass(frozen=True, slots=True)
class _ProviderTurnDraftSeal:
    draft_ref: weakref.ReferenceType[_ProviderTurnDraft]
    registry: DurableToolHandlerRegistry = field(repr=False, compare=False)
    resolutions: tuple[DurableToolHandlerResolution, ...] = field(
        repr=False,
        compare=False,
    )
    registry_id: int
    resolution_ids: tuple[int, ...]
    tool_ids: tuple[int, ...]
    handler_ids: tuple[int, ...]
    model_io_id: int
    provider: str
    model: str
    attempt_payload: Mapping[str, Any]
    iteration: int
    toolkit_id: int
    toolkit_sha256: str
    catalog_id: int
    catalog_sha256: str
    request_payload_id: int
    request_payload_sha256: str


class _ProviderTurnDraftIssuer:
    def __init__(self, *, max_records: int = MAX_PREPARED_PROVIDER_TURNS) -> None:
        if type(max_records) is not int or max_records <= 0:
            raise ValueError("provider turn draft capacity must be positive")
        if max_records > MAX_PREPARED_PROVIDER_TURNS:
            raise ValueError("provider turn draft capacity is unbounded")
        self._max_records = max_records
        self._lock = threading.RLock()
        self._records: dict[int, _ProviderTurnDraftSeal] = {}

        authority_key = secrets.token_bytes(32)
        authority_anchors: dict[
            int,
            tuple[weakref.ReferenceType[_ProviderTurnDraft], int, str],
        ] = {}

        def calculate_authority_mac(
            draft: _ProviderTurnDraft,
            seal: _ProviderTurnDraftSeal,
        ) -> str:
            payload = self._authority_anchor_payload(draft=draft, seal=seal)
            return hmac.new(
                authority_key,
                _canonical_bytes(payload),
                hashlib.sha256,
            ).hexdigest()

        def authority_anchor_is_valid(
            draft: _ProviderTurnDraft,
            seal: _ProviderTurnDraftSeal,
        ) -> bool:
            with self._lock:
                anchor = authority_anchors.get(id(draft))
                if anchor is None or anchor[0]() is not draft or anchor[1] != id(seal):
                    return False
                expected = anchor[2]
            observed = calculate_authority_mac(draft, seal)
            return hmac.compare_digest(expected, observed)

        def mint_draft_authority(
            *,
            model_io: object,
            provider: str,
            model: str,
            attempt: AttemptRef,
            iteration: int,
            toolkit: FrozenProviderToolkit,
            registry: DurableToolHandlerRegistry,
            resolutions: tuple[DurableToolHandlerResolution, ...],
            catalog: ToolCatalogEnvelope,
            request_payload: Mapping[str, Any],
            request_payload_sha256: str,
        ) -> _ProviderTurnDraft:
            _verify_frozen_provider_toolkit_authority(
                toolkit=toolkit,
                registry=registry,
                resolutions=resolutions,
            )
            draft = _ProviderTurnDraft(
                model_io=model_io,
                provider=provider,
                model=model,
                attempt=attempt,
                iteration=iteration,
                toolkit=toolkit,
                catalog=catalog,
                _request_payload=request_payload,
                _request_payload_sha256=request_payload_sha256,
            )
            identity = id(draft)

            def discard_record(
                expired: weakref.ReferenceType[_ProviderTurnDraft],
            ) -> None:
                with self._lock:
                    current = self._records.get(identity)
                    if current is not None and expired is current.draft_ref:
                        self._records.pop(identity, None)

            def discard_anchor(
                expired: weakref.ReferenceType[_ProviderTurnDraft],
            ) -> None:
                with self._lock:
                    current = authority_anchors.get(identity)
                    if current is not None and current[0] is expired:
                        authority_anchors.pop(identity, None)

            seal = _ProviderTurnDraftSeal(
                draft_ref=weakref.ref(draft, discard_record),
                registry=registry,
                resolutions=resolutions,
                registry_id=id(registry),
                resolution_ids=tuple(id(item) for item in resolutions),
                tool_ids=tuple(id(item.tool) for item in resolutions),
                handler_ids=tuple(id(item.handler) for item in resolutions),
                model_io_id=id(model_io),
                provider=provider,
                model=model,
                attempt_payload=MappingProxyType(attempt.to_dict()),
                iteration=iteration,
                toolkit_id=id(toolkit),
                toolkit_sha256=toolkit.toolkit_sha256,
                catalog_id=id(catalog),
                catalog_sha256=catalog.catalog_sha256,
                request_payload_id=id(request_payload),
                request_payload_sha256=request_payload_sha256,
            )
            anchor_ref = weakref.ref(draft, discard_anchor)
            anchor = (
                anchor_ref,
                id(seal),
                calculate_authority_mac(draft, seal),
            )
            with self._lock:
                self._prune()
                if len(self._records) >= self._max_records:
                    raise RuntimeError("provider turn draft capacity exceeded")
                existing = self._records.get(identity)
                if existing is not None and existing.draft_ref() is not None:
                    raise RuntimeError("provider turn draft identity collision")
                existing_anchor = authority_anchors.get(identity)
                if existing_anchor is not None:
                    if existing_anchor[0]() is not None:
                        raise RuntimeError(
                            "provider turn draft authority anchor identity collision"
                        )
                    authority_anchors.pop(identity, None)
                authority_anchors[identity] = anchor
                try:
                    self._records[identity] = seal
                except BaseException:
                    if authority_anchors.get(identity) is anchor:
                        authority_anchors.pop(identity, None)
                    raise
            return draft

        self.__mint_draft_authority = mint_draft_authority
        self.__authority_anchor_is_valid = authority_anchor_is_valid

    @staticmethod
    def _authority_anchor_payload(
        *,
        draft: _ProviderTurnDraft,
        seal: _ProviderTurnDraftSeal,
    ) -> dict[str, Any]:
        resolution_bindings = []
        for resolution in seal.resolutions:
            resolution_bindings.append(
                {
                    "binding": resolution.binding.to_dict(),
                    "binding_sha256": resolution.binding.sha256,
                    "tool_descriptor_sha256": resolution.tool_descriptor_sha256,
                }
            )
        return {
            "schema": "unchain.provider_turn_draft_authority_anchor.v1",
            "seal_id": id(seal),
            "draft_id": id(draft),
            "registry_id": seal.registry_id,
            "resolution_ids": list(seal.resolution_ids),
            "tool_ids": list(seal.tool_ids),
            "handler_ids": list(seal.handler_ids),
            "resolution_bindings": resolution_bindings,
            "model_io_id": seal.model_io_id,
            "provider": seal.provider,
            "model": seal.model,
            "attempt": dict(seal.attempt_payload),
            "iteration": seal.iteration,
            "toolkit_id": seal.toolkit_id,
            "toolkit_sha256": seal.toolkit_sha256,
            "catalog_id": seal.catalog_id,
            "catalog_sha256": seal.catalog_sha256,
            "request_payload_id": seal.request_payload_id,
            "request_payload_sha256": seal.request_payload_sha256,
        }

    def _verify_authority_anchor(
        self,
        *,
        draft: _ProviderTurnDraft,
        seal: _ProviderTurnDraftSeal,
    ) -> None:
        try:
            valid = self.__authority_anchor_is_valid(draft, seal)
        except (AttributeError, TypeError, ValueError, UnicodeError) as exc:
            raise PreparedProviderTurnError(
                "provider turn draft authority anchor changed"
            ) from exc
        if not valid:
            raise PreparedProviderTurnError(
                "provider turn draft authority anchor changed"
            )

    def issue(
        self,
        *,
        model_io: object,
        provider: str,
        model: str,
        attempt: AttemptRef,
        iteration: int,
        toolkit: FrozenProviderToolkit,
        registry: DurableToolHandlerRegistry,
        resolutions: tuple[DurableToolHandlerResolution, ...],
        catalog: ToolCatalogEnvelope,
        request_payload: Mapping[str, Any],
        request_payload_sha256: str,
    ) -> _ProviderTurnDraft:
        return self.__mint_draft_authority(
            model_io=model_io,
            provider=provider,
            model=model,
            attempt=attempt,
            iteration=iteration,
            toolkit=toolkit,
            registry=registry,
            resolutions=resolutions,
            catalog=catalog,
            request_payload=request_payload,
            request_payload_sha256=request_payload_sha256,
        )

    def _prune(self) -> None:
        for identity, seal in tuple(self._records.items()):
            if seal.draft_ref() is None:
                self._records.pop(identity, None)

    def verify(self, draft: object) -> _ProviderTurnDraft:
        if type(draft) is not _ProviderTurnDraft:
            raise TypeError("provider turn draft must be exact")
        with self._lock:
            seal = self._records.get(id(draft))
            if (
                type(seal) is not _ProviderTurnDraftSeal
                or seal.draft_ref() is not draft
            ):
                raise PreparedProviderTurnError(
                    "provider turn draft has no issuer authority"
                )
        self._verify_authority_anchor(draft=draft, seal=seal)
        if (
            id(seal.registry) != seal.registry_id
            or type(seal.resolutions) is not tuple
            or tuple(id(item) for item in seal.resolutions) != seal.resolution_ids
            or tuple(id(item.tool) for item in seal.resolutions) != seal.tool_ids
            or tuple(id(item.handler) for item in seal.resolutions) != seal.handler_ids
        ):
            raise PreparedProviderTurnError("provider turn draft authority changed")
        _verify_frozen_provider_toolkit_authority(
            toolkit=draft.toolkit,
            registry=seal.registry,
            resolutions=seal.resolutions,
        )
        self._verify_authority_anchor(draft=draft, seal=seal)
        if (
            id(draft.model_io) != seal.model_io_id
            or draft.provider != seal.provider
            or draft.model != seal.model
            or draft.attempt.to_dict() != dict(seal.attempt_payload)
            or draft.iteration != seal.iteration
            or id(draft.toolkit) != seal.toolkit_id
            or draft.toolkit.toolkit_sha256 != seal.toolkit_sha256
            or id(draft.catalog) != seal.catalog_id
            or draft.catalog.catalog_sha256 != seal.catalog_sha256
            or id(draft._request_payload) != seal.request_payload_id
            or draft._request_payload_sha256 != seal.request_payload_sha256
        ):
            raise PreparedProviderTurnError(
                "provider turn draft changed after issuer binding"
            )
        return draft


_PROVIDER_TURN_DRAFT_ISSUER = _ProviderTurnDraftIssuer()


def _build_provider_turn_draft(
    *,
    model_io: object,
    registry: DurableToolHandlerRegistry,
    resolutions: Sequence[DurableToolHandlerResolution],
    attempt: AttemptRef,
    iteration: int,
    supports_tools: bool,
    request_payload: dict[str, Any],
    prompt_sha256: str,
    exposure_plan_sha256: str,
) -> _ProviderTurnDraft:
    provider = _canonical_provider(getattr(model_io, "provider", None))
    model = _canonical_model(getattr(model_io, "model", None))
    if type(attempt) is not AttemptRef:
        raise TypeError("attempt must be an exact AttemptRef")
    attempt = AttemptRef.from_dict(attempt.to_dict())
    iteration = _bounded_int(iteration, "iteration")
    if iteration > 2**31 - 1:
        raise ModelValidationError("iteration exceeds provider turn limit")
    toolkit, toolkit_resolutions = _build_frozen_provider_toolkit(
        provider=provider,
        supports_tools=supports_tools,
        registry=registry,
        resolutions=resolutions,
    )
    private_payload = _strict_private_payload(request_payload)
    private_payload_sha256 = _canonical_sha256(_thaw_json(private_payload))
    catalog = ToolCatalogEnvelope(
        attempt=attempt,
        iteration=iteration,
        provider=provider,
        model=model,
        semantic_schemas=toolkit.to_provider_json(provider),
        entries=toolkit.catalog_entries,
        required_betas_sha256=toolkit.required_betas_sha256,
        prompt_sha256=_sha256(prompt_sha256, "prompt_sha256"),
        exposure_plan_sha256=_sha256(
            exposure_plan_sha256,
            "exposure_plan_sha256",
        ),
    )
    return _PROVIDER_TURN_DRAFT_ISSUER.issue(
        model_io=model_io,
        provider=provider,
        model=model,
        attempt=attempt,
        iteration=iteration,
        toolkit=toolkit,
        registry=registry,
        resolutions=toolkit_resolutions,
        catalog=catalog,
        request_payload=private_payload,
        request_payload_sha256=private_payload_sha256,
    )


def _verify_draft(draft: object) -> _ProviderTurnDraft:
    draft = _PROVIDER_TURN_DRAFT_ISSUER.verify(draft)
    if type(draft.catalog) is not ToolCatalogEnvelope:
        raise PreparedProviderTurnError("prepared provider draft catalog changed")
    try:
        canonical_catalog = ToolCatalogEnvelope.from_dict(draft.catalog.to_dict())
    except (AttributeError, TypeError, ValueError) as exc:
        raise PreparedProviderTurnError(
            "prepared provider draft catalog is no longer canonical"
        ) from exc
    if canonical_catalog.canonical_bytes() != draft.catalog.canonical_bytes():
        raise PreparedProviderTurnError(
            "prepared provider draft catalog changed after construction"
        )
    if (
        getattr(draft.model_io, "provider", None) != draft.provider
        or getattr(draft.model_io, "model", None) != draft.model
        or type(draft.attempt) is not AttemptRef
        or draft.catalog.attempt != draft.attempt
        or draft.catalog.iteration != draft.iteration
        or draft.catalog.provider != draft.provider
        or draft.catalog.model != draft.model
        or draft.catalog.tool_schema_sha256 != draft.toolkit.tool_schema_sha256
        or draft.catalog.required_betas_sha256 != draft.toolkit.required_betas_sha256
        or draft._request_payload_sha256
        != _canonical_sha256(draft._request_payload_copy())
    ):
        raise PreparedProviderTurnError("prepared provider draft changed")
    return draft


class PersistedToolCatalogAuthority:
    """Fresh-process system assertion for an already-persisted snapshot.

    The pure contract cannot prove persistence.  Production code must keep the
    private mint inside the successful atomic repository path.
    """

    __slots__ = ("__issued_record", "__weakref__")

    def __new__(cls, *args: object, **kwargs: object):
        del cls, args, kwargs
        raise TypeError("PersistedToolCatalogAuthority is issuer-created")

    def __init_subclass__(cls, **kwargs: object) -> None:
        del cls, kwargs
        raise TypeError("PersistedToolCatalogAuthority cannot be subclassed")

    def __setattr__(self, name: str, value: object) -> None:
        del name, value
        raise AttributeError("PersistedToolCatalogAuthority is immutable")

    def __copy__(self):
        raise TypeError("PersistedToolCatalogAuthority cannot be copied or serialized")

    def __deepcopy__(self, memo: object):
        del memo
        raise TypeError("PersistedToolCatalogAuthority cannot be copied or serialized")

    def __reduce__(self):
        raise TypeError("PersistedToolCatalogAuthority cannot be copied or serialized")

    def __reduce_ex__(self, protocol: int):
        del protocol
        raise TypeError("PersistedToolCatalogAuthority cannot be copied or serialized")

    @property
    def attempt(self) -> AttemptRef:
        record = _PERSISTED_TOOL_CATALOG_ISSUER.record_for(self)
        return AttemptRef.from_dict(record.snapshot.attempt.to_dict())

    @property
    def iteration(self) -> int:
        return _PERSISTED_TOOL_CATALOG_ISSUER.record_for(self).snapshot.iteration

    @property
    def catalog_sha256(self) -> str:
        return _PERSISTED_TOOL_CATALOG_ISSUER.record_for(self).snapshot.catalog_sha256

    @property
    def catalog_artifact(self) -> ArtifactRef:
        artifact = _PERSISTED_TOOL_CATALOG_ISSUER.record_for(self).snapshot.artifact
        return ArtifactRef.from_dict(artifact.to_dict())


@dataclass(frozen=True, slots=True, weakref_slot=True)
class _PersistedToolCatalogRecord:
    snapshot: ToolCatalogSnapshot


@dataclass(frozen=True, slots=True)
class _PersistedToolCatalogSeal:
    authority_ref: weakref.ReferenceType[PersistedToolCatalogAuthority]
    record_ref: weakref.ReferenceType[_PersistedToolCatalogRecord]
    snapshot_sha256: str


class _PersistedToolCatalogAuthorityIssuer:
    def __init__(self, *, max_records: int = MAX_PREPARED_PROVIDER_TURNS) -> None:
        if type(max_records) is not int or max_records <= 0:
            raise ValueError("persisted catalog authority capacity must be positive")
        if max_records > MAX_PREPARED_PROVIDER_TURNS:
            raise ValueError("persisted catalog authority capacity is unbounded")
        self._max_records = max_records
        self._lock = threading.RLock()
        self._records: dict[int, _PersistedToolCatalogSeal] = {}

    def issue(self, snapshot: ToolCatalogSnapshot) -> PersistedToolCatalogAuthority:
        if type(snapshot) is not ToolCatalogSnapshot:
            raise TypeError("persisted catalog authority requires an exact snapshot")
        try:
            detached = ToolCatalogSnapshot.from_dict(snapshot.to_dict())
        except (AttributeError, TypeError, ValueError) as exc:
            raise PreparedProviderTurnError(
                "persisted catalog snapshot is not canonical"
            ) from exc
        authority = object.__new__(PersistedToolCatalogAuthority)
        record = _PersistedToolCatalogRecord(snapshot=detached)
        object.__setattr__(
            authority,
            "_PersistedToolCatalogAuthority__issued_record",
            record,
        )
        identity = id(authority)

        def discard(expired: weakref.ReferenceType[object]) -> None:
            with self._lock:
                current = self._records.get(identity)
                if current is not None and (
                    expired is current.authority_ref or expired is current.record_ref
                ):
                    self._records.pop(identity, None)

        authority_ref = weakref.ref(authority, discard)
        record_ref = weakref.ref(record, discard)
        seal = _PersistedToolCatalogSeal(
            authority_ref=authority_ref,
            record_ref=record_ref,
            snapshot_sha256=detached.snapshot_sha256,
        )
        with self._lock:
            self._prune()
            if len(self._records) >= self._max_records:
                raise RuntimeError("persisted catalog authority capacity exceeded")
            self._records[identity] = seal
        return authority

    def _prune(self) -> None:
        for identity, seal in tuple(self._records.items()):
            if seal.authority_ref() is None or seal.record_ref() is None:
                self._records.pop(identity, None)

    def record_for(self, authority: object) -> _PersistedToolCatalogRecord:
        if type(authority) is not PersistedToolCatalogAuthority:
            raise PreparedProviderTurnError(
                "persisted catalog authority was not issued by this issuer"
            )
        with self._lock:
            seal = self._records.get(id(authority))
            if seal is None or seal.authority_ref() is not authority:
                raise PreparedProviderTurnError(
                    "persisted catalog authority was not issued by this issuer"
                )
        try:
            record = object.__getattribute__(
                authority,
                "_PersistedToolCatalogAuthority__issued_record",
            )
        except AttributeError as exc:
            raise PreparedProviderTurnError(
                "persisted catalog authority record is missing"
            ) from exc
        if (
            type(record) is not _PersistedToolCatalogRecord
            or seal.record_ref() is not record
        ):
            raise PreparedProviderTurnError(
                "persisted catalog authority record changed"
            )
        try:
            canonical = ToolCatalogSnapshot.from_dict(record.snapshot.to_dict())
        except (AttributeError, TypeError, ValueError) as exc:
            raise PreparedProviderTurnError(
                "persisted catalog authority snapshot changed"
            ) from exc
        if (
            canonical.snapshot_sha256 != seal.snapshot_sha256
            or canonical.to_dict() != record.snapshot.to_dict()
        ):
            raise PreparedProviderTurnError(
                "persisted catalog authority snapshot changed"
            )
        return record


_PERSISTED_TOOL_CATALOG_ISSUER = _PersistedToolCatalogAuthorityIssuer()


def _issue_persisted_tool_catalog_authority(
    snapshot: ToolCatalogSnapshot,
) -> PersistedToolCatalogAuthority:
    """Private hook for the production repository after durable persistence."""

    return _PERSISTED_TOOL_CATALOG_ISSUER.issue(snapshot)


def verify_persisted_tool_catalog_authority(
    authority: object,
) -> PersistedToolCatalogAuthority:
    _PERSISTED_TOOL_CATALOG_ISSUER.record_for(authority)
    return authority


def _catalog_authority_subject(
    authority: object,
) -> tuple[AttemptRef, int, str, ArtifactRef]:
    if type(authority) is PersistedToolCatalogAuthority:
        record = _PERSISTED_TOOL_CATALOG_ISSUER.record_for(authority)
        snapshot = record.snapshot
        return (
            snapshot.attempt,
            snapshot.iteration,
            snapshot.catalog_sha256,
            snapshot.artifact,
        )
    if type(authority) is RecoveredToolCatalogAuthority:
        recovered = verify_recovered_tool_catalog_authority(authority)
        return (
            recovered.attempt,
            recovered.iteration,
            recovered.catalog_sha256,
            recovered.catalog_artifact,
        )
    raise PreparedProviderTurnError(
        "catalog authority must be a fresh persisted or recovered authority"
    )


class PreparedProviderTurn:
    """Issuer-created capability consumed by one exact ``ModelIO`` object."""

    __slots__ = ("__issued_record", "__weakref__")

    def __new__(cls, *args: object, **kwargs: object):
        del cls, args, kwargs
        raise TypeError("PreparedProviderTurn is issuer-created")

    def __init_subclass__(cls, **kwargs: object) -> None:
        del cls, kwargs
        raise TypeError("PreparedProviderTurn cannot be subclassed")

    def __setattr__(self, name: str, value: object) -> None:
        del name, value
        raise AttributeError("PreparedProviderTurn is immutable")

    def __copy__(self):
        raise TypeError("PreparedProviderTurn cannot be copied or serialized")

    def __deepcopy__(self, memo: object):
        del memo
        raise TypeError("PreparedProviderTurn cannot be copied or serialized")

    def __reduce__(self):
        raise TypeError("PreparedProviderTurn cannot be copied or serialized")

    def __reduce_ex__(self, protocol: int):
        del protocol
        raise TypeError("PreparedProviderTurn cannot be copied or serialized")

    @property
    def provider(self) -> str:
        return _PREPARED_PROVIDER_TURN_ISSUER.record_for(self).provider

    @property
    def model(self) -> str:
        return _PREPARED_PROVIDER_TURN_ISSUER.record_for(self).model

    @property
    def attempt(self) -> AttemptRef:
        record = _PREPARED_PROVIDER_TURN_ISSUER.record_for(self)
        return AttemptRef.from_dict(record.attempt.to_dict())

    @property
    def iteration(self) -> int:
        return _PREPARED_PROVIDER_TURN_ISSUER.record_for(self).iteration

    @property
    def toolkit(self) -> FrozenProviderToolkit:
        return _PREPARED_PROVIDER_TURN_ISSUER.record_for(self).toolkit

    @property
    def catalog_sha256(self) -> str:
        return _PREPARED_PROVIDER_TURN_ISSUER.record_for(self).catalog_sha256


@dataclass(frozen=True, slots=True, weakref_slot=True)
class _PreparedProviderTurnRecord:
    draft: _ProviderTurnDraft
    model_io: object = field(repr=False)
    catalog_authority: object = field(repr=False)
    provider: str
    model: str
    attempt: AttemptRef
    iteration: int
    toolkit: FrozenProviderToolkit
    catalog: ToolCatalogEnvelope
    request_payload: Mapping[str, Any] = field(repr=False)
    catalog_sha256: str
    catalog_artifact_sha256: str


@dataclass(frozen=True, slots=True)
class _PreparedProviderTurnSeal:
    prepared_ref: weakref.ReferenceType[PreparedProviderTurn]
    record_ref: weakref.ReferenceType[_PreparedProviderTurnRecord]
    draft_id: int
    model_io_id: int
    catalog_authority_id: int
    catalog_sha256: str


class _PreparedProviderTurnIssuer:
    def __init__(self, *, max_records: int = MAX_PREPARED_PROVIDER_TURNS) -> None:
        if type(max_records) is not int or max_records <= 0:
            raise ValueError("prepared provider turn capacity must be positive")
        if max_records > MAX_PREPARED_PROVIDER_TURNS:
            raise ValueError("prepared provider turn capacity is unbounded")
        self._max_records = max_records
        self._lock = threading.RLock()
        self._records: dict[int, _PreparedProviderTurnSeal] = {}

    def issue(
        self,
        *,
        draft: _ProviderTurnDraft,
        catalog_authority: object,
    ) -> PreparedProviderTurn:
        draft = _verify_draft(draft)
        (
            authority_attempt,
            authority_iteration,
            catalog_sha256,
            artifact,
        ) = _catalog_authority_subject(catalog_authority)
        catalog_bytes = draft.catalog.canonical_bytes()
        if (
            authority_attempt != draft.attempt
            or authority_iteration != draft.iteration
            or catalog_sha256 != draft.catalog.catalog_sha256
            or artifact.media_type != "application/json"
            or artifact.preview
            or artifact.byte_length != len(catalog_bytes)
            or artifact.sha256 != hashlib.sha256(catalog_bytes).hexdigest()
        ):
            raise PreparedProviderTurnError(
                "catalog authority does not match the provider draft"
            )
        if type(catalog_authority) is PersistedToolCatalogAuthority:
            snapshot = _PERSISTED_TOOL_CATALOG_ISSUER.record_for(
                catalog_authority
            ).snapshot
            if snapshot.envelope.canonical_bytes() != catalog_bytes:
                raise PreparedProviderTurnError(
                    "fresh catalog authority envelope differs from provider draft"
                )

        prepared = object.__new__(PreparedProviderTurn)
        record = _PreparedProviderTurnRecord(
            draft=draft,
            model_io=draft.model_io,
            catalog_authority=catalog_authority,
            provider=draft.provider,
            model=draft.model,
            attempt=draft.attempt,
            iteration=draft.iteration,
            toolkit=draft.toolkit,
            catalog=draft.catalog,
            request_payload=draft._request_payload,
            catalog_sha256=draft.catalog.catalog_sha256,
            catalog_artifact_sha256=artifact.sha256,
        )
        object.__setattr__(
            prepared,
            "_PreparedProviderTurn__issued_record",
            record,
        )
        identity = id(prepared)

        def discard(expired: weakref.ReferenceType[object]) -> None:
            with self._lock:
                current = self._records.get(identity)
                if current is not None and (
                    expired is current.prepared_ref or expired is current.record_ref
                ):
                    self._records.pop(identity, None)

        prepared_ref = weakref.ref(prepared, discard)
        record_ref = weakref.ref(record, discard)
        seal = _PreparedProviderTurnSeal(
            prepared_ref=prepared_ref,
            record_ref=record_ref,
            draft_id=id(draft),
            model_io_id=id(draft.model_io),
            catalog_authority_id=id(catalog_authority),
            catalog_sha256=draft.catalog.catalog_sha256,
        )
        with self._lock:
            self._prune()
            if len(self._records) >= self._max_records:
                raise RuntimeError("prepared provider turn capacity exceeded")
            self._records[identity] = seal
        return prepared

    def _prune(self) -> None:
        for identity, seal in tuple(self._records.items()):
            if seal.prepared_ref() is None or seal.record_ref() is None:
                self._records.pop(identity, None)

    def record_for(self, prepared: object) -> _PreparedProviderTurnRecord:
        if type(prepared) is not PreparedProviderTurn:
            raise PreparedProviderTurnError(
                "prepared provider turn must be an exact issuer authority"
            )
        with self._lock:
            seal = self._records.get(id(prepared))
            if seal is None or seal.prepared_ref() is not prepared:
                raise PreparedProviderTurnError(
                    "prepared provider turn has no issuer authority record"
                )
        try:
            record = object.__getattribute__(
                prepared,
                "_PreparedProviderTurn__issued_record",
            )
        except AttributeError as exc:
            raise PreparedProviderTurnError(
                "prepared provider turn record is missing"
            ) from exc
        if (
            type(record) is not _PreparedProviderTurnRecord
            or seal.record_ref() is not record
            or id(record.draft) != seal.draft_id
            or id(record.model_io) != seal.model_io_id
            or id(record.catalog_authority) != seal.catalog_authority_id
            or record.catalog_sha256 != seal.catalog_sha256
        ):
            raise PreparedProviderTurnError("prepared provider turn record changed")
        draft = _verify_draft(record.draft)
        if (
            draft.model_io is not record.model_io
            or draft.provider != record.provider
            or draft.model != record.model
            or draft.attempt != record.attempt
            or draft.iteration != record.iteration
            or draft.toolkit is not record.toolkit
            or draft.catalog is not record.catalog
            or draft._request_payload is not record.request_payload
            or draft.catalog.catalog_sha256 != record.catalog_sha256
        ):
            raise PreparedProviderTurnError(
                "prepared provider draft changed after issuance"
            )
        (
            authority_attempt,
            authority_iteration,
            catalog_sha256,
            artifact,
        ) = _catalog_authority_subject(record.catalog_authority)
        if (
            authority_attempt != record.attempt
            or authority_iteration != record.iteration
            or catalog_sha256 != record.catalog_sha256
            or artifact.sha256 != record.catalog_artifact_sha256
        ):
            raise PreparedProviderTurnError(
                "prepared provider catalog authority changed"
            )
        return record

    def consume(
        self,
        prepared: object,
        *,
        model_io: object,
        attempt: AttemptRef,
        iteration: int,
    ) -> _ProviderTurnDraft:
        record = self.record_for(prepared)
        if model_io is not record.model_io:
            raise PreparedProviderTurnError(
                "prepared provider turn belongs to another model_io consumer"
            )
        if type(attempt) is not AttemptRef or attempt != record.attempt:
            raise PreparedProviderTurnError(
                "prepared provider turn belongs to another attempt"
            )
        if type(iteration) is not int or iteration != record.iteration:
            raise PreparedProviderTurnError(
                "prepared provider turn belongs to another iteration"
            )
        return record.draft


_PREPARED_PROVIDER_TURN_ISSUER = _PreparedProviderTurnIssuer()


def _issue_prepared_provider_turn(
    *,
    draft: _ProviderTurnDraft,
    catalog_authority: object,
) -> PreparedProviderTurn:
    return _PREPARED_PROVIDER_TURN_ISSUER.issue(
        draft=draft,
        catalog_authority=catalog_authority,
    )


def verify_prepared_provider_turn(
    prepared: object,
    *,
    model_io: object,
    attempt: AttemptRef,
    iteration: int,
) -> PreparedProviderTurn:
    _PREPARED_PROVIDER_TURN_ISSUER.consume(
        prepared,
        model_io=model_io,
        attempt=attempt,
        iteration=iteration,
    )
    return prepared


def _consume_prepared_provider_turn(
    prepared: object,
    *,
    model_io: object,
    attempt: AttemptRef,
    iteration: int,
) -> _ProviderTurnDraft:
    return _PREPARED_PROVIDER_TURN_ISSUER.consume(
        prepared,
        model_io=model_io,
        attempt=attempt,
        iteration=iteration,
    )


__all__ = [
    "FrozenProviderTool",
    "FrozenProviderToolkit",
    "MAX_PREPARED_PROVIDER_TURNS",
    "MAX_PROVIDER_BETA_BYTES",
    "MAX_PROVIDER_REQUIRED_BETAS",
    "MAX_PROVIDER_REQUIRED_BETAS_BYTES",
    "PersistedToolCatalogAuthority",
    "PreparedProviderTurn",
    "PreparedProviderTurnError",
    "verify_persisted_tool_catalog_authority",
    "verify_prepared_provider_turn",
]

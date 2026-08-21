"""Attempt-scoped tool-output projection policy for durable context runtimes.

The module deliberately owns *model-visible* result views only.  Durable
artifact persistence and paged reads stay behind the Context V2 artifact
boundary; callers pass an opaque source reference into this module and retain
ownership of the actual bytes.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping


TOOL_OUTPUT_MANAGEMENT_SCHEMA = "unchain.tool_output_management.v1"
TOOL_OUTPUT_PROJECTION_VERSION = "v1"
TOOL_OUTPUT_POLICY_MAP_SCHEMA = "unchain.tool_output_policy_map.v1"
SUPPORTED_TOOL_OUTPUT_POLICIES = frozenset({"default", "head_tail", "artifact_only"})


class ToolOutputManagementError(ValueError):
    """Base error for a closed Tool Output management boundary."""


class ToolOutputPolicyVersionError(ToolOutputManagementError):
    """A route requested a policy that was not declared by its snapshot."""


class ToolOutputReadError(ToolOutputManagementError):
    """A paged continuation did not point at its original source artifact."""


def canonical_content_bytes(value: Any) -> bytes:
    """Return the stable JSON representation used for output metadata."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def normalize_tool_output_policy(value: Any) -> str:
    """Normalize an undeclared policy to the active-route default name."""

    if not isinstance(value, str):
        return "default"
    normalized = value.strip().lower()
    return normalized if normalized in SUPPORTED_TOOL_OUTPUT_POLICIES else "default"


def _canonical_ref(value: Any) -> str:
    return canonical_content_bytes(value).decode("utf-8")


def _valid_source_ref(value: Any) -> bool:
    """Accept only a non-empty opaque URI or a whole canonical artifact ref."""

    if isinstance(value, str):
        normalized = value.strip()
        return bool(normalized) and "://" in normalized
    if not isinstance(value, Mapping):
        return False
    uri = value.get("uri")
    if isinstance(uri, str) and uri.strip() and "://" in uri:
        return True
    try:
        from unchain.journal import ResourceRef

        reference = ResourceRef.from_dict(value)
    except (TypeError, ValueError):
        return False
    return reference.kind == "artifact" and not reference.fragment


def _require_int(value: Any, *, field_name: str, minimum: int) -> int:
    if isinstance(value, bool):
        raise ToolOutputManagementError(f"{field_name} must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise ToolOutputManagementError(f"{field_name} must be an integer") from error
    if parsed < minimum:
        raise ToolOutputManagementError(f"{field_name} is below its minimum")
    return parsed


@dataclass(frozen=True)
class ToolOutputPolicy:
    """One versioned, route-manifest-declared projection policy."""

    name: str = "default"
    version: str = TOOL_OUTPUT_PROJECTION_VERSION
    preview_chars: int = 1_200
    inline_chars: int = 16_000

    def __post_init__(self) -> None:
        name = normalize_tool_output_policy(self.name)
        if name != self.name:
            raise ToolOutputManagementError("tool output policy name is unsupported")
        version = str(self.version or "").strip()
        if version != TOOL_OUTPUT_PROJECTION_VERSION:
            raise ToolOutputPolicyVersionError("tool output policy version is unsupported")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "version", version)
        object.__setattr__(
            self,
            "preview_chars",
            _require_int(self.preview_chars, field_name="preview_chars", minimum=0),
        )
        object.__setattr__(
            self,
            "inline_chars",
            _require_int(self.inline_chars, field_name="inline_chars", minimum=0),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "preview_chars": self.preview_chars,
            "inline_chars": self.inline_chars,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ToolOutputPolicy":
        if not isinstance(value, Mapping) or set(value) != {
            "name",
            "version",
            "preview_chars",
            "inline_chars",
        }:
            raise ToolOutputManagementError("tool output policy shape is invalid")
        return cls(
            name=value["name"],
            version=value["version"],
            preview_chars=value["preview_chars"],
            inline_chars=value["inline_chars"],
        )


@dataclass(frozen=True)
class ToolOutputProjection:
    """A durable projection receipt; raw output never appears in metadata."""

    payload: dict[str, Any]
    metadata: dict[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", copy.deepcopy(self.payload))
        object.__setattr__(self, "metadata", copy.deepcopy(self.metadata))


@dataclass(frozen=True)
class ToolOutputBudget:
    """The one reduction owner selected for an attempt."""

    projection_active: bool

    @property
    def legacy_budget_enabled(self) -> bool:
        return not self.projection_active


@dataclass(frozen=True)
class ToolOutputReadRequest:
    """A page request permanently bound to one source artifact."""

    source_ref: Any
    offset: int
    limit: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_ref", copy.deepcopy(self.source_ref))
        object.__setattr__(
            self, "offset", _require_int(self.offset, field_name="offset", minimum=0)
        )
        object.__setattr__(
            self, "limit", _require_int(self.limit, field_name="limit", minimum=1)
        )


@dataclass
class ToolOutputManager:
    """One output manager per attempt, configured by a closed route snapshot."""

    active: bool
    default_policy: ToolOutputPolicy
    policies: dict[str, ToolOutputPolicy]
    attempt_id: str = ""
    _receipts: dict[str, ToolOutputProjection] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        normalized: dict[str, ToolOutputPolicy] = {}
        for name, policy in self.policies.items():
            if not isinstance(policy, ToolOutputPolicy) or name != policy.name:
                raise ToolOutputManagementError("policy snapshot is invalid")
            normalized[name] = policy
        if self.default_policy.name not in normalized:
            raise ToolOutputManagementError("default policy is missing from snapshot")
        self.policies = normalized
        self.attempt_id = str(self.attempt_id or "")

    @classmethod
    def active_default(
        cls,
        *,
        attempt_id: str = "",
        preview_chars: int = 1_200,
        inline_chars: int = 16_000,
    ) -> "ToolOutputManager":
        policies = {
            name: ToolOutputPolicy(
                name=name,
                preview_chars=preview_chars,
                inline_chars=inline_chars,
            )
            for name in sorted(SUPPORTED_TOOL_OUTPUT_POLICIES)
        }
        return cls(
            active=True,
            default_policy=policies["default"],
            policies=policies,
            attempt_id=attempt_id,
        )

    @classmethod
    def disabled_default(cls, *, attempt_id: str = "") -> "ToolOutputManager":
        active = cls.active_default(attempt_id=attempt_id)
        return cls(
            active=False,
            default_policy=active.default_policy,
            policies=active.policies,
            attempt_id=attempt_id,
        )

    @classmethod
    def from_runtime_config(
        cls,
        runtime_config: Any,
        *,
        attempt_id: str = "",
    ) -> "ToolOutputManager":
        config = runtime_config if isinstance(runtime_config, Mapping) else {}
        snapshot = config.get("tool_output_management")
        if snapshot is None:
            # Compatibility only for active V2 callers during the migration.
            if config.get("tool_output_projection") is True:
                return cls.active_default(attempt_id=attempt_id)
            policies = cls.active_default().policies
            return cls(
                active=False,
                default_policy=policies["default"],
                policies=policies,
                attempt_id=attempt_id,
            )
        if not isinstance(snapshot, Mapping) or set(snapshot) != {
            "schema",
            "mode",
            "default_policy",
            "policies",
        }:
            raise ToolOutputManagementError("tool output management snapshot shape is invalid")
        if snapshot["schema"] != TOOL_OUTPUT_MANAGEMENT_SCHEMA:
            raise ToolOutputManagementError("tool output management schema is unsupported")
        if snapshot["mode"] not in {"active", "disabled"}:
            raise ToolOutputManagementError("tool output management mode is invalid")
        raw_policies = snapshot["policies"]
        if not isinstance(raw_policies, list) or not raw_policies:
            raise ToolOutputManagementError("tool output management policies are invalid")
        policies = {
            policy.name: policy
            for policy in (ToolOutputPolicy.from_dict(item) for item in raw_policies)
        }
        if len(policies) != len(raw_policies):
            raise ToolOutputManagementError("tool output management policies are duplicate")
        default_name = snapshot["default_policy"]
        if not isinstance(default_name, str) or default_name not in policies:
            raise ToolOutputManagementError("tool output management default policy is invalid")
        return cls(
            active=snapshot["mode"] == "active",
            default_policy=policies[default_name],
            policies=policies,
            attempt_id=attempt_id,
        )

    def runtime_snapshot(self) -> dict[str, Any]:
        return {
            "schema": TOOL_OUTPUT_MANAGEMENT_SCHEMA,
            "mode": "active" if self.active else "disabled",
            "default_policy": self.default_policy.name,
            "policies": [
                self.policies[name].to_dict() for name in sorted(self.policies)
            ],
        }

    @classmethod
    def active_runtime_config_for_toolkit(
        cls,
        toolkit: Any,
        *,
        attempt_id: str,
    ) -> dict[str, Any]:
        """Create the active route snapshot from exposed toolkit declarations."""

        manager = cls.active_default(attempt_id=attempt_id)
        tools = getattr(toolkit, "tools", None)
        declared: dict[str, str] = {}
        if tools is not None:
            if not isinstance(tools, Mapping):
                raise ToolOutputManagementError("tool output toolkit shape is invalid")
            for raw_name, tool in tools.items():
                if not isinstance(raw_name, str) or not raw_name.strip():
                    raise ToolOutputManagementError("tool output toolkit name is invalid")
                requested = getattr(tool, "output_policy", None)
                if requested is None:
                    continue
                declared[raw_name] = manager.resolve_policy(requested).name
        config = {"tool_output_management": manager.runtime_snapshot()}
        if declared:
            config["tool_output_policy_map"] = {
                "schema": TOOL_OUTPUT_POLICY_MAP_SCHEMA,
                "policies": dict(sorted(declared.items())),
            }
        return config

    @property
    def legacy_budget_enabled(self) -> bool:
        """Active V2 projection owns reduction; the old budget must not run."""

        return ToolOutputBudget(self.active).legacy_budget_enabled

    def resolve_policy(self, requested: Any = None) -> ToolOutputPolicy:
        if requested is None or requested == "":
            return self.default_policy
        if not isinstance(requested, str):
            raise ToolOutputPolicyVersionError("requested tool output policy is invalid")
        name = requested.strip().lower()
        policy = self.policies.get(name)
        if policy is None:
            raise ToolOutputPolicyVersionError(
                "requested tool output policy is not declared by the route snapshot"
            )
        return policy

    def resolve_policy_for_tool(
        self,
        runtime_config: Any,
        *,
        tool_name: str,
    ) -> ToolOutputPolicy:
        """Resolve one host-declared tool policy from the closed route map."""

        config = runtime_config if isinstance(runtime_config, Mapping) else {}
        policy_map = config.get("tool_output_policy_map")
        if policy_map is None:
            return self.resolve_policy()
        if not isinstance(policy_map, Mapping) or set(policy_map) != {
            "schema",
            "policies",
        }:
            raise ToolOutputManagementError("tool output policy map shape is invalid")
        if policy_map.get("schema") != TOOL_OUTPUT_POLICY_MAP_SCHEMA:
            raise ToolOutputManagementError("tool output policy map schema is unsupported")
        policies = policy_map.get("policies")
        if not isinstance(policies, Mapping):
            raise ToolOutputManagementError("tool output policy map policies are invalid")
        normalized_name = str(tool_name or "").strip()
        if not normalized_name:
            raise ToolOutputManagementError("tool output policy map tool name is invalid")
        for name, value in policies.items():
            if not isinstance(name, str) or not name.strip() or not isinstance(value, str):
                raise ToolOutputManagementError("tool output policy map entry is invalid")
        return self.resolve_policy(policies.get(normalized_name))

    def project(
        self,
        result_bytes: bytes,
        *,
        full_output_ref: Any,
        digest: str,
        content_bytes: int,
        requested_policy: Any = None,
        call_id: str = "",
    ) -> ToolOutputProjection:
        if not self.active:
            raise ToolOutputManagementError("tool output projection is not active")
        if not isinstance(result_bytes, bytes):
            raise TypeError("result_bytes must be bytes")
        policy = self.resolve_policy(requested_policy)
        canonical_ref = _canonical_ref(full_output_ref)
        if (
            not _valid_source_ref(full_output_ref)
            or not canonical_ref
            or not isinstance(digest, str)
            or len(digest) != 64
        ):
            raise ToolOutputManagementError("tool output source receipt is invalid")
        if hashlib.sha256(result_bytes).hexdigest() != digest:
            raise ToolOutputManagementError("tool output digest does not match raw bytes")
        if int(content_bytes) != len(result_bytes):
            raise ToolOutputManagementError("tool output byte count does not match raw bytes")
        text = result_bytes.decode("utf-8", errors="replace")
        if not text:
            payload: dict[str, Any] = {"projection": "empty"}
        elif policy.name == "artifact_only":
            payload = {
                "projection": "artifact_only",
                "note": "Full tool output is available in durable artifact",
            }
        elif policy.name == "head_tail":
            payload = {
                "projection": "head_tail",
                "preview": text[: policy.preview_chars],
                "tail_preview": text[-policy.preview_chars :] if policy.preview_chars else "",
                "content_chars": len(text),
            }
        else:
            payload = {"projection": "default"}
            if len(result_bytes) > policy.inline_chars:
                payload.update({"inline": False, "preview": text[: policy.preview_chars]})
            else:
                payload.update({"inline": True, "preview": text})
        payload.update(
            {
                "full_output_ref": copy.deepcopy(full_output_ref),
                "content_bytes": len(result_bytes),
                "content_sha256": digest,
            }
        )
        metadata = {
            "projection_policy": policy.name,
            "projection_version": policy.version,
            "inline": bool(payload.get("inline", False)),
            "projection_bytes": len(canonical_content_bytes(payload)),
            "source_ref_sha256": hashlib.sha256(canonical_ref.encode("utf-8")).hexdigest(),
        }
        receipt = ToolOutputProjection(payload=payload, metadata=metadata)
        normalized_call_id = str(call_id or "").strip()
        if normalized_call_id:
            existing = self._receipts.get(normalized_call_id)
            if existing is not None:
                if existing != receipt:
                    raise ToolOutputManagementError(
                        "tool output projection was already recorded with different content"
                    )
                return existing
            self._receipts[normalized_call_id] = receipt
        return receipt

    @staticmethod
    def compact_historical_message(
        message: Mapping[str, Any],
        *,
        call_ids: list[str] | tuple[str, ...] | set[str] | frozenset[str],
    ) -> dict[str, Any]:
        """Return the provider-valid historical ref view for a tool result.

        This is deliberately a Tool Output policy operation rather than a
        compiler operation: compilers select when a historical projection is
        needed, while the manager owns every provider wire-shape rewrite.
        """

        updated = copy.deepcopy(dict(message))
        normalized_call_ids = sorted({str(call_id) for call_id in call_ids if call_id})
        if not normalized_call_ids:
            return updated
        marker_payload = {
            "memory_v2_compacted": True,
            "call_ids": normalized_call_ids,
            "note": "Full tool output is available in the durable context journal.",
        }
        marker = json.dumps(marker_payload, ensure_ascii=False)
        if updated.get("role") == "tool":
            updated["content"] = marker
        elif updated.get("type") in {
            "function_call_output",
            "computer_call_output",
            "tool_result",
        }:
            updated["output"] = marker
        content = updated.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    block["content"] = marker
        parts = updated.get("parts")
        if isinstance(parts, list):
            for part in parts:
                if isinstance(part, dict) and isinstance(part.get("function_response"), dict):
                    response = part["function_response"]
                    response["response"] = {
                        "memory_v2_compacted": True,
                        "call_ids": normalized_call_ids,
                    }
        return updated

    def read_page(
        self,
        *,
        source_ref: Any,
        offset: int,
        limit: int,
        continuation: ToolOutputReadRequest | None = None,
    ) -> ToolOutputReadRequest:
        if continuation is not None and _canonical_ref(source_ref) != _canonical_ref(
            continuation.source_ref
        ):
            raise ToolOutputReadError("tool output continuation changed source artifact")
        return ToolOutputReadRequest(source_ref=source_ref, offset=offset, limit=limit)


__all__ = [
    "SUPPORTED_TOOL_OUTPUT_POLICIES",
    "TOOL_OUTPUT_MANAGEMENT_SCHEMA",
    "TOOL_OUTPUT_POLICY_MAP_SCHEMA",
    "TOOL_OUTPUT_PROJECTION_VERSION",
    "ToolOutputManagementError",
    "ToolOutputManager",
    "ToolOutputBudget",
    "ToolOutputPolicy",
    "ToolOutputPolicyVersionError",
    "ToolOutputProjection",
    "ToolOutputReadError",
    "ToolOutputReadRequest",
    "canonical_content_bytes",
    "normalize_tool_output_policy",
]

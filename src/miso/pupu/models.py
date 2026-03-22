from __future__ import annotations

import copy
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

SERVER_STATUSES = {
    "draft",
    "ready_for_review",
    "testing",
    "test_passed",
    "test_failed",
    "enabled",
    "disabled",
    "needs_secret",
    "revoked",
}


def _copy_jsonish(value: Any) -> Any:
    return copy.deepcopy(value)


def _string_tuple(values: list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    result: list[str] = []
    for raw in values or ():
        text = str(raw).strip()
        if text and text not in result:
            result.append(text)
    return tuple(result)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def slugify(value: str, *, fallback: str = "mcp") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    return slug or fallback


@dataclass(frozen=True)
class ToolPreview:
    name: str
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any] | str) -> "ToolPreview":
        if isinstance(value, str):
            return cls(name=value)
        return cls(
            name=str(value.get("name", "")).strip(),
            description=str(value.get("description", "")).strip(),
        )


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    kind: str = "string"
    required: bool = False
    secret: bool = False
    placeholder: str = ""
    help_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "kind": self.kind,
            "required": self.required,
            "secret": self.secret,
            "placeholder": self.placeholder,
            "help_text": self.help_text,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "FieldSpec":
        return cls(
            key=str(value.get("key", "")).strip(),
            label=str(value.get("label", value.get("key", ""))).strip(),
            kind=str(value.get("kind", "string")).strip() or "string",
            required=bool(value.get("required", False)),
            secret=bool(value.get("secret", False)),
            placeholder=str(value.get("placeholder", "")).strip(),
            help_text=str(value.get("help_text", value.get("helpText", ""))).strip(),
        )


@dataclass(frozen=True)
class InstallProfile:
    id: str
    label: str
    runtime: str
    transport: str
    platforms: tuple[str, ...]
    fields: tuple[FieldSpec, ...]
    required_secrets: tuple[str, ...]
    default_values: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "runtime": self.runtime,
            "transport": self.transport,
            "platforms": list(self.platforms),
            "fields": [field.to_dict() for field in self.fields],
            "required_secrets": list(self.required_secrets),
            "default_values": _copy_jsonish(self.default_values),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "InstallProfile":
        fields_raw = value.get("fields", [])
        return cls(
            id=str(value.get("id", "")).strip(),
            label=str(value.get("label", value.get("id", ""))).strip(),
            runtime=str(value.get("runtime", "")).strip(),
            transport=str(value.get("transport", "")).strip(),
            platforms=_string_tuple(value.get("platforms")),
            fields=tuple(FieldSpec.from_dict(item) for item in fields_raw if isinstance(item, dict)),
            required_secrets=_string_tuple(
                value.get("required_secrets", value.get("requiredSecrets"))
            ),
            default_values=_copy_jsonish(value.get("default_values", value.get("defaultValues", {}))),
        )


@dataclass(frozen=True)
class ConnectionIssue:
    code: str
    message: str
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ConnectionIssue":
        return cls(
            code=str(value.get("code", "")).strip(),
            message=str(value.get("message", "")).strip(),
            detail=str(value.get("detail", "")).strip(),
        )


@dataclass(frozen=True)
class ConnectionTestResult:
    status: str
    phase: str
    summary: str
    tool_count: int
    tools: tuple[ToolPreview, ...]
    warnings: tuple[str, ...]
    errors: tuple[ConnectionIssue, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "phase": self.phase,
            "summary": self.summary,
            "tool_count": self.tool_count,
            "tools": [tool.to_dict() for tool in self.tools],
            "warnings": list(self.warnings),
            "errors": [error.to_dict() for error in self.errors],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> "ConnectionTestResult | None":
        if not isinstance(value, dict):
            return None
        return cls(
            status=str(value.get("status", "")).strip(),
            phase=str(value.get("phase", "")).strip(),
            summary=str(value.get("summary", "")).strip(),
            tool_count=int(value.get("tool_count", 0)),
            tools=tuple(
                ToolPreview.from_dict(item) for item in value.get("tools", ()) if isinstance(item, (dict, str))
            ),
            warnings=_string_tuple(value.get("warnings")),
            errors=tuple(
                ConnectionIssue.from_dict(item)
                for item in value.get("errors", ())
                if isinstance(item, dict)
            ),
        )


@dataclass(frozen=True)
class DraftEntry:
    entry_id: str
    source_kind: str
    display_name: str
    profile_candidates: tuple[InstallProfile, ...]
    prefilled_config: dict[str, Any]
    required_fields: tuple[str, ...]
    required_secrets: tuple[str, ...]
    warnings: tuple[str, ...]
    catalog_entry_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "source_kind": self.source_kind,
            "display_name": self.display_name,
            "profile_candidates": [profile.to_dict() for profile in self.profile_candidates],
            "prefilled_config": _copy_jsonish(self.prefilled_config),
            "required_fields": list(self.required_fields),
            "required_secrets": list(self.required_secrets),
            "warnings": list(self.warnings),
            "catalog_entry_id": self.catalog_entry_id,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "DraftEntry":
        return cls(
            entry_id=str(value.get("entry_id", "")).strip(),
            source_kind=str(value.get("source_kind", "")).strip(),
            display_name=str(value.get("display_name", "")).strip(),
            profile_candidates=tuple(
                InstallProfile.from_dict(item)
                for item in value.get("profile_candidates", ())
                if isinstance(item, dict)
            ),
            prefilled_config=_copy_jsonish(value.get("prefilled_config", {})),
            required_fields=_string_tuple(value.get("required_fields")),
            required_secrets=_string_tuple(value.get("required_secrets")),
            warnings=_string_tuple(value.get("warnings")),
            catalog_entry_id=(
                str(value.get("catalog_entry_id", "")).strip() or None
            ),
        )


@dataclass(frozen=True)
class MCPImportDraft:
    draft_id: str
    source_kind: str
    source_label: str
    warnings: tuple[str, ...]
    entries: tuple[DraftEntry, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "draft_id": self.draft_id,
            "source_kind": self.source_kind,
            "source_label": self.source_label,
            "warnings": list(self.warnings),
            "entries": [entry.to_dict() for entry in self.entries],
        }

    @classmethod
    def create(
        cls,
        *,
        source_kind: str,
        source_label: str,
        warnings: list[str] | tuple[str, ...] | None = None,
        entries: list[DraftEntry] | tuple[DraftEntry, ...] | None = None,
    ) -> "MCPImportDraft":
        return cls(
            draft_id=f"draft_{uuid.uuid4().hex}",
            source_kind=source_kind,
            source_label=source_label,
            warnings=_string_tuple(warnings),
            entries=tuple(entries or ()),
        )


@dataclass(frozen=True)
class CatalogEntry:
    id: str
    slug: str
    name: str
    publisher: str
    description: str
    icon_url: str
    verification: str
    source_url: str
    tags: tuple[str, ...]
    revoked: bool
    install_profiles: tuple[InstallProfile, ...]
    tool_preview: tuple[ToolPreview, ...]
    min_app_version: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "slug": self.slug,
            "name": self.name,
            "publisher": self.publisher,
            "description": self.description,
            "icon_url": self.icon_url,
            "verification": self.verification,
            "source_url": self.source_url,
            "tags": list(self.tags),
            "revoked": self.revoked,
            "install_profiles": [profile.to_dict() for profile in self.install_profiles],
            "tool_preview": [item.to_dict() for item in self.tool_preview],
            "min_app_version": self.min_app_version,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "CatalogEntry":
        return cls(
            id=str(value.get("id", "")).strip(),
            slug=str(value.get("slug", value.get("id", ""))).strip(),
            name=str(value.get("name", "")).strip(),
            publisher=str(value.get("publisher", "")).strip(),
            description=str(value.get("description", "")).strip(),
            icon_url=str(value.get("icon_url", value.get("iconUrl", ""))).strip(),
            verification=str(value.get("verification", "")).strip(),
            source_url=str(value.get("source_url", value.get("sourceUrl", ""))).strip(),
            tags=_string_tuple(value.get("tags")),
            revoked=bool(value.get("revoked", False)),
            install_profiles=tuple(
                InstallProfile.from_dict(item)
                for item in value.get("install_profiles", value.get("installProfiles", ()))
                if isinstance(item, dict)
            ),
            tool_preview=tuple(
                ToolPreview.from_dict(item)
                for item in value.get("tool_preview", value.get("toolPreview", ()))
                if isinstance(item, (dict, str))
            ),
            min_app_version=(
                str(value.get("min_app_version", value.get("minAppVersion", ""))).strip() or None
            ),
        )


@dataclass(frozen=True)
class InstalledServer:
    instance_id: str
    instance_slug: str
    display_name: str
    source_kind: str
    runtime: str
    transport: str
    status: str
    enabled: bool
    normalized_config: dict[str, Any]
    required_secrets: tuple[str, ...]
    catalog_entry_id: str | None = None
    tool_count: int = 0
    cached_tools: tuple[ToolPreview, ...] = ()
    last_test_result: ConnectionTestResult | None = None
    updated_at: str = ""

    def __post_init__(self) -> None:
        if self.status not in SERVER_STATUSES:
            raise ValueError(f"unsupported installed server status: {self.status}")

    @property
    def needs_secret(self) -> bool:
        return self.status == "needs_secret"

    def with_updates(self, **kwargs: Any) -> "InstalledServer":
        payload = self.to_record()
        if "cached_tools" in kwargs:
            payload["cached_tools"] = [
                item.to_dict() if isinstance(item, ToolPreview) else item
                for item in kwargs.pop("cached_tools")
            ]
        if "last_test_result" in kwargs:
            last_test_result = kwargs.pop("last_test_result")
            payload["last_test_result"] = (
                last_test_result.to_dict()
                if isinstance(last_test_result, ConnectionTestResult)
                else last_test_result
            )
        payload.update(kwargs)
        return InstalledServer.from_record(payload)

    def to_record(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "instance_slug": self.instance_slug,
            "display_name": self.display_name,
            "catalog_entry_id": self.catalog_entry_id,
            "source_kind": self.source_kind,
            "runtime": self.runtime,
            "transport": self.transport,
            "status": self.status,
            "enabled": self.enabled,
            "normalized_config": _copy_jsonish(self.normalized_config),
            "required_secrets": list(self.required_secrets),
            "tool_count": self.tool_count,
            "cached_tools": [item.to_dict() for item in self.cached_tools],
            "last_test_result": self.last_test_result.to_dict() if self.last_test_result else None,
            "updated_at": self.updated_at,
        }

    def to_public_dict(self, *, configured_secrets: list[str] | tuple[str, ...] | None = None) -> dict[str, Any]:
        configured = _string_tuple(configured_secrets)
        return {
            "instance_id": self.instance_id,
            "display_name": self.display_name,
            "catalog_entry_id": self.catalog_entry_id,
            "source_kind": self.source_kind,
            "runtime": self.runtime,
            "transport": self.transport,
            "status": self.status,
            "enabled": self.enabled,
            "needs_secret": self.needs_secret,
            "tool_count": self.tool_count,
            "cached_tools": [item.to_dict() for item in self.cached_tools],
            "last_test_result": self.last_test_result.to_dict() if self.last_test_result else None,
            "updated_at": self.updated_at,
            "normalized_config": _copy_jsonish(self.normalized_config),
            "required_secrets": list(self.required_secrets),
            "configured_secrets": list(configured),
        }

    @classmethod
    def create(
        cls,
        *,
        display_name: str,
        source_kind: str,
        runtime: str,
        transport: str,
        normalized_config: dict[str, Any],
        required_secrets: list[str] | tuple[str, ...] | None = None,
        catalog_entry_id: str | None = None,
        status: str = "ready_for_review",
        enabled: bool = False,
    ) -> "InstalledServer":
        instance_id = f"mcp_{uuid.uuid4().hex}"
        return cls(
            instance_id=instance_id,
            instance_slug=slugify(display_name, fallback=instance_id),
            display_name=display_name,
            catalog_entry_id=catalog_entry_id,
            source_kind=source_kind,
            runtime=runtime,
            transport=transport,
            status=status,
            enabled=enabled,
            normalized_config=_copy_jsonish(normalized_config),
            required_secrets=_string_tuple(required_secrets),
            updated_at=utc_now_iso(),
        )

    @classmethod
    def from_record(cls, value: dict[str, Any]) -> "InstalledServer":
        return cls(
            instance_id=str(value.get("instance_id", "")).strip(),
            instance_slug=str(value.get("instance_slug", "")).strip(),
            display_name=str(value.get("display_name", "")).strip(),
            catalog_entry_id=(
                str(value.get("catalog_entry_id", "")).strip() or None
            ),
            source_kind=str(value.get("source_kind", "")).strip(),
            runtime=str(value.get("runtime", "")).strip(),
            transport=str(value.get("transport", "")).strip(),
            status=str(value.get("status", "")).strip(),
            enabled=bool(value.get("enabled", False)),
            normalized_config=_copy_jsonish(value.get("normalized_config", {})),
            required_secrets=_string_tuple(value.get("required_secrets")),
            tool_count=int(value.get("tool_count", 0)),
            cached_tools=tuple(
                ToolPreview.from_dict(item)
                for item in value.get("cached_tools", ())
                if isinstance(item, (dict, str))
            ),
            last_test_result=ConnectionTestResult.from_dict(value.get("last_test_result")),
            updated_at=str(value.get("updated_at", "")).strip() or utc_now_iso(),
        )


__all__ = [
    "CatalogEntry",
    "ConnectionIssue",
    "ConnectionTestResult",
    "DraftEntry",
    "FieldSpec",
    "InstallProfile",
    "InstalledServer",
    "MCPImportDraft",
    "SERVER_STATUSES",
    "ToolPreview",
    "slugify",
    "utc_now_iso",
]

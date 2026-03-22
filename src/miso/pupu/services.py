from __future__ import annotations

import copy
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import httpx

from ..toolkits import MCPToolkit
from ..tools import Tool, ToolParameter, Toolkit
from .models import (
    CatalogEntry,
    ConnectionIssue,
    ConnectionTestResult,
    DraftEntry,
    FieldSpec,
    InstallProfile,
    InstalledServer,
    MCPImportDraft,
    ToolPreview,
    slugify,
    utc_now_iso,
)
from .stores import FileCatalogCache, FileInstalledServerStore, InMemorySecretStore

VALID_RUNTIMES = {"local", "remote"}
VALID_TRANSPORTS = {"stdio", "sse", "streamable_http"}
GITHUB_CANDIDATE_PATHS = (
    "server.json",
    "mcp.json",
    ".mcp.json",
    "claude_desktop_config.json",
)
SENSITIVE_TOKENS = (
    "token",
    "secret",
    "password",
    "passwd",
    "bearer",
    "api-key",
    "apikey",
    "api_key",
    "auth",
)


def _version_tuple(value: str | None) -> tuple[int, ...]:
    if not value:
        return ()
    parts: list[int] = []
    for chunk in re.split(r"[^\d]+", value):
        if chunk:
            parts.append(int(chunk))
    return tuple(parts)


def _copy_jsonish(value: Any) -> Any:
    return copy.deepcopy(value)


def _ensure_runtime(value: str) -> str:
    runtime = str(value or "").strip().lower()
    if runtime not in VALID_RUNTIMES:
        raise ValueError(f"unsupported runtime: {value}")
    return runtime


def _ensure_transport(value: str, *, runtime: str) -> str:
    transport = str(value or "").strip().lower()
    if not transport:
        return "stdio" if runtime == "local" else "sse"
    if transport not in VALID_TRANSPORTS:
        raise ValueError(f"unsupported transport: {value}")
    if runtime == "local" and transport != "stdio":
        raise ValueError("local MCP profiles must use stdio transport")
    return transport


def _coerce_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("expected a list of strings")
    result: list[str] = []
    for item in value:
        result.append(str(item))
    return result


def _coerce_string_dict(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("expected a mapping of strings")
    result: dict[str, str] = {}
    for key, item in value.items():
        result[str(key)] = str(item)
    return result


def _looks_sensitive_name(value: str) -> bool:
    lowered = str(value or "").strip().lower()
    return any(token in lowered for token in SENSITIVE_TOKENS)


def _set_nested_value(payload: dict[str, Any], dotted_key: str, value: str) -> None:
    parts = dotted_key.split(".")
    if len(parts) == 1:
        payload[parts[0]] = value
        return

    cursor: dict[str, Any] = payload
    for part in parts[:-1]:
        current = cursor.get(part)
        if not isinstance(current, dict):
            current = {}
            cursor[part] = current
        cursor = current
    cursor[parts[-1]] = value


def _tool_previews_from_runtime_tools(toolkit: Toolkit) -> tuple[ToolPreview, ...]:
    previews = [
        ToolPreview(name=tool.name, description=tool.description)
        for tool in toolkit.tools.values()
    ]
    return tuple(sorted(previews, key=lambda item: item.name))


def _default_fields_for_profile(runtime: str, transport: str, required_secrets: list[str]) -> tuple[FieldSpec, ...]:
    fields: list[FieldSpec] = []
    if runtime == "local":
        fields.extend(
            [
                FieldSpec(key="command", label="Command", required=True, placeholder="npx"),
                FieldSpec(key="args", label="Arguments", kind="string_list"),
                FieldSpec(key="cwd", label="Working directory", placeholder="/path/to/project"),
                FieldSpec(key="env", label="Environment", kind="mapping"),
            ]
        )
    else:
        fields.extend(
            [
                FieldSpec(key="url", label="URL", required=True, placeholder="https://example.com/mcp"),
                FieldSpec(key="transport", label="Transport", required=True, placeholder=transport),
                FieldSpec(key="headers", label="Headers", kind="mapping"),
                FieldSpec(key="auth", label="Auth", kind="mapping"),
            ]
        )
    for secret_key in required_secrets:
        fields.append(
            FieldSpec(
                key=secret_key,
                label=secret_key,
                kind="secret",
                required=True,
                secret=True,
                placeholder="Configured locally only",
            )
        )
    return tuple(fields)


def _sanitize_sensitive_mappings(config: dict[str, Any]) -> tuple[dict[str, Any], tuple[str, ...]]:
    sanitized = _copy_jsonish(config)
    required_secrets: list[str] = []

    for mapping_key in ("env", "headers", "auth"):
        mapping = sanitized.get(mapping_key)
        if not isinstance(mapping, dict):
            continue
        cleaned: dict[str, Any] = {}
        for key, value in mapping.items():
            target = f"{mapping_key}.{key}"
            if _looks_sensitive_name(key):
                if target not in required_secrets:
                    required_secrets.append(target)
                continue
            cleaned[str(key)] = value
        sanitized[mapping_key] = cleaned

    return sanitized, tuple(required_secrets)


def _materialize_instance_config(instance: InstalledServer, secret_store: InMemorySecretStore) -> dict[str, Any]:
    payload = _copy_jsonish(instance.normalized_config)
    resolved = secret_store.resolve_secrets(instance.instance_id, instance.required_secrets)
    for target, value in resolved.items():
        _set_nested_value(payload, target, value)
    return payload


def _default_toolkit_factory(instance: InstalledServer, materialized_config: dict[str, Any]) -> MCPToolkit:
    runtime = instance.runtime
    if runtime == "local":
        return MCPToolkit(
            command=str(materialized_config.get("command", "")).strip(),
            args=_coerce_string_list(materialized_config.get("args")),
            env=_coerce_string_dict(materialized_config.get("env")),
            cwd=(str(materialized_config.get("cwd", "")).strip() or None),
            transport="stdio",
        )
    headers = _coerce_string_dict(materialized_config.get("headers"))
    auth = materialized_config.get("auth")
    if isinstance(auth, dict):
        for key, value in auth.items():
            headers[str(key)] = str(value)
    return MCPToolkit(
        url=str(materialized_config.get("url", "")).strip(),
        headers=headers or None,
        transport=_ensure_transport(str(materialized_config.get("transport", "")), runtime="remote"),
    )


class GitHubRepositoryClient:
    def __init__(self, *, http_client: httpx.Client | None = None):
        self.http_client = http_client or httpx.Client(timeout=10.0, follow_redirects=True)

    def parse_repo_url(self, url: str) -> tuple[str, str, str | None]:
        parsed = urlparse(url)
        if parsed.netloc not in {"github.com", "www.github.com"}:
            raise ValueError("GitHub import requires a github.com repository URL")
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) < 2:
            raise ValueError("GitHub import requires an owner/repository URL")
        branch: str | None = None
        if len(parts) >= 4 and parts[2] == "tree":
            branch = parts[3]
        return parts[0], parts[1], branch

    def get_default_branch(self, owner: str, repo: str) -> str:
        response = self.http_client.get(f"https://api.github.com/repos/{owner}/{repo}")
        response.raise_for_status()
        payload = response.json()
        default_branch = str(payload.get("default_branch", "")).strip()
        if not default_branch:
            raise ValueError("GitHub repository did not return a default_branch")
        return default_branch

    def fetch_file(self, owner: str, repo: str, branch: str, path: str) -> str | None:
        raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"
        response = self.http_client.get(raw_url)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.text


class CatalogService:
    def __init__(
        self,
        *,
        remote_url: str | None = None,
        cache: FileCatalogCache | None = None,
        fetcher: Callable[[str, str | None], tuple[dict[str, Any] | None, str | None, bool]] | None = None,
        app_version: str | None = None,
        seed_entries: list[CatalogEntry] | None = None,
    ):
        self.remote_url = remote_url
        self.cache = cache or FileCatalogCache()
        self.fetcher = fetcher or self._fetch_remote_catalog
        self.app_version = app_version
        self.seed_entries = list(seed_entries or [])
        self._entries: dict[str, CatalogEntry] = {entry.id: entry for entry in self.seed_entries}
        self._loaded = bool(self._entries)

    def _fetch_remote_catalog(
        self,
        url: str,
        etag: str | None,
    ) -> tuple[dict[str, Any] | None, str | None, bool]:
        headers = {"Accept": "application/json"}
        if etag:
            headers["If-None-Match"] = etag
        response = httpx.get(url, headers=headers, timeout=10.0, follow_redirects=True)
        if response.status_code == 304:
            return None, etag, True
        response.raise_for_status()
        payload = response.json()
        return payload, response.headers.get("etag"), False

    def refresh(self, *, force: bool = False) -> None:
        cache_data = self.cache.load()
        cached_payload = cache_data.get("payload") if isinstance(cache_data, dict) else None
        cached_etag = cache_data.get("etag") if isinstance(cache_data, dict) else None

        if self.remote_url is None:
            if isinstance(cached_payload, dict):
                self._entries = self._parse_catalog_payload(cached_payload)
                self._loaded = True
            return

        if self._loaded and not force:
            return

        try:
            payload, next_etag, not_modified = self.fetcher(self.remote_url, cached_etag)
        except Exception:
            if isinstance(cached_payload, dict):
                self._entries = self._parse_catalog_payload(cached_payload)
                self._loaded = True
                return
            raise

        if not_modified and isinstance(cached_payload, dict):
            self._entries = self._parse_catalog_payload(cached_payload)
            self._loaded = True
            return

        if not isinstance(payload, dict):
            raise ValueError("catalog fetch did not return a JSON object")
        self.cache.save(etag=next_etag, payload=payload)
        self._entries = self._parse_catalog_payload(payload)
        self._loaded = True

    def _parse_catalog_payload(self, payload: dict[str, Any]) -> dict[str, CatalogEntry]:
        entries_raw = payload.get("entries", [])
        entries: dict[str, CatalogEntry] = {}
        for item in entries_raw:
            if not isinstance(item, dict):
                continue
            entry = CatalogEntry.from_dict(item)
            if self.app_version and entry.min_app_version:
                if _version_tuple(entry.min_app_version) > _version_tuple(self.app_version):
                    continue
            entries[entry.id] = entry
        return entries

    def list_catalog(
        self,
        *,
        query: str | None = None,
        tags: list[str] | tuple[str, ...] | None = None,
        runtime: str | None = None,
    ) -> list[CatalogEntry]:
        self.refresh()
        values = list(self._entries.values())
        query_text = str(query or "").strip().lower()
        tag_filter = {str(tag).strip().lower() for tag in tags or () if str(tag).strip()}
        runtime_filter = str(runtime or "").strip().lower()

        filtered: list[CatalogEntry] = []
        for entry in values:
            if query_text:
                haystack = " ".join(
                    [
                        entry.name,
                        entry.publisher,
                        entry.description,
                        " ".join(entry.tags),
                    ]
                ).lower()
                if query_text not in haystack:
                    continue
            if tag_filter and not tag_filter.issubset({tag.lower() for tag in entry.tags}):
                continue
            if runtime_filter:
                if runtime_filter not in {profile.runtime for profile in entry.install_profiles}:
                    continue
            filtered.append(entry)
        return sorted(filtered, key=lambda item: (item.name.casefold(), item.id.casefold()))

    def get_entry(self, entry_id: str) -> CatalogEntry | None:
        self.refresh()
        return self._entries.get(str(entry_id).strip())


class ImportService:
    def __init__(self, *, github_client: GitHubRepositoryClient | None = None):
        self.github_client = github_client or GitHubRepositoryClient()

    def import_claude_config(self, *, json_text: str) -> MCPImportDraft:
        try:
            payload = json.loads(str(json_text or ""))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid Claude MCP JSON: {exc}") from exc
        entries = self._draft_entries_from_payload(payload, source_kind="claude", source_label="Claude JSON")
        if not entries:
            raise ValueError("Claude MCP JSON did not contain any importable servers")
        return MCPImportDraft.create(
            source_kind="claude",
            source_label="Claude JSON",
            entries=entries,
        )

    def import_github_repo(self, *, url: str) -> MCPImportDraft:
        owner, repo, branch = self.github_client.parse_repo_url(url)
        resolved_branch = branch or self.github_client.get_default_branch(owner, repo)

        warnings: list[str] = []
        for candidate in GITHUB_CANDIDATE_PATHS:
            text = self.github_client.fetch_file(owner, repo, resolved_branch, candidate)
            if text is None:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                warnings.append(f"ignored invalid JSON in {candidate}")
                continue
            entries = self._draft_entries_from_payload(payload, source_kind="github", source_label=url)
            if entries:
                return MCPImportDraft.create(
                    source_kind="github",
                    source_label=url,
                    warnings=warnings,
                    entries=entries,
                )
            warnings.append(f"{candidate} did not contain a supported MCP configuration shape")

        fallback_entry = self._manual_draft_entry(
            source_kind="github",
            display_name=repo,
            runtime="remote",
            transport="streamable_http",
            config={"source_url": url},
            warnings=tuple(warnings + ["No structured MCP config found in the repository; falling back to manual draft."]),
        )
        return MCPImportDraft.create(
            source_kind="github",
            source_label=url,
            warnings=fallback_entry.warnings,
            entries=[fallback_entry],
        )

    def create_manual_draft(
        self,
        *,
        runtime: str,
        transport: str,
        name: str,
        config: dict[str, Any] | None = None,
    ) -> MCPImportDraft:
        entry = self._manual_draft_entry(
            source_kind="manual",
            display_name=name,
            runtime=runtime,
            transport=transport,
            config=config or {},
            warnings=(),
        )
        return MCPImportDraft.create(
            source_kind="manual",
            source_label=name,
            entries=[entry],
        )

    def _manual_draft_entry(
        self,
        *,
        source_kind: str,
        display_name: str,
        runtime: str,
        transport: str,
        config: dict[str, Any],
        warnings: tuple[str, ...],
    ) -> DraftEntry:
        normalized_runtime = _ensure_runtime(runtime)
        normalized_transport = _ensure_transport(transport, runtime=normalized_runtime)
        sanitized_config, required_secrets = _sanitize_sensitive_mappings(config)
        profile = InstallProfile(
            id=f"profile_{slugify(display_name)}_{normalized_transport}",
            label=f"{display_name} ({normalized_transport})",
            runtime=normalized_runtime,
            transport=normalized_transport,
            platforms=(),
            fields=_default_fields_for_profile(normalized_runtime, normalized_transport, list(required_secrets)),
            required_secrets=required_secrets,
            default_values=_copy_jsonish(sanitized_config),
        )
        required_fields = tuple(
            field.key for field in profile.fields if field.required and not field.secret
        )
        return DraftEntry(
            entry_id=f"entry_{slugify(display_name)}_{slugify(source_kind)}_{slugify(normalized_transport)}",
            source_kind=source_kind,
            display_name=display_name,
            profile_candidates=(profile,),
            prefilled_config=_copy_jsonish(sanitized_config),
            required_fields=required_fields,
            required_secrets=profile.required_secrets,
            warnings=warnings,
        )

    def _draft_entries_from_payload(
        self,
        payload: dict[str, Any],
        *,
        source_kind: str,
        source_label: str,
    ) -> list[DraftEntry]:
        del source_label
        if not isinstance(payload, dict):
            return []

        for key in ("mcpServers", "servers"):
            servers = payload.get(key)
            if isinstance(servers, dict):
                entries = []
                for name, config in servers.items():
                    if not isinstance(config, dict):
                        continue
                    entries.append(
                        self._draft_entry_from_server_config(
                            display_name=str(name),
                            config=config,
                            source_kind=source_kind,
                        )
                    )
                if entries:
                    return entries

        install_profiles = payload.get("install_profiles", payload.get("installProfiles"))
        if isinstance(install_profiles, list):
            name = str(payload.get("name", "Imported MCP")).strip() or "Imported MCP"
            entry = self._draft_entry_from_install_profiles(
                display_name=name,
                payload=payload,
                source_kind=source_kind,
            )
            return [entry]

        if "command" in payload or "url" in payload:
            name = str(payload.get("name", "Imported MCP")).strip() or "Imported MCP"
            return [
                self._draft_entry_from_server_config(
                    display_name=name,
                    config=payload,
                    source_kind=source_kind,
                )
            ]
        return []

    def _draft_entry_from_install_profiles(
        self,
        *,
        display_name: str,
        payload: dict[str, Any],
        source_kind: str,
    ) -> DraftEntry:
        profiles_raw = payload.get("install_profiles", payload.get("installProfiles", []))
        profiles: list[InstallProfile] = []
        aggregated_required_fields: list[str] = []
        aggregated_required_secrets: list[str] = []
        warnings = [str(item) for item in payload.get("warnings", []) if str(item).strip()]

        for raw_profile in profiles_raw:
            if not isinstance(raw_profile, dict):
                continue
            profile = InstallProfile.from_dict(raw_profile)
            runtime = _ensure_runtime(profile.runtime)
            transport = _ensure_transport(profile.transport, runtime=runtime)
            sanitized_defaults, inferred_secrets = _sanitize_sensitive_mappings(profile.default_values)
            required_secrets = tuple(
                secret for secret in (*profile.required_secrets, *inferred_secrets) if secret
            )
            fields = profile.fields or _default_fields_for_profile(runtime, transport, list(required_secrets))
            rebuilt = InstallProfile(
                id=profile.id or f"profile_{slugify(display_name)}_{slugify(transport)}",
                label=profile.label or display_name,
                runtime=runtime,
                transport=transport,
                platforms=profile.platforms,
                fields=fields,
                required_secrets=required_secrets,
                default_values=sanitized_defaults,
            )
            profiles.append(rebuilt)
            for field in rebuilt.fields:
                if field.required and not field.secret and field.key not in aggregated_required_fields:
                    aggregated_required_fields.append(field.key)
            for secret in rebuilt.required_secrets:
                if secret not in aggregated_required_secrets:
                    aggregated_required_secrets.append(secret)

        return DraftEntry(
            entry_id=f"entry_{slugify(display_name)}_{slugify(source_kind)}",
            source_kind=source_kind,
            display_name=display_name,
            profile_candidates=tuple(profiles),
            prefilled_config={},
            required_fields=tuple(aggregated_required_fields),
            required_secrets=tuple(aggregated_required_secrets),
            warnings=tuple(warnings),
        )

    def _draft_entry_from_server_config(
        self,
        *,
        display_name: str,
        config: dict[str, Any],
        source_kind: str,
    ) -> DraftEntry:
        runtime = "local" if "command" in config else "remote"
        transport = config.get("transport")
        normalized_runtime = _ensure_runtime(runtime)
        normalized_transport = _ensure_transport(str(transport or ""), runtime=normalized_runtime)

        base_config: dict[str, Any] = {}
        if normalized_runtime == "local":
            base_config["command"] = str(config.get("command", "")).strip()
            if "args" in config:
                base_config["args"] = _coerce_string_list(config.get("args"))
            if "cwd" in config and str(config.get("cwd", "")).strip():
                base_config["cwd"] = str(config.get("cwd", "")).strip()
            if "env" in config:
                base_config["env"] = _coerce_string_dict(config.get("env"))
        else:
            base_config["url"] = str(config.get("url", "")).strip()
            base_config["transport"] = normalized_transport
            if "headers" in config:
                base_config["headers"] = _coerce_string_dict(config.get("headers"))
            if "auth" in config:
                base_config["auth"] = _coerce_string_dict(config.get("auth"))

        sanitized_config, required_secrets = _sanitize_sensitive_mappings(base_config)
        profile = InstallProfile(
            id=f"profile_{slugify(display_name)}_{slugify(normalized_transport)}",
            label=f"{display_name} ({normalized_transport})",
            runtime=normalized_runtime,
            transport=normalized_transport,
            platforms=(),
            fields=_default_fields_for_profile(normalized_runtime, normalized_transport, list(required_secrets)),
            required_secrets=required_secrets,
            default_values=_copy_jsonish(sanitized_config),
        )
        required_fields = tuple(
            field.key for field in profile.fields if field.required and not field.secret
        )
        return DraftEntry(
            entry_id=f"entry_{slugify(display_name)}_{slugify(source_kind)}_{slugify(normalized_transport)}",
            source_kind=source_kind,
            display_name=display_name,
            profile_candidates=(profile,),
            prefilled_config=_copy_jsonish(sanitized_config),
            required_fields=required_fields,
            required_secrets=profile.required_secrets,
            warnings=(),
        )


class NamespacedToolkit(Toolkit):
    def __init__(self, *, namespace: str, inner: Toolkit):
        super().__init__()
        self.namespace = namespace
        self.inner = inner
        self.refresh_tools()

    def refresh_tools(self) -> None:
        self.tools.clear()
        for tool_obj in self.inner.tools.values():
            prefixed_name = f"{self.namespace}{tool_obj.name}"
            self.tools[prefixed_name] = Tool(
                name=prefixed_name,
                description=tool_obj.description,
                func=lambda **kwargs: {"result": kwargs},
                parameters=[
                    ToolParameter(
                        name=parameter.name,
                        description=parameter.description,
                        type_=parameter.type_,
                        required=parameter.required,
                        pattern=parameter.pattern,
                        items=_copy_jsonish(parameter.items),
                    )
                    for parameter in tool_obj.parameters
                ],
                observe=tool_obj.observe,
                requires_confirmation=tool_obj.requires_confirmation,
                history_arguments_optimizer=tool_obj.history_arguments_optimizer,
                history_result_optimizer=tool_obj.history_result_optimizer,
            )

    def execute(self, function_name: str, arguments: dict[str, Any] | str | None) -> dict[str, Any]:
        if not function_name.startswith(self.namespace):
            return {"error": f"tool not found: {function_name}", "tool": function_name}
        inner_name = function_name[len(self.namespace):]
        return self.inner.execute(inner_name, arguments)


class MCPTestService:
    def __init__(
        self,
        *,
        secret_store: InMemorySecretStore,
        toolkit_factory: Callable[[InstalledServer, dict[str, Any]], Toolkit] | None = None,
    ):
        self.secret_store = secret_store
        self.toolkit_factory = toolkit_factory or _default_toolkit_factory

    def test(self, instance: InstalledServer) -> ConnectionTestResult:
        if not self._all_required_secrets_present(instance):
            return ConnectionTestResult(
                status="failed",
                phase="validate",
                summary="Missing required secrets.",
                tool_count=0,
                tools=(),
                warnings=(),
                errors=(ConnectionIssue(code="MISSING_SECRET", message="One or more required secrets are not configured."),),
            )

        materialized = _materialize_instance_config(instance, self.secret_store)
        validation = self._validate_config(instance, materialized)
        if validation is not None:
            return validation

        toolkit = self.toolkit_factory(instance, materialized)
        try:
            if hasattr(toolkit, "connect"):
                getattr(toolkit, "connect")()
        except httpx.TimeoutException as exc:
            return ConnectionTestResult(
                status="failed",
                phase="connect",
                summary="Timed out while connecting to the MCP server.",
                tool_count=0,
                tools=(),
                warnings=(),
                errors=(ConnectionIssue(code="CONNECT_TIMEOUT", message=str(exc)),),
            )
        except httpx.HTTPStatusError as exc:
            code = "AUTH_FAILED" if exc.response.status_code in {401, 403} else "HANDSHAKE_FAILED"
            return ConnectionTestResult(
                status="failed",
                phase="connect",
                summary="Failed to connect to the MCP server.",
                tool_count=0,
                tools=(),
                warnings=(),
                errors=(ConnectionIssue(code=code, message=str(exc)),),
            )
        except ValueError as exc:
            return ConnectionTestResult(
                status="failed",
                phase="validate",
                summary="Invalid MCP configuration.",
                tool_count=0,
                tools=(),
                warnings=(),
                errors=(ConnectionIssue(code="INVALID_CONFIG", message=str(exc)),),
            )
        except RuntimeError as exc:
            return ConnectionTestResult(
                status="failed",
                phase="connect",
                summary="MCP handshake failed.",
                tool_count=0,
                tools=(),
                warnings=(),
                errors=(ConnectionIssue(code="HANDSHAKE_FAILED", message=str(exc)),),
            )
        try:
            tools = self._discover_tools(toolkit)
        except Exception as exc:
            if hasattr(toolkit, "disconnect"):
                try:
                    getattr(toolkit, "disconnect")()
                except Exception:
                    pass
            return ConnectionTestResult(
                status="failed",
                phase="list_tools",
                summary="Connected, but failed while loading tool definitions.",
                tool_count=0,
                tools=(),
                warnings=(),
                errors=(ConnectionIssue(code="LIST_TOOLS_FAILED", message=str(exc)),),
            )
        if hasattr(toolkit, "disconnect"):
            try:
                getattr(toolkit, "disconnect")()
            except Exception:
                pass

        return ConnectionTestResult(
            status="success",
            phase="list_tools",
            summary=f"Connected successfully and discovered {len(tools)} tools.",
            tool_count=len(tools),
            tools=tools,
            warnings=(),
            errors=(),
        )

    def _all_required_secrets_present(self, instance: InstalledServer) -> bool:
        return all(self.secret_store.has_secret(instance.instance_id, target) for target in instance.required_secrets)

    def _validate_config(
        self,
        instance: InstalledServer,
        materialized: dict[str, Any],
    ) -> ConnectionTestResult | None:
        if instance.runtime == "local":
            command = str(materialized.get("command", "")).strip()
            if not command:
                return ConnectionTestResult(
                    status="failed",
                    phase="validate",
                    summary="Local MCP config is missing a command.",
                    tool_count=0,
                    tools=(),
                    warnings=(),
                    errors=(ConnectionIssue(code="INVALID_CONFIG", message="command is required"),),
                )
            cwd = str(materialized.get("cwd", "")).strip()
            if cwd and not Path(cwd).is_dir():
                return ConnectionTestResult(
                    status="failed",
                    phase="prepare",
                    summary="The configured working directory does not exist.",
                    tool_count=0,
                    tools=(),
                    warnings=(),
                    errors=(ConnectionIssue(code="BAD_WORKING_DIR", message=f"cwd does not exist: {cwd}"),),
                )
            if "/" in command or "\\" in command:
                candidate = Path(command)
                if not candidate.is_file():
                    return ConnectionTestResult(
                        status="failed",
                        phase="prepare",
                        summary="The configured MCP command could not be found.",
                        tool_count=0,
                        tools=(),
                        warnings=(),
                        errors=(ConnectionIssue(code="BINARY_NOT_FOUND", message=f"command not found: {command}"),),
                    )
            elif shutil.which(command) is None:
                return ConnectionTestResult(
                    status="failed",
                    phase="prepare",
                    summary="The configured MCP command could not be found in PATH.",
                    tool_count=0,
                    tools=(),
                    warnings=(),
                    errors=(ConnectionIssue(code="BINARY_NOT_FOUND", message=f"command not found on PATH: {command}"),),
                )
            return None

        url = str(materialized.get("url", "")).strip()
        if not url:
            return ConnectionTestResult(
                status="failed",
                phase="validate",
                summary="Remote MCP config is missing a URL.",
                tool_count=0,
                tools=(),
                warnings=(),
                errors=(ConnectionIssue(code="INVALID_CONFIG", message="url is required"),),
            )
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return ConnectionTestResult(
                status="failed",
                phase="validate",
                summary="Remote MCP URL must use http or https.",
                tool_count=0,
                tools=(),
                warnings=(),
                errors=(ConnectionIssue(code="INVALID_CONFIG", message=f"unsupported MCP URL scheme: {parsed.scheme}"),),
            )
        return None

    def _discover_tools(self, toolkit: Toolkit) -> tuple[ToolPreview, ...]:
        if hasattr(toolkit, "list_tools") and callable(getattr(toolkit, "list_tools")):
            raw = getattr(toolkit, "list_tools")()
            previews = []
            for item in raw:
                if isinstance(item, Tool):
                    previews.append(ToolPreview(name=item.name, description=item.description))
                elif isinstance(item, dict):
                    previews.append(ToolPreview.from_dict(item))
                else:
                    previews.append(ToolPreview(name=str(getattr(item, "name", item)), description=str(getattr(item, "description", ""))))
            return tuple(sorted(previews, key=lambda item: item.name))
        return _tool_previews_from_runtime_tools(toolkit)


@dataclass
class ManagedRuntimeHandle:
    instance_id: str
    toolkit: Toolkit
    namespaced_toolkit: NamespacedToolkit
    cached_tools: tuple[ToolPreview, ...]


class MCPRuntimeManager:
    def __init__(
        self,
        *,
        secret_store: InMemorySecretStore,
        toolkit_factory: Callable[[InstalledServer, dict[str, Any]], Toolkit] | None = None,
    ):
        self.secret_store = secret_store
        self.toolkit_factory = toolkit_factory or _default_toolkit_factory
        self._handles: dict[str, ManagedRuntimeHandle] = {}
        self._attached_instances: dict[str, list[str]] = {}

    def ensure_enabled(self, instance: InstalledServer) -> ManagedRuntimeHandle:
        handle = self._handles.get(instance.instance_id)
        if handle is not None:
            return handle

        materialized = _materialize_instance_config(instance, self.secret_store)
        toolkit = self.toolkit_factory(instance, materialized)
        if hasattr(toolkit, "connect"):
            getattr(toolkit, "connect")()
        cached_tools = _tool_previews_from_runtime_tools(toolkit)
        namespace = f"mcp__{instance.instance_slug}__"
        namespaced_toolkit = NamespacedToolkit(namespace=namespace, inner=toolkit)
        handle = ManagedRuntimeHandle(
            instance_id=instance.instance_id,
            toolkit=toolkit,
            namespaced_toolkit=namespaced_toolkit,
            cached_tools=cached_tools,
        )
        self._handles[instance.instance_id] = handle
        return handle

    def disable(self, instance_id: str) -> None:
        handle = self._handles.pop(instance_id, None)
        if handle is not None and hasattr(handle.toolkit, "disconnect"):
            getattr(handle.toolkit, "disconnect")()
        for chat_id, attached in list(self._attached_instances.items()):
            next_attached = [value for value in attached if value != instance_id]
            if next_attached:
                self._attached_instances[chat_id] = next_attached
            else:
                self._attached_instances.pop(chat_id, None)

    def attach(self, chat_id: str, instances: list[InstalledServer]) -> list[str]:
        attached: list[str] = []
        for instance in instances:
            handle = self.ensure_enabled(instance)
            handle.namespaced_toolkit.refresh_tools()
            attached.append(instance.instance_id)
        self._attached_instances[str(chat_id)] = attached
        return attached

    def toolkits_for_chat(self, chat_id: str) -> list[Toolkit]:
        toolkits: list[Toolkit] = []
        for instance_id in self._attached_instances.get(str(chat_id), []):
            handle = self._handles.get(instance_id)
            if handle is not None:
                toolkits.append(handle.namespaced_toolkit)
        return toolkits


class PupuMCPService:
    def __init__(
        self,
        *,
        catalog_service: CatalogService | None = None,
        import_service: ImportService | None = None,
        installed_server_store: FileInstalledServerStore | None = None,
        secret_store: InMemorySecretStore | None = None,
        test_service: MCPTestService | None = None,
        runtime_manager: MCPRuntimeManager | None = None,
    ):
        self.catalog_service = catalog_service or CatalogService()
        self.import_service = import_service or ImportService()
        self.installed_server_store = installed_server_store or FileInstalledServerStore()
        self.secret_store = secret_store or InMemorySecretStore()
        self.test_service = test_service or MCPTestService(secret_store=self.secret_store)
        self.runtime_manager = runtime_manager or MCPRuntimeManager(secret_store=self.secret_store)
        self._draft_entries: dict[str, DraftEntry] = {}

    def list_catalog(
        self,
        *,
        query: str | None = None,
        tags: list[str] | tuple[str, ...] | None = None,
        runtime: str | None = None,
    ) -> dict[str, Any]:
        entries = self.catalog_service.list_catalog(query=query, tags=tags, runtime=runtime)
        return {"entries": [entry.to_dict() for entry in entries]}

    def import_claude_config(self, *, json_text: str) -> dict[str, Any]:
        draft = self.import_service.import_claude_config(json_text=json_text)
        self._register_draft(draft)
        return draft.to_dict()

    def import_github_repo(self, *, url: str) -> dict[str, Any]:
        draft = self.import_service.import_github_repo(url=url)
        self._register_draft(draft)
        return draft.to_dict()

    def create_manual_draft(
        self,
        *,
        runtime: str,
        transport: str,
        name: str,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        draft = self.import_service.create_manual_draft(
            runtime=runtime,
            transport=transport,
            name=name,
            config=config,
        )
        self._register_draft(draft)
        return draft.to_dict()

    def save_installed_server(
        self,
        *,
        draft_entry_id: str,
        profile_id: str,
        display_name: str | None = None,
        config: dict[str, Any] | None = None,
        secrets: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        entry = self._resolve_entry_source(draft_entry_id)
        profile = next(
            (candidate for candidate in entry.profile_candidates if candidate.id == profile_id),
            None,
        )
        if profile is None:
            raise KeyError(f"unknown profile_id: {profile_id}")

        runtime = _ensure_runtime(profile.runtime)
        transport = _ensure_transport(profile.transport, runtime=runtime)
        merged = _copy_jsonish(profile.default_values)
        merged.update(_copy_jsonish(entry.prefilled_config))
        merged.update(_copy_jsonish(config or {}))

        for field in profile.fields:
            if field.secret:
                merged.pop(field.key, None)

        for field in profile.fields:
            if not field.required or field.secret:
                continue
            if field.key not in merged or merged.get(field.key) in ("", None):
                raise ValueError(f"missing required config field: {field.key}")

        catalog_entry_id = entry.catalog_entry_id
        if catalog_entry_id is None:
            catalog_entry = self.catalog_service.get_entry(entry.entry_id)
            if catalog_entry is not None:
                catalog_entry_id = catalog_entry.id

        status = "ready_for_review"
        if catalog_entry_id is not None:
            catalog_entry = self.catalog_service.get_entry(catalog_entry_id)
            if catalog_entry is not None and catalog_entry.revoked:
                status = "revoked"

        instance = InstalledServer.create(
            display_name=display_name or entry.display_name,
            source_kind=entry.source_kind,
            runtime=runtime,
            transport=transport,
            normalized_config=merged,
            required_secrets=list(profile.required_secrets),
            catalog_entry_id=catalog_entry_id,
            status=status,
        )

        for target, value in (secrets or {}).items():
            if value:
                self.secret_store.set_secret(instance.instance_id, target, value)

        if status != "revoked" and not self._all_required_secrets_present(instance):
            instance = instance.with_updates(status="needs_secret", updated_at=utc_now_iso())

        self.installed_server_store.save_instance(instance)
        return self._instance_payload(instance)

    def list_installed_servers(self) -> dict[str, Any]:
        instances = [self._synchronize_catalog_state(item) for item in self.installed_server_store.list_instances()]
        return {
            "instances": [self._instance_payload(instance) for instance in instances],
        }

    def get_installed_server_detail(self, *, instance_id: str) -> dict[str, Any]:
        instance = self._require_instance(instance_id)
        return self._instance_payload(self._synchronize_catalog_state(instance))

    def test_installed_server(self, *, instance_id: str) -> dict[str, Any]:
        instance = self._synchronize_catalog_state(self._require_instance(instance_id))
        if instance.status == "revoked" and not instance.enabled:
            result = ConnectionTestResult(
                status="failed",
                phase="validate",
                summary="This catalog entry has been revoked and can no longer be enabled.",
                tool_count=0,
                tools=(),
                warnings=(),
                errors=(ConnectionIssue(code="REVOKED_ENTRY", message="catalog entry is revoked"),),
            )
            updated = instance.with_updates(last_test_result=result, updated_at=utc_now_iso())
            self.installed_server_store.save_instance(updated)
            return result.to_dict()

        updated = instance.with_updates(status="testing", updated_at=utc_now_iso())
        self.installed_server_store.save_instance(updated)
        result = self.test_service.test(updated)

        next_status = "test_passed" if result.status == "success" else "test_failed"
        if not self._all_required_secrets_present(updated):
            next_status = "needs_secret"

        persisted = updated.with_updates(
            status=next_status,
            tool_count=result.tool_count,
            cached_tools=result.tools,
            last_test_result=result,
            updated_at=utc_now_iso(),
        )
        self.installed_server_store.save_instance(persisted)
        return result.to_dict()

    def enable_installed_server(self, *, instance_id: str) -> dict[str, Any]:
        instance = self._synchronize_catalog_state(self._require_instance(instance_id))
        if instance.status == "revoked" and not instance.enabled:
            self.installed_server_store.save_instance(instance)
            return self._instance_payload(instance)
        if not self._all_required_secrets_present(instance):
            updated = instance.with_updates(status="needs_secret", enabled=False, updated_at=utc_now_iso())
            self.installed_server_store.save_instance(updated)
            return self._instance_payload(updated)
        if instance.last_test_result is None or instance.last_test_result.status != "success":
            raise ValueError("instance must pass testInstalledServer before enableInstalledServer")

        handle = self.runtime_manager.ensure_enabled(instance)
        updated = instance.with_updates(
            status="enabled",
            enabled=True,
            tool_count=len(handle.cached_tools),
            cached_tools=handle.cached_tools,
            updated_at=utc_now_iso(),
        )
        self.installed_server_store.save_instance(updated)
        return self._instance_payload(updated)

    def disable_installed_server(self, *, instance_id: str) -> dict[str, Any]:
        instance = self._require_instance(instance_id)
        self.runtime_manager.disable(instance_id)
        next_status = "revoked" if instance.status == "revoked" else "disabled"
        updated = instance.with_updates(status=next_status, enabled=False, updated_at=utc_now_iso())
        self.installed_server_store.save_instance(updated)
        return self._instance_payload(updated)

    def attach_servers_to_chat(self, *, chat_id: str, instance_ids: list[str]) -> dict[str, Any]:
        instances: list[InstalledServer] = []
        for instance_id in instance_ids:
            instance = self._synchronize_catalog_state(self._require_instance(instance_id))
            if not instance.enabled or instance.status == "revoked":
                continue
            instances.append(instance)
        attached = self.runtime_manager.attach(str(chat_id), instances)
        return {"attached_instance_ids": attached}

    def get_chat_toolkits(self, chat_id: str) -> list[Toolkit]:
        return self.runtime_manager.toolkits_for_chat(str(chat_id))

    def _register_draft(self, draft: MCPImportDraft) -> None:
        for entry in draft.entries:
            self._draft_entries[entry.entry_id] = entry

    def _resolve_entry_source(self, draft_entry_id: str) -> DraftEntry:
        entry = self._draft_entries.get(str(draft_entry_id).strip())
        if entry is not None:
            return entry

        catalog_entry = self.catalog_service.get_entry(str(draft_entry_id).strip())
        if catalog_entry is None:
            raise KeyError(f"unknown draft_entry_id: {draft_entry_id}")

        required_fields = []
        required_secrets = []
        for profile in catalog_entry.install_profiles:
            for field in profile.fields:
                if field.required and not field.secret and field.key not in required_fields:
                    required_fields.append(field.key)
            for secret in profile.required_secrets:
                if secret not in required_secrets:
                    required_secrets.append(secret)

        return DraftEntry(
            entry_id=catalog_entry.id,
            source_kind="official",
            display_name=catalog_entry.name,
            profile_candidates=catalog_entry.install_profiles,
            prefilled_config={},
            required_fields=tuple(required_fields),
            required_secrets=tuple(required_secrets),
            warnings=(),
            catalog_entry_id=catalog_entry.id,
        )

    def _require_instance(self, instance_id: str) -> InstalledServer:
        instance = self.installed_server_store.get_instance(instance_id)
        if instance is None:
            raise KeyError(f"unknown instance_id: {instance_id}")
        return instance

    def _all_required_secrets_present(self, instance: InstalledServer) -> bool:
        return all(self.secret_store.has_secret(instance.instance_id, target) for target in instance.required_secrets)

    def _instance_payload(self, instance: InstalledServer) -> dict[str, Any]:
        configured = [
            target for target in instance.required_secrets
            if self.secret_store.has_secret(instance.instance_id, target)
        ]
        return instance.to_public_dict(configured_secrets=configured)

    def _synchronize_catalog_state(self, instance: InstalledServer) -> InstalledServer:
        if instance.catalog_entry_id is None:
            return instance
        catalog_entry = self.catalog_service.get_entry(instance.catalog_entry_id)
        if catalog_entry is None or not catalog_entry.revoked or instance.enabled:
            return instance
        updated = instance.with_updates(status="revoked", enabled=False, updated_at=utc_now_iso())
        self.installed_server_store.save_instance(updated)
        return updated

    # UI-oriented camelCase aliases.
    def listCatalog(self, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        filters = filters or {}
        return self.list_catalog(
            query=filters.get("query"),
            tags=filters.get("tags"),
            runtime=filters.get("runtime"),
        )

    def importClaudeConfig(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.import_claude_config(json_text=str(payload.get("json_text", "")))

    def importGitHubRepo(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.import_github_repo(url=str(payload.get("url", "")))

    def createManualDraft(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.create_manual_draft(
            runtime=str(payload.get("runtime", "")),
            transport=str(payload.get("transport", "")),
            name=str(payload.get("name", "")),
            config=payload.get("config"),
        )

    def saveInstalledServer(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.save_installed_server(
            draft_entry_id=str(payload.get("draft_entry_id", "")),
            profile_id=str(payload.get("profile_id", "")),
            display_name=payload.get("display_name"),
            config=payload.get("config"),
            secrets=payload.get("secrets"),
        )

    def listInstalledServers(self) -> dict[str, Any]:
        return self.list_installed_servers()

    def getInstalledServerDetail(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.get_installed_server_detail(instance_id=str(payload.get("instance_id", "")))

    def testInstalledServer(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.test_installed_server(instance_id=str(payload.get("instance_id", "")))

    def enableInstalledServer(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.enable_installed_server(instance_id=str(payload.get("instance_id", "")))

    def disableInstalledServer(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.disable_installed_server(instance_id=str(payload.get("instance_id", "")))

    def attachServersToChat(self, payload: dict[str, Any]) -> dict[str, Any]:
        instance_ids = [str(item) for item in payload.get("instance_ids", [])]
        return self.attach_servers_to_chat(chat_id=str(payload.get("chat_id", "")), instance_ids=instance_ids)


__all__ = [
    "CatalogService",
    "GitHubRepositoryClient",
    "ImportService",
    "MCPRuntimeManager",
    "MCPTestService",
    "NamespacedToolkit",
    "PupuMCPService",
]

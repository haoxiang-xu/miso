from __future__ import annotations

import hashlib
import importlib
import json
import math

import pytest

from unchain.journal.models import (
    ArtifactRef,
    AttemptRef,
    EventCursor,
    GenerationRef,
    ResourceRef,
)
from unchain.journal.resource_limits import BoundaryResourceLimitError
from unchain.tools.handler_registry import (
    DurableToolHandlerBinding,
    DurableToolHandlerRegistry,
    tool_config_sha256,
)
from unchain.tools.tool import Tool


ZERO_SHA = "0" * 64
ONE_SHA = "1" * 64
TWO_SHA = "2" * 64
THREE_SHA = "3" * 64


def _secret_handle_template() -> dict:
    return {
        "type": "string",
        "x-pupu-secret": True,
        "x-pupu-secret-kind": "handle",
    }


def _secret_handle_map_template() -> dict:
    return {
        "type": "object",
        "additionalProperties": {"type": "string"},
        "x-pupu-secret": True,
        "x-pupu-secret-kind": "handle_map",
    }


def _contract():
    return importlib.import_module("unchain.context.tool_catalog")


def _digest(value) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _attempt() -> AttemptRef:
    return AttemptRef(
        generation=GenerationRef(
            execution_id="execution-catalog-1",
            generation_id="generation-catalog-1",
        ),
        attempt_id="attempt-catalog-1",
    )


def _binding(handler_id: str, revision: int = 1) -> DurableToolHandlerBinding:
    return DurableToolHandlerBinding(
        handler_id=handler_id,
        revision=revision,
        config_sha256=ZERO_SHA,
        kind="stable",
    )


def _entry(*, module=None, tool_descriptor_sha256: str = THREE_SHA, **values):
    module = module or _contract()
    return module.ToolCatalogEntry(
        tool_descriptor_sha256=tool_descriptor_sha256,
        **values,
    )


def _schemas() -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": "search",
                "description": "Search indexed documents",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "name": "lookup",
            "description": "Read one durable object",
            "input_schema": {
                "type": "object",
                "properties": {"ref": {"type": "string"}},
                "required": ["ref"],
            },
        },
    ]


def _entries(schemas: list[dict] | None = None):
    module = _contract()
    schemas = schemas or _schemas()
    return [
        _entry(
            module=module,
            tool_name="search",
            semantic_schema_sha256=_digest(schemas[0]),
            handler_binding=_binding("host.search"),
            route_kind="normal",
        ),
        _entry(
            module=module,
            tool_name="lookup",
            semantic_schema_sha256=_digest(schemas[1]),
            handler_binding=_binding("host.lookup", revision=4),
            route_kind="normal",
        ),
    ]


def _envelope(**overrides):
    module = _contract()
    schemas = overrides.pop("semantic_schemas", _schemas())
    entries = overrides.pop("entries", None)
    values = {
        "attempt": _attempt(),
        "iteration": 7,
        "provider": "openai",
        "model": "gpt-frontier",
        "semantic_schemas": schemas,
        "entries": _entries(schemas) if entries is None else entries,
        "required_betas_sha256": ZERO_SHA,
        "prompt_sha256": ONE_SHA,
        "exposure_plan_sha256": TWO_SHA,
    }
    values.update(overrides)
    return module.ToolCatalogEnvelope(**values)


def _artifact_for(envelope) -> ArtifactRef:
    content = envelope.canonical_bytes()
    return ArtifactRef(
        ref=ResourceRef(
            kind="artifact",
            resource_id="tool-catalog-artifact-1",
            revision=1,
        ),
        media_type="application/json",
        byte_length=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        preview="",
    )


def _handler(arguments):
    return arguments


def test_catalog_entry_is_derived_from_verified_frozen_descriptor_authority() -> None:
    module = _contract()
    registry = DurableToolHandlerRegistry()
    tool = Tool(
        name="search",
        description="Search durable documents",
        func=_handler,
    )
    resolution = registry.register(
        DurableToolHandlerBinding(
            handler_id="host.search.verified",
            revision=1,
            config_sha256=tool_config_sha256(tool),
            kind="stable",
        ),
        tool=tool,
        handler=_handler,
    )

    schema, entry = module.build_tool_catalog_entry_from_resolution(
        registry=registry,
        resolution=resolution,
        provider="openai",
    )

    frozen_schema = resolution.tool_descriptor["provider_schemas"]["openai"]
    assert schema["name"] == frozen_schema["name"]
    assert schema["parameters"]["required"] == list(
        frozen_schema["parameters"]["required"]
    )
    assert entry.tool_descriptor_sha256 == resolution.tool_descriptor_sha256
    assert entry.tool_descriptor_sha256 != entry.handler_binding.config_sha256
    assert entry.semantic_schema_sha256 == _digest(schema)
    recovered = module.ToolCatalogEntry.from_dict(entry.to_dict())
    assert recovered.tool_descriptor_sha256 == entry.tool_descriptor_sha256
    assert (
        module.verify_tool_catalog_entry_resolution(
            recovered,
            registry=registry,
            resolution=resolution,
            provider="openai",
        )
        is recovered
    )

    envelope = _envelope(
        provider="openai",
        semantic_schemas=[schema],
        entries=[entry],
    )
    cold_envelope = module.ToolCatalogEnvelope.from_dict(
        json.loads(envelope.canonical_bytes())
    )
    assert (
        module.verify_tool_catalog_entry_resolution(
            cold_envelope.entries[0],
            registry=registry,
            resolution=resolution,
            provider="openai",
        )
        is cold_envelope.entries[0]
    )

    forged = _entry(
        module=module,
        tool_name=entry.tool_name,
        semantic_schema_sha256=entry.semantic_schema_sha256,
        tool_descriptor_sha256=ZERO_SHA,
        handler_binding=entry.handler_binding,
    )
    with pytest.raises(ValueError, match="descriptor digest"):
        module.verify_tool_catalog_entry_resolution(
            forged,
            registry=registry,
            resolution=resolution,
            provider="openai",
        )


def test_catalog_entry_is_strict_and_binds_schema_to_registry_identity() -> None:
    module = _contract()
    schema = _schemas()[0]
    entry = _entry(
        module=module,
        tool_name="search",
        semantic_schema_sha256=_digest(schema),
        handler_binding=_binding("host.search"),
        route_kind="normal",
    )
    expected = {
        "schema": "unchain.tool_catalog_entry.v1",
        "tool_name": "search",
        "semantic_schema_sha256": _digest(schema),
        "tool_descriptor_sha256": THREE_SHA,
        "route_kind": "normal",
        "handler_binding": _binding("host.search").to_dict(),
    }

    assert entry.to_dict() == expected
    assert module.ToolCatalogEntry.from_dict(expected) == entry
    assert entry.entry_sha256 == _digest(expected)
    for changed in (
        {**expected, "extra": True},
        {**expected, "semantic_schema_sha256": "A" * 64},
        {**expected, "tool_descriptor_sha256": "A" * 64},
        {
            key: value
            for key, value in expected.items()
            if key != "tool_descriptor_sha256"
        },
        {**expected, "route_kind": "fallback"},
    ):
        with pytest.raises((TypeError, ValueError)):
            module.ToolCatalogEntry.from_dict(changed)


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    [
        ("revision", 0),
        ("config_sha256", "A" * 64),
        ("kind", "stable"),
    ],
)
def test_catalog_entry_rejects_a_handler_binding_mutated_after_validation(
    field_name: str,
    field_value: object,
) -> None:
    module = _contract()
    binding = _binding("host.search")
    object.__setattr__(binding, field_name, field_value)

    with pytest.raises((TypeError, ValueError), match="handler binding|canonical"):
        _entry(
            module=module,
            tool_name="search",
            semantic_schema_sha256=ZERO_SHA,
            handler_binding=binding,
        )


def test_catalog_entry_detaches_its_handler_binding_from_later_mutation() -> None:
    module = _contract()
    binding = _binding("host.search")
    entry = _entry(
        module=module,
        tool_name="search",
        semantic_schema_sha256=ZERO_SHA,
        handler_binding=binding,
    )
    object.__setattr__(binding, "revision", 0)

    assert entry.handler_binding is not binding
    assert entry.handler_binding.revision == 1
    assert module.ToolCatalogEntry.from_dict(entry.to_dict()) == entry


def test_envelope_carries_ordered_schemas_manifest_entries_and_all_input_digests() -> (
    None
):
    envelope = _envelope()
    schemas = _schemas()
    raw = envelope.to_dict()

    assert raw["schema"] == "unchain.tool_catalog_envelope.v1"
    assert raw["attempt"] == _attempt().to_dict()
    assert raw["iteration"] == 7
    assert raw["provider"] == "openai"
    assert raw["model"] == "gpt-frontier"
    assert raw["semantic_schemas"] == schemas
    assert [entry["tool_name"] for entry in raw["entries"]] == ["search", "lookup"]
    assert all(entry["tool_descriptor_sha256"] == THREE_SHA for entry in raw["entries"])
    assert raw["tool_schema_manifest"] == {
        "search": _digest(schemas[0]),
        "lookup": _digest(schemas[1]),
    }
    assert raw["tool_schema_sha256"] == _digest(schemas)
    assert raw["required_betas_sha256"] == ZERO_SHA
    assert raw["prompt_sha256"] == ONE_SHA
    assert raw["exposure_plan_sha256"] == TWO_SHA
    assert raw["catalog_sha256"] == envelope.catalog_sha256
    assert len(envelope.catalog_sha256) == 64


def test_envelope_roundtrip_is_deterministic_and_detaches_mutable_inputs() -> None:
    module = _contract()
    schemas = _schemas()
    envelope = _envelope(semantic_schemas=schemas)
    original = envelope.to_dict()
    schemas[0]["function"]["description"] = "mutated after binding"

    assert envelope.to_dict() == original
    recovered = module.ToolCatalogEnvelope.from_dict(original)
    assert recovered == envelope
    assert recovered.catalog_sha256 == envelope.catalog_sha256
    assert recovered.canonical_bytes() == envelope.canonical_bytes()
    with pytest.raises(TypeError):
        envelope.semantic_schemas[0]["function"]["description"] = "internal mutation"


def test_canonical_artifact_bytes_cold_roundtrip_preserves_schema_order() -> None:
    module = _contract()
    schemas = _schemas()
    entries = _entries(schemas)
    envelope = _envelope(semantic_schemas=schemas, entries=entries)

    recovered = module.ToolCatalogEnvelope.from_dict(
        json.loads(envelope.canonical_bytes())
    )

    assert [entry.tool_name for entry in recovered.entries] == ["search", "lookup"]
    assert recovered.catalog_sha256 == envelope.catalog_sha256
    assert recovered.canonical_bytes() == envelope.canonical_bytes()


def test_envelope_deep_clones_exact_entries_at_ownership_boundary() -> None:
    schemas = _schemas()
    entries = _entries(schemas)
    envelope = _envelope(semantic_schemas=schemas, entries=entries)
    before = envelope.to_dict()

    object.__setattr__(entries[0], "tool_name", "mutated_after_binding")
    object.__setattr__(entries[0].handler_binding, "revision", 0)

    assert envelope.to_dict() == before
    assert envelope.entries[0] is not entries[0]
    assert envelope.entries[0].handler_binding is not entries[0].handler_binding


def test_snapshot_revalidates_and_detaches_an_exact_envelope() -> None:
    module = _contract()
    envelope = _envelope()
    artifact = _artifact_for(envelope)
    snapshot = module.ToolCatalogSnapshot(
        envelope=envelope,
        event_cursor=EventCursor(store_seq=42, event_id="event-tool-catalog-1"),
        artifact=artifact,
    )
    expected_catalog_sha256 = snapshot.catalog_sha256

    object.__setattr__(envelope, "catalog_sha256", ZERO_SHA)

    assert snapshot.envelope is not envelope
    assert snapshot.catalog_sha256 == expected_catalog_sha256


def test_snapshot_rejects_mutated_nested_entry_even_with_recomputed_artifact() -> None:
    module = _contract()
    envelope = _envelope()
    object.__setattr__(
        envelope.entries[0],
        "semantic_schema_sha256",
        ZERO_SHA,
    )
    forged_artifact = _artifact_for(envelope)

    with pytest.raises(ValueError, match="schema digest|catalog digest"):
        module.ToolCatalogSnapshot(
            envelope=envelope,
            event_cursor=EventCursor(
                store_seq=42,
                event_id="event-tool-catalog-1",
            ),
            artifact=forged_artifact,
        )


def test_plugin_catalog_entry_requires_a_durable_route_resolver_identity() -> None:
    module = _contract()

    with pytest.raises(ValueError, match="route resolver"):
        _entry(
            module=module,
            tool_name="dynamic_plugin",
            semantic_schema_sha256=ZERO_SHA,
            handler_binding=_binding("plugin.dynamic"),
            route_kind="plugin",
        )


@pytest.mark.parametrize(
    "bad_schema",
    [
        {"name": "search", "arguments": ("not", "json")},
        {"name": "search", "weight": math.nan},
        {"name": "search", "payload": b"not-json"},
    ],
)
def test_envelope_rejects_noncanonical_provider_schema_json(bad_schema: dict) -> None:
    with pytest.raises((TypeError, ValueError), match="strict canonical JSON"):
        _envelope(
            semantic_schemas=[bad_schema],
            entries=[
                _entry(
                    module=_contract(),
                    tool_name="search",
                    semantic_schema_sha256=ZERO_SHA,
                    handler_binding=_binding("host.search"),
                )
            ],
        )


def test_envelope_rejects_plaintext_secret_material_before_persistence() -> None:
    schema = {
        "name": "search",
        "description": "malicious schema metadata",
        "metadata": {"api_key": "sk-live-plaintext"},
    }
    with pytest.raises(ValueError, match="plaintext secret|opaque"):
        _envelope(
            semantic_schemas=[schema],
            entries=[
                _entry(
                    module=_contract(),
                    tool_name="search",
                    semantic_schema_sha256=_digest(schema),
                    handler_binding=_binding("host.search"),
                )
            ],
        )


def test_envelope_allows_declared_secret_sink_schema_without_secret_values() -> None:
    module = _contract()
    schema = {
        "type": "function",
        "function": {
            "name": "shell",
            "parameters": {
                "type": "object",
                "properties": {
                    "secret_env": _secret_handle_map_template(),
                    "api_key": _secret_handle_template(),
                },
                "required": ["secret_env"],
            },
        },
    }
    entry = _entry(
        module=module,
        tool_name="shell",
        semantic_schema_sha256=_digest(schema),
        handler_binding=_binding("host.shell"),
    )

    envelope = _envelope(
        provider="ollama",
        semantic_schemas=[schema],
        entries=[entry],
    )

    raw = envelope.to_dict()
    assert raw["semantic_schemas"] == [schema]
    assert raw["secret_schema_manifest"] == [
        {
            "path": "/semantic_schemas/0/function/parameters/properties/api_key",
            "kind": "handle",
            "revision": 1,
            "schema_sha256": _digest(_secret_handle_template()),
        },
        {
            "path": "/semantic_schemas/0/function/parameters/properties/secret_env",
            "kind": "handle_map",
            "revision": 1,
            "schema_sha256": _digest(_secret_handle_map_template()),
        },
    ]


@pytest.mark.parametrize(
    ("provider", "schema", "expected_path"),
    [
        (
            "openai",
            {
                "name": "login",
                "parameters": {
                    "type": "object",
                    "properties": {"api_key": _secret_handle_template()},
                },
            },
            "/semantic_schemas/0/parameters/properties/api_key",
        ),
        (
            "hyperspace",
            {
                "name": "login",
                "input_schema": {
                    "type": "object",
                    "properties": {"api_key": _secret_handle_template()},
                },
            },
            "/semantic_schemas/0/input_schema/properties/api_key",
        ),
    ],
)
def test_secret_manifest_uses_only_actual_provider_parameter_roots(
    provider: str,
    schema: dict,
    expected_path: str,
) -> None:
    entry = _entry(
        tool_name="login",
        semantic_schema_sha256=_digest(schema),
        handler_binding=_binding(f"host.{provider}.login"),
    )

    envelope = _envelope(
        provider=provider,
        semantic_schemas=[schema],
        entries=[entry],
    )

    assert [item["path"] for item in envelope.secret_schema_manifest] == [expected_path]


def test_secret_schema_forbids_annotations_but_normal_descriptions_remain_valid() -> (
    None
):
    module = _contract()
    safe_schema = {
        "name": "login",
        "description": "Authenticate using a Vault-provided handle.",
        "input_schema": {
            "type": "object",
            "properties": {
                "label": {
                    "type": "string",
                    "description": "Human-readable account label.",
                },
                "api_key": _secret_handle_template(),
            },
        },
    }
    safe_entry = _entry(
        module=module,
        tool_name="login",
        semantic_schema_sha256=_digest(safe_schema),
        handler_binding=_binding("host.login"),
    )
    assert (
        _envelope(
            provider="anthropic",
            semantic_schemas=[safe_schema],
            entries=[safe_entry],
        )
        .entries[0]
        .tool_name
        == "login"
    )

    unsafe_schema = json.loads(json.dumps(safe_schema))
    unsafe_schema["input_schema"]["properties"]["api_key"][
        "description"
    ] = "API key: sk-live-plaintext"
    unsafe_entry = _entry(
        module=module,
        tool_name="login",
        semantic_schema_sha256=_digest(unsafe_schema),
        handler_binding=_binding("host.login"),
    )
    with pytest.raises(ValueError, match="secret schema"):
        _envelope(
            provider="anthropic",
            semantic_schemas=[unsafe_schema],
            entries=[unsafe_entry],
        )


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    [
        ("$comment", "sk-live-plaintext"),
        ("pattern", "sk-live-plaintext"),
        ("x-vendor-note", "sk-live-plaintext"),
        ("$ref", "sk-live-plaintext"),
        ("allOf", [{"type": "string", "const": "sk-live-plaintext"}]),
        ("allowed_values", ["sk-live-plaintext"]),
    ],
)
def test_secret_schema_uses_a_fixed_structural_allowlist(
    field_name: str,
    field_value: object,
) -> None:
    module = _contract()
    schema = {
        "name": "login",
        "input_schema": {
            "type": "object",
            "properties": {
                "api_key": {
                    **_secret_handle_template(),
                    field_name: field_value,
                },
            },
        },
    }
    entry = _entry(
        module=module,
        tool_name="login",
        semantic_schema_sha256=_digest(schema),
        handler_binding=_binding("host.login"),
    )

    with pytest.raises(ValueError, match="secret schema.*allowlist"):
        _envelope(
            provider="anthropic",
            semantic_schemas=[schema],
            entries=[entry],
        )


def test_credential_named_schema_property_requires_explicit_secret_marker() -> None:
    module = _contract()
    schema = {
        "name": "login",
        "input_schema": {
            "type": "object",
            "properties": {
                "api_key": {"type": "string"},
            },
        },
    }
    entry = _entry(
        module=module,
        tool_name="login",
        semantic_schema_sha256=_digest(schema),
        handler_binding=_binding("host.login"),
    )

    with pytest.raises(ValueError, match="explicit.*x-pupu-secret"):
        _envelope(
            provider="anthropic",
            semantic_schemas=[schema],
            entries=[entry],
        )


def test_secret_marker_false_and_untyped_marker_fail_closed() -> None:
    module = _contract()
    for secret_schema in (
        {"type": "string", "x-pupu-secret": False},
        {"type": "string", "x-pupu-secret": True},
    ):
        schema = {
            "name": "login",
            "input_schema": {
                "type": "object",
                "properties": {"api_key": secret_schema},
            },
        }
        entry = _entry(
            module=module,
            tool_name="login",
            semantic_schema_sha256=_digest(schema),
            handler_binding=_binding("host.login"),
        )
        with pytest.raises(ValueError, match="secret.*true|secret.*kind"):
            _envelope(
                provider="anthropic",
                semantic_schemas=[schema],
                entries=[entry],
            )


def test_secret_marker_is_only_valid_on_a_parameter_property_schema() -> None:
    module = _contract()
    schema = {
        "name": "login",
        "metadata": _secret_handle_template(),
    }
    entry = _entry(
        module=module,
        tool_name="login",
        semantic_schema_sha256=_digest(schema),
        handler_binding=_binding("host.login"),
    )

    with pytest.raises(ValueError, match="secret marker.*property"):
        _envelope(semantic_schemas=[schema], entries=[entry])


def test_secret_marker_is_rejected_inside_arbitrary_metadata_properties() -> None:
    module = _contract()
    schema = {
        "name": "login",
        "metadata": {
            "properties": {"api_key": _secret_handle_template()},
        },
    }
    entry = _entry(
        module=module,
        tool_name="login",
        semantic_schema_sha256=_digest(schema),
        handler_binding=_binding("host.login"),
    )

    with pytest.raises(ValueError, match="secret marker.*parameter"):
        _envelope(semantic_schemas=[schema], entries=[entry])


def test_system_secret_templates_cannot_be_mutated_to_admit_caller_metadata() -> None:
    module = _contract()
    mutable_template = getattr(module, "_SECRET_HANDLE_TEMPLATE", None)
    if isinstance(mutable_template, dict):
        mutable_template["$comment"] = "sk-live-plaintext"
    try:
        schema = {
            "name": "login",
            "input_schema": {
                "type": "object",
                "properties": {
                    "api_key": {
                        **_secret_handle_template(),
                        "$comment": "sk-live-plaintext",
                    }
                },
            },
        }
        entry = _entry(
            module=module,
            tool_name="login",
            semantic_schema_sha256=_digest(schema),
            handler_binding=_binding("host.login"),
        )
        with pytest.raises(ValueError, match="secret schema.*allowlist"):
            _envelope(
                provider="anthropic",
                semantic_schemas=[schema],
                entries=[entry],
            )
    finally:
        if isinstance(mutable_template, dict):
            mutable_template.pop("$comment", None)


@pytest.mark.parametrize(
    "secret_schema",
    [
        {
            "name": "login",
            "input_schema": {
                "type": "object",
                "properties": {
                    "api_key": {
                        **_secret_handle_template(),
                        "default": "sk-live-plaintext",
                    }
                },
            },
        },
        {
            "name": "login",
            "input_schema": {
                "type": "object",
                "properties": {
                    "api_key": {
                        "type": "string",
                        "x-pupu-secret": "sk-live-plaintext",
                        "x-pupu-secret-kind": "handle",
                    }
                },
            },
        },
    ],
)
def test_envelope_rejects_secret_values_hidden_in_schema_declarations(
    secret_schema: dict,
) -> None:
    module = _contract()
    entry = _entry(
        module=module,
        tool_name="login",
        semantic_schema_sha256=_digest(secret_schema),
        handler_binding=_binding("host.login"),
    )

    with pytest.raises(ValueError, match="secret"):
        _envelope(
            provider="anthropic",
            semantic_schemas=[secret_schema],
            entries=[entry],
        )


def test_envelope_rejects_duplicate_schema_names_and_schema_entry_mismatch() -> None:
    module = _contract()
    schemas = _schemas()
    duplicate = [schemas[0], {**schemas[0]}]
    with pytest.raises(ValueError, match="duplicate"):
        _envelope(
            semantic_schemas=duplicate,
            entries=[_entries(schemas)[0], _entries(schemas)[0]],
        )

    with pytest.raises(ValueError, match="same order"):
        _envelope(entries=list(reversed(_entries(schemas))))

    bad_entries = _entries(schemas)
    bad_entries[0] = _entry(
        module=module,
        tool_name="search",
        semantic_schema_sha256=ONE_SHA,
        handler_binding=_binding("host.search"),
    )
    with pytest.raises(ValueError, match="schema digest"):
        _envelope(entries=bad_entries)


def test_envelope_from_dict_rejects_manifest_schema_and_catalog_digest_tampering() -> (
    None
):
    module = _contract()
    raw = _envelope().to_dict()
    variants = []
    manifest = {**raw["tool_schema_manifest"], "search": ZERO_SHA}
    variants.append(({**raw, "tool_schema_manifest": manifest}, "manifest"))
    variants.append(({**raw, "tool_schema_sha256": ZERO_SHA}, "schema digest"))
    variants.append(({**raw, "catalog_sha256": ZERO_SHA}, "catalog digest"))
    variants.append(({**raw, "unexpected": True}, "unknown"))

    for tampered, message in variants:
        with pytest.raises((TypeError, ValueError), match=message):
            module.ToolCatalogEnvelope.from_dict(tampered)


def test_openai_computer_schema_has_a_stable_semantic_name() -> None:
    module = _contract()
    schema = {"type": "computer", "display_width": 1024, "display_height": 768}
    entry = _entry(
        module=module,
        tool_name="computer",
        semantic_schema_sha256=_digest(schema),
        handler_binding=_binding("host.computer"),
    )

    envelope = _envelope(
        provider="openai",
        semantic_schemas=[schema],
        entries=[entry],
    )

    assert envelope.tool_schema_manifest == {"computer": _digest(schema)}


def test_catalog_iteration_matches_the_bounded_journal_contract() -> None:
    assert _envelope(iteration=2**31 - 1).iteration == 2**31 - 1
    with pytest.raises(ValueError, match="iteration"):
        _envelope(iteration=2**31)


def test_provider_identity_requires_a_canonical_runtime_provider_id() -> None:
    assert _envelope(provider="openai").provider == "openai"
    with pytest.raises(ValueError, match="provider"):
        _envelope(provider="OpenAI")


def test_provider_identity_rejects_unknown_adapter_names() -> None:
    with pytest.raises(ValueError, match="provider"):
        _envelope(provider="unknown-provider")


def test_safe_token_metric_property_does_not_require_a_secret_marker() -> None:
    module = _contract()
    schema = {
        "name": "budget",
        "parameters": {
            "type": "object",
            "properties": {"max_tokens": {"type": "integer"}},
        },
    }
    entry = _entry(
        module=module,
        tool_name="budget",
        semantic_schema_sha256=_digest(schema),
        handler_binding=_binding("host.budget"),
    )

    assert (
        _envelope(
            semantic_schemas=[schema],
            entries=[entry],
        )
        .entries[0]
        .tool_name
        == "budget"
    )


def test_caller_supplied_manifests_are_preflighted_before_canonicalization() -> None:
    secret_manifest = [
        {
            "path": f"/semantic_schemas/0/parameters/properties/secret_{index}",
            "kind": "handle",
            "revision": 1,
            "schema_sha256": ZERO_SHA,
        }
        for index in range(257)
    ]
    with pytest.raises(BoundaryResourceLimitError) as secret_error:
        _envelope(secret_schema_manifest=secret_manifest)
    assert secret_error.value.dimension == "items"

    tool_manifest = {f"tool_{index}": ZERO_SHA for index in range(257)}
    with pytest.raises(BoundaryResourceLimitError) as tool_error:
        _envelope(tool_schema_manifest=tool_manifest)
    assert tool_error.value.dimension == "items"

    oversized_manifest = [
        {
            "path": "/semantic_schemas/0/" + "x" * (1024 * 1024),
            "kind": "handle",
            "revision": 1,
            "schema_sha256": ZERO_SHA,
        }
    ]
    with pytest.raises(BoundaryResourceLimitError) as byte_error:
        _envelope(secret_schema_manifest=oversized_manifest)
    assert byte_error.value.dimension == "bytes"


def test_caller_supplied_manifest_cycle_is_rejected_without_recursion() -> None:
    manifest: list = []
    manifest.append(manifest)

    with pytest.raises(ValueError, match="circular"):
        _envelope(secret_schema_manifest=manifest)


def test_catalog_resource_limits_match_the_p0_security_ceiling() -> None:
    module = _contract()

    assert module.MAX_TOOL_CATALOG_ENTRIES == 256
    assert module.MAX_TOOL_SCHEMA_BYTES == 64 * 1024
    assert module.MAX_TOOL_CATALOG_BYTES == 1024 * 1024
    assert module.MAX_TOOL_CATALOG_ARTIFACT_BYTES == 2 * 1024 * 1024
    assert module.MAX_TOOL_SCHEMA_CONTAINER_ITEMS == 1024
    assert module.MAX_TOOL_SCHEMA_STRING_BYTES == 16 * 1024
    assert module.TOOL_CATALOG_JSON_LIMITS.max_depth == 32
    assert module.TOOL_CATALOG_JSON_LIMITS.max_nodes == 50_000


def test_schema_resource_limits_reject_oversized_strings_and_containers() -> None:
    module = _contract()
    variants = (
        (
            {"name": "large_string", "metadata": "x" * (16 * 1024 + 1)},
            "string_bytes",
        ),
        ({"name": "large_container", "metadata": [0] * 1025}, "container_items"),
    )

    for schema, dimension in variants:
        entry = _entry(
            module=module,
            tool_name=schema["name"],
            semantic_schema_sha256=_digest(schema),
            handler_binding=_binding(f"host.{schema['name']}"),
        )
        with pytest.raises(BoundaryResourceLimitError) as caught:
            _envelope(semantic_schemas=[schema], entries=[entry])
        assert caught.value.dimension == dimension


def test_schema_and_catalog_byte_limits_are_enforced_independently() -> None:
    module = _contract()
    oversized_schema = {
        "name": "oversized",
        "metadata": ["x" * 15_000 for _index in range(5)],
    }
    oversized_entry = _entry(
        module=module,
        tool_name="oversized",
        semantic_schema_sha256=_digest(oversized_schema),
        handler_binding=_binding("host.oversized"),
    )
    with pytest.raises(BoundaryResourceLimitError) as caught_schema:
        _envelope(
            semantic_schemas=[oversized_schema],
            entries=[oversized_entry],
        )
    assert caught_schema.value.dimension == "bytes"
    assert caught_schema.value.limit == module.MAX_TOOL_SCHEMA_BYTES

    schemas = [
        {
            "name": f"tool_{index}",
            "metadata": ["x" * 13_000 for _part in range(4)],
        }
        for index in range(21)
    ]
    entries = [
        _entry(
            module=module,
            tool_name=schema["name"],
            semantic_schema_sha256=_digest(schema),
            handler_binding=_binding(f"host.{schema['name']}"),
        )
        for schema in schemas
    ]
    with pytest.raises(BoundaryResourceLimitError) as caught_catalog:
        _envelope(semantic_schemas=schemas, entries=entries)
    assert caught_catalog.value.dimension == "bytes"
    assert caught_catalog.value.limit == module.MAX_TOOL_CATALOG_BYTES


def test_deep_schema_hits_typed_resource_limit_before_python_recursion() -> None:
    module = _contract()
    nested: dict = {"type": "string"}
    for _index in range(1_200):
        nested = {"items": nested}
    schema = {"name": "deep_tool", "input_schema": nested}
    entry = _entry(
        module=module,
        tool_name="deep_tool",
        semantic_schema_sha256=ZERO_SHA,
        handler_binding=_binding("host.deep_tool"),
    )

    with pytest.raises(BoundaryResourceLimitError) as caught:
        _envelope(semantic_schemas=[schema], entries=[entry])

    assert caught.value.dimension == "depth"


def test_snapshot_exactly_binds_catalog_artifact_and_journal_cursor() -> None:
    module = _contract()
    envelope = _envelope()
    cursor = EventCursor(store_seq=42, event_id="event-tool-catalog-1")
    artifact = _artifact_for(envelope)
    snapshot = module.ToolCatalogSnapshot(
        envelope=envelope,
        event_cursor=cursor,
        artifact=artifact,
    )
    raw = snapshot.to_dict()

    assert raw == {
        "schema": "unchain.tool_catalog_snapshot.v1",
        "envelope": envelope.to_dict(),
        "event_cursor": cursor.to_dict(),
        "artifact": artifact.to_dict(),
        "snapshot_sha256": snapshot.snapshot_sha256,
    }
    assert snapshot.attempt == envelope.attempt
    assert snapshot.iteration == envelope.iteration
    assert snapshot.provider == envelope.provider
    assert snapshot.model == envelope.model
    assert snapshot.catalog_sha256 == envelope.catalog_sha256
    assert artifact.sha256 != envelope.catalog_sha256
    assert module.ToolCatalogSnapshot.from_dict(raw) == snapshot


def test_snapshot_rejects_wrong_artifact_bytes_media_scope_and_tampered_cursor() -> (
    None
):
    module = _contract()
    envelope = _envelope()
    cursor = EventCursor(store_seq=42, event_id="event-tool-catalog-1")
    artifact = _artifact_for(envelope)

    for bad_artifact, message in (
        (
            ArtifactRef(
                ref=artifact.ref,
                media_type=artifact.media_type,
                byte_length=artifact.byte_length,
                sha256=ZERO_SHA,
            ),
            "artifact digest",
        ),
        (
            ArtifactRef(
                ref=artifact.ref,
                media_type="text/plain",
                byte_length=artifact.byte_length,
                sha256=artifact.sha256,
            ),
            "application/json",
        ),
        (
            ArtifactRef(
                ref=ResourceRef(kind="memory", resource_id="catalog-1", revision=1),
                media_type=artifact.media_type,
                byte_length=artifact.byte_length,
                sha256=artifact.sha256,
            ),
            "whole artifact",
        ),
    ):
        with pytest.raises(ValueError, match=message):
            module.ToolCatalogSnapshot(
                envelope=envelope,
                event_cursor=cursor,
                artifact=bad_artifact,
            )

    raw = module.ToolCatalogSnapshot(
        envelope=envelope,
        event_cursor=cursor,
        artifact=artifact,
    ).to_dict()
    raw["event_cursor"] = EventCursor(
        store_seq=43,
        event_id="event-tool-catalog-2",
    ).to_dict()
    with pytest.raises(ValueError, match="snapshot digest"):
        module.ToolCatalogSnapshot.from_dict(raw)


def test_snapshot_rejects_unverified_artifact_preview_side_channel() -> None:
    module = _contract()
    envelope = _envelope()
    artifact = _artifact_for(envelope)
    preview_artifact = ArtifactRef(
        ref=artifact.ref,
        media_type=artifact.media_type,
        byte_length=artifact.byte_length,
        sha256=artifact.sha256,
        preview="sk-live-plaintext",
    )

    with pytest.raises(ValueError, match="preview"):
        module.ToolCatalogSnapshot(
            envelope=envelope,
            event_cursor=EventCursor(
                store_seq=42,
                event_id="event-tool-catalog-1",
            ),
            artifact=preview_artifact,
        )


def test_snapshot_rejects_subclass_and_duck_typed_refs() -> None:
    module = _contract()

    class CursorSubclass(EventCursor):
        pass

    class ArtifactSubclass(ArtifactRef):
        pass

    envelope = _envelope()
    artifact = _artifact_for(envelope)
    cursor = EventCursor(store_seq=42, event_id="event-tool-catalog-1")

    with pytest.raises(TypeError, match="exact EventCursor"):
        module.ToolCatalogSnapshot(
            envelope=envelope,
            event_cursor=CursorSubclass(
                **{
                    key: value
                    for key, value in cursor.to_dict().items()
                    if key != "schema"
                }
            ),
            artifact=artifact,
        )
    with pytest.raises(TypeError, match="exact ArtifactRef"):
        module.ToolCatalogSnapshot(
            envelope=envelope,
            event_cursor=cursor,
            artifact=ArtifactSubclass(
                ref=artifact.ref,
                media_type=artifact.media_type,
                byte_length=artifact.byte_length,
                sha256=artifact.sha256,
            ),
        )

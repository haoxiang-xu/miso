from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import fields, is_dataclass

import pytest

from unchain.context.tool_catalog import ToolCatalogEnvelope, ToolCatalogEntry
from unchain.journal.models import AttemptRef, GenerationRef
from unchain.journal.resource_limits import BoundaryResourceLimitError
from unchain.tools.handler_registry import (
    DurableToolHandlerBinding,
    DurableToolHandlerRegistry,
)
from unchain.tools.tool import Tool


ZERO_SHA = "0" * 64
ONE_SHA = "1" * 64
TARGET_SHA = "2" * 64
ATTEMPT = AttemptRef(
    GenerationRef("execution-wire-1", "generation-wire-1"),
    "attempt-wire-1",
)
EPHEMERAL = {"type": "ephemeral"}
PROFILES = {
    "openai": (
        "unchain.openai.responses.request.v1",
        "openai.responses.create",
    ),
    "anthropic": (
        "unchain.anthropic.messages.request.v1",
        "anthropic.messages.stream",
    ),
    "hyperspace": (
        "unchain.hyperspace.anthropic-messages.request.v1",
        "hyperspace.anthropic.messages.stream",
    ),
    "ollama": (
        "unchain.ollama.chat.request.v1",
        "ollama.api.chat.post",
    ),
}


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _plain_json(value: object):
    if isinstance(value, Mapping):
        return {key: _plain_json(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_plain_json(child) for child in value]
    return value


def _schema(provider: str, name: str = "search") -> dict:
    parameters = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    }
    if provider in {"anthropic", "hyperspace"}:
        return {
            "name": name,
            "description": f"Run {name}",
            "input_schema": parameters,
        }
    if provider == "ollama":
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": f"Run {name}",
                "parameters": parameters,
            },
        }
    return {
        "type": "function",
        "name": name,
        "description": f"Run {name}",
        "parameters": parameters,
    }


def _schema_name(provider: str, schema: dict) -> str:
    if provider == "ollama":
        return schema["function"]["name"]
    return schema["name"]


def _catalog(
    provider: str,
    *,
    schemas: list[dict] | None = None,
    required_betas: tuple[str, ...] = (),
    configured_model: str = "frontier-model",
    prompt_sha256: str = ZERO_SHA,
) -> ToolCatalogEnvelope:
    semantic_schemas = schemas if schemas is not None else [_schema(provider)]
    entries = []
    for index, schema in enumerate(semantic_schemas):
        name = _schema_name(provider, schema)
        entries.append(
            ToolCatalogEntry(
                tool_name=name,
                semantic_schema_sha256=_canonical_sha256(schema),
                tool_descriptor_sha256=f"{index + 3:x}" * 64,
                handler_binding=DurableToolHandlerBinding(
                    handler_id=f"host.{name}",
                    revision=1,
                    config_sha256=f"{index + 4:x}" * 64,
                    kind="stable",
                ),
            )
        )
    return ToolCatalogEnvelope(
        attempt=ATTEMPT,
        iteration=7,
        provider=provider,
        model=configured_model,
        semantic_schemas=semantic_schemas,
        entries=entries,
        required_betas_sha256=_canonical_sha256(list(required_betas)),
        prompt_sha256=prompt_sha256,
        exposure_plan_sha256=ONE_SHA,
    )


def _wire_tools(catalog: ToolCatalogEnvelope) -> list[dict]:
    tools = [_plain_json(schema) for schema in catalog.provider_schemas]
    if tools and catalog.provider in {"anthropic", "hyperspace"}:
        tools[-1]["cache_control"] = copy.deepcopy(EPHEMERAL)
    return tools


def _body(
    provider: str,
    catalog: ToolCatalogEnvelope,
    *,
    request_model: str = "frontier-model",
    required_betas: tuple[str, ...] = (),
    base_betas: tuple[str, ...] = (),
) -> dict:
    if provider == "openai":
        body = {
            "model": request_model,
            "input": [{"role": "user", "content": "hello"}],
            "stream": True,
            "store": False,
            "include": ["reasoning.encrypted_content"],
            "tool_choice": "auto",
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "answer",
                    "schema": {"type": "object"},
                }
            },
        }
    elif provider in {"anthropic", "hyperspace"}:
        body = {
            "model": request_model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "hello",
                            "cache_control": copy.deepcopy(EPHEMERAL),
                        }
                    ],
                }
            ],
            "max_tokens": 1024,
            "system": [
                {
                    "type": "text",
                    "text": "system",
                    "cache_control": copy.deepcopy(EPHEMERAL),
                }
            ],
        }
        merged = [*base_betas]
        for beta in required_betas:
            if beta not in merged:
                merged.append(beta)
        if merged:
            body["extra_headers"] = {"anthropic-beta": ",".join(merged)}
    else:
        body = {
            "model": request_model,
            "messages": [{"role": "user", "content": "hello"}],
            "stream": True,
            "options": {"temperature": 0.2},
            "format": {"type": "object"},
        }

    tools = _wire_tools(catalog)
    if tools:
        body["tools"] = tools
        if provider == "ollama":
            body["tool_choice"] = "auto"
    return body


def _route(module, name: str, request: dict):
    return module.ProviderWireRoute(name=name, request=request)


def _envelope(
    *,
    module,
    provider: str = "openai",
    catalog: ToolCatalogEnvelope | None = None,
    routes: list | tuple | None = None,
    configured_model: str = "frontier-model",
    request_model: str = "frontier-model",
    required_betas: tuple[str, ...] = (),
    base_betas: tuple[str, ...] = (),
    adapter_revision: str | None = None,
    transport_kind: str | None = None,
):
    resolved_catalog = catalog or _catalog(
        provider,
        required_betas=required_betas,
        configured_model=configured_model,
    )
    revision, transport = PROFILES[provider]
    resolved_routes = routes or [
        _route(
            module,
            "primary",
            _body(
                provider,
                resolved_catalog,
                request_model=request_model,
                required_betas=required_betas,
                base_betas=base_betas,
            ),
        )
    ]
    return module.ProviderWireEnvelope(
        attempt=ATTEMPT,
        iteration=7,
        provider=provider,
        configured_model=configured_model,
        request_model=request_model,
        adapter_revision=adapter_revision or revision,
        transport_kind=transport_kind or transport,
        transport_target_sha256=TARGET_SHA,
        source_request_sha256="5" * 64,
        source_payload_sha256="6" * 64,
        catalog_sha256=resolved_catalog.catalog_sha256,
        prompt_sha256=resolved_catalog.prompt_sha256,
        tool_schema_sha256=resolved_catalog.tool_schema_sha256,
        required_betas=required_betas,
        base_anthropic_betas=base_betas,
        routes=resolved_routes,
    )


@pytest.mark.parametrize("provider", tuple(PROFILES))
def test_envelope_round_trips_canonical_final_wire_for_each_provider(
    provider: str,
) -> None:
    from unchain.providers import wire_envelope as module

    required = (
        ("computer-use-2025-11-24",) if provider in {"anthropic", "hyperspace"} else ()
    )
    base = ("prompt-caching-2024",) if provider in {"anthropic", "hyperspace"} else ()
    request_model = (
        "provider-frontier-model"
        if provider in {"anthropic", "hyperspace"}
        else "frontier-model"
    )
    catalog = _catalog(provider, required_betas=required)
    envelope = _envelope(
        module=module,
        provider=provider,
        catalog=catalog,
        request_model=request_model,
        required_betas=required,
        base_betas=base,
    )

    assert envelope.verify_against_catalog(catalog) is envelope
    restored = module.ProviderWireEnvelope.from_dict(envelope.to_dict())
    assert restored.to_dict() == envelope.to_dict()
    assert restored.canonical_bytes() == envelope.canonical_bytes()
    assert restored.envelope_sha256 == envelope.envelope_sha256
    assert restored.verify_against_catalog(catalog) is restored


def test_route_and_envelope_detach_input_and_return_fresh_request_copies() -> None:
    from unchain.providers import wire_envelope as module

    catalog = _catalog("openai")
    request = _body("openai", catalog)
    route = _route(module, "primary", request)
    envelope = _envelope(module=module, catalog=catalog, routes=[route])
    original_digest = envelope.envelope_sha256

    request["input"][0]["content"] = "source mutation"
    route_copy = envelope.request_copy("primary")
    route_copy["input"][0]["content"] = "caller mutation"

    assert envelope.request_copy("primary")["input"][0]["content"] == "hello"
    assert envelope.envelope_sha256 == original_digest
    with pytest.raises(ValueError, match="route"):
        envelope.request_copy("missing")


def test_canonical_digest_ignores_object_key_order_but_preserves_array_order() -> None:
    from unchain.providers import wire_envelope as module

    catalog = _catalog(
        "openai", schemas=[_schema("openai", "a"), _schema("openai", "b")]
    )
    first_body = _body("openai", catalog)
    reordered_body = {key: first_body[key] for key in reversed(tuple(first_body))}
    first = _envelope(
        module=module, catalog=catalog, routes=[_route(module, "primary", first_body)]
    )
    reordered = _envelope(
        module=module,
        catalog=catalog,
        routes=[_route(module, "primary", reordered_body)],
    )
    assert reordered.envelope_sha256 == first.envelope_sha256

    array_changed = copy.deepcopy(first_body)
    array_changed["tools"].reverse()
    changed = _envelope(
        module=module,
        catalog=catalog,
        routes=[_route(module, "primary", array_changed)],
    )
    assert changed.envelope_sha256 != first.envelope_sha256
    with pytest.raises(ValueError, match="tools|catalog"):
        changed.verify_against_catalog(catalog)


@pytest.mark.parametrize("provider", tuple(PROFILES))
def test_provider_revision_and_transport_are_an_exact_allowlisted_tuple(
    provider: str,
) -> None:
    from unchain.providers import wire_envelope as module

    with pytest.raises(ValueError, match="adapter_revision|profile"):
        _envelope(
            module=module, provider=provider, adapter_revision="foreign.adapter.v1"
        )
    with pytest.raises(ValueError, match="transport_kind|profile"):
        _envelope(module=module, provider=provider, transport_kind="foreign.transport")

    raw = _envelope(module=module, provider=provider).to_dict()
    raw["provider"] = "foreign"
    with pytest.raises(ValueError, match="provider"):
        module.ProviderWireEnvelope.from_dict(raw)


def test_transport_target_is_only_a_digest_and_models_are_canonical() -> None:
    from unchain.providers import wire_envelope as module

    envelope = _envelope(module=module)
    raw = envelope.to_dict()
    assert raw["transport_target_sha256"] == TARGET_SHA
    assert "base_url" not in raw
    assert "api_key" not in raw

    raw["transport_target_sha256"] = "https://secret.example"
    with pytest.raises(ValueError, match="transport_target_sha256"):
        module.ProviderWireEnvelope.from_dict(raw)
    for model in (" frontier-model", "frontier-model ", "Cafe\u0301"):
        with pytest.raises((TypeError, ValueError), match="model"):
            _envelope(module=module, configured_model=model, request_model=model)


def test_openai_primary_and_previous_response_fallback_are_both_frozen() -> None:
    from unchain.providers import wire_envelope as module

    catalog = _catalog("openai")
    primary = _body("openai", catalog)
    primary["input"] = [
        {"type": "function_call_output", "call_id": "call-1", "output": "ok"}
    ]
    primary["previous_response_id"] = "resp-1"
    fallback = copy.deepcopy(primary)
    fallback.pop("previous_response_id")
    fallback["input"] = [
        {"role": "user", "content": "full local replay"},
        {"type": "function_call_output", "call_id": "call-1", "output": "ok"},
    ]
    envelope = _envelope(
        module=module,
        catalog=catalog,
        routes=[
            _route(module, "primary", primary),
            _route(module, "openai_previous_response_fallback", fallback),
        ],
    )

    assert tuple(route.name for route in envelope.routes) == (
        "primary",
        "openai_previous_response_fallback",
    )
    assert envelope.request_copy("primary")["previous_response_id"] == "resp-1"
    assert "previous_response_id" not in envelope.request_copy(
        "openai_previous_response_fallback"
    )
    assert envelope.verify_against_catalog(catalog) is envelope


def test_openai_previous_response_fallback_requires_non_empty_replay() -> None:
    from unchain.providers import wire_envelope as module

    catalog = _catalog("openai")
    primary = _body("openai", catalog)
    primary["previous_response_id"] = "response-previous"
    fallback = copy.deepcopy(primary)
    fallback.pop("previous_response_id")
    fallback["input"] = []

    with pytest.raises(ValueError, match="fallback.*non-empty|non-empty.*fallback"):
        _envelope(
            module=module,
            catalog=catalog,
            routes=[
                _route(module, "primary", primary),
                _route(module, "openai_previous_response_fallback", fallback),
            ],
        )


@pytest.mark.parametrize(
    "mutator",
    [
        lambda primary, fallback: fallback.__setitem__(
            "previous_response_id", "resp-1"
        ),
        lambda primary, fallback: fallback.__setitem__("temperature", 0.9),
        lambda primary, fallback: primary.pop("previous_response_id"),
    ],
)
def test_openai_fallback_cannot_change_common_wire_fields(mutator) -> None:
    from unchain.providers import wire_envelope as module

    catalog = _catalog("openai")
    primary = _body("openai", catalog)
    primary["previous_response_id"] = "resp-1"
    fallback = copy.deepcopy(primary)
    fallback.pop("previous_response_id")
    fallback["input"] = [{"role": "user", "content": "full replay"}]
    mutator(primary, fallback)

    with pytest.raises(ValueError, match="fallback|previous_response_id"):
        _envelope(
            module=module,
            catalog=catalog,
            routes=[
                _route(module, "primary", primary),
                _route(module, "openai_previous_response_fallback", fallback),
            ],
        )


def test_route_set_is_unique_bounded_and_provider_specific() -> None:
    from unchain.providers import wire_envelope as module

    catalog = _catalog("openai")
    primary = _route(module, "primary", _body("openai", catalog))
    fallback_body = _body("openai", catalog)
    fallback = _route(module, "openai_previous_response_fallback", fallback_body)

    with pytest.raises(ValueError, match="duplicate|unique"):
        _envelope(module=module, catalog=catalog, routes=[primary, primary])
    with pytest.raises((TypeError, ValueError), match="at most|routes"):
        _envelope(module=module, catalog=catalog, routes=[primary, fallback, fallback])
    with pytest.raises(ValueError, match="primary"):
        _envelope(module=module, catalog=catalog, routes=[fallback])

    anthropic_catalog = _catalog("anthropic")
    with pytest.raises(ValueError, match="fallback|route"):
        _envelope(
            module=module,
            provider="anthropic",
            catalog=anthropic_catalog,
            routes=[
                _route(module, "primary", _body("anthropic", anthropic_catalog)),
                _route(
                    module,
                    "openai_previous_response_fallback",
                    _body("anthropic", anthropic_catalog),
                ),
            ],
        )


@pytest.mark.parametrize("provider", tuple(PROFILES))
def test_catalog_cross_verification_rejects_tool_wire_drift(provider: str) -> None:
    from unchain.providers import wire_envelope as module

    catalog = _catalog(provider)
    body = _body(provider, catalog)
    if provider == "ollama":
        body["tools"][0]["function"]["description"] = "forged"
    else:
        body["tools"][0]["description"] = "forged"
    envelope = _envelope(
        module=module,
        provider=provider,
        catalog=catalog,
        routes=[_route(module, "primary", body)],
    )

    with pytest.raises(ValueError, match="tools|catalog"):
        envelope.verify_against_catalog(catalog)


@pytest.mark.parametrize("provider", ("anthropic", "hyperspace"))
def test_anthropic_family_derives_only_last_tool_cache_decoration(
    provider: str,
) -> None:
    from unchain.providers import wire_envelope as module

    catalog = _catalog(
        provider, schemas=[_schema(provider, "first"), _schema(provider, "last")]
    )
    expected = _body(provider, catalog)
    envelope = _envelope(
        module=module,
        provider=provider,
        catalog=catalog,
        routes=[_route(module, "primary", expected)],
    )
    assert "cache_control" not in envelope.request_copy("primary")["tools"][0]
    assert envelope.request_copy("primary")["tools"][-1]["cache_control"] == EPHEMERAL
    assert envelope.verify_against_catalog(catalog) is envelope

    missing = _body(provider, catalog)
    missing["tools"][-1].pop("cache_control")
    with pytest.raises(ValueError, match="tools|cache_control"):
        _envelope(
            module=module,
            provider=provider,
            catalog=catalog,
            routes=[_route(module, "primary", missing)],
        ).verify_against_catalog(catalog)

    misplaced = _body(provider, catalog)
    misplaced["tools"][0]["cache_control"] = copy.deepcopy(EPHEMERAL)
    with pytest.raises(ValueError, match="tools|cache_control"):
        _envelope(
            module=module,
            provider=provider,
            catalog=catalog,
            routes=[_route(module, "primary", misplaced)],
        ).verify_against_catalog(catalog)


@pytest.mark.parametrize("provider", ("anthropic", "hyperspace"))
def test_anthropic_family_binds_tail_cache_and_final_beta_header(provider: str) -> None:
    from unchain.providers import wire_envelope as module

    required = ("computer-use-2025-11-24", "tools-v2")
    base = ("prompt-caching-2024", "computer-use-2025-11-24")
    catalog = _catalog(provider, required_betas=required)
    envelope = _envelope(
        module=module,
        provider=provider,
        catalog=catalog,
        required_betas=required,
        base_betas=base,
    )
    request = envelope.request_copy("primary")
    assert request["system"][-1]["cache_control"] == EPHEMERAL
    assert request["messages"][-1]["content"][-1]["cache_control"] == EPHEMERAL
    assert request["extra_headers"] == {
        "anthropic-beta": "prompt-caching-2024,computer-use-2025-11-24,tools-v2"
    }
    assert envelope.verify_against_catalog(catalog) is envelope

    for mutation in ("wrong-beta", "computer-use-2025-11-24,tools-v2"):
        bad = copy.deepcopy(request)
        bad["extra_headers"]["anthropic-beta"] = mutation
        with pytest.raises(ValueError, match="anthropic-beta|header"):
            _envelope(
                module=module,
                provider=provider,
                catalog=catalog,
                routes=[_route(module, "primary", bad)],
                required_betas=required,
                base_betas=base,
            )


@pytest.mark.parametrize("provider", ("anthropic", "hyperspace"))
def test_anthropic_family_rejects_missing_tail_message_or_system_cache(
    provider: str,
) -> None:
    from unchain.providers import wire_envelope as module

    catalog = _catalog(provider)
    for path in ("system", "message"):
        body = _body(provider, catalog)
        if path == "system":
            body["system"][-1].pop("cache_control")
        else:
            body["messages"][-1]["content"][-1].pop("cache_control")
        with pytest.raises(ValueError, match="cache_control|cache"):
            _envelope(
                module=module,
                provider=provider,
                catalog=catalog,
                routes=[_route(module, "primary", body)],
            )


@pytest.mark.parametrize(
    ("provider", "header"),
    [
        ("openai", {"anthropic-beta": "x"}),
        ("ollama", {"anthropic-beta": "x"}),
        ("anthropic", {"authorization": "Bearer secret"}),
        ("hyperspace", {"x-api-key": "secret"}),
        ("anthropic", {"cookie": "secret"}),
    ],
)
def test_provider_header_allowlist_rejects_credential_and_foreign_headers(
    provider: str,
    header: dict,
) -> None:
    from unchain.providers import wire_envelope as module

    catalog = _catalog(provider)
    body = _body(provider, catalog)
    body["extra_headers"] = header
    with pytest.raises(ValueError, match="header|credential"):
        _envelope(
            module=module,
            provider=provider,
            catalog=catalog,
            routes=[_route(module, "primary", body)],
        )


def test_ollama_tool_choice_is_exactly_auto_if_and_only_if_tools_exist() -> None:
    from unchain.providers import wire_envelope as module

    catalog = _catalog("ollama")
    body = _body("ollama", catalog)
    body["tool_choice"] = "required"
    with pytest.raises(ValueError, match="tool_choice"):
        _envelope(
            module=module,
            provider="ollama",
            catalog=catalog,
            routes=[_route(module, "primary", body)],
        )

    empty_catalog = _catalog("ollama", schemas=[])
    empty_body = _body("ollama", empty_catalog)
    empty_body["tool_choice"] = "auto"
    with pytest.raises(ValueError, match="tool_choice"):
        _envelope(
            module=module,
            provider="ollama",
            catalog=empty_catalog,
            routes=[_route(module, "primary", empty_body)],
        )


@pytest.mark.parametrize(
    ("provider", "mutation", "message"),
    [
        ("openai", lambda body: body.__setitem__("stream", False), "stream"),
        ("openai", lambda body: body.pop("input"), "input"),
        ("anthropic", lambda body: body.__setitem__("messages", []), "messages"),
        ("anthropic", lambda body: body.__setitem__("max_tokens", 0), "max_tokens"),
        ("hyperspace", lambda body: body.__setitem__("stream", True), "stream"),
        ("ollama", lambda body: body.__setitem__("stream", False), "stream"),
        ("ollama", lambda body: body.pop("messages"), "messages"),
    ],
)
def test_provider_request_root_shape_is_exact(
    provider: str, mutation, message: str
) -> None:
    from unchain.providers import wire_envelope as module

    catalog = _catalog(provider)
    body = _body(provider, catalog)
    mutation(body)
    with pytest.raises(ValueError, match=message):
        _envelope(
            module=module,
            provider=provider,
            catalog=catalog,
            routes=[_route(module, "primary", body)],
        )


def test_catalog_subject_and_digest_must_match_the_envelope() -> None:
    from unchain.providers import wire_envelope as module

    catalog = _catalog("openai")
    envelope = _envelope(module=module, catalog=catalog)
    for foreign in (
        _catalog("anthropic"),
        _catalog("openai", configured_model="different-model"),
        _catalog("openai", prompt_sha256="7" * 64),
    ):
        with pytest.raises(ValueError, match="catalog|provider|model|prompt"):
            envelope.verify_against_catalog(foreign)


def test_repr_and_public_object_graph_do_not_expose_request_or_live_authority() -> None:
    from unchain.providers import wire_envelope as module

    catalog = _catalog("openai")
    body = _body("openai", catalog)
    body["input"][0]["content"] = "super-secret-request-text"
    envelope = _envelope(
        module=module,
        catalog=catalog,
        routes=[_route(module, "primary", body)],
    )

    assert "super-secret-request-text" not in repr(envelope)
    assert "super-secret-request-text" not in repr(envelope.routes[0])
    forbidden = (Tool, DurableToolHandlerRegistry)
    seen: set[int] = set()
    stack = [envelope]
    while stack:
        value = stack.pop()
        identity = id(value)
        if identity in seen:
            continue
        seen.add(identity)
        assert not isinstance(value, forbidden)
        if value is None or type(value) in {bool, int, float, str, bytes}:
            continue
        assert not callable(value)
        if is_dataclass(value):
            stack.extend(getattr(value, item.name) for item in fields(value))
        elif isinstance(value, Mapping):
            stack.extend(value.keys())
            stack.extend(value.values())
        elif isinstance(value, Sequence):
            stack.extend(value)


def test_wire_transport_safety_limits_are_not_context_budget_limits() -> None:
    from unchain.providers import wire_envelope as module

    assert module.MAX_PROVIDER_WIRE_BYTES == 64 * 1024 * 1024
    assert module.MAX_PROVIDER_WIRE_DEPTH == 64
    assert module.MAX_PROVIDER_WIRE_NODES == 1_000_000
    assert module.MAX_PROVIDER_WIRE_CONTAINER_ITEMS == 250_000
    assert module.MAX_PROVIDER_WIRE_STRING_BYTES == 32 * 1024 * 1024
    assert module.MAX_PROVIDER_WIRE_ROUTES == 2


@pytest.mark.parametrize(
    ("constant", "limit", "payload", "dimension"),
    [
        ("MAX_PROVIDER_WIRE_DEPTH", 3, {"value": [[[[None]]]]}, "depth"),
        ("MAX_PROVIDER_WIRE_NODES", 5, {"values": [0, 1, 2, 3]}, "nodes"),
        ("MAX_PROVIDER_WIRE_BYTES", 24, {"value": "01234567890123456789"}, "bytes"),
        (
            "MAX_PROVIDER_WIRE_CONTAINER_ITEMS",
            2,
            {"values": [0, 1, 2]},
            "container_items",
        ),
        ("MAX_PROVIDER_WIRE_STRING_BYTES", 4, {"value": "12345"}, "string_bytes"),
    ],
)
def test_iterative_preflight_rejects_each_resource_dimension_before_serialization(
    monkeypatch,
    constant: str,
    limit: int,
    payload: dict,
    dimension: str,
) -> None:
    from unchain.providers import wire_envelope as module

    monkeypatch.setattr(module, constant, limit)
    monkeypatch.setattr(
        module.json,
        "dumps",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("serialization ran before preflight")
        ),
    )
    with pytest.raises(BoundaryResourceLimitError) as caught:
        module.ProviderWireRoute(name="primary", request=payload)
    assert caught.value.boundary == "provider wire request"
    assert caught.value.dimension == dimension


def test_iterative_preflight_rejects_cycles_and_non_exact_json_before_serialization(
    monkeypatch,
) -> None:
    from unchain.providers import wire_envelope as module

    cyclic: dict = {}
    cyclic["self"] = cyclic
    monkeypatch.setattr(
        module.json,
        "dumps",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("serialization ran before preflight")
        ),
    )
    with pytest.raises(ValueError, match="circular"):
        module.ProviderWireRoute(name="primary", request=cyclic)
    with pytest.raises(TypeError, match="exact JSON"):
        module.ProviderWireRoute(name="primary", request={"value": (1,)})


def test_supplied_route_and_envelope_digests_cannot_be_forged() -> None:
    from unchain.providers import wire_envelope as module

    catalog = _catalog("openai")
    route = _route(module, "primary", _body("openai", catalog))
    route_raw = route.to_dict()
    route_raw["request_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="digest|request_sha256"):
        module.ProviderWireRoute.from_dict(route_raw)

    envelope = _envelope(module=module, catalog=catalog)
    raw = envelope.to_dict()
    raw["envelope_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="digest|envelope_sha256"):
        module.ProviderWireEnvelope.from_dict(raw)

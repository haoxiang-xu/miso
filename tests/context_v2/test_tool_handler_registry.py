from __future__ import annotations

import importlib
import pickle
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from types import MappingProxyType

import pytest

from unchain.journal.resource_limits import BoundaryResourceLimitError
from unchain.tools.tool import Tool


ZERO_SHA = "0" * 64
ONE_SHA = "1" * 64


def _contract():
    return importlib.import_module("unchain.tools.handler_registry")


def _binding(**overrides):
    module = _contract()
    values = {
        "handler_id": "host.shell.execute",
        "revision": 3,
        "config_sha256": module.tool_config_sha256(_tool()),
        "kind": "stable",
        "route_resolver_id": "host.route.direct",
        "route_resolver_revision": 2,
        "process_epoch": None,
    }
    values.update(overrides)
    if values["kind"] == "process_bound" and "process_epoch" not in overrides:
        values["process_epoch"] = "untrusted-process-epoch"
    return module.DurableToolHandlerBinding(**values)


def _handler(arguments):
    return arguments


def _tool(handler=_handler) -> Tool:
    return Tool(name="shell", description="Run a command", func=handler)


def _assert_registry_maps_empty(registry) -> None:
    assert registry.registration_count == 0
    assert registry._registrations == {}
    assert registry._identity_revisions == {}
    assert registry._tool_bindings == {}


def test_handler_binding_is_strict_canonical_and_has_a_deterministic_digest() -> None:
    module = _contract()
    binding = _binding()
    expected = {
        "schema": "unchain.durable_tool_handler_binding.v1",
        "handler_id": "host.shell.execute",
        "revision": 3,
        "config_sha256": module.tool_config_sha256(_tool()),
        "kind": "stable",
        "route_resolver_id": "host.route.direct",
        "route_resolver_revision": 2,
        "process_epoch": None,
    }

    assert binding.to_dict() == expected
    assert type(binding).from_dict(dict(reversed(list(expected.items())))) == binding
    assert binding.sha256 == type(binding).from_dict(expected).sha256
    assert len(binding.sha256) == 64
    with pytest.raises(FrozenInstanceError):
        binding.revision = 4


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"unknown": True}, "unknown"),
        ({"revision": 0}, "revision"),
        ({"config_sha256": "A" * 64}, "SHA-256"),
        ({"kind": "ephemeral"}, "kind"),
        ({"kind": "stable", "process_epoch": "forbidden-epoch"}, "process_epoch"),
        ({"kind": "process_bound", "process_epoch": None}, "process_epoch"),
        ({"route_resolver_id": "", "route_resolver_revision": 2}, "route_resolver_id"),
        (
            {"route_resolver_id": "host.route.direct", "route_resolver_revision": None},
            "together",
        ),
    ],
)
def test_handler_binding_rejects_noncanonical_or_incomplete_identity(
    changes: dict[str, object],
    message: str,
) -> None:
    module = _contract()
    raw = _binding().to_dict()
    raw.update(changes)
    if "unknown" in changes:
        raw["unknown"] = raw.pop("unknown")

    with pytest.raises((TypeError, ValueError), match=message):
        module.DurableToolHandlerBinding.from_dict(raw)


def test_handler_binding_does_not_serialize_callable_diagnostics_as_authority() -> None:
    raw = _binding().to_dict()

    assert "module" not in raw
    assert "qualname" not in raw
    assert "name" not in raw


def test_handler_binding_from_dict_requires_exact_canonical_input() -> None:
    module = _contract()
    raw = _binding().to_dict()

    with pytest.raises(TypeError, match="exact dict"):
        module.DurableToolHandlerBinding.from_dict(MappingProxyType(raw))
    with pytest.raises(ValueError, match="canonical"):
        module.DurableToolHandlerBinding.from_dict(
            {**raw, "handler_id": " host.shell.execute "}
        )


def test_registry_binds_and_resolves_the_exact_objects() -> None:
    module = _contract()
    registry = module.DurableToolHandlerRegistry()
    binding = _binding()
    tool = _tool()
    resolver = object()

    registered = registry.register(
        binding,
        tool=tool,
        handler=_handler,
        route_resolver=resolver,
    )
    resolved = registry.resolve(binding)

    assert type(registered) is module.DurableToolHandlerResolution
    assert resolved is registered
    assert resolved.binding is binding
    assert resolved.tool is tool
    assert resolved.handler is _handler
    assert resolved.route_resolver is resolver
    assert registry.verify_resolution(resolved) is resolved


def test_stable_binding_can_be_reresolved_within_the_owning_registry() -> None:
    module = _contract()
    registry = module.DurableToolHandlerRegistry()
    original = _binding()
    registration = registry.register(
        original,
        tool=_tool(),
        handler=_handler,
        route_resolver=object(),
    )
    recovered = module.DurableToolHandlerBinding.from_dict(original.to_dict())

    assert recovered is not original
    assert registry.resolve_stable(recovered) is registration
    assert registry.resolve(recovered) is registration
    assert not hasattr(registry, "resolve_cold")


def test_registry_rejects_duplicate_and_conflicting_id_revision_bindings() -> None:
    module = _contract()
    registry = module.DurableToolHandlerRegistry()
    binding = _binding()
    registry.register(
        binding,
        tool=_tool(),
        handler=_handler,
        route_resolver=object(),
    )

    with pytest.raises(module.ToolHandlerConflictError, match="already registered"):
        registry.register(
            module.DurableToolHandlerBinding.from_dict(binding.to_dict()),
            tool=_tool(),
            handler=_handler,
            route_resolver=object(),
        )
    with pytest.raises(module.ToolHandlerConflictError, match="conflicts"):
        registry.register(
            _binding(config_sha256=ONE_SHA),
            tool=_tool(),
            handler=_handler,
            route_resolver=object(),
        )


def test_process_bound_binding_is_warm_only_and_registry_local() -> None:
    module = _contract()
    owner = module.DurableToolHandlerRegistry()
    stranger = module.DurableToolHandlerRegistry()
    untrusted_binding = _binding(
        handler_id="dynamic.plugin.execute",
        kind="process_bound",
        route_resolver_id=None,
        route_resolver_revision=None,
    )
    with pytest.raises(module.ProcessBoundToolHandlerError, match="warm authority"):
        owner.register(
            untrusted_binding,
            tool=_tool(),
            handler=_handler,
        )
    registration = owner.register_process_bound(
        handler_id="dynamic.plugin.execute",
        revision=3,
        tool=_tool(),
        handler=_handler,
    )
    binding = registration.binding

    assert owner.resolve(binding) is registration
    with pytest.raises(module.ProcessBoundToolHandlerError, match="warm authority"):
        owner.resolve(module.DurableToolHandlerBinding.from_dict(binding.to_dict()))
    with pytest.raises(module.ProcessBoundToolHandlerError, match="stable"):
        owner.resolve_stable(binding)
    with pytest.raises(module.ToolHandlerNotFoundError, match="registry"):
        stranger.resolve(binding)


def test_registry_never_falls_back_to_name_module_or_qualname() -> None:
    module = _contract()
    registry = module.DurableToolHandlerRegistry()

    def first(arguments):
        return arguments

    def impostor(arguments):
        return arguments

    impostor.__name__ = first.__name__
    impostor.__module__ = first.__module__
    impostor.__qualname__ = first.__qualname__
    binding = _binding(route_resolver_id=None, route_resolver_revision=None)
    registration = registry.register(
        binding,
        tool=_tool(first),
        handler=first,
    )

    assert registry.resolve(binding).handler is first
    assert registry.resolve(binding).handler is not impostor
    assert registry.verify_resolution(registration).handler is first
    with pytest.raises(TypeError, match="[Bb]inding"):
        registry.resolve(binding.handler_id)


def test_registry_rejects_subclass_and_duck_typed_authority() -> None:
    module = _contract()

    class BindingSubclass(module.DurableToolHandlerBinding):
        pass

    class RegistrySubclass(module.DurableToolHandlerRegistry):
        pass

    class ToolSubclass(Tool):
        pass

    binding = _binding(route_resolver_id=None, route_resolver_revision=None)
    forged = BindingSubclass(
        **{key: value for key, value in binding.to_dict().items() if key != "schema"}
    )
    registry = module.DurableToolHandlerRegistry()

    with pytest.raises(TypeError, match="exact DurableToolHandlerBinding"):
        registry.register(forged, tool=_tool(), handler=_handler)
    with pytest.raises(TypeError, match="exact Tool"):
        registry.register(
            binding,
            tool=ToolSubclass(name="shell", func=_handler),
            handler=_handler,
        )
    with pytest.raises(TypeError, match="subclass"):
        RegistrySubclass()


def test_registry_requires_route_resolver_object_to_match_binding_identity() -> None:
    module = _contract()
    registry = module.DurableToolHandlerRegistry()

    with pytest.raises(module.ToolHandlerRegistryError, match="route resolver"):
        registry.register(_binding(), tool=_tool(), handler=_handler)
    with pytest.raises(module.ToolHandlerRegistryError, match="route resolver"):
        registry.register(
            _binding(route_resolver_id=None, route_resolver_revision=None),
            tool=_tool(),
            handler=_handler,
            route_resolver=object(),
        )


def test_registry_detects_resolution_object_tampering() -> None:
    module = _contract()
    registry = module.DurableToolHandlerRegistry()
    resolver = object()
    resolution = registry.register(
        _binding(),
        tool=_tool(),
        handler=_handler,
        route_resolver=resolver,
    )

    def impostor(arguments):
        return arguments

    object.__setattr__(resolution, "_handler", impostor)
    with pytest.raises(module.ToolHandlerRegistryError, match="changed"):
        registry.verify_resolution(resolution)


def test_registry_rejects_caller_selected_cold_recovery_flag() -> None:
    module = _contract()
    registry = module.DurableToolHandlerRegistry()
    binding = _binding(route_resolver_id=None, route_resolver_revision=None)
    registry.register(binding, tool=_tool(), handler=_handler)

    with pytest.raises(TypeError, match="cold"):
        registry.resolve(binding, cold=True)


def test_registry_rejects_a_binding_mutated_after_validation() -> None:
    module = _contract()
    registry = module.DurableToolHandlerRegistry()
    binding = _binding(route_resolver_id=None, route_resolver_revision=None)
    object.__setattr__(binding, "handler_id", " changed.after.validation ")

    with pytest.raises((TypeError, ValueError), match="canonical|binding"):
        registry.register(binding, tool=_tool(), handler=_handler)


def test_registry_requires_tool_func_to_be_the_single_execution_handler() -> None:
    module = _contract()
    registry = module.DurableToolHandlerRegistry()

    def other(arguments):
        return arguments

    with pytest.raises(module.ToolHandlerRegistryError, match="tool.func"):
        registry.register(
            _binding(route_resolver_id=None, route_resolver_revision=None),
            tool=_tool(),
            handler=other,
        )


def test_registry_cryptographically_binds_config_digest_to_live_tool() -> None:
    module = _contract()
    registry = module.DurableToolHandlerRegistry()
    tool = _tool()
    expected_config_sha256 = module.tool_config_sha256(tool)
    binding = _binding(
        config_sha256=expected_config_sha256,
        route_resolver_id=None,
        route_resolver_revision=None,
    )

    resolution = registry.register(binding, tool=tool, handler=_handler)

    assert len(resolution.tool_descriptor_sha256) == 64
    assert resolution.binding.config_sha256 == expected_config_sha256
    assert resolution.tool_descriptor["provider_neutral_schema"]["name"] == "shell"
    assert set(resolution.tool_descriptor["provider_schemas"]) == {
        "anthropic",
        "hyperspace",
        "ollama",
        "openai",
    }
    with pytest.raises(TypeError):
        resolution.tool_descriptor["provider_neutral_schema"]["name"] = "mutated"

    with pytest.raises(module.ToolHandlerRegistryError, match="config_sha256"):
        module.DurableToolHandlerRegistry().register(
            _binding(
                config_sha256=ZERO_SHA,
                route_resolver_id=None,
                route_resolver_revision=None,
            ),
            tool=_tool(),
            handler=_handler,
        )


def test_registry_enforces_one_binding_per_exact_tool_object() -> None:
    module = _contract()
    registry = module.DurableToolHandlerRegistry()
    tool = _tool()
    first = _binding(
        handler_id="host.first",
        route_resolver_id=None,
        route_resolver_revision=None,
    )
    second = _binding(
        handler_id="host.second",
        route_resolver_id=None,
        route_resolver_revision=None,
    )
    registry.register(first, tool=tool, handler=_handler)

    with pytest.raises(module.ToolHandlerConflictError, match="Tool object"):
        registry.register(second, tool=tool, handler=_handler)

    clone = _tool()
    clone_resolution = registry.register(
        second,
        tool=clone,
        handler=_handler,
    )
    assert clone_resolution.tool is clone


def test_process_bound_registration_is_minted_by_warm_registry_authority() -> None:
    module = _contract()
    owner = module.DurableToolHandlerRegistry()
    fresh = module.DurableToolHandlerRegistry()
    resolution = owner.register_process_bound(
        handler_id="dynamic.plugin.execute",
        revision=3,
        tool=_tool(),
        handler=_handler,
    )
    recovered = module.DurableToolHandlerBinding.from_dict(resolution.binding.to_dict())

    assert owner.resolve(resolution.binding) is resolution
    with pytest.raises(module.ProcessBoundToolHandlerError, match="warm authority"):
        fresh.register(recovered, tool=_tool(), handler=_handler)
    with pytest.raises(module.ToolHandlerNotFoundError, match="registry"):
        fresh.resolve(recovered)


def test_process_bound_binding_bytes_are_scoped_to_one_registry_epoch() -> None:
    module = _contract()
    first_registry = module.DurableToolHandlerRegistry()
    second_registry = module.DurableToolHandlerRegistry()
    first = first_registry.register_process_bound(
        handler_id="dynamic.plugin.same",
        revision=1,
        tool=_tool(),
        handler=_handler,
    )
    second = second_registry.register_process_bound(
        handler_id="dynamic.plugin.same",
        revision=1,
        tool=_tool(),
        handler=_handler,
    )

    assert first.binding.process_epoch != second.binding.process_epoch
    assert first.binding.to_dict() != second.binding.to_dict()
    assert first.binding.sha256 != second.binding.sha256
    assert first_registry.resolve(first.binding) is first
    assert second_registry.resolve(second.binding) is second
    with pytest.raises(module.ToolHandlerNotFoundError, match="registry"):
        first_registry.resolve(second.binding)


@pytest.mark.parametrize(
    "mutator",
    [
        lambda tool: setattr(tool, "name", "changed_name"),
        lambda tool: setattr(tool, "description", "Changed schema description"),
        lambda tool: setattr(tool.parameters[0], "description", "Changed parameter"),
        lambda tool: tool.provider_native_specs.update(
            {"openai": {"type": "computer"}}
        ),
        lambda tool: tool.required_betas.update({"anthropic": ["different-beta"]}),
        lambda tool: setattr(tool, "requires_confirmation", True),
        lambda tool: setattr(tool, "always_load", True),
    ],
)
def test_registry_detects_schema_and_config_relevant_tool_drift(mutator) -> None:
    module = _contract()
    registry = module.DurableToolHandlerRegistry()
    binding = _binding(route_resolver_id=None, route_resolver_revision=None)
    tool = _tool()
    resolution = registry.register(binding, tool=tool, handler=_handler)
    assert len(resolution.tool_descriptor_sha256) == 64

    mutator(tool)

    with pytest.raises(module.ToolHandlerRegistryError, match="changed"):
        registry.resolve(binding)


def test_registry_detects_execution_handler_drift_after_registration() -> None:
    module = _contract()
    registry = module.DurableToolHandlerRegistry()
    binding = _binding(route_resolver_id=None, route_resolver_revision=None)
    tool = _tool()
    registry.register(binding, tool=tool, handler=_handler)

    def impostor(arguments):
        return arguments

    tool.func = impostor
    with pytest.raises(module.ToolHandlerRegistryError, match="changed"):
        registry.resolve(binding)


def test_handler_resolution_is_explicitly_nonserializable_and_boot_bound() -> None:
    module = _contract()
    owner = module.DurableToolHandlerRegistry()
    fresh = module.DurableToolHandlerRegistry()
    resolution = owner.register_process_bound(
        handler_id="dynamic.plugin.execute",
        revision=3,
        tool=_tool(),
        handler=_handler,
    )
    binding = resolution.binding

    with pytest.raises(TypeError, match="serialized"):
        pickle.dumps(resolution)
    with pytest.raises(module.ToolHandlerNotFoundError, match="registry"):
        fresh.resolve(binding)


def test_registry_has_an_atomic_finite_admission_limit() -> None:
    module = _contract()
    registry = module.DurableToolHandlerRegistry(max_registrations=1)
    first = _binding(
        handler_id="host.first",
        route_resolver_id=None,
        route_resolver_revision=None,
    )
    registry.register(first, tool=_tool(), handler=_handler)

    with pytest.raises(module.ToolHandlerRegistryCapacityError, match="capacity"):
        registry.register(
            _binding(
                handler_id="host.second",
                route_resolver_id=None,
                route_resolver_revision=None,
            ),
            tool=_tool(),
            handler=_handler,
        )
    assert registry.registration_count == 1


def test_registry_duplicate_admission_is_atomic_under_concurrency() -> None:
    module = _contract()
    registry = module.DurableToolHandlerRegistry()
    binding = _binding(route_resolver_id=None, route_resolver_revision=None)

    def admit(_index: int) -> str:
        try:
            registry.register(binding, tool=_tool(), handler=_handler)
        except module.ToolHandlerConflictError:
            return "conflict"
        return "registered"

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(admit, range(32)))

    assert outcomes.count("registered") == 1
    assert outcomes.count("conflict") == 31
    assert registry.registration_count == 1


def _set_deep_native_schema(tool: Tool) -> None:
    nested: dict = {"type": "string"}
    for _index in range(20_000):
        nested = {"items": nested}
    tool.provider_native_specs = {"openai": nested}


def _set_circular_icon(tool: Tool) -> None:
    circular: dict = {}
    circular["self"] = circular
    tool.icon = circular


@pytest.mark.parametrize(
    ("mutator", "dimension"),
    [
        (_set_deep_native_schema, "depth"),
        (
            lambda tool: setattr(
                tool,
                "icon_path",
                "x" * (16 * 1024 + 1),
            ),
            "string_bytes",
        ),
        (
            lambda tool: setattr(
                tool,
                "icon",
                {f"key_{index}": "x" * 1024 for index in range(1024)},
            ),
            "bytes",
        ),
        (
            lambda tool: setattr(
                tool,
                "icon",
                {"payload": [[None] * 50 for _index in range(1024)]},
            ),
            "nodes",
        ),
        (
            lambda tool: setattr(
                tool,
                "provider_native_specs",
                {"openai": {"payload": [None] * 1025}},
            ),
            "container_items",
        ),
    ],
)
def test_tool_projection_limits_fail_before_hash_or_partial_registration(
    mutator,
    dimension: str,
) -> None:
    module = _contract()
    registry = module.DurableToolHandlerRegistry()
    tool = _tool()
    mutator(tool)

    with pytest.raises(BoundaryResourceLimitError) as direct_error:
        module.tool_config_sha256(tool)
    assert direct_error.value.dimension == dimension

    with pytest.raises(BoundaryResourceLimitError) as register_error:
        registry.register(
            _binding(route_resolver_id=None, route_resolver_revision=None),
            tool=tool,
            handler=_handler,
        )
    assert register_error.value.dimension == dimension
    _assert_registry_maps_empty(registry)


def test_tool_projection_cycle_is_typed_and_never_partially_registered() -> None:
    module = _contract()
    registry = module.DurableToolHandlerRegistry()
    tool = _tool()
    _set_circular_icon(tool)

    with pytest.raises(module.ToolHandlerRegistryError, match="circular"):
        module.tool_config_sha256(tool)
    with pytest.raises(module.ToolHandlerRegistryError, match="circular"):
        registry.register(
            _binding(route_resolver_id=None, route_resolver_revision=None),
            tool=tool,
            handler=_handler,
        )
    _assert_registry_maps_empty(registry)


def test_descriptor_snapshot_preflights_before_json_copy() -> None:
    module = _contract()
    nested: dict = {"type": "string"}
    for _index in range(20_000):
        nested = {"items": nested}

    with pytest.raises(BoundaryResourceLimitError) as caught:
        module._canonical_snapshot({"schema": nested})

    assert caught.value.dimension == "depth"


@pytest.mark.parametrize("method_name", ["to_json", "to_provider_json"])
def test_registry_rejects_instance_shadowed_projection_methods(
    method_name: str,
) -> None:
    module = _contract()
    registry = module.DurableToolHandlerRegistry()
    tool = _tool()
    setattr(tool, method_name, lambda *_args: {"name": "forged"})

    with pytest.raises(module.ToolHandlerRegistryError, match="shadow"):
        registry.register(
            _binding(route_resolver_id=None, route_resolver_revision=None),
            tool=tool,
            handler=_handler,
        )

    _assert_registry_maps_empty(registry)


def test_fourth_to_json_drift_is_rejected_before_registry_maps(monkeypatch) -> None:
    module = _contract()
    registry = module.DurableToolHandlerRegistry()
    binding = _binding(route_resolver_id=None, route_resolver_revision=None)
    tool = _tool()
    original = Tool.to_json
    calls = 0

    def drifting_to_json(self):
        nonlocal calls
        calls += 1
        payload = original(self)
        if calls == 4:
            payload["description"] = "fourth projection drift"
        return payload

    with monkeypatch.context() as scoped:
        scoped.setattr(Tool, "to_json", drifting_to_json)
        with pytest.raises(module.ToolHandlerRegistryError, match="changed"):
            registry.register(binding, tool=tool, handler=_handler)

    assert calls == 4
    _assert_registry_maps_empty(registry)
    assert registry.register(binding, tool=tool, handler=_handler).tool is tool


def test_to_provider_json_drift_is_rejected_before_registry_maps(
    monkeypatch,
) -> None:
    module = _contract()
    registry = module.DurableToolHandlerRegistry()
    binding = _binding(route_resolver_id=None, route_resolver_revision=None)
    tool = _tool()
    original = Tool.to_provider_json
    calls = 0

    def drifting_provider_json(self, provider=None):
        nonlocal calls
        calls += 1
        payload = original(self, provider)
        if calls == 4:
            payload["description"] = "provider projection drift"
        return payload

    with monkeypatch.context() as scoped:
        scoped.setattr(Tool, "to_provider_json", drifting_provider_json)
        with pytest.raises(module.ToolHandlerRegistryError, match="changed"):
            registry.register(binding, tool=tool, handler=_handler)

    _assert_registry_maps_empty(registry)
    assert registry.register(binding, tool=tool, handler=_handler).tool is tool

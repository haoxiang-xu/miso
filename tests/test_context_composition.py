from __future__ import annotations

from types import SimpleNamespace

import pytest

from unchain.agent import ContextCompositionBootstrapModule
from unchain.agent.builder import AgentBuilder, AgentCallContext
from unchain.agent.model_io import ModelIOFactoryRegistry
from unchain.agent.spec import AgentSpec, AgentState
from unchain.context.composition import (
    ContextCompositionBootstrapHarness,
    ContextCompositionContractError,
    build_internal_context_composition,
)
from unchain.kernel.harness import HarnessContext
from unchain.kernel.state import RunState
from unchain.providers.base import ModelTurnRequest
from unchain.providers.model_turn_runtime import build_model_turn_request
from unchain.providers.prepared_request_factory import (
    resolve_prepared_provider_request_payload,
)
from unchain.tools import Toolkit


PRIVATE_HINT = {
    "category": "skills",
    "subtype": "expanded_invocation",
    "surface": "messages",
    "utf8_bytes": 48,
    "source_count": 1,
}


class _ModelIO:
    provider = "openai"
    model = "gpt-test"


class _PreparedAdapter:
    provider = "openai"
    model = "gpt-test"

    @staticmethod
    def _merged_payload(payload):
        return dict(payload)

    @staticmethod
    def _model_capability(_name, default):
        return default


def _builder() -> AgentBuilder:
    builder = AgentBuilder(
        agent=SimpleNamespace(name="composition-agent"),
        spec=AgentSpec(name="composition-agent", provider="openai", model="gpt-test"),
        state=AgentState(),
        call_context=AgentCallContext(
            mode="run",
            input_messages=[{"role": "user", "content": "hello"}],
        ),
        model_io_registry=ModelIOFactoryRegistry(),
    )
    builder.set_model_io(_ModelIO())
    return builder


def test_public_bootstrap_module_accepts_only_the_private_aggregate_tuple() -> None:
    module = ContextCompositionBootstrapModule.from_private_hint(PRIVATE_HINT)
    builder = _builder()

    module.configure(builder)

    [harness] = [
        item
        for item in builder.harnesses
        if type(item) is ContextCompositionBootstrapHarness
    ]
    assert module.name == "context_composition_bootstrap"
    assert harness.phases == ("before_model",)

    with pytest.raises((TypeError, ValueError)):
        ContextCompositionBootstrapModule.from_private_hint(
            {**PRIVATE_HINT, "source_count": 2}
        )


def test_bootstrap_facts_freeze_into_internal_model_request_only() -> None:
    module = ContextCompositionBootstrapModule.from_private_hint(PRIVATE_HINT)
    builder = _builder()
    module.configure(builder)
    [harness] = [
        item
        for item in builder.harnesses
        if type(item) is ContextCompositionBootstrapHarness
    ]
    state = RunState()
    state.seed_messages([{"role": "user", "content": "hello"}])
    state.provider_state.provider = "openai"
    state.provider_state.model = "gpt-test"
    state.provider_state.max_context_window_tokens = 272_000
    delta = harness.build_delta(HarnessContext(state=state, phase="before_model"))
    state.apply_delta(delta)

    request = build_model_turn_request(state, toolkit=Toolkit())
    manifest = request.internal_context_composition_v1

    assert manifest["schema"] == "unchain.context/internal_context_composition_v1"
    assert manifest["method"] == "utf8_heuristic_v1"
    assert manifest["context_window_tokens"] == 272_000
    assert manifest["routes"][0]["contributions"][0] == PRIVATE_HINT
    with pytest.raises(TypeError):
        manifest["routes"][0]["contributions"][0]["utf8_bytes"] = 1

    state.component_state["context_composition"] = {
        "private_hint": {**PRIVATE_HINT, "utf8_bytes": 999_999}
    }
    unchanged = build_model_turn_request(state, toolkit=Toolkit())
    assert (
        unchanged.internal_context_composition_v1["routes"][0]["contributions"][0][
            "utf8_bytes"
        ]
        == 48
    )


def test_composition_runtime_failure_does_not_block_the_base_model_request(
    monkeypatch,
) -> None:
    state = RunState()
    state.seed_messages([{"role": "user", "content": "hello"}])

    def fail_composition(*_args, **_kwargs):
        raise RuntimeError("composition-only runtime mutant")

    monkeypatch.setattr(
        "unchain.context.composition.build_internal_context_composition",
        fail_composition,
    )

    request = build_model_turn_request(state, toolkit=Toolkit())

    assert request.messages
    assert request.internal_context_composition_v1 is None


def test_malformed_composition_builder_result_is_omitted_from_the_base_request(
    monkeypatch,
) -> None:
    state = RunState()
    state.seed_messages([{"role": "user", "content": "hello"}])
    monkeypatch.setattr(
        "unchain.context.composition.build_internal_context_composition",
        lambda *_args, **_kwargs: {"schema": "mutant"},
    )

    request = build_model_turn_request(state, toolkit=Toolkit())

    assert request.messages
    assert request.internal_context_composition_v1 is None


def test_internal_carrier_is_excluded_from_prepared_provider_bytes_and_digest() -> None:
    baseline = ModelTurnRequest(
        messages=[{"role": "user", "content": "hello"}],
        payload={"temperature": 0},
    )
    enriched = ModelTurnRequest(
        messages=[{"role": "user", "content": "hello"}],
        payload={"temperature": 0},
        internal_context_composition_v1={
            "schema": "unchain.context/internal_context_composition_v1",
            "method": "utf8_heuristic_v1",
            "context_window_tokens": 272_000,
            "routes": [
                {
                    "route_name": "primary",
                    "context_mode": "semantic",
                    "provider_retained": False,
                    "manifest_items": 1,
                    "wire_surfaces": 1,
                    "contributions": [PRIVATE_HINT],
                }
            ],
        },
    )

    baseline_payload = resolve_prepared_provider_request_payload(
        model_io=_PreparedAdapter(),
        request=baseline,
    )
    enriched_payload = resolve_prepared_provider_request_payload(
        model_io=_PreparedAdapter(),
        request=enriched,
    )

    assert enriched_payload == baseline_payload


def test_private_hint_projects_to_exact_remote_and_fallback_surfaces() -> None:
    state = RunState()
    state.metadata["context_composition_source_v1"] = {
        "schema": "unchain.context/context_composition_source_v1",
        "contributions": [PRIVATE_HINT],
    }
    assembly = SimpleNamespace(
        mode="remote_continuation",
        previous_response_id="response-previous",
        fallback_messages=[{"role": "user", "content": "local replay"}],
    )

    manifest = build_internal_context_composition(state, assembly)
    primary, fallback = manifest["routes"]

    assert primary["route_name"] == "primary"
    assert primary["context_mode"] == "remote_continuation"
    assert primary["provider_retained"] is True
    assert primary["contributions"][0]["surface"] == "provider_state"
    assert fallback["route_name"] == "openai_previous_response_fallback"
    assert fallback["context_mode"] == "local_replay"
    assert fallback["provider_retained"] is False
    assert fallback["contributions"][0]["surface"] == "messages"


def test_route_totals_include_structured_uninstrumented_messages_and_tools() -> None:
    state = RunState()
    state.seed_messages(
        [
            {"role": "system", "content": "system rules"},
            {"role": "user", "content": "expanded request"},
        ]
    )
    state.metadata["context_composition_source_v1"] = {
        "schema": "unchain.context/context_composition_source_v1",
        "contributions": [PRIVATE_HINT],
    }
    state.provider_state.provider = "openai"
    toolkit = Toolkit()

    @toolkit.tool
    def lookup(query: str) -> str:
        return query

    request = build_model_turn_request(state, toolkit=toolkit)
    [route] = request.internal_context_composition_v1["routes"]

    assert route["manifest_items"] == 4
    assert route["wire_surfaces"] == 2
    assert route["contributions"] == (PRIVATE_HINT,)


def test_identical_private_tuples_merge_with_checked_arithmetic() -> None:
    state = RunState()
    state.metadata["context_composition_source_v1"] = {
        "schema": "unchain.context/context_composition_source_v1",
        "contributions": [
            PRIVATE_HINT,
            {**PRIVATE_HINT, "utf8_bytes": 16},
        ],
    }
    assembly = SimpleNamespace(
        mode="semantic",
        previous_response_id=None,
        fallback_messages=None,
        messages=[{"role": "user", "content": "request"}],
    )

    manifest = build_internal_context_composition(state, assembly)
    [route] = manifest["routes"]

    assert route["manifest_items"] == 3
    assert route["wire_surfaces"] == 1
    assert route["contributions"] == (
        {
            "category": "skills",
            "subtype": "expanded_invocation",
            "surface": "messages",
            "utf8_bytes": 64,
            "source_count": 2,
        },
    )

    state.metadata["context_composition_source_v1"]["contributions"] = [
        {**PRIVATE_HINT, "utf8_bytes": (1 << 53) - 1},
        {**PRIVATE_HINT, "utf8_bytes": 1},
    ]
    with pytest.raises(ContextCompositionContractError, match="JSON safe integer"):
        build_internal_context_composition(state, assembly)

from __future__ import annotations

from dataclasses import replace

import pytest

from unchain.journal import AttemptRef, GenerationRef
from unchain.persistence import SQLiteContextV2Store
from unchain.providers import (
    AnthropicModelIO,
    HyperspaceModelIO,
    OllamaModelIO,
    OpenAIModelIO,
)
from unchain.providers.base import ModelTurnRequest
from unchain.tools import Toolkit


ATTEMPT = AttemptRef(
    GenerationRef("execution-provider-host", "generation-provider-host"),
    "attempt-provider-host",
)
TARGET_SHA256 = "a" * 64


def _model_io(provider: str):
    if provider == "openai":
        return OpenAIModelIO(
            model="gpt-test",
            api_key="test-key",
            client_factory=lambda **kwargs: kwargs,
        )
    if provider == "anthropic":
        return AnthropicModelIO(
            model="claude-test",
            api_key="test-key",
            client_factory=lambda **kwargs: kwargs,
        )
    if provider == "hyperspace":
        return HyperspaceModelIO(
            model="claude-test",
            api_key="test-key",
            base_url="https://example.invalid/anthropic",
            client_factory=lambda **kwargs: kwargs,
        )
    if provider == "ollama":
        return OllamaModelIO(model="llama-test")
    raise AssertionError(provider)


def _request() -> ModelTurnRequest:
    return ModelTurnRequest(
        messages=[{"role": "user", "content": "persist this exact turn"}],
        payload={},
        run_id=ATTEMPT.attempt_id,
        iteration=3,
        toolkit=Toolkit(),
    )


def _tool_request() -> ModelTurnRequest:
    toolkit = Toolkit()
    toolkit.register(
        lambda query="": {"query": query},
        name="probe",
        description="Probe provider authority",
    )
    return replace(_request(), toolkit=toolkit)


def _store(tmp_path):
    return SQLiteContextV2Store(
        database_path=tmp_path / "memory_v2" / "context_v2.sqlite3",
        object_directory=tmp_path / "memory_v2" / "objects",
    )


@pytest.mark.parametrize(
    "provider",
    ["openai", "anthropic", "hyperspace", "ollama"],
)
def test_authority_service_persists_and_recovers_exact_provider_wire(
    tmp_path,
    provider,
):
    from unchain.context.provider_turns import ProviderTurnAuthorityService

    first_repository = _store(tmp_path).bind_execution(ATTEMPT.generation.execution_id)
    first = ProviderTurnAuthorityService(
        attempt=ATTEMPT,
        store=first_repository,
        transport_target_sha256=TARGET_SHA256,
    ).prepare(
        model_io=_model_io(provider),
        request=_request(),
    )

    reopened_repository = _store(tmp_path).bind_execution(
        ATTEMPT.generation.execution_id
    )
    recovered = ProviderTurnAuthorityService(
        attempt=ATTEMPT,
        store=reopened_repository,
        transport_target_sha256=TARGET_SHA256,
    ).prepare(
        model_io=_model_io(provider),
        request=_request(),
    )

    assert recovered.envelope == first.envelope
    assert recovered.catalog == first.catalog
    assert recovered.artifact == first.artifact
    assert (
        len(
            reopened_repository.lookup_tool_catalog_receipts(
                attempt=ATTEMPT,
                iteration=3,
            ).events
        )
        == 1
    )
    assert (
        len(
            reopened_repository.lookup_provider_wire_receipts(
                attempt=ATTEMPT,
                iteration=3,
            ).events
        )
        == 1
    )


def test_authority_service_rejects_request_drift_for_existing_turn(tmp_path):
    from unchain.context.provider_turns import (
        ProviderTurnAuthorityConflict,
        ProviderTurnAuthorityService,
    )

    repository = _store(tmp_path).bind_execution(ATTEMPT.generation.execution_id)
    service = ProviderTurnAuthorityService(
        attempt=ATTEMPT,
        store=repository,
        transport_target_sha256=TARGET_SHA256,
    )
    service.prepare(model_io=_model_io("openai"), request=_request())

    changed = replace(
        _request(),
        messages=[{"role": "user", "content": "changed request"}],
    )
    with pytest.raises(ProviderTurnAuthorityConflict, match="changed"):
        service.prepare(model_io=_model_io("openai"), request=changed)


def test_authority_service_requires_an_official_tool_resolution_adapter(tmp_path):
    from unchain.context.provider_turns import (
        ProviderTurnToolAdapterRequired,
        ProviderTurnAuthorityService,
    )

    toolkit = Toolkit()
    toolkit.register(lambda: "ok", name="probe")
    request = replace(_request(), toolkit=toolkit)
    service = ProviderTurnAuthorityService(
        attempt=ATTEMPT,
        store=_store(tmp_path).bind_execution(ATTEMPT.generation.execution_id),
        transport_target_sha256=TARGET_SHA256,
    )

    with pytest.raises(ProviderTurnToolAdapterRequired, match="tool"):
        service.prepare(model_io=_model_io("openai"), request=request)


def test_authority_service_recovers_tool_catalog_with_fresh_official_adapter(
    tmp_path,
):
    from unchain.context.provider_toolkit import ProviderToolkitAuthorityAdapter
    from unchain.context.provider_turns import ProviderTurnAuthorityService

    first = ProviderTurnAuthorityService(
        attempt=ATTEMPT,
        store=_store(tmp_path).bind_execution(ATTEMPT.generation.execution_id),
        transport_target_sha256=TARGET_SHA256,
        toolkit_adapter=ProviderToolkitAuthorityAdapter(),
    ).prepare(
        model_io=_model_io("openai"),
        request=_tool_request(),
    )

    recovered = ProviderTurnAuthorityService(
        attempt=ATTEMPT,
        store=_store(tmp_path).bind_execution(ATTEMPT.generation.execution_id),
        transport_target_sha256=TARGET_SHA256,
        toolkit_adapter=ProviderToolkitAuthorityAdapter(),
    ).recover_existing(
        model_io=_model_io("openai"),
        request=_tool_request(),
    )

    assert recovered is not None
    assert recovered.envelope == first.envelope
    assert [entry.tool_name for entry in recovered.catalog.entries] == ["probe"]


def test_recover_existing_is_read_only_and_returns_only_a_complete_authority(
    tmp_path,
):
    from unchain.context.provider_turns import ProviderTurnAuthorityService

    repository = _store(tmp_path).bind_execution(ATTEMPT.generation.execution_id)
    service = ProviderTurnAuthorityService(
        attempt=ATTEMPT,
        store=repository,
        transport_target_sha256=TARGET_SHA256,
    )

    assert (
        service.recover_existing(
            model_io=_model_io("openai"),
            request=_request(),
        )
        is None
    )
    assert repository.capture_snapshot().events == ()

    persisted = service.prepare(
        model_io=_model_io("openai"),
        request=_request(),
    )
    recovered = service.recover_existing(
        model_io=_model_io("openai"),
        request=_request(),
    )

    assert recovered.envelope == persisted.envelope
    assert len(repository.capture_snapshot().events) == 2

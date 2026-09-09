from __future__ import annotations

import pytest

from unchain.context.tool_catalog import ToolCatalogEnvelope as _ToolCatalogEnvelope
from unchain.journal import AttemptRef, GenerationRef
from unchain.providers.physical_send import (
    ProviderPhysicalSendContext,
    provider_physical_ordinal,
)
from unchain.providers.request_lease import ProviderRequestSubject
from unchain.providers.wire_envelope import ProviderWireRoute

del _ToolCatalogEnvelope


def _subject(route: str, retry_ordinal: int) -> ProviderRequestSubject:
    return ProviderRequestSubject(
        attempt=AttemptRef(
            generation=GenerationRef("execution-physical", "generation-physical"),
            attempt_id="attempt-physical",
        ),
        iteration=3,
        envelope_sha256="a" * 64,
        route=route,
        retry_ordinal=retry_ordinal,
    )


@pytest.mark.parametrize(
    ("route", "retry_ordinal", "physical_ordinal"),
    [
        ("primary", 0, 0),
        ("primary", 1, 1),
        ("openai_previous_response_fallback", 0, 1),
        ("openai_previous_response_fallback", 1, 2),
    ],
)
def test_provider_physical_ordinal_is_deterministic_across_routes(
    route: str,
    retry_ordinal: int,
    physical_ordinal: int,
) -> None:
    assert provider_physical_ordinal(_subject(route, retry_ordinal)) == physical_ordinal


def test_physical_send_context_binds_exact_subject_route_and_ordinal() -> None:
    subject = _subject("openai_previous_response_fallback", 0)
    route = ProviderWireRoute(
        name="openai_previous_response_fallback",
        request={"model": "gpt-test", "input": [{"role": "user", "content": "x"}]},
    )

    context = ProviderPhysicalSendContext(
        subject=subject,
        route=route,
        physical_ordinal=1,
    )

    assert context.subject == subject
    assert context.route == route
    assert context.physical_ordinal == 1

    with pytest.raises(ValueError, match="physical ordinal"):
        ProviderPhysicalSendContext(
            subject=subject,
            route=route,
            physical_ordinal=0,
        )

    with pytest.raises(ValueError, match="route"):
        ProviderPhysicalSendContext(
            subject=subject,
            route=ProviderWireRoute(name="primary", request={"model": "gpt-test"}),
            physical_ordinal=1,
        )

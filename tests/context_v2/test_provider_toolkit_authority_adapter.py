from __future__ import annotations

from unchain.tools import Toolkit


def _probe_handler(query: str = "") -> dict[str, str]:
    return {"query": query}


def _toolkit() -> Toolkit:
    toolkit = Toolkit()
    toolkit.register(
        _probe_handler,
        name="probe",
        description="Probe durable toolkit authority",
    )
    return toolkit


def test_adapter_builds_restart_stable_resolutions_for_equivalent_toolkits():
    from unchain.context.provider_toolkit import ProviderToolkitAuthorityAdapter
    from unchain.tools.handler_registry import DurableToolHandlerKind

    first_registry, first_resolutions = ProviderToolkitAuthorityAdapter().resolve(
        _toolkit()
    )
    second_registry, second_resolutions = ProviderToolkitAuthorityAdapter().resolve(
        _toolkit()
    )

    assert (
        first_registry.verify_resolution(first_resolutions[0]) is first_resolutions[0]
    )
    assert (
        second_registry.verify_resolution(second_resolutions[0])
        is second_resolutions[0]
    )
    assert first_resolutions[0].binding == second_resolutions[0].binding
    assert first_resolutions[0].tool_descriptor_sha256 == (
        second_resolutions[0].tool_descriptor_sha256
    )
    assert first_resolutions[0].binding.kind is DurableToolHandlerKind.STABLE


def test_adapter_preserves_toolkit_order_and_rejects_key_name_drift():
    import pytest

    from unchain.context.provider_toolkit import (
        ProviderToolkitAuthorityAdapter,
        ProviderToolkitAuthorityError,
    )

    toolkit = _toolkit()
    toolkit.register(lambda: "second", name="second")
    _registry, resolutions = ProviderToolkitAuthorityAdapter().resolve(toolkit)

    assert [resolution.tool.name for resolution in resolutions] == [
        "probe",
        "second",
    ]

    toolkit.tools["changed-key"] = toolkit.tools.pop("probe")
    with pytest.raises(ProviderToolkitAuthorityError, match="name"):
        ProviderToolkitAuthorityAdapter().resolve(toolkit)

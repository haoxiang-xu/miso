from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from unchain.kernel.run_ledger import build_model_attempt_receipt
from unchain.kernel.types import ModelTurnResult
from unchain.pricing_catalog import (
    PRICING_PROJECTION_PATH_ENV,
    PRICING_PROJECTION_SHA256_ENV,
    PricingCatalogError,
    PricingCatalogResolver,
    load_pinned_pricing_catalog,
    resolve_pricing_for_receipt,
)
from unchain.run_bundle import (
    ProviderCallIdentity,
    ProviderCallReceipt,
    ProviderCallTiming,
    ProviderCallUsage,
    RunIdentity,
)


_EXACT_PRICE_IDENTITY = {
    "billing_surface": "first_party_api",
    "batch": False,
    "inference_geo": "global",
}


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _rates(**overrides: int | None) -> dict[str, int | None]:
    value: dict[str, int | None] = {
        "input_uncached": None,
        "input_cache_read": None,
        "input_cache_write": None,
        "input_cache_write_5m": None,
        "input_cache_write_1h": None,
        "output": None,
    }
    value.update(overrides)
    return value


def _catalog(
    *,
    version: str = "synthetic-test-history-v1",
    openai_input_rate: int = 1_000_000_000,
) -> dict[str, object]:
    return {
        "schema": "pupu.pricing_catalog.v1",
        "catalog_version": version,
        "currency": "USD",
        "effective_from": "2026-08-13T00:00:00Z",
        "effective_to": "2026-09-01T00:00:00Z",
        "retrieved_at": "2026-08-13T12:00:00Z",
        "sources": [
            {
                "provider": "openai",
                "url": "https://developers.openai.com/api/docs/pricing",
                "retrieved_at": "2026-08-13T12:00:00Z",
                "source_digest": "a" * 64,
                "review_note": "synthetic contract fixture; allowlisted provenance URL only",
            },
            {
                "provider": "anthropic",
                "url": "https://platform.claude.com/docs/en/about-claude/pricing",
                "retrieved_at": "2026-08-13T12:00:00Z",
                "source_digest": "b" * 64,
                "review_note": "synthetic contract fixture; allowlisted provenance URL only",
            },
        ],
        "entries": [
            {
                "provider": "openai",
                "billing_surface": "first_party_api",
                "model": "synthetic-openai-model-v1",
                "service_tier": "standard",
                "batch": False,
                "inference_geo": "global",
                "currency": "USD",
                "rates_nano_usd_per_million": _rates(
                    input_uncached=openai_input_rate,
                    input_cache_read=100_000_000,
                    input_cache_write=1_250_000_000,
                    output=6_000_000_000,
                ),
                "long_context_rule": {
                    "threshold_input_tokens": 272_000,
                    "input_multiplier_ppm": 2_000_000,
                    "output_multiplier_ppm": 1_500_000,
                },
                "source_index": 0,
            },
            {
                "provider": "anthropic",
                "billing_surface": "first_party_api",
                "model": "synthetic-anthropic-model-v1",
                "service_tier": "standard",
                "batch": False,
                "inference_geo": "global",
                "currency": "USD",
                "rates_nano_usd_per_million": _rates(
                    input_uncached=3_000_000_000,
                    input_cache_read=300_000_000,
                    input_cache_write_5m=3_750_000_000,
                    input_cache_write_1h=6_000_000_000,
                    output=15_000_000_000,
                ),
                "long_context_rule": None,
                "source_index": 1,
            },
        ],
    }


def _projection(catalog: dict[str, object]) -> dict[str, object]:
    body: dict[str, object] = {
        "schema": "pupu.verified_pricing_catalog_projection.v1",
        "verification": {
            "algorithm": "ed25519",
            "key_id": "fixture-release-key",
            "trusted_public_key_sha256": "c" * 64,
            "catalog_payload_sha256": _canonical_sha256(catalog),
        },
        "catalog": catalog,
    }
    return {**body, "projection_sha256": _canonical_sha256(body)}


def _write_projection(
    tmp_path: Path,
    *,
    version: str = "synthetic-test-history-v1",
    openai_input_rate: int = 1_000_000_000,
    filename: str = "projection.json",
) -> tuple[Path, str]:
    projection = _projection(
        _catalog(version=version, openai_input_rate=openai_input_rate)
    )
    target = tmp_path / filename
    target.write_text(json.dumps(projection, ensure_ascii=False), encoding="utf-8")
    return target, str(projection["projection_sha256"])


def _identity(request_digit: str = "1") -> ProviderCallIdentity:
    return ProviderCallIdentity(
        execution_id="execution-pricing",
        attempt_id="attempt-pricing",
        root_run_id="root-pricing",
        owner_run_id="root-pricing",
        parent_run_id=None,
        iteration=1,
        retry_ordinal=0,
        purpose="agent_turn",
        request_sha256=request_digit * 64,
        route="primary",
    )


def _receipt(
    *,
    provider: str = "openai",
    model: str = "synthetic-openai-model-v1",
    usage: ProviderCallUsage,
    service_tier: str | None = "standard",
    request_digit: str = "1",
) -> ProviderCallReceipt:
    return ProviderCallReceipt(
        identity=_identity(request_digit),
        provider=provider,
        model=model,
        service_tier=service_tier,
        status="completed",
        timing=ProviderCallTiming(
            started_at="2026-08-13T18:00:00Z",
            completed_at="2026-08-13T18:00:01Z",
        ),
        usage=usage,
    )


def test_run_receipt_builder_attaches_only_exact_pinned_pricing(
    tmp_path,
    monkeypatch,
) -> None:
    projection_path, projection_sha256 = _write_projection(tmp_path)
    monkeypatch.setenv(PRICING_PROJECTION_PATH_ENV, str(projection_path))
    monkeypatch.setenv(PRICING_PROJECTION_SHA256_ENV, projection_sha256)
    identity = RunIdentity(
        execution_id="execution-pricing",
        attempt_id="attempt-pricing",
        root_run_id="root-pricing",
        run_id="root-pricing",
        parent_run_id=None,
        relation="root",
    )
    turn = ModelTurnResult(
        assistant_messages=[],
        tool_calls=[],
        provider_call_usage=ProviderCallUsage(
            input_uncached_tokens=1_000,
            input_cache_read_tokens=0,
            input_cache_write_tokens=0,
            input_cache_write_5m_tokens=0,
            input_cache_write_1h_tokens=0,
            input_total_tokens=1_000,
            output_visible_tokens=1_000,
            output_reasoning_tokens=0,
            output_total_tokens=1_000,
            total_tokens=2_000,
            source="provider_observed",
        ),
    )
    common = {
        "identity": identity,
        "provider": "openai",
        "model": "synthetic-openai-model-v1",
        "iteration": 1,
        "retry_ordinal": 0,
        "purpose": "agent_turn",
        "request_digest": "f" * 64,
        "route": "openai.responses.create",
        "started_at": "2026-08-13T18:00:00Z",
        "completed_at": "2026-08-13T18:00:01Z",
        "turn": turn,
    }
    priced = build_model_attempt_receipt(
        **common,
        payload={
            "service_tier": "standard",
            "billing_surface": "first_party_api",
            "batch": False,
            "inference_geo": "global",
        },
    )
    assert priced.pricing.status == "estimated"
    assert priced.pricing.snapshot is not None
    assert priced.pricing.amount_nano_usd == 7_000_000

    monkeypatch.delenv(PRICING_PROJECTION_PATH_ENV)
    monkeypatch.delenv(PRICING_PROJECTION_SHA256_ENV)
    unavailable = build_model_attempt_receipt(
        **{**common, "request_digest": "e" * 64},
        payload={"service_tier": "standard"},
    )
    assert unavailable.pricing.status == "unavailable"


def _openai_usage(*, input_tokens: int, output_tokens: int) -> ProviderCallUsage:
    return ProviderCallUsage(
        input_uncached_tokens=input_tokens,
        input_cache_read_tokens=0,
        input_cache_write_tokens=0,
        input_cache_write_5m_tokens=None,
        input_cache_write_1h_tokens=None,
        input_total_tokens=input_tokens,
        output_visible_tokens=output_tokens,
        output_reasoning_tokens=0,
        output_total_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        source="provider_observed_partial",
    )


def test_default_and_partial_configuration_are_unavailable(tmp_path: Path) -> None:
    receipt = _receipt(usage=_openai_usage(input_tokens=1, output_tokens=1))
    unconfigured = resolve_pricing_for_receipt(
        receipt,
        occurred_at="2026-08-13T13:00:00Z",
        environment={},
        **_EXACT_PRICE_IDENTITY,
    )
    assert unconfigured.status == "unavailable"
    assert unconfigured.reason == "pricing_catalog_unconfigured"

    projection_path, _pin = _write_projection(tmp_path)
    incomplete = resolve_pricing_for_receipt(
        receipt,
        occurred_at="2026-08-13T13:00:00Z",
        environment={PRICING_PROJECTION_PATH_ENV: str(projection_path)},
        **_EXACT_PRICE_IDENTITY,
    )
    assert incomplete.reason == "pricing_catalog_trust_configuration_incomplete"

    configured_environment = {
        PRICING_PROJECTION_PATH_ENV: str(projection_path),
        PRICING_PROJECTION_SHA256_ENV: _pin,
    }
    assert PricingCatalogResolver.from_environment(
        configured_environment
    ) is PricingCatalogResolver.from_environment(configured_environment)


def test_pinned_projection_prices_long_context_and_preserves_source_digest(
    tmp_path: Path,
) -> None:
    projection_path, pin = _write_projection(tmp_path)
    resolver = load_pinned_pricing_catalog(
        projection_path, expected_projection_sha256=pin
    )
    price = resolve_pricing_for_receipt(
        _receipt(usage=_openai_usage(input_tokens=300_000, output_tokens=100_000)),
        occurred_at="2026-08-13T13:00:00Z",
        resolver=resolver,
        **_EXACT_PRICE_IDENTITY,
    )
    assert price.status == "estimated"
    assert price.amount_nano_usd == 1_500_000_000
    assert price.input_multiplier_ppm == 2_000_000
    assert price.output_multiplier_ppm == 1_500_000
    assert price.snapshot is not None
    assert price.snapshot.catalog_sha256 == resolver.catalog_payload_sha256
    assert price.snapshot.source_sha256 == "a" * 64
    assert price.snapshot.source_url == (
        "https://developers.openai.com/api/docs/pricing"
    )


def test_anthropic_cache_write_ttl_rates_remain_disjoint(tmp_path: Path) -> None:
    projection_path, pin = _write_projection(tmp_path)
    resolver = PricingCatalogResolver.from_projection_file(
        projection_path, expected_projection_sha256=pin
    )
    usage = ProviderCallUsage(
        input_uncached_tokens=1_000,
        input_cache_read_tokens=1_000,
        input_cache_write_tokens=2_000,
        input_cache_write_5m_tokens=1_000,
        input_cache_write_1h_tokens=1_000,
        input_total_tokens=4_000,
        output_visible_tokens=1_000,
        output_reasoning_tokens=0,
        output_total_tokens=1_000,
        total_tokens=5_000,
        source="provider_observed",
    )
    price = resolve_pricing_for_receipt(
        _receipt(
            provider="anthropic",
            model="synthetic-anthropic-model-v1",
            usage=usage,
            request_digit="2",
        ),
        occurred_at="2026-08-13T13:00:00Z",
        resolver=resolver,
        **_EXACT_PRICE_IDENTITY,
    )
    assert price.status == "estimated"
    assert price.amount_nano_usd == 28_050_000
    assert price.snapshot is not None
    assert price.snapshot.input_cache_write_nano_usd_per_million is None
    assert price.snapshot.input_cache_write_5m_nano_usd_per_million == 3_750_000_000
    assert price.snapshot.input_cache_write_1h_nano_usd_per_million == 6_000_000_000


def test_unknown_identity_time_and_service_tier_fail_closed(tmp_path: Path) -> None:
    projection_path, pin = _write_projection(tmp_path)
    resolver = PricingCatalogResolver.from_projection_file(
        projection_path, expected_projection_sha256=pin
    )
    usage = _openai_usage(input_tokens=1, output_tokens=1)
    unknown_model = resolve_pricing_for_receipt(
        _receipt(model="moving-alias", usage=usage),
        occurred_at="2026-08-13T13:00:00Z",
        resolver=resolver,
        **_EXACT_PRICE_IDENTITY,
    )
    assert unknown_model.reason == "pricing_identity_unknown"
    outside_interval = resolve_pricing_for_receipt(
        _receipt(usage=usage),
        occurred_at="2026-09-01T00:00:00Z",
        resolver=resolver,
        **_EXACT_PRICE_IDENTITY,
    )
    assert outside_interval.reason == "pricing_catalog_not_effective"
    tier_unknown = resolve_pricing_for_receipt(
        _receipt(usage=usage, service_tier=None),
        occurred_at="2026-08-13T13:00:00Z",
        resolver=resolver,
        **_EXACT_PRICE_IDENTITY,
    )
    assert tier_unknown.reason == "pricing_service_tier_unknown"
    time_unknown = resolve_pricing_for_receipt(
        _receipt(usage=usage), resolver=resolver, **_EXACT_PRICE_IDENTITY
    )
    assert time_unknown.reason == "pricing_time_unknown"
    surface_unknown = resolve_pricing_for_receipt(
        _receipt(usage=usage),
        occurred_at="2026-08-13T13:00:00Z",
        resolver=resolver,
    )
    assert surface_unknown.reason == "pricing_billing_surface_unknown"
    invalid_time = resolve_pricing_for_receipt(
        _receipt(usage=usage),
        occurred_at="2026-99-13T13:00:00Z",
        resolver=resolver,
        **_EXACT_PRICE_IDENTITY,
    )
    assert invalid_time.reason == "pricing_time_invalid"


def test_projection_tamper_and_wrong_pin_are_rejected(tmp_path: Path) -> None:
    projection_path, pin = _write_projection(tmp_path)
    with pytest.raises(PricingCatalogError, match="projection digest is not pinned"):
        load_pinned_pricing_catalog(
            projection_path, expected_projection_sha256="0" * 64
        )

    projection = json.loads(projection_path.read_text(encoding="utf-8"))
    projection["catalog"]["sources"][0]["source_digest"] = "f" * 64
    projection_path.write_text(json.dumps(projection), encoding="utf-8")
    with pytest.raises(PricingCatalogError, match="catalog payload digest changed"):
        load_pinned_pricing_catalog(
            projection_path, expected_projection_sha256=pin
        )


def test_historical_pin_is_not_repriced_by_a_new_catalog(tmp_path: Path) -> None:
    v1_path, v1_pin = _write_projection(tmp_path, filename="v1.json")
    v2_path, v2_pin = _write_projection(
        tmp_path,
        version="synthetic-test-history-v2",
        openai_input_rate=2_000_000_000,
        filename="v2.json",
    )
    v1 = load_pinned_pricing_catalog(v1_path, expected_projection_sha256=v1_pin)
    v2 = load_pinned_pricing_catalog(v2_path, expected_projection_sha256=v2_pin)
    receipt = _receipt(usage=_openai_usage(input_tokens=300_000, output_tokens=0))
    historical = resolve_pricing_for_receipt(
        receipt,
        occurred_at="2026-08-13T13:00:00Z",
        resolver=v1,
        **_EXACT_PRICE_IDENTITY,
    )
    current = resolve_pricing_for_receipt(
        receipt,
        occurred_at="2026-08-13T13:00:00Z",
        resolver=v2,
        **_EXACT_PRICE_IDENTITY,
    )
    historical_again = resolve_pricing_for_receipt(
        receipt,
        occurred_at="2026-08-13T13:00:00Z",
        resolver=v1,
        **_EXACT_PRICE_IDENTITY,
    )
    assert historical.amount_nano_usd == 600_000_000
    assert historical_again == historical
    assert current.amount_nano_usd == 1_200_000_000
    assert v1.projection_sha256 != v2.projection_sha256
    assert historical.snapshot is not None and current.snapshot is not None
    assert historical.snapshot.snapshot_id != current.snapshot.snapshot_id


def test_fractional_effective_boundary_uses_nanosecond_ordering(
    tmp_path: Path,
) -> None:
    catalog = _catalog()
    catalog["effective_from"] = "2026-08-13T00:00:00Z"
    catalog["effective_to"] = "2026-08-13T00:00:00.000000001Z"
    projection = _projection(catalog)
    projection_path = tmp_path / "fractional.json"
    projection_path.write_text(json.dumps(projection), encoding="utf-8")
    resolver = load_pinned_pricing_catalog(
        projection_path,
        expected_projection_sha256=str(projection["projection_sha256"]),
    )
    receipt = _receipt(usage=_openai_usage(input_tokens=0, output_tokens=0))
    inside = resolve_pricing_for_receipt(
        receipt,
        occurred_at="2026-08-13T00:00:00Z",
        resolver=resolver,
        **_EXACT_PRICE_IDENTITY,
    )
    boundary = resolve_pricing_for_receipt(
        receipt,
        occurred_at="2026-08-13T00:00:00.000000001Z",
        resolver=resolver,
        **_EXACT_PRICE_IDENTITY,
    )
    assert inside.status == "estimated"
    assert boundary.reason == "pricing_catalog_not_effective"

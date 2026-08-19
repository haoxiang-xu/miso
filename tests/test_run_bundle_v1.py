from __future__ import annotations

import json
from dataclasses import fields
from types import SimpleNamespace

import pytest

from unchain.journal.models import AttemptRef, GenerationRef
from unchain.journal.provider_result import ProviderTurnResultEnvelope
from unchain.kernel import ModelTurnRequest
from unchain.kernel.types import ModelTurnResult
from unchain.providers.anthropic import AnthropicModelIO
from unchain.providers.ollama import OllamaModelIO
from unchain.providers.openai import OpenAIModelIO
from unchain.providers.canonical_hash import canonical_json_sha256
from unchain.providers.request_lease import ProviderRequestSubject
from unchain.run_bundle import (
    LegacyAttribution,
    PricingSnapshotRef,
    ProviderCallIdentity,
    ProviderBillingDimensions,
    ProviderCallIds,
    ProviderCallPricing,
    ProviderCallReceipt,
    ProviderCallTiming,
    ProviderCallUsage,
    RunBundle,
    RunBundleProtocolError,
    RunBundleReducer,
    RunChild,
    RunIdentity,
    RunLifecycle,
    RunMetricCounters,
    RunMetricEvent,
    canonical_sha256,
    deterministic_bundle_id,
    reproject_run_bundle_extensions,
)


def _usage(*, input_tokens: int, output_tokens: int) -> ProviderCallUsage:
    return ProviderCallUsage(
        input_uncached_tokens=input_tokens,
        input_cache_read_tokens=0,
        input_cache_write_tokens=0,
        input_cache_write_5m_tokens=0,
        input_cache_write_1h_tokens=0,
        input_total_tokens=input_tokens,
        output_visible_tokens=output_tokens,
        output_reasoning_tokens=0,
        output_total_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        source="provider_observed",
    )


def _lifecycle(status: str = "completed") -> RunLifecycle:
    return RunLifecycle(
        status=status,
        started_at="2026-08-13T18:00:00Z",
        completed_at=(
            None
            if status in {"running", "uncertain"}
            else "2026-08-13T18:01:00Z"
        ),
    )


def _timing() -> ProviderCallTiming:
    return ProviderCallTiming(
        started_at="2026-08-13T18:00:00Z",
        completed_at="2026-08-13T18:00:01Z",
    )


def _call_identity(
    *,
    owner_run_id: str,
    parent_run_id: str | None,
    request_digit: str,
    iteration: int = 1,
    attempt_id: str = "attempt-1",
) -> ProviderCallIdentity:
    return ProviderCallIdentity(
        execution_id="execution-1",
        attempt_id=attempt_id,
        root_run_id="root-1",
        owner_run_id=owner_run_id,
        parent_run_id=parent_run_id,
        iteration=iteration,
        retry_ordinal=0,
        purpose="agent_turn",
        request_sha256=request_digit * 64,
        route="primary",
    )


def _receipt(
    *,
    owner_run_id: str,
    parent_run_id: str | None,
    request_digit: str,
    input_tokens: int,
    output_tokens: int,
    provider: str = "openai",
    model: str = "gpt-test",
    attempt_id: str = "attempt-1",
) -> ProviderCallReceipt:
    return ProviderCallReceipt(
        identity=_call_identity(
            owner_run_id=owner_run_id,
            parent_run_id=parent_run_id,
            request_digit=request_digit,
            attempt_id=attempt_id,
        ),
        provider=provider,
        model=model,
        status="completed",
        timing=_timing(),
        usage=_usage(input_tokens=input_tokens, output_tokens=output_tokens),
        raw_usage_sha256=(request_digit.lower() * 64),
    )


def _root_identity() -> RunIdentity:
    return RunIdentity(
        execution_id="execution-1",
        attempt_id="attempt-1",
        root_run_id="root-1",
        run_id="root-1",
        parent_run_id=None,
        relation="root",
    )


def test_provider_call_usage_has_one_source_field_and_exact_closed_shape() -> None:
    assert [item.name for item in fields(ProviderCallUsage)].count("source") == 1
    payload = _usage(input_tokens=3, output_tokens=2).to_dict()
    assert set(payload) == {"input", "output", "total_tokens", "source"}
    assert set(payload["input"]) == {
        "uncached_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "cache_write_5m_tokens",
        "cache_write_1h_tokens",
        "total_tokens",
    }
    assert set(payload["output"]) == {
        "visible_tokens",
        "reasoning_tokens",
        "total_tokens",
    }
    payload["unexpected"] = 1
    with pytest.raises(RunBundleProtocolError, match="unsupported shape"):
        ProviderCallUsage.from_dict(payload)


def test_provider_usage_mappers_preserve_disjoint_cache_and_reasoning() -> None:
    openai = ProviderCallUsage.from_openai_usage(
        {
            "input_tokens": 100,
            "input_tokens_details": {
                "cached_tokens": 20,
                "cache_write_tokens": 10,
            },
            "output_tokens": 40,
            "output_tokens_details": {"reasoning_tokens": 15},
            "total_tokens": 140,
        }
    )
    assert openai.input_uncached_tokens == 70
    assert openai.input_cache_read_tokens == 20
    assert openai.input_cache_write_tokens == 10
    assert openai.output_visible_tokens == 25
    assert openai.output_reasoning_tokens == 15
    assert openai.total_tokens == 140

    anthropic = ProviderCallUsage.from_anthropic_usage(
        {
            "input_tokens": 50,
            "cache_read_input_tokens": 20,
            "cache_creation_input_tokens": 30,
            "cache_creation": {
                "ephemeral_5m_input_tokens": 10,
                "ephemeral_1h_input_tokens": 20,
            },
            "output_tokens": 40,
        },
        reasoning_present=False,
    )
    assert anthropic.input_total_tokens == 100
    assert anthropic.input_cache_write_5m_tokens == 10
    assert anthropic.input_cache_write_1h_tokens == 20
    assert anthropic.output_visible_tokens == 40
    assert anthropic.output_reasoning_tokens == 0
    assert anthropic.total_tokens == 140

    ollama = ProviderCallUsage.from_ollama_usage(
        {"prompt_eval_count": 10, "eval_count": 5},
        reasoning_present=False,
    )
    assert ollama.input_cache_read_tokens == 0
    assert ollama.input_cache_write_tokens == 0
    assert ollama.output_visible_tokens == 5
    assert ollama.output_reasoning_tokens == 0
    assert ollama.total_tokens == 15


def test_openai_usage_alias_conflict_and_component_overflow_fail_closed() -> None:
    with pytest.raises(RunBundleProtocolError, match="aliases disagree"):
        ProviderCallUsage.from_openai_usage(
            {
                "input_tokens": 10,
                "cached_tokens": 2,
                "input_tokens_details": {"cached_tokens": 3},
            }
        )
    with pytest.raises(RunBundleProtocolError, match="exceed"):
        ProviderCallUsage.from_openai_usage(
            {
                "input_tokens": 10,
                "input_tokens_details": {
                    "cached_tokens": 8,
                    "cache_write_tokens": 4,
                },
            }
        )


def test_receipt_and_bundle_ids_are_deterministic_and_schemas_are_strict() -> None:
    receipt = _receipt(
        owner_run_id="root-1",
        parent_run_id=None,
        request_digit="1",
        input_tokens=3,
        output_tokens=2,
    )
    assert receipt.to_dict()["schema"] == "unchain.provider_call_usage.v1"
    assert ProviderCallReceipt.from_dict(receipt.to_dict()) == receipt

    bundle = RunBundleReducer.reduce(
        identity=_root_identity(),
        lifecycle=_lifecycle(),
        receipts=[receipt],
    )
    assert bundle.to_dict()["schema"] == "unchain.run_bundle.v1"
    assert bundle.bundle_digest == canonical_sha256(
        {key: value for key, value in bundle.to_dict().items() if key != "bundle_digest"}
    )
    assert RunBundle.from_dict(bundle.to_dict()) == bundle

    wrong = bundle.to_dict()
    wrong["unexpected"] = True
    with pytest.raises(RunBundleProtocolError, match="unsupported shape"):
        RunBundle.from_dict(wrong)
    wrong = bundle.to_dict()
    wrong["bundle_digest"] = "f" * 64
    with pytest.raises(RunBundleProtocolError, match="bundle_digest"):
        RunBundle.from_dict(wrong)


def test_receipt_atomic_metadata_is_closed_hashed_and_route_bound() -> None:
    identity = _call_identity(
        owner_run_id="root-1",
        parent_run_id=None,
        request_digit="9",
    )
    receipt = ProviderCallReceipt(
        identity=identity,
        provider="openai",
        model="gpt-test",
        status="completed",
        timing=ProviderCallTiming(
            started_at="2026-08-13T12:00:00-07:00",
            completed_at="2026-08-13T19:00:00.000000001Z",
        ),
        provider_ids=ProviderCallIds(
            request_id_sha256="a" * 64,
            response_id_sha256="b" * 64,
        ),
        billing_dimensions=ProviderBillingDimensions(
            billing_surface="first_party_api",
            batch=False,
            inference_geo="global",
        ),
        usage=_usage(input_tokens=1, output_tokens=1),
    )
    wire = receipt.to_dict()
    assert set(wire) == {
        "schema",
        "provider_call_id",
        "identity",
        "provider",
        "status",
        "timing",
        "provider_ids",
        "billing_dimensions",
        "usage",
        "raw_usage_sha256",
        "pricing",
        "extensions",
    }
    assert set(wire["provider_ids"]) == {
        "request_id_sha256",
        "response_id_sha256",
    }
    assert ProviderCallReceipt.from_dict(wire) == receipt

    changed_route = ProviderCallReceipt(
        identity=ProviderCallIdentity(
            **{
                **identity.to_dict(),
                "route": "openai.responses.create",
            }
        ),
        provider="openai",
        model="gpt-test",
        status="completed",
        timing=_timing(),
        usage=_usage(input_tokens=1, output_tokens=1),
    )
    assert changed_route.provider_call_id != receipt.provider_call_id

    with pytest.raises(RunBundleProtocolError, match="requires both boundaries"):
        ProviderCallReceipt(
            identity=identity,
            provider="openai",
            model="gpt-test",
            status="completed",
            timing=ProviderCallTiming(started_at="2026-08-13T19:00:00Z"),
            usage=_usage(input_tokens=1, output_tokens=1),
        )
    uncertain = ProviderCallReceipt(
        identity=identity,
        provider="openai",
        model="gpt-test",
        status="uncertain",
        timing=ProviderCallTiming(started_at="2026-08-13T19:00:00Z"),
        usage=ProviderCallUsage(),
    )
    assert uncertain.timing.completed_at is None


def test_metrics_partition_mutation_fails_closed() -> None:
    receipt = _receipt(
        owner_run_id="root-1",
        parent_run_id=None,
        request_digit="8",
        input_tokens=1,
        output_tokens=1,
    )
    bundle = RunBundleReducer.reduce(
        identity=_root_identity(),
        lifecycle=_lifecycle(),
        receipts=[receipt],
        metric_events=[
            RunMetricEvent(
                execution_id="execution-1",
                attempt_id="attempt-1",
                root_run_id="root-1",
                owner_run_id="root-1",
                parent_run_id=None,
                kind="iteration",
                subject_id="iteration:0",
                outcome="completed",
            )
        ],
    )
    mutated = bundle.to_dict()
    mutated["metrics"]["direct"] = RunMetricCounters().to_dict()
    with pytest.raises(RunBundleProtocolError, match="direct or descendant"):
        RunBundle.from_dict(mutated)


def test_run_topology_rejects_orphan_cycle_and_fake_child_bundle_id() -> None:
    def child(run_id: str, parent_run_id: str, bundle_id: str) -> RunChild:
        return RunChild(
            run_id=run_id,
            attempt_id=f"attempt-{run_id}",
            parent_run_id=parent_run_id,
            relation="subagent",
            bundle_id=bundle_id,
            status="completed",
        )

    with pytest.raises(RunBundleProtocolError, match="orphan"):
        RunBundleReducer.reduce(
            identity=_root_identity(),
            lifecycle=_lifecycle(),
            receipts=[],
            children=[child("orphan", "missing", "rb_" + "a" * 64)],
        )
    with pytest.raises(RunBundleProtocolError, match="cycle"):
        first_identity = RunIdentity(
            execution_id="execution-1",
            attempt_id="attempt-first",
            root_run_id="root-1",
            run_id="first",
            parent_run_id="second",
            relation="subagent",
        )
        second_identity = RunIdentity(
            execution_id="execution-1",
            attempt_id="attempt-second",
            root_run_id="root-1",
            run_id="second",
            parent_run_id="first",
            relation="subagent",
        )
        RunBundleReducer.reduce(
            identity=_root_identity(),
            lifecycle=_lifecycle(),
            receipts=[],
            children=[
                child(
                    "first",
                    "second",
                    deterministic_bundle_id(identity=first_identity),
                ),
                child(
                    "second",
                    "first",
                    deterministic_bundle_id(identity=second_identity),
                ),
            ],
        )
    valid_child_identity = RunIdentity(
        execution_id="execution-1",
        attempt_id="attempt-child",
        root_run_id="root-1",
        run_id="child",
        parent_run_id="root-1",
        relation="subagent",
    )
    with pytest.raises(RunBundleProtocolError, match="bundle_id"):
        RunBundleReducer.reduce(
            identity=_root_identity(),
            lifecycle=_lifecycle(),
            receipts=[],
            children=[child("child", "root-1", "rb_" + "f" * 64)],
        )
    assert deterministic_bundle_id(identity=valid_child_identity).startswith("rb_")


def test_reducer_unions_root_and_child_call_sets_without_double_counting() -> None:
    root_receipt = _receipt(
        owner_run_id="root-1",
        parent_run_id=None,
        request_digit="1",
        input_tokens=10,
        output_tokens=5,
    )
    child_receipt = _receipt(
        owner_run_id="child-1",
        parent_run_id="root-1",
        request_digit="2",
        input_tokens=20,
        output_tokens=7,
        attempt_id="child-1",
    )
    child = RunChild(
        run_id="child-1",
        attempt_id="child-1",
        parent_run_id="root-1",
        relation="subagent",
        bundle_id=deterministic_bundle_id(
            identity=RunIdentity(
                execution_id="execution-1",
                attempt_id="child-1",
                root_run_id="root-1",
                run_id="child-1",
                parent_run_id="root-1",
                relation="subagent",
            )
        ),
        status="completed",
    )
    bundle = RunBundleReducer.reduce(
        identity=_root_identity(),
        lifecycle=_lifecycle(),
        receipts=[root_receipt, child_receipt, child_receipt],
        children=[child],
    )
    assert bundle.aggregation.direct_call_ids == (root_receipt.provider_call_id,)
    assert bundle.aggregation.descendant_call_ids == (child_receipt.provider_call_id,)
    assert bundle.aggregation.all_usage.total_tokens == 42
    assert bundle.coverage.status == "complete"
    assert len(bundle.provider_calls) == 2

    conflicting = ProviderCallReceipt(
        identity=child_receipt.identity,
        provider=child_receipt.provider,
        model=child_receipt.model,
        status="completed",
        timing=_timing(),
        usage=_usage(input_tokens=99, output_tokens=1),
    )
    with pytest.raises(RunBundleProtocolError, match="conflicting"):
        RunBundleReducer.reduce(
            identity=_root_identity(),
            lifecycle=_lifecycle(),
            receipts=[child_receipt, conflicting],
            children=[child],
        )


def test_reduce_bundles_merges_public_child_bundle_and_preserves_topology() -> None:
    child_receipt = _receipt(
        owner_run_id="child-1",
        parent_run_id="root-1",
        request_digit="2",
        input_tokens=4,
        output_tokens=6,
        attempt_id="child-1",
    )
    child_bundle = RunBundleReducer.reduce(
        identity=RunIdentity(
            execution_id="execution-1",
            attempt_id="child-1",
            root_run_id="root-1",
            run_id="child-1",
            parent_run_id="root-1",
            relation="graph_node",
        ),
        lifecycle=_lifecycle(),
        receipts=[child_receipt],
    )
    root_receipt = _receipt(
        owner_run_id="root-1",
        parent_run_id=None,
        request_digit="1",
        input_tokens=2,
        output_tokens=3,
    )
    merged = RunBundleReducer.reduce_bundles(
        identity=_root_identity(),
        lifecycle=_lifecycle(),
        bundles=[child_bundle],
        receipts=[root_receipt],
    )
    assert {child.run_id for child in merged.children} == {"child-1"}
    assert merged.children[0].bundle_id == child_bundle.bundle_id
    assert merged.aggregation.all_usage.total_tokens == 15


def test_missing_or_uncertain_usage_is_null_and_coverage_is_not_zero_complete() -> None:
    receipt = ProviderCallReceipt(
        identity=_call_identity(
            owner_run_id="root-1",
            parent_run_id=None,
            request_digit="3",
        ),
        provider="openai",
        model="gpt-test",
        status="uncertain",
        usage=ProviderCallUsage(),
    )
    bundle = RunBundleReducer.reduce(
        identity=_root_identity(),
        lifecycle=_lifecycle("uncertain"),
        receipts=[receipt],
    )
    assert bundle.aggregation.all_usage.total_tokens is None
    assert bundle.coverage.status == "unavailable"
    assert bundle.coverage.uncertain_call_count == 1
    assert bundle.coverage.missing_usage_call_ids == (receipt.provider_call_id,)


def _pricing_snapshot(
    *,
    provider: str,
    model: str,
    generic_write_rate: int | None,
    five_minute_rate: int | None,
    one_hour_rate: int | None,
    threshold: int | None = None,
    input_multiplier: int | None = None,
    output_multiplier: int | None = None,
) -> PricingSnapshotRef:
    return PricingSnapshotRef(
        catalog_version="test-2026-08-13.1",
        catalog_sha256="a" * 64,
        source_url="https://example.com/official-pricing",
        source_sha256="b" * 64,
        effective_from="2026-08-13T00:00:00Z",
        provider=provider,
        billing_surface="first_party_api",
        model=model,
        service_tier="standard",
        batch=False,
        inference_geo="global",
        input_uncached_nano_usd_per_million=(
            3_000_000_000 if provider == "anthropic" else 1_000_000_000
        ),
        input_cache_read_nano_usd_per_million=(
            300_000_000 if provider == "anthropic" else 100_000_000
        ),
        input_cache_write_nano_usd_per_million=generic_write_rate,
        input_cache_write_5m_nano_usd_per_million=five_minute_rate,
        input_cache_write_1h_nano_usd_per_million=one_hour_rate,
        output_nano_usd_per_million=(
            15_000_000_000 if provider == "anthropic" else 6_000_000_000
        ),
        long_context_threshold_input_tokens=threshold,
        long_context_input_multiplier_ppm=input_multiplier,
        long_context_output_multiplier_ppm=output_multiplier,
    )


def test_pricing_snapshot_preserves_ttl_and_long_context_modifiers() -> None:
    anthropic_usage = ProviderCallUsage(
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
    anthropic_snapshot = _pricing_snapshot(
        provider="anthropic",
        model="claude-test",
        generic_write_rate=None,
        five_minute_rate=3_750_000_000,
        one_hour_rate=6_000_000_000,
    )
    anthropic_price = ProviderCallPricing.estimate(
        snapshot=anthropic_snapshot,
        usage=anthropic_usage,
    )
    assert anthropic_price.status == "estimated"
    assert anthropic_price.amount_nano_usd == 28_050_000

    openai_usage = ProviderCallUsage(
        input_uncached_tokens=300_000,
        input_cache_read_tokens=0,
        input_cache_write_tokens=0,
        input_total_tokens=300_000,
        output_visible_tokens=100_000,
        output_reasoning_tokens=0,
        output_total_tokens=100_000,
        total_tokens=400_000,
        source="provider_observed_partial",
    )
    openai_snapshot = _pricing_snapshot(
        provider="openai",
        model="gpt-test",
        generic_write_rate=1_250_000_000,
        five_minute_rate=None,
        one_hour_rate=None,
        threshold=272_000,
        input_multiplier=2_000_000,
        output_multiplier=1_500_000,
    )
    openai_price = ProviderCallPricing.estimate(
        snapshot=openai_snapshot,
        usage=openai_usage,
    )
    assert openai_price.amount_nano_usd == 1_500_000_000
    assert openai_price.input_multiplier_ppm == 2_000_000
    assert openai_price.output_multiplier_ppm == 1_500_000

    threshold_usage = ProviderCallUsage(
        input_uncached_tokens=272_000,
        input_cache_read_tokens=0,
        input_cache_write_tokens=0,
        input_total_tokens=272_000,
        output_visible_tokens=1,
        output_reasoning_tokens=0,
        output_total_tokens=1,
        total_tokens=272_001,
        source="provider_observed_partial",
    )
    threshold_price = ProviderCallPricing.estimate(
        snapshot=openai_snapshot,
        usage=threshold_usage,
    )
    assert threshold_price.input_multiplier_ppm == 1_000_000
    assert threshold_price.output_multiplier_ppm == 1_000_000

    ttl_unknown = ProviderCallUsage(
        input_uncached_tokens=0,
        input_cache_read_tokens=0,
        input_cache_write_tokens=10,
        input_total_tokens=10,
        output_visible_tokens=0,
        output_reasoning_tokens=0,
        output_total_tokens=0,
        total_tokens=10,
        source="provider_observed_partial",
    )
    assert ProviderCallPricing.estimate(
        snapshot=anthropic_snapshot,
        usage=ttl_unknown,
    ).status == "unavailable"
    unavailable = ProviderCallPricing.estimate(
        snapshot=anthropic_snapshot,
        usage=ttl_unknown,
    )
    assert unavailable.snapshot == anthropic_snapshot
    assert ProviderCallPricing.from_dict(unavailable.to_dict()) == unavailable


def test_timestamp_order_uses_instants_across_offsets_and_nanoseconds() -> None:
    base = _pricing_snapshot(
        provider="openai",
        model="gpt-test",
        generic_write_rate=1,
        five_minute_rate=None,
        one_hour_rate=None,
    )
    fields = base.to_dict()
    fields["snapshot_id"] = ""
    fields["effective_from"] = "2026-08-13T12:00:00-07:00"
    fields["effective_until"] = "2026-08-13T19:00:00.000000001Z"
    assert PricingSnapshotRef.from_dict(fields).effective_until is not None
    fields["effective_until"] = "2026-08-13T19:00:00Z"
    with pytest.raises(RunBundleProtocolError, match="must follow"):
        PricingSnapshotRef.from_dict(fields)


def test_run_lifecycle_requires_exact_live_and_terminal_timestamps() -> None:
    with pytest.raises(RunBundleProtocolError, match="started_at"):
        RunLifecycle(status="running")
    with pytest.raises(RunBundleProtocolError, match="null completed_at"):
        RunLifecycle(
            status="running",
            started_at="2026-08-13T18:00:00Z",
            completed_at="2026-08-13T18:01:00Z",
        )
    for status in ("completed", "failed", "suspended", "cancelled"):
        with pytest.raises(RunBundleProtocolError, match="requires completed_at"):
            RunLifecycle(
                status=status,
                started_at="2026-08-13T18:00:00Z",
            )
    assert RunLifecycle(
        status="uncertain",
        started_at="2026-08-13T18:00:00Z",
    ).completed_at is None
    assert RunLifecycle(
        status="uncertain",
        started_at="2026-08-13T18:00:00Z",
        completed_at="2026-08-13T18:01:00Z",
    ).completed_at is not None


def test_extensions_reject_prompt_secret_and_provider_request_payloads() -> None:
    with pytest.raises(RunBundleProtocolError, match="prohibited"):
        ProviderCallReceipt(
            identity=_call_identity(
                owner_run_id="root-1",
                parent_run_id=None,
                request_digit="4",
            ),
            provider="openai",
            model="gpt-test",
            status="completed",
            timing=_timing(),
            usage=_usage(input_tokens=1, output_tokens=1),
            extensions={"vendor.test/debug": {"prompt": "do not persist"}},
        )


def test_provider_turn_result_v1_is_unchanged_and_recovers_as_legacy_partial() -> None:
    result = ModelTurnResult(
        assistant_messages=[{"role": "assistant", "content": "done"}],
        tool_calls=[],
        final_text="done",
        consumed_tokens=5,
        input_tokens=3,
        output_tokens=2,
        provider_call_usage=_usage(input_tokens=3, output_tokens=2),
    )
    subject = ProviderRequestSubject(
        attempt=AttemptRef(
            GenerationRef("execution-1", "generation-1"),
            "attempt-1",
        ),
        iteration=1,
        envelope_sha256="a" * 64,
        route="primary",
        retry_ordinal=0,
    )
    envelope = ProviderTurnResultEnvelope.from_model_turn_result(
        subject=subject,
        route_sha256="b" * 64,
        visible_output=True,
        result=result,
    )
    assert "provider_call_usage" not in envelope.to_dict()["result"]
    recovered = envelope.to_model_turn_result()
    assert recovered.provider_call_usage is None
    receipt = ProviderCallReceipt.from_model_turn_result(
        identity=_call_identity(
            owner_run_id="root-1",
            parent_run_id=None,
            request_digit="5",
        ),
        provider="openai",
        model="gpt-test",
        result=recovered,
    )
    assert receipt.usage.source == "legacy_partial"
    bundle = RunBundleReducer.reduce(
        identity=_root_identity(),
        lifecycle=_lifecycle(),
        receipts=[receipt],
        legacy=LegacyAttribution(
            status="legacy_partial",
            source="unchain.provider_turn_result.v1",
            reason="canonical provider usage was unavailable on recovery",
        ),
    )
    assert bundle.legacy.status == "legacy_partial"


def test_extension_reprojection_is_additive_and_exactly_next_revision() -> None:
    bundle = RunBundleReducer.reduce(
        identity=_root_identity(),
        lifecycle=_lifecycle(),
        receipts=(),
    )
    diagnostic_ref = {
        "schema": "pupu.completion_diagnostics_ref.v1",
        "diagnostics_schema": "pupu.completion_diagnostics.v1",
        "diagnostics_sha256": "a" * 64,
    }
    projected = reproject_run_bundle_extensions(
        bundle,
        extensions={
            "pupu.run/completion_diagnostics_ref_v1": diagnostic_ref,
        },
        next_revision=2,
    )
    assert projected.revision == 2
    assert projected.bundle_id == bundle.bundle_id
    assert projected.bundle_digest != bundle.bundle_digest
    assert projected.extensions[
        "pupu.run/completion_diagnostics_ref_v1"
    ] == diagnostic_ref

    with pytest.raises(RunBundleProtocolError, match="exact next revision"):
        reproject_run_bundle_extensions(
            bundle,
            extensions={"pupu.run/completion_diagnostics_ref_v1": diagnostic_ref},
            next_revision=3,
        )
    with pytest.raises(RunBundleProtocolError, match="cannot rewrite"):
        reproject_run_bundle_extensions(
            projected,
            extensions={"pupu.run/completion_diagnostics_ref_v1": diagnostic_ref},
            next_revision=3,
        )


class _OpenAIStream:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def __iter__(self):
        yield SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(
                id="response-1",
                output=[
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "done"}],
                    }
                ],
                usage={
                    "input_tokens": 100,
                    "input_tokens_details": {
                        "cached_tokens": 20,
                        "cache_write_tokens": 10,
                    },
                    "output_tokens": 40,
                    "output_tokens_details": {"reasoning_tokens": 15},
                    "total_tokens": 140,
                },
            ),
        )


def test_openai_fetch_turn_attaches_canonical_provider_usage() -> None:
    class Responses:
        def create(self, **kwargs):
            return _OpenAIStream()

    class Client:
        responses = Responses()

    io = OpenAIModelIO(
        model="gpt-test",
        api_key="test-key",
        client_factory=lambda **kwargs: Client(),
        default_payloads={},
        model_capabilities={},
    )
    result = io.fetch_turn(
        ModelTurnRequest(messages=[{"role": "user", "content": "hi"}])
    )
    assert result.provider_call_usage is not None
    assert result.provider_call_usage.input_uncached_tokens == 70
    assert result.provider_call_usage.input_cache_write_tokens == 10
    assert result.provider_call_usage.output_reasoning_tokens == 15
    assert result.cache_creation_input_tokens == 10


class _AnthropicStream:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def __iter__(self):
        yield SimpleNamespace(
            type="message_start",
            message=SimpleNamespace(
                usage={
                    "input_tokens": 50,
                    "output_tokens": 0,
                    "cache_read_input_tokens": 20,
                    "cache_creation_input_tokens": 30,
                    "cache_creation": {
                        "ephemeral_5m_input_tokens": 10,
                        "ephemeral_1h_input_tokens": 20,
                    },
                }
            ),
        )
        yield SimpleNamespace(
            type="content_block_delta",
            delta=SimpleNamespace(type="text_delta", text="done"),
        )
        yield SimpleNamespace(type="message_delta", usage={"output_tokens": 40})


def test_anthropic_fetch_turn_attaches_canonical_provider_usage() -> None:
    class Messages:
        def stream(self, **kwargs):
            return _AnthropicStream()

    class Client:
        messages = Messages()

    io = AnthropicModelIO(
        model="claude-test",
        api_key="test-key",
        client_factory=lambda **kwargs: Client(),
        default_payloads={},
        model_capabilities={},
    )
    result = io.fetch_turn(
        ModelTurnRequest(messages=[{"role": "user", "content": "hi"}])
    )
    assert result.provider_call_usage is not None
    assert result.provider_call_usage.input_total_tokens == 100
    assert result.provider_call_usage.input_cache_write_5m_tokens == 10
    assert result.provider_call_usage.input_cache_write_1h_tokens == 20
    assert result.provider_call_usage.output_visible_tokens == 40


class _OllamaResponse:
    status_code = 200

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def raise_for_status(self):
        return None

    def read(self):
        return b""

    def iter_lines(self):
        yield json.dumps(
            {
                "message": {"content": "done"},
                "prompt_eval_count": 10,
                "eval_count": 5,
                "done": True,
            }
        )


def test_ollama_fetch_turn_attaches_canonical_provider_usage() -> None:
    io = OllamaModelIO(
        model="llama-test",
        stream_factory=lambda *args, **kwargs: _OllamaResponse(),
        default_payloads={},
        model_capabilities={},
    )
    result = io.fetch_turn(
        ModelTurnRequest(messages=[{"role": "user", "content": "hi"}])
    )
    assert result.provider_call_usage is not None
    assert result.provider_call_usage.total_tokens == 15
    assert result.provider_call_usage.output_visible_tokens == 5
    assert result.provider_call_usage.output_reasoning_tokens == 0


def test_openai_fetch_turn_handles_large_provider_usage_without_run_bundle_limit() -> None:
    raw_usage = {
        "input_tokens": 120,
        "output_tokens": 45,
        "total_tokens": 165,
        "input_tokens_details": {"cached_tokens": 20, "cache_write_tokens": 10},
        "output_tokens_details": {"reasoning_tokens": 15},
        "large_blob": "x" * 2_200_000,
    }

    class _LargeOpenAIStream:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def __iter__(self):
            yield SimpleNamespace(
                type="response.completed",
                response=SimpleNamespace(
                    id="response-large",
                    output=[
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "ok"}],
                        }
                    ],
                    usage=raw_usage,
                ),
            )

    class Responses:
        def create(self, **kwargs):
            return _LargeOpenAIStream()

    class Client:
        responses = Responses()

    io = OpenAIModelIO(
        model="gpt-test",
        api_key="test-key",
        client_factory=lambda **kwargs: Client(),
        default_payloads={},
        model_capabilities={},
    )
    result = io.fetch_turn(
        ModelTurnRequest(messages=[{"role": "user", "content": "hi"}])
    )
    assert result.provider_call_usage is not None
    assert result.provider_raw_usage_sha256 == canonical_json_sha256(raw_usage)


def test_anthropic_fetch_turn_handles_large_provider_usage_without_run_bundle_limit() -> None:
    raw_usage = {
        "input_tokens": 90,
        "output_tokens": 40,
        "cache_read_input_tokens": 10,
        "cache_creation_input_tokens": 30,
        "cache_creation": {
            "ephemeral_5m_input_tokens": 10,
            "ephemeral_1h_input_tokens": 20,
        },
        "large_blob": "x" * 2_200_000,
    }

    class _LargeAnthropicStream:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def __iter__(self):
            yield SimpleNamespace(
                type="message_start",
                message=SimpleNamespace(
                    usage=raw_usage,
                ),
            )
            yield SimpleNamespace(
                type="content_block_delta",
                delta=SimpleNamespace(type="text_delta", text="done"),
            )
            yield SimpleNamespace(type="message_delta", usage={"output_tokens": 40})

    class Messages:
        def stream(self, **kwargs):
            return _LargeAnthropicStream()

    class Client:
        messages = Messages()

    io = AnthropicModelIO(
        model="claude-test",
        api_key="test-key",
        client_factory=lambda **kwargs: Client(),
        default_payloads={},
        model_capabilities={},
    )
    result = io.fetch_turn(
        ModelTurnRequest(messages=[{"role": "user", "content": "hi"}])
    )
    assert result.provider_call_usage is not None
    assert result.provider_raw_usage_sha256 == canonical_json_sha256(raw_usage)

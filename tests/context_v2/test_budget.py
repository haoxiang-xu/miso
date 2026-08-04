from __future__ import annotations

import pytest

from unchain.context import (
    ContextBudgetError,
    estimate_context_tokens,
    resolve_context_budget,
)


@pytest.mark.parametrize(
    (
        "window",
        "reserve",
        "margin",
        "available",
        "pressure_threshold",
    ),
    (
        (8_192, 2_048, 512, 5_632, 5_068),
        (131_072, 8_192, 2_621, 120_259, 108_233),
    ),
)
def test_default_budget_math_matches_the_p0_contract(
    window: int,
    reserve: int,
    margin: int,
    available: int,
    pressure_threshold: int,
) -> None:
    budget = resolve_context_budget(context_window_tokens=window)

    assert budget.context_window_tokens == window
    assert budget.output_reserve_tokens == reserve
    assert budget.transport_margin_tokens == margin
    assert budget.available_input_tokens == available
    assert budget.pressure_threshold_tokens == pressure_threshold


def test_explicit_budget_values_are_clamped_without_a_second_window_fraction() -> None:
    budget = resolve_context_budget(
        context_window_tokens=10_000,
        output_reserve_tokens=1_000,
        transport_margin_tokens=500,
    )

    assert budget.available_input_tokens == 8_500
    assert budget.pressure_threshold_tokens == 7_650


def test_non_positive_overrides_follow_the_p0_clamp_policy() -> None:
    budget = resolve_context_budget(
        context_window_tokens=10_000,
        output_reserve_tokens=0,
        transport_margin_tokens=-10,
    )

    assert budget.output_reserve_tokens == 1
    assert budget.transport_margin_tokens == 0
    assert budget.available_input_tokens == 9_999


@pytest.mark.parametrize("window", (0, -1, True, None))
def test_unknown_or_invalid_window_fails_closed(window: object) -> None:
    with pytest.raises((ContextBudgetError, TypeError)):
        resolve_context_budget(context_window_tokens=window)  # type: ignore[arg-type]


def test_multimodal_estimate_adds_the_p0_conservative_charge() -> None:
    estimate = estimate_context_tokens(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "inspect"},
                    {"type": "input_image", "image_url": "authorized-ref"},
                    {"type": "input_pdf", "page_start": 2, "page_end": 4},
                ],
            }
        ]
    )

    assert estimate.image_count == 1
    assert estimate.pdf_page_count == 3
    assert estimate.multimodal_tokens == 2_048 + (3 * 4_096)
    assert estimate.total_tokens == estimate.text_tokens + estimate.multimodal_tokens


def test_token_estimate_counts_large_nested_tool_arguments() -> None:
    estimate = estimate_context_tokens(
        [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-1",
                        "function": {
                            "name": "lookup",
                            "arguments": "x" * 100_000,
                        },
                    }
                ],
            }
        ]
    )

    assert estimate.text_tokens > 25_000

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from unchain.optimizers.common import estimate_tokens

from .models import ContextBudget


_IMAGE_TOKEN_CHARGE = 2_048
_PDF_PAGE_TOKEN_CHARGE = 4_096


class ContextBudgetError(ValueError):
    """Raised when a finite, usable Context V2 budget cannot be resolved."""


@dataclass(frozen=True)
class ContextTokenEstimate:
    text_tokens: int
    multimodal_tokens: int
    image_count: int
    pdf_page_count: int

    @property
    def total_tokens(self) -> int:
        return self.text_tokens + self.multimodal_tokens


def _required_int(value: Any, field_name: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an integer")
    if value < minimum:
        raise ContextBudgetError(f"{field_name} must be at least {minimum}")
    return value


def _clamped_default(window: int, ratio: float, minimum: int, maximum: int) -> int:
    return min(maximum, max(minimum, int(window * ratio)))


def resolve_context_budget(
    *,
    context_window_tokens: int,
    output_reserve_tokens: int | None = None,
    transport_margin_tokens: int | None = None,
) -> ContextBudget:
    """Apply the P0 budget rule once, against a real finite model window."""

    window = _required_int(
        context_window_tokens,
        "context_window_tokens",
        minimum=1,
    )
    default_margin = _clamped_default(window, 0.02, 512, 4_096)
    if transport_margin_tokens is None:
        margin = default_margin
    else:
        raw_margin = _required_int(
            transport_margin_tokens,
            "transport_margin_tokens",
            minimum=-(2**63),
        )
        margin = min(
            max(0, raw_margin),
            max(0, window - 1),
        )

    default_reserve = _clamped_default(window, 0.10, 2_048, 8_192)
    if output_reserve_tokens is None:
        reserve = default_reserve
    else:
        raw_reserve = _required_int(
            output_reserve_tokens,
            "output_reserve_tokens",
            minimum=-(2**63),
        )
        reserve = min(
            max(1, raw_reserve),
            max(1, window - margin),
        )

    available = window - reserve - margin
    pressure_threshold = int(math.floor(available * 0.90))
    if available <= 0 or pressure_threshold <= 0:
        raise ContextBudgetError("context budget has no usable input capacity")
    return ContextBudget(
        context_window_tokens=window,
        output_reserve_tokens=reserve,
        transport_margin_tokens=margin,
        available_input_tokens=available,
        pressure_threshold_tokens=pressure_threshold,
    )


def _pdf_pages(block: Mapping[str, Any]) -> int:
    page_count = block.get("page_count")
    if isinstance(page_count, int) and not isinstance(page_count, bool):
        return max(1, page_count)
    pages = block.get("pages")
    if isinstance(pages, Sequence) and not isinstance(
        pages, (str, bytes, bytearray)
    ):
        return max(1, len(pages))
    page_start = block.get("page_start")
    page_end = block.get("page_end")
    if all(
        isinstance(value, int) and not isinstance(value, bool)
        for value in (page_start, page_end)
    ):
        return max(1, page_end - page_start + 1)
    return 1


def estimate_context_tokens(messages: Sequence[Mapping[str, Any]]) -> ContextTokenEstimate:
    """Return the deterministic P0 text estimate plus conservative media charges."""

    def plain(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {str(key): plain(item) for key, item in value.items()}
        if isinstance(value, Sequence) and not isinstance(
            value, (str, bytes, bytearray)
        ):
            return [plain(item) for item in value]
        return value

    normalized = [plain(message) for message in messages]
    image_count = 0
    pdf_page_count = 0

    def visit(value: Any) -> None:
        nonlocal image_count, pdf_page_count
        if isinstance(value, Sequence) and not isinstance(
            value, (str, bytes, bytearray)
        ):
            for item in value:
                visit(item)
            return
        if not isinstance(value, Mapping):
            return
        block_type = str(value.get("type") or "").strip().lower()
        media_type = str(
            value.get("media_type") or value.get("mime_type") or ""
        ).strip().lower()
        if block_type in {"image", "image_url", "input_image"} or media_type.startswith(
            "image/"
        ):
            image_count += 1
            return
        if block_type in {"pdf", "input_pdf"} or media_type == "application/pdf":
            pdf_page_count += _pdf_pages(value)
            return
        for item in value.values():
            visit(item)

    visit(normalized)
    legacy_text_tokens = max(0, int(estimate_tokens(normalized)))
    structural_bytes = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    structural_tokens = int(math.ceil(len(structural_bytes) / 4))
    text_tokens = max(legacy_text_tokens, structural_tokens)
    multimodal_tokens = (
        image_count * _IMAGE_TOKEN_CHARGE
        + pdf_page_count * _PDF_PAGE_TOKEN_CHARGE
    )
    return ContextTokenEstimate(
        text_tokens=text_tokens,
        multimodal_tokens=multimodal_tokens,
        image_count=image_count,
        pdf_page_count=pdf_page_count,
    )


__all__ = [
    "ContextBudgetError",
    "ContextTokenEstimate",
    "estimate_context_tokens",
    "resolve_context_budget",
]

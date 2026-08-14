from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..run_bundle import ProviderCallReceipt, ProviderCallUsage


@dataclass(frozen=True)
class ToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any] | str | None


@dataclass(frozen=True)
class TokenUsage:
    consumed_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class ModelTurnResult:
    assistant_messages: list[dict[str, Any]]
    tool_calls: list[ToolCall]
    final_text: str = ""
    response_id: str | None = None
    reasoning_items: list[dict[str, Any]] | None = None
    consumed_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    provider_replay_frame: dict[str, Any] | None = None
    # Canonical usage is emitted on the new provider-call ledger boundary.
    # provider_turn_result.v1 deliberately does not serialize this optional
    # field; old durable receipts therefore recover as explicit legacy_partial.
    provider_call_usage: ProviderCallUsage | None = None
    # Content-free ephemeral evidence for RunBundle.  These fields are not
    # serialized into provider_turn_result.v1.
    provider_raw_usage_sha256: str | None = None
    provider_request_id_sha256: str | None = None
    provider_response_id_sha256: str | None = None
    # Exact live accounting fact returned by the durable provider boundary.
    # This is intentionally excluded from provider_turn_result.v1; the same
    # receipt is committed beside that v1 result in the accounting ledger.
    provider_call_receipt: ProviderCallReceipt | None = None


@dataclass(frozen=True)
class KernelRunResult:
    messages: list[dict[str, Any]]
    status: str
    continuation: dict[str, Any] | None = None
    human_input_request: dict[str, Any] | None = None
    consumed_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    last_turn_tokens: int = 0
    last_turn_input_tokens: int = 0
    last_turn_output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    previous_response_id: str | None = None
    iteration: int = 0
    provider_replay_handle: dict[str, Any] | None = None
    interaction_request: dict[str, Any] | None = None
    # Renderer-safe unchain.run_bundle.v1 projection. Existing runtimes leave
    # this null until the provider-call ledger integration is enabled.
    run_bundle: dict[str, Any] | None = None

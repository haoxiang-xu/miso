"""Attempt-scoped host execution for one exact durable provider turn."""

from __future__ import annotations

import time
from collections.abc import Callable

from unchain.journal import AttemptRef
from unchain.journal.provider_wire import BoundProviderWireStore
from unchain.kernel.types import ModelTurnResult
from unchain.providers.anthropic import AnthropicModelIO
from unchain.providers.base import ModelTurnRequest
from unchain.providers.durable_turn_runtime import (
    DurableProviderTurnMode,
    DurableProviderTurnRuntime,
    DurableProviderTurnStatus,
    ExactProviderRouteTransport,
)
from unchain.providers.exact_route_transport import (
    AnthropicExactRouteTransport,
    HyperspaceExactRouteTransport,
    OllamaExactRouteTransport,
    OpenAIExactRouteTransport,
)
from unchain.providers.ollama import OllamaModelIO
from unchain.providers.openai import OpenAIModelIO
from unchain.retry import RetryConfig

from .provider_toolkit import ProviderToolkitAuthorityAdapter
from .provider_turns import ProviderTurnAuthorityService


class ContextProviderTurnExecutionError(RuntimeError):
    """The host could not resolve an exact provider transport."""


def _exact_transport(
    *,
    model_io: object,
    catalog,
    request: ModelTurnRequest,
) -> ExactProviderRouteTransport:
    provider = getattr(model_io, "provider", None)
    common = {
        "model_io": model_io,
        "catalog": catalog,
        "callback": request.callback,
        "run_id": request.run_id,
        "emit_stream": request.emit_stream,
    }
    if provider == "openai" and type(model_io) is OpenAIModelIO:
        return OpenAIExactRouteTransport(**common)
    if provider == "anthropic" and type(model_io) is AnthropicModelIO:
        return AnthropicExactRouteTransport(**common)
    if provider == "hyperspace":
        return HyperspaceExactRouteTransport(**common)
    if provider == "ollama" and type(model_io) is OllamaModelIO:
        return OllamaExactRouteTransport(**common)
    raise ContextProviderTurnExecutionError(
        "model_io does not have an official exact provider transport"
    )


class ContextProviderTurnExecutionService:
    """Compose durable authority, transport, lease, and result persistence.

    ``off`` is a read-only legacy fallthrough unless prior enforce evidence
    exists. ``shadow`` persists only the request authority. ``enforce_test``
    is the sole mode allowed to send through the exact transport.
    """

    def __init__(
        self,
        *,
        attempt: AttemptRef,
        store: BoundProviderWireStore,
        mode: DurableProviderTurnMode,
        transport_target_sha256: str,
        sleep: Callable[[float], None] = time.sleep,
        toolkit_adapter: ProviderToolkitAuthorityAdapter | None = None,
    ) -> None:
        try:
            resolved_mode = DurableProviderTurnMode(mode)
        except (TypeError, ValueError) as exc:
            raise ValueError("durable provider turn mode is unsupported") from exc
        if not callable(sleep):
            raise TypeError("sleep must be callable")
        self._attempt = AttemptRef.from_dict(attempt.to_dict())
        self._store = store
        self._mode = resolved_mode
        self._sleep = sleep
        resolved_toolkit_adapter = (
            toolkit_adapter
            if toolkit_adapter is not None
            else ProviderToolkitAuthorityAdapter()
        )
        if type(resolved_toolkit_adapter) is not ProviderToolkitAuthorityAdapter:
            raise TypeError(
                "toolkit_adapter must be the official "
                "ProviderToolkitAuthorityAdapter or null"
            )
        self._authority_service = ProviderTurnAuthorityService(
            attempt=self._attempt,
            store=store,
            transport_target_sha256=transport_target_sha256,
            toolkit_adapter=resolved_toolkit_adapter,
        )

    @property
    def attempt(self) -> AttemptRef:
        return AttemptRef.from_dict(self._attempt.to_dict())

    @property
    def store(self) -> BoundProviderWireStore:
        return self._store

    @property
    def mode(self) -> DurableProviderTurnMode:
        return self._mode

    def fetch_prepared(
        self,
        *,
        model_io: object,
        request: ModelTurnRequest,
        retry_config: RetryConfig,
        before_attempt: Callable[[int], None] | None = None,
    ) -> ModelTurnResult | None:
        if type(request) is not ModelTurnRequest:
            raise TypeError("request must be an exact ModelTurnRequest")
        if before_attempt is not None and not callable(before_attempt):
            raise TypeError("before_attempt must be callable or null")

        if self._mode is DurableProviderTurnMode.OFF:
            authority = self._authority_service.recover_existing(
                model_io=model_io,
                request=request,
            )
            if authority is None:
                return None
        else:
            authority = self._authority_service.prepare(
                model_io=model_io,
                request=request,
            )

        transport = _exact_transport(
            model_io=model_io,
            catalog=authority.catalog,
            request=request,
        )
        runtime = DurableProviderTurnRuntime(
            mode=self._mode,
            store=self._store,
            transport=transport,
            sleep=self._sleep,
        )
        send_number = 0

        def before_send() -> None:
            nonlocal send_number
            if before_attempt is not None:
                before_attempt(send_number)
            send_number += 1

        try:
            outcome = runtime.execute(
                authority=authority,
                retry_config=retry_config,
                before_send=before_send,
            )
        except BaseException:
            transport.discard_buffered_events()
            raise

        if outcome.status is not DurableProviderTurnStatus.COMPLETED:
            transport.discard_buffered_events()
            return None
        if outcome.result is None:
            transport.discard_buffered_events()
            raise ContextProviderTurnExecutionError(
                "completed provider turn lost its durable result"
            )
        if outcome.recovered:
            transport.discard_buffered_events()
        else:
            transport.release_buffered_events()
        return outcome.result


__all__ = [
    "ContextProviderTurnExecutionError",
    "ContextProviderTurnExecutionService",
]

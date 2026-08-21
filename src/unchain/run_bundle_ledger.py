"""Durable receipt and projection ledger for ``unchain.run_bundle.v1``.

The protocol is deliberately independent from provider-turn recovery.  A
provider result may be useful to resume inference, but only immutable
``ProviderCallReceipt`` rows are valid accounting evidence.
"""

from __future__ import annotations

from typing import Mapping, Protocol, runtime_checkable

from .run_bundle import ProviderCallReceipt, RunBundle, RunIdentity, RunMetricEvent, RunChild
from .run_bundle_v2 import CompactRunBundle


class RunBundleLedgerError(RuntimeError):
    """Base failure for durable run-bundle accounting."""


class RunBundleLedgerConflictError(RunBundleLedgerError):
    """An immutable receipt or bundle revision was rewritten."""


class RunBundleLedgerIntegrityError(RunBundleLedgerError):
    """Persisted accounting bytes failed canonical validation."""


class RunBundleLedgerScopeError(RunBundleLedgerError):
    """An accounting fact crossed its execution-bound capability."""


class RunBundleContinuationError(RunBundleLedgerError):
    """A fresh-run predecessor link could not be admitted safely."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@runtime_checkable
class RunBundleLedger(Protocol):
    """Execution-bound, exact-once durable accounting capability.

    ``list_bundles`` returns the latest persisted revision for each matching
    bundle identity.  Callers that need an historical audit use ``load_bundle``
    with an explicit revision.
    """

    @property
    def execution_id(self) -> str:
        ...

    def append_receipt(
        self,
        receipt: ProviderCallReceipt,
    ) -> ProviderCallReceipt:
        ...

    def load_receipts(
        self,
        *,
        root_run_id: str | None = None,
        owner_run_id: str | None = None,
        attempt_id: str | None = None,
    ) -> tuple[ProviderCallReceipt, ...]:
        ...

    def persist_bundle(self, bundle: RunBundle) -> RunBundle:
        ...

    def load_bundle(
        self,
        bundle_id: str,
        *,
        revision: int | None = None,
    ) -> RunBundle | None:
        ...

    def list_bundles(
        self,
        *,
        root_run_id: str | None = None,
        run_id: str | None = None,
        attempt_id: str | None = None,
    ) -> tuple[RunBundle, ...]:
        ...


@runtime_checkable
class RunBundleProjectionDetailsLedger(Protocol):
    """Optional v1 extension for exact compact projection details.

    Compact projections may omit verbose metric events from the public v1
    bundle, but only when the omitted facts are atomically persisted under the
    same bundle revision and projection hash.
    """

    @property
    def execution_id(self) -> str:
        ...

    def persist_bundle_with_projection_details(
        self,
        *,
        bundle: RunBundle,
        projection_hash: str,
        projection_metric_events: tuple[RunMetricEvent, ...] = (),
    ) -> RunBundle:
        ...

    def load_projection_details(
        self,
        *,
        bundle_id: str,
        revision: int,
        projection_hash: str,
        metric_events_sha256: str,
    ) -> tuple[RunMetricEvent, ...]:
        ...


@runtime_checkable
class RunBundleCompactDetailsLedger(Protocol):
    """Durable facts capability for the compact ``run_bundle.v2`` envelope."""

    @property
    def execution_id(self) -> str:
        ...

    def persist_compact_bundle_with_details(
        self,
        *,
        bundle: CompactRunBundle,
        details: Mapping[str, list[dict[str, object]]],
    ) -> CompactRunBundle:
        ...

    def load_compact_bundle_details(
        self,
        *,
        bundle: CompactRunBundle,
    ) -> tuple[tuple[ProviderCallReceipt, ...], tuple[RunMetricEvent, ...], tuple[RunChild, ...]]:
        ...

    def list_compact_bundles(
        self,
        *,
        root_run_id: str | None = None,
        run_id: str | None = None,
        attempt_id: str | None = None,
    ) -> tuple[CompactRunBundle, ...]:
        ...

@runtime_checkable
class RunBundleContinuationLedger(Protocol):
    """Optional v1 extension for atomic fresh-run predecessor consumption."""

    @property
    def execution_id(self) -> str:
        ...

    def claim_continuation(
        self,
        *,
        successor: RunIdentity,
        requested_run_id: str | None = None,
    ) -> RunBundle | CompactRunBundle | None:
        """Atomically bind one unconsumed suspended/cancelled predecessor.

        Repeating the same successor identity is idempotent and returns the
        already-bound predecessor.  ``requested_run_id`` may only narrow or
        confirm the durable candidate; it cannot create a link by itself.
        """

        ...


__all__ = [
    "RunBundleLedger",
    "RunBundleContinuationError",
    "RunBundleContinuationLedger",
    "RunBundleLedgerConflictError",
    "RunBundleLedgerError",
    "RunBundleLedgerIntegrityError",
    "RunBundleProjectionDetailsLedger",
    "RunBundleCompactDetailsLedger",
    "RunBundleLedgerScopeError",
]

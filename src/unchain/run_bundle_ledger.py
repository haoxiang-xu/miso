"""Durable receipt and projection ledger for ``unchain.run_bundle.v1``.

The protocol is deliberately independent from provider-turn recovery.  A
provider result may be useful to resume inference, but only immutable
``ProviderCallReceipt`` rows are valid accounting evidence.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .run_bundle import ProviderCallReceipt, RunBundle, RunIdentity


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
    ) -> RunBundle | None:
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
    "RunBundleLedgerScopeError",
]

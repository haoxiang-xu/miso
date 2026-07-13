"""Execution ownership primitives for single-writer agent runs.

An execution lease answers a narrower question than a session revision: which
worker is currently allowed to advance an execution?  The monotonically
increasing fencing token makes an old worker permanently distinguishable from
the worker that takes over after a release or expiry.

This module deliberately depends only on the Python standard library.  Session
stores implement :class:`ExecutionLeaseStore`; higher-level runtime and memory
code can then share the same guard without creating package import cycles.
"""

from __future__ import annotations

import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator, Protocol, runtime_checkable


def _validate_identifier(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _validate_positive_int(value: object, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _validate_expected_revision(value: int | None) -> int | None:
    if value is not None and (
        isinstance(value, bool) or not isinstance(value, int) or value < 0
    ):
        raise ValueError("expected_revision must be a non-negative integer")
    return value


@dataclass(frozen=True, slots=True)
class ExecutionFence:
    """Stable proof of ownership attached to a durable execution write."""

    execution_id: str
    owner_id: str
    fencing_token: int

    def __post_init__(self) -> None:
        _validate_identifier(self.execution_id, name="execution_id")
        _validate_identifier(self.owner_id, name="owner_id")
        _validate_positive_int(self.fencing_token, name="fencing_token")


@dataclass(frozen=True, slots=True)
class ExecutionLease:
    """A time-bounded execution fence returned by a lease store."""

    execution_id: str
    owner_id: str
    fencing_token: int
    acquired_at_ms: int
    expires_at_ms: int

    def __post_init__(self) -> None:
        _validate_identifier(self.execution_id, name="execution_id")
        _validate_identifier(self.owner_id, name="owner_id")
        _validate_positive_int(self.fencing_token, name="fencing_token")
        if (
            isinstance(self.acquired_at_ms, bool)
            or not isinstance(self.acquired_at_ms, int)
            or self.acquired_at_ms < 0
        ):
            raise ValueError("acquired_at_ms must be a non-negative integer")
        if (
            isinstance(self.expires_at_ms, bool)
            or not isinstance(self.expires_at_ms, int)
            or self.expires_at_ms <= self.acquired_at_ms
        ):
            raise ValueError("expires_at_ms must be greater than acquired_at_ms")

    @property
    def fence(self) -> ExecutionFence:
        return ExecutionFence(
            execution_id=self.execution_id,
            owner_id=self.owner_id,
            fencing_token=self.fencing_token,
        )


@dataclass(frozen=True, slots=True)
class ExecutionLeaseConfig:
    """Timing policy for an :class:`ExecutionRuntime` guard."""

    ttl_ms: int = 60_000
    heartbeat_interval_ms: int = 20_000

    def __post_init__(self) -> None:
        _validate_positive_int(self.ttl_ms, name="ttl_ms")
        if (
            isinstance(self.heartbeat_interval_ms, bool)
            or not isinstance(self.heartbeat_interval_ms, int)
            or self.heartbeat_interval_ms < 0
        ):
            raise ValueError("heartbeat_interval_ms must be a non-negative integer")
        if self.heartbeat_interval_ms >= self.ttl_ms and self.heartbeat_interval_ms:
            raise ValueError("heartbeat_interval_ms must be smaller than ttl_ms")


class ExecutionLeaseError(RuntimeError):
    """Base class for execution ownership failures."""

    code = "execution_lease_error"

    def __init__(
        self,
        message: str | None = None,
        *,
        execution_id: str | None = None,
        owner_id: str | None = None,
        fencing_token: int | None = None,
    ) -> None:
        self.execution_id = execution_id
        self.owner_id = owner_id
        self.fencing_token = fencing_token
        detail = message or self.code.replace("_", " ")
        context = []
        if execution_id is not None:
            context.append(f"execution_id={execution_id!r}")
        if owner_id is not None:
            context.append(f"owner_id={owner_id!r}")
        if fencing_token is not None:
            context.append(f"fencing_token={fencing_token}")
        if context:
            detail = f"{detail}: {', '.join(context)}"
        super().__init__(detail)


class ExecutionLeaseConflictError(ExecutionLeaseError):
    """Raised when another worker currently owns the execution."""

    code = "execution_lease_conflict"


class ExecutionLeaseExpiredError(ExecutionLeaseError):
    """Raised when a worker presents a lease whose TTL has elapsed."""

    code = "execution_lease_expired"


class ExecutionLeaseNotOwnedError(ExecutionLeaseError):
    """Raised when the presented owner does not own the current lease."""

    code = "execution_lease_not_owned"


class StaleExecutionLeaseError(ExecutionLeaseConflictError):
    """Raised when a newer fencing token has superseded this worker."""

    code = "stale_execution_lease"


class ActiveExecutionLeaseError(ExecutionLeaseConflictError):
    """Raised when an unfenced operation would bypass an active lease."""

    code = "active_execution_lease"


@runtime_checkable
class ExecutionLeaseStore(Protocol):
    """Atomic lease operations required by :class:`ExecutionRuntime`."""

    def acquire_lease(
        self,
        execution_id: str,
        owner_id: str,
        ttl_ms: int,
        *,
        expected_revision: int | None = None,
    ) -> ExecutionLease:
        ...

    def verify_lease(
        self,
        execution_id: str,
        owner_id: str,
        fencing_token: int,
    ) -> ExecutionLease:
        ...

    def renew_lease(
        self,
        execution_id: str,
        owner_id: str,
        fencing_token: int,
        ttl_ms: int,
    ) -> ExecutionLease:
        ...

    def release_lease(
        self,
        execution_id: str,
        owner_id: str,
        fencing_token: int,
    ) -> None:
        ...


_LEASE_METHODS = (
    "acquire_lease",
    "verify_lease",
    "renew_lease",
    "release_lease",
)


def supports_execution_leases(store: object) -> bool:
    """Return ``True`` only when the complete lease capability is callable.

    Structural protocols can otherwise make a partially upgraded session store
    look usable until the first missing operation is reached.  Checking every
    method here keeps capability negotiation fail-closed.
    """

    return all(callable(getattr(store, method, None)) for method in _LEASE_METHODS)


class ExecutionRuntime:
    """Creates and scopes execution guards backed by an atomic lease store."""

    def __init__(
        self,
        store: ExecutionLeaseStore,
        config: ExecutionLeaseConfig | None = None,
    ) -> None:
        if not supports_execution_leases(store):
            missing = [
                method
                for method in _LEASE_METHODS
                if not callable(getattr(store, method, None))
            ]
            raise TypeError(
                "execution lease store capability is incomplete; missing callable "
                + ", ".join(missing)
            )
        self.store = store
        self.config = config or ExecutionLeaseConfig()

    def acquire(
        self,
        execution_id: str,
        owner_id: str | None = None,
        *,
        expected_revision: int | None = None,
    ) -> ExecutionGuard:
        execution_id = _validate_identifier(execution_id, name="execution_id")
        owner_id = _validate_identifier(
            owner_id if owner_id is not None else str(uuid.uuid4()),
            name="owner_id",
        )
        expected_revision = _validate_expected_revision(expected_revision)
        lease = self.store.acquire_lease(
            execution_id,
            owner_id,
            self.config.ttl_ms,
            expected_revision=expected_revision,
        )
        lease = _validate_store_lease(
            lease,
            execution_id=execution_id,
            owner_id=owner_id,
        )
        return ExecutionGuard(runtime=self, lease=lease)

    @contextmanager
    def scope(
        self,
        execution_id: str,
        owner_id: str | None = None,
        *,
        expected_revision: int | None = None,
    ) -> Iterator[ExecutionGuard]:
        guard = self.acquire(
            execution_id,
            owner_id,
            expected_revision=expected_revision,
        )
        try:
            yield guard
        except BaseException as body_error:
            try:
                guard.release()
            except Exception as release_error:
                body_error.add_note(
                    "execution lease cleanup also failed: "
                    f"{type(release_error).__name__}: {release_error}"
                )
            raise
        else:
            guard.release()


def _validate_store_lease(
    lease: object,
    *,
    execution_id: str,
    owner_id: str,
    fencing_token: int | None = None,
) -> ExecutionLease:
    if not isinstance(lease, ExecutionLease):
        raise ExecutionLeaseError(
            "execution lease store returned an invalid lease",
            execution_id=execution_id,
            owner_id=owner_id,
            fencing_token=fencing_token,
        )
    if lease.execution_id != execution_id or lease.owner_id != owner_id:
        raise ExecutionLeaseNotOwnedError(
            "execution lease store returned ownership for a different execution",
            execution_id=execution_id,
            owner_id=owner_id,
            fencing_token=fencing_token,
        )
    if fencing_token is not None and lease.fencing_token != fencing_token:
        raise StaleExecutionLeaseError(
            "execution lease store changed the fencing token unexpectedly",
            execution_id=execution_id,
            owner_id=owner_id,
            fencing_token=fencing_token,
        )
    return lease


class ExecutionGuard:
    """Mutable ownership handle shared across one logical agent execution."""

    def __init__(self, *, runtime: ExecutionRuntime, lease: ExecutionLease) -> None:
        self._runtime = runtime
        self._lease = lease
        self._state = "active"
        self._lock = threading.RLock()
        self._heartbeat_error: Exception | None = None
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None
        with self._lock:
            self._start_heartbeat_locked()

    @property
    def lease(self) -> ExecutionLease:
        with self._lock:
            return self._lease

    @property
    def fence(self) -> ExecutionFence:
        with self._lock:
            self._ensure_active_locked()
            return self._lease.fence

    def _not_active_error_locked(self) -> ExecutionLeaseNotOwnedError:
        detail = (
            "execution lease is released while waiting"
            if self._state == "waiting"
            else "execution lease guard has been released"
        )
        return ExecutionLeaseNotOwnedError(
            detail,
            execution_id=self._lease.execution_id,
            owner_id=self._lease.owner_id,
            fencing_token=self._lease.fencing_token,
        )

    def _ensure_active_locked(self) -> None:
        if self._state != "active":
            raise self._not_active_error_locked()
        if self._heartbeat_error is not None:
            raise self._heartbeat_error

    def assert_active(self) -> ExecutionLease:
        """Atomically verify that this guard still owns its fencing token."""

        with self._lock:
            self._ensure_active_locked()
            current = self._lease
            verified = self._runtime.store.verify_lease(
                current.execution_id,
                current.owner_id,
                current.fencing_token,
            )
            self._lease = _validate_store_lease(
                verified,
                execution_id=current.execution_id,
                owner_id=current.owner_id,
                fencing_token=current.fencing_token,
            )
            return self._lease

    def renew(self) -> ExecutionLease:
        """Extend the current lease without changing its fencing token."""

        with self._lock:
            self._ensure_active_locked()
            return self._renew_locked()

    def _renew_locked(self) -> ExecutionLease:
        current = self._lease
        renewed = self._runtime.store.renew_lease(
            current.execution_id,
            current.owner_id,
            current.fencing_token,
            self._runtime.config.ttl_ms,
        )
        renewed = _validate_store_lease(
            renewed,
            execution_id=current.execution_id,
            owner_id=current.owner_id,
            fencing_token=current.fencing_token,
        )
        if renewed.acquired_at_ms != current.acquired_at_ms:
            raise ExecutionLeaseError(
                "execution lease store changed acquired_at_ms during renewal",
                execution_id=current.execution_id,
                owner_id=current.owner_id,
                fencing_token=current.fencing_token,
            )
        if renewed.expires_at_ms < current.expires_at_ms:
            raise ExecutionLeaseError(
                "execution lease store shortened the lease during renewal",
                execution_id=current.execution_id,
                owner_id=current.owner_id,
                fencing_token=current.fencing_token,
            )
        self._lease = renewed
        return renewed

    def release(self) -> None:
        """Release ownership permanently; repeated calls are harmless."""

        thread = self._request_heartbeat_stop()
        try:
            with self._lock:
                if self._state == "released":
                    pass
                elif self._state == "waiting":
                    self._state = "released"
                    self._heartbeat_error = None
                else:
                    current = self._lease
                    self._runtime.store.release_lease(
                        current.execution_id,
                        current.owner_id,
                        current.fencing_token,
                    )
                    self._state = "released"
                    self._heartbeat_error = None
        finally:
            self._join_heartbeat(thread)

    def release_for_wait(self) -> None:
        """Release remotely while retaining permission to reacquire this guard."""

        thread = self._request_heartbeat_stop()
        try:
            with self._lock:
                if self._state == "waiting":
                    pass
                elif self._state != "active":
                    raise self._not_active_error_locked()
                else:
                    current = self._lease
                    self._runtime.store.release_lease(
                        current.execution_id,
                        current.owner_id,
                        current.fencing_token,
                    )
                    self._state = "waiting"
                    self._heartbeat_error = None
        finally:
            self._join_heartbeat(thread)

    def reacquire(
        self,
        *,
        expected_revision: int | None = None,
    ) -> ExecutionLease:
        """Acquire a newer token after :meth:`release_for_wait`."""

        expected_revision = _validate_expected_revision(expected_revision)
        with self._lock:
            if self._state != "waiting":
                raise ActiveExecutionLeaseError(
                    "execution guard can only reacquire after release_for_wait",
                    execution_id=self._lease.execution_id,
                    owner_id=self._lease.owner_id,
                    fencing_token=self._lease.fencing_token,
                )
            previous = self._lease
            lease = self._runtime.store.acquire_lease(
                previous.execution_id,
                previous.owner_id,
                self._runtime.config.ttl_ms,
                expected_revision=expected_revision,
            )
            lease = _validate_store_lease(
                lease,
                execution_id=previous.execution_id,
                owner_id=previous.owner_id,
            )
            if lease.fencing_token <= previous.fencing_token:
                raise StaleExecutionLeaseError(
                    "reacquiring an execution must advance its fencing token",
                    execution_id=previous.execution_id,
                    owner_id=previous.owner_id,
                    fencing_token=lease.fencing_token,
                )
            self._lease = lease
            self._state = "active"
            self._heartbeat_error = None
            self._heartbeat_stop = threading.Event()
            self._start_heartbeat_locked()
            return lease

    def guard_model_io(self, delegate: Any, operation: str) -> Any:
        """Wrap ``fetch_turn`` with a pre-renew and post-response fence check."""

        if not callable(getattr(delegate, "fetch_turn", None)):
            raise TypeError("delegate must define fetch_turn(request)")
        operation = _validate_identifier(operation, name="operation")
        return _GuardedModelIO(self, delegate, operation)

    def _start_heartbeat_locked(self) -> None:
        interval_ms = self._runtime.config.heartbeat_interval_ms
        if interval_ms == 0:
            self._heartbeat_thread = None
            return
        thread = threading.Thread(
            target=self._heartbeat_loop,
            name=f"unchain-execution-heartbeat-{self._lease.fencing_token}",
            daemon=True,
        )
        self._heartbeat_thread = thread
        thread.start()

    def _request_heartbeat_stop(self) -> threading.Thread | None:
        self._heartbeat_stop.set()
        with self._lock:
            return self._heartbeat_thread

    @staticmethod
    def _join_heartbeat(thread: threading.Thread | None) -> None:
        if thread is not None and thread is not threading.current_thread():
            thread.join()

    def _heartbeat_loop(self) -> None:
        interval_seconds = self._runtime.config.heartbeat_interval_ms / 1_000
        while not self._heartbeat_stop.wait(interval_seconds):
            with self._lock:
                if self._heartbeat_stop.is_set() or self._state != "active":
                    return
                try:
                    self._renew_locked()
                except Exception as exc:
                    self._heartbeat_error = exc
                    self._heartbeat_stop.set()
                    return


class _GuardedModelIO:
    """Small structural ModelIO wrapper used at external call boundaries."""

    def __init__(self, guard: ExecutionGuard, delegate: Any, operation: str) -> None:
        self._guard = guard
        self._delegate = delegate
        self.operation = operation

    @property
    def provider(self) -> Any:
        return getattr(self._delegate, "provider", None)

    def fetch_turn(self, request: Any) -> Any:
        self._guard.renew()
        result = self._delegate.fetch_turn(request)
        self._guard.assert_active()
        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)


__all__ = [
    "ActiveExecutionLeaseError",
    "ExecutionFence",
    "ExecutionGuard",
    "ExecutionLease",
    "ExecutionLeaseConfig",
    "ExecutionLeaseConflictError",
    "ExecutionLeaseError",
    "ExecutionLeaseExpiredError",
    "ExecutionLeaseNotOwnedError",
    "ExecutionLeaseStore",
    "ExecutionRuntime",
    "StaleExecutionLeaseError",
    "supports_execution_leases",
]

from __future__ import annotations

import traceback

from unchain.durability import (
    DurablePersistenceBoundaryError,
    DurablePersistenceTraversalExhaustedError,
    MAX_DURABLE_FAILURE_TRAVERSAL_DEPTH,
    MAX_DURABLE_FAILURE_TRAVERSAL_NODES,
    find_durable_persistence_failure,
    is_durable_persistence_failure,
    mark_durable_persistence_failure,
)
from unchain.context.runtime import ContextRuntime
from unchain.retry.types import RetriesExhaustedError


class _UnmarkableError(Exception):
    def __setattr__(self, name: str, value: object) -> None:
        if name == "_unchain_durable_persistence_failure":
            raise TypeError("immutable exception")
        super().__setattr__(name, value)


class _RealErrorWrapper(RuntimeError):
    def __init__(self, real: BaseException) -> None:
        super().__init__(str(real))
        self.real = real


def test_mutable_durable_failure_is_marked_without_changing_identity() -> None:
    failure = RuntimeError("journal write failed")

    marked = mark_durable_persistence_failure(failure)

    assert marked is failure
    assert find_durable_persistence_failure(failure) is failure
    assert is_durable_persistence_failure(failure) is True


def test_unmarkable_durable_failure_is_wrapped_with_original_cause() -> None:
    failure = _UnmarkableError("extension failure")

    marked = mark_durable_persistence_failure(failure)

    assert isinstance(marked, DurablePersistenceBoundaryError)
    assert marked.original is failure
    assert marked.__cause__ is None
    assert find_durable_persistence_failure(marked) is marked


def test_boundary_wrapper_diagnostics_never_render_original_exception_data() -> None:
    secret = "vault-token-super-secret"
    failure = _UnmarkableError(secret, {"password": secret})

    marked = mark_durable_persistence_failure(failure)

    assert isinstance(marked, DurablePersistenceBoundaryError)
    assert secret not in str(marked)
    assert secret not in repr(marked)
    assert marked.args == (DurablePersistenceBoundaryError.code,)


def test_boundary_wrapper_tracebacks_never_expand_original_exception_data() -> None:
    secret = "traceback-secret-value"
    failure = _UnmarkableError(secret)
    marked = mark_durable_persistence_failure(failure)
    assert isinstance(marked, DurablePersistenceBoundaryError)

    direct = "".join(traceback.format_exception(marked))
    assert secret not in direct

    caught = None
    try:
        try:
            raise failure
        except _UnmarkableError as original:
            boundary = mark_durable_persistence_failure(original)
            raise boundary from None
    except DurablePersistenceBoundaryError as error:
        caught = error

    assert caught is not None
    rendered = "".join(traceback.format_exception(caught))
    assert secret not in rendered


def test_find_has_explicit_depth_and_node_bounds_for_adversarial_groups() -> None:
    depth_secret = "durable-beyond-depth-secret"
    deep_durable = mark_durable_persistence_failure(RuntimeError(depth_secret))
    deep: BaseException = deep_durable
    for index in range(MAX_DURABLE_FAILURE_TRAVERSAL_DEPTH + 1):
        wrapper = RuntimeError(f"wrapper-{index}")
        wrapper.original = deep
        deep = wrapper

    depth_result = find_durable_persistence_failure(deep)
    assert isinstance(depth_result, DurablePersistenceTraversalExhaustedError)
    assert is_durable_persistence_failure(deep) is True
    assert is_durable_persistence_failure(depth_result) is True
    assert depth_secret not in str(depth_result)
    assert depth_secret not in repr(depth_result)
    assert depth_secret not in "".join(traceback.format_exception(depth_result))

    node_secret = "durable-beyond-node-secret"
    wide_durable = mark_durable_persistence_failure(RuntimeError(node_secret))
    wide = ExceptionGroup(
        "wide",
        [
            ValueError(f"ordinary-{index}")
            for index in range(MAX_DURABLE_FAILURE_TRAVERSAL_NODES)
        ]
        + [wide_durable],
    )

    node_result = find_durable_persistence_failure(wide)
    assert isinstance(node_result, DurablePersistenceTraversalExhaustedError)
    assert is_durable_persistence_failure(wide) is True
    assert node_secret not in str(node_result)
    assert node_secret not in repr(node_result)
    assert node_secret not in "".join(traceback.format_exception(node_result))

    assert find_durable_persistence_failure(ValueError("ordinary shallow")) is None
    assert is_durable_persistence_failure(ValueError("ordinary shallow")) is False


def test_find_fail_closes_when_exception_links_cannot_be_read() -> None:
    secret = "hostile-attribute-secret"

    class HostileLinkError(RuntimeError):
        def __init__(self, blocked_name: str) -> None:
            super().__init__(secret)
            self.blocked_name = blocked_name

        def __getattribute__(self, name: str):
            if name == object.__getattribute__(self, "blocked_name"):
                raise RuntimeError(secret)
            return super().__getattribute__(name)

    for blocked_name in (
        "_unchain_durable_persistence_failure",
        "original",
        "real",
        "last_error",
        "__cause__",
        "__context__",
    ):
        result = find_durable_persistence_failure(
            HostileLinkError(blocked_name)
        )
        assert isinstance(result, DurablePersistenceTraversalExhaustedError)
        assert secret not in str(result)
        assert secret not in repr(result)

    class HostileExceptionGroup(ExceptionGroup):
        def __getattribute__(self, name: str):
            if name == "exceptions":
                raise RuntimeError(secret)
            return super().__getattribute__(name)

    group_result = find_durable_persistence_failure(
        HostileExceptionGroup("hostile", [ValueError("ordinary")])
    )
    assert isinstance(group_result, DurablePersistenceTraversalExhaustedError)
    assert secret not in str(group_result)
    assert secret not in repr(group_result)


def test_find_traverses_retry_real_cause_context_and_exception_groups_cycle_safely() -> None:
    durable = mark_durable_persistence_failure(RuntimeError("disk full"))
    retry = RetriesExhaustedError(last_error=durable, attempts=3)
    real = _RealErrorWrapper(retry)
    caused = RuntimeError("caused")
    caused.__cause__ = real
    contextual = RuntimeError("contextual")
    contextual.__context__ = caused
    group = ExceptionGroup("parallel failures", [ValueError("ordinary"), contextual])
    outer = RuntimeError("outer")
    outer.original = group
    group.__context__ = outer

    assert find_durable_persistence_failure(outer) is durable
    assert is_durable_persistence_failure(outer) is True
    assert is_durable_persistence_failure(ValueError("ordinary")) is False


def test_context_runtime_raises_and_latches_wrapper_for_unmarkable_sink_failure() -> None:
    failure = _UnmarkableError("native store failed")

    def fail_sink(_event: dict[str, object]) -> None:
        raise failure

    runtime = ContextRuntime._for_test(
        owner_id="context-owner",
        request_factory=lambda _context: None,  # type: ignore[arg-type]
        durable_event_sink=fail_sink,
        partial_attempt_sink=lambda _event, _error: None,
    )
    event = {"execution_id": "execution-1", "attempt_id": "attempt-1"}

    first_error = None
    try:
        runtime.persist_event(event)
    except DurablePersistenceBoundaryError as error:
        first_error = error
    else:  # pragma: no cover - assertion branch
        raise AssertionError("unmarkable persistence errors must be wrapped")

    assert first_error is not None
    assert first_error.original is failure
    rendered = "".join(traceback.format_exception(first_error))
    assert "native store failed" not in rendered
    second_error = None
    try:
        runtime.persist_event(event)
    except DurablePersistenceBoundaryError as error:
        second_error = error
    else:  # pragma: no cover - assertion branch
        raise AssertionError("the boundary failure must remain latched")
    assert second_error is first_error

from __future__ import annotations

import json

import pytest

from unchain.context import (
    BoundContextTaskStateReader,
    ContextBuildStatus,
    ContextCompileRequest,
    ContextTaskStateReadOutcome,
    PinnedTaskState,
)
from unchain.context.task_state_request_factory import (
    TaskStateContextRequestFactory,
    TaskStateContextRequestFactoryError,
)


def _state(*, oversized: bool = False) -> PinnedTaskState:
    constraints = (
        tuple(f"classified constraint {index}" for index in range(257))
        if oversized
        else ("preserve exact tool references",)
    )
    return PinnedTaskState(
        state_id="task-state-a",
        revision=3,
        objective="ship the canonical Context V2 path",
        success_criteria=("survives restart",),
        constraints=constraints,
        confirmed_decisions=("Unchain owns compilation",),
        open_questions=("when to canary",),
        active_plan=("mount the read seam",),
    )


class _Reader(BoundContextTaskStateReader):
    def __init__(self, outcome, *, binding_id: str = "binding-a", log=None):
        super().__init__(binding_id)
        self.outcome = outcome
        self.log = log
        self.calls = 0

    def read_for_context(self):
        self.calls += 1
        if self.log is not None:
            self.log.append("reader")
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class _BaseFactory:
    def __init__(self, request, *, log=None):
        self.request = request
        self.log = log
        self.calls = 0
        self.attempt = object()
        self.journal = object()

    def __call__(self, context):
        del context
        self.calls += 1
        if self.log is not None:
            self.log.append("base")
        return self.request


def _request(**changes) -> ContextCompileRequest:
    values = {
        "case": "journal-runtime",
        "source_messages": ({"role": "user", "content": "continue"},),
        "capture_quality": None,
    }
    values.update(changes)
    return ContextCompileRequest(**values)


def test_available_task_state_is_injected_after_the_canonical_base_request() -> None:
    log = []
    original = _request()
    base = _BaseFactory(original, log=log)
    state = _state()
    reader = _Reader(ContextTaskStateReadOutcome.from_state(state), log=log)
    factory = TaskStateContextRequestFactory(
        binding_id="binding-a",
        base_factory=base,
        reader=reader,
    )

    decorated = factory(object())

    assert log == ["base", "reader"]
    assert decorated is not original
    assert decorated.to_dict()["task_state"] == state.to_dict()
    assert decorated.task_state_unavailable is None
    assert decorated.capture_quality == ContextBuildStatus.COMPLETE.value
    assert decorated.source_messages == original.source_messages
    assert original.task_state is None
    assert original.capture_quality is None
    assert factory.attempt is base.attempt
    assert factory.journal is base.journal


def test_absent_task_state_records_a_complete_read_without_inventing_content() -> None:
    reader = _Reader(ContextTaskStateReadOutcome.from_state(None))
    decorated = TaskStateContextRequestFactory(
        binding_id="binding-a",
        base_factory=_BaseFactory(_request()),
        reader=reader,
    )(object())

    assert decorated.task_state is None
    assert decorated.task_state_unavailable is None
    assert decorated.capture_quality == ContextBuildStatus.COMPLETE.value


@pytest.mark.parametrize(
    "base_quality",
    (
        ContextBuildStatus.COMPLETE,
        ContextBuildStatus.PARTIAL,
        ContextBuildStatus.LEGACY,
        ContextBuildStatus.UNAVAILABLE,
    ),
)
def test_complete_task_state_read_preserves_an_existing_capture_quality(
    base_quality: ContextBuildStatus,
) -> None:
    decorated = TaskStateContextRequestFactory(
        binding_id="binding-a",
        base_factory=_BaseFactory(
            _request(capture_quality=base_quality.value)
        ),
        reader=_Reader(ContextTaskStateReadOutcome.from_state(_state())),
    )(object())

    assert decorated.capture_quality == base_quality.value


@pytest.mark.parametrize(
    "base_quality",
    (
        None,
        ContextBuildStatus.COMPLETE.value,
        ContextBuildStatus.PARTIAL.value,
        ContextBuildStatus.LEGACY.value,
    ),
)
def test_unavailable_task_state_promotes_capture_quality_without_content(
    base_quality: str | None,
) -> None:
    outcome = ContextTaskStateReadOutcome.from_state(_state(oversized=True))
    assert outcome.unavailable is not None
    decorated = TaskStateContextRequestFactory(
        binding_id="binding-a",
        base_factory=_BaseFactory(_request(capture_quality=base_quality)),
        reader=_Reader(outcome),
    )(object())

    assert decorated.task_state is None
    assert decorated.task_state_unavailable == outcome.unavailable
    assert decorated.capture_quality == ContextBuildStatus.UNAVAILABLE.value
    serialized = json.dumps(decorated.to_dict(), sort_keys=True)
    assert "classified constraint" not in serialized
    assert "ship the canonical" not in serialized


def test_binding_and_capability_validation_fail_closed() -> None:
    base = _BaseFactory(_request())
    reader = _Reader(
        ContextTaskStateReadOutcome.from_state(None),
        binding_id="binding-b",
    )

    with pytest.raises(
        TaskStateContextRequestFactoryError,
        match="another binding",
    ):
        TaskStateContextRequestFactory(
            binding_id="binding-a",
            base_factory=base,
            reader=reader,
        )
    with pytest.raises(TypeError, match="base_factory"):
        TaskStateContextRequestFactory(
            binding_id="binding-a",
            base_factory=None,
            reader=_Reader(ContextTaskStateReadOutcome.from_state(None)),
        )
    with pytest.raises(TypeError, match="BoundContextTaskStateReader"):
        TaskStateContextRequestFactory(
            binding_id="binding-a",
            base_factory=base,
            reader=object(),
        )


def test_reader_failure_and_invalid_outcome_fail_after_base_construction() -> None:
    log = []
    failed = TaskStateContextRequestFactory(
        binding_id="binding-a",
        base_factory=_BaseFactory(_request(), log=log),
        reader=_Reader(OSError("storage offline"), log=log),
    )

    with pytest.raises(
        TaskStateContextRequestFactoryError,
        match="read failed closed",
    ):
        failed(object())
    assert log == ["base", "reader"]

    invalid = TaskStateContextRequestFactory(
        binding_id="binding-a",
        base_factory=_BaseFactory(_request()),
        reader=_Reader({"capture_quality": "complete"}),
    )
    with pytest.raises(
        TaskStateContextRequestFactoryError,
        match="invalid outcome",
    ):
        invalid(object())


def test_base_factory_must_return_an_unowned_compile_request() -> None:
    reader = _Reader(ContextTaskStateReadOutcome.from_state(None))
    invalid = TaskStateContextRequestFactory(
        binding_id="binding-a",
        base_factory=_BaseFactory(object()),
        reader=reader,
    )
    with pytest.raises(
        TaskStateContextRequestFactoryError,
        match="invalid context request",
    ):
        invalid(object())
    assert reader.calls == 0

    already_owned = TaskStateContextRequestFactory(
        binding_id="binding-a",
        base_factory=_BaseFactory(_request(task_state=_state().to_dict())),
        reader=reader,
    )
    with pytest.raises(
        TaskStateContextRequestFactoryError,
        match="already owns",
    ):
        already_owned(object())
    assert reader.calls == 0

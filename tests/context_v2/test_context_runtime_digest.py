from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

from unchain.context import (
    ContextBuildEnvelope,
    ContextCompileRequest,
    ContextCompileResult,
    ContextRuntime,
    resolve_context_budget,
)


_LIMIT_2MIB = 2 * 1024 * 1024


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _make_large_request() -> ContextCompileRequest:
    return ContextCompileRequest(
        case="digest-boundary",
        source_messages=(
            {
                "role": "user",
                "content": "x" * 2_500_000,
            },
        ),
        current_generation="generation-large",
        budget=resolve_context_budget(context_window_tokens=16_384),
        provider="openai",
        model="gpt-test",
        build_id="build-large",
        execution_id="execution-large",
        generation_id="generation-large",
        attempt_id="attempt-large",
    )


def _make_envelope(*, build_id: str = "build-large") -> ContextBuildEnvelope:
    return ContextBuildEnvelope(
        build_id=build_id,
        execution_id="execution-large",
        generation_id="generation-large",
        attempt_id="attempt-large",
        provider="openai",
        model="gpt-test",
        budget=resolve_context_budget(context_window_tokens=16_384),
    )


def test_context_compile_context_large_request_reaches_compiler_not_runbundle_limit():
    request = _make_large_request()
    request_size = len(
        json.dumps(
            request.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    assert request_size > _LIMIT_2MIB
    assert request_size < 32 * 1024 * 1024

    compiler_requests = []

    class SpyCompiler:
        def compile(self, candidate: ContextCompileRequest) -> ContextCompileResult:
            compiler_requests.append(candidate)
            assert _canonical_sha256(candidate.to_dict()) == _canonical_sha256(
                request.to_dict()
            )
            return ContextCompileResult(
                messages=(),
                diagnostics={"status": "ok"},
                envelope=_make_envelope(build_id=candidate.build_id),
            )

    runtime = ContextRuntime._for_test(
        owner_id="context-v2",
        request_factory=lambda _context: request,
        compiler=SpyCompiler(),
        durable_event_sink=lambda event: None,
        partial_attempt_sink=lambda event, error: None,
    )

    result = runtime.compile_context(SimpleNamespace(event={}))

    assert len(compiler_requests) == 1
    assert result.envelope is not None
    assert result.envelope.build_id == "build-large"

from __future__ import annotations

import unchain.context.compiler as compiler_module
from unchain.context import (
    ContextCompileRequest,
    ContextCompiler,
    SourceMessageCursor,
    resolve_context_budget,
)


def test_pressure_reducer_does_not_rebuild_cursor_map_per_omitted_message(
    monkeypatch,
) -> None:
    message_count = 200
    messages = tuple(
        {
            "role": "user",
            "content": f"history-{index}-" + ("x" * 128),
        }
        for index in range(message_count)
    )
    cursors = tuple(
        SourceMessageCursor(index, f"event-{index}", index + 1)
        for index in range(message_count)
    )
    calls = 0
    original = compiler_module._source_cursor_map

    def counted(request):
        nonlocal calls
        calls += 1
        return original(request)

    monkeypatch.setattr(compiler_module, "_source_cursor_map", counted)
    result = ContextCompiler().compile(
        ContextCompileRequest(
            case="reducer-complexity-contract",
            source_messages=messages,
            semantic_events=None,
            source_message_cursors=cursors,
            fixed_overhead_tokens=0,
            budget=resolve_context_budget(context_window_tokens=4_096),
        )
    )

    assert result.diagnostics["status"] == "checkpoint_required"
    assert calls <= 10

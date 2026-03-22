import json

import pytest

from miso.memory import MemoryConfig, MemoryManager
from miso.toolkits import WorkspaceToolkit
from miso.tools import tool


def _build_openai_tool_turn(*, tool_name: str, call_id: str, arguments: dict, result: dict) -> list[dict]:
    return [
        {"role": "user", "content": "u1"},
        {"type": "function_call", "call_id": call_id, "name": tool_name, "arguments": json.dumps(arguments, ensure_ascii=False)},
        {"type": "function_call_output", "call_id": call_id, "output": json.dumps(result, ensure_ascii=False)},
        {"role": "assistant", "content": "a1"},
    ]


def _build_ollama_tool_turn(*, tool_name: str, call_id: str, arguments: dict, result: dict) -> list[dict]:
    return [
        {"role": "user", "content": "u1"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": call_id, "function": {"name": tool_name, "arguments": arguments}}],
        },
        {"role": "tool", "tool_call_id": call_id, "content": json.dumps(result, ensure_ascii=False)},
        {"role": "assistant", "content": "a1"},
    ]


def _build_anthropic_tool_turn(*, tool_name: str, call_id: str, arguments: dict, result: dict) -> list[dict]:
    return [
        {"role": "user", "content": "u1"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "thinking"},
                {"type": "tool_use", "id": call_id, "name": tool_name, "input": arguments},
            ],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": call_id, "content": json.dumps(result, ensure_ascii=False)}],
        },
        {"role": "assistant", "content": "a1"},
    ]


def _build_gemini_tool_turn(*, tool_name: str, call_id: str, arguments: dict, result: dict) -> list[dict]:
    return [
        {"role": "user", "parts": [{"text": "u1"}]},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "thinking"},
                {"type": "tool_use", "id": call_id, "name": tool_name, "input": arguments},
            ],
        },
        {
            "role": "user",
            "parts": [{"function_response": {"name": tool_name, "response": result}}],
        },
        {"role": "assistant", "content": "a1"},
    ]


@pytest.mark.parametrize(
    ("provider", "builder"),
    [
        ("openai", _build_openai_tool_turn),
        ("ollama", _build_ollama_tool_turn),
        ("anthropic", _build_anthropic_tool_turn),
        ("gemini", _build_gemini_tool_turn),
    ],
)
def test_tool_owned_deferred_compaction_supports_provider_specific_history_shapes(provider, builder):
    captured_contexts: list[dict] = []

    def _arguments_optimizer(payload, context):
        captured_contexts.append({"kind": context.kind, "messages": json.dumps(context.latest_messages, ensure_ascii=False)})
        return {"compacted": True, "provider": context.provider, "kind": context.kind, "payload_type": type(payload).__name__}

    def _result_optimizer(payload, context):
        captured_contexts.append({"kind": context.kind, "messages": json.dumps(context.latest_messages, ensure_ascii=False)})
        return {"compacted": True, "provider": context.provider, "kind": context.kind, "payload_type": type(payload).__name__}

    demo_tool = tool(
        name="demo_tool",
        func=lambda **kwargs: {},
        history_arguments_optimizer=_arguments_optimizer,
        history_result_optimizer=_result_optimizer,
    )
    demo_tool.name = "demo"

    manager = MemoryManager(config=MemoryConfig())
    session_id = f"s_compact_{provider}"
    history = (
        builder(
            tool_name="demo",
            call_id="call_old",
            arguments={"path": "old.txt", "content": "A" * 2400},
            result={"content": "B" * 2400},
        )
        + [{"role": "user", "content": "u2"}, {"role": "assistant", "content": "a2"}]
    )
    manager.commit_messages(session_id, history)

    prepared = manager.prepare_messages(
        session_id,
        [{"role": "user", "content": "u3"}],
        max_context_window_tokens=50_000,
        model="gpt-5",
        provider=provider,
        tool_resolver=lambda name: demo_tool if name == "demo" else None,
    )

    info = manager.last_prepare_info
    assert info.get("deferred_compaction_applied") is True
    assert info.get("deferred_compaction_turns_compacted") == 1
    assert captured_contexts
    for context in captured_contexts:
        assert '"u2"' in context["messages"]
        assert '"u3"' in context["messages"]
        assert '"u1"' not in context["messages"]

    if provider == "openai":
        function_call = next(msg for msg in prepared if msg.get("type") == "function_call" and msg.get("call_id") == "call_old")
        function_output = next(msg for msg in prepared if msg.get("type") == "function_call_output" and msg.get("call_id") == "call_old")
        assert json.loads(function_call["arguments"])["compacted"] is True
        assert json.loads(function_output["output"])["compacted"] is True
    elif provider == "ollama":
        assistant_tool_call = next(msg for msg in prepared if isinstance(msg.get("tool_calls"), list))
        tool_result = next(msg for msg in prepared if msg.get("role") == "tool")
        assert assistant_tool_call["tool_calls"][0]["function"]["arguments"]["compacted"] is True
        assert json.loads(tool_result["content"])["compacted"] is True
    elif provider == "anthropic":
        assistant_tool_use = next(msg for msg in prepared if isinstance(msg.get("content"), list) and any(block.get("type") == "tool_use" for block in msg["content"]))
        tool_result = next(msg for msg in prepared if isinstance(msg.get("content"), list) and any(block.get("type") == "tool_result" for block in msg["content"]))
        tool_use_block = next(block for block in assistant_tool_use["content"] if block.get("type") == "tool_use")
        tool_result_block = next(block for block in tool_result["content"] if block.get("type") == "tool_result")
        assert tool_use_block["input"]["compacted"] is True
        assert json.loads(tool_result_block["content"])["compacted"] is True
    else:
        assistant_tool_use = next(msg for msg in prepared if isinstance(msg.get("content"), list) and any(block.get("type") == "tool_use" for block in msg["content"]))
        tool_result = next(msg for msg in prepared if isinstance(msg.get("parts"), list) and any("function_response" in part for part in msg["parts"]))
        tool_use_block = next(block for block in assistant_tool_use["content"] if block.get("type") == "tool_use")
        function_response = next(part["function_response"] for part in tool_result["parts"] if "function_response" in part)
        assert tool_use_block["input"]["compacted"] is True
        assert function_response["response"]["compacted"] is True


def test_generic_deferred_compaction_falls_back_without_tool_optimizer():
    manager = MemoryManager(
        config=MemoryConfig(
            deferred_tool_compaction_max_chars=200,
            deferred_tool_compaction_preview_chars=40,
        )
    )
    session_id = "s_compact_generic"
    history = _build_openai_tool_turn(
        tool_name="unknown_tool",
        call_id="call_generic",
        arguments={"payload": "A" * 1600},
        result={"payload": "B" * 1600},
    ) + [{"role": "user", "content": "u2"}, {"role": "assistant", "content": "a2"}]
    manager.commit_messages(session_id, history)

    prepared = manager.prepare_messages(
        session_id,
        [{"role": "user", "content": "u3"}],
        max_context_window_tokens=50_000,
        model="gpt-5",
        provider="openai",
        tool_resolver=lambda name: None,
    )

    function_call = next(msg for msg in prepared if msg.get("type") == "function_call" and msg.get("call_id") == "call_generic")
    function_output = next(msg for msg in prepared if msg.get("type") == "function_call_output" and msg.get("call_id") == "call_generic")
    assert json.loads(function_call["arguments"])["compacted"] is True
    assert json.loads(function_output["output"])["compacted"] is True


def test_deferred_compaction_keeps_latest_completed_turn_raw():
    manager = MemoryManager()
    session_id = "s_keep_latest_completed_raw"
    old_arguments = {"path": "old.txt", "content": "A" * 2000}
    latest_arguments = {"path": "latest.txt", "content": "B" * 2000}
    history = (
        _build_openai_tool_turn(tool_name="demo", call_id="call_old", arguments=old_arguments, result={"ok": True})
        + _build_openai_tool_turn(tool_name="demo", call_id="call_latest", arguments=latest_arguments, result={"ok": True})
    )
    manager.commit_messages(session_id, history)

    prepared = manager.prepare_messages(
        session_id,
        [{"role": "user", "content": "u3"}],
        max_context_window_tokens=50_000,
        model="gpt-5",
        provider="openai",
        tool_resolver=lambda name: None,
    )

    old_call = next(msg for msg in prepared if msg.get("type") == "function_call" and msg.get("call_id") == "call_old")
    latest_call = next(msg for msg in prepared if msg.get("type") == "function_call" and msg.get("call_id") == "call_latest")
    assert json.loads(old_call["arguments"])["compacted"] is True
    assert json.loads(latest_call["arguments"]) == latest_arguments


def test_read_files_history_result_is_compacted_with_preview():
    tk = WorkspaceToolkit(workspace_root=".")
    manager = MemoryManager()
    session_id = "s_read_files_tool_compact"
    large_content = "header\n" + ("line\n" * 800) + "footer\n"
    history = _build_openai_tool_turn(
        tool_name="read_files",
        call_id="call_read_files",
        arguments={
            "paths": ["notes.txt", "other.txt"],
            "max_chars_per_file": 50000,
            "max_total_chars": 80000,
            "noise": "drop me",
        },
        result={
            "files": [
                {
                    "requested_path": "notes.txt",
                    "path": "/tmp/notes.txt",
                    "content": large_content,
                    "total_lines": 802,
                    "truncated": False,
                },
                {
                    "requested_path": "other.txt",
                    "path": "/tmp/other.txt",
                    "content": large_content,
                    "total_lines": 802,
                    "truncated": False,
                },
            ],
            "requested_paths": 2,
            "returned_files": 2,
            "truncated": False,
            "skipped_paths": [],
        },
    ) + [{"role": "user", "content": "u2"}, {"role": "assistant", "content": "a2"}]
    manager.commit_messages(session_id, history)

    prepared = manager.prepare_messages(
        session_id,
        [{"role": "user", "content": "u3"}],
        max_context_window_tokens=50_000,
        model="gpt-5",
        provider="openai",
        tool_resolver=tk.get,
    )

    function_call = next(msg for msg in prepared if msg.get("type") == "function_call" and msg.get("call_id") == "call_read_files")
    function_output = next(msg for msg in prepared if msg.get("type") == "function_call_output" and msg.get("call_id") == "call_read_files")
    compacted_args = json.loads(function_call["arguments"])
    compacted_result = json.loads(function_output["output"])

    assert compacted_args == {
        "paths": ["notes.txt", "other.txt"],
        "max_chars_per_file": 50000,
        "max_total_chars": 80000,
        "compacted": True,
    }
    assert compacted_result["compacted"] is True
    assert compacted_result["requested_paths"] == 2
    assert compacted_result["returned_files"] == 2
    assert compacted_result["files"][0]["content"].startswith("header")
    assert "footer" in compacted_result["files"][0]["content"]


def test_list_directories_history_result_is_compacted_with_preview():
    tk = WorkspaceToolkit(workspace_root=".")
    manager = MemoryManager()
    session_id = "s_list_directories_tool_compact"
    entries = [f"src/file_{index}.py" for index in range(80)]
    history = _build_openai_tool_turn(
        tool_name="list_directories",
        call_id="call_list_directories",
        arguments={
            "paths": ["src", "tests"],
            "recursive": True,
            "max_entries_per_directory": 200,
            "max_total_entries": 400,
            "noise": "drop me",
        },
        result={
            "directories": [
                {
                    "requested_path": "src",
                    "path": "/tmp/src",
                    "entries": entries,
                    "truncated": False,
                },
                {
                    "requested_path": "tests",
                    "path": "/tmp/tests",
                    "entries": entries,
                    "truncated": False,
                },
            ],
            "requested_paths": 2,
            "returned_directories": 2,
            "truncated": False,
            "skipped_paths": [],
        },
    ) + [{"role": "user", "content": "u2"}, {"role": "assistant", "content": "a2"}]
    manager.commit_messages(session_id, history)

    prepared = manager.prepare_messages(
        session_id,
        [{"role": "user", "content": "u3"}],
        max_context_window_tokens=50_000,
        model="gpt-5",
        provider="openai",
        tool_resolver=tk.get,
    )

    function_call = next(
        msg for msg in prepared if msg.get("type") == "function_call" and msg.get("call_id") == "call_list_directories"
    )
    function_output = next(
        msg for msg in prepared if msg.get("type") == "function_call_output" and msg.get("call_id") == "call_list_directories"
    )
    compacted_args = json.loads(function_call["arguments"])
    compacted_result = json.loads(function_output["output"])

    assert compacted_args == {
        "paths": ["src", "tests"],
        "recursive": True,
        "max_entries_per_directory": 200,
        "max_total_entries": 400,
        "compacted": True,
    }
    assert compacted_result["compacted"] is True
    assert compacted_result["requested_paths"] == 2
    assert compacted_result["returned_directories"] == 2
    assert compacted_result["directories"][0]["entry_count"] == 80
    assert "... <omitted" in compacted_result["directories"][0]["entries"][10]


def test_write_file_history_arguments_are_compacted_by_tool_optimizer():
    tk = WorkspaceToolkit(workspace_root=".")
    manager = MemoryManager()
    session_id = "s_write_file_tool_compact"
    large_content = "A" * 2400
    history = _build_openai_tool_turn(
        tool_name="write_file",
        call_id="call_write_file",
        arguments={"path": "notes.txt", "append": True, "content": large_content},
        result={"path": "/tmp/notes.txt", "bytes_written": len(large_content.encode("utf-8")), "append": True},
    ) + [{"role": "user", "content": "u2"}, {"role": "assistant", "content": "a2"}]
    manager.commit_messages(session_id, history)

    prepared = manager.prepare_messages(
        session_id,
        [{"role": "user", "content": "u3"}],
        max_context_window_tokens=50_000,
        model="gpt-5",
        provider="openai",
        tool_resolver=tk.get,
    )

    function_call = next(msg for msg in prepared if msg.get("type") == "function_call" and msg.get("call_id") == "call_write_file")
    compacted_args = json.loads(function_call["arguments"])

    assert compacted_args["path"] == "notes.txt"
    assert compacted_args["append"] is True
    assert compacted_args["compacted"] is True
    assert compacted_args["content"]["compacted"] is True
    assert compacted_args["content"]["chars"] == len(large_content)

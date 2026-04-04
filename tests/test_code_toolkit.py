import os
import tempfile
from pathlib import Path

from unchain.kernel.types import ToolCall
from unchain.tools import ToolExecutionContext, Toolkit, execute_confirmable_tool_call
from unchain.toolkits import CodeToolkit
from unchain.toolkits.builtin.code import web_fetch as web_fetch_module


EXPECTED_CODE_TOOLS = {"read", "write", "edit", "glob", "grep", "web_fetch"}


def _merge_toolkit(source: Toolkit) -> Toolkit:
    merged = Toolkit()
    for tool_obj in source.tools.values():
        merged.register(tool_obj)
    return merged


def test_code_toolkit_registers_expected_tools_and_confirmation_contract():
    with tempfile.TemporaryDirectory() as tmp:
        toolkit = CodeToolkit(workspace_root=tmp)

        assert set(toolkit.tools.keys()) == EXPECTED_CODE_TOOLS
        assert toolkit.tools["read"].requires_confirmation is False
        assert toolkit.tools["glob"].requires_confirmation is False
        assert toolkit.tools["grep"].requires_confirmation is False
        assert toolkit.tools["write"].requires_confirmation is True
        assert toolkit.tools["edit"].requires_confirmation is True
        assert toolkit.tools["web_fetch"].requires_confirmation is True


def test_code_toolkit_requires_full_read_before_mutating_existing_files():
    with tempfile.TemporaryDirectory() as tmp:
        toolkit = CodeToolkit(workspace_root=tmp)
        target = Path(tmp, "demo.txt").resolve()
        target.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")

        partial = toolkit.execute("read", {"path": str(target), "offset": 1, "limit": 1})
        assert partial["content"] == "2\tbeta"
        denied = toolkit.execute("write", {"path": str(target), "content": "replaced\n"})
        assert "partially read" in denied["error"]

        full = toolkit.execute("read", {"path": str(target)})
        assert full["start_line"] == 1
        assert full["end_line"] == 3
        updated = toolkit.execute(
            "edit",
            {
                "path": str(target),
                "old_string": "beta",
                "new_string": "BETA",
            },
        )
        assert updated["replacement_count"] == 1
        assert target.read_text(encoding="utf-8") == "alpha\nBETA\ngamma\n"


def test_code_toolkit_write_can_create_new_file_without_prior_read():
    with tempfile.TemporaryDirectory() as tmp:
        toolkit = CodeToolkit(workspace_root=tmp)
        target = Path(tmp, "notes.txt").resolve()

        result = toolkit.execute("write", {"path": str(target), "content": "hello\nworld\n"})

        assert result["operation"] == "create"
        assert target.read_text(encoding="utf-8") == "hello\nworld\n"


def test_code_toolkit_rejects_stale_snapshot_and_skips_binary_reads():
    with tempfile.TemporaryDirectory() as tmp:
        toolkit = CodeToolkit(workspace_root=tmp)
        text_file = Path(tmp, "stale.txt").resolve()
        text_file.write_text("one\ntwo\n", encoding="utf-8")

        toolkit.execute("read", {"path": str(text_file)})
        text_file.write_text("changed\nexternally\n", encoding="utf-8")
        stale = toolkit.execute(
            "edit",
            {"path": str(text_file), "old_string": "changed", "new_string": "CHANGED"},
        )
        assert "changed since it was last read" in stale["error"]

        image_file = Path(tmp, "image.png").resolve()
        image_file.write_bytes(b"\x89PNG\r\n\x1a\nbinary")
        skipped = toolkit.execute("read", {"path": str(image_file)})
        assert skipped["file_kind"] == "image"
        assert skipped["skipped"] is True


def test_code_toolkit_glob_and_grep_return_sorted_and_paginated_results():
    with tempfile.TemporaryDirectory() as tmp:
        toolkit = CodeToolkit(workspace_root=tmp)
        src_dir = Path(tmp, "src")
        src_dir.mkdir()
        first = (src_dir / "a.py").resolve()
        second = (src_dir / "b.py").resolve()
        other = (src_dir / "note.txt").resolve()
        first.write_text("alpha\nbeta\n", encoding="utf-8")
        second.write_text("beta\ngamma\n", encoding="utf-8")
        other.write_text("beta\n", encoding="utf-8")
        os.utime(first, ns=(1_000_000_000, 1_000_000_000))
        os.utime(second, ns=(2_000_000_000, 2_000_000_000))

        glob_result = toolkit.execute("glob", {"pattern": "**/*.py", "path": str(src_dir.resolve())})
        assert glob_result["matches"] == [str(second), str(first)]

        content_result = toolkit.execute(
            "grep",
            {
                "pattern": "beta",
                "path": str(src_dir.resolve()),
                "glob": "*.py",
                "output_mode": "content",
                "context": 1,
                "head_limit": 1,
            },
        )
        assert content_result["match_count"] == 2
        assert len(content_result["matches"]) == 1
        assert content_result["matches"][0]["line"] == 2

        files_result = toolkit.execute(
            "grep",
            {
                "pattern": "beta",
                "path": str(src_dir.resolve()),
                "glob": "*.py",
                "output_mode": "files_with_matches",
                "head_limit": 10,
            },
        )
        assert set(files_result["files"]) == {str(first), str(second)}

        count_result = toolkit.execute(
            "grep",
            {
                "pattern": "beta",
                "path": str(src_dir.resolve()),
                "glob": "*.py",
                "output_mode": "count",
            },
        )
        assert count_result["match_count"] == 2
        assert count_result["files_with_matches"] == 2


def test_execute_confirmable_tool_call_pushes_code_toolkit_execution_context_by_session():
    with tempfile.TemporaryDirectory() as tmp:
        toolkit = CodeToolkit(workspace_root=tmp)
        merged = _merge_toolkit(toolkit)
        target = Path(tmp, "demo.txt").resolve()
        target.write_text("alpha\nbeta\n", encoding="utf-8")

        read_outcome = execute_confirmable_tool_call(
            toolkit=merged,
            tool_call=ToolCall(call_id="call_read", name="read", arguments={"path": str(target)}),
            on_tool_confirm=None,
            loop=None,
            callback=None,
            run_id="run-1",
            iteration=0,
            execution_context=ToolExecutionContext(
                session_id="session-a",
                run_id="run-1",
                provider="openai",
                model="gpt-5",
                iteration=0,
            ),
        )
        assert read_outcome.tool_result["path"] == str(target)
        assert toolkit.current_execution_context is None

        other_session_outcome = execute_confirmable_tool_call(
            toolkit=merged,
            tool_call=ToolCall(
                call_id="call_write_other",
                name="write",
                arguments={"path": str(target), "content": "from other session\n"},
            ),
            on_tool_confirm=None,
            loop=None,
            callback=None,
            run_id="run-2",
            iteration=1,
            execution_context=ToolExecutionContext(
                session_id="session-b",
                run_id="run-2",
                provider="openai",
                model="gpt-5",
                iteration=1,
            ),
        )
        assert "fully read" in other_session_outcome.tool_result["error"]

        same_session_outcome = execute_confirmable_tool_call(
            toolkit=merged,
            tool_call=ToolCall(
                call_id="call_write_same",
                name="write",
                arguments={"path": str(target), "content": "from same session\n"},
            ),
            on_tool_confirm=None,
            loop=None,
            callback=None,
            run_id="run-3",
            iteration=2,
            execution_context=ToolExecutionContext(
                session_id="session-a",
                run_id="run-3",
                provider="openai",
                model="gpt-5",
                iteration=2,
            ),
        )
        assert same_session_outcome.tool_result["operation"] == "update"
        assert target.read_text(encoding="utf-8") == "from same session\n"


def test_code_toolkit_web_fetch_raw_uses_cache_and_paginates(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        toolkit = CodeToolkit(workspace_root=tmp)
        monkeypatch.setattr(web_fetch_module, "validate_public_url", lambda url: (url, None))

        calls = {"count": 0}

        def fake_request(url: str):
            calls["count"] += 1
            return (
                {
                    "ok": True,
                    "url": url,
                    "final_url": url,
                    "host": "example.com",
                    "status_code": 200,
                    "content_type": "text/plain",
                    "file_kind": "text",
                    "result": "",
                    "content_length": len("alpha beta gamma delta"),
                    "returned_chars": 0,
                    "truncated": False,
                    "next_offset": None,
                    "cached": False,
                    "redirect": None,
                    "skipped": False,
                    "error": "",
                },
                "alpha beta gamma delta",
            )

        monkeypatch.setattr(toolkit._web_fetch_service, "_request", fake_request)

        first = toolkit.execute(
            "web_fetch",
            {"url": "https://example.com/docs", "mode": "raw", "offset": 6, "max_chars": 4},
        )
        assert first["ok"] is True
        assert first["result"] == "beta"
        assert first["truncated"] is True
        assert first["next_offset"] == 10
        assert first["cached"] is False

        second = toolkit.execute(
            "web_fetch",
            {"url": "https://example.com/docs", "mode": "raw", "offset": 0, "max_chars": 5},
        )
        assert second["result"] == "alpha"
        assert second["cached"] is True
        assert calls["count"] == 1


def test_code_toolkit_web_fetch_extract_uses_runtime_config(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        toolkit = CodeToolkit(workspace_root=tmp)
        merged = _merge_toolkit(toolkit)
        monkeypatch.setattr(web_fetch_module, "validate_public_url", lambda url: (url, None))

        monkeypatch.setattr(
            toolkit._web_fetch_service,
            "_request",
            lambda url: (
                {
                    "ok": True,
                    "url": url,
                    "final_url": url,
                    "host": "example.com",
                    "status_code": 200,
                    "content_type": "text/html",
                    "file_kind": "text",
                    "result": "",
                    "content_length": 21,
                    "returned_chars": 0,
                    "truncated": False,
                    "next_offset": None,
                    "cached": False,
                    "redirect": None,
                    "skipped": False,
                    "error": "",
                },
                "React documentation body",
            ),
        )

        seen: dict[str, object] = {}

        def fake_extract(*, url: str, content: str, prompt: str, extract_model_config: dict[str, object]) -> str:
            seen["url"] = url
            seen["content"] = content
            seen["prompt"] = prompt
            seen["config"] = dict(extract_model_config)
            return "summary output"

        monkeypatch.setattr("unchain.toolkits.builtin.code.code.run_extract_model", fake_extract)

        outcome = execute_confirmable_tool_call(
            toolkit=merged,
            tool_call=ToolCall(
                call_id="call_fetch_extract",
                name="web_fetch",
                arguments={
                    "url": "https://example.com/react",
                    "mode": "extract",
                    "prompt": "Summarize the docs changes",
                },
            ),
            on_tool_confirm=None,
            loop=None,
            callback=None,
            run_id="run-fetch",
            iteration=0,
            execution_context=ToolExecutionContext(
                session_id="session-fetch",
                run_id="run-fetch",
                provider="openai",
                model="gpt-5",
                iteration=0,
                tool_runtime_config={
                    "web_fetch": {
                        "extract_model": {
                            "provider": "openai",
                            "model": "gpt-5-mini",
                            "payload": {"store": False},
                        }
                    }
                },
            ),
        )

        assert outcome.tool_result["ok"] is True
        assert outcome.tool_result["result"] == "summary output"
        assert seen["prompt"] == "Summarize the docs changes"
        assert seen["config"] == {
            "provider": "openai",
            "model": "gpt-5-mini",
            "payload": {"store": False},
        }


def test_code_toolkit_web_fetch_rejects_private_urls_and_requires_extract_config(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        toolkit = CodeToolkit(workspace_root=tmp)

        denied = toolkit.execute("web_fetch", {"url": "http://localhost:3000", "mode": "raw"})
        assert denied["ok"] is False
        assert "private or localhost" in denied["error"]

        monkeypatch.setattr(web_fetch_module, "validate_public_url", lambda url: (url, None))
        monkeypatch.setattr(
            toolkit._web_fetch_service,
            "_request",
            lambda url: (
                {
                    "ok": True,
                    "url": url,
                    "final_url": url,
                    "host": "example.com",
                    "status_code": 200,
                    "content_type": "text/plain",
                    "file_kind": "text",
                    "result": "",
                    "content_length": 12,
                    "returned_chars": 0,
                    "truncated": False,
                    "next_offset": None,
                    "cached": False,
                    "redirect": None,
                    "skipped": False,
                    "error": "",
                },
                "plain text body",
            ),
        )
        missing_config = toolkit.execute(
            "web_fetch",
            {
                "url": "https://example.com/plain",
                "mode": "extract",
                "prompt": "Summarize",
            },
        )
        assert missing_config["ok"] is False
        assert "tool_runtime_config['web_fetch']['extract_model']" in missing_config["error"]

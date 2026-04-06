import os
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

from unchain.kernel.types import ToolCall
from unchain.tools import ToolExecutionContext, Toolkit, execute_confirmable_tool_call
from unchain.input import ASK_USER_QUESTION_TOOL_NAME
from unchain.toolkits import CoreToolkit
from unchain.toolkits.builtin.core.lsp_runtime import LSPServerSpec
from unchain.toolkits.builtin.core import web_fetch as web_fetch_module
from unchain.toolkits.builtin.core.shell_runtime import ShellRuntime


EXPECTED_CORE_TOOLS = {
    ASK_USER_QUESTION_TOOL_NAME,
    "read",
    "write",
    "edit",
    "glob",
    "grep",
    "web_fetch",
    "shell",
    "lsp",
}


def _write_fake_lsp_server(root: Path) -> Path:
    script_path = root / "fake_lsp_server.py"
    script_path.write_text(
        textwrap.dedent(
            """
            import json
            import sys
            from pathlib import Path
            from urllib.parse import unquote, urlparse

            ROOT_URI = ""
            OPEN_DOCS = {}


            def read_message():
                headers = {}
                while True:
                    line = sys.stdin.buffer.readline()
                    if not line:
                        return None
                    if line in {b"\\r\\n", b"\\n"}:
                        break
                    key, value = line.decode("utf-8", errors="replace").split(":", 1)
                    headers[key.strip().lower()] = value.strip()
                content_length = int(headers.get("content-length", "0"))
                if content_length <= 0:
                    return None
                body = sys.stdin.buffer.read(content_length)
                return json.loads(body.decode("utf-8", errors="replace"))


            def write_message(payload):
                body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                header = f"Content-Length: {len(body)}\\r\\n\\r\\n".encode("ascii")
                sys.stdout.buffer.write(header)
                sys.stdout.buffer.write(body)
                sys.stdout.buffer.flush()


            def uri_to_path(uri):
                parsed = urlparse(uri)
                raw_path = parsed.path or uri.replace("file://", "", 1)
                if raw_path.startswith("/") and len(raw_path) > 3 and raw_path[2] == ":":
                    raw_path = raw_path[1:]
                return Path(unquote(raw_path))


            def path_to_uri(path):
                return Path(path).resolve().as_uri()


            def current_doc_uri():
                if OPEN_DOCS:
                    return next(iter(OPEN_DOCS))
                if ROOT_URI:
                    root_path = uri_to_path(ROOT_URI)
                    return path_to_uri(root_path / "main.py")
                return path_to_uri(Path.cwd() / "main.py")


            while True:
                message = read_message()
                if message is None:
                    break

                method = message.get("method")
                if method == "initialize":
                    ROOT_URI = message.get("params", {}).get("rootUri", "")
                    write_message(
                        {
                            "jsonrpc": "2.0",
                            "id": message.get("id"),
                            "result": {"capabilities": {}},
                        }
                    )
                    continue

                if method == "initialized":
                    continue

                if method == "textDocument/didOpen":
                    params = message.get("params", {})
                    text_document = params.get("textDocument", {})
                    OPEN_DOCS[text_document.get("uri")] = text_document.get("text", "")
                    continue

                if method == "textDocument/didChange":
                    params = message.get("params", {})
                    text_document = params.get("textDocument", {})
                    changes = params.get("contentChanges", [])
                    if changes:
                        OPEN_DOCS[text_document.get("uri")] = changes[0].get("text", "")
                    continue

                if method == "shutdown":
                    write_message({"jsonrpc": "2.0", "id": message.get("id"), "result": None})
                    continue

                if method == "exit":
                    break

                if method == "textDocument/definition":
                    uri = message.get("params", {}).get("textDocument", {}).get("uri") or current_doc_uri()
                    write_message(
                        {
                            "jsonrpc": "2.0",
                            "id": message.get("id"),
                            "result": [
                                {
                                    "uri": uri,
                                    "range": {
                                        "start": {"line": 0, "character": 0},
                                        "end": {"line": 0, "character": 4},
                                    },
                                }
                            ],
                        }
                    )
                    continue

                if method == "textDocument/references":
                    uri = message.get("params", {}).get("textDocument", {}).get("uri") or current_doc_uri()
                    current_path = uri_to_path(uri)
                    ignored_path = current_path.with_name("ignored" + current_path.suffix)
                    write_message(
                        {
                            "jsonrpc": "2.0",
                            "id": message.get("id"),
                            "result": [
                                {
                                    "uri": uri,
                                    "range": {
                                        "start": {"line": 1, "character": 0},
                                        "end": {"line": 1, "character": 4},
                                    },
                                },
                                {
                                    "uri": path_to_uri(ignored_path),
                                    "range": {
                                        "start": {"line": 2, "character": 0},
                                        "end": {"line": 2, "character": 4},
                                    },
                                },
                            ],
                        }
                    )
                    continue

                if method == "textDocument/hover":
                    write_message(
                        {
                            "jsonrpc": "2.0",
                            "id": message.get("id"),
                            "result": {
                                "contents": {"kind": "plaintext", "value": "DemoType -> int"},
                                "range": {
                                    "start": {"line": 0, "character": 0},
                                    "end": {"line": 0, "character": 4},
                                },
                            },
                        }
                    )
                    continue

                if method == "textDocument/documentSymbol":
                    write_message(
                        {
                            "jsonrpc": "2.0",
                            "id": message.get("id"),
                            "result": [
                                {
                                    "name": "Demo",
                                    "kind": 5,
                                    "detail": "class",
                                    "range": {
                                        "start": {"line": 0, "character": 0},
                                        "end": {"line": 4, "character": 0},
                                    },
                                    "selectionRange": {
                                        "start": {"line": 0, "character": 0},
                                        "end": {"line": 0, "character": 4},
                                    },
                                    "children": [
                                        {
                                            "name": "method",
                                            "kind": 6,
                                            "detail": "()",
                                            "range": {
                                                "start": {"line": 1, "character": 2},
                                                "end": {"line": 3, "character": 0},
                                            },
                                            "selectionRange": {
                                                "start": {"line": 1, "character": 2},
                                                "end": {"line": 1, "character": 8},
                                            },
                                            "children": [],
                                        }
                                    ],
                                }
                            ],
                        }
                    )
                    continue

                if method == "workspace/symbol":
                    uri = current_doc_uri()
                    current_path = uri_to_path(uri)
                    util_path = current_path.with_name("util" + current_path.suffix)
                    write_message(
                        {
                            "jsonrpc": "2.0",
                            "id": message.get("id"),
                            "result": [
                                {
                                    "name": "Demo",
                                    "kind": 5,
                                    "location": {
                                        "uri": uri,
                                        "range": {
                                            "start": {"line": 0, "character": 0},
                                            "end": {"line": 0, "character": 4},
                                        },
                                    },
                                    "containerName": "",
                                },
                                {
                                    "name": "helper",
                                    "kind": 12,
                                    "location": {
                                        "uri": path_to_uri(util_path),
                                        "range": {
                                            "start": {"line": 0, "character": 0},
                                            "end": {"line": 0, "character": 6},
                                        },
                                    },
                                    "containerName": "Demo",
                                },
                            ],
                        }
                    )
                    continue

                write_message(
                    {
                        "jsonrpc": "2.0",
                        "id": message.get("id"),
                        "error": {"code": -32601, "message": f"Unhandled method: {method}"},
                    }
                )
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    return script_path


def _merge_toolkit(source: Toolkit) -> Toolkit:
    merged = Toolkit()
    for tool_obj in source.tools.values():
        merged.register(tool_obj)
    return merged


def test_core_toolkit_registers_expected_tools_and_confirmation_contract():
    with tempfile.TemporaryDirectory() as tmp:
        toolkit = CoreToolkit(workspace_root=tmp)

        assert set(toolkit.tools.keys()) == EXPECTED_CORE_TOOLS
        assert toolkit.tools[ASK_USER_QUESTION_TOOL_NAME].requires_confirmation is False
        assert toolkit.tools["read"].requires_confirmation is False
        assert toolkit.tools["glob"].requires_confirmation is False
        assert toolkit.tools["grep"].requires_confirmation is False
        assert toolkit.tools["write"].requires_confirmation is True
        assert toolkit.tools["edit"].requires_confirmation is True
        assert toolkit.tools["web_fetch"].requires_confirmation is True
        assert toolkit.tools["shell"].requires_confirmation is True
        assert toolkit.tools["lsp"].requires_confirmation is False


def test_code_toolkit_requires_full_read_before_mutating_existing_files():
    with tempfile.TemporaryDirectory() as tmp:
        toolkit = CoreToolkit(workspace_root=tmp)
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
        toolkit = CoreToolkit(workspace_root=tmp)
        target = Path(tmp, "notes.txt").resolve()

        result = toolkit.execute("write", {"path": str(target), "content": "hello\nworld\n"})

        assert result["operation"] == "create"
        assert target.read_text(encoding="utf-8") == "hello\nworld\n"


def test_code_toolkit_rejects_stale_snapshot_and_skips_binary_reads():
    with tempfile.TemporaryDirectory() as tmp:
        toolkit = CoreToolkit(workspace_root=tmp)
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
        toolkit = CoreToolkit(workspace_root=tmp)
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
        toolkit = CoreToolkit(workspace_root=tmp)
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
        toolkit = CoreToolkit(workspace_root=tmp)
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
        toolkit = CoreToolkit(workspace_root=tmp)
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

        monkeypatch.setattr("unchain.toolkits.builtin.core.core.run_extract_model", fake_extract)

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
        toolkit = CoreToolkit(workspace_root=tmp)

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


def test_code_toolkit_shell_persists_cwd_between_runs():
    with tempfile.TemporaryDirectory() as tmp:
        toolkit = CoreToolkit(workspace_root=tmp)
        subdir = Path(tmp, "subdir").resolve()
        subdir.mkdir()

        enter_command = (
            "Set-Location subdir; (Get-Location).Path"
            if sys.platform.startswith("win")
            else "cd subdir && pwd"
        )
        pwd_command = "(Get-Location).Path" if sys.platform.startswith("win") else "pwd"

        first = toolkit.execute("shell", {"action": "run", "command": enter_command})
        second = toolkit.execute("shell", {"action": "run", "command": pwd_command})

        assert first["status"] == "completed"
        assert first["cwd"] == str(subdir)
        assert str(subdir) in first["stdout"]
        assert second["status"] == "completed"
        assert second["cwd"] == str(subdir)
        assert second["stdout"].strip() == str(subdir)


def test_code_toolkit_shell_confirmation_policy_and_background_lifecycle():
    with tempfile.TemporaryDirectory() as tmp:
        toolkit = CoreToolkit(workspace_root=tmp)
        merged = _merge_toolkit(toolkit)
        confirm_requests = []

        low_risk_command = "Get-Location" if sys.platform.startswith("win") else "pwd"
        low_risk = execute_confirmable_tool_call(
            toolkit=merged,
            tool_call=ToolCall(
                call_id="call_shell_low",
                name="shell",
                arguments={"action": "run", "command": low_risk_command},
            ),
            on_tool_confirm=lambda req: confirm_requests.append(req) or {"approved": True},
            loop=None,
            callback=None,
            run_id="run-shell-low",
            iteration=0,
            execution_context=ToolExecutionContext(
                session_id="session-shell",
                run_id="run-shell-low",
                provider="openai",
                model="gpt-5",
                iteration=0,
            ),
        )
        assert low_risk.tool_result["status"] == "completed"
        assert confirm_requests == []

        high_risk_command = (
            "'boom' | Set-Content note.txt" if sys.platform.startswith("win") else "echo boom > note.txt"
        )
        high_risk = execute_confirmable_tool_call(
            toolkit=merged,
            tool_call=ToolCall(
                call_id="call_shell_high",
                name="shell",
                arguments={"action": "run", "command": high_risk_command},
            ),
            on_tool_confirm=lambda req: confirm_requests.append(req) or {"approved": True},
            loop=None,
            callback=None,
            run_id="run-shell-high",
            iteration=1,
            execution_context=ToolExecutionContext(
                session_id="session-shell",
                run_id="run-shell-high",
                provider="openai",
                model="gpt-5",
                iteration=1,
            ),
        )
        assert high_risk.tool_result["status"] == "completed"
        assert len(confirm_requests) == 1

        background_command = "Start-Sleep -Seconds 30" if sys.platform.startswith("win") else "sleep 30"
        background = execute_confirmable_tool_call(
            toolkit=merged,
            tool_call=ToolCall(
                call_id="call_shell_background",
                name="shell",
                arguments={
                    "action": "run",
                    "command": background_command,
                    "run_in_background": True,
                    "yield_time_ms": 0,
                },
            ),
            on_tool_confirm=lambda req: confirm_requests.append(req) or {"approved": True},
            loop=None,
            callback=None,
            run_id="run-shell-bg",
            iteration=2,
            execution_context=ToolExecutionContext(
                session_id="session-shell",
                run_id="run-shell-bg",
                provider="openai",
                model="gpt-5",
                iteration=2,
            ),
        )
        task_id = background.tool_result["task_id"]
        assert background.tool_result["status"] == "running"
        assert len(confirm_requests) == 2

        poll = execute_confirmable_tool_call(
            toolkit=merged,
            tool_call=ToolCall(
                call_id="call_shell_poll",
                name="shell",
                arguments={"action": "poll", "task_id": task_id},
            ),
            on_tool_confirm=lambda req: confirm_requests.append(req) or {"approved": True},
            loop=None,
            callback=None,
            run_id="run-shell-poll",
            iteration=3,
            execution_context=ToolExecutionContext(
                session_id="session-shell",
                run_id="run-shell-poll",
                provider="openai",
                model="gpt-5",
                iteration=3,
            ),
        )
        assert poll.tool_result["action"] == "poll"
        assert len(confirm_requests) == 2

        killed = execute_confirmable_tool_call(
            toolkit=merged,
            tool_call=ToolCall(
                call_id="call_shell_kill",
                name="shell",
                arguments={"action": "kill", "task_id": task_id},
            ),
            on_tool_confirm=lambda req: confirm_requests.append(req) or {"approved": True},
            loop=None,
            callback=None,
            run_id="run-shell-kill",
            iteration=4,
            execution_context=ToolExecutionContext(
                session_id="session-shell",
                run_id="run-shell-kill",
                provider="openai",
                model="gpt-5",
                iteration=4,
            ),
        )
        assert killed.tool_result["status"] == "killed"
        assert len(confirm_requests) == 2


def test_code_toolkit_shell_background_poll_returns_incremental_output():
    with tempfile.TemporaryDirectory() as tmp:
        toolkit = CoreToolkit(workspace_root=tmp)
        command = (
            "Write-Output alpha; Start-Sleep -Milliseconds 300; Write-Output beta"
            if sys.platform.startswith("win")
            else "printf 'alpha\\n'; sleep 0.3; printf 'beta\\n'"
        )
        started = toolkit.execute(
            "shell",
            {
                "action": "run",
                "command": command,
                "run_in_background": True,
                "yield_time_ms": 0,
            },
        )
        task_id = started["task_id"]
        time.sleep(0.1)
        first_poll = toolkit.execute("shell", {"action": "poll", "task_id": task_id})
        time.sleep(0.4)
        second_poll = toolkit.execute("shell", {"action": "poll", "task_id": task_id})

        assert "alpha" in (first_poll["stdout"] + second_poll["stdout"])
        assert "beta" in second_poll["stdout"]
        assert second_poll["status"] in {"completed", "timed_out"}
        assert second_poll["completed"] is True


def test_shell_runtime_detect_executor_prefers_pwsh_and_env_shell(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/opt/homebrew/bin/pwsh" if name == "pwsh" else None)
    windows_spec = ShellRuntime.detect_executor(platform_name="win32")
    assert windows_spec.family == "powershell"
    assert windows_spec.program == "/opt/homebrew/bin/pwsh"

    monkeypatch.setattr("shutil.which", lambda name: None)
    posix_spec = ShellRuntime.detect_executor(
        platform_name="linux",
        env={"SHELL": "/bin/zsh"},
    )
    assert posix_spec.family == "posix"
    assert posix_spec.program == "/bin/zsh"


def test_code_toolkit_lsp_python_operations_use_fake_server(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        script = _write_fake_lsp_server(root)
        toolkit = CoreToolkit(workspace_root=root)
        target = root / "main.py"
        target.write_text("class Demo:\n    pass\n", encoding="utf-8")
        (root / "ignored.py").write_text("ignored\n", encoding="utf-8")

        monkeypatch.setattr(
            toolkit._lsp_runtime,
            "_server_spec_for_language",
            lambda language: LSPServerSpec(language=language, server_name="fake-lsp", command=[sys.executable, "-u", str(script)]),
        )
        monkeypatch.setattr(
            toolkit._lsp_runtime,
            "_gitignored_paths",
            lambda paths, *, root: {path for path in paths if path.name == "ignored.py"},
        )

        definition = toolkit.execute(
            "lsp",
            {
                "operation": "goToDefinition",
                "file_path": str(target),
                "line": 1,
                "character": 1,
            },
        )
        references = toolkit.execute(
            "lsp",
            {
                "operation": "findReferences",
                "file_path": str(target),
                "line": 1,
                "character": 1,
            },
        )
        hover = toolkit.execute(
            "lsp",
            {
                "operation": "hover",
                "file_path": str(target),
                "line": 1,
                "character": 1,
            },
        )

        assert definition["ok"] is True
        assert definition["server"] == "fake-lsp"
        assert "Defined in main.py:1:1" in definition["result"]
        assert references["ok"] is True
        assert references["result_count"] == 1
        assert "ignored.py" not in references["result"]
        assert hover["ok"] is True
        assert "DemoType -> int" in hover["result"]

        toolkit.shutdown()


def test_code_toolkit_lsp_typescript_symbols_reuse_session_and_shutdown(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        script = _write_fake_lsp_server(root)
        toolkit = CoreToolkit(workspace_root=root)
        target = root / "main.ts"
        target.write_text("export class Demo {}\n", encoding="utf-8")
        (root / "util.ts").write_text("export function helper() {}\n", encoding="utf-8")

        monkeypatch.setattr(
            toolkit._lsp_runtime,
            "_server_spec_for_language",
            lambda language: LSPServerSpec(language=language, server_name="fake-lsp", command=[sys.executable, "-u", str(script)]),
        )

        first = toolkit.execute(
            "lsp",
            {
                "operation": "documentSymbol",
                "file_path": str(target),
            },
        )
        assert first["ok"] is True
        assert "Document symbols:" in first["result"]
        assert "Demo (Class)" in first["result"]

        session_before = next(iter(toolkit._lsp_runtime._sessions.values()))
        second = toolkit.execute(
            "lsp",
            {
                "operation": "workspaceSymbol",
                "file_path": str(target),
                "query": "Demo",
            },
        )
        session_after = next(iter(toolkit._lsp_runtime._sessions.values()))

        assert second["ok"] is True
        assert second["result_count"] == 2
        assert "main.ts" in second["result"]
        assert "util.ts" in second["result"]
        assert session_before is session_after

        process = session_before.process
        toolkit.shutdown()
        process.wait(timeout=2.0)
        assert process.poll() is not None


def test_code_toolkit_lsp_validates_paths_and_missing_servers(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        toolkit = CoreToolkit(workspace_root=root)
        target = root / "main.py"
        target.write_text("print('hi')\n", encoding="utf-8")
        unsupported = root / "main.rb"
        unsupported.write_text("puts 'hi'\n", encoding="utf-8")

        invalid_op = toolkit.execute("lsp", {"operation": "rename", "file_path": str(target)})
        relative_path = toolkit.execute("lsp", {"operation": "documentSymbol", "file_path": "main.py"})
        missing_position = toolkit.execute(
            "lsp",
            {"operation": "hover", "file_path": str(target)},
        )
        unsupported_language = toolkit.execute(
            "lsp",
            {"operation": "documentSymbol", "file_path": str(unsupported)},
        )

        monkeypatch.setattr(toolkit._lsp_runtime, "_server_spec_for_language", lambda language: None)
        missing_server = toolkit.execute(
            "lsp",
            {"operation": "documentSymbol", "file_path": str(target)},
        )

        assert "operation must be one of" in invalid_op["error"]
        assert "absolute path" in relative_path["error"]
        assert "line and character are required" in missing_position["error"]
        assert "unsupported language" in unsupported_language["error"]
        assert "no LSP server available" in missing_server["error"]


def test_code_toolkit_lsp_compacts_large_results():
    with tempfile.TemporaryDirectory() as tmp:
        toolkit = CoreToolkit(workspace_root=tmp)
        optimizer = toolkit.tools["lsp"].history_result_optimizer
        assert optimizer is not None

        payload = {
            "ok": True,
            "operation": "workspaceSymbol",
            "file_path": "/tmp/main.ts",
            "result": "x" * 5000,
            "result_count": 20,
            "file_count": 4,
            "language": "typescript",
            "server": "fake-lsp",
            "error": "",
        }
        compacted = optimizer(
            payload,
            type(
                "Ctx",
                (),
                {
                    "tool_name": "lsp",
                    "call_id": "call-1",
                    "kind": "result",
                    "provider": "openai",
                    "session_id": "session-1",
                    "latest_messages": [],
                    "max_chars": 200,
                    "preview_chars": 32,
                    "include_hash": True,
                },
            )(),
        )

        assert compacted["compacted"] is True
        assert compacted["digest"]

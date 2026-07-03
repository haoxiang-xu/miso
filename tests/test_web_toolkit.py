from __future__ import annotations

from unchain.tools import ToolExecutionContext
from unchain.toolkits import WebToolkit
from unchain.toolkits.builtin.core import web_fetch as web_fetch_module


def _web_fetch_success_payload(url: str, *, content_type: str = "text/plain") -> dict[str, object]:
    return {
        "ok": True,
        "url": url,
        "final_url": url,
        "host": "example.com",
        "status_code": 200,
        "content_type": content_type,
        "file_kind": "text",
        "result": "",
        "content_length": 0,
        "returned_chars": 0,
        "truncated": False,
        "next_offset": None,
        "cached": False,
        "redirect": None,
        "skipped": False,
        "error": "",
    }


def test_web_toolkit_fetches_raw_content_with_cache_and_pagination(monkeypatch, tmp_path):
    toolkit = WebToolkit(workspace_root=tmp_path)
    monkeypatch.setattr(web_fetch_module, "validate_public_url", lambda url: (url, None))
    calls = {"count": 0}

    def fake_request(url: str):
        calls["count"] += 1
        return (_web_fetch_success_payload(url), "alpha beta gamma delta")

    monkeypatch.setattr(toolkit._web_fetch_service, "_request", fake_request)

    first = toolkit.execute(
        "web_fetch",
        {"url": "https://example.com/docs", "mode": "raw", "offset": 6, "max_chars": 4},
    )
    second = toolkit.execute(
        "web_fetch",
        {"url": "https://example.com/docs", "mode": "raw", "offset": 0, "max_chars": 5},
    )

    assert first["ok"] is True
    assert first["result"] == "beta"
    assert first["truncated"] is True
    assert first["next_offset"] == 10
    assert second["result"] == "alpha"
    assert second["cached"] is True
    assert calls["count"] == 1


def test_web_toolkit_extract_uses_execution_context_runtime_config(monkeypatch, tmp_path):
    toolkit = WebToolkit(workspace_root=tmp_path)
    monkeypatch.setattr(web_fetch_module, "validate_public_url", lambda url: (url, None))
    monkeypatch.setattr(
        toolkit._web_fetch_service,
        "_request",
        lambda url: (_web_fetch_success_payload(url, content_type="text/html"), "React documentation body"),
    )
    seen: dict[str, object] = {}

    def fake_extract(*, url: str, content: str, prompt: str, extract_model_config: dict[str, object]) -> str:
        seen["url"] = url
        seen["content"] = content
        seen["prompt"] = prompt
        seen["config"] = dict(extract_model_config)
        return "summary output"

    monkeypatch.setattr("unchain.toolkits.builtin.web.web.run_extract_model", fake_extract)
    toolkit.push_execution_context(
        ToolExecutionContext(
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
        )
    )
    try:
        result = toolkit.web_fetch(
            url="https://example.com/react",
            mode="extract",
            prompt="Summarize the docs changes",
        )
    finally:
        toolkit.pop_execution_context()

    assert result["ok"] is True
    assert result["result"] == "summary output"
    assert result["returned_chars"] == len("summary output")
    assert seen == {
        "url": "https://example.com/react",
        "content": "React documentation body",
        "prompt": "Summarize the docs changes",
        "config": {"provider": "openai", "model": "gpt-5-mini", "payload": {"store": False}},
    }

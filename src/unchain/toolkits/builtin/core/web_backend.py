from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Any

from ....tools.models import ToolHistoryOptimizationContext
from .web_fetch import WebFetchService, run_extract_model


class CoreWebBackend:
    """Private backend for CoreToolkit web-fetch behavior."""

    def __init__(
        self,
        *,
        runtime_config_provider: Callable[[str], dict[str, Any]] | None = None,
        web_fetch_service: WebFetchService | None = None,
        extract_model_runner: Callable[..., str] = run_extract_model,
    ) -> None:
        self._runtime_config_provider = runtime_config_provider
        self._web_fetch_service = web_fetch_service or WebFetchService()
        self._extract_model_runner = extract_model_runner

    @property
    def web_fetch_service(self) -> WebFetchService:
        return self._web_fetch_service

    def fetch(
        self,
        url: str,
        mode: str = "raw",
        prompt: str | None = None,
        offset: int = 0,
        max_chars: int = 20000,
    ) -> dict[str, Any]:
        resolved_mode = str(mode or "raw").strip().lower()
        if resolved_mode not in {"raw", "extract"}:
            return {"ok": False, "url": url, "error": "mode must be one of: raw, extract"}

        page_result, page_content = self._web_fetch_service.fetch(url)
        result = dict(page_result)
        result["mode"] = resolved_mode
        if not result.get("ok"):
            return result
        if not isinstance(page_content, str):
            result["ok"] = False
            result["error"] = "web page content could not be processed"
            return result

        if resolved_mode == "extract":
            return self._extract(
                url=url,
                prompt=prompt,
                page_content=page_content,
                result=result,
            )
        return self._raw_page(
            page_content=page_content,
            result=result,
            offset=offset,
            max_chars=max_chars,
        )

    def _extract(
        self,
        *,
        url: str,
        prompt: str | None,
        page_content: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(prompt, str) or not prompt.strip():
            result["ok"] = False
            result["error"] = "prompt is required when mode=extract"
            return result
        tool_config = self._tool_runtime_config_for("web_fetch")
        extract_model = tool_config.get("extract_model")
        if not isinstance(extract_model, dict):
            result["ok"] = False
            result["error"] = (
                "web_fetch extract mode requires runtime config at "
                "tool_runtime_config['web_fetch']['extract_model']"
            )
            return result
        try:
            extract_output = self._extract_model_runner(
                url=str(result.get("final_url") or result.get("url") or url),
                content=page_content,
                prompt=prompt,
                extract_model_config=extract_model,
            )
        except Exception as exc:
            result["ok"] = False
            result["error"] = f"extract failed: {type(exc).__name__}: {exc}"
            return result
        result["result"] = extract_output
        result["returned_chars"] = len(extract_output)
        result["truncated"] = False
        result["next_offset"] = None
        return result

    def _raw_page(
        self,
        *,
        page_content: str,
        result: dict[str, Any],
        offset: int,
        max_chars: int,
    ) -> dict[str, Any]:
        offset_value = self._coerce_nonnegative_int(offset, 0)
        try:
            limit_value = max(1, min(50_000, int(max_chars)))
        except (TypeError, ValueError):
            limit_value = 20_000
        chunk = page_content[offset_value : offset_value + limit_value]
        next_offset = (
            offset_value + len(chunk)
            if offset_value + len(chunk) < len(page_content)
            else None
        )
        result["result"] = chunk
        result["returned_chars"] = len(chunk)
        result["truncated"] = next_offset is not None
        result["next_offset"] = next_offset
        return result

    def _tool_runtime_config_for(self, tool_name: str) -> dict[str, Any]:
        if self._runtime_config_provider is None:
            return {}
        config = self._runtime_config_provider(tool_name)
        return dict(config) if isinstance(config, dict) else {}

    def compact_args(self, payload: Any, context: ToolHistoryOptimizationContext) -> Any:
        if not isinstance(payload, dict):
            return payload
        prompt = payload.get("prompt")
        compacted = {
            "url": payload.get("url"),
            "mode": payload.get("mode", "raw"),
            "offset": payload.get("offset"),
            "max_chars": payload.get("max_chars"),
            "compacted": True,
        }
        if isinstance(prompt, str) and prompt:
            compacted["prompt"] = (
                self._preview_text(prompt, context.preview_chars)
                if len(prompt) > context.max_chars
                else prompt
            )
            if len(prompt) > context.max_chars and context.include_hash:
                compacted["prompt_digest"] = hashlib.sha1(
                    prompt.encode("utf-8", errors="replace")
                ).hexdigest()
        return compacted

    def compact_result(self, payload: Any, context: ToolHistoryOptimizationContext) -> Any:
        if not isinstance(payload, dict):
            return payload
        result_text = payload.get("result")
        if not isinstance(result_text, str):
            return payload
        if len(result_text) <= context.max_chars:
            return payload
        compacted = dict(payload)
        compacted["result"] = self._preview_text(result_text, context.preview_chars)
        compacted["compacted"] = True
        if context.include_hash:
            compacted["digest"] = hashlib.sha1(
                result_text.encode("utf-8", errors="replace")
            ).hexdigest()
        return compacted

    @staticmethod
    def _coerce_nonnegative_int(value: Any, default: int) -> int:
        try:
            coerced = int(value)
        except (TypeError, ValueError):
            return default
        return max(0, coerced)

    @staticmethod
    def _preview_text(text: str, chars: int = 160) -> str:
        if len(text) <= chars * 2:
            return text
        omitted = len(text) - chars * 2
        return f"{text[:chars]}\n... <omitted {omitted} chars> ...\n{text[-chars:]}"


__all__ = ["CoreWebBackend"]

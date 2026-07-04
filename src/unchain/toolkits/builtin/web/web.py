from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from ....tools.models import ToolHistoryOptimizationContext
from ...base import BuiltinToolkit
from ..core.web_fetch import WebFetchService, run_extract_model


class WebToolkit(BuiltinToolkit):
    """Focused public web access toolkit."""

    __unchain_public_builtin__ = False
    __unchain_legacy_compat__ = True

    def __init__(
        self,
        *,
        workspace_root: str | Path | None = None,
        workspace_roots: list[str | Path] | None = None,
    ) -> None:
        super().__init__(workspace_root=workspace_root, workspace_roots=workspace_roots)
        self._web_fetch_service = WebFetchService()
        self.register(
            self.web_fetch,
            name="web_fetch",
            description="Fetch a public web page over HTTP(S), return raw page content or run a runtime-configured extraction model.",
            requires_confirmation=True,
            history_arguments_optimizer=self._compact_web_fetch_args,
            history_result_optimizer=self._compact_web_fetch_result,
        )

    def web_fetch(
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
                extract_output = run_extract_model(
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

        offset_value = self._coerce_nonnegative_int(offset, 0)
        try:
            limit_value = max(1, min(50_000, int(max_chars)))
        except (TypeError, ValueError):
            limit_value = 20_000
        chunk = page_content[offset_value : offset_value + limit_value]
        next_offset = offset_value + len(chunk) if offset_value + len(chunk) < len(page_content) else None
        result["result"] = chunk
        result["returned_chars"] = len(chunk)
        result["truncated"] = next_offset is not None
        result["next_offset"] = next_offset
        return result

    def _tool_runtime_config_for(self, tool_name: str) -> dict[str, Any]:
        context = self.current_execution_context
        config = getattr(context, "tool_runtime_config", None)
        if not isinstance(config, dict):
            return {}
        tool_config = config.get(tool_name)
        return dict(tool_config) if isinstance(tool_config, dict) else {}

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
        return f"{text[:chars]}\n... <omitted {len(text) - chars * 2} chars> ...\n{text[-chars:]}"

    def _compact_web_fetch_args(self, payload: Any, context: ToolHistoryOptimizationContext) -> Any:
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
                compacted["prompt_digest"] = hashlib.sha1(prompt.encode("utf-8", errors="replace")).hexdigest()
        return compacted

    def _compact_web_fetch_result(self, payload: Any, context: ToolHistoryOptimizationContext) -> Any:
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
            compacted["digest"] = hashlib.sha1(result_text.encode("utf-8", errors="replace")).hexdigest()
        return compacted


__all__ = ["WebToolkit"]

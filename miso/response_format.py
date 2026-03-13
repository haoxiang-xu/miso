from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Callable

class response_format:
    """JSON-schema based response format as a concrete class."""

    def __init__(
        self,
        name: str,
        schema: dict[str, Any],
        required: list[str] | None = None,
        post_processor: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ):
        if not name:
            raise ValueError("response format name is required")
        if not isinstance(schema, dict):
            raise ValueError("response format schema must be a dict")

        self.name = name
        self.schema = deepcopy(schema)
        self.required = list(required or schema.get("required", []))
        self.post_processor = post_processor

    def to_openai(self) -> dict[str, Any]:
        return {
            "type": "json_schema",
            "json_schema": {
                "name": self.name,
                "schema": self.schema,
            },
        }

    def to_ollama(self) -> dict[str, Any]:
        # Ollama /api/chat accepts either "json" or a schema-like object in "format".
        return deepcopy(self.schema)

    def to_anthropic(self) -> str:
        """Return a system-prompt suffix that instructs Claude to output JSON."""
        schema_str = json.dumps(self.schema, indent=2, ensure_ascii=False)
        return (
            f"You MUST respond with valid JSON only, no other text.\n"
            f"The JSON must conform to this schema:\n{schema_str}"
        )

    def to_gemini(self) -> dict[str, Any]:
        """Return Gemini-compatible structured output config.

        Returns a dict with ``response_mime_type`` and ``response_schema``
        suitable for passing into Gemini's ``generation_config``.
        """
        return {
            "response_mime_type": "application/json",
            "response_schema": deepcopy(self.schema),
        }

    def parse(self, content: str | dict[str, Any]) -> dict[str, Any]:
        if isinstance(content, dict):
            parsed = deepcopy(content)
        else:
            raw = (content or "{}").strip()
            parsed = json.loads(raw)

        if not isinstance(parsed, dict):
            raise ValueError("response format parse result must be a JSON object")

        missing = [field for field in self.required if field not in parsed]
        if missing:
            raise ValueError(f"response format missing required fields: {missing}")

        if self.post_processor is not None:
            parsed = self.post_processor(parsed)
            if not isinstance(parsed, dict):
                raise ValueError("post_processor must return a dict")

        return parsed

__all__ = ["response_format"]

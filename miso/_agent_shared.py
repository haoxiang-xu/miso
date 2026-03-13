from __future__ import annotations

import json
import re
from typing import Any

_MENTION_PATTERN = re.compile(r"@([A-Za-z0-9_.-]+)")


def as_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = str(block.get("type", ""))
            if block_type in {"text", "input_text", "output_text"}:
                text = block.get("text", "")
                if text:
                    parts.append(text if isinstance(text, str) else str(text))
                continue
            parts.append(json.dumps(block, default=str, ensure_ascii=False))
        return "".join(parts)
    if isinstance(content, dict):
        return json.dumps(content, default=str, ensure_ascii=False)
    if content is None:
        return ""
    return str(content)


def normalize_mentions(raw_mentions: Any, *, content: str = "") -> list[str]:
    mentions: list[str] = []
    if isinstance(raw_mentions, list):
        for item in raw_mentions:
            if isinstance(item, str) and item.strip():
                mentions.append(item.strip())
    mentions.extend(match.group(1) for match in _MENTION_PATTERN.finditer(content))

    deduped: list[str] = []
    seen: set[str] = set()
    for mention in mentions:
        if mention in seen:
            continue
        seen.add(mention)
        deduped.append(mention)
    return deduped


__all__ = ["as_text", "normalize_mentions"]

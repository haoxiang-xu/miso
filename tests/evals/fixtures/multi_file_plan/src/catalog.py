from __future__ import annotations


def list_items(items: list[dict], owner: str) -> list[dict]:
    return [item for item in items if item["owner"] == owner]

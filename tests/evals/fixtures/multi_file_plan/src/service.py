from __future__ import annotations

from src.catalog import list_items


def list_catalog(store, owner: str) -> list[dict]:
    items = store.load_items()
    return list_items(items, owner)

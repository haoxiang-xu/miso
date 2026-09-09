from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json_sha256(value: Any) -> str:
    """Return deterministic SHA-256 over strict canonical JSON bytes."""
    try:
        content = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (RecursionError, TypeError, ValueError, UnicodeError) as exc:
        raise ValueError("provider usage must be strict canonical JSON") from exc
    return hashlib.sha256(content).hexdigest()

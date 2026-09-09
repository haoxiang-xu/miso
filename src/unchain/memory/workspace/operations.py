from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from enum import Enum
from typing import Any

from unchain.journal import ModelValidationError, OperationRef, ResourceRef
from unchain.journal.models import _required_text


def _canonical_value(value: Any, *, path: str) -> Any:
    if isinstance(value, ResourceRef):
        return value.to_dict()
    if isinstance(value, Enum):
        return value.value
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ModelValidationError(f"{path} contains a non-finite number")
        return value
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key in sorted(value):
            if not isinstance(key, str) or not key or "\x00" in key:
                raise ModelValidationError(f"{path} contains an invalid key")
            normalized[key] = _canonical_value(value[key], path=f"{path}.{key}")
        return normalized
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [
            _canonical_value(item, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    raise TypeError(f"{path} contains a non-canonical value")


def build_operation_ref(
    operation_id: object,
    *,
    domain: str,
    payload: Mapping[str, Any],
) -> OperationRef:
    """Bind an idempotency key to the exact normalized semantic mutation."""

    identifier = _required_text(operation_id, "operation_id", identifier=True)
    operation_domain = _required_text(domain, "domain", identifier=True)
    if not isinstance(payload, Mapping):
        raise TypeError("payload must be an object")
    canonical = _canonical_value(
        {"domain": operation_domain, "payload": payload},
        path="operation",
    )
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return OperationRef(identifier, hashlib.sha256(encoded).hexdigest())


__all__ = ["build_operation_ref"]

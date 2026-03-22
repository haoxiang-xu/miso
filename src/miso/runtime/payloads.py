from __future__ import annotations

import copy
import json
from importlib.resources import files
from typing import Any

DEFAULT_PAYLOADS_RESOURCE = "model_default_payloads.json"
MODEL_CAPABILITIES_RESOURCE = "model_capabilities.json"
_RESOURCE_PACKAGE = "miso.runtime.resources"


def load_runtime_json_resource(name: str) -> dict[str, dict[str, Any]]:
    try:
        raw = json.loads(files(_RESOURCE_PACKAGE).joinpath(name).read_text(encoding="utf-8"))
    except Exception:
        return {}

    if not isinstance(raw, dict):
        return {}

    parsed: dict[str, dict[str, Any]] = {}
    for key, value in raw.items():
        if isinstance(key, str) and isinstance(value, dict):
            parsed[key] = copy.deepcopy(value)
    return parsed


def load_default_payloads() -> dict[str, dict[str, Any]]:
    return load_runtime_json_resource(DEFAULT_PAYLOADS_RESOURCE)


def load_model_capabilities() -> dict[str, dict[str, Any]]:
    return load_runtime_json_resource(MODEL_CAPABILITIES_RESOURCE)


__all__ = [
    "DEFAULT_PAYLOADS_RESOURCE",
    "MODEL_CAPABILITIES_RESOURCE",
    "load_default_payloads",
    "load_model_capabilities",
    "load_runtime_json_resource",
]

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any


class ProviderReplayFrameError(ValueError):
    code = "provider_replay_frame_error"


def strict_json_copy(value: Any) -> Any:
    try:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ProviderReplayFrameError(
            f"provider replay data must be strict JSON: {exc}"
        ) from exc
    return json.loads(serialized)


def stable_json_digest(value: Any) -> str:
    copied = strict_json_copy(value)
    serialized = json.dumps(
        copied,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def tool_schema_digest(toolkit: Any, provider: str) -> str:
    to_provider_json = getattr(toolkit, "to_provider_json", None)
    tools = to_provider_json(provider) if callable(to_provider_json) else []
    return stable_json_digest(tools or [])


def tool_schema_manifest(toolkit: Any, provider: str) -> dict[str, str]:
    to_provider_json = getattr(toolkit, "to_provider_json", None)
    tools = to_provider_json(provider) if callable(to_provider_json) else []
    manifest: dict[str, str] = {}
    for schema in tools or []:
        if not isinstance(schema, dict):
            raise ProviderReplayFrameError(
                "provider tool schema entries must be dictionaries"
            )
        function = schema.get("function")
        name = (
            function.get("name")
            if isinstance(function, dict)
            else schema.get("name")
        )
        if (
            not name
            and str(provider or "").strip().lower() == "openai"
            and schema.get("type") == "computer"
        ):
            # OpenAI's built-in Computer schema is intentionally nameless on
            # the wire (`{"type":"computer"}`). Give it a stable internal
            # manifest key without weakening validation for any other schema.
            name = "computer"
        normalized_name = str(name or "").strip()
        if not normalized_name or normalized_name in manifest:
            raise ProviderReplayFrameError(
                "provider tool schemas require unique non-empty names"
            )
        manifest[normalized_name] = stable_json_digest(schema)
    return manifest


def ensure_replay_tool_schema_compatible(
    frame: dict[str, Any],
    *,
    toolkit: Any,
    provider: str,
) -> None:
    expected_manifest = frame.get("tool_schema_manifest")
    if isinstance(expected_manifest, dict):
        actual_manifest = tool_schema_manifest(toolkit, provider)
        incompatible = [
            name
            for name, digest in expected_manifest.items()
            if name in actual_manifest and actual_manifest.get(name) != digest
        ]
        if incompatible:
            raise ProviderReplayFrameError(
                "provider replay tool schema does not match previously captured tools"
            )
        return
    expected = frame.get("tool_schema_digest")
    if not isinstance(expected, str) or not expected:
        return
    actual = tool_schema_digest(toolkit, provider)
    if actual != expected:
        raise ProviderReplayFrameError(
            "provider replay tool schema does not match the current toolkit"
        )


def redact_provider_replay_secrets(value: Any) -> Any:
    if isinstance(value, list):
        return [redact_provider_replay_secrets(item) for item in value]
    if not isinstance(value, dict):
        return copy.deepcopy(value)
    output: dict[str, Any] = {}
    block_type = value.get("type")
    for key, item in value.items():
        secret = (
            key in {"encrypted_content", "signature", "thinking"}
            or (block_type == "redacted_thinking" and key == "data")
        )
        if secret and isinstance(item, str):
            digest = hashlib.sha256(item.encode("utf-8")).hexdigest()[:16]
            output[key] = f"[redacted {key} chars={len(item)} sha256={digest}]"
        else:
            output[key] = redact_provider_replay_secrets(item)
    return output


def validate_provider_replay_frame(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ProviderReplayFrameError("provider replay frame must be a dict")
    frame = strict_json_copy(raw)
    if not isinstance(frame.get("format"), str) or not frame.get("format"):
        raise ProviderReplayFrameError("provider replay frame requires format")
    if not isinstance(frame.get("complete"), bool):
        raise ProviderReplayFrameError("provider replay frame.complete must be a bool")
    if not isinstance(frame.get("items"), list):
        raise ProviderReplayFrameError("provider replay frame.items must be a list")
    if "tool_schema_digest" in frame and (
        not isinstance(frame.get("tool_schema_digest"), str)
        or not frame.get("tool_schema_digest")
    ):
        raise ProviderReplayFrameError(
            "provider replay frame.tool_schema_digest must be a non-empty string"
        )
    if "tool_schema_manifest" in frame:
        manifest = frame.get("tool_schema_manifest")
        if not isinstance(manifest, dict) or any(
            not isinstance(name, str)
            or not name
            or not isinstance(digest, str)
            or not digest
            for name, digest in manifest.items()
        ):
            raise ProviderReplayFrameError(
                "provider replay frame.tool_schema_manifest must map tool names to digests"
            )
    return frame


def current_provider_replay_frame(state: Any) -> dict[str, Any] | None:
    component_state = getattr(state, "component_state", None)
    bucket = (
        component_state.get("provider_replay")
        if isinstance(component_state, dict)
        else None
    )
    raw_frame = bucket.get("frame") if isinstance(bucket, dict) else None
    if not isinstance(raw_frame, dict):
        return None
    return validate_provider_replay_frame(raw_frame)


def merge_model_turn_replay_frame(
    state: Any,
    incoming: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if incoming is None:
        return current_provider_replay_frame(state)
    if not isinstance(incoming, dict):
        raise ProviderReplayFrameError("model turn provider_replay_frame must be a dict")

    mode = str(incoming.get("mode") or "replace")
    if mode == "replace":
        clean = {
            key: copy.deepcopy(value)
            for key, value in incoming.items()
            if key not in {"mode", "response_items"}
        }
        existing = current_provider_replay_frame(state)
        if (
            isinstance(existing, dict)
            and existing.get("format") == clean.get("format")
        ):
            existing_manifest = existing.get("tool_schema_manifest")
            incoming_manifest = clean.get("tool_schema_manifest")
            if isinstance(existing_manifest, dict):
                if isinstance(incoming_manifest, dict):
                    incompatible = [
                        name
                        for name, digest in existing_manifest.items()
                        if name in incoming_manifest
                        and incoming_manifest.get(name) != digest
                    ]
                    if incompatible:
                        raise ProviderReplayFrameError(
                            "provider replay tool schema changed while replacing context"
                        )
                clean["tool_schema_manifest"] = {
                    **copy.deepcopy(existing_manifest),
                    **copy.deepcopy(
                        incoming_manifest
                        if isinstance(incoming_manifest, dict)
                        else {}
                    ),
                }
        return validate_provider_replay_frame(clean)

    if mode != "append_response":
        raise ProviderReplayFrameError(f"unsupported provider replay mode: {mode!r}")
    response_items = strict_json_copy(incoming.get("response_items"))
    if not isinstance(response_items, list):
        raise ProviderReplayFrameError(
            "append_response provider replay frame requires response_items"
        )
    incoming_items = strict_json_copy(incoming.get("items"))
    if not isinstance(incoming_items, list):
        raise ProviderReplayFrameError(
            "append_response provider replay frame requires items"
        )
    if response_items:
        if (
            len(incoming_items) < len(response_items)
            or incoming_items[-len(response_items) :] != response_items
        ):
            raise ProviderReplayFrameError(
                "append_response provider replay items must end with response_items"
            )
        request_items = incoming_items[: -len(response_items)]
    else:
        request_items = incoming_items
    existing = current_provider_replay_frame(state)
    incoming_format = str(incoming.get("format") or "")
    if (
        isinstance(existing, dict)
        and existing.get("format") == incoming_format
    ):
        existing_schema_digest = existing.get("tool_schema_digest")
        incoming_schema_digest = incoming.get("tool_schema_digest")
        existing_manifest = existing.get("tool_schema_manifest")
        incoming_manifest = incoming.get("tool_schema_manifest")
        merged_manifest: dict[str, str] | None = None
        if isinstance(existing_manifest, dict) and isinstance(incoming_manifest, dict):
            incompatible = [
                name
                for name, digest in existing_manifest.items()
                if name in incoming_manifest and incoming_manifest.get(name) != digest
            ]
            if incompatible:
                raise ProviderReplayFrameError(
                    "provider replay tool schema changed during remote continuation"
                )
            merged_manifest = {
                **copy.deepcopy(existing_manifest),
                **copy.deepcopy(incoming_manifest),
            }
        elif (
            isinstance(existing_schema_digest, str)
            and isinstance(incoming_schema_digest, str)
            and existing_schema_digest != incoming_schema_digest
        ):
            raise ProviderReplayFrameError(
                "provider replay tool schema changed during remote continuation"
            )
        merged = copy.deepcopy(existing)
        if request_items and (
            len(merged["items"]) < len(request_items)
            or merged["items"][-len(request_items) :] != request_items
        ):
            merged["items"].extend(request_items)
        merged["items"].extend(response_items)
        merged["source"] = str(incoming.get("source") or merged.get("source") or "")
        if isinstance(merged_manifest, dict):
            merged["tool_schema_manifest"] = merged_manifest
        elif isinstance(incoming_manifest, dict):
            merged["tool_schema_manifest"] = copy.deepcopy(incoming_manifest)
        if isinstance(incoming_schema_digest, str):
            merged["tool_schema_digest"] = incoming_schema_digest
        return validate_provider_replay_frame(merged)

    fallback_items = strict_json_copy(incoming.get("items") or response_items)
    fallback_frame = {
        "format": incoming_format,
        "complete": False,
        "items": fallback_items,
        "source": str(incoming.get("source") or "provider_response_fragment"),
        "incomplete_reason": (
            "provider response used remote continuation without a complete local replay prefix"
        ),
    }
    for schema_key in ("tool_schema_digest", "tool_schema_manifest"):
        if schema_key in incoming:
            fallback_frame[schema_key] = copy.deepcopy(incoming[schema_key])
    return validate_provider_replay_frame(fallback_frame)


def set_provider_replay_frame(state: Any, frame: dict[str, Any]) -> None:
    validated = validate_provider_replay_frame(frame)
    bucket = state.component_bucket("provider_replay")
    bucket["frame"] = validated


def append_provider_replay_items(state: Any, items: list[dict[str, Any]]) -> None:
    frame = current_provider_replay_frame(state)
    if frame is None:
        return
    copied_items = strict_json_copy(items)
    if not isinstance(copied_items, list):
        raise ProviderReplayFrameError("provider replay append items must be a list")
    frame["items"].extend(copied_items)
    set_provider_replay_frame(state, frame)


__all__ = [
    "ProviderReplayFrameError",
    "append_provider_replay_items",
    "current_provider_replay_frame",
    "ensure_replay_tool_schema_compatible",
    "merge_model_turn_replay_frame",
    "redact_provider_replay_secrets",
    "set_provider_replay_frame",
    "strict_json_copy",
    "stable_json_digest",
    "tool_schema_digest",
    "tool_schema_manifest",
    "validate_provider_replay_frame",
]

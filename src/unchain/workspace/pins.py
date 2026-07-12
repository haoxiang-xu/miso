from __future__ import annotations

import copy
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..memory.revision import (
    SessionRevisionConflictError,
    load_session_snapshot,
    save_session_snapshot,
)


MAX_FULL_FILE_PIN_CHARS = 120_000
MAX_SESSION_PIN_COUNT = 64
_SESSION_STATE_KEY = "workspace_pins"


@dataclass(frozen=True)
class WorkspacePinExecutionContext:
    session_id: str
    session_store: Any


def load_workspace_pins(session_store: Any, session_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    state = _load_session_state(session_store, session_id)
    raw_pins = state.get(_SESSION_STATE_KEY, [])
    pins = copy.deepcopy(raw_pins) if isinstance(raw_pins, list) else []
    normalized_pins = [pin for pin in pins if isinstance(pin, dict)]
    return state, normalized_pins


def save_workspace_pins(
    session_store: Any,
    session_id: str,
    state: dict[str, Any],
    pins: list[dict[str, Any]],
) -> None:
    if session_store is None or not session_id:
        return
    normalized_pins = copy.deepcopy([pin for pin in pins if isinstance(pin, dict)])
    for attempt in range(3):
        snapshot = load_session_snapshot(session_store, session_id)
        next_state = copy.deepcopy(snapshot.state)
        next_state[_SESSION_STATE_KEY] = normalized_pins
        try:
            save_session_snapshot(
                session_store,
                session_id,
                next_state,
                expected_revision=snapshot.revision,
            )
            return
        except SessionRevisionConflictError:
            if attempt == 2:
                raise


def build_pin_record(
    *,
    path: str | Path,
    lines: list[str],
    start: int | None = None,
    end: int | None = None,
    reason: str | None = None,
    start_with: str | None = None,
    end_with: str | None = None,
) -> dict[str, Any]:
    target = Path(path).resolve()
    total_lines = len(lines)
    start_line, end_line = _resolve_line_range(
        lines,
        start=start,
        end=end,
        start_with=start_with,
        end_with=end_with,
    )
    selected = lines[start_line - 1 : end_line] if total_lines else []
    content = "".join(selected)
    content_sha1 = hashlib.sha1(content.encode("utf-8", errors="replace")).hexdigest()
    pin_id = _build_pin_id(target, start_line, end_line, content_sha1)
    return {
        "pin_id": pin_id,
        "path": str(target),
        "start_line": start_line,
        "end_line": end_line,
        "total_lines": total_lines,
        "reason": str(reason or ""),
        "content": content,
        "content_sha1": content_sha1,
    }


def find_duplicate_pin(
    pins: list[dict[str, Any]],
    candidate: dict[str, Any],
) -> dict[str, Any] | None:
    candidate_key = _pin_identity(candidate)
    for pin in pins:
        if _pin_identity(pin) == candidate_key:
            return pin
    return None


def remove_pins(
    pins: list[dict[str, Any]],
    *,
    pin_id: str | None = None,
    path: str | None = None,
    start: int | None = None,
    end: int | None = None,
    remove_all: bool = False,
) -> tuple[list[dict[str, Any]], list[str]]:
    if remove_all:
        return [], [str(pin.get("pin_id", "")) for pin in pins if isinstance(pin, dict)]

    requested_pin_id = str(pin_id or "").strip()
    requested_path = str(Path(path).resolve()) if path else ""
    requested_start = _coerce_optional_int(start)
    requested_end = _coerce_optional_int(end)

    remaining: list[dict[str, Any]] = []
    removed: list[str] = []
    for pin in pins:
        if _should_remove_pin(
            pin,
            pin_id=requested_pin_id,
            path=requested_path,
            start=requested_start,
            end=requested_end,
        ):
            removed.append(str(pin.get("pin_id", "")))
        else:
            remaining.append(pin)
    return remaining, removed


def _load_session_state(session_store: Any, session_id: str) -> dict[str, Any]:
    if session_store is None or not session_id:
        return {}
    return copy.deepcopy(load_session_snapshot(session_store, session_id).state)


def _save_session_state(session_store: Any, session_id: str, state: dict[str, Any]) -> None:
    if session_store is None or not session_id:
        return
    snapshot = load_session_snapshot(session_store, session_id)
    save_session_snapshot(
        session_store,
        session_id,
        copy.deepcopy(state),
        expected_revision=snapshot.revision,
    )


def _resolve_line_range(
    lines: list[str],
    *,
    start: int | None,
    end: int | None,
    start_with: str | None,
    end_with: str | None,
) -> tuple[int, int]:
    total_lines = len(lines)
    if total_lines == 0:
        return 1, 1

    start_line = max(1, min(total_lines, _coerce_optional_int(start) or 1))
    end_line = max(start_line, min(total_lines, _coerce_optional_int(end) or total_lines))

    if isinstance(start_with, str) and start_with:
        found_start = _find_line_containing(lines, start_with, lower_bound=1)
        if found_start is not None:
            start_line = found_start
            end_line = max(end_line, start_line)

    if isinstance(end_with, str) and end_with:
        found_end = _find_line_containing(lines, end_with, lower_bound=start_line)
        if found_end is not None:
            end_line = max(start_line, found_end)

    return start_line, end_line


def _find_line_containing(lines: list[str], needle: str, *, lower_bound: int) -> int | None:
    for index, line in enumerate(lines, start=1):
        if index < lower_bound:
            continue
        if needle in line:
            return index
    return None


def _build_pin_id(path: Path, start_line: int, end_line: int, content_sha1: str) -> str:
    raw = f"{path}:{start_line}:{end_line}:{content_sha1}"
    digest = hashlib.sha1(raw.encode("utf-8", errors="replace")).hexdigest()[:16]
    return f"pin_{digest}"


def _pin_identity(pin: dict[str, Any]) -> tuple[str, int, int, str]:
    return (
        str(pin.get("path", "")),
        int(pin.get("start_line", 0) or 0),
        int(pin.get("end_line", 0) or 0),
        str(pin.get("content_sha1", "")),
    )


def _should_remove_pin(
    pin: dict[str, Any],
    *,
    pin_id: str,
    path: str,
    start: int | None,
    end: int | None,
) -> bool:
    if pin_id and str(pin.get("pin_id", "")) == pin_id:
        return True
    if path and str(pin.get("path", "")) != path:
        return False
    if path:
        if start is not None and int(pin.get("start_line", 0) or 0) != start:
            return False
        if end is not None and int(pin.get("end_line", 0) or 0) != end:
            return False
        return True
    return False


def _coerce_optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "MAX_FULL_FILE_PIN_CHARS",
    "MAX_SESSION_PIN_COUNT",
    "WorkspacePinExecutionContext",
    "build_pin_record",
    "find_duplicate_pin",
    "load_workspace_pins",
    "remove_pins",
    "save_workspace_pins",
]

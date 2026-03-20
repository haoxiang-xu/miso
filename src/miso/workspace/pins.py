from __future__ import annotations

import hashlib
import re
import uuid
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..memory import SessionStore


MAX_SESSION_PIN_COUNT = 8
MAX_FULL_FILE_PIN_CHARS = 12_000
MAX_PINNED_INJECTION_CHARS = 16_000
NEARBY_PIN_SEARCH_WINDOW = 40
ANCHOR_MATCH_WINDOW = 3
MAX_ANCHOR_CANDIDATES = 64
MAX_ANCHOR_LENGTH = 240

_WHITESPACE_RE = re.compile(r"\s+")
_PY_DECLARATION_RE = re.compile(r"^\s*(async\s+def|def|class)\s+([A-Za-z_]\w*)\b")
_JS_DECLARATION_RE = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:function|class)\s+([A-Za-z_$][\w$]*)\b"
)


@dataclass(frozen=True)
class WorkspacePinExecutionContext:
    session_id: str
    session_store: SessionStore


def load_workspace_pins(store: SessionStore, session_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    state = store.load(session_id) if session_id else {}
    pins = _normalize_pins(state.get("workspace_pins"))
    return state, pins


def save_workspace_pins(
    store: SessionStore,
    session_id: str,
    state: dict[str, Any],
    pins: list[dict[str, Any]],
) -> None:
    if pins:
        state["workspace_pins"] = pins
    else:
        state.pop("workspace_pins", None)
    store.save(session_id, state)


def build_pin_record(
    *,
    path: Path,
    lines: list[str],
    start: int | None,
    end: int | None,
    start_with: str | None = None,
    end_with: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    total_lines = len(lines)
    if start is None or end is None:
        content = "".join(lines)
        declaration = _extract_declaration_metadata(lines)
        return {
            "pin_id": f"pin_{uuid.uuid4().hex[:10]}",
            "path": str(path),
            "mode": "file",
            "start": None,
            "end": None,
            "line_count": total_lines,
            "start_with": _clean_optional_text(start_with) or _auto_anchor(lines, from_end=False),
            "end_with": _clean_optional_text(end_with) or _auto_anchor(lines, from_end=True),
            "start_anchor_offset": 0,
            "end_anchor_trailing_lines": 0,
            "content_fingerprint": _hash_normalized_text(content),
            "reason": _clean_optional_text(reason),
            "last_resolved": {
                "start": 1 if total_lines > 0 else 0,
                "end": total_lines,
            },
            "last_resolution_status": "resolved",
            "last_resolution_error": None,
            **declaration,
        }

    selection = lines[start - 1 : end]
    start_anchor, start_anchor_offset = _resolve_anchor(selection, explicit=start_with, from_end=False)
    end_anchor, end_anchor_trailing_lines = _resolve_anchor(selection, explicit=end_with, from_end=True)
    declaration = _extract_declaration_metadata(selection)

    return {
        "pin_id": f"pin_{uuid.uuid4().hex[:10]}",
        "path": str(path),
        "mode": "lines",
        "start": start,
        "end": end,
        "line_count": len(selection),
        "start_with": start_anchor,
        "end_with": end_anchor,
        "start_anchor_offset": start_anchor_offset,
        "end_anchor_trailing_lines": end_anchor_trailing_lines,
        "content_fingerprint": _hash_normalized_text("".join(selection)),
        "reason": _clean_optional_text(reason),
        "last_resolved": {"start": start, "end": end},
        "last_resolution_status": "resolved",
        "last_resolution_error": None,
        **declaration,
    }


def find_duplicate_pin(
    pins: list[dict[str, Any]],
    candidate: dict[str, Any],
) -> dict[str, Any] | None:
    comparable_fields = (
        "path",
        "mode",
        "start",
        "end",
        "start_with",
        "end_with",
    )
    for pin in pins:
        if all(pin.get(field) == candidate.get(field) for field in comparable_fields):
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
    removed_ids: list[str] = []
    remaining: list[dict[str, Any]] = []

    normalized_path = str(Path(path).resolve()) if path else None

    for pin in pins:
        should_remove = False
        if pin_id:
            should_remove = pin.get("pin_id") == pin_id
        elif normalized_path is not None and start is not None and end is not None:
            should_remove = (
                pin.get("path") == normalized_path
                and pin.get("start") == start
                and pin.get("end") == end
            )
        elif remove_all:
            should_remove = True

        if should_remove:
            removed_ids.append(str(pin.get("pin_id", "")))
        else:
            remaining.append(pin)

    return remaining, [pin_id for pin_id in removed_ids if pin_id]


def build_pinned_prompt_messages(
    *,
    store: SessionStore,
    session_id: str,
    max_total_chars: int = MAX_PINNED_INJECTION_CHARS,
) -> list[dict[str, str]]:
    state, pins = load_workspace_pins(store, session_id)
    if not pins:
        return []

    updated = False
    path_counts = Counter(str(pin.get("path", "")) for pin in pins if pin.get("path"))
    remaining_chars = max(0, int(max_total_chars))
    summary_lines: list[str] = []
    content_sections: list[str] = []

    for pin in pins:
        status, resolution = resolve_pin(pin)

        if status == "resolved":
            pin["last_resolved"] = {
                "start": resolution.get("start", 0),
                "end": resolution.get("end", 0),
            }
            pin["last_resolution_status"] = "resolved"
            pin["last_resolution_error"] = None
            updated = True
        else:
            pin["last_resolution_status"] = "unresolved"
            pin["last_resolution_error"] = resolution.get("reason")
            updated = True

        content = resolution.get("content", "")
        if status == "resolved" and isinstance(content, str):
            content_len = len(content)
            if content_len <= remaining_chars:
                remaining_chars -= content_len
                content_sections.append(_format_content_section(pin, resolution))
                summary_lines.append(_format_summary_line(pin, "resolved", resolution, path_counts))
            else:
                summary_lines.append(
                    _format_summary_line(
                        pin,
                        "skipped_due_to_budget",
                        {
                            **resolution,
                            "content_chars": content_len,
                            "remaining_chars": remaining_chars,
                        },
                        path_counts,
                    )
                )
        else:
            summary_lines.append(_format_summary_line(pin, "unresolved", resolution, path_counts))

    if updated:
        save_workspace_pins(store, session_id, state, pins)

    summary_message = {
        "role": "system",
        "content": "[PINNED SUMMARY]\n" + "\n".join(summary_lines),
    }
    content_body = "\n\n".join(content_sections) if content_sections else "No live pinned content injected for this request."
    content_message = {
        "role": "system",
        "content": "[PINNED CONTENT]\n" + content_body,
    }
    return [summary_message, content_message]


def resolve_pin(pin: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    path_value = pin.get("path")
    if not isinstance(path_value, str) or not path_value.strip():
        return "unresolved", {"reason": "path_missing"}

    path = Path(path_value)
    if not path.exists():
        return "unresolved", {"reason": "path_missing"}
    if not path.is_file():
        return "unresolved", {"reason": "not_a_file"}

    content = path.read_text(encoding="utf-8", errors="replace")
    lines = content.splitlines(keepends=True)
    total_lines = len(lines)

    if pin.get("mode") == "file":
        return "resolved", {
            "path": str(path),
            "start": 1 if total_lines > 0 else 0,
            "end": total_lines,
            "content": content,
        }

    original_start = _coerce_positive_int(pin.get("start"))
    original_end = _coerce_positive_int(pin.get("end"))
    line_count = max(1, _coerce_positive_int(pin.get("line_count")) or 1)
    start_with = _clean_optional_text(pin.get("start_with"))
    end_with = _clean_optional_text(pin.get("end_with"))
    start_anchor_offset = max(0, int(pin.get("start_anchor_offset", 0) or 0))
    end_anchor_trailing_lines = max(0, int(pin.get("end_anchor_trailing_lines", 0) or 0))
    content_fingerprint = _clean_optional_text(pin.get("content_fingerprint"))

    if original_start is None or original_end is None:
        return "unresolved", {"reason": "range_missing"}

    same_range_candidate = (original_start, original_end)
    if _candidate_matches(
        lines=lines,
        start=same_range_candidate[0],
        end=same_range_candidate[1],
        start_with=start_with,
        end_with=end_with,
        start_anchor_offset=start_anchor_offset,
        end_anchor_trailing_lines=end_anchor_trailing_lines,
        content_fingerprint=content_fingerprint,
        require_fingerprint=not (start_with or end_with),
    ):
        return _resolved_candidate(path, lines, *same_range_candidate)

    nearby_candidates = _generate_anchor_candidates(
        lines=lines,
        search_start=max(1, original_start - NEARBY_PIN_SEARCH_WINDOW),
        search_end=min(total_lines, original_end + NEARBY_PIN_SEARCH_WINDOW),
        line_count=line_count,
        start_with=start_with,
        end_with=end_with,
        start_anchor_offset=start_anchor_offset,
        end_anchor_trailing_lines=end_anchor_trailing_lines,
    )
    nearby_match = _select_best_candidate(
        candidates=nearby_candidates,
        lines=lines,
        original_start=original_start,
        original_end=original_end,
        start_with=start_with,
        end_with=end_with,
        start_anchor_offset=start_anchor_offset,
        end_anchor_trailing_lines=end_anchor_trailing_lines,
        content_fingerprint=content_fingerprint,
    )
    if nearby_match is not None:
        return _resolved_candidate(path, lines, *nearby_match)

    global_anchor_candidates = _generate_anchor_candidates(
        lines=lines,
        search_start=1,
        search_end=total_lines,
        line_count=line_count,
        start_with=start_with,
        end_with=end_with,
        start_anchor_offset=start_anchor_offset,
        end_anchor_trailing_lines=end_anchor_trailing_lines,
    )
    global_anchor_match = _select_best_candidate(
        candidates=global_anchor_candidates,
        lines=lines,
        original_start=original_start,
        original_end=original_end,
        start_with=start_with,
        end_with=end_with,
        start_anchor_offset=start_anchor_offset,
        end_anchor_trailing_lines=end_anchor_trailing_lines,
        content_fingerprint=content_fingerprint,
    )
    if global_anchor_match is not None:
        return _resolved_candidate(path, lines, *global_anchor_match)

    fingerprint_match = _find_fingerprint_match(
        lines=lines,
        line_count=line_count,
        content_fingerprint=content_fingerprint,
        original_start=original_start,
        original_end=original_end,
    )
    if fingerprint_match is not None:
        return _resolved_candidate(path, lines, *fingerprint_match)

    declaration_match = _find_declaration_match(
        lines=lines,
        pin=pin,
        original_start=original_start,
        original_end=original_end,
        line_count=line_count,
        end_with=end_with,
        end_anchor_trailing_lines=end_anchor_trailing_lines,
    )
    if declaration_match is not None:
        return _resolved_candidate(path, lines, *declaration_match)

    return "unresolved", {
        "reason": "anchor_or_fingerprint_not_found",
        "path": str(path),
        "last_known": pin.get("last_resolved"),
    }


def _normalize_pins(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        pin_id = item.get("pin_id")
        path = item.get("path")
        mode = item.get("mode")
        if not isinstance(pin_id, str) or not pin_id:
            continue
        if not isinstance(path, str) or not path:
            continue
        if mode not in {"file", "lines"}:
            continue
        normalized.append(dict(item))
    return normalized


def _clean_optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _normalize_anchor_text(value: str) -> str:
    return _WHITESPACE_RE.sub(" ", value.strip())


def _normalize_content_text(value: str) -> str:
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    normalized_lines = [line.rstrip() for line in text.split("\n")]
    while normalized_lines and not normalized_lines[0].strip():
        normalized_lines.pop(0)
    while normalized_lines and not normalized_lines[-1].strip():
        normalized_lines.pop()
    return "\n".join(normalized_lines)


def _hash_normalized_text(value: str) -> str:
    return hashlib.sha256(_normalize_content_text(value).encode("utf-8")).hexdigest()


def _line_matches_anchor(line: str, anchor: str | None) -> bool:
    if not anchor:
        return False
    normalized_anchor = _normalize_anchor_text(anchor)
    normalized_line = _normalize_anchor_text(line)
    return bool(normalized_anchor and normalized_anchor in normalized_line)


def _resolve_anchor(
    selection: list[str],
    *,
    explicit: str | None,
    from_end: bool,
) -> tuple[str | None, int]:
    explicit_clean = _clean_optional_text(explicit)
    if explicit_clean:
        if from_end:
            for index in range(len(selection) - 1, -1, -1):
                if _line_matches_anchor(selection[index], explicit_clean):
                    return explicit_clean[:MAX_ANCHOR_LENGTH], len(selection) - 1 - index
            return explicit_clean[:MAX_ANCHOR_LENGTH], 0
        for index, line in enumerate(selection):
            if _line_matches_anchor(line, explicit_clean):
                return explicit_clean[:MAX_ANCHOR_LENGTH], index
        return explicit_clean[:MAX_ANCHOR_LENGTH], 0

    auto_anchor = _auto_anchor(selection, from_end=from_end)
    if auto_anchor is None:
        return None, 0

    if from_end:
        for index in range(len(selection) - 1, -1, -1):
            if _line_matches_anchor(selection[index], auto_anchor):
                return auto_anchor, len(selection) - 1 - index
        return auto_anchor, 0

    for index, line in enumerate(selection):
        if _line_matches_anchor(line, auto_anchor):
            return auto_anchor, index
    return auto_anchor, 0


def _auto_anchor(lines: list[str], *, from_end: bool) -> str | None:
    indices = range(len(lines) - 1, -1, -1) if from_end else range(len(lines))
    for index in indices:
        stripped = lines[index].strip()
        if stripped:
            return stripped[:MAX_ANCHOR_LENGTH]
    return None


def _extract_declaration_metadata(lines: list[str]) -> dict[str, Any]:
    for index, line in enumerate(lines[:12]):
        stripped = line.strip()
        if not stripped or stripped.startswith("@"):
            continue
        py_match = _PY_DECLARATION_RE.match(line)
        if py_match:
            return {
                "declaration_kind": "python",
                "declaration_name": py_match.group(2),
                "declaration_offset": index,
            }
        js_match = _JS_DECLARATION_RE.match(line)
        if js_match:
            return {
                "declaration_kind": "javascript",
                "declaration_name": js_match.group(1),
                "declaration_offset": index,
            }
    return {
        "declaration_kind": None,
        "declaration_name": None,
        "declaration_offset": None,
    }


def _coerce_positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    return None


def _candidate_matches(
    *,
    lines: list[str],
    start: int,
    end: int,
    start_with: str | None,
    end_with: str | None,
    start_anchor_offset: int,
    end_anchor_trailing_lines: int,
    content_fingerprint: str | None,
    require_fingerprint: bool = False,
) -> bool:
    if start < 1 or end < start or end > len(lines):
        return False

    selection = lines[start - 1 : end]
    if start_with:
        start_window = selection[: max(ANCHOR_MATCH_WINDOW, start_anchor_offset + ANCHOR_MATCH_WINDOW)]
        if not any(_line_matches_anchor(line, start_with) for line in start_window):
            return False

    if end_with:
        end_window_size = max(ANCHOR_MATCH_WINDOW, end_anchor_trailing_lines + ANCHOR_MATCH_WINDOW)
        end_window = selection[-end_window_size:]
        if not any(_line_matches_anchor(line, end_with) for line in end_window):
            return False

    if content_fingerprint:
        fingerprint_matches = _hash_normalized_text("".join(selection)) == content_fingerprint
        if require_fingerprint:
            return fingerprint_matches
        if fingerprint_matches:
            return True

    return bool(start_with or end_with)


def _generate_anchor_candidates(
    *,
    lines: list[str],
    search_start: int,
    search_end: int,
    line_count: int,
    start_with: str | None,
    end_with: str | None,
    start_anchor_offset: int,
    end_anchor_trailing_lines: int,
) -> list[tuple[int, int]]:
    if not lines or search_start > search_end:
        return []

    deduped: set[tuple[int, int]] = set()
    candidates: list[tuple[int, int]] = []
    bounded_search_end = min(len(lines), search_end)
    bounded_search_start = max(1, search_start)

    start_occurrences = [
        line_number
        for line_number in range(bounded_search_start, bounded_search_end + 1)
        if start_with and _line_matches_anchor(lines[line_number - 1], start_with)
    ]
    end_occurrences = [
        line_number
        for line_number in range(bounded_search_start, bounded_search_end + 1)
        if end_with and _line_matches_anchor(lines[line_number - 1], end_with)
    ]

    if start_occurrences:
        for occurrence in start_occurrences[:MAX_ANCHOR_CANDIDATES]:
            candidate_start = occurrence - start_anchor_offset
            if end_occurrences:
                for end_occurrence in end_occurrences[:MAX_ANCHOR_CANDIDATES]:
                    if end_occurrence < occurrence:
                        continue
                    candidate_end = end_occurrence + end_anchor_trailing_lines
                    candidate = (candidate_start, candidate_end)
                    if candidate not in deduped:
                        deduped.add(candidate)
                        candidates.append(candidate)
            else:
                candidate = (candidate_start, candidate_start + line_count - 1)
                if candidate not in deduped:
                    deduped.add(candidate)
                    candidates.append(candidate)
    elif end_occurrences:
        for occurrence in end_occurrences[:MAX_ANCHOR_CANDIDATES]:
            candidate_end = occurrence + end_anchor_trailing_lines
            candidate = (candidate_end - line_count + 1, candidate_end)
            if candidate not in deduped:
                deduped.add(candidate)
                candidates.append(candidate)

    return candidates


def _select_best_candidate(
    *,
    candidates: list[tuple[int, int]],
    lines: list[str],
    original_start: int,
    original_end: int,
    start_with: str | None,
    end_with: str | None,
    start_anchor_offset: int,
    end_anchor_trailing_lines: int,
    content_fingerprint: str | None,
) -> tuple[int, int] | None:
    scored: list[tuple[int, int, tuple[int, int]]] = []
    for candidate in candidates:
        start, end = candidate
        if not _candidate_matches(
            lines=lines,
            start=start,
            end=end,
            start_with=start_with,
            end_with=end_with,
            start_anchor_offset=start_anchor_offset,
            end_anchor_trailing_lines=end_anchor_trailing_lines,
            content_fingerprint=content_fingerprint,
        ):
            continue

        distance = abs(start - original_start) + abs(end - original_end)
        exact_fingerprint_bonus = 0
        if content_fingerprint and _hash_normalized_text("".join(lines[start - 1 : end])) == content_fingerprint:
            exact_fingerprint_bonus = -1_000
        scored.append((exact_fingerprint_bonus + distance, end - start, candidate))

    if not scored:
        return None

    scored.sort(key=lambda item: (item[0], item[1]))
    best_score = scored[0][:2]
    best_candidates = [candidate for score, length, candidate in scored if (score, length) == best_score]
    if len(best_candidates) != 1:
        return None
    return best_candidates[0]


def _find_fingerprint_match(
    *,
    lines: list[str],
    line_count: int,
    content_fingerprint: str | None,
    original_start: int,
    original_end: int,
) -> tuple[int, int] | None:
    if not content_fingerprint or line_count <= 0 or line_count > len(lines):
        return None

    matches: list[tuple[int, int]] = []
    for start in range(1, len(lines) - line_count + 2):
        end = start + line_count - 1
        candidate_fingerprint = _hash_normalized_text("".join(lines[start - 1 : end]))
        if candidate_fingerprint == content_fingerprint:
            matches.append((start, end))

    if not matches:
        return None

    matches.sort(key=lambda item: abs(item[0] - original_start) + abs(item[1] - original_end))
    if len(matches) >= 2:
        best_distance = abs(matches[0][0] - original_start) + abs(matches[0][1] - original_end)
        second_distance = abs(matches[1][0] - original_start) + abs(matches[1][1] - original_end)
        if best_distance == second_distance:
            return None
    return matches[0]


def _find_declaration_match(
    *,
    lines: list[str],
    pin: dict[str, Any],
    original_start: int,
    original_end: int,
    line_count: int,
    end_with: str | None,
    end_anchor_trailing_lines: int,
) -> tuple[int, int] | None:
    declaration_kind = pin.get("declaration_kind")
    declaration_name = pin.get("declaration_name")
    declaration_offset = pin.get("declaration_offset")

    if declaration_kind not in {"python", "javascript"}:
        return None
    if not isinstance(declaration_name, str) or not declaration_name:
        return None
    if not isinstance(declaration_offset, int) or declaration_offset < 0:
        return None

    candidates: list[tuple[int, int]] = []
    for line_number, line in enumerate(lines, start=1):
        if declaration_kind == "python":
            match = _PY_DECLARATION_RE.match(line)
            if not match or match.group(2) != declaration_name:
                continue
        else:
            match = _JS_DECLARATION_RE.match(line)
            if not match or match.group(1) != declaration_name:
                continue

        candidate_start = line_number - declaration_offset
        if end_with:
            end_line = _find_end_anchor_after(lines, start_line=line_number, end_with=end_with)
            candidate_end = end_line + end_anchor_trailing_lines if end_line is not None else candidate_start + line_count - 1
        elif declaration_kind == "python":
            block_end = _estimate_python_block_end(lines, line_number)
            candidate_end = max(candidate_start + line_count - 1, block_end)
        else:
            candidate_end = candidate_start + line_count - 1

        candidates.append((candidate_start, candidate_end))

    if not candidates:
        return None

    candidates.sort(key=lambda item: abs(item[0] - original_start) + abs(item[1] - original_end))
    if len(candidates) >= 2:
        best_distance = abs(candidates[0][0] - original_start) + abs(candidates[0][1] - original_end)
        second_distance = abs(candidates[1][0] - original_start) + abs(candidates[1][1] - original_end)
        if best_distance == second_distance:
            return None
    return candidates[0]


def _find_end_anchor_after(
    lines: list[str],
    *,
    start_line: int,
    end_with: str,
) -> int | None:
    for line_number in range(start_line, len(lines) + 1):
        if _line_matches_anchor(lines[line_number - 1], end_with):
            return line_number
    return None


def _estimate_python_block_end(lines: list[str], declaration_line: int) -> int:
    base_line = lines[declaration_line - 1]
    base_indent = len(base_line) - len(base_line.lstrip(" "))
    end_line = len(lines)

    for line_number in range(declaration_line + 1, len(lines) + 1):
        line = lines[line_number - 1]
        stripped = line.strip()
        if not stripped:
            continue

        current_indent = len(line) - len(line.lstrip(" "))
        if current_indent <= base_indent and (
            _PY_DECLARATION_RE.match(line) or stripped.startswith("@")
        ):
            end_line = line_number - 1
            break

    return max(declaration_line, end_line)


def _resolved_candidate(path: Path, lines: list[str], start: int, end: int) -> tuple[str, dict[str, Any]]:
    if start < 1 or end < start or end > len(lines):
        return "unresolved", {"reason": "candidate_out_of_bounds", "path": str(path)}
    return "resolved", {
        "path": str(path),
        "start": start,
        "end": end,
        "content": "".join(lines[start - 1 : end]),
    }


def _format_summary_line(
    pin: dict[str, Any],
    status: str,
    resolution: dict[str, Any],
    path_counts: Counter[str],
) -> str:
    path = str(pin.get("path", ""))
    mode = str(pin.get("mode", "lines"))
    reason = _clean_optional_text(pin.get("reason"))
    last_resolved = pin.get("last_resolved") if isinstance(pin.get("last_resolved"), dict) else {}
    shared_path_count = path_counts.get(path, 0)
    cleanup = "recommended" if status in {"unresolved", "skipped_due_to_budget"} else "optional"

    if status == "resolved":
        current_location = _format_location(mode, resolution.get("start"), resolution.get("end"))
        extra = f"current={current_location}"
    elif status == "skipped_due_to_budget":
        current_location = _format_location(mode, resolution.get("start"), resolution.get("end"))
        extra = (
            f"current={current_location}; content_chars={resolution.get('content_chars', 0)}; "
            f"remaining_chars={resolution.get('remaining_chars', 0)}"
        )
    else:
        last_known = _format_location(
            mode,
            last_resolved.get("start") if isinstance(last_resolved, dict) else None,
            last_resolved.get("end") if isinstance(last_resolved, dict) else None,
        )
        extra = f"last_known={last_known}; action=re-pin or unpin"

    parts = [
        f"- {pin.get('pin_id', 'pin_unknown')}",
        f"status={status}",
        f"path={path}",
        extra,
        f"cleanup={cleanup}",
    ]
    if reason:
        parts.append(f"reason={reason}")
    if shared_path_count > 1:
        parts.append(f"shared_path=yes({shared_path_count} pins)")
    return " | ".join(parts)


def _format_content_section(pin: dict[str, Any], resolution: dict[str, Any]) -> str:
    location = _format_location(pin.get("mode"), resolution.get("start"), resolution.get("end"))
    header = f"pin_id={pin.get('pin_id', 'pin_unknown')} | path={resolution.get('path', pin.get('path', ''))} | {location}"
    return header + "\n" + str(resolution.get("content", ""))


def _format_location(mode: Any, start: Any, end: Any) -> str:
    if mode == "file":
        return "file"
    if isinstance(start, int) and isinstance(end, int) and start > 0 and end >= start:
        return f"lines={start}-{end}"
    return "lines=unknown"

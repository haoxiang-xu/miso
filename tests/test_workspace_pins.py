from pathlib import Path

from miso.memory import InMemorySessionStore
from miso.workspace import (
    build_pin_record,
    build_pinned_prompt_messages,
    load_workspace_pins,
    save_workspace_pins,
)


def test_pinned_prompt_messages_live_reload_updated_content_and_shifted_range(tmp_path):
    store = InMemorySessionStore()
    session_id = "pin-live-reload"
    file_path = tmp_path / "demo.py"
    file_path.write_text(
        "before\n"
        "def run_task():\n"
        "    value = 1\n"
        "    return value\n"
        "after\n",
        encoding="utf-8",
    )

    lines = file_path.read_text(encoding="utf-8").splitlines(keepends=True)
    pin = build_pin_record(
        path=file_path.resolve(),
        lines=lines,
        start=2,
        end=4,
        start_with="def run_task():",
        reason="keep function in view",
    )
    save_workspace_pins(store, session_id, {}, [pin])

    file_path.write_text(
        "intro\n"
        "before\n"
        "def run_task():\n"
        "    value = 2\n"
        "    return value\n"
        "after\n",
        encoding="utf-8",
    )

    messages = build_pinned_prompt_messages(store=store, session_id=session_id)

    assert len(messages) == 2
    assert "status=resolved" in messages[0]["content"]
    assert "current=lines=3-5" in messages[0]["content"]
    assert "value = 2" in messages[1]["content"]
    assert "value = 1" not in messages[1]["content"]

    _, saved_pins = load_workspace_pins(store, session_id)
    assert saved_pins[0]["last_resolved"] == {"start": 3, "end": 5}


def test_pinned_prompt_messages_mark_unresolved_without_reinjecting_stale_content(tmp_path):
    store = InMemorySessionStore()
    session_id = "pin-unresolved"
    file_path = tmp_path / "demo.py"
    file_path.write_text(
        "before\n"
        "def run_task():\n"
        "    value = 1\n"
        "    return value\n"
        "after\n",
        encoding="utf-8",
    )

    lines = file_path.read_text(encoding="utf-8").splitlines(keepends=True)
    pin = build_pin_record(
        path=file_path.resolve(),
        lines=lines,
        start=2,
        end=4,
        start_with="def run_task():",
    )
    save_workspace_pins(store, session_id, {}, [pin])

    file_path.write_text(
        "before\n"
        "def other_task():\n"
        "    pass\n"
        "after\n",
        encoding="utf-8",
    )

    messages = build_pinned_prompt_messages(store=store, session_id=session_id)

    assert "status=unresolved" in messages[0]["content"]
    assert "re-pin or unpin" in messages[0]["content"]
    assert messages[1]["content"].endswith("No live pinned content injected for this request.")
    assert "return value" not in messages[1]["content"]


def test_pinned_prompt_messages_mark_pins_skipped_due_to_budget(tmp_path):
    store = InMemorySessionStore()
    session_id = "pin-budget"

    small_path = tmp_path / "small.py"
    small_path.write_text("def small():\n    return 1\n", encoding="utf-8")
    small_pin = build_pin_record(
        path=small_path.resolve(),
        lines=small_path.read_text(encoding="utf-8").splitlines(keepends=True),
        start=1,
        end=2,
        start_with="def small():",
    )

    large_path = tmp_path / "large.py"
    large_path.write_text(
        "def large():\n"
        "    alpha = 'aaaaaaaaaa'\n"
        "    beta = 'bbbbbbbbbb'\n"
        "    gamma = 'cccccccccc'\n"
        "    return alpha + beta + gamma\n",
        encoding="utf-8",
    )
    large_pin = build_pin_record(
        path=large_path.resolve(),
        lines=large_path.read_text(encoding="utf-8").splitlines(keepends=True),
        start=1,
        end=5,
        start_with="def large():",
    )

    save_workspace_pins(store, session_id, {}, [small_pin, large_pin])

    messages = build_pinned_prompt_messages(
        store=store,
        session_id=session_id,
        max_total_chars=40,
    )

    assert "status=resolved" in messages[0]["content"]
    assert "status=skipped_due_to_budget" in messages[0]["content"]
    assert "def small():" in messages[1]["content"]
    assert "def large():" not in messages[1]["content"]

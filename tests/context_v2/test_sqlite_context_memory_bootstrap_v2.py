from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

import unchain.persistence.sqlite_context_memory_bootstrap_v2 as bootstrap_module

from unchain.persistence.sqlite_chat_deletion_v2 import (
    ChatDeletionScope,
    SQLiteChatDeletionV2Service,
    read_chat_deletion_tombstone,
)
from unchain.persistence.sqlite_context_memory_bootstrap_v2 import (
    bootstrap_empty_context_memory_v2_database,
)


def test_bootstrap_publishes_complete_empty_plane_for_tombstoning(tmp_path) -> None:
    database_path = tmp_path / "context_v2.sqlite3"
    object_directory = tmp_path / "objects"

    assert bootstrap_empty_context_memory_v2_database(
        database_path=database_path,
        object_directory=object_directory,
    ) is True
    assert database_path.is_file()
    assert object_directory.is_dir()

    receipt = SQLiteChatDeletionV2Service(database_path=database_path).delete_chat(
        scope=ChatDeletionScope(owner_chat_id="chat-empty"),
        operation_id="delete-empty",
    )

    assert receipt.owner_chat_id == "chat-empty"
    assert receipt.replayed is False
    tombstone = read_chat_deletion_tombstone(
        database_path=database_path,
        owner_chat_id="chat-empty",
    )
    assert tombstone is not None
    assert tombstone.scope == ChatDeletionScope(owner_chat_id="chat-empty")


def test_bootstrap_never_replaces_an_existing_database(tmp_path) -> None:
    database_path = tmp_path / "context_v2.sqlite3"
    object_directory = tmp_path / "objects"
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA user_version = 0")
    before = database_path.read_bytes()

    assert bootstrap_empty_context_memory_v2_database(
        database_path=database_path,
        object_directory=object_directory,
    ) is False
    assert database_path.read_bytes() == before
    assert not object_directory.exists()


def test_concurrent_bootstrap_has_one_publisher_and_one_complete_plane(
    tmp_path,
) -> None:
    database_path = tmp_path / "context_v2.sqlite3"
    object_directory = tmp_path / "objects"

    def bootstrap() -> bool:
        return bootstrap_empty_context_memory_v2_database(
            database_path=database_path,
            object_directory=object_directory,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(lambda _: bootstrap(), range(2)))

    assert sorted(results) == [False, True]
    assert database_path.is_file()
    SQLiteChatDeletionV2Service(database_path=database_path)


def test_failed_candidate_bootstrap_never_publishes_partial_state(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "context_v2.sqlite3"
    object_directory = tmp_path / "objects"

    def fail_host_schema(*, database_path) -> None:
        raise RuntimeError("injected host schema failure")

    monkeypatch.setattr(
        bootstrap_module,
        "initialize_sqlite_memory_host_v2_schema",
        fail_host_schema,
    )

    with pytest.raises(RuntimeError, match="injected host schema failure"):
        bootstrap_empty_context_memory_v2_database(
            database_path=database_path,
            object_directory=object_directory,
        )

    assert not database_path.exists()
    assert not object_directory.exists()
    assert not tuple(tmp_path.glob(".context-memory-v2-bootstrap-*"))

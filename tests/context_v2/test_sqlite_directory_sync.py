from __future__ import annotations

import errno
import hashlib
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from unchain.persistence import sqlite_curator_v2, sqlite_memory_v2, sqlite_v2


STORES = [
    (sqlite_v2, sqlite_v2.SQLiteContextV2Store),
    (sqlite_memory_v2, sqlite_memory_v2.SQLiteMemoryV2Store),
    (sqlite_curator_v2, sqlite_curator_v2.SQLiteCuratorV2Store),
]


@pytest.mark.parametrize("module,store_type", STORES)
def test_objects_survive_repeat_second_write_and_reopen(tmp_path, module, store_type):
    options = dict(database_path=tmp_path / "ledger.sqlite3", object_directory=tmp_path / "objects")
    store = store_type(**options)
    for content in (b"hello", b"second message"):
        expected = (hashlib.sha256(content).hexdigest(), len(content))
        assert store._install_object(content) == expected
        assert store._install_object(content) == expected
        reopened = store_type(**options)
        assert reopened._read_object(digest=expected[0], byte_length=expected[1]) == content
    assert len(list((tmp_path / "objects").iterdir())) == 2


@pytest.mark.parametrize("module,store_type", STORES)
def test_windows_does_not_open_directory(tmp_path, monkeypatch, module, store_type):
    platform = SimpleNamespace(name="nt", O_RDONLY=0, open=Mock(side_effect=PermissionError(errno.EACCES, "directory")))
    monkeypatch.setattr(module, "os", platform)
    store_type._fsync_directory(tmp_path)
    platform.open.assert_not_called()


@pytest.mark.parametrize("module,store_type", STORES)
@pytest.mark.parametrize("fails", [False, True])
def test_posix_directory_sync_closes_descriptor_and_propagates_errors(tmp_path, monkeypatch, module, store_type, fails):
    failure = OSError(errno.EIO, "disk error")
    platform = SimpleNamespace(name="posix", O_RDONLY=0, open=Mock(return_value=42), fsync=Mock(side_effect=failure if fails else None), close=Mock())
    monkeypatch.setattr(module, "os", platform)
    if fails:
        with pytest.raises(OSError) as caught:
            store_type._fsync_directory(tmp_path)
        assert caught.value is failure
    else:
        store_type._fsync_directory(tmp_path)
    platform.open.assert_called_once_with(tmp_path, platform.O_RDONLY)
    platform.fsync.assert_called_once_with(42)
    platform.close.assert_called_once_with(42)


@pytest.mark.parametrize("module,store_type", STORES)
def test_file_sync_failure_is_not_suppressed(tmp_path, monkeypatch, module, store_type):
    store = store_type(database_path=tmp_path / "ledger.sqlite3", object_directory=tmp_path / "objects")
    failure = OSError(errno.EIO, "file sync failed")
    monkeypatch.setattr(module.os, "fsync", Mock(side_effect=failure))
    with pytest.raises(OSError) as caught:
        store._install_object(b"must not be acknowledged")
    assert caught.value is failure
    assert list((tmp_path / "objects").iterdir()) == []

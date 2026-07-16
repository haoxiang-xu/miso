from __future__ import annotations

import copy
import json
import os
import threading
import time
import uuid
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator
from weakref import WeakValueDictionary

from ..runtime.payloads import (
    DEFAULT_PAYLOADS_RESOURCE,
    MODEL_CAPABILITIES_RESOURCE,
    load_default_payloads,
    load_model_capabilities,
)
from ..execution import (
    ActiveExecutionLeaseError,
    ExecutionCancellation,
    ExecutionCancelledError,
    ExecutionLease,
    ExecutionLeaseConflictError,
    ExecutionLeaseError,
    ExecutionLeaseExpiredError,
    ExecutionLeaseNotOwnedError,
    StaleExecutionLeaseError,
)
from .revision import (
    SessionRevisionConflictError,
    SessionSnapshot,
    SessionStoreCorruptionError,
    _validate_execution_fence_target,
)

if os.name == "nt":  # pragma: no cover - exercised on Windows
    import msvcrt
else:  # pragma: no cover - platform selection itself is trivial
    import fcntl

try:
    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, FieldCondition, Filter, MatchValue, PointStruct, VectorParams
    _QDRANT_AVAILABLE = True
except ImportError:  # pragma: no cover
    _QDRANT_AVAILABLE = False

DEFAULT_PAYLOADS_FILE = DEFAULT_PAYLOADS_RESOURCE
MODEL_CAPABILITIES_FILE = MODEL_CAPABILITIES_RESOURCE

_SESSION_REVISION_FIELD = "__unchain_session_revision__"
_JSON_SESSION_THREAD_LOCKS: WeakValueDictionary[str, threading.RLock] = WeakValueDictionary()
_JSON_SESSION_THREAD_LOCKS_GUARD = threading.Lock()


def _json_session_thread_lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _JSON_SESSION_THREAD_LOCKS_GUARD:
        lock = _JSON_SESSION_THREAD_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _JSON_SESSION_THREAD_LOCKS[key] = lock
        return lock


@contextmanager
def _exclusive_json_session_lock(path: Path) -> Iterator[None]:
    thread_lock = _json_session_thread_lock(path)
    with thread_lock:
        lock_path = path.with_name(f".{path.name}.lock")
        with lock_path.open("a+b") as lock_file:
            if os.name != "nt":
                os.chmod(lock_path, 0o600)
            if os.name == "nt":  # pragma: no cover - exercised on Windows
                lock_file.seek(0, os.SEEK_END)
                if lock_file.tell() == 0:
                    lock_file.write(b"\0")
                    lock_file.flush()
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            else:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if os.name == "nt":  # pragma: no cover - exercised on Windows
                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


@contextmanager
def _exclusive_json_session_locks(*paths: Path) -> Iterator[None]:
    """Lock multiple session records in a deterministic cross-process order."""

    ordered: dict[str, Path] = {}
    for path in paths:
        ordered[str(path.resolve())] = path
    with ExitStack() as stack:
        for key in sorted(ordered):
            stack.enter_context(_exclusive_json_session_lock(ordered[key]))
        yield


def _load_json_registry(path: str | Path) -> dict[str, dict[str, Any]]:
    resource_name = str(path)
    if resource_name == MODEL_CAPABILITIES_RESOURCE:
        return load_model_capabilities()
    if resource_name == DEFAULT_PAYLOADS_RESOURCE:
        return load_default_payloads()
    return {}


def _resolve_model_key(model: str, registry: dict[str, Any]) -> str | None:
    if model in registry:
        return model

    normalized_model = model.replace(".", "-")
    best: str | None = None
    for key in registry:
        normalized_key = key.replace(".", "-")
        if (
            model.startswith(key)
            or model.startswith(normalized_key)
            or normalized_model.startswith(key)
            or normalized_model.startswith(normalized_key)
            or key.startswith(model)
            or key.startswith(normalized_model)
            or normalized_key.startswith(model)
            or normalized_key.startswith(normalized_model)
        ) and (best is None or len(key) > len(best)):
            best = key
    return best


def _merged_embedding_payload(
    *,
    model_key: str,
    model_capabilities: dict[str, Any],
    default_payloads: dict[str, dict[str, Any]],
    payload: dict[str, Any] | None,
) -> dict[str, Any]:
    defaults = copy.deepcopy(default_payloads.get(model_key, {}))
    if not isinstance(defaults, dict):
        defaults = {}

    user_payload = payload or {}
    for key in list(defaults.keys()):
        if key in user_payload:
            defaults[key] = user_payload[key]

    allowed_keys = model_capabilities.get("allowed_payload_keys")
    if isinstance(allowed_keys, list) and allowed_keys:
        allowed_key_set = {key for key in allowed_keys if isinstance(key, str)}
        for key in user_payload:
            if key in allowed_key_set and key not in defaults:
                defaults[key] = user_payload[key]
        defaults = {key: value for key, value in defaults.items() if key in allowed_key_set}

    defaults = {k: v for k, v in defaults.items() if v is not None or k in user_payload}
    return defaults


def _resolve_embedding_api_key(*, api_key_source: Any | None) -> str:
    if api_key_source is not None:
        source_key = getattr(api_key_source, "api_key", None)
        if isinstance(source_key, str) and source_key.strip():
            return source_key.strip()

    env_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if env_key:
        return env_key

    raise ValueError(
        "error: openai api key is required for embedding requests. "
        "set api_key_source.api_key or OPENAI_API_KEY."
    )


def build_openai_embed_fn(
    *,
    model: str,
    api_key_source: Any | None = None,
    payload: dict[str, Any] | None = None,
) -> tuple[Callable[[list[str]], list[list[float]]], int]:
    """Build an OpenAI embedding function from model JSON config.

    Returns:
        ``(embed_fn, vector_size)``

    Key resolution order:
        1. ``api_key_source.api_key``
        2. ``OPENAI_API_KEY`` env var
    """
    if not isinstance(model, str) or not model.strip():
        raise ValueError("error: embedding model is required")

    requested_model = model.strip()
    model_capabilities_registry = _load_json_registry(MODEL_CAPABILITIES_FILE)
    default_payload_registry = _load_json_registry(DEFAULT_PAYLOADS_FILE)

    resolved_model_key = _resolve_model_key(requested_model, model_capabilities_registry)
    if resolved_model_key is None:
        raise ValueError(f"error: embedding model '{requested_model}' is not configured")

    model_capabilities = model_capabilities_registry.get(resolved_model_key, {})
    provider = str(model_capabilities.get("provider", "")).strip().lower()
    model_type = str(model_capabilities.get("model_type", "")).strip().lower()
    if provider != "openai" or model_type != "embedding":
        raise ValueError(
            f"error: model '{resolved_model_key}' is not configured as an openai embedding model"
        )

    input_payload = payload or {}
    merged_payload = _merged_embedding_payload(
        model_key=resolved_model_key,
        model_capabilities=model_capabilities,
        default_payloads=default_payload_registry,
        payload=input_payload,
    )

    try:
        default_dimensions = int(model_capabilities.get("default_embedding_dimensions", 0))
    except Exception as exc:
        raise ValueError(
            f"error: invalid default embedding dimensions for model '{resolved_model_key}'"
        ) from exc
    if default_dimensions <= 0:
        raise ValueError(
            f"error: model '{resolved_model_key}' must define positive default_embedding_dimensions"
        )

    supports_dimensions = bool(model_capabilities.get("supports_dimensions", False))
    vector_size = default_dimensions
    if "dimensions" in input_payload:
        if not supports_dimensions:
            raise ValueError(f"error: model '{resolved_model_key}' does not support dimensions")
        try:
            vector_size = int(input_payload["dimensions"])
        except Exception as exc:
            raise ValueError("error: dimensions must be a positive integer") from exc
        if vector_size <= 0:
            raise ValueError("error: dimensions must be a positive integer")
        merged_payload["dimensions"] = vector_size

    from openai import OpenAI

    api_key = _resolve_embedding_api_key(api_key_source=api_key_source)
    openai_client = OpenAI(api_key=api_key)

    def _embed(texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        normalized_texts = [text if isinstance(text, str) else str(text) for text in texts]
        request_kwargs: dict[str, Any] = {
            "model": requested_model,
            "input": normalized_texts,
            **copy.deepcopy(merged_payload),
        }
        response = openai_client.embeddings.create(**request_kwargs)
        vectors: list[list[float]] = []
        for item in response.data:
            embedding = getattr(item, "embedding", None)
            if embedding is None and isinstance(item, dict):
                embedding = item.get("embedding")
            if not isinstance(embedding, list):
                raise ValueError("error: invalid embedding response payload from openai")
            vectors.append([float(value) for value in embedding])
        return vectors

    return _embed, vector_size


class QdrantVectorAdapter:
    """VectorStoreAdapter backed by Qdrant embedded storage.

    Each session_id maps to one Qdrant collection so vector spaces are
    fully isolated between chats.
    """

    def __init__(
        self,
        client: "QdrantClient",
        embed_fn,
        vector_size: int,
        collection_prefix: str = "chat",
    ) -> None:
        self._client = client
        self._embed_fn = embed_fn
        self._vector_size = vector_size
        self._collection_prefix = collection_prefix
        self._ensured: set[str] = set()

    def __deepcopy__(self, memo):
        return self

    def __copy__(self):
        return self

    def _collection_name(self, session_id: str) -> str:
        safe = "".join(c if c.isalnum() or c == "_" else "_" for c in session_id)
        return f"{self._collection_prefix}_{safe}"

    def _ensure_collection(self, name: str) -> None:
        if name in self._ensured:
            return
        existing = {c.name for c in self._client.get_collections().collections}
        if name not in existing:
            self._client.create_collection(
                collection_name=name,
                vectors_config=VectorParams(
                    size=self._vector_size,
                    distance=Distance.COSINE,
                ),
            )
        self._ensured.add(name)

    def add_texts(
        self,
        *,
        session_id: str,
        texts: list[str],
        metadatas: list[dict[str, Any]],
    ) -> None:
        collection = self._collection_name(session_id)
        self._ensure_collection(collection)
        vectors = self._embed_fn(texts)
        points = [
            PointStruct(
                id=str(uuid.uuid4()),
                vector=vec,
                payload={"text": text, **meta},
            )
            for text, vec, meta in zip(texts, vectors, metadatas)
        ]
        self._client.upsert(collection_name=collection, points=points)

    def similarity_search(
        self,
        *,
        session_id: str,
        query: str,
        k: int,
        min_score: float | None = None,
    ) -> list[dict[str, Any]]:
        collection = self._collection_name(session_id)
        self._ensure_collection(collection)
        query_vec = self._embed_fn([query])[0]
        results = self._client.search(
            collection_name=collection,
            query_vector=query_vec,
            limit=k,
        )
        recalled: list[dict[str, Any]] = []
        for result in results:
            payload = result.payload or {}
            item: dict[str, Any] = {}
            score = getattr(result, "score", None)
            if min_score is not None:
                if not isinstance(score, (int, float)) or float(score) < float(min_score):
                    continue

            raw_messages = payload.get("messages")
            if isinstance(raw_messages, list):
                item["messages"] = copy.deepcopy(raw_messages)

            text = payload.get("text")
            if isinstance(text, str) and text.strip():
                item["text"] = text

            role = payload.get("role")
            if isinstance(role, str) and role.strip():
                item["role"] = role.strip().lower()

            index = payload.get("index")
            if isinstance(index, int):
                item["index"] = index

            if isinstance(score, (int, float)):
                item["score"] = float(score)

            if item:
                recalled.append(item)
        return recalled


class QdrantLongTermVectorAdapter:
    """Long-term vector adapter backed by Qdrant embedded storage.

    Each namespace maps to one collection so user/application memories stay
    isolated while still being shared across short-term session ids.
    """

    def __init__(
        self,
        client: "QdrantClient",
        embed_fn,
        vector_size: int,
        collection_prefix: str = "long_term",
    ) -> None:
        self._client = client
        self._embed_fn = embed_fn
        self._vector_size = vector_size
        self._collection_prefix = collection_prefix
        self._ensured: set[str] = set()

    def __deepcopy__(self, memo):
        return self

    def __copy__(self):
        return self

    def _collection_name(self, namespace: str) -> str:
        safe = "".join(c if c.isalnum() or c == "_" else "_" for c in namespace)
        return f"{self._collection_prefix}_{safe}"

    def _ensure_collection(self, name: str) -> None:
        if name in self._ensured:
            return
        existing = {c.name for c in self._client.get_collections().collections}
        if name not in existing:
            self._client.create_collection(
                collection_name=name,
                vectors_config=VectorParams(
                    size=self._vector_size,
                    distance=Distance.COSINE,
                ),
            )
        self._ensured.add(name)

    def add_texts(
        self,
        *,
        namespace: str,
        texts: list[str],
        metadatas: list[dict[str, Any]],
    ) -> None:
        collection = self._collection_name(namespace)
        self._ensure_collection(collection)
        vectors = self._embed_fn(texts)
        points = [
            PointStruct(
                id=str(uuid.uuid4()),
                vector=vec,
                payload={"text": text, **meta},
            )
            for text, vec, meta in zip(texts, vectors, metadatas)
        ]
        self._client.upsert(collection_name=collection, points=points)

    def similarity_search(
        self,
        *,
        namespace: str,
        query: str,
        k: int,
        filters: dict[str, Any] | None = None,
        min_score: float | None = None,
    ) -> list[dict[str, Any]]:
        collection = self._collection_name(namespace)
        self._ensure_collection(collection)
        query_vec = self._embed_fn([query])[0]

        qdrant_filter = None
        if filters:
            conditions = []
            for key, value in filters.items():
                if not isinstance(key, str) or not key.strip():
                    continue
                if isinstance(value, (str, int, float, bool)):
                    conditions.append(FieldCondition(key=key, match=MatchValue(value=value)))
            if conditions:
                qdrant_filter = Filter(must=conditions)

        results = self._client.search(
            collection_name=collection,
            query_vector=query_vec,
            limit=k,
            query_filter=qdrant_filter,
        )
        recalled: list[dict[str, Any]] = []
        for result in results:
            payload = result.payload or {}
            if not isinstance(payload, dict):
                continue
            item = copy.deepcopy(payload)
            score = getattr(result, "score", None)
            if min_score is not None:
                if not isinstance(score, (int, float)) or float(score) < float(min_score):
                    continue
            if isinstance(score, (int, float)):
                item["score"] = float(score)
            if item:
                recalled.append(item)
        return recalled


class JsonFileSessionStore:
    """SessionStore backed by one JSON file per session.

    Stores messages, vector_indexed_until, and summary so the unchain
    MemoryManager can work correctly across process restarts without
    re-embedding already-indexed messages.

    Path layout:
        {base_dir}/{sanitized_session_id}.json
    """

    execution_lease_scope = "host_local"

    def __init__(
        self,
        base_dir: str | Path,
        *,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        self._base = Path(base_dir)
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self._base.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != "nt":
            os.chmod(self._base, 0o700)

    def _path(self, session_id: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in session_id)
        return self._base / f"{safe}.json"

    @staticmethod
    def _lease_path(path: Path) -> Path:
        return path.with_name(f".{path.name}.lease")

    def _now_ms(self) -> int:
        now = self._clock_ms()
        if isinstance(now, bool) or not isinstance(now, int):
            raise TypeError("clock_ms must return an integer epoch timestamp")
        return now

    @staticmethod
    def _validate_lease_request(
        execution_id: str,
        owner_id: str,
        *,
        ttl_ms: int | None = None,
        fencing_token: int | None = None,
    ) -> None:
        if not isinstance(execution_id, str) or not execution_id.strip():
            raise ValueError("execution_id must be a non-empty string")
        if not isinstance(owner_id, str) or not owner_id.strip():
            raise ValueError("owner_id must be a non-empty string")
        if ttl_ms is not None and (
            isinstance(ttl_ms, bool) or not isinstance(ttl_ms, int) or ttl_ms <= 0
        ):
            raise ValueError("ttl_ms must be a positive integer")
        if fencing_token is not None and (
            isinstance(fencing_token, bool)
            or not isinstance(fencing_token, int)
            or fencing_token <= 0
        ):
            raise ValueError("fencing_token must be a positive integer")

    @staticmethod
    def _validate_expected_revision(expected_revision: int | None) -> None:
        if expected_revision is not None and (
            isinstance(expected_revision, bool)
            or not isinstance(expected_revision, int)
            or expected_revision < 0
        ):
            raise ValueError("expected_revision must be a non-negative integer")

    def _read_lease_unlocked(
        self,
        execution_id: str,
        path: Path,
    ) -> dict[str, Any]:
        lease_path = self._lease_path(path)
        if not lease_path.exists():
            return {
                "last_fencing_token": 0,
                "last_owner_id": "",
                "active": None,
                "cancellations": {},
            }
        try:
            raw = json.loads(lease_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise SessionStoreCorruptionError(
                session_id=execution_id,
                detail=f"cannot decode execution lease {lease_path.name}",
            ) from exc
        if not isinstance(raw, dict):
            raise SessionStoreCorruptionError(
                session_id=execution_id,
                detail="execution lease must be a JSON object",
            )
        last_token = raw.get("last_fencing_token", 0)
        last_owner_id = raw.get("last_owner_id", "")
        if (
            isinstance(last_token, bool)
            or not isinstance(last_token, int)
            or last_token < 0
            or not isinstance(last_owner_id, str)
        ):
            raise SessionStoreCorruptionError(
                session_id=execution_id,
                detail="execution lease watermark is invalid",
            )
        active_raw = raw.get("active")
        active = None
        if active_raw is not None:
            if not isinstance(active_raw, dict):
                raise SessionStoreCorruptionError(
                    session_id=execution_id,
                    detail="active execution lease is invalid",
                )
            try:
                active = ExecutionLease(
                    execution_id=execution_id,
                    owner_id=active_raw["owner_id"],
                    fencing_token=active_raw["fencing_token"],
                    acquired_at_ms=active_raw["acquired_at_ms"],
                    expires_at_ms=active_raw["expires_at_ms"],
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise SessionStoreCorruptionError(
                    session_id=execution_id,
                    detail="active execution lease fields are invalid",
                ) from exc
            if (
                not active.owner_id
                or active.fencing_token <= 0
                or active.acquired_at_ms < 0
                or active.expires_at_ms <= active.acquired_at_ms
                or active.fencing_token != last_token
                or last_owner_id != active.owner_id
            ):
                raise SessionStoreCorruptionError(
                    session_id=execution_id,
                    detail="active execution lease does not match its watermark",
                )
        cancellations_raw = raw.get("cancellations", {})
        if not isinstance(cancellations_raw, dict):
            raise SessionStoreCorruptionError(
                session_id=execution_id,
                detail="execution cancellations must be an object",
            )
        cancellations: dict[str, ExecutionCancellation] = {}
        for owner_id, cancellation_raw in cancellations_raw.items():
            if (
                not isinstance(owner_id, str)
                or not owner_id
                or not isinstance(cancellation_raw, dict)
            ):
                raise SessionStoreCorruptionError(
                    session_id=execution_id,
                    detail="execution cancellation identity is invalid",
                )
            try:
                cancellation = ExecutionCancellation(
                    execution_id=cancellation_raw["execution_id"],
                    owner_id=cancellation_raw["owner_id"],
                    fencing_token=cancellation_raw.get("fencing_token"),
                    requested_at_ms=cancellation_raw["requested_at_ms"],
                    reason=cancellation_raw.get("reason", ""),
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise SessionStoreCorruptionError(
                    session_id=execution_id,
                    detail="execution cancellation fields are invalid",
                ) from exc
            if (
                cancellation.execution_id != execution_id
                or cancellation.owner_id != owner_id
            ):
                raise SessionStoreCorruptionError(
                    session_id=execution_id,
                    detail="execution cancellation belongs to another owner",
                )
            cancellations[owner_id] = cancellation
        return {
            "last_fencing_token": last_token,
            "last_owner_id": last_owner_id,
            "active": active,
            "cancellations": cancellations,
        }

    def _write_lease_unlocked(
        self,
        path: Path,
        record: dict[str, Any],
    ) -> None:
        active = record.get("active")
        payload = {
            "version": 1,
            "last_fencing_token": int(record.get("last_fencing_token") or 0),
            "last_owner_id": str(record.get("last_owner_id") or ""),
            "active": (
                {
                    "owner_id": active.owner_id,
                    "fencing_token": active.fencing_token,
                    "acquired_at_ms": active.acquired_at_ms,
                    "expires_at_ms": active.expires_at_ms,
                }
                if isinstance(active, ExecutionLease)
                else None
            ),
            "cancellations": {
                owner_id: {
                    "execution_id": cancellation.execution_id,
                    "owner_id": cancellation.owner_id,
                    "fencing_token": cancellation.fencing_token,
                    "requested_at_ms": cancellation.requested_at_ms,
                    "reason": cancellation.reason,
                }
                for owner_id, cancellation in dict(
                    record.get("cancellations") or {}
                ).items()
                if isinstance(cancellation, ExecutionCancellation)
            },
        }
        self._write_json_unlocked(path=self._lease_path(path), payload=payload)

    def _assert_active_lease_unlocked(
        self,
        execution_id: str,
        owner_id: str,
        fencing_token: int,
        path: Path,
    ) -> ExecutionLease:
        record = self._read_lease_unlocked(execution_id, path)
        cancellation = dict(record.get("cancellations") or {}).get(owner_id)
        if isinstance(cancellation, ExecutionCancellation):
            raise ExecutionCancelledError(
                cancellation.reason or "execution was cancelled",
                execution_id=execution_id,
                owner_id=owner_id,
                fencing_token=cancellation.fencing_token or fencing_token,
            )
        active = record.get("active")
        last_token = int(record.get("last_fencing_token") or 0)
        if not isinstance(active, ExecutionLease):
            if fencing_token <= last_token:
                raise StaleExecutionLeaseError(
                    "execution lease has already been released or superseded",
                    execution_id=execution_id,
                    owner_id=owner_id,
                    fencing_token=fencing_token,
                )
            raise ExecutionLeaseNotOwnedError(
                "execution lease is not owned",
                execution_id=execution_id,
                owner_id=owner_id,
                fencing_token=fencing_token,
            )
        if fencing_token != active.fencing_token:
            error_type = (
                StaleExecutionLeaseError
                if fencing_token < active.fencing_token
                else ExecutionLeaseNotOwnedError
            )
            raise error_type(
                "execution fencing token is not current",
                execution_id=execution_id,
                owner_id=owner_id,
                fencing_token=fencing_token,
            )
        if owner_id != active.owner_id:
            raise ExecutionLeaseNotOwnedError(
                "execution lease belongs to a different owner",
                execution_id=execution_id,
                owner_id=owner_id,
                fencing_token=fencing_token,
            )
        if self._now_ms() >= active.expires_at_ms:
            raise ExecutionLeaseExpiredError(
                "execution lease has expired",
                execution_id=execution_id,
                owner_id=owner_id,
                fencing_token=fencing_token,
            )
        return active

    def _ensure_unfenced_write_allowed_unlocked(
        self,
        session_id: str,
        path: Path,
    ) -> None:
        active = self._read_lease_unlocked(session_id, path).get("active")
        if isinstance(active, ExecutionLease) and self._now_ms() < active.expires_at_ms:
            raise ActiveExecutionLeaseError(
                "unfenced session write is forbidden while an execution lease is active",
                execution_id=session_id,
                owner_id=active.owner_id,
                fencing_token=active.fencing_token,
            )

    def _read_unlocked(self, session_id: str, path: Path) -> SessionSnapshot:
        if not path.exists():
            return SessionSnapshot(state={}, revision=0)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise SessionStoreCorruptionError(
                session_id=session_id,
                detail=f"cannot decode {path.name}",
            ) from exc
        if not isinstance(raw, dict):
            raise SessionStoreCorruptionError(
                session_id=session_id,
                detail=f"{path.name} must contain a JSON object",
            )

        revision = raw.pop(_SESSION_REVISION_FIELD, 0)
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise SessionStoreCorruptionError(
                session_id=session_id,
                detail=f"{path.name} contains an invalid revision",
            )
        return SessionSnapshot(state=copy.deepcopy(raw), revision=revision)

    def _write_unlocked(
        self,
        *,
        path: Path,
        state: dict[str, Any],
        revision: int,
    ) -> None:
        payload = copy.deepcopy(state)
        payload.pop(_SESSION_REVISION_FIELD, None)
        payload[_SESSION_REVISION_FIELD] = revision
        self._write_json_unlocked(path=path, payload=payload)

    def _write_json_unlocked(self, *, path: Path, payload: dict[str, Any]) -> None:
        serialized = json.dumps(payload, default=str, ensure_ascii=False)
        temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temp_fd = os.open(
                temp_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            with os.fdopen(temp_fd, "w", encoding="utf-8") as temp_file:
                temp_file.write(serialized)
                temp_file.flush()
                os.fsync(temp_file.fileno())
            os.replace(temp_path, path)
            if os.name != "nt":
                os.chmod(path, 0o600)
                directory_fd = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        finally:
            temp_path.unlink(missing_ok=True)

    def load(self, session_id: str) -> dict[str, Any]:
        return self.load_with_revision(session_id).state

    def save(self, session_id: str, state: dict[str, Any]) -> None:
        path = self._path(session_id)
        with _exclusive_json_session_lock(path):
            self._ensure_unfenced_write_allowed_unlocked(session_id, path)
            snapshot = self._read_unlocked(session_id, path)
            self._write_unlocked(
                path=path,
                state=state,
                revision=int(snapshot.revision or 0) + 1,
            )

    def load_with_revision(self, session_id: str) -> SessionSnapshot:
        path = self._path(session_id)
        with _exclusive_json_session_lock(path):
            return self._read_unlocked(session_id, path)

    def save_if_revision(
        self,
        session_id: str,
        state: dict[str, Any],
        expected_revision: int,
    ) -> int:
        if (
            isinstance(expected_revision, bool)
            or not isinstance(expected_revision, int)
            or expected_revision < 0
        ):
            raise ValueError("expected_revision must be a non-negative integer")
        path = self._path(session_id)
        with _exclusive_json_session_lock(path):
            self._ensure_unfenced_write_allowed_unlocked(session_id, path)
            snapshot = self._read_unlocked(session_id, path)
            actual_revision = int(snapshot.revision or 0)
            if actual_revision != expected_revision:
                raise SessionRevisionConflictError(
                    session_id=session_id,
                    expected_revision=expected_revision,
                    actual_revision=actual_revision,
                )
            next_revision = actual_revision + 1
            self._write_unlocked(
                path=path,
                state=state,
                revision=next_revision,
            )
            return next_revision

    def save_if_revision_and_execution_not_cancelled(
        self,
        session_id: str,
        state: dict[str, Any],
        expected_revision: int,
        *,
        execution_id: str,
        owner_id: str,
    ) -> int:
        """CAS a detached child write and root cancellation check together."""

        _validate_execution_fence_target(session_id, execution_id)
        self._validate_expected_revision(expected_revision)
        self._validate_lease_request(execution_id, owner_id)
        path = self._path(session_id)
        execution_path = self._path(execution_id)
        with _exclusive_json_session_locks(path, execution_path):
            record = self._read_lease_unlocked(execution_id, execution_path)
            cancellation = dict(record.get("cancellations") or {}).get(owner_id)
            if isinstance(cancellation, ExecutionCancellation):
                raise ExecutionCancelledError(
                    cancellation.reason or "execution was cancelled",
                    execution_id=execution_id,
                    owner_id=owner_id,
                    fencing_token=cancellation.fencing_token,
                )
            snapshot = self._read_unlocked(session_id, path)
            actual_revision = int(snapshot.revision or 0)
            if actual_revision != expected_revision:
                raise SessionRevisionConflictError(
                    session_id=session_id,
                    expected_revision=expected_revision,
                    actual_revision=actual_revision,
                )
            next_revision = actual_revision + 1
            self._write_unlocked(
                path=path,
                state=state,
                revision=next_revision,
            )
            return next_revision

    def save_if_revision_and_execution_cancelled(
        self,
        session_id: str,
        state: dict[str, Any],
        expected_revision: int,
        *,
        execution_id: str,
        owner_id: str,
    ) -> int:
        """CAS terminal cleanup and exact-root tombstone proof together."""

        _validate_execution_fence_target(session_id, execution_id)
        self._validate_expected_revision(expected_revision)
        self._validate_lease_request(execution_id, owner_id)
        path = self._path(session_id)
        execution_path = self._path(execution_id)
        with _exclusive_json_session_locks(path, execution_path):
            record = self._read_lease_unlocked(execution_id, execution_path)
            cancellation = dict(record.get("cancellations") or {}).get(owner_id)
            if not isinstance(cancellation, ExecutionCancellation):
                raise ExecutionLeaseError(
                    "execution cancellation is required for terminal cleanup",
                    execution_id=execution_id,
                    owner_id=owner_id,
                )
            snapshot = self._read_unlocked(session_id, path)
            actual_revision = int(snapshot.revision or 0)
            if actual_revision != expected_revision:
                raise SessionRevisionConflictError(
                    session_id=session_id,
                    expected_revision=expected_revision,
                    actual_revision=actual_revision,
                )
            next_revision = actual_revision + 1
            self._write_unlocked(
                path=path,
                state=state,
                revision=next_revision,
            )
            return next_revision

    def acquire_lease(
        self,
        execution_id: str,
        owner_id: str,
        ttl_ms: int,
        *,
        expected_revision: int | None = None,
    ) -> ExecutionLease:
        self._validate_lease_request(execution_id, owner_id, ttl_ms=ttl_ms)
        self._validate_expected_revision(expected_revision)
        path = self._path(execution_id)
        with _exclusive_json_session_lock(path):
            snapshot = self._read_unlocked(execution_id, path)
            actual_revision = int(snapshot.revision or 0)
            if expected_revision is not None and actual_revision != expected_revision:
                raise SessionRevisionConflictError(
                    session_id=execution_id,
                    expected_revision=expected_revision,
                    actual_revision=actual_revision,
                )
            now = self._now_ms()
            record = self._read_lease_unlocked(execution_id, path)
            cancellation = dict(record.get("cancellations") or {}).get(owner_id)
            if isinstance(cancellation, ExecutionCancellation):
                raise ExecutionCancelledError(
                    cancellation.reason or "execution was cancelled",
                    execution_id=execution_id,
                    owner_id=owner_id,
                    fencing_token=cancellation.fencing_token,
                )
            active = record.get("active")
            if isinstance(active, ExecutionLease) and now < active.expires_at_ms:
                if active.owner_id == owner_id:
                    return active
                raise ExecutionLeaseConflictError(
                    "execution is already leased by another owner",
                    execution_id=execution_id,
                    owner_id=owner_id,
                    fencing_token=active.fencing_token,
                )
            token = int(record.get("last_fencing_token") or 0) + 1
            lease = ExecutionLease(
                execution_id=execution_id,
                owner_id=owner_id,
                fencing_token=token,
                acquired_at_ms=now,
                expires_at_ms=now + ttl_ms,
            )
            record.update(
                {
                    "last_fencing_token": token,
                    "last_owner_id": owner_id,
                    "active": lease,
                }
            )
            self._write_lease_unlocked(path, record)
            return lease

    def request_execution_cancel(
        self,
        execution_id: str,
        owner_id: str,
        *,
        reason: str = "",
    ) -> ExecutionCancellation:
        self._validate_lease_request(execution_id, owner_id)
        if not isinstance(reason, str):
            raise ValueError("reason must be a string")
        path = self._path(execution_id)
        with _exclusive_json_session_lock(path):
            record = self._read_lease_unlocked(execution_id, path)
            cancellations = dict(record.get("cancellations") or {})
            existing = cancellations.get(owner_id)
            if isinstance(existing, ExecutionCancellation):
                return existing
            active = record.get("active")
            fencing_token = None
            if isinstance(active, ExecutionLease) and active.owner_id == owner_id:
                fencing_token = active.fencing_token
                record["active"] = None
            elif str(record.get("last_owner_id") or "") == owner_id:
                last_token = int(record.get("last_fencing_token") or 0)
                fencing_token = last_token or None
            cancellation = ExecutionCancellation(
                execution_id=execution_id,
                owner_id=owner_id,
                fencing_token=fencing_token,
                requested_at_ms=self._now_ms(),
                reason=reason,
            )
            cancellations[owner_id] = cancellation
            record["cancellations"] = cancellations
            self._write_lease_unlocked(path, record)
            return cancellation

    def load_execution_cancellation(
        self,
        execution_id: str,
        owner_id: str,
    ) -> ExecutionCancellation | None:
        self._validate_lease_request(execution_id, owner_id)
        path = self._path(execution_id)
        with _exclusive_json_session_lock(path):
            record = self._read_lease_unlocked(execution_id, path)
            cancellation = dict(record.get("cancellations") or {}).get(owner_id)
            return (
                cancellation
                if isinstance(cancellation, ExecutionCancellation)
                else None
            )

    def verify_lease(
        self,
        execution_id: str,
        owner_id: str,
        fencing_token: int,
    ) -> ExecutionLease:
        self._validate_lease_request(
            execution_id,
            owner_id,
            fencing_token=fencing_token,
        )
        path = self._path(execution_id)
        with _exclusive_json_session_lock(path):
            return self._assert_active_lease_unlocked(
                execution_id,
                owner_id,
                fencing_token,
                path,
            )

    def renew_lease(
        self,
        execution_id: str,
        owner_id: str,
        fencing_token: int,
        ttl_ms: int,
    ) -> ExecutionLease:
        self._validate_lease_request(
            execution_id,
            owner_id,
            ttl_ms=ttl_ms,
            fencing_token=fencing_token,
        )
        path = self._path(execution_id)
        with _exclusive_json_session_lock(path):
            active = self._assert_active_lease_unlocked(
                execution_id,
                owner_id,
                fencing_token,
                path,
            )
            record = self._read_lease_unlocked(execution_id, path)
            renewed = ExecutionLease(
                execution_id=active.execution_id,
                owner_id=active.owner_id,
                fencing_token=active.fencing_token,
                acquired_at_ms=active.acquired_at_ms,
                expires_at_ms=max(active.expires_at_ms, self._now_ms() + ttl_ms),
            )
            record["active"] = renewed
            self._write_lease_unlocked(path, record)
            return renewed

    def release_lease(
        self,
        execution_id: str,
        owner_id: str,
        fencing_token: int,
    ) -> None:
        self._validate_lease_request(
            execution_id,
            owner_id,
            fencing_token=fencing_token,
        )
        path = self._path(execution_id)
        with _exclusive_json_session_lock(path):
            record = self._read_lease_unlocked(execution_id, path)
            active = record.get("active")
            if not isinstance(active, ExecutionLease):
                if (
                    int(record.get("last_fencing_token") or 0) == fencing_token
                    and str(record.get("last_owner_id") or "") == owner_id
                ):
                    return
                self._assert_active_lease_unlocked(
                    execution_id,
                    owner_id,
                    fencing_token,
                    path,
                )
                return
            self._assert_active_lease_unlocked(
                execution_id,
                owner_id,
                fencing_token,
                path,
            )
            record["active"] = None
            self._write_lease_unlocked(path, record)

    def save_if_revision_and_fence(
        self,
        session_id: str,
        state: dict[str, Any],
        expected_revision: int,
        *,
        execution_id: str,
        owner_id: str,
        fencing_token: int,
    ) -> int:
        _validate_execution_fence_target(session_id, execution_id)
        self._validate_expected_revision(expected_revision)
        self._validate_lease_request(
            execution_id,
            owner_id,
            fencing_token=fencing_token,
        )
        path = self._path(session_id)
        execution_path = self._path(execution_id)
        with _exclusive_json_session_locks(path, execution_path):
            self._assert_active_lease_unlocked(
                execution_id,
                owner_id,
                fencing_token,
                execution_path,
            )
            snapshot = self._read_unlocked(session_id, path)
            actual_revision = int(snapshot.revision or 0)
            if actual_revision != expected_revision:
                raise SessionRevisionConflictError(
                    session_id=session_id,
                    expected_revision=expected_revision,
                    actual_revision=actual_revision,
                )
            next_revision = actual_revision + 1
            self._write_unlocked(
                path=path,
                state=state,
                revision=next_revision,
            )
            return next_revision


def build_embedded_qdrant_client(*, path: str | Path) -> "QdrantClient":
    if not _QDRANT_AVAILABLE:
        raise ValueError(
            "error: qdrant-client is required for embedded Qdrant storage. "
            "install 'qdrant-client' or provide a custom vector adapter."
        )
    base_path = Path(path)
    base_path.mkdir(parents=True, exist_ok=True)
    return QdrantClient(path=str(base_path))


def build_default_long_term_qdrant_vector_adapter(
    *,
    api_key_source: Any | None = None,
    model: str = "text-embedding-3-small",
    payload: dict[str, Any] | None = None,
    path: str | Path,
    collection_prefix: str = "long_term",
) -> QdrantLongTermVectorAdapter:
    if not _QDRANT_AVAILABLE:
        raise ValueError(
            "error: qdrant-client is required for default long-term vector storage. "
            "install 'qdrant-client' or provide MemoryConfig.long_term.vector_adapter."
        )
    client = build_embedded_qdrant_client(path=path)
    embed_fn, vector_size = build_openai_embed_fn(
        model=model,
        api_key_source=api_key_source,
        payload=payload,
    )
    return QdrantLongTermVectorAdapter(
        client=client,
        embed_fn=embed_fn,
        vector_size=vector_size,
        collection_prefix=collection_prefix,
    )


__all__ = [
    "JsonFileSessionStore",
    "QdrantLongTermVectorAdapter",
    "QdrantVectorAdapter",
    "build_default_long_term_qdrant_vector_adapter",
    "build_embedded_qdrant_client",
    "build_openai_embed_fn",
]

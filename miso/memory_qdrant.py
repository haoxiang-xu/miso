from __future__ import annotations

import copy
import json
import uuid
from pathlib import Path
from typing import Any

try:
    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, PointStruct, VectorParams
    _QDRANT_AVAILABLE = True
except ImportError:  # pragma: no cover
    _QDRANT_AVAILABLE = False


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
    ) -> list[str]:
        collection = self._collection_name(session_id)
        self._ensure_collection(collection)
        query_vec = self._embed_fn([query])[0]
        results = self._client.search(
            collection_name=collection,
            query_vector=query_vec,
            limit=k,
        )
        return [r.payload.get("text", "") for r in results if r.payload]


class JsonFileSessionStore:
    """SessionStore backed by one JSON file per session.

    Stores messages, vector_indexed_until, and summary so the miso
    MemoryManager can work correctly across process restarts without
    re-embedding already-indexed messages.

    Path layout:
        {base_dir}/{sanitized_session_id}.json
    """

    def __init__(self, base_dir: str | Path) -> None:
        self._base = Path(base_dir)
        self._base.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in session_id)
        return self._base / f"{safe}.json"

    def load(self, session_id: str) -> dict[str, Any]:
        p = self._path(session_id)
        if not p.exists():
            return {}
        try:
            return copy.deepcopy(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            return {}

    def save(self, session_id: str, state: dict[str, Any]) -> None:
        p = self._path(session_id)
        try:
            p.write_text(
                json.dumps(state, default=str, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass


__all__ = ["JsonFileSessionStore", "QdrantVectorAdapter"]

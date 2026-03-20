from __future__ import annotations

import copy
import json
import os
import uuid
from pathlib import Path
from typing import Any, Callable

try:
    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, FieldCondition, Filter, MatchValue, PointStruct, VectorParams
    _QDRANT_AVAILABLE = True
except ImportError:  # pragma: no cover
    _QDRANT_AVAILABLE = False

DEFAULT_PAYLOADS_FILE = Path(__file__).with_name("model_default_payloads.json")
MODEL_CAPABILITIES_FILE = Path(__file__).with_name("model_capabilities.json")


def _load_json_registry(path: str | Path) -> dict[str, dict[str, Any]]:
    file_path = Path(path)
    if not file_path.exists():
        return {}

    try:
        raw = json.loads(file_path.read_text(encoding="utf-8"))
    except Exception:
        return {}

    if not isinstance(raw, dict):
        return {}

    parsed: dict[str, dict[str, Any]] = {}
    for key, value in raw.items():
        if isinstance(key, str) and isinstance(value, dict):
            parsed[key] = value
    return parsed


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


def _resolve_embedding_api_key(*, broth_instance: Any | None) -> str:
    if broth_instance is not None:
        broth_key = getattr(broth_instance, "api_key", None)
        if isinstance(broth_key, str) and broth_key.strip():
            return broth_key.strip()

    env_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if env_key:
        return env_key

    raise ValueError(
        "error: openai api key is required for embedding requests. "
        "set broth.api_key or OPENAI_API_KEY."
    )


def build_openai_embed_fn(
    *,
    model: str,
    broth_instance: Any | None = None,
    payload: dict[str, Any] | None = None,
) -> tuple[Callable[[list[str]], list[list[float]]], int]:
    """Build an OpenAI embedding function from model JSON config.

    Returns:
        ``(embed_fn, vector_size)``

    Key resolution order:
        1. ``broth_instance.api_key``
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

    api_key = _resolve_embedding_api_key(broth_instance=broth_instance)
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
    broth_instance: Any | None = None,
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
        broth_instance=broth_instance,
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

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from unchain.journal.models import (
    ArtifactRef,
    ResourceRef,
    _bounded_int,
    _freeze_json,
    _required_text,
    _thaw_json,
)
from unchain.journal.runtime import build_operation_ref

from .ports import (
    BoundArtifactRepository,
    ContextRepositoryError,
    ContextScopeError,
)


MAX_ARTIFACT_BYTES = 32 * 1024 * 1024
MAX_INLINE_TOOL_RESULT_BYTES = 16_000
MAX_PREVIEW_BYTES = 1_200
MAX_ARTIFACT_READ_BYTES = 65_536


class ArtifactServiceError(RuntimeError):
    """Base error for durable artifact semantics."""


class ArtifactTooLargeError(ArtifactServiceError):
    """Content exceeded the P0 durable object ceiling."""


class ArtifactBudgetError(ArtifactServiceError):
    """A full read would exceed the caller's remaining context budget."""


class ArtifactIntegrityError(ArtifactServiceError):
    """A repository receipt or read contradicted the content descriptor."""


class ContentSanitizer(Protocol):
    def __call__(self, content: bytes, media_type: str) -> bytes:
        ...


@dataclass(frozen=True)
class ArtifactReadPage:
    artifact: ArtifactRef
    offset: int
    data: bytes
    next_offset: int
    has_more: bool


@dataclass(frozen=True)
class ToolResultArtifactization:
    artifact: ArtifactRef
    visible_result: Any
    result_bytes: int
    result_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.artifact, ArtifactRef):
            object.__setattr__(self, "artifact", ArtifactRef.from_dict(self.artifact))
        frozen = _freeze_json(self.visible_result, path="visible_result")
        object.__setattr__(self, "visible_result", frozen)
        if self.result_bytes != self.artifact.byte_length:
            raise ArtifactIntegrityError("result byte count does not match artifact")
        if self.result_sha256 != self.artifact.sha256:
            raise ArtifactIntegrityError("result sha256 does not match artifact")

    @property
    def full_output_ref(self) -> ResourceRef:
        return self.artifact.ref

    def event_fields(self) -> dict[str, Any]:
        return {
            "result": _thaw_json(self.visible_result),
            "full_output_ref": self.full_output_ref.to_dict(),
            "result_bytes": self.result_bytes,
            "result_sha256": self.result_sha256,
        }


@dataclass(frozen=True)
class ToolCompletionArtifactization:
    artifact: ArtifactRef
    completion: Any

    def __post_init__(self) -> None:
        if not isinstance(self.artifact, ArtifactRef):
            object.__setattr__(
                self,
                "artifact",
                ArtifactRef.from_dict(self.artifact),
            )
        object.__setattr__(
            self,
            "completion",
            _freeze_json(self.completion, path="completion"),
        )


def _normalize_media_type(value: object) -> str:
    media_type = _required_text(value, "media_type", maximum=255).casefold()
    if "/" not in media_type:
        raise ValueError("media_type must be a MIME type")
    return media_type


def _bounded_utf8_preview(content: bytes, limit: int = MAX_PREVIEW_BYTES) -> str:
    if not content or limit <= 0:
        return ""
    # ArtifactRef stores optional text through the journal model's canonical
    # whitespace normalization. Normalize at derivation time as well so the
    # descriptor, operation receipt, and later integrity checks all agree even
    # when the byte boundary lands on whitespace.
    return content[:limit].decode("utf-8", errors="ignore").strip()


def _supports_text_preview(media_type: str) -> bool:
    base_type = media_type.split(";", 1)[0].strip()
    return (
        base_type.startswith("text/")
        or base_type == "application/json"
        or base_type.endswith("+json")
        or base_type in {"application/xml", "application/yaml"}
        or base_type.endswith("+xml")
    )


def _canonical_json_value(value: Any) -> bytes:
    frozen = _freeze_json(value, path="content")
    return json.dumps(
        _thaw_json(frozen),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


class ArtifactService:
    """Owns P0 object limits, hashing, preview reduction, and read budgets."""

    def __init__(
        self,
        repository: BoundArtifactRepository,
        *,
        sanitizer: ContentSanitizer,
        user_message_sanitizer: ContentSanitizer | None = None,
    ) -> None:
        if not isinstance(repository, BoundArtifactRepository):
            raise TypeError("repository must be a BoundArtifactRepository")
        if not callable(sanitizer):
            raise TypeError("sanitizer must be callable")
        if user_message_sanitizer is not None and not callable(
            user_message_sanitizer
        ):
            raise TypeError("user_message_sanitizer must be callable")
        self._repository = repository
        self._sanitizer = sanitizer
        self._user_message_sanitizer = (
            sanitizer
            if user_message_sanitizer is None
            else user_message_sanitizer
        )

    @property
    def execution_id(self) -> str:
        return self._repository.execution_id

    def _sanitize(self, content: bytes, media_type: str) -> bytes:
        if not isinstance(content, bytes):
            raise TypeError("artifact content must be bytes")
        if len(content) > MAX_ARTIFACT_BYTES:
            raise ArtifactTooLargeError("artifact exceeds the 32 MiB P0 limit")
        sanitized = self._sanitizer(content, media_type)
        if not isinstance(sanitized, bytes):
            raise TypeError("artifact sanitizer must return bytes")
        if len(sanitized) > MAX_ARTIFACT_BYTES:
            raise ArtifactTooLargeError("artifact exceeds the 32 MiB P0 limit")
        return sanitized

    def _persist_sanitized(
        self,
        content: bytes,
        *,
        media_type: str,
        operation_id: object,
        operation_binding: Mapping[str, Any] | None = None,
    ) -> ArtifactRef:
        digest = hashlib.sha256(content).hexdigest()
        preview = (
            _bounded_utf8_preview(content)
            if _supports_text_preview(media_type)
            else ""
        )
        operation = build_operation_ref(
            operation_id,
            domain="context.artifact.put",
            payload={
                "media_type": media_type,
                "byte_length": len(content),
                "sha256": digest,
                "preview": preview,
                "binding": dict(operation_binding or {}),
            },
        )
        artifact = self._repository.put(
            content=content,
            media_type=media_type,
            operation=operation,
            preview=preview,
        )
        if not isinstance(artifact, ArtifactRef):
            raise ArtifactIntegrityError(
                "artifact repository did not return an ArtifactRef"
            )
        if artifact.ref.kind != "artifact" or artifact.ref.fragment:
            raise ArtifactIntegrityError("artifact ref is not a whole artifact")
        if artifact.media_type != media_type:
            raise ArtifactIntegrityError("artifact media_type does not match content")
        if artifact.byte_length != len(content):
            raise ArtifactIntegrityError("artifact byte_length does not match content")
        if artifact.sha256 != digest:
            raise ArtifactIntegrityError("artifact sha256 does not match content")
        if artifact.preview != preview:
            raise ArtifactIntegrityError("artifact preview does not match content")
        return artifact

    def persist(
        self,
        content: bytes,
        *,
        media_type: object,
        operation_id: object,
    ) -> ArtifactRef:
        normalized_media_type = _normalize_media_type(media_type)
        sanitized = self._sanitize(content, normalized_media_type)
        return self._persist_sanitized(
            sanitized,
            media_type=normalized_media_type,
            operation_id=operation_id,
        )

    def persist_exact_json(
        self,
        value: Any,
        *,
        operation_id: object,
        operation_binding: Mapping[str, Any] | None = None,
    ) -> ArtifactRef:
        """Persist control-plane JSON only when sanitization is byte-exact."""

        media_type = "application/json"
        canonical = _canonical_json_value(value)
        sanitized = self._sanitize(canonical, media_type)
        if sanitized != canonical:
            raise ArtifactIntegrityError(
                "control-plane JSON sanitizer changed protected bytes"
            )
        return self._persist_sanitized(
            canonical,
            media_type=media_type,
            operation_id=operation_id,
            operation_binding=operation_binding,
        )

    def _persist_json_value(
        self,
        value: Any,
        *,
        operation_id: object,
        operation_binding: Mapping[str, Any] | None = None,
    ) -> tuple[ArtifactRef, Any, bytes]:
        media_type = "application/json"
        canonical = _canonical_json_value(value)
        sanitized = self._sanitize(canonical, media_type)
        try:
            decoded = json.loads(sanitized.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ArtifactIntegrityError(
                "sanitized JSON artifact is not valid UTF-8 JSON"
            ) from exc
        normalized = _canonical_json_value(decoded)
        if normalized != sanitized:
            raise ArtifactIntegrityError(
                "sanitized JSON artifact is not in canonical form"
            )
        artifact = self._persist_sanitized(
            sanitized,
            media_type=media_type,
            operation_id=operation_id,
            operation_binding=operation_binding,
        )
        return artifact, decoded, sanitized

    def artifactize_user_message(
        self,
        message: Any,
        *,
        operation_id: object,
        operation_binding: Mapping[str, Any] | None = None,
    ) -> tuple[ArtifactRef, Any, bytes]:
        """Persist one user-message artifact through its provenance sanitizer."""

        media_type = "application/json"
        canonical = _canonical_json_value(message)
        if len(canonical) > MAX_ARTIFACT_BYTES:
            raise ArtifactTooLargeError("artifact exceeds the 32 MiB P0 limit")
        sanitized = self._user_message_sanitizer(canonical, media_type)
        if not isinstance(sanitized, bytes):
            raise TypeError("user-message artifact sanitizer must return bytes")
        if len(sanitized) > MAX_ARTIFACT_BYTES:
            raise ArtifactTooLargeError("artifact exceeds the 32 MiB P0 limit")
        try:
            decoded = json.loads(sanitized.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ArtifactIntegrityError(
                "sanitized user-message artifact is not valid UTF-8 JSON"
            ) from exc
        normalized = _canonical_json_value(decoded)
        if normalized != sanitized:
            raise ArtifactIntegrityError(
                "sanitized user-message artifact is not in canonical form"
            )
        artifact = self._persist_sanitized(
            sanitized,
            media_type=media_type,
            operation_id=operation_id,
            operation_binding=operation_binding,
        )
        return artifact, decoded, sanitized

    def artifactize_tool_result(
        self,
        result: Any,
        *,
        operation_id: object,
    ) -> ToolResultArtifactization:
        artifact, sanitized_result, content = self._persist_json_value(
            result,
            operation_id=operation_id,
            operation_binding={"kind": "tool_result_full_output"},
        )
        if len(content) <= MAX_INLINE_TOOL_RESULT_BYTES:
            visible_result = sanitized_result
        else:
            visible_result = {
                "preview": artifact.preview,
                "full_output_ref": artifact.ref.to_dict(),
                "content_bytes": artifact.byte_length,
                "content_sha256": artifact.sha256,
            }
        return ToolResultArtifactization(
            artifact=artifact,
            visible_result=visible_result,
            result_bytes=artifact.byte_length,
            result_sha256=artifact.sha256,
        )

    def artifactize_tool_completion(
        self,
        completion: Any,
        *,
        operation_id: object,
    ) -> ToolCompletionArtifactization:
        artifact, sanitized_completion, _content = self._persist_json_value(
            completion,
            operation_id=operation_id,
            operation_binding={"kind": "tool_completion"},
        )
        return ToolCompletionArtifactization(
            artifact=artifact,
            completion=sanitized_completion,
        )

    def verify_tool_result_artifactization(
        self,
        artifactization: ToolResultArtifactization,
    ) -> ToolResultArtifactization:
        """Rebuild model-visible output exclusively from verified artifact bytes."""

        if type(artifactization) is not ToolResultArtifactization:
            raise TypeError(
                "tool result verification requires ToolResultArtifactization"
            )
        artifact = artifactization.artifact
        content = self.read_full(
            artifact,
            remaining_budget_bytes=artifact.byte_length,
        )
        try:
            decoded = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ArtifactIntegrityError(
                "tool result artifact is not valid UTF-8 JSON"
            ) from exc
        if _canonical_json_value(decoded) != content:
            raise ArtifactIntegrityError(
                "tool result artifact is not canonical JSON"
            )
        expected_preview = _bounded_utf8_preview(content)
        if artifact.preview != expected_preview:
            raise ArtifactIntegrityError("tool result artifact preview changed")
        if artifact.byte_length <= MAX_INLINE_TOOL_RESULT_BYTES:
            visible_result = decoded
        else:
            visible_result = {
                "preview": expected_preview,
                "full_output_ref": artifact.ref.to_dict(),
                "content_bytes": artifact.byte_length,
                "content_sha256": artifact.sha256,
            }
        if _canonical_json_value(
            _thaw_json(artifactization.visible_result)
        ) != _canonical_json_value(visible_result):
            raise ArtifactIntegrityError(
                "tool result model-visible result disagrees with artifact"
            )
        return ToolResultArtifactization(
            artifact=artifact,
            visible_result=visible_result,
            result_bytes=artifact.byte_length,
            result_sha256=artifact.sha256,
        )

    def verify_tool_completion_artifactization(
        self,
        artifactization: ToolCompletionArtifactization,
    ) -> ToolCompletionArtifactization:
        """Rebuild a completion exclusively from its verified artifact bytes."""

        if type(artifactization) is not ToolCompletionArtifactization:
            raise TypeError(
                "tool completion verification requires "
                "ToolCompletionArtifactization"
            )
        artifact = artifactization.artifact
        content = self.read_full(
            artifact,
            remaining_budget_bytes=artifact.byte_length,
        )
        try:
            decoded = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ArtifactIntegrityError(
                "tool completion artifact is not valid UTF-8 JSON"
            ) from exc
        if _canonical_json_value(decoded) != content:
            raise ArtifactIntegrityError(
                "tool completion artifact is not canonical JSON"
            )
        expected_preview = _bounded_utf8_preview(content)
        if artifact.preview != expected_preview:
            raise ArtifactIntegrityError(
                "tool completion artifact preview changed"
            )
        if _canonical_json_value(
            _thaw_json(artifactization.completion)
        ) != _canonical_json_value(decoded):
            raise ArtifactIntegrityError(
                "tool completion value disagrees with artifact"
            )
        return ToolCompletionArtifactization(
            artifact=artifact,
            completion=decoded,
        )

    def read_page(
        self,
        artifact: ArtifactRef,
        *,
        offset: int = 0,
        limit: int = MAX_ARTIFACT_READ_BYTES,
    ) -> ArtifactReadPage:
        if not isinstance(artifact, ArtifactRef):
            artifact = ArtifactRef.from_dict(artifact)
        if artifact.ref.kind != "artifact" or artifact.ref.fragment:
            raise ArtifactIntegrityError("artifact ref is not a whole artifact")
        normalized_offset = _bounded_int(offset, "offset")
        normalized_limit = _bounded_int(limit, "limit", minimum=1)
        if normalized_limit > MAX_ARTIFACT_READ_BYTES:
            raise ArtifactBudgetError(
                f"artifact page limit exceeds {MAX_ARTIFACT_READ_BYTES} bytes"
            )
        if normalized_offset > artifact.byte_length:
            raise ArtifactBudgetError("artifact read offset exceeds content length")
        remaining = artifact.byte_length - normalized_offset
        requested = min(normalized_limit, remaining)
        try:
            data = self._repository.read_verified(
                artifact=artifact,
                offset=normalized_offset,
                limit=requested,
            )
        except ContextScopeError:
            raise
        except ContextRepositoryError as exc:
            raise ArtifactIntegrityError(str(exc)) from exc
        if not isinstance(data, bytes):
            raise ArtifactIntegrityError("artifact repository read did not return bytes")
        if len(data) != requested:
            raise ArtifactIntegrityError("artifact repository returned a short read")
        next_offset = normalized_offset + len(data)
        return ArtifactReadPage(
            artifact=artifact,
            offset=normalized_offset,
            data=data,
            next_offset=next_offset,
            has_more=next_offset < artifact.byte_length,
        )

    def read_full(
        self,
        artifact: ArtifactRef,
        *,
        remaining_budget_bytes: int,
    ) -> bytes:
        if not isinstance(artifact, ArtifactRef):
            artifact = ArtifactRef.from_dict(artifact)
        if artifact.ref.kind != "artifact" or artifact.ref.fragment:
            raise ArtifactIntegrityError("artifact ref is not a whole artifact")
        budget = _bounded_int(remaining_budget_bytes, "remaining_budget_bytes")
        if artifact.byte_length > MAX_ARTIFACT_BYTES:
            raise ArtifactTooLargeError("artifact exceeds the 32 MiB P0 limit")
        if artifact.byte_length > budget:
            raise ArtifactBudgetError(
                "artifact full read exceeds the remaining budget"
            )
        try:
            content = self._repository.read_full_verified(artifact=artifact)
        except ContextScopeError:
            raise
        except ContextRepositoryError as exc:
            raise ArtifactIntegrityError(str(exc)) from exc
        if not isinstance(content, bytes):
            raise ArtifactIntegrityError(
                "artifact repository full read did not return bytes"
            )
        if len(content) != artifact.byte_length:
            raise ArtifactIntegrityError("artifact full read byte_length mismatch")
        if hashlib.sha256(content).hexdigest() != artifact.sha256:
            raise ArtifactIntegrityError("artifact full read sha256 mismatch")
        return content


__all__ = [
    "MAX_ARTIFACT_BYTES",
    "MAX_ARTIFACT_READ_BYTES",
    "MAX_INLINE_TOOL_RESULT_BYTES",
    "MAX_PREVIEW_BYTES",
    "ArtifactBudgetError",
    "ArtifactIntegrityError",
    "ArtifactReadPage",
    "ArtifactService",
    "ArtifactServiceError",
    "ArtifactTooLargeError",
    "ContentSanitizer",
    "ToolResultArtifactization",
    "ToolCompletionArtifactization",
]

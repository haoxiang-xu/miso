from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar

from unchain.journal import ArtifactRef
from unchain.journal.models import _record_data, _required_text


MAX_CURRENT_INPUT_ATTACHMENTS = 32


@dataclass(frozen=True)
class HostResolvedAttachment:
    """A bounded host descriptor for bytes already owned by ArtifactService."""

    SCHEMA: ClassVar[str] = "unchain.host_resolved_attachment.v1"

    artifact: ArtifactRef
    kind: str
    name: str
    media_type: str

    def __post_init__(self) -> None:
        if not isinstance(self.artifact, ArtifactRef):
            object.__setattr__(
                self,
                "artifact",
                ArtifactRef.from_dict(self.artifact),
            )
        if self.artifact.ref.kind != "artifact" or self.artifact.ref.fragment:
            raise ValueError("attachment must reference one whole artifact")
        object.__setattr__(
            self,
            "kind",
            _required_text(self.kind, "attachment kind", maximum=64, identifier=True),
        )
        object.__setattr__(
            self,
            "name",
            _required_text(self.name, "attachment name", maximum=255),
        )
        media_type = _required_text(
            self.media_type,
            "attachment media_type",
            maximum=255,
        )
        if "/" not in media_type or media_type != self.artifact.media_type:
            raise ValueError("attachment media_type must match the artifact descriptor")
        object.__setattr__(self, "media_type", media_type)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.SCHEMA,
            "kind": self.kind,
            "name": self.name,
            "media_type": self.media_type,
            "artifact": self.artifact.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> HostResolvedAttachment:
        raw = _record_data(
            value,
            schema=cls.SCHEMA,
            required=frozenset({"artifact", "kind", "name", "media_type"}),
        )
        return cls(
            artifact=ArtifactRef.from_dict(raw["artifact"]),
            kind=raw["kind"],
            name=raw["name"],
            media_type=raw["media_type"],
        )


def normalize_host_resolved_attachments(
    values: Sequence[HostResolvedAttachment | Mapping[str, Any]] | None,
) -> tuple[HostResolvedAttachment, ...]:
    if values is None:
        return ()
    if not isinstance(values, Sequence) or isinstance(
        values,
        (str, bytes, bytearray),
    ):
        raise TypeError("attachments must be an array")
    if len(values) > MAX_CURRENT_INPUT_ATTACHMENTS:
        raise ValueError(
            f"attachments exceed the {MAX_CURRENT_INPUT_ATTACHMENTS}-item P0 limit"
        )
    attachments = tuple(
        value
        if isinstance(value, HostResolvedAttachment)
        else HostResolvedAttachment.from_dict(value)
        for value in values
    )
    refs = tuple(attachment.artifact.ref for attachment in attachments)
    if len(set(refs)) != len(refs):
        raise ValueError("duplicate attachment artifact ref")
    return attachments


__all__ = [
    "MAX_CURRENT_INPUT_ATTACHMENTS",
    "HostResolvedAttachment",
]

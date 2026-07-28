from __future__ import annotations

import hashlib
import json
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path


JOB_ENVIRONMENT_PROFILE_VERSION = 1


@dataclass(frozen=True)
class JobEnvironmentProfile:
    """Immutable execution environment shared by a supervisor and its jobs.

    Environment values may contain credentials, so they are intentionally
    excluded from ``repr`` and never written to the durable job store.  The
    store persists only ``digest``; a fresh supervisor must reconstruct the
    same profile before it can safely launch a queued job.
    """

    entries: tuple[tuple[str, str], ...] = field(repr=False)
    digest: str
    version: int = JOB_ENVIRONMENT_PROFILE_VERSION

    @classmethod
    def capture(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> "JobEnvironmentProfile":
        normalized = normalized_job_environment(environment)
        entries = tuple(sorted(normalized.items()))
        digest = _environment_digest(dict(entries))
        return cls(entries=entries, digest=digest)

    def __post_init__(self) -> None:
        if self.version != JOB_ENVIRONMENT_PROFILE_VERSION:
            raise ValueError("unsupported durable job environment profile version")
        environment = dict(self.entries)
        if len(environment) != len(self.entries):
            raise ValueError("job environment profile contains duplicate keys")
        normalized = normalized_job_environment(environment)
        canonical_entries = tuple(sorted(normalized.items()))
        if canonical_entries != self.entries:
            raise ValueError("job environment profile is not normalized")
        if _environment_digest(environment) != self.digest:
            raise ValueError("job environment profile digest does not match")

    def to_environment(self) -> dict[str, str]:
        """Return an isolated mapping suitable for ``subprocess.Popen``."""

        return dict(self.entries)


def normalized_job_environment(
    environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return the exact environment inherited by worker and child processes.

    The source checkout is placed first on ``PYTHONPATH`` so the detached
    wrapper loads the same Unchain package as its parent.  The returned mapping
    is also passed to the user child, making the profile hashed at approval
    time identical to the profile used at launch time.
    """

    source = os.environ if environment is None else environment
    normalized: dict[str, str] = {}
    for key, value in source.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise TypeError("job environment keys and values must be strings")
        if not key or "\0" in key or "=" in key or "\0" in value:
            raise ValueError("job environment contains an invalid key or value")
        # Windows environment names are case-insensitive.  Canonicalizing the
        # mapping prevents the digest from describing two keys that the child
        # process will collapse into one environment entry.
        normalized_key = key.upper() if os.name == "nt" else key
        previous = normalized.get(normalized_key)
        if previous is not None and previous != value:
            raise ValueError(
                "job environment contains conflicting case-insensitive keys"
            )
        normalized[normalized_key] = value

    existing = normalized.get("PYTHONPATH", "")
    entries = [
        resolved
        for item in existing.split(os.pathsep)
        if item
        for resolved in (str(Path(item).expanduser().resolve()),)
    ]
    if not bool(getattr(sys, "frozen", False)):
        source_root = str(Path(__file__).resolve().parents[2])
        entries = [entry for entry in entries if entry != source_root]
        entries.insert(0, source_root)
    normalized["PYTHONPATH"] = os.pathsep.join(entries)
    return normalized


def _environment_digest(normalized: Mapping[str, str]) -> str:
    encoded = json.dumps(
        {
            "profile_version": JOB_ENVIRONMENT_PROFILE_VERSION,
            "environment": normalized,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def job_environment_digest(environment: Mapping[str, str]) -> str:
    return _environment_digest(normalized_job_environment(environment))


__all__ = [
    "JOB_ENVIRONMENT_PROFILE_VERSION",
    "JobEnvironmentProfile",
    "job_environment_digest",
    "normalized_job_environment",
]

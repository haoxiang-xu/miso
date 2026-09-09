"""Bounded compact accounting projection for oversized RunBundle facts.

The v1 wire schema deliberately inlines every receipt and metric event.  That
is useful for small runs but it is not a safe envelope once a run has thousands
of provider calls.  This module defines the independent v2 projection: the
renderer-safe totals stay inline and the immutable receipt/event facts are
addressed by a strict details reference.

The details reference is not a lossy top-N summary.  It carries counts and
domain-separated set roots for every externalized partition.  A durable ledger
stores the corresponding facts and can hydrate them by ``details_id``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .run_bundle import (
    ProviderCallReceipt,
    ProviderCallUsage,
    RunBundle,
    RunChild,
    RunDescriptor,
    RunIdentity,
    RunLifecycle,
    RunMetricCounters,
    RunMetricEvent,
    RunCost,
    UsageCoverage,
)


COMPACT_RUN_BUNDLE_SCHEMA = "unchain.run_bundle.v2"
COMPACT_RUN_BUNDLE_DETAILS_SCHEMA = "unchain.run_bundle_details_ref.v1"
COMPACT_RUN_BUNDLE_MAX_CANONICAL_BYTES = 512 * 1024
COMPACT_RUN_BUNDLE_MAX_DETAILS_BYTES = 64 * 1024 * 1024
COMPACT_RUN_BUNDLE_FACTS_ALGORITHM = "run_bundle_facts_digest.v1"
_SHA256_HEX = set("0123456789abcdef")


class CompactRunBundleProtocolError(ValueError):
    """A compact envelope or details reference violated its closed schema."""


def _canonical_bytes(value: object, *, limit: int | None = None) -> bytes:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (RecursionError, TypeError, ValueError, UnicodeError) as exc:
        raise CompactRunBundleProtocolError("compact bundle must be canonical JSON") from exc
    if limit is not None and len(encoded) > limit:
        raise CompactRunBundleProtocolError("compact bundle exceeds the canonical byte limit")
    return encoded


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _digest_partition(name: str, values: Iterable[Mapping[str, Any]]) -> tuple[int, int, str]:
    encoded_items = tuple(_canonical_bytes(value) for value in values)
    payload = bytearray()
    domain = name.encode("utf-8")
    payload.extend(len(domain).to_bytes(4, "big"))
    payload.extend(domain)
    for encoded in encoded_items:
        payload.extend(len(encoded).to_bytes(8, "big"))
        payload.extend(encoded)
    raw = bytes(payload)
    if len(raw) > COMPACT_RUN_BUNDLE_MAX_DETAILS_BYTES:
        raise CompactRunBundleProtocolError("compact bundle details exceed the durable limit")
    return len(encoded_items), len(raw), _sha256(raw)


def _safe_digest(value: object, field: str) -> str:
    if type(value) is not str or len(value) != 64 or any(ch not in _SHA256_HEX for ch in value):
        raise CompactRunBundleProtocolError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _count(value: object, field: str) -> int:
    if type(value) is not int or value < 0:
        raise CompactRunBundleProtocolError(f"{field} must be a non-negative integer")
    return value


@dataclass(frozen=True, slots=True)
class CompactRunBundleDetailsRef:
    details_id: str
    facts_digest: str
    total_bytes: int
    parts: tuple[dict[str, Any], ...]

    def __post_init__(self) -> None:
        if type(self.details_id) is not str or not self.details_id.startswith("rbd_"):
            raise CompactRunBundleProtocolError("details_id is invalid")
        object.__setattr__(self, "facts_digest", _safe_digest(self.facts_digest, "facts_digest"))
        object.__setattr__(self, "total_bytes", _count(self.total_bytes, "total_bytes"))
        if self.total_bytes > COMPACT_RUN_BUNDLE_MAX_DETAILS_BYTES:
            raise CompactRunBundleProtocolError("details total bytes exceed the durable limit")
        parts = tuple(self.parts)
        if not parts or len(parts) > 8:
            raise CompactRunBundleProtocolError("details parts are invalid")
        allowed = {"name", "item_count", "canonical_bytes", "root_sha256"}
        normalized: list[dict[str, Any]] = []
        names: set[str] = set()
        for part in parts:
            if type(part) is not dict or set(part) != allowed:
                raise CompactRunBundleProtocolError("details part shape is invalid")
            name = part["name"]
            if type(name) is not str or not name or name in names:
                raise CompactRunBundleProtocolError("details part name is invalid")
            names.add(name)
            normalized.append(
                {
                    "name": name,
                    "item_count": _count(part["item_count"], "part.item_count"),
                    "canonical_bytes": _count(part["canonical_bytes"], "part.canonical_bytes"),
                    "root_sha256": _safe_digest(part["root_sha256"], "part.root_sha256"),
                }
            )
        object.__setattr__(self, "parts", tuple(normalized))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": COMPACT_RUN_BUNDLE_DETAILS_SCHEMA,
            "details_id": self.details_id,
            "facts_digest": self.facts_digest,
            "total_bytes": self.total_bytes,
            "parts": [dict(part) for part in self.parts],
        }

    @classmethod
    def from_dict(cls, value: object) -> "CompactRunBundleDetailsRef":
        if type(value) is not dict:
            raise CompactRunBundleProtocolError("details_ref must be an object")
        if set(value) != {"schema", "details_id", "facts_digest", "total_bytes", "parts"}:
            raise CompactRunBundleProtocolError("details_ref has unknown fields")
        if value["schema"] != COMPACT_RUN_BUNDLE_DETAILS_SCHEMA or type(value["parts"]) is not list:
            raise CompactRunBundleProtocolError("details_ref schema is invalid")
        return cls(
            details_id=value["details_id"],
            facts_digest=value["facts_digest"],
            total_bytes=value["total_bytes"],
            parts=tuple(value["parts"]),
        )


def _summary_usage(receipts: tuple[ProviderCallReceipt, ...]) -> dict[str, Any]:
    return ProviderCallUsage.sum(receipt.usage for receipt in receipts).to_dict()


def _summary_coverage(receipts: tuple[ProviderCallReceipt, ...]) -> dict[str, Any]:
    coverage = UsageCoverage.from_receipts(receipts).to_dict()
    missing = coverage.pop("missing_usage_call_ids", [])
    coverage["missing_usage_call_ids"] = {
        "count": len(missing),
        "set_sha256": _sha256(_canonical_bytes(sorted(missing))),
    }
    return coverage


def _summary_cost(receipts: tuple[ProviderCallReceipt, ...]) -> dict[str, Any]:
    cost = RunCost.from_receipts(receipts).to_dict()
    snapshot_ids = cost.pop("pricing_snapshot_ids", [])
    cost["pricing_snapshot_ids"] = {
        "count": len(snapshot_ids),
        "set_sha256": _sha256(_canonical_bytes(sorted(snapshot_ids))),
    }
    return cost


def _summary_evidence(receipts: tuple[ProviderCallReceipt, ...]) -> dict[str, Any]:
    receipt_ids = sorted(receipt.receipt_sha256 for receipt in receipts)
    raw_ids = sorted(
        receipt.raw_usage_sha256
        for receipt in receipts
        if receipt.raw_usage_sha256 is not None
    )
    pricing_ids = sorted(
        receipt.pricing.snapshot.snapshot_id
        for receipt in receipts
        if receipt.pricing.snapshot is not None
    )
    return {
        "receipt_sha256s": {"count": len(receipt_ids), "set_sha256": _sha256(_canonical_bytes(receipt_ids))},
        "raw_usage_sha256s": {"count": len(raw_ids), "set_sha256": _sha256(_canonical_bytes(raw_ids))},
        "pricing_snapshot_ids": {"count": len(pricing_ids), "set_sha256": _sha256(_canonical_bytes(pricing_ids))},
    }


@dataclass(frozen=True, slots=True)
class CompactRunBundle:
    identity: RunIdentity
    lifecycle: RunLifecycle
    descriptor: RunDescriptor
    revision: int
    provider_call_count: int
    direct_provider_call_count: int
    descendant_provider_call_count: int
    aggregation_usage: dict[str, Any]
    direct_usage: dict[str, Any]
    descendant_usage: dict[str, Any]
    metrics: dict[str, Any]
    coverage: dict[str, Any]
    cost: dict[str, Any]
    legacy: dict[str, Any]
    evidence: dict[str, Any]
    children: dict[str, Any]
    details_ref: CompactRunBundleDetailsRef
    extensions: Mapping[str, Any]
    bundle_id: str
    bundle_digest: str

    def __post_init__(self) -> None:
        if type(self.identity) is not RunIdentity or type(self.lifecycle) is not RunLifecycle or type(self.descriptor) is not RunDescriptor:
            raise TypeError("compact bundle identity/lifecycle/descriptor types are invalid")
        if type(self.revision) is not int or self.revision <= 0:
            raise CompactRunBundleProtocolError("revision must be positive")
        for field_name in ("provider_call_count", "direct_provider_call_count", "descendant_provider_call_count"):
            _count(getattr(self, field_name), field_name)
        if self.direct_provider_call_count + self.descendant_provider_call_count != self.provider_call_count:
            raise CompactRunBundleProtocolError("provider call counts do not partition the total")
        if type(self.details_ref) is not CompactRunBundleDetailsRef:
            raise TypeError("details_ref must be an exact CompactRunBundleDetailsRef")
        if type(self.extensions) is not dict:
            raise TypeError("extensions must be an exact object")
        if type(self.bundle_id) is not str or not self.bundle_id:
            raise CompactRunBundleProtocolError("bundle_id is invalid")
        _safe_digest(self.bundle_digest, "bundle_digest")
        body = self._body_dict()
        canonical = _canonical_bytes(
            body,
            limit=COMPACT_RUN_BUNDLE_MAX_CANONICAL_BYTES,
        )
        if _sha256(canonical) != self.bundle_digest:
            raise CompactRunBundleProtocolError("bundle_digest does not match compact body")
        _canonical_bytes(
            {**body, "bundle_digest": self.bundle_digest},
            limit=COMPACT_RUN_BUNDLE_MAX_CANONICAL_BYTES,
        )

    def _body_dict(self) -> dict[str, Any]:
        return {
            "schema": COMPACT_RUN_BUNDLE_SCHEMA,
            "bundle_id": self.bundle_id,
            "revision": self.revision,
            "identity": self.identity.to_dict(),
            "lifecycle": self.lifecycle.to_dict(),
            "descriptor": self.descriptor.to_dict(),
            "provider_call_count": self.provider_call_count,
            "direct_provider_call_count": self.direct_provider_call_count,
            "descendant_provider_call_count": self.descendant_provider_call_count,
            "aggregation_usage": self.aggregation_usage,
            "direct_usage": self.direct_usage,
            "descendant_usage": self.descendant_usage,
            "metrics": self.metrics,
            "coverage": self.coverage,
            "cost": self.cost,
            "legacy": self.legacy,
            "evidence": self.evidence,
            "children": self.children,
            "details_ref": self.details_ref.to_dict(),
            "extensions": dict(self.extensions),
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._body_dict(), "bundle_digest": self.bundle_digest}

    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.to_dict(), limit=COMPACT_RUN_BUNDLE_MAX_CANONICAL_BYTES)

    def verify_details(
        self,
        details: object,
    ) -> dict[str, list[dict[str, Any]]]:
        """Verify hydrated facts against every immutable details_ref binding."""

        if type(details) is not dict or set(details) != {
            "provider_calls",
            "metric_events",
            "children",
        }:
            raise CompactRunBundleProtocolError("compact details shape is invalid")
        normalized: dict[str, list[dict[str, Any]]] = {}
        parts: list[dict[str, Any]] = []
        for name in ("provider_calls", "metric_events", "children"):
            items = details[name]
            if type(items) is not list or any(type(item) is not dict for item in items):
                raise CompactRunBundleProtocolError(
                    "compact details partitions are invalid"
                )
            normalized[name] = [dict(item) for item in items]
            count, size, root = _digest_partition(name, normalized[name])
            parts.append(
                {
                    "name": name,
                    "item_count": count,
                    "canonical_bytes": size,
                    "root_sha256": root,
                }
            )
        if tuple(parts) != self.details_ref.parts:
            raise CompactRunBundleProtocolError(
                "compact details partitions do not match details_ref"
            )
        if sum(part["canonical_bytes"] for part in parts) != self.details_ref.total_bytes:
            raise CompactRunBundleProtocolError(
                "compact details byte count does not match details_ref"
            )
        facts_body = {
            "algorithm": COMPACT_RUN_BUNDLE_FACTS_ALGORITHM,
            "identity": self.identity.to_dict(),
            "lifecycle": self.lifecycle.to_dict(),
            "descriptor": self.descriptor.to_dict(),
            "revision": self.revision,
            "parts": parts,
        }
        facts_digest = _sha256(_canonical_bytes(facts_body))
        if (
            facts_digest != self.details_ref.facts_digest
            or self.details_ref.details_id != f"rbd_{facts_digest}"
        ):
            raise CompactRunBundleProtocolError(
                "compact details facts digest does not match details_ref"
            )
        return normalized

    @classmethod
    def from_dict(cls, value: object) -> "CompactRunBundle":
        if type(value) is not dict:
            raise CompactRunBundleProtocolError("compact bundle must be an object")
        keys = {
            "schema", "bundle_id", "revision", "bundle_digest", "identity", "lifecycle", "descriptor",
            "provider_call_count", "direct_provider_call_count", "descendant_provider_call_count",
            "aggregation_usage", "direct_usage", "descendant_usage", "metrics", "coverage", "cost",
            "legacy", "evidence", "children", "details_ref", "extensions",
        }
        if set(value) != keys or value.get("schema") != COMPACT_RUN_BUNDLE_SCHEMA:
            raise CompactRunBundleProtocolError("compact bundle schema is invalid")
        for field_name in ("aggregation_usage", "direct_usage", "descendant_usage", "metrics", "coverage", "cost", "legacy", "evidence", "children", "extensions"):
            if type(value[field_name]) is not dict:
                raise CompactRunBundleProtocolError(f"{field_name} must be an object")
        return cls(
            identity=RunIdentity.from_dict(value["identity"]),
            lifecycle=RunLifecycle.from_dict(value["lifecycle"]),
            descriptor=RunDescriptor.from_dict(value["descriptor"]),
            revision=value["revision"],
            provider_call_count=value["provider_call_count"],
            direct_provider_call_count=value["direct_provider_call_count"],
            descendant_provider_call_count=value["descendant_provider_call_count"],
            aggregation_usage=value["aggregation_usage"],
            direct_usage=value["direct_usage"],
            descendant_usage=value["descendant_usage"],
            metrics=value["metrics"],
            coverage=value["coverage"],
            cost=value["cost"],
            legacy=value["legacy"],
            evidence=value["evidence"],
            children=value["children"],
            details_ref=CompactRunBundleDetailsRef.from_dict(value["details_ref"]),
            extensions=value["extensions"],
            bundle_id=value["bundle_id"],
            bundle_digest=value["bundle_digest"],
        )

    @classmethod
    def from_facts(
        cls,
        *,
        identity: RunIdentity,
        lifecycle: RunLifecycle,
        descriptor: RunDescriptor,
        revision: int,
        receipts: Iterable[ProviderCallReceipt],
        metric_events: Iterable[RunMetricEvent],
        children: Iterable[RunChild],
        extensions: Mapping[str, Any] | None = None,
    ) -> tuple["CompactRunBundle", dict[str, list[dict[str, Any]]]]:
        ordered_receipts = tuple(sorted(receipts, key=lambda item: item.provider_call_id))
        ordered_events = tuple(sorted(metric_events, key=lambda item: item.metric_event_id))
        ordered_children = tuple(sorted(children, key=lambda item: item.run_id))
        if any(type(item) is not ProviderCallReceipt for item in ordered_receipts):
            raise TypeError("receipts must contain exact ProviderCallReceipt values")
        if any(type(item) is not RunMetricEvent for item in ordered_events):
            raise TypeError("metric_events must contain exact RunMetricEvent values")
        if any(type(item) is not RunChild for item in ordered_children):
            raise TypeError("children must contain exact RunChild values")
        direct = tuple(item for item in ordered_receipts if item.identity.owner_run_id == identity.run_id)
        descendant = tuple(item for item in ordered_receipts if item.identity.owner_run_id != identity.run_id)
        direct_events = tuple(item for item in ordered_events if item.owner_run_id == identity.run_id)
        descendant_events = tuple(item for item in ordered_events if item.owner_run_id != identity.run_id)
        receipt_items = [item.to_dict() for item in ordered_receipts]
        event_items = [item.to_dict() for item in ordered_events]
        child_items = [item.to_dict() for item in ordered_children]
        parts = []
        for name, items in (("provider_calls", receipt_items), ("metric_events", event_items), ("children", child_items)):
            count, size, root = _digest_partition(name, items)
            parts.append({"name": name, "item_count": count, "canonical_bytes": size, "root_sha256": root})
        facts_body = {
            "algorithm": COMPACT_RUN_BUNDLE_FACTS_ALGORITHM,
            "identity": identity.to_dict(),
            "lifecycle": lifecycle.to_dict(),
            "descriptor": descriptor.to_dict(),
            "revision": revision,
            "parts": parts,
        }
        facts_digest = _sha256(_canonical_bytes(facts_body))
        details_ref = CompactRunBundleDetailsRef(
            details_id=f"rbd_{facts_digest}",
            facts_digest=facts_digest,
            total_bytes=sum(part["canonical_bytes"] for part in parts),
            parts=tuple(parts),
        )
        children_summary = {
            "count": len(ordered_children),
            "set_sha256": _sha256(_canonical_bytes(child_items)),
        }
        metrics = {
            "algorithm": "unique_metric_event_set_union.v1",
            "event_count": len(ordered_events),
            "event_set_sha256": _sha256(_canonical_bytes(event_items)),
            "direct": RunMetricCounters.from_events(direct_events).to_dict(),
            "descendant": RunMetricCounters.from_events(descendant_events).to_dict(),
            "all": RunMetricCounters.from_events(ordered_events).to_dict(),
        }
        body = {
            "schema": COMPACT_RUN_BUNDLE_SCHEMA,
            "bundle_id": "",
            "revision": revision,
            "identity": identity.to_dict(),
            "lifecycle": lifecycle.to_dict(),
            "descriptor": descriptor.to_dict(),
            "provider_call_count": len(ordered_receipts),
            "direct_provider_call_count": len(direct),
            "descendant_provider_call_count": len(descendant),
            "aggregation_usage": _summary_usage(ordered_receipts),
            "direct_usage": _summary_usage(direct),
            "descendant_usage": _summary_usage(descendant),
            "metrics": metrics,
            "coverage": _summary_coverage(ordered_receipts),
            "cost": _summary_cost(ordered_receipts),
            "legacy": {"status": "legacy_partial" if any(item.usage.source == "legacy_partial" for item in ordered_receipts) else "canonical"},
            "evidence": _summary_evidence(ordered_receipts),
            "children": children_summary,
            "details_ref": details_ref.to_dict(),
            "extensions": dict(extensions or {}),
        }
        from .run_bundle import deterministic_bundle_id

        body["bundle_id"] = deterministic_bundle_id(identity=identity)
        digest = _sha256(_canonical_bytes(body, limit=COMPACT_RUN_BUNDLE_MAX_CANONICAL_BYTES))
        return (
            cls(
                identity=identity,
                lifecycle=lifecycle,
                descriptor=descriptor,
                revision=revision,
                provider_call_count=len(ordered_receipts),
                direct_provider_call_count=len(direct),
                descendant_provider_call_count=len(descendant),
                aggregation_usage=body["aggregation_usage"],
                direct_usage=body["direct_usage"],
                descendant_usage=body["descendant_usage"],
                metrics=metrics,
                coverage=body["coverage"],
                cost=body["cost"],
                legacy=body["legacy"],
                evidence=body["evidence"],
                children=children_summary,
                details_ref=details_ref,
                extensions=dict(extensions or {}),
                bundle_id=body["bundle_id"],
                bundle_digest=digest,
            ),
            {"provider_calls": receipt_items, "metric_events": event_items, "children": child_items},
        )


def run_bundle_from_dict(value: object) -> RunBundle | CompactRunBundle:
    """Parse exactly one supported RunBundle wire without schema fallback."""

    if type(value) is not dict:
        raise CompactRunBundleProtocolError("run bundle must be an object")
    if value.get("schema") == COMPACT_RUN_BUNDLE_SCHEMA:
        return CompactRunBundle.from_dict(value)
    return RunBundle.from_dict(value)


__all__ = [
    "COMPACT_RUN_BUNDLE_DETAILS_SCHEMA",
    "COMPACT_RUN_BUNDLE_FACTS_ALGORITHM",
    "COMPACT_RUN_BUNDLE_MAX_CANONICAL_BYTES",
    "COMPACT_RUN_BUNDLE_MAX_DETAILS_BYTES",
    "COMPACT_RUN_BUNDLE_SCHEMA",
    "CompactRunBundle",
    "CompactRunBundleDetailsRef",
    "CompactRunBundleProtocolError",
    "run_bundle_from_dict",
]

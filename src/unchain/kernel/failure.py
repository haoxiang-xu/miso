"""Content-free carrier for a failed kernel run's canonical accounting."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..run_bundle import RunBundle
from ..run_bundle_v2 import CompactRunBundle


_CODE_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
_FAILURE_ATTRIBUTE = "_unchain_kernel_run_failure_v1"


@dataclass(frozen=True, slots=True)
class KernelRunFailureRecord:
    """Safe terminal facts attached to the original raised exception."""

    error_category: str
    error_code: str
    run_bundle: RunBundle | CompactRunBundle

    def __post_init__(self) -> None:
        if _CODE_RE.fullmatch(self.error_category) is None:
            raise ValueError("error_category must be a stable lowercase code")
        if _CODE_RE.fullmatch(self.error_code) is None:
            raise ValueError("error_code must be a stable lowercase code")
        if type(self.run_bundle) not in {RunBundle, CompactRunBundle}:
            raise TypeError("run_bundle must be an exact v1 or v2 RunBundle")
        if self.run_bundle.lifecycle.status != "failed":
            raise ValueError("kernel failure record requires a failed RunBundle")


def attach_kernel_run_failure(
    error: Exception,
    *,
    error_category: str,
    error_code: str,
    run_bundle: RunBundle | CompactRunBundle,
) -> KernelRunFailureRecord:
    """Attach canonical safe evidence without changing the exception type."""

    if not isinstance(error, Exception):
        raise TypeError("error must be an Exception")
    record = KernelRunFailureRecord(
        error_category=error_category,
        error_code=error_code,
        run_bundle=run_bundle,
    )
    prior = getattr(error, _FAILURE_ATTRIBUTE, None)
    if prior is not None:
        if type(prior) is not KernelRunFailureRecord:
            raise RuntimeError("kernel exception failure accounting was corrupted")
        if prior == record:
            return prior
        # The same durable exception may cross child and root run boundaries.
        # Preserve the outermost/root projection for the public error carrier;
        # a descendant projection must never replace an existing root record.
        prior_is_root = prior.run_bundle.identity.parent_run_id is None
        record_is_root = record.run_bundle.identity.parent_run_id is None
        if prior_is_root or not record_is_root:
            return prior
    try:
        setattr(error, _FAILURE_ATTRIBUTE, record)
    except (AttributeError, TypeError) as exc:
        raise RuntimeError(
            "kernel exception cannot carry canonical failure accounting"
        ) from exc
    return record


def kernel_run_failure_from_exception(
    error: BaseException,
) -> KernelRunFailureRecord | None:
    """Return only the typed safe carrier; never expose exception text/stack."""

    record = getattr(error, _FAILURE_ATTRIBUTE, None)
    if record is None:
        return None
    if type(record) is not KernelRunFailureRecord:
        raise RuntimeError("kernel exception failure accounting was corrupted")
    return record


__all__ = [
    "KernelRunFailureRecord",
    "attach_kernel_run_failure",
    "kernel_run_failure_from_exception",
]

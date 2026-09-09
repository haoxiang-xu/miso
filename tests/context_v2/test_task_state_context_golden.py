from __future__ import annotations

import hashlib
import json
from pathlib import Path

from unchain.context import ContextCompileRequest, ContextCompiler


FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "task_state_context"
    / "legacy_overflow.json"
)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def test_legacy_task_state_overflow_matches_exact_content_free_golden_bytes() -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    result = ContextCompiler().compile(
        ContextCompileRequest.from_dict(fixture["input"])
    ).to_dict()
    actual = _canonical(result)

    assert actual == fixture["expected_result_json"].encode("utf-8")
    assert hashlib.sha256(actual).hexdigest() == fixture["expected_sha256"]
    assert fixture["forbidden_content"] not in actual.decode("utf-8")

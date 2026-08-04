from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from unchain.context import ContextCompileRequest, ContextCompiler


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "compiler_p0"


def _canonical_messages_diagnostics(request: dict) -> bytes:
    result = ContextCompiler().compile(
        ContextCompileRequest.from_dict(request)
    ).to_dict()
    return json.dumps(
        {
            "messages": result["messages"],
            "diagnostics": result["diagnostics"],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def test_compiler_p0_manifest_is_branch_local_and_content_addressed() -> None:
    manifest = json.loads(
        (FIXTURE_ROOT / "manifest.json").read_text(encoding="utf-8")
    )

    assert manifest["schema"] == "unchain.context_v2.compiler_golden_manifest.v1"
    assert manifest["provenance"]["kind"] == "unchain_branch_local_compiler_freeze"
    assert manifest["provenance"]["cross_repo_equivalence_claim"] is False
    assert manifest["comparison"] == {
        "scope": ["messages", "diagnostics"],
        "encoding": "canonical_json_utf8",
        "exact_bytes": True,
    }
    assert [item["case"] for item in manifest["fixtures"]] == [
        "core_below_pressure",
        "core_over_pressure",
        "core_cross_provider",
        "core_recorded_refs",
    ]
    for item in manifest["fixtures"]:
        raw = (FIXTURE_ROOT / item["file"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == item["sha256"]
    for item in manifest["host_boundary_partitions"]:
        raw = (FIXTURE_ROOT / item["file"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == item["sha256"]


def test_compiler_owned_messages_and_diagnostics_match_exact_frozen_bytes() -> None:
    for fixture_name in (
        "below_pressure.json",
        "over_pressure.json",
        "recorded_refs.json",
    ):
        fixture = json.loads(
            (FIXTURE_ROOT / fixture_name).read_text(encoding="utf-8")
        )
        assert _canonical_messages_diagnostics(fixture["input"]) == fixture[
            "expected_messages_diagnostics_json"
        ].encode("utf-8")


def test_cross_provider_compiler_outputs_match_exact_frozen_bytes() -> None:
    fixture = json.loads(
        (FIXTURE_ROOT / "cross_provider.json").read_text(encoding="utf-8")
    )
    compiled_messages: list[bytes] = []
    for variant in fixture["variants"]:
        request = copy.deepcopy(fixture["base_input"])
        request["provider"] = variant["provider"]
        request["model"] = variant["model"]
        result = ContextCompiler().compile(
            ContextCompileRequest.from_dict(request)
        ).to_dict()
        actual_messages = json.dumps(
            result["messages"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        actual_diagnostics = json.dumps(
            result["diagnostics"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        assert actual_messages == fixture["expected_messages_json"].encode(
            "utf-8"
        )
        assert actual_diagnostics == variant["expected_diagnostics_json"].encode(
            "utf-8"
        )
        compiled_messages.append(actual_messages)

    assert len(set(compiled_messages)) == 1
    neutral_payload = json.loads(
        json.loads(compiled_messages[0])[1]["content"].split("\n", 2)[2]
    )
    assert neutral_payload["tool_exchanges"][0]["call_id"] == "call-1"
    assert not any(
        message.get("type") in {"function_call", "function_call_output"}
        or message.get("tool_calls")
        for message in json.loads(compiled_messages[0])
    )

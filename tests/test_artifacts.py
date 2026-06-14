from __future__ import annotations

import hashlib

from unchain import artifacts
from unchain.artifacts import ArtifactCaps, ArtifactOwner, canonicalize_artifacts


def _owner() -> ArtifactOwner:
    return ArtifactOwner(
        run_id="run-1",
        turn_id="run-1:turn-0",
        tool_name="report_tool",
        call_id="call-1",
    )


def test_markdown_helper_normalizes_to_canonical_snapshot():
    [artifact] = canonicalize_artifacts(
        [
            artifacts.markdown(
                "Benchmark report",
                "# Report\nLooks good.",
                source_path="reports/latest.md",
                artifact_id="benchmark-report",
            )
        ],
        owner=_owner(),
    )

    assert artifact["schema_version"] == "unchain.artifact.v1"
    assert artifact["artifact_id"] == "benchmark-report"
    assert artifact["kind"] == "markdown"
    assert artifact["title"] == "Benchmark report"
    assert artifact["owner"] == {
        "run_id": "run-1",
        "turn_id": "run-1:turn-0",
        "tool_name": "report_tool",
        "call_id": "call-1",
    }
    assert artifact["snapshot"]["markdown"] == "# Report\nLooks good."
    assert artifact["snapshot"]["sha256"] == hashlib.sha256(b"# Report\nLooks good.").hexdigest()
    assert artifact["snapshot"]["truncated"] is False
    assert artifact["source"] == {"path": "reports/latest.md"}
    assert artifact["presentation"] == {
        "surface": "iteration_summary",
        "group": "report_tool",
        "collapsed": True,
    }


def test_table_kv_log_and_link_helpers_normalize_from_simple_payloads():
    canonical = canonicalize_artifacts(
        [
            artifacts.table("Results", ["case", "status"], [{"case": "a", "status": "pass"}]),
            artifacts.kv("Metadata", {"branch": "dev", "count": 2}),
            artifacts.log("Install log", "ok\nready"),
            artifacts.link("Workspace file", path="plans/plan_1.md", artifact_id="plan-link"),
        ],
        owner=_owner(),
    )

    assert [item["kind"] for item in canonical] == ["table", "kv", "log", "link"]
    assert canonical[0]["snapshot"]["columns"] == ["case", "status"]
    assert canonical[0]["snapshot"]["rows"] == [{"case": "a", "status": "pass"}]
    assert canonical[1]["snapshot"]["pairs"] == {"branch": "dev", "count": 2}
    assert canonical[2]["snapshot"]["text"] == "ok\nready"
    assert canonical[3]["artifact_id"] == "plan-link"
    assert canonical[3]["source"] == {"path": "plans/plan_1.md"}


def test_text_snapshots_are_truncated_with_full_content_hash():
    text = "\n".join(f"line {i}" for i in range(405))
    [artifact] = canonicalize_artifacts(
        [artifacts.markdown("Long report", text)],
        owner=_owner(),
    )

    snapshot = artifact["snapshot"]
    assert snapshot["truncated"] is True
    assert snapshot["total_lines"] == 405
    assert snapshot["displayed_lines"] == 400
    assert snapshot["markdown"].splitlines() == [f"line {i}" for i in range(400)]
    assert snapshot["sha256"] == hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert snapshot["total_bytes"] > snapshot["displayed_bytes"]


def test_byte_cap_truncates_text_snapshot_with_full_content_hash():
    text = "0123456789ABCDE"
    [artifact] = canonicalize_artifacts(
        [artifacts.log("Long log", text)],
        owner=_owner(),
        caps=ArtifactCaps(max_lines=400, max_bytes=10),
    )

    snapshot = artifact["snapshot"]
    assert snapshot["text"] == "0123456789"
    assert snapshot["truncated"] is True
    assert snapshot["total_bytes"] == 15
    assert snapshot["displayed_bytes"] == 10
    assert snapshot["sha256"] == hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_byte_cap_truncates_structured_table_rows():
    [artifact] = canonicalize_artifacts(
        [
            artifacts.table(
                "Large table",
                ["value"],
                [{"value": "x" * 80}, {"value": "y" * 80}],
            )
        ],
        owner=_owner(),
        caps=ArtifactCaps(max_lines=400, max_bytes=70),
    )

    snapshot = artifact["snapshot"]
    assert snapshot["rows"] == []
    assert snapshot["truncated"] is True
    assert snapshot["total_bytes"] > snapshot["displayed_bytes"]


def test_file_diff_snapshot_hashes_full_diff_when_display_is_pretruncated():
    full_diff = "\n".join(f"+line {index}" for index in range(500)) + "\n"
    preview_diff = "\n".join(f"+line {index}" for index in range(10)) + "\n"
    [artifact] = canonicalize_artifacts(
        [
            artifacts.file_diff(
                "Large diff",
                [
                    {
                        "path": "app.py",
                        "operation": "edit",
                        "unified_diff_full": full_diff,
                        "unified_diff": preview_diff,
                    }
                ],
                artifact_id="diff-1",
            )
        ],
        owner=_owner(),
    )

    snapshot = artifact["snapshot"]
    file_snapshot = snapshot["files"][0]
    assert snapshot["truncated"] is True
    assert snapshot["total_lines"] == 500
    assert snapshot["displayed_lines"] == 400
    assert snapshot["sha256"] == hashlib.sha256(full_diff.encode("utf-8")).hexdigest()
    assert file_snapshot["truncated"] is True
    assert file_snapshot["total_lines"] == 500
    assert file_snapshot["displayed_lines"] == 400
    assert file_snapshot["unified_diff"] != full_diff
    assert file_snapshot["unified_diff"] != preview_diff


def test_invalid_artifact_descriptors_are_dropped():
    assert canonicalize_artifacts(
        [
            {"kind": "html", "title": "Nope", "html": "<b>unsafe</b>"},
            {"kind": "markdown", "markdown": "missing title"},
            "not a descriptor",
        ],
        owner=_owner(),
    ) == []

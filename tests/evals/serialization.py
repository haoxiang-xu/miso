from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from .types import JudgeReport, RunArtifact, to_jsonable


def ensure_directory(path: str | Path) -> Path:
    directory = Path(path)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def write_json(path: str | Path, payload: Any) -> Path:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(to_jsonable(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return output_path


def write_jsonl(path: str | Path, rows: list[dict[str, Any]]) -> Path:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(json.dumps(to_jsonable(row), ensure_ascii=False, sort_keys=True) for row in rows)
    output_path.write_text((content + "\n") if content else "", encoding="utf-8")
    return output_path


def write_leaderboard_csv(path: str | Path, rows: list[dict[str, Any]]) -> Path:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    for row in rows:
        for key in row.keys():
            if key not in fieldnames:
                fieldnames.append(key)

    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})
    return output_path


def persist_run_artifact(path: str | Path, run_artifact: RunArtifact) -> Path:
    return write_json(path, run_artifact)


def persist_judge_report(path: str | Path, judge_report: JudgeReport) -> Path:
    return write_json(path, judge_report)

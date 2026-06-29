#!/usr/bin/env python
"""Summarize raw Data Transfer download progress without modifying raw files."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from fink_lsst.bulk_transfer.raw_audit import build_raw_audit
from fink_lsst.bulk_transfer.raw_readiness import classify_raw_readiness
from fink_lsst.bulk_transfer.topic_registry import find_topic_entry, load_topic_registry


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", required=True)
    parser.add_argument("--topic")
    parser.add_argument("--registry", default="configs/data_transfer_topics.yaml")
    parser.add_argument("--expected-total", type=int)
    parser.add_argument("--progress-from-terminal", type=int)
    args = parser.parse_args()
    registry = load_topic_registry(PROJECT_ROOT / args.registry)
    entry = find_topic_entry(registry, topic=args.topic) if args.topic else None
    summary = summarize_progress(Path(args.raw_dir), entry=entry, expected_total=args.expected_total, progress_from_terminal=args.progress_from_terminal)
    print(render_summary(summary))
    if entry:
        output_base = PROJECT_ROOT / "outputs/data_transfer/full_week_full_packet" / f"{entry['utc_start']}_to_{entry['utc_stop']}"
        output_base.mkdir(parents=True, exist_ok=True)
        (output_base / "download_progress.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (output_base / "DOWNLOAD_PROGRESS.md").write_text(render_summary(summary), encoding="utf-8")
    return 0


def summarize_progress(raw_dir: Path, entry: dict[str, Any] | None = None, expected_total: int | None = None, progress_from_terminal: int | None = None) -> dict[str, Any]:
    raw_dir = raw_dir if raw_dir.is_absolute() else PROJECT_ROOT / raw_dir
    if not raw_dir.exists():
        return {"status": "raw_delivery_missing", "raw_dir": str(raw_dir), "topic": (entry or {}).get("topic"), "suggested_next_command": "Run the printed fink_datatransfer command manually after preflight passes."}
    audit = build_raw_audit(raw_dir)
    readiness = classify_raw_readiness(audit, expected_total=expected_total, progress_from_terminal=progress_from_terminal)
    return {
        "status": "raw_delivery_present",
        "topic": (entry or {}).get("topic"),
        "raw_dir": str(raw_dir),
        "file_count": audit["file_count"],
        "total_size_bytes": audit["size_summary"]["total_size_bytes"],
        "total_size_mb": round(audit["size_summary"]["total_size_bytes"] / 1024**2, 2),
        "newest_modified_file": audit["activity"]["newest_modified_file"],
        "oldest_modified_file": audit["activity"]["oldest_modified_file"],
        "appears_active_recently": audit["activity"]["appears_active"],
        "activity_windows": audit["activity"]["activity_windows"],
        "schema_files_present": audit["schema_dump_count"],
        "parquet_file_count": audit["parquet"]["parquet_file_count"],
        "readable_parquet_files": audit["parquet"]["readable_parquet_count"],
        "unreadable_files": audit["parquet"]["unreadable_files"],
        "row_count_per_file": audit["parquet"]["row_count_by_file"],
        "total_rows_if_feasible": audit["parquet"]["total_readable_rows"],
        "readiness": readiness,
        "suggested_next_command": "Wait for Kafka lag zero, then run scripts/inspect_full_packet_delivery.py before ingestion.",
    }


def render_summary(summary: dict[str, Any]) -> str:
    lines = ["# Download Progress Summary", "", f"- Status: `{summary['status']}`", f"- Topic: `{summary.get('topic')}`", f"- Raw dir: `{summary.get('raw_dir')}`"]
    for key in ("file_count", "total_size_mb", "parquet_file_count", "readable_parquet_files", "total_rows_if_feasible", "appears_active_recently"):
        if key in summary:
            lines.append(f"- {key}: `{summary.get(key)}`")
    if summary.get("unreadable_files"):
        lines.extend(["", "## Unreadable Files", ""])
        for item in summary["unreadable_files"]:
            lines.append(f"- `{item['path']}`: {item['error']}")
    lines.extend(["", "## Suggested Next Command", "", summary.get("suggested_next_command", ""), ""])
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())

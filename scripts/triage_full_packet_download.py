#!/usr/bin/env python
"""Triage a full-week full-packet raw delivery without modifying raw files."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

from fink_lsst.bulk_transfer.raw_audit import build_raw_audit
from fink_lsst.bulk_transfer.raw_readiness import classify_raw_readiness
from fink_lsst.bulk_transfer.run_manifest import load_run_manifest, topic_entry_from_manifest
from fink_lsst.bulk_transfer.topic_registry import find_topic_entry, load_topic_registry


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--topic")
    parser.add_argument("--run-config")
    parser.add_argument("--raw-dir")
    parser.add_argument("--registry", default="configs/data_transfer_topics.yaml")
    parser.add_argument("--expected-total", type=int)
    parser.add_argument("--progress-from-terminal", type=int)
    parser.add_argument("--json", action="store_true", dest="json_only")
    parser.add_argument("--write-report", action="store_true")
    args = parser.parse_args()

    entry = None
    if args.run_config:
        entry = topic_entry_from_manifest(load_run_manifest(_abs(args.run_config)))
    elif args.topic:
        registry = load_topic_registry(PROJECT_ROOT / args.registry)
        entry = find_topic_entry(registry, topic=args.topic)
    raw_dir = resolve_raw_dir(args.raw_dir, entry)
    expected_total = args.expected_total or (entry or {}).get("download_evidence", {}).get("expected_total_messages") or _expected_total_from_entry(entry)
    report = build_triage_report(raw_dir, entry, expected_total, args.progress_from_terminal)
    if args.write_report and entry:
        output_base = output_base_for(entry)
        output_base.mkdir(parents=True, exist_ok=True)
        (output_base / "download_triage.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (output_base / "DOWNLOAD_TRIAGE.md").write_text(render_triage_markdown(report), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True) if args.json_only else render_triage_markdown(report))
    return 0


def build_triage_report(raw_dir: Path, entry: dict[str, Any] | None, expected_total: int | None, progress_from_terminal: int | None) -> dict[str, Any]:
    audit = build_raw_audit(raw_dir)
    readiness = classify_raw_readiness(audit, expected_total=expected_total, progress_from_terminal=progress_from_terminal)
    raw_ignored = git_ignored(raw_dir / "placeholder.parquet")
    return {
        "topic": (entry or {}).get("topic"),
        "scope": (entry or {}).get("scope"),
        "raw_dir": str(raw_dir),
        "raw_path_ignored_by_git": raw_ignored,
        "expected_total": expected_total,
        "progress_from_terminal": progress_from_terminal,
        "audit": audit,
        "readiness": readiness,
    }


def render_triage_markdown(report: dict[str, Any]) -> str:
    audit = report["audit"]
    readiness = report["readiness"]
    size = audit["size_summary"]
    parquet = audit["parquet"]
    activity = audit["activity"]
    lines = [
        "# Full-Packet Download Triage",
        "",
        f"- Topic: `{report.get('topic')}`",
        f"- Raw dir: `{report.get('raw_dir')}`",
        f"- Raw path ignored by Git: `{report.get('raw_path_ignored_by_git')}`",
        f"- State: `{readiness['state']}`",
        f"- Reason: {readiness['reason']}",
        f"- Raw download appears complete: `{readiness['raw_download_appears_complete']}`",
        f"- Scientific week completeness: `{readiness['week_scientifically_complete']}`",
        f"- File count: `{audit['file_count']}`",
        f"- Total size: `{round(size['total_size_bytes'] / 1024**3, 3)} GiB`",
        f"- Parquet files: `{parquet['parquet_file_count']}`",
        f"- Readable Parquet rows: `{parquet['total_readable_rows']}`",
        f"- Unreadable/corrupt Parquet files: `{parquet['unreadable_parquet_count']}`",
        f"- Expected total: `{readiness.get('expected_total')}`",
        f"- Apparent percent complete: `{readiness.get('apparent_percent_complete')}`",
        f"- Remaining rows: `{readiness.get('remaining_rows')}`",
        f"- Terminal progress gap: `{readiness.get('terminal_progress_gap')}`",
        f"- Newest modified file: `{activity.get('newest_modified_file')}`",
        f"- Oldest modified file: `{activity.get('oldest_modified_file')}`",
        f"- Activity windows: `{activity.get('activity_windows')}`",
        "",
        "## Schema Groups",
        "",
    ]
    for group in audit["schemas"]["schema_groups"][:20]:
        lines.append(f"- `{group['schema_hash']}`: files=`{group['file_count']}`, rows=`{group['rows']}`, columns=`{len(group.get('columns', []))}`")
    if not audit["schemas"]["schema_groups"]:
        lines.append("- None.")
    lines.extend(["", "## Largest Files", ""])
    for item in size["largest_files"][:10]:
        lines.append(f"- `{item['path']}`: `{round(item['size_bytes'] / 1024**2, 2)} MiB`")
    lines.extend(["", "## Smallest Suspicious Files", ""])
    for item in size["tiny_suspicious_files"][:20]:
        lines.append(f"- `{item['path']}`: `{item['size_bytes']} bytes`")
    if not size["tiny_suspicious_files"]:
        lines.append("- None.")
    return "\n".join(lines) + "\n"


def resolve_raw_dir(raw_dir: str | None, entry: dict[str, Any] | None) -> Path:
    if raw_dir:
        path = Path(raw_dir)
    elif entry and entry.get("raw_delivery_dir"):
        path = Path(entry["raw_delivery_dir"])
    else:
        raise SystemExit("Provide --raw-dir or a registered --topic")
    return path if path.is_absolute() else PROJECT_ROOT / path


def _abs(path: str) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else PROJECT_ROOT / candidate


def _expected_total_from_entry(entry: dict[str, Any] | None) -> int | None:
    if not entry:
        return None
    value = entry.get("expected_total_messages")
    if value is not None:
        return int(value)
    if entry.get("topic") == "ftransfer_lsst_2026-06-27_38507":
        return 1071519
    return None


def output_base_for(entry: dict[str, Any]) -> Path:
    return PROJECT_ROOT / "outputs/data_transfer/full_week_full_packet" / f"{entry['utc_start']}_to_{entry['utc_stop']}"


def git_ignored(path: Path) -> bool:
    try:
        rel = path.relative_to(PROJECT_ROOT)
    except ValueError:
        return False
    completed = subprocess.run(["git", "check-ignore", "-q", str(rel)], cwd=PROJECT_ROOT)
    return completed.returncode == 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python
"""Summarize raw Data Transfer download progress without modifying raw files.

Paths resolve against the data root (FINK_LSST_DATA_ROOT, or the checkout when
unset). The raw directory must sit under `data/raw/data_transfer` and every
file in it must stay inside it. When a run manifest or registry topic is given,
the progress report is written to that run's `outputs/data_transfer/...`
directory under the same root.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from fink_lsst.bulk_transfer.raw_audit import build_raw_audit
from fink_lsst.bulk_transfer.raw_readiness import classify_raw_readiness
from fink_lsst.bulk_transfer.run_manifest import (
    PATH_BASES,
    RunManifest,
    derive_default_paths,
    load_run_manifest,
    manifest_from_topic_registry,
    resolve_run_paths,
)
from fink_lsst.bulk_transfer.topic_registry import find_topic_entry, load_topic_registry
from fink_lsst.data_root import DataRootError, PathConfinementError, confine_tree, resolve_data_root, storage_base


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TRANSFER_HINT = "python scripts/print_data_transfer_download_command.py"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-config", help="Run manifest (preferred for production runs).")
    parser.add_argument("--topic", help="Topic recorded in the registry.")
    parser.add_argument("--raw-dir", help="Raw directory override, relative to the data root or absolute inside it.")
    parser.add_argument("--registry", default="configs/data_transfer_topics.yaml")
    parser.add_argument("--expected-total", type=int)
    parser.add_argument("--progress-from-terminal", type=int)
    args = parser.parse_args()
    if not (args.run_config or args.topic or args.raw_dir):
        parser.error("provide --run-config, --topic, or --raw-dir")

    try:
        data_root = resolve_data_root(repo_root=PROJECT_ROOT)
        manifest = _load_manifest(args)
        raw_dir, report_dir = _resolve_locations(manifest, args.raw_dir, data_root)
        expected_total = args.expected_total
        if expected_total is None and manifest is not None:
            expected_total = manifest.download_evidence.expected_total_messages
        summary = summarize_progress(raw_dir, manifest=manifest, expected_total=expected_total, progress_from_terminal=args.progress_from_terminal)
    except (DataRootError, PathConfinementError) as exc:
        print(f"error: {exc}")
        return 2
    summary["data_root"] = str(data_root)
    print(render_summary(summary))
    if report_dir is not None:
        report_dir.mkdir(parents=True, exist_ok=True)
        (report_dir / "download_progress.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (report_dir / "DOWNLOAD_PROGRESS.md").write_text(render_summary(summary), encoding="utf-8")
        print(f"report_dir: {report_dir}")
    return 0


def _load_manifest(args: argparse.Namespace) -> RunManifest | None:
    if args.run_config:
        path = Path(args.run_config)
        return load_run_manifest(path if path.is_absolute() else PROJECT_ROOT / path)
    if args.topic:
        registry_path = Path(args.registry)
        entry = find_topic_entry(load_topic_registry(registry_path if registry_path.is_absolute() else PROJECT_ROOT / registry_path), topic=args.topic)
        if entry is None:
            raise PathConfinementError(f"topic {args.topic!r} is not in the registry; record it before monitoring")
        manifest = manifest_from_topic_registry(entry)
        manifest.paths.outputs_dir = derive_default_paths(manifest).outputs_dir
        return manifest
    return None


def _resolve_locations(manifest: RunManifest | None, raw_override: str | None, data_root: Path) -> tuple[Path, Path | None]:
    """Return the confined raw directory and, for a known run, its confined report directory."""
    raw_base = storage_base(data_root, PATH_BASES["raw_dir"])
    report_dir = None
    raw_dir = None
    if manifest is not None:
        paths = resolve_run_paths(manifest, data_root)
        raw_dir, report_dir = paths["raw_dir"], paths["outputs_dir"]
    if raw_override:
        candidate = Path(raw_override)
        raw_dir = confine_tree(candidate if candidate.is_absolute() else data_root / candidate, raw_base)
    return raw_dir, report_dir


def summarize_progress(raw_dir: Path, manifest: RunManifest | None = None, expected_total: int | None = None, progress_from_terminal: int | None = None) -> dict[str, Any]:
    topic = manifest.topic if manifest is not None else None
    packet_type = manifest.packet_type if manifest is not None else "unknown"
    if not raw_dir.exists():
        return {
            "status": "raw_delivery_missing",
            "raw_dir": str(raw_dir),
            "topic": topic,
            "packet_type": packet_type,
            "suggested_next_command": f"Print the finkctl transfer command with `{TRANSFER_HINT}` and run it manually in tmux.",
        }
    audit = build_raw_audit(raw_dir)
    readiness = classify_raw_readiness(audit, expected_total=expected_total, progress_from_terminal=progress_from_terminal)
    return {
        "status": "raw_delivery_present",
        "topic": topic,
        "packet_type": packet_type,
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
        "suggested_next_command": _next_step(packet_type),
    }


def _next_step(packet_type: str) -> str:
    if packet_type == "full":
        return "Wait for Kafka lag zero, then run scripts/inspect_full_packet_delivery.py before ingestion."
    return (
        "Wait for Kafka lag zero, record expected and terminal message counts in the run manifest, then run "
        "`python scripts/run_analysis.py --run-config <manifest> --stage ingest --dry-run`. "
        "Lag zero and readable Parquet are not proof of completeness."
    )


def render_summary(summary: dict[str, Any]) -> str:
    lines = [
        "# Download Progress Summary",
        "",
        f"- Status: `{summary['status']}`",
        f"- Topic: `{summary.get('topic')}`",
        f"- Packet type: `{summary.get('packet_type')}`",
        f"- Data root: `{summary.get('data_root')}`",
        f"- Raw dir: `{summary.get('raw_dir')}`",
    ]
    for key in ("file_count", "total_size_mb", "parquet_file_count", "readable_parquet_files", "total_rows_if_feasible", "appears_active_recently"):
        if key in summary:
            lines.append(f"- {key}: `{summary.get(key)}`")
    if summary.get("unreadable_files"):
        lines.extend(["", "## Unreadable Files", ""])
        for item in summary["unreadable_files"]:
            lines.append(f"- `{item['path']}`: {item['error']}")
    lines.extend(["", "## Suggested Next Step", "", summary.get("suggested_next_command", ""), ""])
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())

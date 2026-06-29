#!/usr/bin/env python
"""Run fast partial full-packet stress analysis without completeness claims."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from fink_lsst.bulk_transfer.quick_analysis import DEFAULT_OUT_DIR, write_quick_analysis_outputs
from fink_lsst.bulk_transfer.run_manifest import load_run_manifest
from fink_lsst.bulk_transfer.topic_registry import find_topic_entry, load_topic_registry


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-config")
    parser.add_argument("--raw-dir")
    parser.add_argument("--topic")
    parser.add_argument("--registry", default="configs/data_transfer_topics.yaml")
    parser.add_argument("--expected-total", type=int)
    parser.add_argument("--max-files", type=int, default=5000)
    parser.add_argument("--metadata-only", action="store_true")
    parser.add_argument("--include-cutout-columns", action="store_true")
    parser.add_argument("--progress-every", type=int, default=1000)
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--write-report", action="store_true")
    args = parser.parse_args()

    raw_dir = resolve_raw_dir(args)
    output_dir = _abs(args.out_dir)
    print("partial debug/stress-test only; no completeness claim")
    print(f"raw_dir: {raw_dir}")
    print(f"out_dir: {output_dir}")
    if not args.write_report:
        print("Use --write-report to write outputs.")
        return 0
    result = write_quick_analysis_outputs(
        raw_dir,
        out_dir=output_dir,
        expected_total=args.expected_total,
        max_files=args.max_files,
        metadata_only=args.metadata_only,
        include_cutout_columns=args.include_cutout_columns,
        progress_every=args.progress_every,
    )
    print(json.dumps(_printable_result(result), indent=2, sort_keys=True))
    return 0


def resolve_raw_dir(args: argparse.Namespace) -> Path:
    if args.raw_dir:
        return _abs(args.raw_dir).resolve()
    if args.run_config:
        manifest = load_run_manifest(_abs(args.run_config))
        return _abs(manifest.paths.raw_dir).resolve()
    if args.topic:
        registry = load_topic_registry(PROJECT_ROOT / args.registry)
        entry = find_topic_entry(registry, topic=args.topic)
        if not entry:
            raise SystemExit(f"Topic not found in registry: {args.topic}")
        return _abs(entry["raw_delivery_dir"]).resolve()
    raise SystemExit("Provide --run-config, --raw-dir, or --topic")


def _abs(path: str | Path) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else PROJECT_ROOT / candidate


def _printable_result(result: dict) -> dict:
    compact = dict(result)
    metadata = compact.get("metadata") or {}
    compact["metadata"] = {
        "file_count": metadata.get("file_count"),
        "readable_file_count": metadata.get("readable_file_count"),
        "readable_rows": metadata.get("readable_rows"),
        "expected_total": metadata.get("expected_total"),
        "apparent_percent_complete": metadata.get("apparent_percent_complete"),
        "schema_group_count": metadata.get("schema_group_count"),
        "scientific_completeness": metadata.get("scientific_completeness"),
    }
    sample = compact.get("sample_summary") or {}
    if sample:
        compact["sample_summary"] = {
            "sample_rows": sample.get("sample_rows"),
            "unique_objects": sample.get("unique_objects"),
            "unique_sources": sample.get("unique_sources"),
            "band_counts": sample.get("band_counts"),
            "limitations": sample.get("limitations"),
        }
    return compact


if __name__ == "__main__":
    raise SystemExit(main())

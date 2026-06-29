#!/usr/bin/env python
"""Inspect raw full-packet delivery files without dumping payload values."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from fink_lsst.bulk_transfer.raw_audit import build_raw_audit
from fink_lsst.bulk_transfer.raw_readiness import classify_raw_readiness, partial_ingestion_blocked
from fink_lsst.bulk_transfer.run_manifest import load_run_manifest, topic_entry_from_manifest
from fink_lsst.bulk_transfer.topic_registry import find_topic_entry, load_topic_registry


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HEAVY_MARKERS = ("cutout", "stamp", "image", "fits", "bytes", "binary")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--topic")
    parser.add_argument("--run-config")
    parser.add_argument("--raw-dir")
    parser.add_argument("--registry", default="configs/data_transfer_topics.yaml")
    args = parser.parse_args()
    entry = None
    if args.run_config:
        entry = topic_entry_from_manifest(load_run_manifest(_abs(args.run_config)))
    elif args.topic:
        registry = load_topic_registry(PROJECT_ROOT / args.registry)
        entry = find_topic_entry(registry, topic=args.topic)
    raw_dir = Path(args.raw_dir) if args.raw_dir else Path((entry or {}).get("raw_delivery_dir", ""))
    raw_dir = raw_dir if raw_dir.is_absolute() else PROJECT_ROOT / raw_dir
    output_base = output_base_for(entry)
    output_base.mkdir(parents=True, exist_ok=True)
    if not raw_dir.exists():
        payload = {"status": "raw_delivery_missing", "raw_dir": str(raw_dir), "topic": (entry or {}).get("topic")}
        print("raw_delivery_missing")
        (output_base / "raw_full_packet_field_inventory.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (output_base / "RAW_FULL_PACKET_FIELD_INVENTORY.md").write_text("# Raw Full-Packet Field Inventory\n\n- Status: `raw_delivery_missing`\n", encoding="utf-8")
        return 0
    audit = build_raw_audit(raw_dir)
    readiness = classify_raw_readiness(audit)
    inventory = build_inventory(audit, readiness)
    (output_base / "raw_full_packet_field_inventory.json").write_text(json.dumps(inventory, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output_base / "schema_groups.json").write_text(json.dumps(inventory.get("schema_groups", []), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output_base / "RAW_FULL_PACKET_FIELD_INVENTORY.md").write_text(render_inventory(inventory), encoding="utf-8")
    print(render_inventory(inventory))
    return 0


def build_inventory(audit: dict[str, Any], readiness: dict[str, Any]) -> dict[str, Any]:
    schema_groups = audit.get("schemas", {}).get("schema_groups", [])
    columns = sorted({column for group in schema_groups for column in group.get("columns", [])})
    suspected_heavy = [column for column in columns if any(marker in column.lower() for marker in HEAVY_MARKERS)]
    binary_fields = []
    nested_columns = []
    for group in schema_groups:
        for field in group.get("fields", []):
            if field.get("nested"):
                nested_columns.append(field.get("name"))
            if "binary" in str(field.get("type", "")).lower() or "bytes" in str(field.get("type", "")).lower():
                binary_fields.append(field.get("name"))
    status = "partial_raw_inspection" if partial_ingestion_blocked(readiness["state"]) else "raw_delivery_present"
    return {
        "status": status,
        "readiness": readiness,
        "delivery_dir": audit.get("raw_dir"),
        "file_count": audit.get("file_count"),
        "total_size_bytes": audit.get("size_summary", {}).get("total_size_bytes"),
        "total_rows": audit.get("parquet", {}).get("total_readable_rows"),
        "schema_group_count": audit.get("schemas", {}).get("schema_group_count"),
        "columns": columns,
        "nested_columns": sorted(set(nested_columns)),
        "binary_fields": sorted(set(binary_fields)),
        "suspected_heavy_fields": suspected_heavy,
        "unreadable_files": audit.get("parquet", {}).get("unreadable_files", []),
        "schema_groups": schema_groups,
        "ingestion_recommended": bool(readiness.get("ingestion_allowed") and not partial_ingestion_blocked(readiness["state"])),
    }


def render_inventory(inventory: dict[str, Any]) -> str:
    lines = [
        "# Raw Full-Packet Field Inventory",
        "",
        f"- Status: `{inventory.get('status')}`",
        f"- Raw readiness: `{inventory.get('readiness', {}).get('state')}`",
        f"- Ingestion recommended: `{inventory.get('ingestion_recommended')}`",
        f"- Files: `{inventory.get('file_count')}`",
        f"- Rows: `{inventory.get('total_rows')}`",
        f"- Schema groups: `{inventory.get('schema_group_count')}`",
        "",
        "## Nested Columns",
        "",
    ]
    for column in inventory.get("nested_columns", []):
        lines.append(f"- `{column}`")
    if not inventory.get("nested_columns"):
        lines.append("- None detected.")
    lines.extend(["", "## Suspected Heavy Fields", ""])
    for column in inventory.get("suspected_heavy_fields", []):
        lines.append(f"- `{column}`")
    if not inventory.get("suspected_heavy_fields"):
        lines.append("- None detected by name.")
    lines.extend(["", "No raw payload values were dumped.", ""])
    return "\n".join(lines)


def output_base_for(entry: dict[str, Any] | None) -> Path:
    if entry:
        return PROJECT_ROOT / "outputs/data_transfer/full_week_full_packet" / f"{entry['utc_start']}_to_{entry['utc_stop']}"
    return PROJECT_ROOT / "outputs/data_transfer/full_week_full_packet/unregistered"


def _abs(path: str) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else PROJECT_ROOT / candidate


if __name__ == "__main__":
    raise SystemExit(main())

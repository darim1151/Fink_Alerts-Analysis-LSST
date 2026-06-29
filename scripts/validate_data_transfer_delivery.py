#!/usr/bin/env python
"""Validate processed Fink Data Transfer smoke delivery tables if present."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from fink_lsst.bulk_transfer.config import configured_paths, load_data_transfer_config
from fink_lsst.bulk_transfer.ingest import COMPLETENESS_SCOPE, find_latest_delivery_dir, read_processed_tables
from fink_lsst.bulk_transfer.output_inspector import summarize_delivery
from fink_lsst.bulk_transfer.reporting import render_validation_summary
from fink_lsst.bulk_transfer.validation import summarize_validation_status, validate_delivery_tables
from fink_lsst.storage import write_json


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/local_smoke_test.yaml")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--processed-dir", help="Specific processed smoke ingestion run directory.")
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    config = load_data_transfer_config(project_root / args.config)
    paths = configured_paths(config, project_root)
    processed_dir = Path(args.processed_dir).resolve() if args.processed_dir else _latest_processed_dir(paths["processed_dir"] / "smoke_delivery")
    run_id = processed_dir.name if processed_dir and processed_dir.exists() else datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = paths["reports_dir"] / "smoke_delivery" / run_id
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest = _read_json(processed_dir / "manifest.json") if processed_dir else None
    raw_dir = Path(manifest["raw_delivery_dir"]) if manifest and manifest.get("raw_delivery_dir") else find_latest_delivery_dir(paths["raw_delivery_dir"] / "smoke_delivery")
    raw_inspection = summarize_delivery(raw_dir)
    nested_report = _read_json(processed_dir / "nested_conversion_report.json") if processed_dir else None
    tables = read_processed_tables(processed_dir) if processed_dir and processed_dir.exists() else {}

    checks = validate_delivery_tables(
        tables,
        config,
        metadata={
            "raw_delivery": raw_inspection,
            "nested_report": nested_report,
            "completeness_scope": (manifest or {}).get("completeness_scope", COMPLETENESS_SCOPE),
        },
    )
    validation_status = summarize_validation_status(checks)
    payload = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "processed_dir": str(processed_dir) if processed_dir else None,
        "raw_delivery_dir": str(raw_dir),
        "validation_status": validation_status,
        "checks": checks,
    }
    write_json(payload, output_dir / "validation_report.json", enforce_allowed_roots=False)
    write_json(payload, output_dir / "smoke_validation_report.json", enforce_allowed_roots=False)
    (output_dir / "VALIDATION_SUMMARY.md").write_text(render_validation_summary(checks), encoding="utf-8")
    (output_dir / "SMOKE_VALIDATION_SUMMARY.md").write_text(render_validation_summary(checks), encoding="utf-8")
    write_json(
        {
            "run_id": run_id,
            "output_dir": str(output_dir),
            "processed_dir": str(processed_dir) if processed_dir else None,
            "validation_status": validation_status,
        },
        paths["reports_dir"] / "smoke_delivery" / "latest_validation.json",
        enforce_allowed_roots=False,
    )

    if not raw_inspection.get("empty", True) and not tables:
        state = "raw delivery exists but processed ingestion is missing"
    elif raw_inspection.get("empty", True):
        state = "no raw delivery"
    elif validation_status == "failed":
        state = "validation failed"
    else:
        state = "validation passed with full-night completeness blocked"
    print(f"State: {state}")
    print(f"Validation status: {validation_status}")
    print(render_validation_summary(checks))
    return 0


def _latest_processed_dir(processed_root: Path) -> Path | None:
    if not processed_root.exists():
        return None
    candidates = [path for path in processed_root.iterdir() if path.is_dir()]
    if not candidates:
        return None
    return sorted(candidates, key=lambda path: (path.stat().st_mtime, path.name))[-1]


def _read_json(path: Path | None):
    if not path or not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    raise SystemExit(main())

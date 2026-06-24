#!/usr/bin/env python
"""Inspect, ingest, and validate a tiny non-complete Data Transfer smoke delivery."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from fink_lsst.bulk_transfer.config import load_data_transfer_config
from fink_lsst.bulk_transfer.ingest import (
    load_data_transfer_files,
    split_bulk_tables,
    write_bulk_ingestion_manifest,
    write_bulk_processed_tables,
)
from fink_lsst.bulk_transfer.output_inspector import summarize_delivery
from fink_lsst.bulk_transfer.reporting import render_delivery_inspection_report, render_ingestion_report, render_validation_summary
from fink_lsst.bulk_transfer.validation import validate_delivery_tables
from fink_lsst.storage import write_json


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/local_smoke_test.yaml")
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args()
    project_root = Path(args.project_root).resolve()
    config = load_data_transfer_config(project_root / args.config)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    smoke_dir = project_root / "data/raw/data_transfer/smoke_delivery"
    processed_dir = project_root / "data/processed/data_transfer/smoke_delivery" / run_id
    output_dir = project_root / "outputs/data_transfer/smoke_delivery" / run_id
    processed_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    inspection = summarize_delivery(smoke_dir)
    write_json(inspection, output_dir / "smoke_delivery_inspection.json", enforce_allowed_roots=False)
    (output_dir / "SMOKE_DELIVERY_INSPECTION.md").write_text(render_delivery_inspection_report(inspection), encoding="utf-8")
    if inspection["empty"]:
        message = (
            "No smoke delivery files found. Place a tiny manually delivered file under "
            "data/raw/data_transfer/smoke_delivery/ and rerun this script."
        )
        report = render_ingestion_report(run_id, inspection, {}) + f"\nSmoke delivery is not a full-night dataset.\n\n{message}\n"
        (output_dir / "SMOKE_INGESTION_REPORT.md").write_text(report, encoding="utf-8")
        checks = validate_delivery_tables({}, config)
        write_json({"checks": checks}, output_dir / "smoke_validation_report.json", enforce_allowed_roots=False)
        (output_dir / "SMOKE_VALIDATION_SUMMARY.md").write_text(render_validation_summary(checks), encoding="utf-8")
        write_json({"run_id": run_id, "delivery_empty": True, "complete_night_claim_allowed": False}, output_dir / "smoke_manifest.json", enforce_allowed_roots=False)
        print(message)
        return 0
    frame = load_data_transfer_files(smoke_dir)
    tables = split_bulk_tables(frame)
    artifacts = write_bulk_processed_tables(tables, processed_dir, project_root=project_root)
    write_bulk_ingestion_manifest(run_id, smoke_dir, processed_dir, artifacts, processed_dir / "manifest.json")
    checks = validate_delivery_tables(tables, config)
    write_json({"checks": checks}, output_dir / "smoke_validation_report.json", enforce_allowed_roots=False)
    write_json({"run_id": run_id, "artifacts": artifacts, "complete_night_claim_allowed": False}, output_dir / "smoke_manifest.json", enforce_allowed_roots=False)
    (output_dir / "SMOKE_INGESTION_REPORT.md").write_text(
        render_ingestion_report(run_id, inspection, artifacts) + "\nSmoke delivery is not a full-night dataset. No completeness claim allowed.\n",
        encoding="utf-8",
    )
    (output_dir / "SMOKE_VALIDATION_SUMMARY.md").write_text(render_validation_summary(checks), encoding="utf-8")
    print(f"Wrote smoke ingestion run: {run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

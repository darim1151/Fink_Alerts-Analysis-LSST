#!/usr/bin/env python
"""Ingest local Fink Data Transfer delivery files into internal tables."""

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
from fink_lsst.bulk_transfer.reporting import render_ingestion_report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/local_smoke_test.yaml")
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args()
    project_root = Path(args.project_root).resolve()
    config = load_data_transfer_config(project_root / args.config)
    paths = config.get("local_paths", {})
    delivery_dir = project_root / paths.get("raw_delivery_dir", "data/raw/data_transfer")
    report = summarize_delivery(delivery_dir)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    processed_dir = project_root / paths.get("processed_dir", "data/processed/data_transfer") / run_id
    output_dir = project_root / paths.get("reports_dir", "outputs/data_transfer") / run_id
    processed_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    if report["empty"]:
        (output_dir / "INGESTION_REPORT.md").write_text(render_ingestion_report(run_id, report, {}), encoding="utf-8")
        print("No delivered files available to ingest.")
        return 0
    frame = load_data_transfer_files(delivery_dir)
    tables = split_bulk_tables(frame)
    artifacts = write_bulk_processed_tables(tables, processed_dir, project_root=project_root)
    write_bulk_ingestion_manifest(run_id, delivery_dir, processed_dir, artifacts, processed_dir / "manifest.json")
    (output_dir / "INGESTION_REPORT.md").write_text(render_ingestion_report(run_id, report, artifacts), encoding="utf-8")
    print(f"Wrote bulk ingestion run: {run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

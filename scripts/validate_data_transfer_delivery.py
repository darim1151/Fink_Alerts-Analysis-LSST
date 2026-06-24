#!/usr/bin/env python
"""Validate processed Fink Data Transfer delivery tables if present."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from fink_lsst.bulk_transfer.config import load_data_transfer_config
from fink_lsst.bulk_transfer.reporting import render_validation_summary
from fink_lsst.bulk_transfer.validation import validate_delivery_tables
from fink_lsst.storage import write_json


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/local_smoke_test.yaml")
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args()
    project_root = Path(args.project_root).resolve()
    config = load_data_transfer_config(project_root / args.config)
    paths = config.get("local_paths", {})
    processed_root = project_root / paths.get("processed_dir", "data/processed/data_transfer")
    reports_root = project_root / paths.get("reports_dir", "outputs/data_transfer")
    latest_dirs = sorted([path for path in processed_root.glob("*") if path.is_dir()])
    tables = {}
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if latest_dirs:
        latest = latest_dirs[-1]
        run_id = latest.name
        for parquet in latest.glob("*.parquet"):
            tables[parquet.stem] = pd.read_parquet(parquet)
    output_dir = reports_root / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    checks = validate_delivery_tables(tables, config)
    report = {"created_at_utc": datetime.now(timezone.utc).isoformat(), "checks": checks}
    write_json(report, output_dir / "validation_report.json", enforce_allowed_roots=False)
    (output_dir / "VALIDATION_SUMMARY.md").write_text(render_validation_summary(checks), encoding="utf-8")
    print(render_validation_summary(checks))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

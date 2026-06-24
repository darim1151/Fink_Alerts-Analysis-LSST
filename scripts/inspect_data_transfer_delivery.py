#!/usr/bin/env python
"""Inspect local Data Transfer delivery files."""

from __future__ import annotations

import argparse
from pathlib import Path

from fink_lsst.bulk_transfer.config import load_data_transfer_config
from fink_lsst.bulk_transfer.output_inspector import summarize_delivery
from fink_lsst.bulk_transfer.reporting import render_delivery_inspection_report
from fink_lsst.storage import write_json


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/local_smoke_test.yaml")
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args()
    project_root = Path(args.project_root).resolve()
    config = load_data_transfer_config(project_root / args.config)
    local_paths = config.get("local_paths", {})
    delivery_dir = project_root / local_paths.get("raw_delivery_dir", "data/raw/data_transfer")
    reports_dir = project_root / local_paths.get("reports_dir", "outputs/data_transfer")
    reports_dir.mkdir(parents=True, exist_ok=True)
    delivery_dir.mkdir(parents=True, exist_ok=True)
    report = summarize_delivery(delivery_dir)
    write_json(report, reports_dir / "delivery_inspection.json", enforce_allowed_roots=False)
    (reports_dir / "DELIVERY_INSPECTION.md").write_text(render_delivery_inspection_report(report), encoding="utf-8")
    print(render_delivery_inspection_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python
"""Check local readiness for Fink Data Transfer without submitting jobs."""

from __future__ import annotations

import argparse
from pathlib import Path

from fink_lsst.bulk_transfer.config import load_data_transfer_config
from fink_lsst.bulk_transfer.reporting import render_setup_report
from fink_lsst.bulk_transfer.setup_check import build_setup_status_report
from fink_lsst.storage import write_json


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/local_smoke_test.yaml")
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args()
    project_root = Path(args.project_root).resolve()
    config = load_data_transfer_config(project_root / args.config)
    reports_dir = project_root / config.get("local_paths", {}).get("reports_dir", "outputs/data_transfer")
    reports_dir.mkdir(parents=True, exist_ok=True)
    report = build_setup_status_report(config)
    write_json(report, reports_dir / "setup_status.json", enforce_allowed_roots=False)
    (reports_dir / "SETUP_STATUS.md").write_text(render_setup_report(report), encoding="utf-8")
    print(render_setup_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

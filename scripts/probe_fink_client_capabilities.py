#!/usr/bin/env python
"""Run safe fink-client capability probes without submitting jobs."""

from __future__ import annotations

import argparse
from pathlib import Path

from fink_lsst.bulk_transfer.client_probe import run_safe_client_probes
from fink_lsst.bulk_transfer.config import load_data_transfer_config
from fink_lsst.bulk_transfer.reporting import render_client_probe_report
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
    report = run_safe_client_probes()
    write_json(report, reports_dir / "client_probe.json", enforce_allowed_roots=False)
    (reports_dir / "CLIENT_PROBE.md").write_text(render_client_probe_report(report), encoding="utf-8")
    print(render_client_probe_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

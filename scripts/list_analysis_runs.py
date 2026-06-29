#!/usr/bin/env python
"""List manifest-driven analysis runs."""

from __future__ import annotations

import argparse
from pathlib import Path

from fink_lsst.bulk_transfer.run_index import summarize_runs


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", default="configs/runs/index.yaml")
    args = parser.parse_args()
    for summary in summarize_runs(PROJECT_ROOT / args.index, project_root=PROJECT_ROOT):
        claims = summary["claim_state"]
        print(summary["name"])
        print(f"  role: {summary.get('role')}")
        print(f"  scope: {summary['scope']}")
        print(f"  date_window: {summary['date_window']}")
        print(f"  packet_type: {summary['packet_type']}")
        print(f"  lifecycle_state: {summary['lifecycle_state']}")
        print(
            "  claim_state: "
            f"all_alert={claims['all_alert_completeness']}, "
            f"night={claims['night_completeness']}, "
            f"week={claims['week_completeness']}"
        )
        print(f"  config: {summary['config']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

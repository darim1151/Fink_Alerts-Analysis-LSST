#!/usr/bin/env python
"""Validate preliminary normalized minimal-ingestion tables."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from fink_lsst.storage import safe_artifact_path, write_json
from fink_lsst.validation import validate_normalized_table


TABLES = {
    "sources": "sources.parquet",
    "objects": "objects.parquet",
    "forced_photometry": "forced_photometry.parquet",
    "statistics": "statistics.parquet",
    "tags": "tags.parquet",
}


def main() -> int:
    """Run validation checks and save JSON plus Markdown reports."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args()
    project_root = Path(args.project_root).resolve()
    created_at = datetime.now(timezone.utc).isoformat()
    results: list[dict[str, Any]] = []

    for table_type, filename in TABLES.items():
        path = project_root / "data/processed/minimal_samples" / filename
        if not path.exists():
            results.append(
                {
                    "table": table_type,
                    "path": str(path),
                    "checks": [
                        {
                            "check": "table_exists",
                            "passed": False,
                            "severity": "warning",
                            "message": "Processed table is missing",
                            "details": {},
                        }
                    ],
                }
            )
            continue
        df = pd.read_parquet(path)
        results.append({"table": table_type, "path": str(path), "checks": validate_normalized_table(df, table_type)})

    report = {"created_at_utc": created_at, "results": results, "note": "Validation for tiny samples only."}
    report_path = safe_artifact_path(
        "minimal_ingestion/validation_report.json",
        root_name="outputs",
        project_root=project_root,
    )
    write_json(report, report_path, project_root=project_root)
    summary_path = safe_artifact_path(
        "minimal_ingestion/validation_summary.md",
        root_name="outputs",
        project_root=project_root,
    )
    summary_path.write_text(_markdown_summary(report), encoding="utf-8")
    print(f"Wrote validation report: {report_path}")
    print(f"Wrote validation summary: {summary_path}")
    return 0


def _markdown_summary(report: dict[str, Any]) -> str:
    lines = ["# Minimal Ingestion Validation Summary", "", f"- Created UTC: `{report['created_at_utc']}`", ""]
    for table in report["results"]:
        lines.append(f"## {table['table']}")
        lines.append("")
        for check in table["checks"]:
            lines.append(
                f"- `{check['severity']}` `{check['check']}`: "
                f"{'passed' if check['passed'] else 'failed'} - {check['message']}"
            )
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())

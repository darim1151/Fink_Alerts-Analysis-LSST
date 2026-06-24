#!/usr/bin/env python
"""Build preliminary normalized tables from tiny raw Fink LSST samples."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from fink_lsst.normalize import (
    normalize_forced_photometry,
    normalize_objects,
    normalize_sources,
    normalize_statistics,
    normalize_tags_or_classifications,
)
from fink_lsst.storage import read_json, safe_artifact_path, write_json, write_parquet


TABLE_SPECS: dict[str, tuple[str, str, Callable[[Any], pd.DataFrame]]] = {
    "sources": ("raw/minimal_samples/sources_sample_raw.json", "sources", normalize_sources),
    "objects": ("raw/minimal_samples/objects_sample_raw.json", "objects", normalize_objects),
    "forced_photometry": ("raw/minimal_samples/fp_sample_raw.json", "forced_photometry", normalize_forced_photometry),
    "statistics": ("fixtures/fink_lsst_statistics_sample.json", "statistics", normalize_statistics),
    "tags": ("fixtures/fink_lsst_tags.json", "tags", normalize_tags_or_classifications),
}


def main() -> int:
    """Normalize available minimal samples and save parquet plus CSV previews."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args()
    project_root = Path(args.project_root).resolve()
    created_at = datetime.now(timezone.utc).isoformat()
    report: list[dict[str, Any]] = []
    artifacts: list[dict[str, str]] = []

    for table_name, (input_rel, output_stem, normalizer) in TABLE_SPECS.items():
        input_path = project_root / "data" / input_rel
        if not input_path.exists():
            report.append({"table": table_name, "ok": False, "skipped": True, "skip_reason": f"Missing input {input_path}"})
            continue
        payload = read_json(input_path)
        df = normalizer(payload)
        parquet_path = safe_artifact_path(
            f"processed/minimal_samples/{output_stem}.parquet",
            root_name="data",
            project_root=project_root,
        )
        csv_path = safe_artifact_path(
            f"minimal_ingestion/tables_preview/{output_stem}.csv",
            root_name="outputs",
            project_root=project_root,
        )
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(csv_path, index=False)
        try:
            write_parquet(df, parquet_path, project_root=project_root)
            parquet_saved = True
            parquet_error = None
        except Exception as exc:  # noqa: BLE001 - record clear install guidance
            parquet_saved = False
            parquet_error = f"{exc}. Install pyarrow with `python -m pip install pyarrow`."
        report.append(
            {
                "table": table_name,
                "ok": parquet_saved,
                "input_path": str(input_path),
                "rows": int(len(df)),
                "columns": list(df.columns),
                "csv_preview_path": str(csv_path),
                "parquet_path": str(parquet_path) if parquet_saved else None,
                "parquet_error": parquet_error,
                "diagnostics": df.attrs.get("diagnostics", {}),
            }
        )
        artifacts.append({"name": f"{table_name}_csv", "path": str(csv_path)})
        if parquet_saved:
            artifacts.append({"name": f"{table_name}_parquet", "path": str(parquet_path)})

    report_path = safe_artifact_path(
        "minimal_ingestion/normalization_report.json",
        root_name="outputs",
        project_root=project_root,
    )
    write_json(report, report_path, project_root=project_root)
    manifest_path = safe_artifact_path(
        "processed/minimal_samples/manifest.json",
        root_name="data",
        project_root=project_root,
    )
    write_json(
        {
            "created_at_utc": created_at,
            "artifacts": artifacts,
            "report_path": str(report_path),
            "note": "Tiny normalized samples only; not a population-level dataset.",
        },
        manifest_path,
        project_root=project_root,
    )
    failed_parquet = [item for item in report if item.get("parquet_error")]
    if failed_parquet:
        print("Parquet writing failed for at least one table. Install pyarrow with `python -m pip install pyarrow`.")
    print(f"Wrote normalization report: {report_path}")
    print(f"Wrote processed manifest: {manifest_path}")
    return 1 if failed_parquet else 0


if __name__ == "__main__":
    raise SystemExit(main())

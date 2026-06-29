#!/usr/bin/env python
"""Inspect, ingest, validate, and diagnose a non-complete Data Transfer smoke delivery."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from fink_lsst.bulk_transfer.config import configured_paths, load_data_transfer_config
from fink_lsst.bulk_transfer.diagnostics import build_smoke_science_readiness_report
from fink_lsst.bulk_transfer.ingest import (
    COMPLETENESS_SCOPE,
    SMOKE_DELIVERY_METADATA,
    find_latest_delivery_dir,
    load_data_transfer_files,
    read_processed_tables,
    split_bulk_tables,
    table_shapes,
    write_bulk_ingestion_manifest,
    write_bulk_processed_tables,
)
from fink_lsst.bulk_transfer.output_inspector import summarize_delivery
from fink_lsst.bulk_transfer.plots import generate_smoke_diagnostic_plots
from fink_lsst.bulk_transfer.reporting import (
    render_delivery_inspection_report,
    render_ingestion_report,
    render_raw_field_inventory,
    render_validation_summary,
)
from fink_lsst.bulk_transfer.validation import summarize_validation_status, validate_delivery_tables
from fink_lsst.storage import write_json


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/local_smoke_test.yaml")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--delivery-dir", help="Specific raw smoke delivery directory to ingest.")
    parser.add_argument("--skip-plots", action="store_true", help="Skip diagnostic plot generation.")
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    config = load_data_transfer_config(project_root / args.config)
    paths = configured_paths(config, project_root)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    smoke_root = paths["raw_delivery_dir"] / "smoke_delivery"
    delivery_dir = Path(args.delivery_dir).resolve() if args.delivery_dir else find_latest_delivery_dir(smoke_root)
    processed_dir = paths["processed_dir"] / "smoke_delivery" / run_id
    output_dir = paths["reports_dir"] / "smoke_delivery" / run_id
    processed_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    metadata = {
        **SMOKE_DELIVERY_METADATA,
        "topic": delivery_dir.name if delivery_dir.name != "smoke_delivery" else SMOKE_DELIVERY_METADATA["topic"],
        "utc_start": config.get("target_startdate"),
        "utc_stop": config.get("target_stopdate"),
        "completeness_scope": COMPLETENESS_SCOPE,
    }
    inspection = summarize_delivery(delivery_dir)
    _write_inspection_artifacts(paths["reports_dir"], output_dir, inspection)

    if inspection["empty"]:
        checks = validate_delivery_tables(
            {},
            config,
            metadata={"raw_delivery": inspection, "completeness_scope": COMPLETENESS_SCOPE},
        )
        validation_status = summarize_validation_status(checks)
        write_json({"checks": checks, "validation_status": validation_status}, output_dir / "validation_report.json", enforce_allowed_roots=False)
        (output_dir / "VALIDATION_SUMMARY.md").write_text(render_validation_summary(checks), encoding="utf-8")
        write_bulk_ingestion_manifest(
            run_id,
            delivery_dir,
            processed_dir,
            {},
            processed_dir / "manifest.json",
            inspection=inspection,
            validation_status=validation_status,
            metadata=metadata,
        )
        _write_latest_run(paths["reports_dir"], run_id, output_dir, processed_dir, validation_status)
        print("No smoke delivery files found; wrote validation report with raw delivery missing.")
        return 0

    raw_frame = load_data_transfer_files(delivery_dir, include_source_file=True)
    tables = split_bulk_tables(raw_frame, inspection=inspection)
    artifacts, nested_report = write_bulk_processed_tables(
        tables,
        processed_dir,
        project_root=project_root,
        report_dir=output_dir,
        return_report=True,
    )
    processed_tables = read_processed_tables(processed_dir)
    checks = validate_delivery_tables(
        processed_tables,
        config,
        metadata={
            "raw_delivery": inspection,
            "nested_report": nested_report,
            "completeness_scope": COMPLETENESS_SCOPE,
        },
    )
    validation_status = summarize_validation_status(checks)
    manifest_path = processed_dir / "manifest.json"
    write_bulk_ingestion_manifest(
        run_id,
        delivery_dir,
        processed_dir,
        artifacts,
        manifest_path,
        inspection=inspection,
        table_shapes=table_shapes(processed_tables),
        nested_report={"path": nested_report.get("path"), "converted_columns": nested_report.get("converted_columns")},
        validation_status=validation_status,
        metadata=metadata,
    )
    manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    write_json(manifest_payload, output_dir / "smoke_manifest.json", enforce_allowed_roots=False)

    validation_payload = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "validation_status": validation_status,
        "checks": checks,
    }
    write_json(validation_payload, output_dir / "validation_report.json", enforce_allowed_roots=False)
    write_json(validation_payload, output_dir / "smoke_validation_report.json", enforce_allowed_roots=False)
    (output_dir / "VALIDATION_SUMMARY.md").write_text(render_validation_summary(checks), encoding="utf-8")
    (output_dir / "SMOKE_VALIDATION_SUMMARY.md").write_text(render_validation_summary(checks), encoding="utf-8")

    diagnostics = build_smoke_science_readiness_report(
        processed_tables,
        inspection=inspection,
        validation_status=validation_status,
        nested_report=nested_report,
    )
    figures = [] if args.skip_plots else generate_smoke_diagnostic_plots(processed_tables, inspection, output_dir / "figures")
    diagnostics["figures"] = figures
    write_json(diagnostics, output_dir / "smoke_diagnostics.json", enforce_allowed_roots=False)
    (output_dir / "SMOKE_DIAGNOSTICS.md").write_text(diagnostics["markdown"], encoding="utf-8")

    ingestion_report = render_ingestion_report(run_id, inspection, artifacts)
    ingestion_report += "\nSmoke delivery is tag-filtered and not a full-night all-alert dataset. No completeness claim allowed.\n"
    (output_dir / "SMOKE_INGESTION_REPORT.md").write_text(ingestion_report, encoding="utf-8")
    _write_latest_run(paths["reports_dir"], run_id, output_dir, processed_dir, validation_status)

    shapes = table_shapes(processed_tables)
    nested_columns = sorted({column for columns in nested_report.get("converted_columns", {}).values() for column in columns})
    print(f"Smoke ingestion run: {run_id}")
    print(f"Raw files: {inspection['file_count']}")
    print(f"Raw rows: {inspection['total_rows']}")
    print(f"Processed table shapes: {shapes}")
    print(f"Nested columns sanitized: {nested_columns}")
    print(f"Validation status: {validation_status}")
    print("Next step: review validation, then request explicit confirmation before any full-night transfer.")
    return 0


def _write_inspection_artifacts(reports_dir: Path, output_dir: Path, inspection: dict) -> None:
    write_json(inspection, reports_dir / "delivery_inspection.json", enforce_allowed_roots=False)
    (reports_dir / "DELIVERY_INSPECTION.md").write_text(render_delivery_inspection_report(inspection), encoding="utf-8")
    write_json(inspection, output_dir / "smoke_delivery_inspection.json", enforce_allowed_roots=False)
    write_json(inspection, output_dir / "raw_field_inventory.json", enforce_allowed_roots=False)
    (output_dir / "SMOKE_DELIVERY_INSPECTION.md").write_text(render_delivery_inspection_report(inspection), encoding="utf-8")
    (output_dir / "RAW_FIELD_INVENTORY.md").write_text(render_raw_field_inventory(inspection), encoding="utf-8")


def _write_latest_run(reports_dir: Path, run_id: str, output_dir: Path, processed_dir: Path, validation_status: str) -> None:
    latest = {
        "run_id": run_id,
        "output_dir": str(output_dir),
        "processed_dir": str(processed_dir),
        "manifest_path": str(processed_dir / "manifest.json"),
        "validation_status": validation_status,
    }
    write_json(latest, reports_dir / "smoke_delivery" / "latest_run.json", enforce_allowed_roots=False)


if __name__ == "__main__":
    raise SystemExit(main())

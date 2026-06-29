#!/usr/bin/env python
"""Inspect, ingest, validate, and diagnose a delivered Fink Data Transfer topic."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from fink_lsst.bulk_transfer.config import configured_paths, load_data_transfer_config
from fink_lsst.bulk_transfer.diagnostics import build_science_readiness_report
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
from fink_lsst.bulk_transfer.topic_registry import find_topic_entry, load_topic_registry, metadata_from_topic_entry
from fink_lsst.bulk_transfer.validation import summarize_validation_status, validate_delivery_tables
from fink_lsst.bulk_transfer.scopes import normalize_scope
from fink_lsst.storage import write_json


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/local_smoke_test.yaml")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--registry", default="configs/data_transfer_topics.yaml")
    parser.add_argument(
        "--scope",
        default="smoke_delivery",
        choices=["smoke_delivery", "tag_filtered_smoke_delivery", "full_night", "full_night_all_alerts", "full_week", "full_week_full_packet"],
    )
    parser.add_argument("--topic", help="Topic name recorded in the registry.")
    parser.add_argument("--delivery-dir", help="Specific raw delivery directory to ingest.")
    parser.add_argument("--startdate", help="Runtime UTC start-date override for validation/metadata.")
    parser.add_argument("--stopdate", help="Runtime UTC stop-date override for validation/metadata.")
    parser.add_argument("--validate", action="store_true", help="Compatibility flag; validation is always run.")
    parser.add_argument("--diagnostics", action="store_true", help="Compatibility flag; diagnostics are always run.")
    parser.add_argument("--plots", action="store_true", help="Generate diagnostic plots, overriding --skip-plots.")
    parser.add_argument("--skip-plots", action="store_true", help="Skip diagnostic plot generation.")
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    config = load_data_transfer_config(project_root / args.config)
    if args.startdate:
        config = {**config, "target_startdate": args.startdate}
    if args.stopdate:
        config = {**config, "target_stopdate": args.stopdate}
    paths = configured_paths(config, project_root)
    registry = load_topic_registry(project_root / args.registry)
    registry_scope = "tag_filtered_smoke_delivery" if args.scope == "smoke_delivery" else normalize_scope(args.scope)
    entry = find_topic_entry(registry, topic=args.topic, scope=registry_scope, latest=not args.topic)
    metadata = _metadata_for_scope(config, paths, registry_scope, entry)
    if args.startdate:
        metadata["utc_start"] = args.startdate
    if args.stopdate:
        metadata["utc_stop"] = args.stopdate

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    delivery_dir = _delivery_dir(args.delivery_dir, paths, registry_scope, metadata)
    output_scope_dir = _scope_output_dir(registry_scope)
    processed_dir = paths["processed_dir"] / output_scope_dir / run_id
    output_dir = paths["reports_dir"] / output_scope_dir / run_id
    processed_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    inspection = summarize_delivery(delivery_dir)
    _write_inspection_artifacts(paths["reports_dir"], output_dir, output_scope_dir, inspection)
    if inspection["empty"]:
        checks = validate_delivery_tables({}, config, metadata={"raw_delivery": inspection, **metadata})
        validation_status = summarize_validation_status(checks)
        _write_validation(output_dir, output_scope_dir, run_id, delivery_dir, validation_status, checks)
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
        _write_latest_run(paths["reports_dir"], output_scope_dir, run_id, output_dir, processed_dir, validation_status)
        print(f"No delivery files found for scope `{registry_scope}`; wrote validation report.")
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
        metadata={"raw_delivery": inspection, "nested_report": nested_report, **metadata},
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
    write_json(manifest_payload, output_dir / f"{output_scope_dir}_manifest.json", enforce_allowed_roots=False)
    _write_validation(output_dir, output_scope_dir, run_id, delivery_dir, validation_status, checks)

    diagnostics = build_science_readiness_report(
        processed_tables,
        inspection=inspection,
        validation_status=validation_status,
        nested_report=nested_report,
        scope=registry_scope,
    )
    skip_plots = args.skip_plots and not args.plots
    figures = [] if skip_plots else generate_smoke_diagnostic_plots(processed_tables, inspection, output_dir / "figures")
    diagnostics["figures"] = figures
    write_json(diagnostics, output_dir / f"{output_scope_dir}_diagnostics.json", enforce_allowed_roots=False)
    (output_dir / _diagnostics_filename(registry_scope)).write_text(diagnostics["markdown"], encoding="utf-8")

    ingestion_report = render_ingestion_report(run_id, inspection, artifacts)
    if registry_scope == "full_night_all_alerts":
        ingestion_report += "\nFull-night all-alert scope is recorded; population claims still require validation review.\n"
    else:
        ingestion_report += "\nSmoke delivery is tag-filtered and not a full-night all-alert dataset. No completeness claim allowed.\n"
    (output_dir / _ingestion_filename(registry_scope)).write_text(ingestion_report, encoding="utf-8")
    _write_latest_run(paths["reports_dir"], output_scope_dir, run_id, output_dir, processed_dir, validation_status)

    print(f"Data Transfer pipeline run: {run_id}")
    print(f"Scope: {registry_scope}")
    print(f"Raw files: {inspection['file_count']}")
    print(f"Raw rows: {inspection['total_rows']}")
    print(f"Processed table shapes: {table_shapes(processed_tables)}")
    print(f"Validation status: {validation_status}")
    print(f"Reports: {output_dir}")
    return 0


def _metadata_for_scope(config: dict, paths: dict[str, Path], scope: str, entry: dict | None) -> dict:
    fallback = {
        **SMOKE_DELIVERY_METADATA,
        "utc_start": config.get("target_startdate"),
        "utc_stop": config.get("target_stopdate"),
        "completeness_scope": COMPLETENESS_SCOPE,
    }
    if scope in {"full_night", "full_night_all_alerts"}:
        fallback.update(
            {
                "topic": None,
                "survey": str(config.get("survey", "lsst")).lower(),
                "filter": None,
                "scope": "full_night",
                "all_alerts": True,
                "raw_delivery_dir": str(paths["raw_delivery_dir"] / "full_night"),
            }
        )
    elif scope in {"full_week", "full_week_full_packet"}:
        fallback.update(
            {
                "topic": None,
                "survey": str(config.get("survey", "lsst")).lower(),
                "filter": None,
                "scope": scope,
                "all_alerts": True,
                "raw_delivery_dir": str(paths["raw_delivery_dir"] / scope),
            }
        )
    return metadata_from_topic_entry(entry, fallback=fallback)


def _delivery_dir(explicit: str | None, paths: dict[str, Path], scope: str, metadata: dict) -> Path:
    if explicit:
        return Path(explicit).resolve()
    if metadata.get("raw_delivery_dir"):
        return Path(metadata["raw_delivery_dir"]).resolve()
    if scope == "full_night_all_alerts":
        return find_latest_delivery_dir(paths["raw_delivery_dir"] / "full_night")
    return find_latest_delivery_dir(paths["raw_delivery_dir"] / "smoke_delivery")


def _write_inspection_artifacts(reports_dir: Path, output_dir: Path, scope_dir: str, inspection: dict) -> None:
    write_json(inspection, reports_dir / "delivery_inspection.json", enforce_allowed_roots=False)
    (reports_dir / "DELIVERY_INSPECTION.md").write_text(render_delivery_inspection_report(inspection), encoding="utf-8")
    write_json(inspection, output_dir / f"{scope_dir}_delivery_inspection.json", enforce_allowed_roots=False)
    write_json(inspection, output_dir / "raw_field_inventory.json", enforce_allowed_roots=False)
    (output_dir / _inspection_filename(scope_dir)).write_text(render_delivery_inspection_report(inspection), encoding="utf-8")
    (output_dir / "RAW_FIELD_INVENTORY.md").write_text(render_raw_field_inventory(inspection), encoding="utf-8")


def _write_validation(output_dir: Path, scope_dir: str, run_id: str, delivery_dir: Path, validation_status: str, checks: list[dict]) -> None:
    payload = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "raw_delivery_dir": str(delivery_dir),
        "validation_status": validation_status,
        "checks": checks,
    }
    write_json(payload, output_dir / "validation_report.json", enforce_allowed_roots=False)
    write_json(payload, output_dir / f"{scope_dir}_validation_report.json", enforce_allowed_roots=False)
    (output_dir / "VALIDATION_SUMMARY.md").write_text(render_validation_summary(checks), encoding="utf-8")
    (output_dir / _validation_filename(scope_dir)).write_text(render_validation_summary(checks), encoding="utf-8")


def _write_latest_run(reports_dir: Path, scope_dir: str, run_id: str, output_dir: Path, processed_dir: Path, validation_status: str) -> None:
    write_json(
        {
            "run_id": run_id,
            "output_dir": str(output_dir),
            "processed_dir": str(processed_dir),
            "manifest_path": str(processed_dir / "manifest.json"),
            "validation_status": validation_status,
        },
        reports_dir / scope_dir / "latest_run.json",
        enforce_allowed_roots=False,
    )


def _scope_output_dir(scope: str) -> str:
    if scope in {"full_week", "full_week_full_packet"}:
        return scope
    return "full_night" if scope in {"full_night", "full_night_all_alerts"} else "smoke_delivery"


def _inspection_filename(scope_dir: str) -> str:
    return "FULL_NIGHT_DELIVERY_INSPECTION.md" if scope_dir == "full_night" else "SMOKE_DELIVERY_INSPECTION.md"


def _validation_filename(scope_dir: str) -> str:
    return "FULL_NIGHT_VALIDATION_SUMMARY.md" if scope_dir == "full_night" else "SMOKE_VALIDATION_SUMMARY.md"


def _diagnostics_filename(scope: str) -> str:
    return "FULL_NIGHT_DIAGNOSTICS.md" if scope == "full_night_all_alerts" else "SMOKE_DIAGNOSTICS.md"


def _ingestion_filename(scope: str) -> str:
    return "FULL_NIGHT_INGESTION_REPORT.md" if scope == "full_night_all_alerts" else "SMOKE_INGESTION_REPORT.md"


if __name__ == "__main__":
    raise SystemExit(main())

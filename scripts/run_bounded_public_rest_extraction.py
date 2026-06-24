#!/usr/bin/env python
"""Run a bounded public REST extraction feasibility test."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from fink_lsst.api import FinkApiClient
from fink_lsst.bounded_extraction import (
    build_bounded_extraction_manifest,
    compare_to_statistics,
    extract_bounded_tag_sample,
    extract_fp_for_ids,
    extract_object_details_for_ids,
    extract_source_details_for_ids,
)
from fink_lsst.bounded_validation import render_validation_summary, validate_bounded_extraction
from fink_lsst.config import load_config
from fink_lsst.endpoint_capabilities import (
    analyze_endpoint_capabilities,
    capability_by_endpoint,
    render_capabilities_markdown,
)
from fink_lsst.feasibility import generate_feasibility_report, render_feasibility_markdown
from fink_lsst.normalize import (
    normalize_forced_photometry,
    normalize_objects,
    normalize_sources,
    normalize_statistics,
    normalize_tags_or_classifications,
    payload_to_dataframe,
)
from fink_lsst.storage import read_json, safe_artifact_path, write_json, write_parquet


def main() -> int:
    """Run bounded extraction and save raw, processed, validation, and feasibility artifacts."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/local_smoke_test.yaml")
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    config = load_config(project_root / args.config)
    bounded_config = dict((config.smoke_test or {}))
    with (project_root / args.config).open("r", encoding="utf-8") as handle:
        raw_config = yaml.safe_load(handle) or {}
    bounded_config.update(raw_config.get("bounded_extraction", {}))
    client = FinkApiClient(config=config, timeout_seconds=bounded_config.get("request_timeout_seconds"))
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    raw_dir = project_root / "data/raw/bounded_extraction" / run_id
    processed_dir = project_root / "data/processed/bounded_extraction" / run_id
    output_dir = project_root / "outputs/bounded_extraction" / run_id
    raw_dir.mkdir(parents=True, exist_ok=True)
    processed_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    contract_summary = read_json(project_root / "data/fixtures/fink_lsst_api_contract_summary.json")
    capabilities = analyze_endpoint_capabilities(contract_summary)
    capabilities_by_endpoint = capability_by_endpoint(capabilities)
    write_json(capabilities, output_dir / "endpoint_capabilities.json", enforce_allowed_roots=False)
    (output_dir / "endpoint_capabilities.md").write_text(render_capabilities_markdown(capabilities), encoding="utf-8")
    write_json(capabilities, safe_artifact_path("bounded_extraction/endpoint_capabilities.json", root_name="outputs", project_root=project_root), project_root=project_root)
    safe_artifact_path("bounded_extraction/endpoint_capabilities.md", root_name="outputs", project_root=project_root).write_text(render_capabilities_markdown(capabilities), encoding="utf-8")

    artifacts: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []

    tag_diagnostic = extract_bounded_tag_sample(client, bounded_config, capabilities_by_endpoint.get("tags"))
    tag_data = tag_diagnostic.get("data")
    if tag_diagnostic.get("ok"):
        tag_raw_path = raw_dir / "tag_sample_response.json"
        write_json(tag_data, tag_raw_path, enforce_allowed_roots=False)
        tag_diagnostic["output_file"] = str(tag_raw_path)
        tag_diagnostic["response_saved"] = True
        artifacts.append({"name": "tag_sample_raw", "path": str(tag_raw_path)})
    tag_diagnostic.pop("data", None)
    attempts.append(tag_diagnostic)

    tag_rows_df = normalize_sources(tag_data if tag_data is not None else [])
    object_ids = []
    if "internal_object_id" in tag_rows_df.columns:
        object_ids = tag_rows_df["internal_object_id"].dropna().astype(str).unique().tolist()
    object_ids = object_ids[: int(bounded_config.get("max_detail_objects", 10))]

    detail_results: dict[str, list[dict[str, Any]]] = {"objects": [], "sources": [], "forced_photometry": []}
    if bounded_config.get("enable_objects", True):
        detail_results["objects"] = extract_object_details_for_ids(client, object_ids, bounded_config)
    if bounded_config.get("enable_sources", True):
        detail_results["sources"] = extract_source_details_for_ids(client, object_ids, bounded_config)
    if bounded_config.get("enable_forced_photometry", True):
        detail_results["forced_photometry"] = extract_fp_for_ids(client, object_ids, bounded_config)

    raw_payloads: dict[str, list[Any]] = {"objects": [], "sources": [], "forced_photometry": []}
    for table_name, diagnostics in detail_results.items():
        for index, diagnostic in enumerate(diagnostics):
            payload = diagnostic.get("data")
            if diagnostic.get("ok"):
                raw_payloads[table_name].append(payload)
                raw_path = raw_dir / f"{table_name}_{index:03d}.json"
                write_json(payload, raw_path, enforce_allowed_roots=False)
                diagnostic["output_file"] = str(raw_path)
                diagnostic["response_saved"] = True
                artifacts.append({"name": f"{table_name}_{index:03d}_raw", "path": str(raw_path)})
            diagnostic.pop("data", None)
            attempts.append(diagnostic)

    stats_payload = None
    try:
        stats_payload = read_json(project_root / "data/fixtures/fink_lsst_statistics_sample.json")
        write_json(stats_payload, raw_dir / "statistics_comparison_payload.json", enforce_allowed_roots=False)
    except FileNotFoundError:
        stats_payload = None

    tables = {
        "tag_rows": tag_rows_df,
        "object_summary": _object_summary(tag_rows_df),
        "objects": normalize_objects(raw_payloads["objects"]),
        "sources": normalize_sources(raw_payloads["sources"]),
        "forced_photometry": normalize_forced_photometry(raw_payloads["forced_photometry"]),
        "statistics": normalize_statistics(stats_payload if stats_payload is not None else []),
    }
    extraction_summary = _summary(tables, attempts, object_ids, stats_payload)
    extraction_summary["statistics_comparison"] = compare_to_statistics(extraction_summary, stats_payload)

    for table_name, df in tables.items():
        parquet_path = processed_dir / f"{table_name}.parquet"
        write_parquet(df, parquet_path, project_root=project_root)
        artifacts.append({"name": f"{table_name}_parquet", "path": str(parquet_path)})

    write_json(attempts, output_dir / "endpoint_attempts.json", enforce_allowed_roots=False)
    write_json(extraction_summary, processed_dir / "extraction_summary.json", enforce_allowed_roots=False)

    validation_tables = {
        "sources": tables["tag_rows"] if tables["sources"].empty else tables["sources"],
        "object_summary": tables["object_summary"],
        "objects": tables["objects"],
        "forced_photometry": tables["forced_photometry"],
        "statistics": tables["statistics"],
        "tags": normalize_tags_or_classifications(read_json(project_root / "data/fixtures/fink_lsst_tags.json")),
    }
    validation_checks = validate_bounded_extraction(validation_tables, extraction_summary, attempts)
    validation_report = {"created_at_utc": datetime.now(timezone.utc).isoformat(), "checks": validation_checks}
    write_json(validation_report, output_dir / "validation_report.json", enforce_allowed_roots=False)
    (output_dir / "validation_summary.md").write_text(render_validation_summary(validation_checks), encoding="utf-8")

    feasibility = generate_feasibility_report(extraction_summary, validation_checks, capabilities)
    write_json(feasibility, output_dir / "feasibility_report.json", enforce_allowed_roots=False)
    (output_dir / "FEASIBILITY_REPORT.md").write_text(render_feasibility_markdown(feasibility), encoding="utf-8")

    manifest = build_bounded_extraction_manifest(run_id, client.base_url, bounded_config, attempts, artifacts, extraction_summary)
    write_json(manifest, processed_dir / "manifest.json", enforce_allowed_roots=False)
    write_json(manifest, output_dir / "manifest.json", enforce_allowed_roots=False)
    latest = {
        "run_id": run_id,
        "raw_dir": str(raw_dir),
        "processed_dir": str(processed_dir),
        "output_dir": str(output_dir),
        "manifest": str(output_dir / "manifest.json"),
        "feasibility_report": str(output_dir / "FEASIBILITY_REPORT.md"),
    }
    write_json(latest, project_root / "outputs/bounded_extraction/latest_run.json", enforce_allowed_roots=False)
    print(f"Wrote bounded extraction run: {run_id}")
    print(f"Output directory: {output_dir}")
    print(f"Tag rows: {extraction_summary['tag_rows']}")
    return 0


def _object_summary(tag_rows_df: pd.DataFrame) -> pd.DataFrame:
    """Build a compact per-object summary from the bounded tag/sample rows."""
    if tag_rows_df.empty or "internal_object_id" not in tag_rows_df.columns:
        return pd.DataFrame(
            columns=[
                "internal_table_type",
                "internal_object_id",
                "row_count",
                "source_count",
                "first_time_mjd",
                "last_time_mjd",
                "bands",
                "ra",
                "dec",
                "_unmapped_fields",
            ]
        )

    df = tag_rows_df.copy()
    df = df[df["internal_object_id"].notna()]
    if df.empty:
        return _object_summary(pd.DataFrame())

    aggregations: dict[str, Any] = {"row_count": ("internal_object_id", "size")}
    if "internal_source_id" in df.columns:
        aggregations["source_count"] = ("internal_source_id", pd.Series.nunique)
    else:
        aggregations["source_count"] = ("internal_object_id", "size")
    if "time_mjd" in df.columns:
        aggregations["first_time_mjd"] = ("time_mjd", "min")
        aggregations["last_time_mjd"] = ("time_mjd", "max")
    if "ra" in df.columns:
        aggregations["ra"] = ("ra", "first")
    if "dec" in df.columns:
        aggregations["dec"] = ("dec", "first")

    summary = df.groupby("internal_object_id", dropna=True).agg(**aggregations).reset_index()
    if "band" in df.columns:
        bands = (
            df.groupby("internal_object_id")["band"]
            .apply(lambda values: ",".join(sorted({str(value) for value in values.dropna()})))
            .reset_index(name="bands")
        )
        summary = summary.merge(bands, on="internal_object_id", how="left")
    else:
        summary["bands"] = ""
    for column in ("first_time_mjd", "last_time_mjd", "ra", "dec"):
        if column not in summary.columns:
            summary[column] = pd.NA
    summary.insert(0, "internal_table_type", "object_summary")
    summary["_unmapped_fields"] = "[]"
    ordered = [
        "internal_table_type",
        "internal_object_id",
        "row_count",
        "source_count",
        "first_time_mjd",
        "last_time_mjd",
        "bands",
        "ra",
        "dec",
        "_unmapped_fields",
    ]
    return summary[ordered]


def _summary(
    tables: dict[str, pd.DataFrame],
    attempts: list[dict[str, Any]],
    object_ids: list[str],
    stats_payload: Any,
) -> dict[str, Any]:
    detail = {}
    for name in ("objects", "sources", "forced_photometry"):
        matching = [attempt for attempt in attempts if attempt.get("endpoint") == name]
        if name == "forced_photometry":
            matching = [attempt for attempt in attempts if attempt.get("endpoint") == "fp"]
        detail[name] = {"attempted": len(matching), "succeeded": sum(1 for attempt in matching if attempt.get("ok"))}
    tag_df = tables["tag_rows"]
    tag_object_ids = tag_df["internal_object_id"].dropna().astype(str).unique().tolist() if "internal_object_id" in tag_df.columns else []
    source_ids = tag_df["internal_source_id"].dropna().astype(str).unique().tolist() if "internal_source_id" in tag_df.columns else []
    return {
        "tag_rows": int(len(tag_df)),
        "unique_object_ids": len(tag_object_ids),
        "detail_object_count": len(object_ids),
        "unique_source_ids": len(source_ids),
        "object_ids": object_ids,
        "tag_object_ids": tag_object_ids[:100],
        "source_ids": source_ids[:100],
        "detail_attempts": detail,
        "table_shapes": {name: {"rows": int(len(df)), "columns": int(len(df.columns))} for name, df in tables.items()},
        "statistics_payload_available": stats_payload is not None,
    }


if __name__ == "__main__":
    raise SystemExit(main())

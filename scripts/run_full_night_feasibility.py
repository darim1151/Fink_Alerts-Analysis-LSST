#!/usr/bin/env python
"""Run REST full-night completeness feasibility with safe partitioning."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from fink_lsst.api import FinkApiClient
from fink_lsst.completeness_capabilities import (
    diagnose_completeness_capabilities,
    render_capability_diagnosis_markdown,
)
from fink_lsst.config import load_config
from fink_lsst.feasibility import decide_full_night_feasibility, render_full_night_feasibility_markdown
from fink_lsst.full_night_extraction import (
    build_completeness_accounting,
    build_full_night_manifest,
    deduplicate_rows,
    run_partitioned_extraction,
    summarize_partition_results,
    write_full_night_artifacts,
)
from fink_lsst.full_night_validation import render_full_night_validation_summary, validate_full_night_run
from fink_lsst.normalize import normalize_sources, payload_to_dataframe
from fink_lsst.partitioning import build_partition_plan, refine_capped_partitions
from fink_lsst.storage import read_json, write_json
from fink_lsst.time_windows import build_utc_date_window, validate_alert_start_date, validate_utc_timezone


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/local_smoke_test.yaml")
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    fink_config = load_config(project_root / args.config)
    full_config = _load_full_config(project_root / args.config)
    bounded_columns = _load_bounded_columns(project_root / args.config)
    full_config["columns"] = {"tag_rows": bounded_columns.get("tag_rows")}
    _validate_full_config(full_config)

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    raw_dir = project_root / "data/raw/full_night_feasibility" / run_id
    raw_partition_dir = raw_dir / "partitions"
    processed_dir = project_root / "data/processed/full_night_feasibility" / run_id
    output_dir = project_root / "outputs/full_night_feasibility" / run_id
    raw_partition_dir.mkdir(parents=True, exist_ok=True)
    processed_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    contract_summary = read_json(project_root / "data/fixtures/fink_lsst_api_contract_summary.json")
    capability_diagnosis = diagnose_completeness_capabilities(contract_summary)
    write_json(capability_diagnosis, output_dir / "capability_diagnosis.json", enforce_allowed_roots=False)
    (output_dir / "capability_diagnosis.md").write_text(
        render_capability_diagnosis_markdown(capability_diagnosis),
        encoding="utf-8",
    )
    latest_capability_json = project_root / "outputs/full_night_feasibility/capability_diagnosis.json"
    latest_capability_md = project_root / "outputs/full_night_feasibility/capability_diagnosis.md"
    write_json(capability_diagnosis, latest_capability_json, enforce_allowed_roots=False)
    latest_capability_md.write_text(render_capability_diagnosis_markdown(capability_diagnosis), encoding="utf-8")

    client = FinkApiClient(config=fink_config, timeout_seconds=full_config.get("request_timeout_seconds"))
    statistics_payload, statistics_diagnostic = _get_statistics_payload(client, full_config, raw_dir, project_root)
    write_json(statistics_diagnostic, output_dir / "statistics_diagnostic.json", enforce_allowed_roots=False)

    partition_plan = build_partition_plan(full_config, capability_diagnosis)
    partition_results = run_partitioned_extraction(client, full_config, partition_plan, raw_partition_dir)
    refinement_decisions = []
    while (
        any(result.get("status") == "possibly_truncated" for result in partition_results)
        and len(partition_plan.get("partitions", [])) < int(full_config.get("time_partitions", {}).get("max_count", 96))
        and summarize_partition_results(partition_results)["total_raw_rows"] < int(full_config.get("max_total_rows_safety", 5000))
    ):
        refined = refine_capped_partitions(partition_plan["partitions"], partition_results, full_config)
        if not refined:
            refinement_decisions.append({"action": "stop", "reason": "No further supported refinement available"})
            break
        refinement_decisions.append({"action": "refine_time", "new_partition_count": len(refined)})
        refined_plan = dict(partition_plan)
        refined_plan["partitions"] = refined
        refined_results = run_partitioned_extraction(client, full_config, refined_plan, raw_partition_dir)
        partition_plan["partitions"] = refined
        partition_plan["partition_count"] = len(refined)
        partition_results = refined_results
        if not any(result.get("status") == "possibly_truncated" for result in partition_results):
            break
        if len(refined) >= int(full_config.get("time_partitions", {}).get("max_count", 96)):
            refinement_decisions.append({"action": "stop", "reason": "Maximum partition count reached"})
            break

    all_rows = [row for result in partition_results for row in result.get("rows", [])]
    partition_results_for_record = [_without_embedded_rows(result) for result in partition_results]
    dedup = deduplicate_rows(all_rows)
    dedup_frame = normalize_sources(dedup["rows"])
    accounting = build_completeness_accounting(partition_results_for_record, statistics_payload, full_config)
    accounting["deduplication"] = {key: value for key, value in dedup.items() if key != "rows"}
    accounting["refinement_decisions"] = refinement_decisions

    processed_artifacts = write_full_night_artifacts(processed_dir, all_rows, partition_results_for_record, accounting, project_root)
    write_json(partition_plan, output_dir / "partition_plan.json", enforce_allowed_roots=False)
    write_json(partition_results_for_record, output_dir / "partition_results.json", enforce_allowed_roots=False)
    write_json(accounting, output_dir / "completeness_accounting.json", enforce_allowed_roots=False)

    validation_checks = validate_full_night_run(full_config, partition_plan, partition_results_for_record, dedup_frame, accounting)
    decision = decide_full_night_feasibility(capability_diagnosis, partition_results_for_record, validation_checks, accounting)
    validation_checks = validate_full_night_run(
        full_config,
        partition_plan,
        partition_results_for_record,
        dedup_frame,
        accounting,
        proposed_decision=decision["rest_full_night_complete"],
    )
    decision = decide_full_night_feasibility(capability_diagnosis, partition_results_for_record, validation_checks, accounting)

    validation_report = {"created_at_utc": datetime.now(timezone.utc).isoformat(), "checks": validation_checks}
    write_json(validation_report, output_dir / "validation_report.json", enforce_allowed_roots=False)
    (output_dir / "validation_summary.md").write_text(render_full_night_validation_summary(validation_checks), encoding="utf-8")
    write_json(decision, output_dir / "full_night_decision.json", enforce_allowed_roots=False)
    (output_dir / "FULL_NIGHT_FEASIBILITY_REPORT.md").write_text(render_full_night_feasibility_markdown(decision), encoding="utf-8")

    artifacts = {
        **processed_artifacts,
        "capability_diagnosis_json": str(output_dir / "capability_diagnosis.json"),
        "partition_plan_json": str(output_dir / "partition_plan.json"),
        "partition_results_json": str(output_dir / "partition_results.json"),
        "validation_report_json": str(output_dir / "validation_report.json"),
        "full_night_report_md": str(output_dir / "FULL_NIGHT_FEASIBILITY_REPORT.md"),
    }
    manifest = build_full_night_manifest(
        run_id,
        client.base_url,
        full_config,
        partition_plan,
        partition_results_for_record,
        artifacts,
        decision,
    )
    write_json(manifest, output_dir / "manifest.json", enforce_allowed_roots=False)
    write_json(manifest, processed_dir / "manifest.json", enforce_allowed_roots=False)
    latest = {
        "run_id": run_id,
        "raw_dir": str(raw_dir),
        "processed_dir": str(processed_dir),
        "output_dir": str(output_dir),
        "manifest": str(output_dir / "manifest.json"),
        "report": str(output_dir / "FULL_NIGHT_FEASIBILITY_REPORT.md"),
    }
    write_json(latest, project_root / "outputs/full_night_feasibility/latest_run.json", enforce_allowed_roots=False)

    summary = summarize_partition_results(partition_results_for_record)
    print(f"Wrote full-night feasibility run: {run_id}")
    print(f"Target UTC window: {full_config['target_startdate']} to {full_config['target_stopdate']}")
    print(f"Completeness scope attempted: {partition_plan.get('completeness_scope')}")
    print(f"Partition summary: {summary}")
    print(f"Decision: rest_full_night_complete = {decision['rest_full_night_complete']}")
    return 0


def _load_full_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    return dict(raw.get("full_night_feasibility", {}) or {})


def _load_bounded_columns(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    return dict((raw.get("bounded_extraction", {}) or {}).get("columns", {}) or {})


def _validate_full_config(config: dict[str, Any]) -> None:
    required = ["target_startdate", "target_stopdate", "min_lsst_alert_date_utc"]
    missing = [key for key in required if not config.get(key)]
    if missing:
        raise ValueError(f"Missing full_night_feasibility config keys: {missing}")
    validate_utc_timezone(config)
    if config.get("reject_pre_alert_dates", True):
        validate_alert_start_date(config["target_startdate"], config["min_lsst_alert_date_utc"])
    build_utc_date_window(config["target_startdate"], config["target_stopdate"])
    if int(config.get("max_rows_per_partition", 0)) <= 0:
        raise ValueError("full_night_feasibility.max_rows_per_partition must be positive")
    if int(config.get("max_total_rows_safety", 0)) <= 0:
        raise ValueError("full_night_feasibility.max_total_rows_safety must be positive")
    if not config.get("api_date_window_convention"):
        raise ValueError("full_night_feasibility.api_date_window_convention must be recorded")


def _get_statistics_payload(
    client: FinkApiClient,
    config: dict[str, Any],
    raw_dir: Path,
    project_root: Path,
) -> tuple[Any, dict[str, Any]]:
    payload = {"date": config["target_startdate"], "output-format": config.get("output_format", "json")}
    diagnostic = client.probe_endpoint("statistics", method="POST", payload=payload)
    diagnostic["name"] = "target_date_statistics"
    if diagnostic.get("ok"):
        write_json(diagnostic.get("data"), raw_dir / "statistics_target_date.json", enforce_allowed_roots=False)
        diagnostic["output_file"] = str(raw_dir / "statistics_target_date.json")
        data = diagnostic.pop("data")
        diagnostic["statistics_source"] = "live_target_date"
        return data, diagnostic
    try:
        data = read_json(project_root / "data/fixtures/fink_lsst_statistics_sample.json")
        diagnostic.pop("data", None)
        diagnostic["statistics_source"] = "fixture_fallback"
        diagnostic["warning"] = "Target-date statistics request failed; fixture may not match target UTC date/window."
        return data, diagnostic
    except FileNotFoundError:
        diagnostic.pop("data", None)
        diagnostic["statistics_source"] = "unavailable"
        diagnostic["warning"] = "No statistics fixture available; target date needs verification."
        return None, diagnostic


def _without_embedded_rows(result: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(result)
    cleaned.pop("rows", None)
    return cleaned


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python
"""Run safe manifest-driven analysis stages."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

from fink_lsst.bulk_transfer.diagnostics import build_run_diagnostics, write_diagnostics_report
from fink_lsst.bulk_transfer.raw_audit import build_raw_audit
from fink_lsst.bulk_transfer.raw_readiness import classify_raw_readiness
from fink_lsst.bulk_transfer.run_ingestion import IngestionOptions, ingest_run
from fink_lsst.bulk_transfer.run_manifest import load_run_manifest, resolve_run_paths, validate_run_manifest
from fink_lsst.bulk_transfer.validation import validate_run_outputs, write_run_validation_report
from fink_lsst.data_root import DataRootError, PathConfinementError, is_external_data_root, resolve_data_root


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SUPPORTED_STAGES = {"validate_manifest", "triage", "inspect_raw", "next_action", "ingest", "validate", "diagnostics", "all"}
# Legacy helper scripts that still resolve data against the repository checkout.
REPO_ROOTED_STAGES = {"triage", "inspect_raw", "next_action"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-config", required=True)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-partial", action="store_true")
    parser.add_argument("--max-files", type=int)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--skip-plots", action="store_true")
    parser.add_argument("--continue-on-file-error", action="store_true")
    parser.add_argument("--write-report", action="store_true")
    args = parser.parse_args()
    if args.stage not in SUPPORTED_STAGES:
        print("ingestion is not implemented in the unified runner yet; use existing guarded pipeline or wait for Checkpoint 9B")
        return 2

    try:
        data_root = resolve_data_root(repo_root=PROJECT_ROOT)
    except DataRootError as exc:
        print(f"error: {exc}")
        return 2
    print(f"data_root: {data_root}", file=sys.stderr)
    if args.stage in REPO_ROOTED_STAGES and is_external_data_root(data_root, PROJECT_ROOT):
        print(f"error: stage {args.stage!r} still reads data from the repository checkout; unset FINK_LSST_DATA_ROOT to run it")
        return 2

    run_config = _abs(args.run_config)
    manifest = load_run_manifest(run_config)
    errors, warnings = validate_run_manifest(manifest, project_root=data_root)
    for warning in warnings:
        print(f"warning: {warning}")
    if errors:
        for error in errors:
            print(f"error: {error}")
        return 1
    try:
        paths = resolve_run_paths(manifest, data_root)
    except PathConfinementError as exc:
        print(f"error: {exc}")
        return 2

    if args.dry_run and args.stage not in {"ingest", "all"}:
        print(json.dumps(_dry_run_plan(manifest, run_config, args.stage, data_root, paths), indent=2, sort_keys=True))
        return 0

    if args.stage == "validate_manifest":
        print(json.dumps(_dry_run_plan(manifest, run_config, args.stage, data_root, paths), indent=2, sort_keys=True))
        return 0

    if args.stage in {"ingest", "all"}:
        try:
            result = _run_ingest(manifest, args, data_root)
        except PathConfinementError as exc:
            print(f"error: {exc}")
            return 2
        compact = _compact_result(result)
        if args.dry_run:
            compact["dry_run_plan"] = _dry_run_plan(manifest, run_config, args.stage, data_root, paths)
        print(json.dumps(compact, indent=2, sort_keys=True))
        if result.get("status") == "dry_run":
            return 0
        if result.get("status") == "refused":
            return 2
        if args.stage == "all":
            validation_status = _run_validate_from_ingestion(manifest, result, paths)
            diagnostics_status = _run_diagnostics_from_ingestion(manifest, result, paths)
            print(json.dumps({"validation": validation_status, "diagnostics": diagnostics_status}, indent=2, sort_keys=True))
        return 0

    if args.stage == "validate":
        status = _run_validate_existing(manifest, paths)
        print(json.dumps(status, indent=2, sort_keys=True))
        return 0 if status.get("status") != "failed" else 1

    if args.stage == "diagnostics":
        status = _run_diagnostics_existing(manifest, paths)
        print(json.dumps(status, indent=2, sort_keys=True))
        return 0

    command = build_stage_command(args.stage, run_config, args.write_report)
    print("stage_command: " + " ".join(command))
    if args.dry_run:
        print("dry_run: command not executed")
        return 0
    completed = subprocess.run(command, cwd=PROJECT_ROOT, text=True, check=False)
    return completed.returncode


def build_stage_command(stage: str, run_config: Path, write_report: bool) -> list[str]:
    if stage == "triage":
        command = [sys.executable, "scripts/triage_full_packet_download.py", "--run-config", str(run_config)]
        if write_report:
            command.append("--write-report")
        return command
    if stage == "inspect_raw":
        return [sys.executable, "scripts/inspect_full_packet_delivery.py", "--run-config", str(run_config)]
    if stage == "next_action":
        return [sys.executable, "scripts/report_data_transfer_next_action.py", "--run-config", str(run_config)]
    raise ValueError(stage)


def _run_ingest(manifest, args, data_root: Path) -> dict:
    options = IngestionOptions(
        allow_partial=args.allow_partial,
        max_files=args.max_files,
        resume=args.resume,
        force=args.force,
        skip_plots=args.skip_plots,
        continue_on_file_error=args.continue_on_file_error,
        write_report=args.write_report,
        dry_run=args.dry_run,
    )
    return ingest_run(manifest, data_root, options)


def _run_validate_from_ingestion(manifest, ingestion_result: dict, paths: dict[str, Path]) -> dict:
    summary = ingestion_result.get("summary") or {}
    output_dir = summary.get("output_dir")
    if not output_dir:
        return {"status": "skipped", "reason": "no ingestion output directory"}
    raw_audit = build_raw_audit(paths["raw_dir"])
    report = validate_run_outputs(
        manifest,
        summary.get("processed_artifacts", {}),
        raw_audit,
        ingestion_result.get("execution_manifest", {}),
    )
    write_run_validation_report(report, output_dir)
    return {"status": report.get("validation_status"), "output_dir": output_dir}


def _run_diagnostics_from_ingestion(manifest, ingestion_result: dict, paths: dict[str, Path]) -> dict:
    summary = ingestion_result.get("summary") or {}
    output_dir = summary.get("output_dir")
    if not output_dir:
        return {"status": "skipped", "reason": "no ingestion output directory"}
    tables = {}
    for name, path in (summary.get("processed_artifacts") or {}).items():
        candidate = Path(path)
        if candidate.exists() and candidate.suffix == ".parquet":
            tables[name] = pd.read_parquet(candidate)
    raw_audit = build_raw_audit(paths["raw_dir"])
    report = build_run_diagnostics(manifest, tables, raw_audit=raw_audit)
    write_diagnostics_report(report, output_dir)
    return {"status": "written", "output_dir": output_dir}


def _run_validate_existing(manifest, paths: dict[str, Path]) -> dict:
    processed_dir = paths["processed_dir"]
    output_dir = paths["outputs_dir"]
    processed_paths = {path.stem: str(path) for path in processed_dir.glob("*.parquet")} if processed_dir.exists() else {}
    raw_audit = build_raw_audit(paths["raw_dir"])
    report = validate_run_outputs(manifest, processed_paths, raw_audit, {})
    write_run_validation_report(report, output_dir)
    return {"status": report.get("validation_status"), "output_dir": str(output_dir), "tables": sorted(processed_paths)}


def _run_diagnostics_existing(manifest, paths: dict[str, Path]) -> dict:
    processed_dir = paths["processed_dir"]
    output_dir = paths["outputs_dir"]
    tables = {
        path.stem: pd.read_parquet(path)
        for path in processed_dir.glob("*.parquet")
        if path.exists()
    } if processed_dir.exists() else {}
    raw_audit = build_raw_audit(paths["raw_dir"])
    report = build_run_diagnostics(manifest, tables, raw_audit=raw_audit)
    write_diagnostics_report(report, output_dir)
    return {"status": "written", "output_dir": str(output_dir), "tables": sorted(tables)}


def _compact_result(result: dict) -> dict:
    compact = dict(result)
    if "raw_audit" in compact:
        compact["raw_audit"] = {
            "exists": compact["raw_audit"].get("exists"),
            "file_count": compact["raw_audit"].get("file_count"),
        }
    if "execution_manifest" in compact:
        compact["execution_manifest"] = {
            "run_context": compact["execution_manifest"].get("run_context"),
            "validation_status": compact["execution_manifest"].get("validation_status"),
            "completeness_status": compact["execution_manifest"].get("completeness_status"),
        }
    return compact


def _dry_run_plan(manifest, run_config: Path, stage: str, data_root: Path, paths: dict[str, Path]) -> dict:
    raw_audit = build_raw_audit(paths["raw_dir"])
    readiness = classify_raw_readiness(
        raw_audit,
        expected_total=manifest.download_evidence.expected_total_messages,
        progress_from_terminal=manifest.download_evidence.terminal_progress_messages,
    )
    blocked_states = {
        "raw_missing",
        "download_active",
        "partial_download",
        "partial_with_errors",
        "blocked_corrupt_raw",
        "blocked_disk_risk",
    }
    allowed = stage not in {"ingest", "all"} or readiness["state"] not in blocked_states
    claim_state = {
        "all_alert_completeness": manifest.claim_state.all_alert_completeness,
        "night_completeness": manifest.claim_state.night_completeness,
        "week_completeness": manifest.claim_state.week_completeness,
    }
    return {
        "dry_run": True,
        "stage": stage,
        "run_name": manifest.run_name,
        "run_config": str(run_config),
        "data_root": str(data_root),
        "topic": manifest.topic,
        "date_window": {"startdate": manifest.startdate, "stopdate": manifest.stopdate},
        "scope": manifest.scope,
        "packet_type": manifest.packet_type,
        "expected_nights": manifest.expected_nights,
        "paths": {
            "raw_dir": manifest.paths.raw_dir,
            "processed_dir": manifest.paths.processed_dir,
            "outputs_dir": manifest.paths.outputs_dir,
        },
        "resolved_paths": {name: str(path) for name, path in paths.items()},
        "raw_state": readiness["state"],
        "stage_allowed": allowed,
        "blocked_reason": None if allowed else readiness["reason"],
        "claim_state": claim_state,
        "claim_note": "completeness claims remain blocked/unresolved unless strict validation updates them",
        "writes_outputs": False,
    }


def _abs(path: str) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else PROJECT_ROOT / candidate


if __name__ == "__main__":
    raise SystemExit(main())

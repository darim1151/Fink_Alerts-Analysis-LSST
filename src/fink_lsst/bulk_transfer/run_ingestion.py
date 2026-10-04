"""Manifest-driven guarded ingestion for Fink Data Transfer runs."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from fink_lsst.data_root import confine_tree, storage_base, validate_path_component

from .execution import (
    ExecutionManifest,
    FileProcessingResult,
    ProcessingStage,
    RunContext,
    load_or_create_execution_manifest,
    mark_stage_completed,
    mark_stage_failed,
    mark_stage_started,
    record_file_result,
    should_skip_completed_file,
)
from .ingest import write_bulk_processed_tables
from .nightly_split import summarize_nightly_counts, write_nightly_tables
from .output_inspector import classify_file_type, discover_delivery_files
from .raw_audit import build_raw_audit
from .raw_readiness import classify_raw_readiness
from .run_manifest import RunManifest, manifest_to_dict, resolve_run_paths
from .table_builder import build_tables_for_run


BLOCKED_RAW_STATES = {
    "raw_missing",
    "download_active",
    "partial_download",
    "partial_with_errors",
    "blocked_corrupt_raw",
    "blocked_disk_risk",
}
SAFE_MERGE_FILE_LIMIT = 50


@dataclass
class IngestionOptions:
    allow_partial: bool = False
    max_files: int | None = None
    resume: bool = False
    force: bool = False
    skip_plots: bool = False
    continue_on_file_error: bool = False
    write_report: bool = False
    dry_run: bool = False


@dataclass
class IngestionPlan:
    allowed: bool
    raw_state: str
    reason: str
    partial: bool
    files: list[str] = field(default_factory=list)
    processed_dir: str = ""
    output_dir: str = ""
    warnings: list[str] = field(default_factory=list)


def ingest_run(manifest: RunManifest, project_root: str | Path, options: IngestionOptions) -> dict[str, Any]:
    """Run guarded manifest-driven ingestion."""
    project_root = Path(project_root)
    raw_audit = build_raw_audit(resolve_run_paths(manifest, project_root)["raw_dir"])
    plan = plan_ingestion(manifest, raw_audit, options, project_root=project_root)
    if options.dry_run:
        return {"status": "dry_run", "plan": asdict(plan), "raw_audit": raw_audit}
    if not plan.allowed:
        return {"status": "refused", "plan": asdict(plan), "raw_audit": raw_audit}

    processed_dir = Path(plan.processed_dir)
    output_dir = Path(plan.output_dir)
    if processed_dir.exists() and any(processed_dir.iterdir()) and not (options.force or options.resume):
        return {
            "status": "refused",
            "reason": "processed output directory exists; use --resume or --force",
            "plan": asdict(plan),
        }
    output_dir.mkdir(parents=True, exist_ok=True)
    context = RunContext(
        scope=manifest.scope,
        topic=manifest.topic or manifest.run_name,
        startdate=manifest.startdate,
        stopdate=manifest.stopdate,
        run_id=_run_id(manifest, options),
        raw_dir=manifest.paths.raw_dir,
        processed_dir=str(processed_dir),
        output_dir=str(output_dir),
    )
    execution_manifest = load_or_create_execution_manifest(context, no_overwrite=not options.force)
    execution_manifest = mark_stage_started(execution_manifest, ProcessingStage.INGESTION)
    write_run_manifest_snapshot(manifest, processed_dir)

    try:
        process_result = process_raw_files_filewise(manifest, [Path(path) for path in plan.files], options, execution_manifest)
        tables = process_result["tables"]
        artifacts, nested_report, nightly_outputs, nightly_warnings = write_run_outputs(
            manifest,
            tables,
            processed_dir,
            output_dir,
            project_root=project_root,
            partial=plan.partial,
        )
        summary = write_ingestion_summary(
            manifest,
            plan,
            raw_audit,
            process_result,
            artifacts,
            nested_report,
            nightly_outputs,
            nightly_warnings,
            output_dir,
        )
        execution_manifest = mark_stage_completed(
            execution_manifest,
            ProcessingStage.INGESTION,
            details={"processed_tables": artifacts, "partial": plan.partial},
        )
        return {
            "status": "ingested_partial" if plan.partial else "ingested",
            "plan": asdict(plan),
            "summary": summary,
            "execution_manifest": asdict(execution_manifest),
        }
    except Exception as exc:  # noqa: BLE001
        mark_stage_failed(execution_manifest, ProcessingStage.INGESTION, str(exc))
        if options.continue_on_file_error:
            return {"status": "failed_with_continue_requested", "error": str(exc), "plan": asdict(plan)}
        raise


def discover_raw_files_for_manifest(manifest: RunManifest, project_root: str | Path = ".") -> list[Path]:
    """Discover supported raw delivery files for a manifest, confined to its raw directory."""
    return discover_delivery_files(resolve_run_paths(manifest, project_root)["raw_dir"])


def plan_ingestion(
    manifest: RunManifest,
    raw_audit: dict[str, Any],
    options: IngestionOptions,
    project_root: str | Path = ".",
) -> IngestionPlan:
    """Build an ingestion plan from raw readiness and options."""
    project_root = Path(project_root)
    readiness = classify_raw_readiness(
        raw_audit,
        expected_total=manifest.download_evidence.expected_total_messages,
        progress_from_terminal=manifest.download_evidence.terminal_progress_messages,
    )
    raw_state = readiness["state"]
    files = discover_raw_files_for_manifest(manifest, project_root=project_root)
    if options.max_files is not None:
        files = files[: options.max_files]
    partial = bool(options.allow_partial)
    run_id = _run_id(manifest, options)
    processed_dir, output_dir = derive_run_dirs(manifest.run_name, run_id, project_root, partial=partial)
    allowed = raw_state not in BLOCKED_RAW_STATES or options.allow_partial
    reason = readiness["reason"]
    warnings = []
    if raw_state in BLOCKED_RAW_STATES and options.allow_partial:
        reason = f"partial/debug ingestion allowed explicitly despite raw state {raw_state}"
        warnings.append("partial/debug ingestion; completeness claims remain blocked")
    elif raw_state in BLOCKED_RAW_STATES:
        reason = f"refusing ingestion because raw state is {raw_state}: {reason}"
    if not files and allowed:
        allowed = False
        reason = "no supported raw files discovered"
    return IngestionPlan(
        allowed=allowed,
        raw_state=raw_state,
        reason=reason,
        partial=partial,
        files=[str(path) for path in files],
        processed_dir=str(processed_dir),
        output_dir=str(output_dir),
        warnings=warnings,
    )


def derive_run_dirs(run_name: str, run_id: str, project_root: str | Path, partial: bool = False) -> tuple[Path, Path]:
    """Return confined `runs/<run_name>/<run_id>` processed and output directories.

    Both identifiers must be single path components, and both directories (and
    anything already inside them) must stay under the canonical processed and
    outputs bases, checked before anything is created.
    """
    run_parts = (validate_path_component(run_name, "run_name"), validate_path_component(run_id, "run_id"))
    if partial:
        run_parts += ("partial",)
    processed_base = storage_base(project_root, "data/processed/data_transfer")
    output_base = storage_base(project_root, "outputs/data_transfer")
    return (
        confine_tree(processed_base.joinpath("runs", *run_parts), processed_base),
        confine_tree(output_base.joinpath("runs", *run_parts), output_base),
    )


def load_raw_file_metadata_only(path: str | Path) -> dict[str, Any]:
    """Load cheap metadata for a raw file without returning payload rows."""
    file_path = Path(path)
    kind = classify_file_type(file_path)
    metadata = {
        "path": str(file_path),
        "kind": kind,
        "size_bytes": file_path.stat().st_size if file_path.exists() else 0,
        "rows": None,
        "columns": [],
    }
    if kind == "parquet":
        try:
            frame = pd.read_parquet(file_path)
            metadata["rows"] = int(len(frame))
            metadata["columns"] = list(frame.columns)
        except Exception as exc:  # noqa: BLE001
            metadata["error"] = str(exc)
    return metadata


def process_raw_files_filewise(
    manifest: RunManifest,
    files: list[Path],
    options: IngestionOptions,
    execution_manifest: ExecutionManifest,
) -> dict[str, Any]:
    """Process raw files one at a time and accumulate safe-sized table outputs."""
    collected: dict[str, list[pd.DataFrame]] = {}
    file_results = []
    warnings: list[str] = []
    can_merge = bool(options.max_files is not None or len(files) <= SAFE_MERGE_FILE_LIMIT)
    for index, path in enumerate(files):
        path_key = str(path)
        if options.resume and should_skip_completed_file(execution_manifest, path_key, ProcessingStage.INGESTION):
            file_results.append({"path": path_key, "status": "skipped_completed"})
            continue
        try:
            frame = _read_raw_file(path)
            frame = frame.copy()
            frame["_raw_file"] = str(path)
            frame["_raw_file_index"] = index
            tables, table_warnings = build_tables_for_run(frame, manifest=manifest, inspection={"file_count": 1, "total_rows": len(frame)})
            warnings.extend(f"{path.name}: {item}" for item in table_warnings)
            if can_merge:
                for name, table in tables.items():
                    if not table.empty:
                        collected.setdefault(name, []).append(table)
            result = FileProcessingResult(
                path=path_key,
                stage=ProcessingStage.INGESTION.value,
                status="completed",
                rows=int(len(frame)),
                size_bytes=path.stat().st_size,
                warnings=table_warnings,
            )
            record_file_result(execution_manifest, result)
            file_results.append(asdict(result))
        except Exception as exc:  # noqa: BLE001
            result = FileProcessingResult(
                path=path_key,
                stage=ProcessingStage.INGESTION.value,
                status="failed",
                errors=[str(exc)],
                size_bytes=path.stat().st_size if path.exists() else None,
            )
            record_file_result(execution_manifest, result)
            file_results.append(asdict(result))
            if not options.continue_on_file_error:
                raise
    tables = {
        name: pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()
        for name, frames in collected.items()
    }
    if not can_merge:
        warnings.append("raw file count exceeds safe merge limit; merged all/*.parquet tables were not built")
    return {"tables": tables, "files": file_results, "warnings": warnings, "merged_outputs": can_merge}


def write_run_outputs(
    manifest: RunManifest,
    tables: dict[str, pd.DataFrame],
    processed_dir: str | Path,
    output_dir: str | Path,
    project_root: str | Path,
    partial: bool,
) -> tuple[dict[str, str], dict[str, Any], dict[str, dict[str, str]], list[str]]:
    """Write canonical all-run and per-night outputs."""
    processed_dir = Path(processed_dir)
    output_dir = Path(output_dir)
    all_dir = processed_dir / "all"
    artifacts, nested_report = write_bulk_processed_tables(
        tables,
        all_dir,
        project_root=project_root,
        report_dir=output_dir,
        return_report=True,
    )
    (output_dir / "nested_column_report.json").write_text(json.dumps(nested_report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    nightly_outputs, nightly_warnings = write_nightly_tables(
        tables,
        processed_dir / "nights",
        manifest.startdate,
        manifest.stopdate,
        project_root=project_root,
    )
    if partial:
        (processed_dir / "PARTIAL_DEBUG_OUTPUT.txt").write_text(
            "This directory was produced with --allow-partial. Do not use it for completeness claims.\n",
            encoding="utf-8",
        )
    return artifacts, nested_report, nightly_outputs, nightly_warnings


def write_run_manifest_snapshot(manifest: RunManifest, processed_dir: str | Path) -> Path:
    """Write an immutable manifest snapshot alongside processed outputs."""
    path = Path(processed_dir) / "manifest_snapshot.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(manifest_to_dict(manifest), sort_keys=False), encoding="utf-8")
    return path


def write_ingestion_summary(
    manifest: RunManifest,
    plan: IngestionPlan,
    raw_audit: dict[str, Any],
    process_result: dict[str, Any],
    artifacts: dict[str, str],
    nested_report: dict[str, Any],
    nightly_outputs: dict[str, dict[str, str]],
    nightly_warnings: list[str],
    output_dir: str | Path,
) -> dict[str, Any]:
    """Write ingestion JSON and Markdown summaries."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "run_name": manifest.run_name,
        "run_id": _run_id_from_plan(plan),
        "topic": manifest.topic,
        "scope": manifest.scope,
        "packet_type": manifest.packet_type,
        "partial_debug": plan.partial,
        "raw_state": plan.raw_state,
        "raw_file_count": raw_audit.get("file_count"),
        "processed_dir": plan.processed_dir,
        "output_dir": plan.output_dir,
        "processed_artifacts": artifacts,
        "nightly_outputs": nightly_outputs,
        "nightly_counts": summarize_nightly_counts(nightly_outputs),
        "nested_report_path": nested_report.get("path"),
        "file_results": process_result.get("files", []),
        "warnings": plan.warnings + process_result.get("warnings", []) + nightly_warnings,
        "claim_note": "completeness claims remain blocked for partial/debug ingestion" if plan.partial else "no completeness claim is made by ingestion",
    }
    (output_dir / "ingestion_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output_dir / "INGESTION_SUMMARY.md").write_text(_render_ingestion_summary(summary), encoding="utf-8")
    (output_dir / "EXECUTION_SUMMARY.md").write_text(_render_ingestion_summary(summary, title="Execution Summary"), encoding="utf-8")
    return summary


def _read_raw_file(path: Path) -> pd.DataFrame:
    kind = classify_file_type(path)
    if kind == "parquet":
        return pd.read_parquet(path)
    if kind == "json":
        return pd.read_json(path, lines=path.suffix.lower() == ".jsonl")
    if kind == "csv":
        return pd.read_csv(path)
    raise ValueError(f"Unsupported raw file type: {path}")


def _run_id(manifest: RunManifest, options: IngestionOptions) -> str:
    base = manifest.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if options.allow_partial:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return f"{base}_partial_{stamp}"
    return str(base)


def _run_id_from_plan(plan: IngestionPlan) -> str:
    parts = Path(plan.output_dir).parts
    return parts[-2] if parts and parts[-1] == "partial" else parts[-1]


def _render_ingestion_summary(summary: dict[str, Any], title: str = "Manifest-Driven Ingestion Summary") -> str:
    lines = [
        f"# {title}",
        "",
        f"- Run: `{summary.get('run_name')}`",
        f"- Run ID: `{summary.get('run_id')}`",
        f"- Topic: `{summary.get('topic')}`",
        f"- Scope: `{summary.get('scope')}`",
        f"- Packet type: `{summary.get('packet_type')}`",
        f"- Partial/debug: `{summary.get('partial_debug')}`",
        f"- Raw state: `{summary.get('raw_state')}`",
        f"- Raw file count: `{summary.get('raw_file_count')}`",
        f"- Processed dir: `{summary.get('processed_dir')}`",
        f"- Output dir: `{summary.get('output_dir')}`",
        f"- Claim note: {summary.get('claim_note')}",
        "",
        "## Processed Artifacts",
        "",
    ]
    for table, path in summary.get("processed_artifacts", {}).items():
        lines.append(f"- `{table}`: `{path}`")
    if not summary.get("processed_artifacts"):
        lines.append("- None.")
    lines.extend(["", "## Warnings", ""])
    for warning in summary.get("warnings", []):
        lines.append(f"- {warning}")
    if not summary.get("warnings"):
        lines.append("- None.")
    return "\n".join(lines) + "\n"

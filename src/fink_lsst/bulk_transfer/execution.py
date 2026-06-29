"""Execution manifest helpers for resume-safe bulk delivery processing."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from .scopes import date_window_component, normalize_scope


class ProcessingStage(str, Enum):
    PREFLIGHT = "preflight"
    DOWNLOAD = "download"
    RAW_INSPECTION = "raw_inspection"
    INGESTION = "ingestion"
    VALIDATION = "validation"
    DIAGNOSTICS = "diagnostics"


@dataclass
class RunContext:
    scope: str
    topic: str
    startdate: str
    stopdate: str
    run_id: str
    raw_dir: str
    processed_dir: str
    output_dir: str


@dataclass
class FileProcessingResult:
    path: str
    stage: str
    status: str
    rows: int | None = None
    size_bytes: int | None = None
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    updated_at_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class ExecutionManifest:
    run_context: RunContext
    created_at_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    no_overwrite: bool = True
    stages: dict[str, dict[str, Any]] = field(default_factory=dict)
    files: dict[str, dict[str, Any]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    validation_status: str | None = None
    completeness_status: str = "unresolved"


def build_run_context(entry: dict[str, Any], run_id: str | None = None) -> RunContext:
    """Build a run context from a topic registry entry."""
    scope = normalize_scope(entry["scope"])
    run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    window = date_window_component(entry["utc_start"], entry["utc_stop"])
    return RunContext(
        scope=scope,
        topic=entry["topic"],
        startdate=entry["utc_start"],
        stopdate=entry["utc_stop"],
        run_id=run_id,
        raw_dir=entry["raw_delivery_dir"],
        processed_dir=f"data/processed/data_transfer/{scope}/{window}/{run_id}",
        output_dir=f"outputs/data_transfer/{scope}/{window}/{run_id}",
    )


def load_or_create_execution_manifest(context: RunContext, no_overwrite: bool = True) -> ExecutionManifest:
    """Load an existing manifest or create a new one."""
    path = manifest_path(context)
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["run_context"] = RunContext(**payload["run_context"])
        return ExecutionManifest(**payload)
    manifest = ExecutionManifest(run_context=context, no_overwrite=no_overwrite)
    write_execution_checkpoint(manifest)
    return manifest


def mark_stage_started(manifest: ExecutionManifest, stage: ProcessingStage | str) -> ExecutionManifest:
    """Mark a processing stage as started."""
    return _mark_stage(manifest, stage, "started")


def mark_stage_completed(manifest: ExecutionManifest, stage: ProcessingStage | str, details: dict[str, Any] | None = None) -> ExecutionManifest:
    """Mark a processing stage as completed."""
    return _mark_stage(manifest, stage, "completed", details=details)


def mark_stage_failed(manifest: ExecutionManifest, stage: ProcessingStage | str, error: str) -> ExecutionManifest:
    """Mark a processing stage as failed."""
    manifest.errors.append(error)
    return _mark_stage(manifest, stage, "failed", details={"error": error})


def record_file_result(manifest: ExecutionManifest, result: FileProcessingResult) -> ExecutionManifest:
    """Record per-file processing status."""
    manifest.files[result.path] = asdict(result)
    manifest.updated_at_utc = datetime.now(timezone.utc).isoformat()
    write_execution_checkpoint(manifest)
    return manifest


def should_skip_completed_file(manifest: ExecutionManifest, path: str, stage: ProcessingStage | str) -> bool:
    """Return True if a file/stage is already completed and no-overwrite is active."""
    item = manifest.files.get(path)
    return bool(manifest.no_overwrite and item and item.get("stage") == _stage_value(stage) and item.get("status") == "completed")


def write_execution_checkpoint(manifest: ExecutionManifest) -> Path:
    """Write the execution manifest checkpoint."""
    path = manifest_path(manifest.run_context)
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest.updated_at_utc = datetime.now(timezone.utc).isoformat()
    payload = asdict(manifest)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def manifest_path(context: RunContext) -> Path:
    """Return the manifest path for a context."""
    return Path(context.output_dir) / "execution_manifest.json"


def _mark_stage(manifest: ExecutionManifest, stage: ProcessingStage | str, status: str, details: dict[str, Any] | None = None) -> ExecutionManifest:
    value = _stage_value(stage)
    manifest.stages[value] = {
        "status": status,
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "details": details or {},
    }
    manifest.updated_at_utc = datetime.now(timezone.utc).isoformat()
    write_execution_checkpoint(manifest)
    return manifest


def _stage_value(stage: ProcessingStage | str) -> str:
    return stage.value if isinstance(stage, ProcessingStage) else str(stage)

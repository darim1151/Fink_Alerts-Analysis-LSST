"""Raw delivery readiness decisions."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any


PARTIAL_STATES = {
    "raw_missing",
    "download_active",
    "partial_download",
    "partial_with_errors",
    "blocked_corrupt_raw",
    "blocked_disk_risk",
}


def classify_raw_readiness(
    audit: dict[str, Any],
    expected_total: int | None = None,
    progress_from_terminal: int | None = None,
    min_free_gb: float = 20.0,
) -> dict[str, Any]:
    """Classify raw delivery readiness without scientific completeness claims."""
    raw_dir = Path(audit.get("raw_dir", "."))
    parquet = audit.get("parquet", {})
    activity = audit.get("activity", {})
    partial = audit.get("partial_signals", {})
    readable_rows = int(parquet.get("total_readable_rows", 0) or 0)
    unreadable_count = int(parquet.get("unreadable_parquet_count", 0) or 0)
    file_count = int(audit.get("file_count", 0) or 0)
    free_gb = shutil.disk_usage(raw_dir if raw_dir.exists() else Path(".")).free / 1024**3
    expected = int(expected_total) if expected_total else None
    terminal = int(progress_from_terminal) if progress_from_terminal else None
    percent = float(readable_rows / expected * 100) if expected else None
    remaining = int(max(expected - readable_rows, 0)) if expected else None
    terminal_gap = int(terminal - readable_rows) if terminal is not None else None

    if free_gb < min_free_gb:
        state = "blocked_disk_risk"
        reason = "Free disk space is below the configured safety threshold."
    elif not audit.get("exists"):
        state = "raw_missing"
        reason = "Raw directory is missing."
    elif file_count == 0:
        state = "raw_missing"
        reason = "Raw directory exists but contains no delivery files."
    elif unreadable_count and unreadable_count == int(parquet.get("parquet_file_count", 0) or 0):
        state = "blocked_corrupt_raw"
        reason = "All discovered Parquet files are unreadable."
    elif activity.get("appears_active"):
        state = "download_active"
        reason = "Files were modified recently; download may still be active."
    elif unreadable_count:
        state = "partial_with_errors"
        reason = "Some Parquet files are unreadable."
    elif partial.get("tiny_suspicious_files"):
        state = "partial_download"
        reason = "Tiny suspicious files were detected."
    elif expected and readable_rows < expected:
        state = "partial_download"
        reason = "Readable rows are below the expected terminal total."
    elif expected and readable_rows >= expected:
        state = "ready_for_raw_schema_inspection"
        reason = "Readable rows meet or exceed the expected total; raw download appears complete but unvalidated."
    elif readable_rows > 0:
        state = "appears_complete_unverified"
        reason = "Readable rows exist, but no expected total was supplied."
    else:
        state = "partial_download"
        reason = "No readable rows were counted."

    ingestion_allowed = state in {"ready_for_raw_schema_inspection", "ready_for_ingestion", "appears_complete_unverified"}
    return {
        "state": state,
        "reason": reason,
        "raw_download_appears_complete": state in {"ready_for_raw_schema_inspection", "ready_for_ingestion"},
        "week_scientifically_complete": False,
        "ingestion_allowed": ingestion_allowed,
        "expected_total": expected,
        "progress_from_terminal": terminal,
        "readable_rows": readable_rows,
        "apparent_percent_complete": percent,
        "remaining_rows": remaining,
        "terminal_progress_gap": terminal_gap,
        "unreadable_parquet_count": unreadable_count,
        "file_count": file_count,
        "free_disk_gb": round(free_gb, 2),
    }


def partial_ingestion_blocked(state: str) -> bool:
    """Return True if ingestion should be blocked by default."""
    return state in PARTIAL_STATES

"""Metadata-only raw delivery audit helpers."""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any


PARQUET_SUFFIX = ".parquet"
TINY_FILE_BYTES = 1024


def discover_raw_files(raw_dir: str | Path) -> list[Path]:
    """Return files below a raw delivery directory."""
    root = Path(raw_dir)
    if not root.exists():
        return []
    return sorted(path for path in root.rglob("*") if path.is_file() and path.name != ".gitkeep")


def summarize_file_sizes(files: list[Path]) -> dict[str, Any]:
    """Summarize file sizes without reading payloads."""
    sizes = []
    for path in files:
        try:
            sizes.append({"path": str(path), "size_bytes": int(path.stat().st_size)})
        except OSError as exc:
            sizes.append({"path": str(path), "error": str(exc), "size_bytes": None})
    valid = [item for item in sizes if item.get("size_bytes") is not None]
    return {
        "file_count": len(files),
        "total_size_bytes": int(sum(item["size_bytes"] for item in valid)),
        "largest_files": sorted(valid, key=lambda item: item["size_bytes"], reverse=True)[:20],
        "smallest_files": sorted(valid, key=lambda item: item["size_bytes"])[:20],
        "tiny_suspicious_files": [item for item in valid if item["size_bytes"] <= TINY_FILE_BYTES],
        "files": sizes,
    }


def read_parquet_metadata_safe(path: str | Path) -> dict[str, Any]:
    """Read Parquet metadata without loading full row groups."""
    path = Path(path)
    try:
        import pyarrow.parquet as pq

        parquet_file = pq.ParquetFile(path)
        schema = parquet_file.schema_arrow
        fields = []
        for field in schema:
            fields.append(
                {
                    "name": field.name,
                    "type": str(field.type),
                    "nullable": bool(field.nullable),
                    "nested": _is_nested_type(str(field.type)),
                    "binary": _is_binary_type(str(field.type)),
                }
            )
        return {
            "path": str(path),
            "readable": True,
            "rows": int(parquet_file.metadata.num_rows),
            "row_groups": int(parquet_file.metadata.num_row_groups),
            "columns": [field["name"] for field in fields],
            "fields": fields,
            "schema_hash": hashlib.sha256(str(schema).encode("utf-8")).hexdigest()[:16],
            "size_bytes": int(path.stat().st_size),
            "modified_time": float(path.stat().st_mtime),
        }
    except Exception as exc:  # noqa: BLE001
        size = path.stat().st_size if path.exists() else None
        return {
            "path": str(path),
            "readable": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "rows": 0,
            "size_bytes": int(size) if size is not None else None,
            "modified_time": float(path.stat().st_mtime) if path.exists() else None,
        }


def audit_parquet_readability(raw_dir: str | Path) -> dict[str, Any]:
    """Audit readable/unreadable Parquet files using metadata reads only."""
    files = discover_raw_files(raw_dir)
    parquet_files = [path for path in files if path.suffix.lower() == PARQUET_SUFFIX]
    results = [read_parquet_metadata_safe(path) for path in parquet_files]
    readable = [item for item in results if item.get("readable")]
    unreadable = [item for item in results if not item.get("readable")]
    return {
        "raw_dir": str(raw_dir),
        "parquet_file_count": len(parquet_files),
        "readable_parquet_count": len(readable),
        "unreadable_parquet_count": len(unreadable),
        "total_readable_rows": int(sum(int(item.get("rows", 0)) for item in readable)),
        "row_count_by_file": [{"path": item["path"], "rows": int(item.get("rows", 0))} for item in results],
        "unreadable_files": unreadable,
        "metadata": results,
    }


def group_parquet_schemas(raw_dir: str | Path) -> dict[str, Any]:
    """Group Parquet files by schema hash."""
    audit = audit_parquet_readability(raw_dir)
    groups: dict[str, dict[str, Any]] = {}
    for item in audit["metadata"]:
        if not item.get("readable"):
            continue
        schema_hash = item.get("schema_hash", "unknown")
        group = groups.setdefault(
            schema_hash,
            {
                "schema_hash": schema_hash,
                "file_count": 0,
                "rows": 0,
                "columns": item.get("columns", []),
                "fields": item.get("fields", []),
                "files": [],
            },
        )
        group["file_count"] += 1
        group["rows"] += int(item.get("rows", 0))
        group["files"].append(item["path"])
    return {"schema_group_count": len(groups), "schema_groups": list(groups.values())}


def detect_probably_partial_files(raw_dir: str | Path) -> dict[str, Any]:
    """Detect zero-byte/tiny/recently modified files that may be partial."""
    files = discover_raw_files(raw_dir)
    size_summary = summarize_file_sizes(files)
    now = time.time()
    recent = []
    for path in files:
        try:
            age_seconds = now - path.stat().st_mtime
        except OSError:
            continue
        if age_seconds < 300:
            recent.append({"path": str(path), "age_seconds": round(age_seconds, 2), "size_bytes": int(path.stat().st_size)})
    return {
        "tiny_suspicious_files": size_summary["tiny_suspicious_files"],
        "recently_modified_files": recent,
        "probably_partial_count": len(size_summary["tiny_suspicious_files"]) + len(recent),
    }


def estimate_download_activity(raw_dir: str | Path, windows_minutes: tuple[int, ...] = (5, 15, 60)) -> dict[str, Any]:
    """Estimate whether a download directory is actively changing."""
    files = discover_raw_files(raw_dir)
    now = time.time()
    newest = None
    oldest = None
    counts = {}
    for path in files:
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        newest = path if newest is None or mtime > newest.stat().st_mtime else newest
        oldest = path if oldest is None or mtime < oldest.stat().st_mtime else oldest
    for minutes in windows_minutes:
        seconds = minutes * 60
        counts[f"modified_last_{minutes}_minutes"] = sum(1 for path in files if now - path.stat().st_mtime <= seconds)
    active = any(value > 0 for value in counts.values())
    return {
        "newest_modified_file": str(newest) if newest else None,
        "oldest_modified_file": str(oldest) if oldest else None,
        "activity_windows": counts,
        "appears_active": active,
    }


def build_raw_audit(raw_dir: str | Path) -> dict[str, Any]:
    """Build a complete metadata-only raw audit."""
    raw_dir = Path(raw_dir)
    exists = raw_dir.exists()
    files = discover_raw_files(raw_dir)
    size_summary = summarize_file_sizes(files)
    parquet = audit_parquet_readability(raw_dir)
    schemas = group_parquet_schemas(raw_dir)
    partial = detect_probably_partial_files(raw_dir)
    activity = estimate_download_activity(raw_dir)
    return {
        "raw_dir": str(raw_dir),
        "exists": exists,
        "file_count": len(files),
        "schema_dump_count": sum(1 for path in files if "schema" in path.name.lower()),
        "size_summary": size_summary,
        "parquet": parquet,
        "schemas": schemas,
        "partial_signals": partial,
        "activity": activity,
    }


def _is_nested_type(type_text: str) -> bool:
    lowered = type_text.lower()
    return any(marker in lowered for marker in ("struct<", "list<", "map<", "large_list<", "fixed_size_list<"))


def _is_binary_type(type_text: str) -> bool:
    lowered = type_text.lower()
    return any(marker in lowered for marker in ("binary", "large_binary", "fixed_size_binary"))

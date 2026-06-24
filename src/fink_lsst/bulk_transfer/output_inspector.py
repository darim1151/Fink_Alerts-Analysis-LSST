"""Inspect local Data Transfer delivery files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from fink_lsst.storage import write_json


def discover_delivery_files(delivery_dir: str | Path) -> list[Path]:
    """Discover supported delivery files under a directory."""
    root = Path(delivery_dir)
    if not root.exists():
        return []
    return sorted(path for path in root.rglob("*") if path.is_file() and path.name != ".gitkeep")


def classify_file_type(path: str | Path) -> str:
    """Classify delivery file type."""
    suffix = Path(path).suffix.lower()
    return {".parquet": "parquet", ".avro": "avro", ".json": "json", ".jsonl": "json", ".csv": "csv"}.get(suffix, "unknown")


def inspect_parquet_schema(path: str | Path) -> dict[str, Any]:
    """Inspect a Parquet file schema."""
    frame = pd.read_parquet(path)
    return {"columns": list(frame.columns), "rows": int(len(frame)), "dtypes": {name: str(dtype) for name, dtype in frame.dtypes.items()}}


def inspect_json_shape(path: str | Path) -> dict[str, Any]:
    """Inspect JSON or JSONL shape."""
    path = Path(path)
    if path.suffix.lower() == ".jsonl":
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        return {"shape": "jsonl", "rows": len(rows), "columns": sorted(rows[0].keys()) if rows and isinstance(rows[0], dict) else []}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        columns = sorted(payload[0].keys()) if payload and isinstance(payload[0], dict) else []
        return {"shape": "list", "rows": len(payload), "columns": columns}
    if isinstance(payload, dict):
        return {"shape": "dict", "keys": sorted(payload.keys()), "rows": 1}
    return {"shape": type(payload).__name__, "rows": 1}


def inspect_avro_schema(path: str | Path) -> dict[str, Any]:
    """Inspect Avro schema if fastavro is available."""
    try:
        import fastavro  # type: ignore
    except ImportError:
        return {"available": False, "message": "fastavro is not installed"}
    with Path(path).open("rb") as handle:
        reader = fastavro.reader(handle)
        schema = reader.writer_schema
    return {"available": True, "schema": schema}


def summarize_delivery(delivery_dir: str | Path) -> dict[str, Any]:
    """Summarize a delivery directory."""
    files = discover_delivery_files(delivery_dir)
    summaries = []
    for path in files:
        kind = classify_file_type(path)
        info: dict[str, Any] = {"path": str(path), "file_type": kind, "size_bytes": path.stat().st_size}
        try:
            if kind == "parquet":
                info["schema"] = inspect_parquet_schema(path)
            elif kind == "json":
                info["schema"] = inspect_json_shape(path)
            elif kind == "avro":
                info["schema"] = inspect_avro_schema(path)
            elif kind == "csv":
                frame = pd.read_csv(path, nrows=5)
                info["schema"] = {"columns": list(frame.columns)}
        except Exception as exc:  # noqa: BLE001
            info["error"] = str(exc)
        summaries.append(info)
    return {"delivery_dir": str(delivery_dir), "file_count": len(files), "files": summaries, "empty": len(files) == 0}


def summarize_dropzones(base_dir: str | Path) -> dict[str, Any]:
    """Summarize smoke/full-night delivery drop-zones."""
    base = Path(base_dir)
    zones = {}
    for name in ("smoke_delivery", "full_night"):
        path = base / name
        zones[name] = summarize_delivery(path)
    return {"base_dir": str(base), "zones": zones}


def write_delivery_manifest(delivery_dir: str | Path, output_path: str | Path) -> Path:
    """Write a delivery inspection manifest."""
    return write_json(summarize_delivery(delivery_dir), output_path, enforce_allowed_roots=False)

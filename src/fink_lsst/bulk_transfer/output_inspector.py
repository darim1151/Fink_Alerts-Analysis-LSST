"""Inspect local Data Transfer delivery files."""

from __future__ import annotations

import json
import hashlib
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
    path = Path(path)
    try:
        import pyarrow.parquet as pq
    except ImportError:
        frame = pd.read_parquet(path)
        return {"columns": list(frame.columns), "rows": int(len(frame)), "dtypes": {name: str(dtype) for name, dtype in frame.dtypes.items()}}
    parquet_file = pq.ParquetFile(path)
    schema = parquet_file.schema_arrow
    field_rows = []
    for field in schema:
        field_rows.append(
            {
                "name": field.name,
                "type": str(field.type),
                "nullable": bool(field.nullable),
                "nested": _is_arrow_nested_type(str(field.type)),
            }
        )
    sample = pd.read_parquet(path)
    null_fraction = {
        column: float(sample[column].isna().mean())
        for column in list(sample.columns)[:50]
    }
    return {
        "columns": list(sample.columns),
        "rows": int(parquet_file.metadata.num_rows),
        "row_groups": int(parquet_file.metadata.num_row_groups),
        "pyarrow_schema": str(schema),
        "schema_hash": hashlib.sha256(str(schema).encode("utf-8")).hexdigest()[:16],
        "fields": field_rows,
        "nested_columns": [field["name"] for field in field_rows if field["nested"]],
        "dtypes": {name: str(dtype) for name, dtype in sample.dtypes.items()},
        "null_fraction_sample": null_fraction,
    }


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
    total_size = 0
    total_rows = 0
    all_columns: set[str] = set()
    schema_groups: dict[str, dict[str, Any]] = {}
    nested_columns: set[str] = set()
    for path in files:
        kind = classify_file_type(path)
        info: dict[str, Any] = {"path": str(path), "file_type": kind, "size_bytes": path.stat().st_size}
        total_size += int(info["size_bytes"])
        try:
            if kind == "parquet":
                info["schema"] = inspect_parquet_schema(path)
                total_rows += int(info["schema"].get("rows", 0))
                all_columns.update(info["schema"].get("columns", []))
                nested_columns.update(info["schema"].get("nested_columns", []))
                schema_hash = info["schema"].get("schema_hash", "unknown")
                schema_groups.setdefault(
                    schema_hash,
                    {
                        "schema_hash": schema_hash,
                        "file_count": 0,
                        "rows": 0,
                        "columns": info["schema"].get("columns", []),
                        "fields": info["schema"].get("fields", []),
                        "nested_columns": info["schema"].get("nested_columns", []),
                    },
                )
                schema_groups[schema_hash]["file_count"] += 1
                schema_groups[schema_hash]["rows"] += int(info["schema"].get("rows", 0))
            elif kind == "json":
                info["schema"] = inspect_json_shape(path)
                total_rows += int(info["schema"].get("rows", 0))
                all_columns.update(info["schema"].get("columns", info["schema"].get("keys", [])))
            elif kind == "avro":
                info["schema"] = inspect_avro_schema(path)
            elif kind == "csv":
                frame = pd.read_csv(path, nrows=5)
                info["schema"] = {"columns": list(frame.columns)}
                all_columns.update(frame.columns)
        except Exception as exc:  # noqa: BLE001
            info["error"] = str(exc)
        summaries.append(info)
    sidecars = [str(path) for path in Path(delivery_dir).rglob("*schema*") if path.is_file()] if Path(delivery_dir).exists() else []
    return {
        "delivery_dir": str(delivery_dir),
        "file_count": len(files),
        "total_size_bytes": total_size,
        "total_rows": total_rows,
        "columns": sorted(all_columns),
        "nested_columns": sorted(nested_columns),
        "schema_groups": list(schema_groups.values()),
        "schema_group_count": len(schema_groups),
        "schema_sidecar_files": sidecars,
        "files": summaries,
        "empty": len(files) == 0,
    }


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


def _is_arrow_nested_type(type_text: str) -> bool:
    lowered = type_text.lower()
    return any(marker in lowered for marker in ("struct<", "list<", "map<", "large_list<", "fixed_size_list<"))

"""Fast partial full-packet stress analysis without heavy nested serialization."""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from fink_lsst.storage import write_json

from .raw_audit import discover_raw_files, read_parquet_metadata_safe


DEFAULT_OUT_DIR = Path("outputs/data_transfer/quick_analysis/full_week_full_packet_partial")
HEAVY_MARKERS = ("cutout", "stamp", "fits", "image", "bytes", "binary")
DIASOURCE_FIELDS = ("diaSourceId", "diaObjectId", "midPointTai", "midpointMjdTai", "ra", "decl", "dec", "psFlux", "psFluxErr", "band")
TOP_LEVEL_KEEP = (
    "diaSourceId",
    "observation_reason",
    "target_name",
    "brokerIngestMjd",
    "lsst_schema_version",
    "brokerStartProcessTimestamp",
    "fink_broker_version",
    "fink_science_version",
    "publisher",
    "brokerEndProcessTimestamp",
    "timestamp",
    "year",
    "month",
    "day",
    "tns_type_recomputed",
)


def summarize_raw_metadata(raw_dir: str | Path, expected_total: int | None = None, progress_every: int = 10000) -> dict[str, Any]:
    """Scan Parquet metadata without loading full payloads."""
    root = Path(raw_dir)
    files = [path for path in discover_raw_files(root) if path.suffix.lower() == ".parquet"]
    metadata = []
    schema_groups: dict[str, dict[str, Any]] = {}
    failures = []
    total_rows = 0
    for index, path in enumerate(files, start=1):
        item = read_parquet_metadata_safe(path)
        metadata.append(item)
        if not item.get("readable"):
            failures.append(item)
            continue
        total_rows += int(item.get("rows", 0))
        schema_hash = item.get("schema_hash", "unknown")
        group = schema_groups.setdefault(
            schema_hash,
            {
                "schema_hash": schema_hash,
                "file_count": 0,
                "rows": 0,
                "columns": item.get("columns", []),
                "fields": item.get("fields", []),
            },
        )
        group["file_count"] += 1
        group["rows"] += int(item.get("rows", 0))
        if progress_every and index % progress_every == 0:
            print(f"metadata progress: {index}/{len(files)} files")
    apparent_percent = float(total_rows / expected_total * 100) if expected_total else None
    file_rows = pd.DataFrame(
        [
            {
                "path": item.get("path"),
                "rows": int(item.get("rows", 0) or 0),
                "readable": bool(item.get("readable")),
                "size_bytes": item.get("size_bytes"),
                "schema_hash": item.get("schema_hash"),
                "error_type": item.get("error_type"),
            }
            for item in metadata
        ]
    )
    return {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "analysis_type": "partial_debug_stress_test",
        "raw_dir": str(root),
        "file_count": len(files),
        "readable_file_count": sum(1 for item in metadata if item.get("readable")),
        "failed_file_count": len(failures),
        "readable_rows": int(total_rows),
        "expected_total": expected_total,
        "apparent_percent_complete": apparent_percent,
        "scientific_completeness": False,
        "schema_group_count": len(schema_groups),
        "schema_groups": list(schema_groups.values()),
        "columns": sorted({column for group in schema_groups.values() for column in group.get("columns", [])}),
        "failed_files": failures[:100],
        "file_rows": file_rows,
    }


def extract_flat_sample(
    raw_dir: str | Path,
    max_files: int = 5000,
    include_cutout_columns: bool = False,
    progress_every: int = 1000,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Extract a flattened scalar sample from up to max_files raw Parquet files."""
    root = Path(raw_dir)
    files = [path for path in discover_raw_files(root) if path.suffix.lower() == ".parquet"][:max_files]
    frames = []
    skipped_nested = Counter()
    failed_files = []
    for index, path in enumerate(files, start=1):
        try:
            columns, skipped_columns = _safe_read_columns(path, include_cutout_columns=include_cutout_columns)
            for column in skipped_columns:
                skipped_nested[column] += 1
            frame = pd.read_parquet(path, columns=columns)
            flat = _flatten_frame(frame, path, skipped_nested, include_cutout_columns=include_cutout_columns)
            frames.append(flat)
        except Exception as exc:  # noqa: BLE001
            failed_files.append({"path": str(path), "error_type": type(exc).__name__, "error": str(exc)})
        if progress_every and index % progress_every == 0:
            print(f"sample progress: {index}/{len(files)} files")
    sample = pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()
    summary = {
        "analysis_type": "partial_debug_stress_test",
        "max_files": int(max_files),
        "files_attempted": len(files),
        "files_succeeded": len(frames),
        "files_failed": len(failed_files),
        "rows": int(len(sample)),
        "columns": list(sample.columns),
        "skipped_nested_or_heavy_fields": dict(skipped_nested),
        "failed_files": failed_files[:100],
        "include_cutout_columns": bool(include_cutout_columns),
    }
    return sample, summary


def build_quick_partial_report(df: pd.DataFrame, metadata_summary: dict[str, Any], out_dir: str | Path) -> dict[str, Any]:
    """Build and write quick partial-analysis report payloads."""
    output = Path(out_dir)
    band_column = _first_present(df, ("diaSource.band", "band", "r:band"))
    object_column = _first_present(df, ("diaSource.diaObjectId", "diaObjectId", "r:diaObjectId"))
    source_column = _first_present(df, ("diaSource.diaSourceId", "diaSourceId", "r:diaSourceId"))
    ra_column = _first_present(df, ("diaSource.ra", "ra", "r:ra"))
    dec_column = _first_present(df, ("diaSource.decl", "diaSource.dec", "dec", "r:dec"))
    time_column = _first_present(df, ("diaSource.midPointTai", "diaSource.midpointMjdTai", "brokerIngestMjd", "timestamp"))
    clf_columns = [column for column in df.columns if column.startswith("clf.")]
    summary = {
        "analysis_type": "partial_debug_stress_test",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "metadata": _json_metadata(metadata_summary),
        "sample_rows": int(len(df)),
        "sample_columns": list(df.columns),
        "band_counts": _value_counts(df[band_column]) if band_column else {},
        "object_column": object_column,
        "source_column": source_column,
        "unique_objects": int(df[object_column].nunique()) if object_column else None,
        "unique_sources": int(df[source_column].nunique()) if source_column else None,
        "classifier_columns": clf_columns,
        "time_coverage": _numeric_range(df, time_column) if time_column else {},
        "sky_coverage": {"ra": _numeric_range(df, ra_column), "dec": _numeric_range(df, dec_column)} if ra_column or dec_column else {},
        "missingness": {column: int(df[column].isna().sum()) for column in df.columns[:200]},
        "limitations": [
            "partial debug/stress-test only",
            "not a complete week",
            "not a complete first day",
            "not a validated all-alert census",
            "cutout/heavy nested payloads are skipped by default",
        ],
    }
    output.mkdir(parents=True, exist_ok=True)
    write_json(summary, output / "sample_summary.json", enforce_allowed_roots=False)
    (output / "QUICK_PARTIAL_ANALYSIS.md").write_text(render_quick_partial_report(summary), encoding="utf-8")
    return summary


def write_quick_analysis_outputs(
    raw_dir: str | Path,
    out_dir: str | Path = DEFAULT_OUT_DIR,
    expected_total: int | None = None,
    max_files: int = 5000,
    metadata_only: bool = False,
    include_cutout_columns: bool = False,
    progress_every: int = 1000,
) -> dict[str, Any]:
    """Run quick partial analysis and write all requested outputs."""
    output = Path(out_dir)
    output.mkdir(parents=True, exist_ok=True)
    metadata = summarize_raw_metadata(raw_dir, expected_total=expected_total, progress_every=progress_every)
    file_rows = metadata.pop("file_rows")
    write_json(_json_metadata(metadata), output / "raw_metadata_summary.json", enforce_allowed_roots=False)
    _write_parquet(file_rows, output / "file_rows.parquet")
    (output / "RAW_METADATA_SUMMARY.md").write_text(render_raw_metadata_summary(metadata), encoding="utf-8")
    result = {
        "metadata_summary_path": str(output / "raw_metadata_summary.json"),
        "metadata_markdown_path": str(output / "RAW_METADATA_SUMMARY.md"),
        "file_rows_path": str(output / "file_rows.parquet"),
        "metadata": metadata,
        "sample_summary": None,
    }
    if metadata_only:
        return result
    sample, sample_extraction_summary = extract_flat_sample(
        raw_dir,
        max_files=max_files,
        include_cutout_columns=include_cutout_columns,
        progress_every=progress_every,
    )
    sample_path = output / f"sample_{max_files}_files_flattened.parquet"
    _write_parquet(sample, sample_path)
    report = build_quick_partial_report(sample, metadata, output)
    sample_summary_path = output / f"sample_{max_files}_summary.json"
    write_json({**report, "sample_extraction": sample_extraction_summary}, sample_summary_path, enforce_allowed_roots=False)
    result.update(
        {
            "sample_path": str(sample_path),
            "sample_summary_path": str(sample_summary_path),
            "quick_report_path": str(output / "QUICK_PARTIAL_ANALYSIS.md"),
            "sample_summary": {**report, "sample_extraction": sample_extraction_summary},
        }
    )
    return result


def render_raw_metadata_summary(metadata: dict[str, Any]) -> str:
    """Render metadata summary Markdown."""
    lines = [
        "# Raw Metadata Summary",
        "",
        "- Analysis type: `partial_debug_stress_test`",
        f"- Raw dir: `{metadata.get('raw_dir')}`",
        f"- Files: `{metadata.get('file_count')}`",
        f"- Readable files: `{metadata.get('readable_file_count')}`",
        f"- Failed files: `{metadata.get('failed_file_count')}`",
        f"- Readable rows: `{metadata.get('readable_rows')}`",
        f"- Expected total: `{metadata.get('expected_total')}`",
        f"- Apparent percent complete: `{metadata.get('apparent_percent_complete')}`",
        f"- Scientific completeness: `{metadata.get('scientific_completeness')}`",
        f"- Schema groups: `{metadata.get('schema_group_count')}`",
        "",
        "No completeness claim is made.",
        "",
    ]
    return "\n".join(lines)


def render_quick_partial_report(summary: dict[str, Any]) -> str:
    """Render quick partial-analysis Markdown."""
    metadata = summary.get("metadata", {})
    lines = [
        "# Quick Partial Full-Packet Stress Analysis",
        "",
        "- Analysis type: `partial_debug_stress_test`",
        "- Completeness claim: `blocked`",
        f"- Raw files: `{metadata.get('file_count')}`",
        f"- Readable rows: `{metadata.get('readable_rows')}`",
        f"- Expected total: `{metadata.get('expected_total')}`",
        f"- Apparent percent complete: `{metadata.get('apparent_percent_complete')}`",
        f"- Sample rows: `{summary.get('sample_rows')}`",
        f"- Unique objects: `{summary.get('unique_objects')}`",
        f"- Unique sources: `{summary.get('unique_sources')}`",
        "",
        "## Band Counts",
        "",
    ]
    for key, value in summary.get("band_counts", {}).items():
        lines.append(f"- `{key}`: `{value}`")
    if not summary.get("band_counts"):
        lines.append("- No band column found.")
    lines.extend(["", "## Classifier Columns", ""])
    for column in summary.get("classifier_columns", []):
        lines.append(f"- `{column}`")
    if not summary.get("classifier_columns"):
        lines.append("- None found.")
    lines.extend(["", "## Limitations", ""])
    for item in summary.get("limitations", []):
        lines.append(f"- {item}")
    return "\n".join(lines) + "\n"


def _safe_read_columns(path: Path, include_cutout_columns: bool) -> tuple[list[str] | None, list[str]]:
    metadata = read_parquet_metadata_safe(path)
    columns = metadata.get("columns") or []
    if not columns:
        return None, []
    selected = []
    skipped = []
    for column in columns:
        lowered = column.lower()
        if not include_cutout_columns and any(marker in lowered for marker in HEAVY_MARKERS):
            skipped.append(column)
            continue
        if column in TOP_LEVEL_KEEP or column in {"diaSource", "clf"} or "." in column:
            selected.append(column)
    return selected or None, skipped


def _flatten_frame(df: pd.DataFrame, path: Path, skipped_nested: Counter, include_cutout_columns: bool) -> pd.DataFrame:
    rows = []
    for _, row in df.iterrows():
        flat: dict[str, Any] = {"_raw_file": str(path)}
        for column, value in row.items():
            lowered = column.lower()
            if not include_cutout_columns and any(marker in lowered for marker in HEAVY_MARKERS):
                skipped_nested[column] += 1
                continue
            if column == "diaSource":
                source = _mapping(value)
                for field in DIASOURCE_FIELDS:
                    if field in source:
                        flat[f"diaSource.{field}"] = source.get(field)
                continue
            if column == "clf":
                clf = _mapping(value)
                for key, item in clf.items():
                    if _is_scalar(item):
                        flat[f"clf.{key}"] = item
                    else:
                        skipped_nested[f"clf.{key}"] += 1
                continue
            if "." in column and _is_scalar(value):
                flat[column] = value
                continue
            if _is_scalar(value):
                flat[column] = value
            else:
                skipped_nested[column] += 1
        rows.append(flat)
    return pd.DataFrame(rows)


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "as_py"):
        item = value.as_py()
        return item if isinstance(item, dict) else {}
    return {}


def _is_scalar(value: Any) -> bool:
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except Exception:  # noqa: BLE001
        pass
    return isinstance(value, (str, int, float, bool))


def _first_present(df: pd.DataFrame, candidates: tuple[str, ...]) -> str | None:
    return next((column for column in candidates if column in df.columns), None)


def _value_counts(series: pd.Series, limit: int = 20) -> dict[str, int]:
    return {str(key): int(value) for key, value in series.dropna().astype(str).value_counts().head(limit).items()}


def _numeric_range(df: pd.DataFrame, column: str | None) -> dict[str, Any]:
    if not column or column not in df.columns:
        return {}
    values = pd.to_numeric(df[column], errors="coerce").dropna()
    return {
        "column": column,
        "non_null": int(df[column].notna().sum()),
        "numeric_count": int(len(values)),
        "min": float(values.min()) if not values.empty else None,
        "max": float(values.max()) if not values.empty else None,
    }


def _json_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in metadata.items() if key != "file_rows"}


def _write_parquet(df: pd.DataFrame, path: str | Path) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(output, index=False)
    return output

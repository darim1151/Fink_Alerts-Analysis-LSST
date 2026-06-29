"""Ingestion hooks for delivered Fink Data Transfer files."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from fink_lsst.normalize import normalize_forced_photometry, normalize_objects, normalize_sources
from fink_lsst.storage import write_json, write_parquet

from .nested import (
    is_nested_value,
    json_dumps_stable,
    sanitize_dataframe_for_parquet,
    summarize_nested_columns,
    to_json_safe,
)
from .output_inspector import classify_file_type, discover_delivery_files, summarize_delivery


SMOKE_DELIVERY_METADATA = {
    "topic": "ftransfer_lsst_2026-06-24_657339",
    "survey": "lsst",
    "utc_start": "2026-02-25",
    "utc_stop": "2026-02-26",
    "filter": "in_tns",
    "content_type": "Light static packet",
}

COMPLETENESS_SCOPE = {
    "scope": "tag_filtered_smoke_delivery",
    "full_night_complete": False,
    "reason": "in_tns filter and light static packet; not all-alert full-night production",
}

FULL_NIGHT_COMPLETENESS_SCOPE = {
    "scope": "full_night_all_alerts",
    "full_night_complete": True,
    "reason": "all-alert full-night Data Transfer scope is recorded; validation gates any completeness claim",
}


def find_latest_delivery_dir(smoke_root: str | Path) -> Path:
    """Return the newest delivery subdirectory, falling back to the supplied root."""
    root = Path(smoke_root)
    if not root.exists():
        return root
    child_candidates = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        files = discover_delivery_files(child)
        if files:
            child_candidates.append((max(path.stat().st_mtime for path in files), child))
    if child_candidates:
        return sorted(child_candidates, key=lambda item: (item[0], item[1].name))[-1][1]
    return root


def load_data_transfer_files(delivery_dir: str | Path, include_source_file: bool = True) -> pd.DataFrame:
    """Load supported local delivery files into one DataFrame."""
    frames = []
    for index, path in enumerate(discover_delivery_files(delivery_dir)):
        kind = classify_file_type(path)
        if kind == "parquet":
            frame = pd.read_parquet(path)
        elif kind == "json":
            frame = pd.read_json(path, lines=Path(path).suffix.lower() == ".jsonl")
        elif kind == "csv":
            frame = pd.read_csv(path)
        else:
            continue
        if include_source_file:
            frame = frame.copy()
            frame["_raw_file"] = str(path)
            frame["_raw_file_index"] = index
        frames.append(frame)
    return pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()


def normalize_bulk_alerts(payload_or_dataframe: Any) -> pd.DataFrame:
    """Normalize bulk alert rows into the existing source-level internal model."""
    return normalize_sources(payload_or_dataframe)


def split_bulk_tables(df: pd.DataFrame, inspection: dict[str, Any] | None = None) -> dict[str, pd.DataFrame]:
    """Split a raw bulk DataFrame into internal tables."""
    if df.empty:
        return {
            "alerts": pd.DataFrame(),
            "objects": pd.DataFrame(),
            "forced_photometry": pd.DataFrame(),
            "classifications": pd.DataFrame(),
            "lightcurve_features": pd.DataFrame(),
            "nightly_summary": _build_nightly_summary(pd.DataFrame(), df, pd.DataFrame(), inspection),
        }
    alerts = normalize_bulk_alerts(df)
    alerts = _add_json_shadow_columns(alerts, df)
    lightcurve_features = expand_lc_features(df, alerts)
    tables = {
        "alerts": alerts,
        "objects": _build_objects_table(alerts),
        "forced_photometry": _build_forced_table(alerts),
        "classifications": _build_classifications_table(alerts),
        "lightcurve_features": lightcurve_features,
    }
    tables["nightly_summary"] = _build_nightly_summary(alerts, df, lightcurve_features, inspection)
    return tables


def expand_lc_features(raw_df: pd.DataFrame, normalized_alerts: pd.DataFrame | None = None) -> pd.DataFrame:
    """Expand Fink ``lc_features`` maps/lists into one row per object/source/band."""
    if "lc_features" not in raw_df.columns:
        return pd.DataFrame()
    normalized_alerts = normalized_alerts if normalized_alerts is not None else pd.DataFrame(index=raw_df.index)
    rows: list[dict[str, Any]] = []
    for index, value in raw_df["lc_features"].items():
        links = _row_links(raw_df.loc[index], normalized_alerts.loc[index] if index in normalized_alerts.index else None)
        for band, features in _iter_lc_feature_items(value):
            safe_features = to_json_safe(features)
            row = {
                **links,
                "lc_feature_band": band,
                "features_json": json_dumps_stable(safe_features),
            }
            if isinstance(safe_features, dict):
                for key, item in safe_features.items():
                    column = f"feature_{_safe_column_name(str(key))}"
                    row[column] = item if not is_nested_value(item) else json_dumps_stable(item)
            else:
                row["feature_value_json"] = json_dumps_stable(safe_features)
            rows.append(row)
    return pd.DataFrame(rows)


def write_bulk_processed_tables(
    tables: dict[str, pd.DataFrame],
    processed_dir: str | Path,
    project_root: str | Path | None = None,
    report_dir: str | Path | None = None,
    return_report: bool = False,
) -> dict[str, str] | tuple[dict[str, str], dict[str, Any]]:
    """Write non-empty processed bulk tables after nested-column sanitization."""
    processed = Path(processed_dir)
    processed.mkdir(parents=True, exist_ok=True)
    report = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "tables": {},
        "failures": [],
        "converted_columns": {},
    }
    artifacts: dict[str, str] = {}
    for name, frame in tables.items():
        if frame.empty:
            continue
        path = processed / f"{name}.parquet"
        sanitized, conversion = sanitize_dataframe_for_parquet(frame)
        table_report = {
            "input_shape": [int(frame.shape[0]), int(frame.shape[1])],
            "output_shape": [int(sanitized.shape[0]), int(sanitized.shape[1])],
            "path": str(path),
            "conversion": conversion,
            "status": "pending",
        }
        try:
            write_parquet(sanitized, path, project_root=project_root)
        except Exception as exc:  # noqa: BLE001
            preview_path = processed / f"{name}.preview.csv"
            failure_path = processed / f"{name}.write_failure.json"
            sanitized.head(1000).to_csv(preview_path, index=False)
            failure = {
                "table": name,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "preview_path": str(preview_path),
                "failure_path": str(failure_path),
            }
            write_json(failure, failure_path, enforce_allowed_roots=False)
            table_report.update({"status": "failed", **failure})
            report["failures"].append(failure)
        else:
            artifacts[name] = str(path)
            table_report["status"] = "written"
        report["tables"][name] = table_report
        report["converted_columns"][name] = conversion.get("converted_columns", [])
    report["artifact_count"] = len(artifacts)
    report["failure_count"] = len(report["failures"])
    report_path = processed / "nested_conversion_report.json"
    write_json(report, report_path, enforce_allowed_roots=False)
    if report_dir is not None:
        write_json(report, Path(report_dir) / "nested_conversion_report.json", enforce_allowed_roots=False)
    report["path"] = str(report_path)
    return (artifacts, report) if return_report else artifacts


def write_bulk_ingestion_manifest(
    run_id: str,
    delivery_dir: str | Path,
    processed_dir: str | Path,
    artifacts: dict[str, str],
    output_path: str | Path,
    inspection: dict[str, Any] | None = None,
    table_shapes: dict[str, Any] | None = None,
    nested_report: dict[str, Any] | None = None,
    validation_status: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Path:
    """Write bulk ingestion manifest."""
    delivery_dir = Path(delivery_dir)
    inspection = inspection or summarize_delivery(delivery_dir)
    metadata = {**SMOKE_DELIVERY_METADATA, **(metadata or {})}
    completeness = {**COMPLETENESS_SCOPE, **metadata.get("completeness_scope", {})}
    full_night_complete = bool(completeness.get("full_night_complete"))
    manifest = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "topic": metadata.get("topic") or delivery_dir.name,
        "survey": metadata.get("survey"),
        "utc_window": {
            "start": metadata.get("utc_start"),
            "stop": metadata.get("utc_stop"),
            "timezone": "UTC",
            "stop_convention": "exclusive",
        },
        "filter": metadata.get("filter"),
        "content_type": metadata.get("content_type"),
        "delivery_dir": str(delivery_dir),
        "raw_delivery_dir": str(delivery_dir),
        "processed_dir": str(processed_dir),
        "raw_file_count": int(inspection.get("file_count", 0)),
        "raw_row_count": int(inspection.get("total_rows", 0)),
        "processed_table_paths": artifacts,
        "artifacts": artifacts,
        "table_shapes": table_shapes or {},
        "nested_columns_detected": inspection.get("nested_columns", []),
        "nested_conversion_report": nested_report,
        "schema_groups": inspection.get("schema_groups", []),
        "schema_group_count": inspection.get("schema_group_count", 0),
        "validation_status": validation_status or "not_run",
        "completeness_scope": completeness,
        "scope": completeness["scope"],
        "all_alerts": bool(metadata.get("all_alerts", False)),
        "kafka_lag_zero": metadata.get("kafka_lag_zero"),
        "truncation_warning": bool(metadata.get("truncation_warning", False)),
        "date_window_explained": bool(metadata.get("date_window_explained", False)),
        "full_night_complete": full_night_complete,
        "complete_night_claimed": full_night_complete and validation_status == "passed",
        "reason": completeness["reason"],
    }
    return write_json(manifest, output_path, enforce_allowed_roots=False)


def read_processed_tables(processed_dir: str | Path) -> dict[str, pd.DataFrame]:
    """Read processed Parquet tables from an ingestion directory."""
    processed = Path(processed_dir)
    tables: dict[str, pd.DataFrame] = {}
    if not processed.exists():
        return tables
    for parquet in sorted(processed.glob("*.parquet")):
        tables[parquet.stem] = pd.read_parquet(parquet)
    return tables


def table_shapes(tables: dict[str, pd.DataFrame]) -> dict[str, dict[str, int]]:
    """Return row/column counts for each table."""
    return {name: {"rows": int(frame.shape[0]), "columns": int(frame.shape[1])} for name, frame in tables.items()}


def _add_json_shadow_columns(alerts: pd.DataFrame, raw_df: pd.DataFrame) -> pd.DataFrame:
    enriched = alerts.copy()
    for item in summarize_nested_columns(raw_df).get("nested_columns", []):
        column = item["column"]
        if column not in raw_df.columns:
            continue
        target = f"{column}_json"
        if target in enriched.columns:
            continue
        enriched[target] = raw_df[column].map(json_dumps_stable)
    return enriched


def _build_objects_table(alerts: pd.DataFrame) -> pd.DataFrame:
    if alerts.empty:
        return pd.DataFrame()
    object_keys = [column for column in ("internal_object_id", "diaObjectId", "r:diaObjectId") if column in alerts.columns]
    if not object_keys:
        return pd.DataFrame()
    columns = _available_columns(
        alerts,
        [
            "diaObjectId",
            "r:diaObjectId",
            "internal_object_id",
            "ra",
            "dec",
            "time_mjd",
            "midpointMjdTai",
            "firstDiaSourceMjdTai",
            "target_name",
            "tns_type_recomputed",
            "class_label",
            "tag",
            "publisher",
            "brokerIngestMjd",
            "lsst_schema_version",
            "fink_broker_version",
            "fink_science_version",
        ],
    )
    deduped = alerts[columns].drop_duplicates(subset=[object_keys[0]]).copy()
    return normalize_objects(deduped)


def _build_forced_table(alerts: pd.DataFrame) -> pd.DataFrame:
    if alerts.empty:
        return pd.DataFrame()
    force_markers = ("forced", "scienceflux", "templateflux", "psfflux", "fluxerr")
    forced_cols = [column for column in alerts.columns if any(marker in column.lower() for marker in force_markers)]
    if not forced_cols:
        return pd.DataFrame()
    columns = _available_columns(
        alerts,
        forced_cols
        + [
            "diaObjectId",
            "diaSourceId",
            "internal_object_id",
            "internal_source_id",
            "band",
            "midpointMjdTai",
            "time_mjd",
        ],
    )
    return normalize_forced_photometry(alerts[columns].copy())


def _build_classifications_table(alerts: pd.DataFrame) -> pd.DataFrame:
    if alerts.empty:
        return pd.DataFrame()
    markers = ("class", "tag", "clf", "pred", "tns", "publisher", "version", "reliability", "target", "observation_reason", "xm", "misc")
    class_cols = [column for column in alerts.columns if any(marker in column.lower() for marker in markers)]
    if not class_cols:
        return pd.DataFrame()
    columns = _available_columns(
        alerts,
        [
            "diaObjectId",
            "diaSourceId",
            "internal_object_id",
            "internal_source_id",
            "ra",
            "dec",
            "band",
            "midpointMjdTai",
            "time_mjd",
        ]
        + class_cols,
    )
    return alerts[columns].copy()


def _build_nightly_summary(
    alerts: pd.DataFrame,
    raw_df: pd.DataFrame,
    lightcurve_features: pd.DataFrame,
    inspection: dict[str, Any] | None,
) -> pd.DataFrame:
    time_column = next((column for column in ("time_mjd", "midpointMjdTai", "r:midpointMjdTai") if column in alerts.columns), None)
    time_series = pd.to_numeric(alerts[time_column], errors="coerce").dropna() if time_column else pd.Series(dtype="float64")
    band_column = next((column for column in ("band", "r:band") if column in alerts.columns), None)
    nested = summarize_nested_columns(raw_df)
    row = {
        "internal_table_type": "nightly_summary",
        "alert_rows": int(len(alerts)),
        "raw_rows": int(len(raw_df)),
        "unique_objects": int(alerts["internal_object_id"].nunique()) if "internal_object_id" in alerts.columns else None,
        "unique_sources": int(alerts["internal_source_id"].nunique()) if "internal_source_id" in alerts.columns else None,
        "band_values": json_dumps_stable(sorted(str(value) for value in alerts[band_column].dropna().unique())) if band_column else None,
        "time_column": time_column,
        "time_min_mjd": float(time_series.min()) if not time_series.empty else None,
        "time_max_mjd": float(time_series.max()) if not time_series.empty else None,
        "lightcurve_feature_rows": int(len(lightcurve_features)),
        "nested_columns": json_dumps_stable([item["column"] for item in nested.get("nested_columns", [])]),
        "raw_file_count": int(inspection.get("file_count", 0)) if inspection else None,
        "raw_row_count": int(inspection.get("total_rows", len(raw_df))) if inspection else int(len(raw_df)),
        "schema_group_count": int(inspection.get("schema_group_count", 0)) if inspection else None,
        "full_night_complete": False,
        "scope": COMPLETENESS_SCOPE["scope"],
        "reason": COMPLETENESS_SCOPE["reason"],
    }
    return pd.DataFrame([row])


def _iter_lc_feature_items(value: Any):
    safe = to_json_safe(value)
    if safe is None:
        return
    if isinstance(safe, dict):
        for band, features in safe.items():
            yield str(band), features
        return
    if isinstance(safe, list):
        for item in safe:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                yield str(item[0]), item[1]
            elif isinstance(item, dict) and len(item) == 1:
                band, features = next(iter(item.items()))
                yield str(band), features
            else:
                yield None, item


def _row_links(raw_row: pd.Series, normalized_row: pd.Series | None) -> dict[str, Any]:
    links: dict[str, Any] = {}
    for column in ("diaObjectId", "r:diaObjectId", "diaSourceId", "r:diaSourceId", "_raw_file", "_raw_file_index"):
        if column in raw_row.index:
            links[column] = raw_row[column]
    if normalized_row is not None:
        for column in ("internal_object_id", "internal_source_id", "time_mjd", "band", "ra", "dec"):
            if column in normalized_row.index:
                links[column] = normalized_row[column]
    return links


def _safe_column_name(value: str) -> str:
    cleaned = re.sub(r"[^0-9a-zA-Z_]+", "_", value.strip()).strip("_").lower()
    return cleaned or "value"


def _available_columns(df: pd.DataFrame, columns: list[str]) -> list[str]:
    seen = set()
    available = []
    for column in columns:
        if column in df.columns and column not in seen:
            available.append(column)
            seen.add(column)
    return available

"""Ingestion hooks for delivered Fink Data Transfer files."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from fink_lsst.normalize import normalize_forced_photometry, normalize_objects, normalize_sources
from fink_lsst.storage import write_json, write_parquet

from .output_inspector import classify_file_type, discover_delivery_files


def load_data_transfer_files(delivery_dir: str | Path) -> pd.DataFrame:
    """Load supported local delivery files into one DataFrame."""
    frames = []
    for path in discover_delivery_files(delivery_dir):
        kind = classify_file_type(path)
        if kind == "parquet":
            frames.append(pd.read_parquet(path))
        elif kind == "json":
            frames.append(pd.read_json(path, lines=Path(path).suffix.lower() == ".jsonl"))
        elif kind == "csv":
            frames.append(pd.read_csv(path))
    return pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()


def normalize_bulk_alerts(payload_or_dataframe: Any) -> pd.DataFrame:
    """Normalize bulk alert rows into the existing source-level internal model."""
    return normalize_sources(payload_or_dataframe)


def split_bulk_tables(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Split a normalized bulk DataFrame into internal tables."""
    alerts = normalize_bulk_alerts(df)
    objects = normalize_objects(alerts.drop_duplicates(subset=["internal_object_id"]) if "internal_object_id" in alerts.columns else alerts.head(0))
    forced_cols = [column for column in alerts.columns if "forced" in column.lower() or "scienceflux" in column.lower()]
    forced = normalize_forced_photometry(alerts[forced_cols + [c for c in ["internal_object_id", "internal_source_id"] if c in alerts.columns]].copy()) if forced_cols else pd.DataFrame()
    class_cols = [column for column in alerts.columns if "class" in column.lower() or "tag" in column.lower() or column.startswith("f:")]
    classification = alerts[class_cols + [c for c in ["internal_object_id", "internal_source_id"] if c in alerts.columns]].copy() if class_cols else pd.DataFrame()
    nightly_summary = pd.DataFrame(
        [
            {
                "internal_table_type": "nightly_summary",
                "alert_rows": int(len(alerts)),
                "unique_objects": int(alerts["internal_object_id"].nunique()) if "internal_object_id" in alerts.columns else None,
                "unique_sources": int(alerts["internal_source_id"].nunique()) if "internal_source_id" in alerts.columns else None,
            }
        ]
    )
    return {
        "alerts": alerts,
        "objects": objects,
        "forced_photometry": forced,
        "classification_context": classification,
        "nightly_summary": nightly_summary,
    }


def write_bulk_processed_tables(
    tables: dict[str, pd.DataFrame],
    processed_dir: str | Path,
    project_root: str | Path | None = None,
) -> dict[str, str]:
    """Write non-empty processed bulk tables."""
    processed = Path(processed_dir)
    processed.mkdir(parents=True, exist_ok=True)
    artifacts = {}
    for name, frame in tables.items():
        if frame.empty and name not in {"forced_photometry", "classification_context"}:
            continue
        if frame.empty:
            continue
        path = processed / f"{name}.parquet"
        write_parquet(frame, path, project_root=project_root)
        artifacts[name] = str(path)
    return artifacts


def write_bulk_ingestion_manifest(
    run_id: str,
    delivery_dir: str | Path,
    processed_dir: str | Path,
    artifacts: dict[str, str],
    output_path: str | Path,
) -> Path:
    """Write bulk ingestion manifest."""
    manifest = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "delivery_dir": str(delivery_dir),
        "processed_dir": str(processed_dir),
        "artifacts": artifacts,
        "complete_night_claimed": False,
    }
    return write_json(manifest, output_path, enforce_allowed_roots=False)

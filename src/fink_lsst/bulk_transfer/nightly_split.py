"""Generic UTC nightly splitting for manifest-driven runs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from fink_lsst.storage import write_parquet

from .nested import sanitize_dataframe_for_parquet


MJD_EPOCH = datetime(1858, 11, 17, tzinfo=timezone.utc)
MJD_COLUMNS = ("time_mjd", "midpointMjdTai", "r:midpointMjdTai", "firstDiaSourceMjdTai", "brokerIngestMjd")
TIMESTAMP_COLUMNS = ("timestamp", "candidate_timestamp", "alert_timestamp", "brokerIngestTimestamp", "created_at")


def derive_night_key_from_row(row: pd.Series | dict[str, Any], startdate: str | None = None, stopdate: str | None = None) -> str | None:
    """Derive a UTC night key from one row, returning out_of_window when outside the run window."""
    value = row if isinstance(row, dict) else row.to_dict()
    night = _night_from_mapping(value)
    if night is None:
        return None
    if startdate and stopdate and not (startdate <= night < stopdate):
        return "out_of_window"
    return night


def derive_night_key_from_dataframe(df: pd.DataFrame, startdate: str | None = None, stopdate: str | None = None) -> tuple[pd.Series, list[str]]:
    """Return a night-key series and warnings for a DataFrame."""
    warnings: list[str] = []
    if df.empty:
        return pd.Series(dtype="object"), warnings
    source_column = _first_present(df, MJD_COLUMNS + TIMESTAMP_COLUMNS)
    if source_column is None:
        warnings.append("no trusted time field found; rows cannot be split by night")
        return pd.Series([None] * len(df), index=df.index, dtype="object"), warnings
    keys = df.apply(lambda row: derive_night_key_from_row(row, startdate=startdate, stopdate=stopdate), axis=1)
    if keys.isna().any():
        warnings.append(f"some rows could not derive night key from {source_column}")
    return keys.astype("object"), warnings


def split_table_by_night(df: pd.DataFrame, startdate: str, stopdate: str) -> tuple[dict[str, pd.DataFrame], list[str]]:
    """Split a table by derived UTC night key without discarding rows."""
    keys, warnings = derive_night_key_from_dataframe(df, startdate=startdate, stopdate=stopdate)
    if df.empty:
        return {}, warnings
    if keys.isna().all():
        return {"unsplit": df.copy()}, warnings
    split: dict[str, pd.DataFrame] = {}
    keyed = df.copy()
    keyed["_night_key"] = keys.fillna("unknown")
    for night, group in keyed.groupby("_night_key", dropna=False):
        output = group.drop(columns=["_night_key"]).copy()
        split[str(night)] = output
    return split, warnings


def write_nightly_tables(
    tables: dict[str, pd.DataFrame],
    nights_dir: str | Path,
    startdate: str,
    stopdate: str,
    project_root: str | Path | None = None,
) -> tuple[dict[str, dict[str, str]], list[str]]:
    """Write per-night versions of each non-empty table."""
    base = Path(nights_dir)
    outputs: dict[str, dict[str, str]] = {}
    warnings: list[str] = []
    for table_name, frame in tables.items():
        if frame.empty:
            continue
        split, split_warnings = split_table_by_night(frame, startdate, stopdate)
        warnings.extend(f"{table_name}: {item}" for item in split_warnings)
        for night, night_frame in split.items():
            target = base / night / f"{table_name}.parquet"
            sanitized, _conversion = sanitize_dataframe_for_parquet(night_frame)
            write_parquet(sanitized, target, project_root=project_root)
            outputs.setdefault(night, {})[table_name] = str(target)
    return outputs, warnings


def summarize_nightly_counts(nightly_outputs_or_tables: dict[str, Any]) -> dict[str, Any]:
    """Summarize rows by night and table."""
    summary: dict[str, Any] = {}
    for night, value in nightly_outputs_or_tables.items():
        summary[night] = {}
        if isinstance(value, dict):
            for table, item in value.items():
                if isinstance(item, pd.DataFrame):
                    summary[night][table] = int(len(item))
                else:
                    summary[night][table] = str(item)
    return summary


def _night_from_mapping(row: dict[str, Any]) -> str | None:
    for column in MJD_COLUMNS:
        if column in row and pd.notna(row[column]):
            try:
                dt = MJD_EPOCH + timedelta(days=float(row[column]))
                return dt.date().isoformat()
            except Exception:  # noqa: BLE001
                continue
    for column in TIMESTAMP_COLUMNS:
        if column in row and pd.notna(row[column]):
            try:
                dt = pd.to_datetime(row[column], utc=True, errors="coerce")
                if pd.notna(dt):
                    return dt.date().isoformat()
            except Exception:  # noqa: BLE001
                continue
    return None


def _first_present(df: pd.DataFrame, candidates: tuple[str, ...]) -> str | None:
    return next((column for column in candidates if column in df.columns), None)

"""Manifest-driven processed table construction for bulk transfer rows."""

from __future__ import annotations

from typing import Any

import pandas as pd

from .ingest import expand_lc_features, split_bulk_tables


def build_alerts_table(raw_df: pd.DataFrame, manifest: Any | None = None) -> pd.DataFrame:
    """Build the alert/source-level table for a run."""
    del manifest
    return split_bulk_tables(raw_df).get("alerts", pd.DataFrame())


def build_objects_table(alerts_or_raw_df: pd.DataFrame, manifest: Any | None = None) -> pd.DataFrame:
    """Build the object table for a run."""
    del manifest
    tables = split_bulk_tables(alerts_or_raw_df)
    return tables.get("objects", pd.DataFrame())


def build_classifications_table(alerts_or_raw_df: pd.DataFrame, manifest: Any | None = None) -> pd.DataFrame:
    """Build classification/tag-like rows for a run."""
    del manifest
    return split_bulk_tables(alerts_or_raw_df).get("classifications", pd.DataFrame())


def build_forced_photometry_table(alerts_or_raw_df: pd.DataFrame, manifest: Any | None = None) -> pd.DataFrame:
    """Build forced-photometry-like rows for a run."""
    del manifest
    return split_bulk_tables(alerts_or_raw_df).get("forced_photometry", pd.DataFrame())


def build_lightcurve_features_table(raw_df: pd.DataFrame, manifest: Any | None = None) -> pd.DataFrame:
    """Build expanded light-curve feature rows for a run."""
    del manifest
    alerts = split_bulk_tables(raw_df).get("alerts", pd.DataFrame())
    return expand_lc_features(raw_df, alerts)


def build_nightly_summary_table(raw_df: pd.DataFrame, manifest: Any | None = None, inspection: dict[str, Any] | None = None) -> pd.DataFrame:
    """Build the generic nightly summary table for a run."""
    tables = split_bulk_tables(raw_df, inspection=inspection)
    summary = tables.get("nightly_summary", pd.DataFrame()).copy()
    if manifest is not None and not summary.empty:
        summary["run_name"] = getattr(manifest, "run_name", None)
        summary["scope"] = getattr(manifest, "scope", summary.get("scope"))
        summary["packet_type"] = getattr(manifest, "packet_type", None)
    return summary


def build_tables_for_run(raw_df: pd.DataFrame, manifest: Any | None = None, inspection: dict[str, Any] | None = None) -> tuple[dict[str, pd.DataFrame], list[str]]:
    """Build the shared table family for smoke, night, and multi-night runs."""
    warnings: list[str] = []
    tables = split_bulk_tables(raw_df, inspection=inspection)
    required = ("alerts", "objects", "classifications", "forced_photometry", "lightcurve_features", "nightly_summary")
    for name in required:
        tables.setdefault(name, pd.DataFrame())
        if tables[name].empty and name != "nightly_summary":
            warnings.append(f"{name} table is empty; optional/source fields may be absent")
    if manifest is not None:
        for frame in tables.values():
            if not frame.empty:
                frame["run_name"] = getattr(manifest, "run_name", None)
                frame["run_id"] = getattr(manifest, "run_id", None)
    return tables, warnings

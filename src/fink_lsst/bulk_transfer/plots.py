"""Diagnostic plot helpers for Fink Data Transfer smoke deliveries."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pandas as pd


def generate_smoke_diagnostic_plots(
    tables: dict[str, pd.DataFrame],
    inspection: dict[str, Any],
    figures_dir: str | Path,
) -> list[dict[str, Any]]:
    """Generate smoke diagnostic plots when the required fields exist."""
    figures = Path(figures_dir)
    figures.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(figures / "_matplotlib_cache"))
    alerts = tables.get("alerts", pd.DataFrame())
    classifications = tables.get("classifications", pd.DataFrame())
    outputs: list[dict[str, Any]] = []
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:  # noqa: BLE001
        return [{"name": "matplotlib", "status": "skipped", "reason": str(exc)}]

    outputs.extend(_plot_rows_by_raw_file(inspection, figures, plt))
    outputs.extend(_plot_band_counts(alerts, figures, plt))
    outputs.extend(_plot_sky_scatter(alerts, figures, plt))
    outputs.extend(_plot_time_histogram(alerts, figures, plt))
    outputs.extend(_plot_classification_counts(classifications if not classifications.empty else alerts, figures, plt))
    outputs.extend(_plot_missingness(alerts, figures, plt))
    return outputs


def _plot_rows_by_raw_file(inspection: dict[str, Any], figures: Path, plt: Any) -> list[dict[str, Any]]:
    rows = []
    for item in inspection.get("files", []):
        schema = item.get("schema", {})
        if "rows" in schema:
            rows.append({"file": Path(item.get("path", "")).name, "rows": int(schema["rows"])})
    if not rows:
        return []
    frame = pd.DataFrame(rows)
    path = figures / "rows_by_raw_file.png"
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.bar(frame["file"], frame["rows"])
    ax.set_ylabel("rows")
    ax.set_xlabel("raw file")
    ax.tick_params(axis="x", rotation=90)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return [{"name": "rows_by_raw_file", "path": str(path), "status": "written"}]


def _plot_band_counts(alerts: pd.DataFrame, figures: Path, plt: Any) -> list[dict[str, Any]]:
    column = _first_present(alerts, ("band", "r:band", "filter"))
    if not column:
        return []
    counts = alerts[column].dropna().astype(str).value_counts()
    if counts.empty:
        return []
    path = figures / "band_counts.png"
    fig, ax = plt.subplots(figsize=(6, 4))
    counts.plot(kind="bar", ax=ax)
    ax.set_ylabel("alerts")
    ax.set_xlabel(column)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return [{"name": "band_counts", "path": str(path), "status": "written"}]


def _plot_sky_scatter(alerts: pd.DataFrame, figures: Path, plt: Any) -> list[dict[str, Any]]:
    ra = _first_present(alerts, ("ra", "r:ra"))
    dec = _first_present(alerts, ("dec", "r:dec"))
    if not ra or not dec:
        return []
    points = alerts[[ra, dec]].apply(pd.to_numeric, errors="coerce").dropna()
    if points.empty:
        return []
    path = figures / "sky_scatter.png"
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.scatter(points[ra], points[dec], s=4, alpha=0.5)
    ax.set_xlabel(ra)
    ax.set_ylabel(dec)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return [{"name": "sky_scatter", "path": str(path), "status": "written"}]


def _plot_time_histogram(alerts: pd.DataFrame, figures: Path, plt: Any) -> list[dict[str, Any]]:
    column = _first_present(alerts, ("time_mjd", "midpointMjdTai", "r:midpointMjdTai"))
    if not column:
        return []
    series = pd.to_numeric(alerts[column], errors="coerce").dropna()
    if series.empty:
        return []
    path = figures / "mjd_histogram.png"
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.hist(series, bins=30)
    ax.set_xlabel(column)
    ax.set_ylabel("alerts")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return [{"name": "mjd_histogram", "path": str(path), "status": "written"}]


def _plot_classification_counts(df: pd.DataFrame, figures: Path, plt: Any) -> list[dict[str, Any]]:
    column = _first_present(df, ("tns_type_recomputed", "class_label", "tag", "target_name"))
    if not column:
        return []
    counts = df[column].dropna().astype(str).value_counts().head(20)
    if counts.empty:
        return []
    path = figures / "top_classification_counts.png"
    fig, ax = plt.subplots(figsize=(8, 4))
    counts.plot(kind="bar", ax=ax)
    ax.set_ylabel("rows")
    ax.set_xlabel(column)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return [{"name": "top_classification_counts", "path": str(path), "status": "written"}]


def _plot_missingness(alerts: pd.DataFrame, figures: Path, plt: Any) -> list[dict[str, Any]]:
    key_fields = [
        column
        for column in (
            "diaObjectId",
            "diaSourceId",
            "ra",
            "dec",
            "midpointMjdTai",
            "band",
            "scienceFlux",
            "scienceFluxErr",
            "tns_type_recomputed",
        )
        if column in alerts.columns
    ]
    if not key_fields:
        return []
    missing = alerts[key_fields].isna().mean().sort_values(ascending=False)
    path = figures / "key_field_missingness.png"
    fig, ax = plt.subplots(figsize=(8, 4))
    missing.plot(kind="bar", ax=ax)
    ax.set_ylabel("fraction missing")
    ax.set_ylim(0, 1)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return [{"name": "key_field_missingness", "path": str(path), "status": "written"}]


def _first_present(df: pd.DataFrame, candidates: tuple[str, ...]) -> str | None:
    return next((column for column in candidates if column in df.columns), None)

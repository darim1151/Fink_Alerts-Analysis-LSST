"""Matplotlib plots for quick partial full-packet stress analysis."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pandas as pd


PLOT_DPI = 180


def generate_quick_analysis_plots(
    df: pd.DataFrame,
    file_rows: pd.DataFrame,
    figures_dir: str | Path,
    metadata_summary: dict[str, Any] | None = None,
    report_summary: dict[str, Any] | None = None,
) -> list[str]:
    """Generate dense, readable data-quality plots from flattened quick-analysis outputs."""
    figures = Path(figures_dir)
    figures.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(figures / "_matplotlib_cache"))
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update(
        {
            "figure.facecolor": "white",
            "axes.facecolor": "white",
            "axes.edgecolor": "#2f3640",
            "axes.labelcolor": "#1f2933",
            "xtick.color": "#1f2933",
            "ytick.color": "#1f2933",
            "font.size": 10,
            "axes.titlesize": 13,
            "axes.titleweight": "bold",
            "axes.grid": True,
            "grid.alpha": 0.25,
        }
    )
    outputs: list[str] = []
    plotters = [
        lambda: _plot_file_row_count_distribution(file_rows, figures, plt),
        lambda: _plot_band_distribution(df, figures, plt),
        lambda: _plot_night_or_day_distribution(df, figures, plt),
        lambda: _plot_broker_ingest_mjd_distribution(df, figures, plt),
        lambda: _plot_ra_distribution(df, figures, plt),
        lambda: _plot_sky_scatter(df, figures, plt),
        lambda: _plot_object_alert_count_distribution(df, figures, plt),
        lambda: _plot_top_repeated_objects(df, figures, plt),
        lambda: _plot_classifier_cats_class_distribution(df, figures, plt),
        lambda: _plot_classifier_score_distributions(df, figures, plt),
        lambda: _plot_band_by_classifier_heatmap(df, figures, plt),
        lambda: _plot_missingness_key_fields(df, figures, plt),
        lambda: _plot_flux_snr_distribution(df, figures, plt),
        lambda: _plot_ra_dec_by_band_sample(df, figures, plt),
    ]
    for plotter in plotters:
        path = plotter()
        if path:
            outputs.append(str(path))
    return outputs


def _plot_file_row_count_distribution(file_rows: pd.DataFrame, figures: Path, plt: Any) -> Path | None:
    if file_rows.empty or "rows" not in file_rows:
        return None
    path = figures / "file_row_count_distribution.png"
    rows = pd.to_numeric(file_rows["rows"], errors="coerce").dropna()
    if rows.empty:
        return None
    fig, axes = plt.subplots(1, 2, figsize=(12, 4))
    axes[0].hist(rows, bins=50, color="#376996", edgecolor="white")
    axes[0].set_title("Rows Per Raw File")
    axes[0].set_xlabel("Rows")
    axes[0].set_ylabel("File count")
    ranked = rows.sort_values(ascending=False).reset_index(drop=True)
    axes[1].plot(ranked.index + 1, ranked.values, color="#b23a48", linewidth=1.2)
    axes[1].set_title("Ranked File Row Counts")
    axes[1].set_xlabel("File rank")
    axes[1].set_ylabel("Rows")
    fig.suptitle("Partial Full-Packet Delivery: File Row Count Structure")
    fig.tight_layout()
    fig.savefig(path, dpi=PLOT_DPI, bbox_inches="tight")
    plt.close(fig)
    return path


def _plot_band_distribution(df: pd.DataFrame, figures: Path, plt: Any) -> Path | None:
    band = _first_present(df, ("diaSource.band", "band", "r:band"))
    if not band:
        return None
    counts = df[band].dropna().astype(str).value_counts().sort_index()
    if counts.empty:
        return None
    path = figures / "band_distribution.png"
    fig, ax = plt.subplots(figsize=(7, 4))
    colors = ["#4c78a8", "#f58518", "#54a24b", "#e45756", "#72b7b2", "#b279a2"][: len(counts)]
    ax.bar(counts.index, counts.values, color=colors)
    ax.set_title("Alert Rows By Band")
    ax.set_xlabel("Band")
    ax.set_ylabel("Flattened rows")
    _annotate_bars(ax)
    fig.tight_layout()
    fig.savefig(path, dpi=PLOT_DPI, bbox_inches="tight")
    plt.close(fig)
    return path


def _plot_night_or_day_distribution(df: pd.DataFrame, figures: Path, plt: Any) -> Path | None:
    if {"year", "month", "day"}.issubset(df.columns):
        keys = df[["year", "month", "day"]].dropna().astype(int).astype(str)
        values = keys["year"] + "-" + keys["month"].str.zfill(2) + "-" + keys["day"].str.zfill(2)
    else:
        time_col = _first_present(df, ("timestamp", "brokerIngestMjd", "diaSource.midPointTai"))
        if not time_col:
            return None
        values = pd.to_numeric(df[time_col], errors="coerce").dropna().round(0).astype(int).astype(str)
    counts = values.value_counts().sort_index()
    if counts.empty:
        return None
    path = figures / "night_or_day_distribution.png"
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.bar(counts.index, counts.values, color="#5f9ea0")
    ax.set_title("Rows By Available Date/Day Key")
    ax.set_xlabel("Date/day key")
    ax.set_ylabel("Rows")
    ax.tick_params(axis="x", rotation=35)
    fig.tight_layout()
    fig.savefig(path, dpi=PLOT_DPI, bbox_inches="tight")
    plt.close(fig)
    return path


def _plot_broker_ingest_mjd_distribution(df: pd.DataFrame, figures: Path, plt: Any) -> Path | None:
    col = _first_present(df, ("brokerIngestMjd", "diaSource.midPointTai", "diaSource.midpointMjdTai"))
    if not col:
        return None
    values = pd.to_numeric(df[col], errors="coerce").dropna()
    if values.empty:
        return None
    path = figures / "broker_ingest_mjd_distribution.png"
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.hist(values, bins=60, color="#3d5a80", edgecolor="white")
    ax.set_title(f"{col} Distribution")
    ax.set_xlabel(col)
    ax.set_ylabel("Rows")
    fig.tight_layout()
    fig.savefig(path, dpi=PLOT_DPI, bbox_inches="tight")
    plt.close(fig)
    return path


def _plot_ra_distribution(df: pd.DataFrame, figures: Path, plt: Any) -> Path | None:
    col = _first_present(df, ("diaSource.ra", "ra", "r:ra"))
    if not col:
        return None
    values = pd.to_numeric(df[col], errors="coerce").dropna()
    if values.empty:
        return None
    path = figures / "sky_ra_distribution.png"
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.hist(values, bins=72, color="#6a4c93", edgecolor="white")
    ax.set_title("RA Distribution")
    ax.set_xlabel("RA [deg]")
    ax.set_ylabel("Rows")
    fig.tight_layout()
    fig.savefig(path, dpi=PLOT_DPI, bbox_inches="tight")
    plt.close(fig)
    return path


def _plot_sky_scatter(df: pd.DataFrame, figures: Path, plt: Any) -> Path | None:
    ra, dec = _ra_dec_columns(df)
    if not ra or not dec:
        return None
    data = _numeric_pair(df, ra, dec)
    if data.empty:
        return None
    data = _downsample(data, 60000, seed=11)
    path = figures / "sky_scatter_ra_dec.png"
    fig, ax = plt.subplots(figsize=(7, 5))
    ax.scatter(data[ra], data[dec], s=2, alpha=0.25, color="#1f77b4", rasterized=True)
    ax.set_title("Sky Coverage Sample")
    ax.set_xlabel("RA [deg]")
    ax.set_ylabel("Dec [deg]")
    fig.tight_layout()
    fig.savefig(path, dpi=PLOT_DPI, bbox_inches="tight")
    plt.close(fig)
    return path


def _plot_object_alert_count_distribution(df: pd.DataFrame, figures: Path, plt: Any) -> Path | None:
    col = _first_present(df, ("diaSource.diaObjectId", "diaObjectId"))
    if not col:
        return None
    values = df[col].dropna()
    if values.empty:
        return None
    counts = values.value_counts()
    nonzero = counts[counts.index.astype(str) != "0"]
    path = figures / "object_alert_count_distribution.png"
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.hist(nonzero.values if not nonzero.empty else counts.values, bins=50, color="#2a9d8f", edgecolor="white", log=True)
    ax.set_title("Alerts Per Nonzero Object")
    ax.set_xlabel("Alerts per object")
    ax.set_ylabel("Object count [log]")
    zero_count = int(counts.get(0, counts.get("0", 0)))
    if zero_count:
        ax.text(0.98, 0.95, f"diaObjectId=0 rows: {zero_count}", transform=ax.transAxes, ha="right", va="top")
    fig.tight_layout()
    fig.savefig(path, dpi=PLOT_DPI, bbox_inches="tight")
    plt.close(fig)
    return path


def _plot_top_repeated_objects(df: pd.DataFrame, figures: Path, plt: Any) -> Path | None:
    col = _first_present(df, ("diaSource.diaObjectId", "diaObjectId"))
    if not col:
        return None
    counts = df[col].dropna().value_counts()
    counts = counts[counts.index.astype(str) != "0"].head(20)
    if counts.empty:
        return None
    path = figures / "top_repeated_objects.png"
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.barh([str(item) for item in counts.index[::-1]], counts.values[::-1], color="#e76f51")
    ax.set_title("Top Repeated Nonzero Objects")
    ax.set_xlabel("Alert rows")
    ax.set_ylabel("diaObjectId")
    fig.tight_layout()
    fig.savefig(path, dpi=PLOT_DPI, bbox_inches="tight")
    plt.close(fig)
    return path


def _plot_classifier_cats_class_distribution(df: pd.DataFrame, figures: Path, plt: Any) -> Path | None:
    col = "clf.cats_class"
    if col not in df:
        return None
    counts = df[col].dropna().astype(str).value_counts().head(20)
    if counts.empty:
        return None
    path = figures / "classifier_cats_class_distribution.png"
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.barh(counts.index[::-1], counts.values[::-1], color="#8ab17d")
    ax.set_title("Classifier cats_class Distribution")
    ax.set_xlabel("Rows")
    ax.set_ylabel("cats_class")
    fig.tight_layout()
    fig.savefig(path, dpi=PLOT_DPI, bbox_inches="tight")
    plt.close(fig)
    return path


def _plot_classifier_score_distributions(df: pd.DataFrame, figures: Path, plt: Any) -> Path | None:
    cols = [col for col in ("clf.cats_score", "clf.snnSnVsOthers_score", "clf.earlySNIa_score") if col in df]
    if not cols:
        return None
    path = figures / "classifier_score_distributions.png"
    fig, axes = plt.subplots(len(cols), 1, figsize=(8, 3 * len(cols)), squeeze=False)
    for ax, col in zip(axes[:, 0], cols):
        values = pd.to_numeric(df[col], errors="coerce").dropna()
        if values.empty:
            ax.set_visible(False)
            continue
        ax.hist(values, bins=50, color="#457b9d", edgecolor="white")
        ax.set_title(col)
        ax.set_xlabel("Score")
        ax.set_ylabel("Rows")
    fig.suptitle("Classifier Score Distributions")
    fig.tight_layout()
    fig.savefig(path, dpi=PLOT_DPI, bbox_inches="tight")
    plt.close(fig)
    return path


def _plot_band_by_classifier_heatmap(df: pd.DataFrame, figures: Path, plt: Any) -> Path | None:
    band = _first_present(df, ("diaSource.band", "band"))
    cls = "clf.cats_class" if "clf.cats_class" in df else None
    if not band or not cls:
        return None
    table = pd.crosstab(df[band].astype(str), df[cls].astype(str))
    if table.empty:
        return None
    table = table.loc[:, table.sum(axis=0).sort_values(ascending=False).head(12).index]
    path = figures / "band_by_classifier_heatmap.png"
    fig, ax = plt.subplots(figsize=(max(7, table.shape[1] * 0.55), 4.5))
    image = ax.imshow(table.values, aspect="auto", cmap="viridis")
    ax.set_title("Band x cats_class Counts")
    ax.set_xlabel("cats_class")
    ax.set_ylabel("Band")
    ax.set_xticks(range(table.shape[1]), table.columns, rotation=45, ha="right")
    ax.set_yticks(range(table.shape[0]), table.index)
    fig.colorbar(image, ax=ax, label="Rows")
    fig.tight_layout()
    fig.savefig(path, dpi=PLOT_DPI, bbox_inches="tight")
    plt.close(fig)
    return path


def _plot_missingness_key_fields(df: pd.DataFrame, figures: Path, plt: Any) -> Path | None:
    keys = [
        "diaSource.diaSourceId",
        "diaSource.diaObjectId",
        "diaSource.ra",
        "diaSource.decl",
        "diaSource.band",
        "diaSource.psFlux",
        "diaSource.psFluxErr",
        "clf.cats_class",
        "clf.cats_score",
        "clf.snnSnVsOthers_score",
    ]
    present = [col for col in keys if col in df]
    if not present:
        return None
    missing = df[present].isna().mean().sort_values(ascending=False) * 100
    path = figures / "missingness_key_fields.png"
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.barh(missing.index[::-1], missing.values[::-1], color="#bc6c25")
    ax.set_title("Missingness In Key Flattened Fields")
    ax.set_xlabel("Missing [%]")
    ax.set_ylabel("Field")
    fig.tight_layout()
    fig.savefig(path, dpi=PLOT_DPI, bbox_inches="tight")
    plt.close(fig)
    return path


def _plot_flux_snr_distribution(df: pd.DataFrame, figures: Path, plt: Any) -> Path | None:
    cols = [col for col in ("diaSource.psFlux", "diaSource.psFluxErr", "diaSource.snr") if col in df]
    if not cols:
        return None
    path = figures / "flux_snr_distribution.png"
    fig, axes = plt.subplots(len(cols), 1, figsize=(8, 3 * len(cols)), squeeze=False)
    for ax, col in zip(axes[:, 0], cols):
        values = pd.to_numeric(df[col], errors="coerce").dropna()
        if values.empty:
            ax.set_visible(False)
            continue
        ax.hist(values.clip(values.quantile(0.005), values.quantile(0.995)), bins=60, color="#264653", edgecolor="white")
        ax.set_title(f"{col} Distribution (0.5-99.5% clipped)")
        ax.set_xlabel(col)
        ax.set_ylabel("Rows")
    fig.tight_layout()
    fig.savefig(path, dpi=PLOT_DPI, bbox_inches="tight")
    plt.close(fig)
    return path


def _plot_ra_dec_by_band_sample(df: pd.DataFrame, figures: Path, plt: Any) -> Path | None:
    ra, dec = _ra_dec_columns(df)
    band = _first_present(df, ("diaSource.band", "band"))
    if not ra or not dec or not band:
        return None
    data = _numeric_pair(df, ra, dec)
    if data.empty:
        return None
    data[band] = df.loc[data.index, band].astype(str)
    data = _downsample(data, 60000, seed=17)
    path = figures / "ra_dec_by_band_sample.png"
    fig, ax = plt.subplots(figsize=(7, 5))
    bands = sorted(data[band].dropna().unique())
    colors = ["#4c78a8", "#f58518", "#54a24b", "#e45756", "#72b7b2", "#b279a2", "#ff9da6"]
    for index, item in enumerate(bands):
        subset = data[data[band] == item]
        ax.scatter(subset[ra], subset[dec], s=2, alpha=0.28, label=item, color=colors[index % len(colors)], rasterized=True)
    ax.set_title("Sky Coverage By Band")
    ax.set_xlabel("RA [deg]")
    ax.set_ylabel("Dec [deg]")
    ax.legend(markerscale=4, frameon=False, title="Band")
    fig.tight_layout()
    fig.savefig(path, dpi=PLOT_DPI, bbox_inches="tight")
    plt.close(fig)
    return path


def _first_present(df: pd.DataFrame, candidates: tuple[str, ...]) -> str | None:
    return next((column for column in candidates if column in df.columns), None)


def _ra_dec_columns(df: pd.DataFrame) -> tuple[str | None, str | None]:
    return _first_present(df, ("diaSource.ra", "ra", "r:ra")), _first_present(df, ("diaSource.decl", "diaSource.dec", "dec", "r:dec"))


def _numeric_pair(df: pd.DataFrame, left: str, right: str) -> pd.DataFrame:
    data = df[[left, right]].copy()
    data[left] = pd.to_numeric(data[left], errors="coerce")
    data[right] = pd.to_numeric(data[right], errors="coerce")
    return data.dropna()


def _downsample(df: pd.DataFrame, max_rows: int, seed: int) -> pd.DataFrame:
    if len(df) <= max_rows:
        return df
    return df.sample(max_rows, random_state=seed)


def _annotate_bars(ax: Any) -> None:
    for patch in ax.patches:
        height = patch.get_height()
        ax.text(patch.get_x() + patch.get_width() / 2, height, f"{int(height):,}", ha="center", va="bottom", fontsize=8)

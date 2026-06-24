"""Lightweight plotting helpers for exploratory notebooks."""

from __future__ import annotations

import pandas as pd


def sky_scatter(df: pd.DataFrame, ra: str = "i:ra", dec: str = "i:dec", ax=None):
    """Plot RA/Dec scatter if coordinate columns are present."""
    if ra not in df.columns or dec not in df.columns:
        raise ValueError(f"Missing coordinate columns: {ra}, {dec}")
    import matplotlib.pyplot as plt

    ax = ax or plt.gca()
    ax.scatter(df[ra], df[dec], s=12, alpha=0.7)
    ax.set_xlabel(ra)
    ax.set_ylabel(dec)
    return ax


def numeric_histogram(df: pd.DataFrame, column: str, bins: int = 30, ax=None):
    """Plot a histogram for a numeric column."""
    if column not in df.columns:
        raise ValueError(f"Missing column: {column}")
    import matplotlib.pyplot as plt

    ax = ax or plt.gca()
    ax.hist(pd.to_numeric(df[column], errors="coerce").dropna(), bins=bins)
    ax.set_xlabel(column)
    ax.set_ylabel("count")
    return ax


def categorical_bar_counts(df: pd.DataFrame, column: str, limit: int = 20, ax=None):
    """Plot top category counts for a column."""
    if column not in df.columns:
        raise ValueError(f"Missing column: {column}")
    import matplotlib.pyplot as plt

    ax = ax or plt.gca()
    counts = df[column].value_counts(dropna=False).head(limit)
    counts.plot(kind="bar", ax=ax)
    ax.set_xlabel(column)
    ax.set_ylabel("count")
    return ax


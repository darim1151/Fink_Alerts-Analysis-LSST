"""Generic summary utilities for small alert/object tables."""

from __future__ import annotations

from typing import Any

import pandas as pd


def row_count(df: pd.DataFrame) -> dict[str, int]:
    """Return row and column counts."""
    return {"rows": int(len(df)), "columns": int(len(df.columns))}


def column_availability(df: pd.DataFrame, expected_columns: list[str]) -> pd.DataFrame:
    """Summarize whether expected columns are present."""
    return pd.DataFrame(
        [{"column": column, "present": column in df.columns} for column in expected_columns]
    )


def numeric_ranges(df: pd.DataFrame) -> pd.DataFrame:
    """Return min/max summaries for numeric columns."""
    numeric = df.select_dtypes(include="number")
    rows = []
    for column in numeric.columns:
        series = numeric[column].dropna()
        rows.append(
            {
                "column": column,
                "non_null": int(series.count()),
                "min": float(series.min()) if not series.empty else None,
                "max": float(series.max()) if not series.empty else None,
            }
        )
    return pd.DataFrame(rows)


def missingness_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Return missing-value counts and fractions for every column."""
    total = max(len(df), 1)
    return pd.DataFrame(
        [
            {
                "column": column,
                "missing": int(df[column].isna().sum()),
                "missing_fraction": float(df[column].isna().sum() / total),
            }
            for column in df.columns
        ]
    ).sort_values(["missing_fraction", "column"], ascending=[False, True])


def group_counts(df: pd.DataFrame, column: str, limit: int = 20) -> pd.DataFrame:
    """Count rows by a categorical column if present."""
    if column not in df.columns:
        return pd.DataFrame(columns=[column, "count"])
    counts = df[column].value_counts(dropna=False).head(limit).reset_index()
    counts.columns = [column, "count"]
    return counts


def compact_summary(df: pd.DataFrame) -> dict[str, Any]:
    """Return a compact summary suitable for manifests or notebook display."""
    return {
        "shape": row_count(df),
        "columns": list(df.columns),
        "numeric_columns": list(df.select_dtypes(include="number").columns),
    }


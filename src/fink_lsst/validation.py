"""Structured validation checks for small tabular alert samples."""

from __future__ import annotations

from typing import Any, Iterable

import pandas as pd


def result(check: str, passed: bool, message: str, severity: str = "error", **details: Any) -> dict[str, Any]:
    """Build a structured validation result."""
    return {
        "check": check,
        "passed": bool(passed),
        "severity": severity,
        "message": message,
        "details": details,
    }


def check_non_empty(df: pd.DataFrame) -> dict[str, Any]:
    """Check that a DataFrame contains at least one row."""
    return result(
        "non_empty",
        len(df) > 0,
        "DataFrame contains rows" if len(df) > 0 else "DataFrame is empty",
        severity="info" if len(df) > 0 else "warning",
        rows=len(df),
    )


def check_required_columns(df: pd.DataFrame, required_columns: Iterable[str]) -> dict[str, Any]:
    """Check that required columns are present."""
    required = list(required_columns)
    missing = [column for column in required if column not in df.columns]
    return result(
        "required_columns",
        not missing,
        "All required columns are present" if not missing else "Required columns are missing",
        required=required,
        missing=missing,
    )


def check_coordinate_ranges(
    df: pd.DataFrame,
    ra_columns: Iterable[str] = ("i:ra", "ra", "d:ra"),
    dec_columns: Iterable[str] = ("i:dec", "dec", "d:dec"),
) -> list[dict[str, Any]]:
    """Validate RA/Dec ranges for any present coordinate columns."""
    checks: list[dict[str, Any]] = []
    for column in ra_columns:
        if column in df.columns:
            series = pd.to_numeric(df[column], errors="coerce").dropna()
            passed = bool(series.between(0, 360).all())
            checks.append(
                result(
                    "ra_range",
                    passed,
                    f"{column} values are within [0, 360]" if passed else f"{column} has values outside [0, 360]",
                    column=column,
                    min=float(series.min()) if not series.empty else None,
                    max=float(series.max()) if not series.empty else None,
                )
            )
    for column in dec_columns:
        if column in df.columns:
            series = pd.to_numeric(df[column], errors="coerce").dropna()
            passed = bool(series.between(-90, 90).all())
            checks.append(
                result(
                    "dec_range",
                    passed,
                    f"{column} values are within [-90, 90]" if passed else f"{column} has values outside [-90, 90]",
                    column=column,
                    min=float(series.min()) if not series.empty else None,
                    max=float(series.max()) if not series.empty else None,
                )
            )
    if not checks:
        checks.append(result("coordinate_ranges", True, "No coordinate columns found", severity="info"))
    return checks


def check_time_sanity(
    df: pd.DataFrame,
    time_columns: Iterable[str] = ("i:mjd", "mjd", "midpointMjdTai", "i:jd", "jd", "i:jd_start"),
) -> list[dict[str, Any]]:
    """Validate broad MJD/JD sanity ranges for any present time columns."""
    checks: list[dict[str, Any]] = []
    for column in time_columns:
        if column not in df.columns:
            continue
        series = pd.to_numeric(df[column], errors="coerce").dropna()
        column_lower = column.lower()
        lower, upper = (30_000, 100_000) if "mjd" in column_lower else (2_400_000, 2_500_000) if "jd" in column_lower else (30_000, 100_000)
        passed = bool(series.between(lower, upper).all())
        checks.append(
            result(
                "time_sanity",
                passed,
                f"{column} values are within a broad expected range"
                if passed
                else f"{column} has values outside a broad expected range",
                column=column,
                expected_min=lower,
                expected_max=upper,
                min=float(series.min()) if not series.empty else None,
                max=float(series.max()) if not series.empty else None,
            )
        )
    if not checks:
        checks.append(result("time_sanity", True, "No recognized time columns found", severity="info"))
    return checks


def check_duplicate_keys(df: pd.DataFrame, key_columns: Iterable[str]) -> dict[str, Any]:
    """Check duplicate rows for one or more key columns."""
    keys = list(key_columns)
    missing = [column for column in keys if column not in df.columns]
    if missing:
        return result(
            "duplicate_keys",
            False,
            "Cannot check duplicate keys because columns are missing",
            key_columns=keys,
            missing=missing,
        )
    duplicate_count = int(df.duplicated(subset=keys).sum())
    return result(
        "duplicate_keys",
        duplicate_count == 0,
        "No duplicate keys found" if duplicate_count == 0 else "Duplicate keys found",
        key_columns=keys,
        duplicate_count=duplicate_count,
    )


def check_id_presence(df: pd.DataFrame, id_columns: Iterable[str]) -> dict[str, Any]:
    """Check whether at least one configured identifier column has non-null values."""
    columns = [column for column in id_columns if column in df.columns]
    if not columns:
        return result(
            "id_presence",
            False,
            "No expected ID columns are present",
            severity="warning",
            expected=list(id_columns),
        )
    non_null = {column: int(df[column].notna().sum()) for column in columns}
    passed = any(count > 0 for count in non_null.values())
    return result(
        "id_presence",
        passed,
        "At least one ID column has values" if passed else "ID columns are present but empty",
        severity="info" if passed else "warning",
        non_null=non_null,
    )


def check_numeric_sanity(
    df: pd.DataFrame,
    columns: Iterable[str] = ("mag", "mag_err", "flux", "flux_err"),
) -> list[dict[str, Any]]:
    """Run broad sanity checks for optional magnitude/flux fields."""
    checks: list[dict[str, Any]] = []
    for column in columns:
        if column not in df.columns:
            continue
        series = pd.to_numeric(df[column], errors="coerce").dropna()
        if series.empty:
            checks.append(
                result(
                    "numeric_sanity",
                    True,
                    f"{column} has no numeric values to validate",
                    severity="info",
                    column=column,
                )
            )
            continue
        if "mag" in column and "err" not in column:
            passed = bool(series.between(-50, 50).all())
            message = f"{column} values are within broad magnitude range"
        elif "err" in column:
            passed = bool((series >= 0).all())
            message = f"{column} uncertainty values are non-negative"
        else:
            passed = True
            message = f"{column} numeric range recorded"
        checks.append(
            result(
                "numeric_sanity",
                passed,
                message if passed else f"{column} failed broad numeric sanity check",
                severity="info" if passed else "warning",
                column=column,
                min=float(series.min()),
                max=float(series.max()),
            )
        )
    if not checks:
        checks.append(result("numeric_sanity", True, "No magnitude/flux columns found", severity="info"))
    return checks


def check_provenance_columns(df: pd.DataFrame) -> dict[str, Any]:
    """Check lightweight provenance columns added by normalization where available."""
    expected = ["internal_table_type", "_unmapped_fields"]
    present = [column for column in expected if column in df.columns]
    return result(
        "provenance_columns",
        len(present) == len(expected),
        "Normalization provenance columns are present"
        if len(present) == len(expected)
        else "Some normalization provenance columns are missing",
        severity="info" if len(present) == len(expected) else "warning",
        expected=expected,
        present=present,
    )


def validate_normalized_table(df: pd.DataFrame, table_type: str) -> list[dict[str, Any]]:
    """Validate a normalized minimal-ingestion table."""
    requirements = {
        "sources": ["internal_table_type"],
        "objects": ["internal_table_type"],
        "forced_photometry": ["internal_table_type"],
        "statistics": ["internal_table_type"],
        "tags": ["internal_table_type"],
    }
    id_columns = {
        "sources": ["internal_source_id", "internal_object_id", "diaSourceId", "diaObjectId", "r:diaSourceId", "r:diaObjectId"],
        "objects": ["internal_object_id", "diaObjectId", "r:diaObjectId"],
        "forced_photometry": ["internal_object_id", "internal_source_id", "diaObjectId", "diaForcedSourceId", "r:diaObjectId"],
        "statistics": ["f:night", "night"],
        "tags": ["tag"],
    }
    duplicate_keys = {
        "sources": ["internal_source_id"],
        "objects": ["internal_object_id"],
        "forced_photometry": ["internal_source_id"],
        "statistics": ["f:night"],
        "tags": ["tag"],
    }
    checks = [check_non_empty(df)]
    checks.append(check_required_columns(df, requirements.get(table_type, [])))
    checks.append(check_id_presence(df, id_columns.get(table_type, [])))
    checks.append(check_provenance_columns(df))
    checks.extend(check_coordinate_ranges(df, ra_columns=("ra", "r:ra"), dec_columns=("dec", "r:dec")))
    checks.extend(
        check_time_sanity(
            df,
            time_columns=("time_mjd", "midpointMjdTai", "r:midpointMjdTai", "firstDiaSourceMjdTai"),
        )
    )
    checks.extend(check_numeric_sanity(df))
    keys = [column for column in duplicate_keys.get(table_type, []) if column in df.columns]
    if keys and df[keys[0]].notna().any():
        checks.append(check_duplicate_keys(df, keys))
    else:
        checks.append(
            result(
                "duplicate_keys",
                True,
                "No duplicate-key check run because key columns are unavailable or empty",
                severity="info",
                candidate_keys=duplicate_keys.get(table_type, []),
            )
        )
    return checks


def validate_basic_dataframe(
    df: pd.DataFrame,
    required_columns: Iterable[str] | None = None,
    duplicate_key_columns: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """Run a compact set of generic validation checks."""
    checks = [check_non_empty(df)]
    if required_columns:
        checks.append(check_required_columns(df, required_columns))
    checks.extend(check_coordinate_ranges(df))
    checks.extend(check_time_sanity(df))
    if duplicate_key_columns:
        checks.append(check_duplicate_keys(df, duplicate_key_columns))
    return checks

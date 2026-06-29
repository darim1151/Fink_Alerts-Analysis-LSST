"""Smoke-level diagnostics for real Fink Data Transfer deliveries."""

from __future__ import annotations

from typing import Any

import pandas as pd


def summarize_alert_delivery(df: pd.DataFrame) -> dict[str, Any]:
    """Summarize alert-level delivery size and provenance fields."""
    return {
        "rows": int(len(df)),
        "columns": int(len(df.columns)),
        "column_names": list(df.columns),
        "raw_files": int(df["_raw_file"].nunique()) if "_raw_file" in df.columns else None,
    }


def summarize_object_counts(df: pd.DataFrame) -> dict[str, Any]:
    """Summarize object/source counts from any available ID aliases."""
    object_column = _first_present(df, ("internal_object_id", "diaObjectId", "r:diaObjectId"))
    source_column = _first_present(df, ("internal_source_id", "diaSourceId", "r:diaSourceId"))
    return {
        "object_column": object_column,
        "source_column": source_column,
        "unique_objects": int(df[object_column].nunique()) if object_column else None,
        "unique_sources": int(df[source_column].nunique()) if source_column else None,
    }


def summarize_band_counts(df: pd.DataFrame) -> dict[str, Any]:
    """Summarize band/filter counts."""
    column = _first_present(df, ("band", "r:band", "filter"))
    if not column:
        return {"column": None, "counts": {}}
    return {"column": column, "counts": _value_counts(df[column])}


def summarize_time_distribution(df: pd.DataFrame) -> dict[str, Any]:
    """Summarize available time/MJD fields."""
    fields = {}
    for column in ("time_mjd", "midpointMjdTai", "r:midpointMjdTai", "brokerIngestMjd", "timestamp"):
        if column not in df.columns:
            continue
        series = pd.to_numeric(df[column], errors="coerce").dropna()
        fields[column] = {
            "non_null": int(df[column].notna().sum()),
            "numeric_count": int(len(series)),
            "min": float(series.min()) if not series.empty else None,
            "max": float(series.max()) if not series.empty else None,
        }
    return {"fields": fields}


def summarize_sky_distribution(df: pd.DataFrame) -> dict[str, Any]:
    """Summarize RA/Dec field availability and ranges."""
    ra = _first_present(df, ("ra", "r:ra"))
    dec = _first_present(df, ("dec", "r:dec"))
    summary: dict[str, Any] = {"ra_column": ra, "dec_column": dec}
    for name, column in (("ra", ra), ("dec", dec)):
        if not column:
            continue
        series = pd.to_numeric(df[column], errors="coerce").dropna()
        summary[name] = {
            "non_null": int(df[column].notna().sum()),
            "min": float(series.min()) if not series.empty else None,
            "max": float(series.max()) if not series.empty else None,
        }
    return summary


def summarize_flux_or_magnitude_fields(df: pd.DataFrame) -> dict[str, Any]:
    """Summarize flux/magnitude-like numeric fields."""
    fields = {}
    for column in df.columns:
        lowered = column.lower()
        if not any(marker in lowered for marker in ("flux", "mag")):
            continue
        series = pd.to_numeric(df[column], errors="coerce").dropna()
        fields[column] = {
            "non_null": int(df[column].notna().sum()),
            "numeric_count": int(len(series)),
            "min": float(series.min()) if not series.empty else None,
            "max": float(series.max()) if not series.empty else None,
        }
    return {"fields": fields}


def summarize_classification_fields(df: pd.DataFrame) -> dict[str, Any]:
    """Summarize classification/tag-like fields."""
    fields = {}
    markers = ("class", "tag", "clf", "pred", "tns", "reliability", "target_name", "observation_reason")
    for column in df.columns:
        if not any(marker in column.lower() for marker in markers):
            continue
        fields[column] = {
            "non_null": int(df[column].notna().sum()),
            "top_values": _value_counts(df[column], limit=10),
        }
    return {"fields": fields}


def summarize_lc_features(df_or_nested_report: pd.DataFrame | dict[str, Any]) -> dict[str, Any]:
    """Summarize light-curve feature expansion or nested conversion evidence."""
    if isinstance(df_or_nested_report, pd.DataFrame):
        df = df_or_nested_report
        if df.empty:
            return {"rows": 0, "feature_columns": [], "bands": {}}
        feature_columns = [column for column in df.columns if column.startswith("feature_")]
        return {
            "rows": int(len(df)),
            "feature_columns": feature_columns,
            "bands": _value_counts(df["lc_feature_band"]) if "lc_feature_band" in df.columns else {},
        }
    nested_report = df_or_nested_report
    converted = nested_report.get("converted_columns", {}) if isinstance(nested_report, dict) else {}
    return {"rows": None, "feature_columns": [], "nested_conversion_columns": converted}


def build_smoke_science_readiness_report(
    tables: dict[str, pd.DataFrame],
    inspection: dict[str, Any] | None = None,
    validation_status: str | None = None,
    nested_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a compact smoke science-readiness report."""
    return build_science_readiness_report(
        tables,
        inspection=inspection,
        validation_status=validation_status,
        nested_report=nested_report,
        scope="tag_filtered_smoke_delivery",
    )


def build_science_readiness_report(
    tables: dict[str, pd.DataFrame],
    inspection: dict[str, Any] | None = None,
    validation_status: str | None = None,
    nested_report: dict[str, Any] | None = None,
    scope: str = "tag_filtered_smoke_delivery",
) -> dict[str, Any]:
    """Build a compact science-readiness report for a delivered Data Transfer scope."""
    alerts = tables.get("alerts", pd.DataFrame())
    lightcurve_features = tables.get("lightcurve_features", pd.DataFrame())
    classifications = tables.get("classifications", pd.DataFrame())
    is_full_night = scope == "full_night_all_alerts"
    diagnostics = {
        "scope": scope,
        "validation_status": validation_status,
        "raw": {
            "file_count": int((inspection or {}).get("file_count", 0)),
            "row_count": int((inspection or {}).get("total_rows", 0)),
            "nested_columns": (inspection or {}).get("nested_columns", []),
            "schema_group_count": (inspection or {}).get("schema_group_count", 0),
        },
        "alerts": summarize_alert_delivery(alerts),
        "objects": summarize_object_counts(alerts),
        "bands": summarize_band_counts(alerts),
        "time": summarize_time_distribution(alerts),
        "sky": summarize_sky_distribution(alerts),
        "flux_or_magnitude": summarize_flux_or_magnitude_fields(alerts),
        "classifications": summarize_classification_fields(classifications if not classifications.empty else alerts),
        "lightcurve_features": summarize_lc_features(lightcurve_features if not lightcurve_features.empty else (nested_report or {})),
        "promising_first_notebook_fields": _promising_fields(alerts, lightcurve_features, classifications),
        "interpretation": _interpretation_lines(is_full_night),
    }
    diagnostics["markdown"] = render_science_diagnostics_markdown(diagnostics)
    return diagnostics


def render_smoke_diagnostics_markdown(report: dict[str, Any]) -> str:
    """Render smoke diagnostics as Markdown."""
    return render_science_diagnostics_markdown(report)


def render_science_diagnostics_markdown(report: dict[str, Any]) -> str:
    """Render Data Transfer diagnostics as Markdown."""
    raw = report.get("raw", {})
    objects = report.get("objects", {})
    title = "Full-Night Data Transfer Diagnostics" if report.get("scope") == "full_night_all_alerts" else "Smoke Data Transfer Diagnostics"
    lines = [
        f"# {title}",
        "",
        f"- Scope: `{report.get('scope')}`",
        f"- Validation status: `{report.get('validation_status')}`",
        f"- Raw files: `{raw.get('file_count')}`",
        f"- Raw rows: `{raw.get('row_count')}`",
        f"- Schema groups: `{raw.get('schema_group_count')}`",
        f"- Nested raw columns: `{', '.join(raw.get('nested_columns') or []) or 'none'}`",
        f"- Alert rows: `{report.get('alerts', {}).get('rows')}`",
        f"- Unique objects: `{objects.get('unique_objects')}`",
        f"- Unique sources: `{objects.get('unique_sources')}`",
        "",
        "## Bands",
        "",
    ]
    for key, value in report.get("bands", {}).get("counts", {}).items():
        lines.append(f"- `{key}`: `{value}`")
    if not report.get("bands", {}).get("counts"):
        lines.append("- No band/filter field found.")
    lines.extend(["", "## Time Fields", ""])
    for key, value in report.get("time", {}).get("fields", {}).items():
        lines.append(f"- `{key}`: `{value}`")
    if not report.get("time", {}).get("fields"):
        lines.append("- No time fields found.")
    lines.extend(["", "## Sky Fields", "", f"- RA column: `{report.get('sky', {}).get('ra_column')}`", f"- Dec column: `{report.get('sky', {}).get('dec_column')}`"])
    lines.extend(["", "## Classification Fields", ""])
    for key in report.get("classifications", {}).get("fields", {}):
        lines.append(f"- `{key}`")
    if not report.get("classifications", {}).get("fields"):
        lines.append("- No classification/tag fields found.")
    lines.extend(["", "## Light-Curve Features", ""])
    lc = report.get("lightcurve_features", {})
    lines.append(f"- Expanded rows: `{lc.get('rows')}`")
    if lc.get("feature_columns"):
        lines.append(f"- Feature columns: `{', '.join(lc['feature_columns'][:30])}`")
    lines.extend(["", "## Promising First Notebook Fields", ""])
    for item in report.get("promising_first_notebook_fields", []):
        lines.append(f"- `{item}`")
    lines.extend(["", "## Interpretation", ""])
    for item in report.get("interpretation", []):
        lines.append(f"- {item}")
    return "\n".join(lines) + "\n"


def _interpretation_lines(is_full_night: bool) -> list[str]:
    if is_full_night:
        return [
            "Data Transfer access and consumption are evidenced by raw files when file_count > 0.",
            "This delivery is intended as an all-alert full-night census; completeness depends on validation evidence, recorded topic scope, and Kafka lag metadata.",
            "Diagnostics support production readiness and data-quality review before population-level astrophysical claims.",
        ]
    return [
        "Data Transfer access and consumption are evidenced by raw files when file_count > 0.",
        "This smoke delivery is tag-filtered and is not a complete full-night alert census.",
        "Diagnostics are for data quality/readiness, not population-level astrophysical claims.",
    ]


def _promising_fields(alerts: pd.DataFrame, lightcurve_features: pd.DataFrame, classifications: pd.DataFrame) -> list[str]:
    fields: list[str] = []
    for column in ("diaObjectId", "diaSourceId", "ra", "dec", "midpointMjdTai", "band", "scienceFlux", "scienceFluxErr"):
        if column in alerts.columns:
            fields.append(column)
    for column in ("tns_type_recomputed", "reliability", "target_name"):
        if column in alerts.columns or column in classifications.columns:
            fields.append(column)
    fields.extend([column for column in lightcurve_features.columns if column.startswith("feature_")][:10])
    return fields


def _first_present(df: pd.DataFrame, candidates: tuple[str, ...]) -> str | None:
    return next((column for column in candidates if column in df.columns), None)


def _value_counts(series: pd.Series, limit: int = 20) -> dict[str, int]:
    serializable = series.dropna().map(lambda value: str(value)[:200])
    return {str(key): int(value) for key, value in serializable.value_counts().head(limit).items()}

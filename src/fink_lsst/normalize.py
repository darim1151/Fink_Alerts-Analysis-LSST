"""Normalize tiny Fink LSST REST responses into preliminary internal tables."""

from __future__ import annotations

import json
from typing import Any

import pandas as pd


ALIASES = {
    "internal_object_id": ["diaObjectId", "r:diaObjectId", "objectId"],
    "internal_source_id": ["diaSourceId", "r:diaSourceId", "diaForcedSourceId", "r:diaForcedSourceId"],
    "ra": ["ra", "r:ra"],
    "dec": ["dec", "r:dec"],
    "time_mjd": ["midpointMjdTai", "r:midpointMjdTai", "firstDiaSourceMjdTai", "f:firstDiaSourceMjdTaiFink"],
    "band": ["band", "r:band"],
    "mag": ["mag", "r:mag"],
    "mag_err": ["magErr", "r:magErr"],
    "flux": ["psfFlux", "r:psfFlux", "scienceFlux", "r:scienceFlux"],
    "flux_err": ["psfFluxErr", "r:psfFluxErr", "scienceFluxErr", "r:scienceFluxErr"],
    "class_label": ["main_label_classifier", "f:main_label_classifier", "clf_cats_class", "f:clf_cats_class"],
    "tag": ["tag", "f:tag"],
}


def normalize_sources(payload: Any) -> pd.DataFrame:
    """Normalize source/alert-level payloads into a preliminary DataFrame."""
    return _normalize_payload(payload, table_type="sources")


def normalize_objects(payload: Any) -> pd.DataFrame:
    """Normalize object-level payloads into a preliminary DataFrame."""
    return _normalize_payload(payload, table_type="objects")


def normalize_forced_photometry(payload: Any) -> pd.DataFrame:
    """Normalize forced-photometry payloads into a preliminary DataFrame."""
    return _normalize_payload(payload, table_type="forced_photometry")


def normalize_tags_or_classifications(payload: Any) -> pd.DataFrame:
    """Normalize tag/classification dictionaries or tabular responses."""
    if isinstance(payload, dict) and not _looks_tabular_dict(payload):
        rows = []
        for tag, meta in payload.items():
            row = {"tag": tag}
            if isinstance(meta, dict):
                row.update(meta)
            else:
                row["value"] = meta
            rows.append(row)
        return _add_internal_columns(pd.DataFrame(rows), "tags")
    return _normalize_payload(payload, table_type="tags")


def normalize_statistics(payload: Any) -> pd.DataFrame:
    """Normalize statistics payloads into a preliminary DataFrame."""
    return _normalize_payload(payload, table_type="statistics")


def payload_to_dataframe(payload: Any) -> pd.DataFrame:
    """Convert common JSON response shapes into a DataFrame without dropping fields."""
    if payload is None:
        return pd.DataFrame()
    if isinstance(payload, pd.DataFrame):
        return payload.copy()
    if isinstance(payload, list):
        if payload and all(isinstance(item, list) for item in payload):
            flattened = [row for item in payload for row in item]
            return pd.DataFrame(flattened)
        if payload and all(isinstance(item, dict) and "data" in item for item in payload):
            frames = [payload_to_dataframe(item.get("data")) for item in payload]
            return pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()
        return pd.DataFrame(payload)
    if isinstance(payload, dict):
        for key in ("data", "results", "rows", "items", "alerts", "objects", "sources"):
            value = payload.get(key)
            if isinstance(value, list):
                return pd.DataFrame(value)
            if isinstance(value, dict):
                return payload_to_dataframe(value)
        if _looks_tabular_dict(payload):
            return pd.DataFrame(payload)
        return pd.DataFrame([payload])
    return pd.DataFrame([{"value": payload}])


def _normalize_payload(payload: Any, table_type: str) -> pd.DataFrame:
    df = payload_to_dataframe(payload)
    if df.empty:
        df.attrs["diagnostics"] = {"empty": True, "table_type": table_type}
        return df
    return _add_internal_columns(df, table_type)


def _add_internal_columns(df: pd.DataFrame, table_type: str) -> pd.DataFrame:
    normalized = df.copy()
    original_columns = list(normalized.columns)
    mapped_originals: set[str] = set()
    normalized["internal_table_type"] = table_type
    for internal_name, aliases in ALIASES.items():
        source_column = next((column for column in aliases if column in normalized.columns), None)
        if source_column is not None:
            normalized[internal_name] = normalized[source_column]
            mapped_originals.add(source_column)
    if "time_mjd" in normalized.columns:
        normalized["time_mjd"] = pd.to_numeric(normalized["time_mjd"], errors="coerce")
    for numeric_column in ("ra", "dec", "mag", "mag_err", "flux", "flux_err"):
        if numeric_column in normalized.columns:
            normalized[numeric_column] = pd.to_numeric(normalized[numeric_column], errors="coerce")
    unmapped = [column for column in original_columns if column not in mapped_originals]
    normalized["_unmapped_fields"] = json.dumps(unmapped)
    normalized.attrs["diagnostics"] = {
        "empty": False,
        "table_type": table_type,
        "original_columns": original_columns,
        "mapped_columns": sorted(mapped_originals),
        "unmapped_fields": unmapped,
    }
    return normalized


def _looks_tabular_dict(payload: dict[str, Any]) -> bool:
    values = list(payload.values())
    return bool(values) and all(isinstance(value, list) for value in values)

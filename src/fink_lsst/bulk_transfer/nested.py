"""Nested-field handling for real Fink Data Transfer payloads."""

from __future__ import annotations

import base64
import json
import math
from collections.abc import Mapping
from typing import Any

import pandas as pd

try:  # Optional, but normally present through pandas/pyarrow.
    import numpy as np
except ImportError:  # pragma: no cover - numpy is available in the test env via pandas
    np = None  # type: ignore


def is_null_like(value: Any) -> bool:
    """Return true for pandas/numpy/null scalar values."""
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    if isinstance(value, (Mapping, list, tuple, set)):
        return False
    if np is not None and isinstance(value, np.ndarray):
        return False
    if isinstance(value, pd.Series):
        return False
    try:
        result = pd.isna(value)
        if isinstance(result, bool):
            return result
        if np is not None and isinstance(result, np.bool_):
            return bool(result)
    except (TypeError, ValueError):
        return False
    return False


def is_bytes_like(value: Any) -> bool:
    """Return true for bytes-like values."""
    return isinstance(value, (bytes, bytearray, memoryview))


def is_nested_value(value: Any) -> bool:
    """Return true if a value needs serialization before Parquet writing."""
    if is_null_like(value) or is_bytes_like(value):
        return False
    if isinstance(value, (Mapping, list, tuple, set)):
        return True
    if np is not None and isinstance(value, np.ndarray):
        return True
    if isinstance(value, pd.Series):
        return True
    return False


def to_json_safe(value: Any) -> Any:
    """Convert nested/scalar values into a JSON-safe representation."""
    if is_null_like(value):
        return None
    if is_bytes_like(value):
        data = bytes(value)
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return {"__bytes_base64__": base64.b64encode(data).decode("ascii")}
    if np is not None:
        if isinstance(value, np.ndarray):
            return [to_json_safe(item) for item in value.tolist()]
        if isinstance(value, np.generic):
            return to_json_safe(value.item())
    if isinstance(value, pd.Series):
        return {str(key): to_json_safe(item) for key, item in value.to_dict().items()}
    if isinstance(value, Mapping):
        return {str(key): to_json_safe(item) for key, item in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (list, tuple)):
        return [to_json_safe(item) for item in value]
    if isinstance(value, set):
        return [to_json_safe(item) for item in sorted(value, key=str)]
    if isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return str(value)


def json_dumps_stable(value: Any) -> str | None:
    """Serialize a value to stable JSON, preserving nulls as null."""
    safe = to_json_safe(value)
    if safe is None:
        return None
    return json.dumps(safe, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sanitize_series_for_parquet(series: pd.Series, strategy: str = "json") -> tuple[pd.Series, dict[str, Any]]:
    """Sanitize one Series for Parquet writing and return a conversion report."""
    if strategy != "json":
        raise ValueError("Only strategy='json' is currently supported")
    examples = []
    nested_count = 0
    bytes_count = 0
    type_counts: dict[str, int] = {}
    converted_values = []
    should_convert = False
    non_null_type_categories: set[str] = set()
    for value in series:
        type_name = type(value).__name__
        type_counts[type_name] = type_counts.get(type_name, 0) + 1
        nested = is_nested_value(value)
        bytes_like = is_bytes_like(value)
        if not is_null_like(value):
            non_null_type_categories.add(_type_category(value))
        if nested or bytes_like:
            should_convert = True
            nested_count += int(nested)
            bytes_count += int(bytes_like)
            if len(examples) < 3:
                examples.append({"type": type_name, "json_preview": str(json_dumps_stable(value))[:500]})
    mixed_scalar = bool(str(series.dtype) == "object" and len(non_null_type_categories - {"null"}) > 1)
    if mixed_scalar:
        should_convert = True
        if len(examples) < 3:
            examples.append(
                {
                    "type": "mixed_scalar",
                    "json_preview": f"categories={sorted(non_null_type_categories)}",
                }
            )
    if not should_convert:
        return series.copy(), {
            "column": series.name,
            "converted": False,
            "strategy": strategy,
            "nested_count": nested_count,
            "bytes_count": bytes_count,
            "mixed_scalar": mixed_scalar,
            "type_counts": type_counts,
            "examples": examples,
        }
    for value in series:
        converted_values.append(json_dumps_stable(value))
    sanitized = pd.Series(converted_values, name=series.name, index=series.index, dtype="object")
    return sanitized, {
        "column": series.name,
        "converted": True,
        "strategy": strategy,
        "nested_count": nested_count,
        "bytes_count": bytes_count,
        "mixed_scalar": mixed_scalar,
        "type_counts": type_counts,
        "examples": examples,
    }


def sanitize_dataframe_for_parquet(df: pd.DataFrame, strategy: str = "json") -> tuple[pd.DataFrame, dict[str, Any]]:
    """Sanitize all nested/mixed columns in a DataFrame before Parquet writing."""
    sanitized = df.copy()
    reports = []
    for column in sanitized.columns:
        series, report = sanitize_series_for_parquet(sanitized[column], strategy=strategy)
        sanitized[column] = series
        reports.append(report)
    converted = [report["column"] for report in reports if report.get("converted")]
    return sanitized, {
        "strategy": strategy,
        "columns": reports,
        "converted_columns": converted,
        "converted_column_count": len(converted),
    }


def summarize_nested_columns(df: pd.DataFrame, max_examples: int = 3) -> dict[str, Any]:
    """Summarize nested/object columns in a DataFrame."""
    rows = []
    for column in df.columns:
        series = df[column]
        nested_count = 0
        bytes_count = 0
        examples = []
        type_counts: dict[str, int] = {}
        for value in series:
            type_name = type(value).__name__
            type_counts[type_name] = type_counts.get(type_name, 0) + 1
            nested = is_nested_value(value)
            bytes_like = is_bytes_like(value)
            nested_count += int(nested)
            bytes_count += int(bytes_like)
            if (nested or bytes_like) and len(examples) < max_examples:
                examples.append({"type": type_name, "json_preview": str(json_dumps_stable(value))[:500]})
        if nested_count or bytes_count:
            rows.append(
                {
                    "column": column,
                    "dtype": str(series.dtype),
                    "nested_count": nested_count,
                    "bytes_count": bytes_count,
                    "type_counts": type_counts,
                    "examples": examples,
                }
            )
    return {"nested_columns": rows, "nested_column_count": len(rows)}


def _type_category(value: Any) -> str:
    if is_null_like(value):
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, str):
        return "str"
    if isinstance(value, int) and not isinstance(value, bool):
        return "number"
    if isinstance(value, float):
        return "number"
    if np is not None and isinstance(value, np.generic):
        return _type_category(value.item())
    if is_bytes_like(value):
        return "bytes"
    if is_nested_value(value):
        return "nested"
    return type(value).__name__

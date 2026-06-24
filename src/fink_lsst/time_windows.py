"""UTC date-window helpers for Fink LSST alert extraction."""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any, Iterable

import pandas as pd


def parse_utc_date(date_string: str) -> date:
    """Parse a UTC calendar date string without consulting local timezone."""
    text = str(date_string).strip()
    if "T" in text:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).date()
    return date.fromisoformat(text)


def validate_alert_start_date(startdate: str, min_lsst_alert_date_utc: str) -> None:
    """Reject target windows before the configured Rubin/Fink public-alert start date."""
    start = parse_utc_date(startdate)
    minimum = parse_utc_date(min_lsst_alert_date_utc)
    if start < minimum:
        raise ValueError(
            f"target_startdate {start.isoformat()} is before min_lsst_alert_date_utc {minimum.isoformat()}"
        )


def validate_utc_timezone(config: dict[str, Any]) -> None:
    """Require UTC unless the config includes an explicit documented override."""
    timezone_name = str(config.get("timezone", "UTC")).upper()
    if timezone_name == "UTC":
        return
    reason = str(config.get("timezone_override_reason", "")).strip()
    if not reason:
        raise ValueError("full_night_feasibility.timezone must be UTC unless timezone_override_reason is documented")


def build_utc_date_window(startdate: str, stopdate: str) -> dict[str, Any]:
    """Build a UTC calendar date window with an exclusive stopdate convention."""
    start = parse_utc_date(startdate)
    stop = parse_utc_date(stopdate)
    if stop <= start:
        raise ValueError("target_stopdate must be after target_startdate")
    return {
        "startdate": start.isoformat(),
        "stopdate": stop.isoformat(),
        "timezone": "UTC",
        "stopdate_convention": "exclusive_assumed",
    }


def split_utc_window(startdate: str, stopdate: str, count: int) -> list[dict[str, Any]]:
    """Split a UTC date window into `count` half-open UTC intervals."""
    if count <= 0:
        raise ValueError("partition count must be positive")
    start_date = parse_utc_date(startdate)
    stop_date = parse_utc_date(stopdate)
    start = datetime.combine(start_date, time.min, tzinfo=timezone.utc)
    stop = datetime.combine(stop_date, time.min, tzinfo=timezone.utc)
    if stop <= start:
        raise ValueError("stopdate must be after startdate")
    step = (stop - start) / count
    windows = []
    for index in range(count):
        current_start = start + step * index
        current_stop = start + step * (index + 1)
        windows.append(
            {
                "index": index,
                "startdate": _format_payload_datetime(current_start),
                "stopdate": _format_payload_datetime(current_stop),
                "timezone": "UTC",
                "stopdate_convention": "exclusive_assumed",
            }
        )
    return windows


def date_window_to_payload(startdate: str, stopdate: str) -> dict[str, str]:
    """Return the REST payload fields for a UTC date window."""
    return {"startdate": str(startdate), "stopdate": str(stopdate)}


def mjd_tai_to_approx_utc_notes(mjd_value: Any) -> dict[str, Any]:
    """Return non-destructive notes about an MJD/TAI value.

    This intentionally does not rewrite or localize the source field. Raw MJD/TAI
    columns should remain preserved in the normalized tables.
    """
    return {
        "input_mjd_tai": mjd_value,
        "note": (
            "MJD/TAI is preserved as supplied by Fink/Rubin. Any UTC conversion is approximate "
            "and should be done only in analysis code that explicitly accounts for time scale."
        ),
    }


def validate_rows_within_window(
    df: pd.DataFrame,
    time_columns: Iterable[str],
    startdate: str,
    stopdate: str,
    convention: str,
) -> dict[str, Any]:
    """Validate available row time columns against a UTC window without local timezone assumptions."""
    start_mjd = _date_to_mjd(parse_utc_date(startdate))
    stop_mjd = _date_to_mjd(parse_utc_date(stopdate))
    checks = []
    for column in time_columns:
        if column not in df.columns:
            continue
        series = pd.to_numeric(df[column], errors="coerce").dropna()
        if series.empty:
            checks.append({"column": column, "available": True, "checked": False, "reason": "no numeric values"})
            continue
        in_window = bool(series.between(start_mjd, stop_mjd, inclusive="left").all())
        checks.append(
            {
                "column": column,
                "available": True,
                "checked": True,
                "within_window": in_window,
                "min": float(series.min()),
                "max": float(series.max()),
                "start_mjd_utc_midnight": start_mjd,
                "stop_mjd_utc_midnight": stop_mjd,
                "convention": convention,
                "note": "Raw time column preserved; MJD/TAI compared approximately to UTC calendar bounds.",
            }
        )
    return {"checked_columns": checks, "any_checked": any(item.get("checked") for item in checks)}


def _format_payload_datetime(value: datetime) -> str:
    if value.hour == 0 and value.minute == 0 and value.second == 0 and value.microsecond == 0:
        return value.date().isoformat()
    return value.isoformat().replace("+00:00", "Z")


def _date_to_mjd(value: date) -> float:
    epoch = datetime(1858, 11, 17, tzinfo=timezone.utc)
    current = datetime.combine(value, time.min, tzinfo=timezone.utc)
    return (current - epoch).total_seconds() / 86400.0

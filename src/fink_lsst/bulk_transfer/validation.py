"""Validation for delivered Fink Data Transfer products."""

from __future__ import annotations

from typing import Any

import pandas as pd

from fink_lsst.time_windows import validate_alert_start_date
from fink_lsst.validation import result, validate_normalized_table


def validate_delivery_tables(
    tables: dict[str, pd.DataFrame],
    config: dict[str, Any],
    denominator: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Validate delivered bulk tables and block false completeness claims."""
    checks = []
    has_delivery = bool(tables) and any(not frame.empty for frame in tables.values())
    checks.append(result("delivery_present", has_delivery, "Delivered data is present" if has_delivery else "No delivered data to validate", severity="warning"))
    try:
        validate_alert_start_date(config["target_startdate"], config["min_lsst_alert_date_utc"])
        checks.append(result("target_date_not_pre_alert", True, "Target date is not pre-alert"))
    except Exception as exc:  # noqa: BLE001
        checks.append(result("target_date_not_pre_alert", False, str(exc)))
    checks.append(result("timezone_utc", config.get("timezone") == "UTC", "Timezone is UTC" if config.get("timezone") == "UTC" else "Timezone is not UTC"))
    alerts = tables.get("alerts", pd.DataFrame())
    if not alerts.empty:
        for check in validate_normalized_table(alerts, "sources"):
            checks.append({"table": "alerts", **check})
        if "internal_source_id" in alerts.columns:
            duplicate_count = int(alerts.duplicated(subset=["internal_source_id"]).sum())
            checks.append(result("duplicate_diaSourceId", duplicate_count == 0, "No duplicate source IDs" if duplicate_count == 0 else "Duplicate source IDs found", duplicate_count=duplicate_count))
    required = {"internal_object_id", "internal_source_id", "ra", "dec", "time_mjd"}
    missing = sorted(column for column in required if column not in alerts.columns)
    checks.append(result("required_bulk_fields", not missing if has_delivery else False, "Required bulk fields present" if not missing else "Required bulk fields missing", missing=missing))
    denominator = denominator or {}
    metadata = metadata or {}
    denominator_ok = bool(denominator.get("directly_comparable")) or bool(metadata.get("complete_count"))
    all_alert_scope = bool(config.get("request_scope", {}).get("all_alerts"))
    completeness_allowed = has_delivery and not missing and all_alert_scope and denominator_ok
    checks.append(
        result(
            "completeness_claim_allowed",
            completeness_allowed,
            "Complete-night claim allowed" if completeness_allowed else "Complete-night claim is blocked",
            severity="error",
            has_delivery=has_delivery,
            all_alert_scope=all_alert_scope,
            denominator_or_metadata_complete=denominator_ok,
        )
    )
    return checks

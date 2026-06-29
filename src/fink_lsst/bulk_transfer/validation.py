"""Validation for delivered Fink Data Transfer products."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from fink_lsst.time_windows import validate_alert_start_date, validate_rows_within_window
from fink_lsst.validation import result, validate_normalized_table

from .ingest import COMPLETENESS_SCOPE
from .run_manifest import RunManifest


OBJECT_ID_COLUMNS = ("internal_object_id", "diaObjectId", "r:diaObjectId")
SOURCE_ID_COLUMNS = ("internal_source_id", "diaSourceId", "r:diaSourceId")
RA_COLUMNS = ("ra", "r:ra")
DEC_COLUMNS = ("dec", "r:dec")
TIME_COLUMNS = ("time_mjd", "midpointMjdTai", "r:midpointMjdTai", "firstDiaSourceMjdTai")
BAND_COLUMNS = ("band", "r:band", "filter")


def validate_delivery_tables(
    tables: dict[str, pd.DataFrame],
    config: dict[str, Any],
    denominator: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Validate delivered bulk tables and block false completeness claims."""
    checks: list[dict[str, Any]] = []
    metadata = metadata or {}
    raw = metadata.get("raw_delivery") or metadata.get("inspection") or {}
    nested_report = metadata.get("nested_report") or metadata.get("nested_conversion_report")
    completeness_scope = metadata.get("completeness_scope") or COMPLETENESS_SCOPE
    has_processed = bool(tables) and any(not frame.empty for frame in tables.values())
    raw_present = bool(raw and not raw.get("empty", True)) or bool(metadata.get("raw_delivery_present"))
    raw_file_count = int(raw.get("file_count", metadata.get("raw_file_count", 0)) or 0)
    raw_row_count = int(raw.get("total_rows", metadata.get("raw_row_count", 0)) or 0)

    checks.append(
        result(
            "raw_delivery_present",
            raw_present,
            "Raw delivery files are present" if raw_present else "No raw delivery files found",
            severity="info" if raw_present else "warning",
            raw_file_count=raw_file_count,
            raw_row_count=raw_row_count,
        )
    )
    checks.append(
        result(
            "raw_file_count_positive",
            raw_file_count > 0,
            "Raw file count is positive" if raw_file_count > 0 else "Raw file count is zero",
            severity="info" if raw_file_count > 0 else "warning",
            raw_file_count=raw_file_count,
        )
    )
    checks.append(
        result(
            "raw_row_count_positive",
            raw_row_count > 0,
            "Raw row count is positive" if raw_row_count > 0 else "Raw row count is zero or unknown",
            severity="info" if raw_row_count > 0 else "warning",
            raw_row_count=raw_row_count,
        )
    )
    checks.append(
        result(
            "delivery_present",
            raw_present or has_processed,
            "Delivered data is present"
            if raw_present or has_processed
            else "No delivered data to validate",
            severity="info" if raw_present or has_processed else "warning",
        )
    )

    try:
        validate_alert_start_date(config["target_startdate"], config["min_lsst_alert_date_utc"])
        checks.append(result("target_date_not_pre_alert", True, "Target date is not pre-alert", severity="info"))
    except Exception as exc:  # noqa: BLE001
        checks.append(result("target_date_not_pre_alert", False, str(exc)))
    checks.append(
        result(
            "timezone_utc",
            config.get("timezone") == "UTC",
            "Timezone is UTC" if config.get("timezone") == "UTC" else "Timezone is not UTC",
            severity="info" if config.get("timezone") == "UTC" else "error",
        )
    )

    alerts = tables.get("alerts", pd.DataFrame())
    window_check: dict[str, Any] | None = None
    checks.append(
        result(
            "processed_alerts_present",
            not alerts.empty,
            "Processed alerts table is present"
            if not alerts.empty
            else (
                "Raw delivery exists but processed alerts are missing; ingestion likely failed"
                if raw_present
                else "Processed alerts table is missing"
            ),
            severity="info" if not alerts.empty else "error",
        )
    )
    if not alerts.empty:
        for check in validate_normalized_table(alerts, "sources"):
            checks.append({"table": "alerts", **check})
        checks.append(_check_any_identifier(alerts, "object_identifier_present", OBJECT_ID_COLUMNS))
        checks.append(_check_any_identifier(alerts, "source_identifier_present", SOURCE_ID_COLUMNS))
        checks.append(_check_optional_field_group(alerts, "coordinate_fields_available", RA_COLUMNS + DEC_COLUMNS))
        checks.append(_check_optional_field_group(alerts, "time_fields_available", TIME_COLUMNS))
        checks.append(_check_optional_field_group(alerts, "band_fields_available", BAND_COLUMNS))
        checks.append(_check_duplicate_source_rate(alerts))
        checks.append(_check_object_grouping(alerts))
        window_check = _check_window_consistency(alerts, config)
        checks.append(window_check)
    else:
        checks.append(result("object_identifier_present", False, "No processed alerts available for object ID validation"))
        checks.append(result("source_identifier_present", False, "No processed alerts available for source ID validation"))

    raw_nested_columns = raw.get("nested_columns", []) if raw else []
    nested_report_exists = bool(nested_report)
    checks.append(
        result(
            "nested_sanitization_report_present",
            nested_report_exists or not raw_nested_columns,
            "Nested conversion report is present"
            if nested_report_exists
            else "No nested columns required conversion"
            if not raw_nested_columns
            else "Nested columns were detected but no conversion report was supplied",
            severity="info" if nested_report_exists or not raw_nested_columns else "error",
            raw_nested_columns=raw_nested_columns,
            report_present=nested_report_exists,
        )
    )

    denominator = denominator or {}
    all_alert_scope = (
        bool(config.get("request_scope", {}).get("all_alerts"))
        and bool(metadata.get("all_alerts", completeness_scope.get("scope") in {"full_night", "full_night_all_alerts"}))
        and not metadata.get("filter")
        and completeness_scope.get("scope") in {"full_night", "full_night_all_alerts"}
    )
    kafka_lag_zero = metadata.get("kafka_lag_zero")
    kafka_ok = kafka_lag_zero is not False
    no_truncation_warning = not bool(metadata.get("truncation_warning", False))
    date_window_ok = bool((window_check or {}).get("passed")) or bool(metadata.get("date_window_explained", False))
    hard_integrity_ok = not _has_hard_failures(checks)
    denominator_ok = bool(denominator.get("directly_comparable")) or bool(metadata.get("complete_count"))
    full_night_scope = completeness_scope.get("scope") in {"full_night", "full_night_all_alerts"}
    completeness_allowed = bool(
        has_processed
        and raw_row_count > 0
        and all_alert_scope
        and kafka_ok
        and no_truncation_warning
        and date_window_ok
        and hard_integrity_ok
        and completeness_scope.get("full_night_complete")
    )
    checks.append(
        result(
            "full_night_completeness_evidence",
            completeness_allowed or not full_night_scope,
            _full_night_evidence_message(full_night_scope, completeness_allowed),
            severity="info" if completeness_allowed or not full_night_scope else "warning",
            has_processed=has_processed,
            raw_row_count=raw_row_count,
            all_alert_scope=all_alert_scope,
            kafka_lag_zero=kafka_lag_zero,
            kafka_ok=kafka_ok,
            no_truncation_warning=no_truncation_warning,
            date_window_ok=date_window_ok,
            hard_integrity_ok=hard_integrity_ok,
            denominator_or_metadata_complete=denominator_ok,
            denominator_required=False,
        )
    )
    checks.append(
        result(
            "completeness_claim_allowed",
            completeness_allowed,
            "Complete-night claim allowed"
            if completeness_allowed
            else "Complete-night claim is blocked for this delivery",
            severity="info" if completeness_allowed else "warning",
            has_processed=has_processed,
            all_alert_scope=all_alert_scope,
            denominator_or_metadata_complete=denominator_ok,
            completeness_scope=completeness_scope,
        )
    )
    smoke_scope = completeness_scope.get("scope") == "tag_filtered_smoke_delivery"
    checks.append(
        result(
            "smoke_completeness_blocked",
            (smoke_scope and completeness_scope.get("full_night_complete") is False) or not smoke_scope,
            "Smoke delivery is explicitly marked non-complete"
            if smoke_scope
            else "Not a smoke delivery; smoke completeness block is not applicable",
            severity="info",
            completeness_scope=completeness_scope,
        )
    )
    return checks


def summarize_validation_status(checks: list[dict[str, Any]]) -> str:
    """Return a compact validation status that treats blocked completeness as expected."""
    hard_failures = [
        check
        for check in checks
        if not check.get("passed")
        and check.get("severity") == "error"
        and check.get("check") != "completeness_claim_allowed"
    ]
    if hard_failures:
        return "failed"
    warning_failures = [
        check
        for check in checks
        if not check.get("passed") and check.get("check") != "completeness_claim_allowed"
    ]
    return "passed_with_warnings" if warning_failures else "passed"


def validate_run_outputs(
    manifest: RunManifest,
    processed_paths: dict[str, str],
    raw_audit: dict[str, Any],
    execution_manifest: dict[str, Any],
) -> dict[str, Any]:
    """Validate manifest-driven run outputs and write claim-safe status details."""
    tables = _read_tables(processed_paths)
    config = {
        "target_startdate": manifest.startdate,
        "target_stopdate": manifest.stopdate,
        "min_lsst_alert_date_utc": "2026-02-25",
        "timezone": "UTC",
        "request_scope": {"all_alerts": manifest.is_all_alert},
    }
    metadata = {
        "raw_delivery": {
            "empty": not bool(raw_audit.get("file_count")),
            "file_count": raw_audit.get("file_count", 0),
            "total_rows": raw_audit.get("parquet", {}).get("total_readable_rows", 0),
            "nested_columns": raw_audit.get("schemas", {}).get("nested_columns", []),
        },
        "all_alerts": manifest.is_all_alert,
        "filter": manifest.filters[0] if len(manifest.filters) == 1 else None,
        "kafka_lag_zero": manifest.download_evidence.kafka_lag_zero,
        "completeness_scope": _manifest_completeness_scope(manifest),
    }
    checks = validate_delivery_tables(tables, config, metadata=metadata)
    nightly = validate_nightly_outputs(manifest, _nightly_paths_from_processed(processed_paths))
    checks.extend(nightly["checks"])
    status = summarize_validation_status(checks)
    claim_updates = derive_claim_updates(manifest, {"validation_status": status, "checks": checks, "execution_manifest": execution_manifest})
    return {
        "run_name": manifest.run_name,
        "validation_status": status,
        "checks": checks,
        "nightly_validation": nightly,
        "claim_updates": claim_updates,
    }


def validate_nightly_outputs(manifest: RunManifest, nightly_outputs: dict[str, dict[str, str]]) -> dict[str, Any]:
    """Validate night coverage without making completeness claims."""
    checks = []
    present_nights = sorted(night for night in nightly_outputs if night not in {"out_of_window", "unsplit", "unknown"})
    expected = list(manifest.expected_nights)
    missing = [night for night in expected if night not in present_nights]
    checks.append(
        result(
            "nightly_outputs_present",
            bool(nightly_outputs),
            "Nightly outputs are present" if nightly_outputs else "No nightly outputs found",
            severity="info" if nightly_outputs else "warning",
        )
    )
    checks.append(
        result(
            "expected_night_coverage",
            not missing,
            "All expected nights have output" if not missing else "Some expected nights have no output",
            severity="info" if not missing else "warning",
            expected_nights=expected,
            present_nights=present_nights,
            missing_nights=missing,
            out_of_window_present="out_of_window" in nightly_outputs,
        )
    )
    return {"present_nights": present_nights, "missing_nights": missing, "checks": checks}


def derive_claim_updates(manifest: RunManifest, validation_report: dict[str, Any]) -> dict[str, str]:
    """Derive conservative claim states from manifest and validation evidence."""
    partial = _execution_is_partial(validation_report.get("execution_manifest", {}))
    if partial or manifest.filters or not manifest.is_all_alert:
        return {
            "all_alert_completeness": "blocked",
            "night_completeness": "blocked",
            "week_completeness": "blocked",
            "reason": "partial/debug or filtered run cannot support completeness claims",
        }
    if manifest.packet_type == "unknown":
        return {
            "all_alert_completeness": "unresolved",
            "night_completeness": "unresolved",
            "week_completeness": "unresolved",
            "reason": "packet type is unknown",
        }
    strict_ok = (
        validation_report.get("validation_status") == "passed"
        and manifest.download_evidence.kafka_lag_zero is True
        and bool(manifest.download_evidence.expected_total_messages)
        and manifest.download_evidence.local_readable_rows == manifest.download_evidence.expected_total_messages
    )
    if strict_ok and len(manifest.expected_nights) == 1:
        return {
            "all_alert_completeness": "allowed",
            "night_completeness": "allowed",
            "week_completeness": "blocked",
            "reason": "strict single-night evidence is present",
        }
    return {
        "all_alert_completeness": "unresolved",
        "night_completeness": "unresolved",
        "week_completeness": "unresolved",
        "reason": "strict completeness evidence is not present",
    }


def write_run_validation_report(report: dict[str, Any], output_dir: str | Path) -> None:
    """Write manifest-driven validation report artifacts."""
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    (output / "validation_report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output / "VALIDATION_SUMMARY.md").write_text(_render_run_validation(report), encoding="utf-8")


def _check_any_identifier(df: pd.DataFrame, check_name: str, candidates: tuple[str, ...]) -> dict[str, Any]:
    present = [column for column in candidates if column in df.columns]
    non_null = {column: int(df[column].notna().sum()) for column in present}
    passed = any(count > 0 for count in non_null.values())
    return result(
        check_name,
        passed,
        "Identifier field is present" if passed else "No usable identifier field found",
        severity="info" if passed else "error",
        candidates=list(candidates),
        present=present,
        non_null=non_null,
    )


def _check_optional_field_group(df: pd.DataFrame, check_name: str, candidates: tuple[str, ...]) -> dict[str, Any]:
    present = [column for column in candidates if column in df.columns]
    non_null = {column: int(df[column].notna().sum()) for column in present}
    passed = any(count > 0 for count in non_null.values())
    return result(
        check_name,
        passed,
        "Field group is available" if passed else "Field group is unavailable in processed alerts",
        severity="info" if passed else "warning",
        candidates=list(candidates),
        present=present,
        non_null=non_null,
    )


def _check_duplicate_source_rate(df: pd.DataFrame) -> dict[str, Any]:
    source_column = next((column for column in SOURCE_ID_COLUMNS if column in df.columns), None)
    if not source_column:
        return result("duplicate_source_id_rate", False, "No source ID field available", severity="warning")
    valid = df[source_column].dropna()
    duplicate_count = int(valid.duplicated().sum())
    denominator = int(len(valid))
    rate = float(duplicate_count / denominator) if denominator else 0.0
    return result(
        "duplicate_source_id_rate",
        duplicate_count == 0,
        "No duplicate source IDs" if duplicate_count == 0 else "Duplicate source IDs found",
        severity="info" if duplicate_count == 0 else "warning",
        source_column=source_column,
        duplicate_count=duplicate_count,
        denominator=denominator,
        duplicate_rate=rate,
    )


def _check_object_grouping(df: pd.DataFrame) -> dict[str, Any]:
    object_column = next((column for column in OBJECT_ID_COLUMNS if column in df.columns), None)
    if not object_column:
        return result("object_grouping_count", False, "No object ID field available", severity="warning")
    unique_objects = int(df[object_column].dropna().nunique())
    return result(
        "object_grouping_count",
        unique_objects > 0,
        "Object grouping count is available" if unique_objects > 0 else "No object groups found",
        severity="info" if unique_objects > 0 else "warning",
        object_column=object_column,
        unique_objects=unique_objects,
    )


def _check_window_consistency(df: pd.DataFrame, config: dict[str, Any]) -> dict[str, Any]:
    available = [column for column in TIME_COLUMNS if column in df.columns]
    if not available:
        return result("date_window_consistency", True, "No time columns available for window comparison", severity="info")
    comparison = validate_rows_within_window(
        df,
        available,
        config["target_startdate"],
        config["target_stopdate"],
        convention="exclusive",
    )
    checked = [item for item in comparison["checked_columns"] if item.get("checked")]
    passed = bool(checked) and all(item.get("within_window") for item in checked)
    return result(
        "date_window_consistency",
        passed,
        "Available time columns are within the requested UTC window"
        if passed
        else "Available time columns are not fully within the requested UTC window or could not be checked",
        severity="info" if passed else "warning",
        comparison=comparison,
    )


def _has_hard_failures(checks: list[dict[str, Any]]) -> bool:
    return any(
        not check.get("passed")
        and check.get("severity") == "error"
        and check.get("check") != "completeness_claim_allowed"
        for check in checks
    )


def _full_night_evidence_message(full_night_scope: bool, completeness_allowed: bool) -> str:
    if completeness_allowed:
        return "Full-night completeness evidence is sufficient"
    if full_night_scope:
        return "Full-night scope is recorded, but completeness evidence is insufficient"
    return "Not a full-night scope; full-night completeness evidence is not applicable"


def _read_tables(processed_paths: dict[str, str]) -> dict[str, pd.DataFrame]:
    tables = {}
    for name, path in processed_paths.items():
        candidate = Path(path)
        if candidate.suffix == ".parquet" and candidate.exists():
            tables[name] = pd.read_parquet(candidate)
    return tables


def _nightly_paths_from_processed(processed_paths: dict[str, str]) -> dict[str, dict[str, str]]:
    nightly: dict[str, dict[str, str]] = {}
    for table_path in processed_paths.values():
        path = Path(table_path)
        try:
            all_dir = path.parents[0]
            nights_dir = all_dir.parent / "nights"
        except IndexError:
            continue
        if not nights_dir.exists():
            continue
        for parquet in nights_dir.glob("*/*.parquet"):
            nightly.setdefault(parquet.parent.name, {})[parquet.stem] = str(parquet)
    return nightly


def _manifest_completeness_scope(manifest: RunManifest) -> dict[str, Any]:
    if manifest.filters:
        return {
            "scope": "tag_filtered_smoke_delivery",
            "full_night_complete": False,
            "reason": "filtered manifest; completeness claims blocked",
        }
    if manifest.is_all_alert and len(manifest.expected_nights) == 1:
        return {
            "scope": "full_night_all_alerts",
            "full_night_complete": True,
            "reason": "all-alert single-night manifest; validation still controls claims",
        }
    return {
        "scope": manifest.scope,
        "full_night_complete": False,
        "week_complete": False,
        "reason": "multi-night completeness remains unresolved without strict evidence",
    }


def _execution_is_partial(execution_manifest: dict[str, Any]) -> bool:
    context = execution_manifest.get("run_context", {}) if isinstance(execution_manifest, dict) else {}
    return "partial" in str(context.get("processed_dir", "")) or "partial" in str(context.get("output_dir", ""))


def _render_run_validation(report: dict[str, Any]) -> str:
    lines = [
        "# Manifest-Driven Validation Summary",
        "",
        f"- Run: `{report.get('run_name')}`",
        f"- Status: `{report.get('validation_status')}`",
        f"- Claim updates: `{report.get('claim_updates')}`",
        "",
        "## Checks",
        "",
    ]
    for check in report.get("checks", []):
        status = "passed" if check.get("passed") else "failed"
        lines.append(f"- `{check.get('check')}`: {status} - {check.get('message')}")
    return "\n".join(lines) + "\n"

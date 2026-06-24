"""Validation for full-night REST completeness feasibility runs."""

from __future__ import annotations

from typing import Any

import pandas as pd

from .time_windows import validate_alert_start_date
from .validation import result, validate_normalized_table


def validate_full_night_run(
    config: dict[str, Any],
    partition_plan: dict[str, Any],
    partition_results: list[dict[str, Any]],
    deduplicated_frame: pd.DataFrame,
    accounting: dict[str, Any],
    proposed_decision: str | None = None,
) -> list[dict[str, Any]]:
    """Run validation checks that prevent false full-night completeness claims."""
    checks: list[dict[str, Any]] = []
    try:
        validate_alert_start_date(config["target_startdate"], config["min_lsst_alert_date_utc"])
        checks.append(result("target_date_not_pre_alert", True, "Target start date is not before configured alert start", severity="error"))
    except Exception as exc:  # noqa: BLE001 - validation should report all issues
        checks.append(result("target_date_not_pre_alert", False, str(exc), severity="error"))

    checks.append(
        result(
            "timezone_utc",
            str(config.get("timezone", "")).upper() == "UTC",
            "Timezone is UTC" if str(config.get("timezone", "")).upper() == "UTC" else "Timezone is not UTC",
            severity="error",
            timezone=config.get("timezone"),
        )
    )
    checks.append(
        result(
            "window_convention_recorded",
            bool(config.get("api_date_window_convention")),
            "API date-window convention is recorded",
            severity="error",
            convention=config.get("api_date_window_convention"),
        )
    )

    statuses = [item.get("status") for item in partition_results]
    checks.append(
        result(
            "partition_status_recorded",
            len(statuses) == len(partition_results) and all(statuses),
            "Every partition has a recorded status",
            severity="error",
            statuses=statuses,
        )
    )
    unbounded = [
        item.get("partition_id")
        for item in partition_results
        if item.get("status") not in {"unsupported", "unsafe", "skipped"} and not item.get("query_payload", {}).get("n")
    ]
    checks.append(
        result(
            "no_unbounded_queries",
            not unbounded,
            "No attempted partition query lacks an explicit n cap" if not unbounded else "Some attempted queries were unbounded",
            severity="error",
            unbounded_partition_ids=unbounded,
        )
    )
    missing_raw = [
        item.get("partition_id")
        for item in partition_results
        if item.get("success") and not item.get("raw_response_saved")
    ]
    checks.append(
        result(
            "raw_response_saved",
            not missing_raw,
            "Raw response exists for every successful attempted partition",
            severity="error",
            missing_raw_partition_ids=missing_raw,
        )
    )
    over_limit = [
        item.get("partition_id")
        for item in partition_results
        if int(item.get("row_count", 0)) > int(item.get("requested_n", 0) or 0) > 0
    ]
    checks.append(
        result(
            "row_count_within_requested_limit",
            not over_limit,
            "No partition row count exceeds requested limit",
            severity="error",
            over_limit_partition_ids=over_limit,
        )
    )
    misclassified_caps = [
        item.get("partition_id")
        for item in partition_results
        if int(item.get("row_count", 0)) == int(item.get("requested_n", 0) or -1) and item.get("status") != "possibly_truncated"
    ]
    checks.append(
        result(
            "cap_hits_marked_truncated",
            not misclassified_caps,
            "Every row-count cap hit is marked possibly_truncated",
            severity="error",
            misclassified_partition_ids=misclassified_caps,
        )
    )

    if not deduplicated_frame.empty:
        for check in validate_normalized_table(deduplicated_frame, "sources"):
            checks.append({"table": "deduplicated_rows", **check})

    denominator_direct = bool(accounting.get("directly_comparable"))
    checks.append(
        result(
            "denominator_comparison_validity",
            denominator_direct or accounting.get("completeness_fraction") is None,
            "No completeness fraction was computed without a directly comparable denominator",
            severity="error",
            directly_comparable=denominator_direct,
            completeness_fraction=accounting.get("completeness_fraction"),
        )
    )

    blocking_statuses = {"failed", "skipped", "possibly_truncated", "unsupported", "unsafe"}
    blockers = [item.get("partition_id") for item in partition_results if item.get("status") in blocking_statuses]
    scope = partition_plan.get("completeness_scope")
    can_claim_complete = (
        not blockers
        and scope == "all_alert"
        and denominator_direct
        and str(config.get("timezone", "")).upper() == "UTC"
    )
    checks.append(
        result(
            "completeness_claim_validity",
            proposed_decision != "yes" or can_claim_complete,
            "Completeness claim is valid" if can_claim_complete else "Full-night completeness cannot be claimed from this run",
            severity="error",
            blocking_partition_ids=blockers,
            completeness_scope=scope,
            denominator_directly_comparable=denominator_direct,
            proposed_decision=proposed_decision,
        )
    )
    return checks


def render_full_night_validation_summary(checks: list[dict[str, Any]]) -> str:
    """Render validation checks as Markdown."""
    lines = ["# Full-Night Feasibility Validation Summary", ""]
    for check in checks:
        table = f" `{check['table']}`" if "table" in check else ""
        lines.append(
            f"- `{check['severity']}`{table} `{check['check']}`: "
            f"{'passed' if check['passed'] else 'failed'} - {check['message']}"
        )
    return "\n".join(lines) + "\n"

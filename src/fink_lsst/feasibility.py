"""Feasibility conclusions for Fink LSST public REST extraction."""

from __future__ import annotations

from typing import Any


def generate_feasibility_report(
    extraction_summary: dict[str, Any],
    validation_checks: list[dict[str, Any]],
    capabilities: list[dict[str, Any]],
) -> dict[str, Any]:
    """Generate structured feasibility conclusions from bounded extraction evidence."""
    tag_rows = int(extraction_summary.get("tag_rows", 0))
    detail = extraction_summary.get("detail_attempts", {})
    any_details = any(values.get("succeeded", 0) > 0 for values in detail.values() if isinstance(values, dict))
    hit_limit = any(
        check.get("check") == "truncation_pagination" and not check.get("passed")
        for check in validation_checks
    )
    supports_bounded = tag_rows > 0 and any_details
    full_night = "unresolved"
    blockers = [
        "No all-pages pagination/continuation strategy has been proven.",
        "Bounded tag/window rows are not equivalent to complete nightly all-alert extraction.",
    ]
    if hit_limit:
        blockers.append("The bounded tag response hit the configured row limit, so additional matching rows may exist.")
    return {
        "public_rest_minimal_lookup": "yes" if any_details else "partial",
        "public_rest_bounded_tag_window_extraction": "yes" if supports_bounded else "partial",
        "public_rest_complete_full_night_all_alert_extraction": full_night,
        "evidence": {
            "tag_rows": tag_rows,
            "unique_object_ids": extraction_summary.get("unique_object_ids", 0),
            "detail_attempts": detail,
            "capability_count": len(capabilities),
        },
        "blockers": blockers,
        "required_next_tests": [
            "Test explicit pagination/limit behavior if exposed by future contracts.",
            "Run multiple small date/tag windows and compare against matching statistics if available.",
            "Determine whether public REST can enumerate all alerts for one night without Data Transfer.",
        ],
        "data_transfer_consider_later": True,
        "antares_comparison_note": (
            "ANTARES work required managing search limits and sky tiling around locus access. "
            "Fink public REST now supports minimal lookup and bounded samples, but full-night completeness remains unresolved."
        ),
    }


def render_feasibility_markdown(report: dict[str, Any]) -> str:
    """Render feasibility conclusions as Markdown."""
    lines = [
        "# Bounded Public REST Feasibility Report",
        "",
        f"- Public REST minimal lookup: `{report['public_rest_minimal_lookup']}`",
        f"- Public REST bounded tag/window extraction: `{report['public_rest_bounded_tag_window_extraction']}`",
        f"- Public REST complete full-night all-alert extraction: `{report['public_rest_complete_full_night_all_alert_extraction']}`",
        "",
        "## Evidence",
        "",
    ]
    for key, value in report.get("evidence", {}).items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Blockers", ""])
    for blocker in report.get("blockers", []):
        lines.append(f"- {blocker}")
    lines.extend(["", "## Required Next Tests", ""])
    for test in report.get("required_next_tests", []):
        lines.append(f"- {test}")
    lines.extend(["", "## ANTARES Comparison", "", report.get("antares_comparison_note", ""), ""])
    return "\n".join(lines)


def decide_full_night_feasibility(
    capabilities: dict[str, Any],
    partition_results: list[dict[str, Any]],
    validation: list[dict[str, Any]],
    denominator: dict[str, Any],
) -> dict[str, Any]:
    """Decide whether public REST proved full-night all-alert completeness."""
    statuses = [result.get("status") for result in partition_results]
    scope = next((result.get("intended_completeness") for result in partition_results if result.get("intended_completeness")), None)
    all_alert_supported = bool(capabilities.get("all_alert_completeness", {}).get("public_rest_enumeration_supported"))
    tag_supported = bool(capabilities.get("tag_specific_completeness", {}).get("public_rest_enumeration_supported"))
    blocking_statuses = {"failed", "skipped", "possibly_truncated", "unsupported", "unsafe"}
    blockers = []
    if not all_alert_supported:
        blockers.append("No public REST all-alert enumeration path was proven.")
    if scope != "all_alert":
        blockers.append("The executed extraction scope was not all-alert.")
    if any(status in blocking_statuses for status in statuses):
        blockers.append("At least one partition was failed, skipped, unsupported, unsafe, or possibly truncated.")
    if not denominator.get("directly_comparable"):
        blockers.append("No directly comparable denominator is available.")
    validation_failures = [check for check in validation if check.get("severity") == "error" and not check.get("passed")]
    if validation_failures:
        blockers.append("Validation found errors that prevent a completeness claim.")

    if any(status == "unsafe" for status in statuses) or (not all_alert_supported and not tag_supported):
        decision = "unsafe_to_attempt" if any(status == "unsafe" for status in statuses) else "no"
    elif not blockers and all(status in {"exhausted", "empty"} for status in statuses):
        decision = "yes"
    elif any(status == "possibly_truncated" for status in statuses) or tag_supported:
        decision = "unresolved"
    else:
        decision = "no"

    return {
        "rest_full_night_complete": decision,
        "evidence": {
            "all_alert_enumeration_supported": all_alert_supported,
            "tag_specific_enumeration_supported": tag_supported,
            "pagination_exposed": capabilities.get("pagination_exposed"),
            "row_caps_exposed": capabilities.get("row_caps_exposed"),
            "partition_statuses": statuses,
            "partition_count": len(partition_results),
            "total_raw_rows": denominator.get("extracted_count"),
            "denominator_directly_comparable": denominator.get("directly_comparable"),
            "extraction_scope": scope,
        },
        "blockers": blockers,
        "next_required_action": _next_action(decision, blockers),
        "data_transfer_should_be_considered": decision in {"no", "unresolved", "unsafe_to_attempt"},
        "rest_still_useful_for": [
            "candidate-level lookup",
            "bounded tag/date-window samples",
            "known-ID object/source/forced-photometry enrichment",
        ],
    }


def render_full_night_feasibility_markdown(decision: dict[str, Any]) -> str:
    """Render the full-night decision as a blunt Markdown report."""
    lines = [
        "# REST Full-Night Completeness Feasibility Report",
        "",
        f"- `rest_full_night_complete`: `{decision['rest_full_night_complete']}`",
        f"- Data Transfer should be considered: `{decision['data_transfer_should_be_considered']}`",
        "",
        "## Evidence",
        "",
    ]
    for key, value in decision.get("evidence", {}).items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Blockers", ""])
    for blocker in decision.get("blockers", []):
        lines.append(f"- {blocker}")
    lines.extend(["", "## Next Required Action", "", decision.get("next_required_action", ""), "", "## REST Remains Useful For", ""])
    for item in decision.get("rest_still_useful_for", []):
        lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)


def _next_action(decision: str, blockers: list[str]) -> str:
    if decision == "yes":
        return "Proceed to science-grade validation and larger controlled runs."
    if decision == "unsafe_to_attempt":
        return "Do not escalate public REST calls; use an access mode designed for bulk data."
    if decision == "no":
        return "Plan Data Transfer or another bulk-supported route for full-night extraction."
    if any("possibly truncated" in blocker.lower() for blocker in blockers):
        return "Find documented pagination/continuation or a stronger partition dimension before claiming completeness."
    return "Resolve the listed blockers; public REST remains bounded/candidate-useful but not full-night proven."

"""Next-action decision logic for Data Transfer readiness."""

from __future__ import annotations

from typing import Any


def decide_data_transfer_readiness(
    setup: dict[str, Any] | None,
    client_probe: dict[str, Any] | None,
    request: dict[str, Any] | None,
    delivery_inspection: dict[str, Any] | None,
    validation: dict[str, Any] | None = None,
    processed_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Decide the next safe Data Transfer action."""
    setup = setup or {}
    client_probe = client_probe or {}
    delivery_inspection = delivery_inspection or {}
    request = request or {}
    validation = validation or {}
    processed_manifest = processed_manifest or {}
    client_available = bool(client_probe.get("package_installed") or client_probe.get("cli_available"))
    delivery_present = bool(delivery_inspection and not delivery_inspection.get("empty", True))
    processed_present = bool(processed_manifest.get("processed_table_paths") or processed_manifest.get("artifacts"))
    validation_status = validation.get("validation_status")
    validation_hard_failed = _validation_hard_failed(validation)
    blockers = []
    evidence = {
        "setup_status": setup.get("status"),
        "fink_client_installed": client_probe.get("package_installed"),
        "cli_available": client_probe.get("cli_available"),
        "client_available": client_available,
        "request_prepared": bool(request),
        "request_profile": request.get("profile"),
        "dry_run": request.get("confirmation", {}).get("dry_run"),
        "allow_submit": request.get("confirmation", {}).get("allow_submit"),
        "delivery_present": delivery_present,
        "raw_file_count": delivery_inspection.get("file_count"),
        "raw_row_count": delivery_inspection.get("total_rows"),
        "processed_delivery_present": processed_present,
        "processed_validation_status": processed_manifest.get("validation_status"),
        "validation_available": bool(validation),
        "validation_status": validation_status,
    }
    if not setup:
        blockers.append("Setup status has not been generated.")
        value = "not_ready"
    elif not request:
        blockers.append("No Data Transfer request draft exists.")
        value = "ready_for_registration"
    elif not client_available:
        blockers.append("No fink-client import or known Data Transfer executable was detected; manual portal workflow may still be possible.")
        value = "ready_for_registration"
    elif delivery_present and processed_present and validation and not validation_hard_failed:
        value = "ready_for_first_full_night_request"
    elif delivery_present and processed_present and not validation:
        value = "ready_for_smoke_validation"
    elif delivery_present and validation_hard_failed:
        value = "smoke_ingestion_failed_needs_fix"
    elif delivery_present:
        value = "ready_for_smoke_ingestion"
    elif request.get("profile") == "smoke_delivery":
        value = "ready_for_manual_smoke_request"
    else:
        value = "ready_for_manual_smoke_request"

    if validation:
        complete_allowed = any(
            check.get("check") == "completeness_claim_allowed" and check.get("passed")
            for check in validation.get("checks", [])
        )
        evidence["complete_night_claim_allowed"] = bool(complete_allowed)
    return {
        "decision": value,
        "blockers": blockers,
        "evidence": evidence,
        "next_action": _next_action(value, blockers),
        "user_confirmation_required": value in {"ready_for_manual_smoke_request", "ready_for_first_full_night_request"},
        "full_night_request_allowed": value == "ready_for_first_full_night_request",
    }


def render_next_action(decision: dict[str, Any]) -> str:
    """Render next-action decision Markdown."""
    lines = [
        "# Data Transfer Next Action",
        "",
        f"- Decision: `{decision['decision']}`",
        f"- User confirmation required: `{decision['user_confirmation_required']}`",
        f"- Full-night request allowed: `{decision['full_night_request_allowed']}`",
        "",
        "## Evidence",
        "",
    ]
    for key, value in decision.get("evidence", {}).items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Blockers", ""])
    if decision.get("blockers"):
        for blocker in decision["blockers"]:
            lines.append(f"- {blocker}")
    else:
        lines.append("- None for the current next action.")
    lines.extend(["", "## Next Action", "", decision.get("next_action", ""), ""])
    return "\n".join(lines)


def _next_action(value: str, blockers: list[str]) -> str:
    if value == "not_ready":
        return "Run setup, client probe, and request-draft scripts."
    if value == "ready_for_registration":
        return "Install/probe fink-client if desired and complete registration/authentication manually. The portal smoke workflow can still be prepared."
    if value == "ready_for_manual_smoke_request":
        return "Review the smoke request and submit only a tiny manual portal request if the portal supports it."
    if value == "ready_for_smoke_ingestion":
        return "Run `python scripts/run_data_transfer_smoke_ingestion.py`."
    if value == "smoke_ingestion_failed_needs_fix":
        return "Review the latest smoke ingestion and validation reports, fix ingestion/validation errors, then rerun the smoke pipeline."
    if value == "ready_for_smoke_validation":
        return "Run `python scripts/validate_data_transfer_delivery.py`."
    if value == "smoke_delivery_validated":
        return "Smoke delivery validation is complete; review reports before deciding on a full-night request."
    if value == "ready_for_first_full_night_request":
        return "Review validation and request explicit user confirmation before preparing the first full-night request."
    if value == "ready_for_full_night_validation":
        return "Validate delivered full-night products before any science claim."
    return "Review blockers."


def _validation_hard_failed(validation: dict[str, Any]) -> bool:
    if not validation:
        return False
    if validation.get("validation_status") == "failed":
        return True
    return any(
        not check.get("passed")
        and check.get("severity") == "error"
        and check.get("check") != "completeness_claim_allowed"
        for check in validation.get("checks", [])
    )

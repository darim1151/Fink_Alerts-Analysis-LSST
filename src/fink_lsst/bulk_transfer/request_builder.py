"""Dry-run Fink Data Transfer request builder."""

from __future__ import annotations

import json
from typing import Any


def build_data_transfer_request(config: dict[str, Any], contracts: Any | None = None) -> dict[str, Any]:
    """Build a dry-run Data Transfer request object without submitting it."""
    return build_profiled_data_transfer_request(config, profile="full_night_candidate", contracts=contracts)


def build_profiled_data_transfer_request(
    config: dict[str, Any],
    profile: str = "full_night_candidate",
    contracts: Any | None = None,
) -> dict[str, Any]:
    """Build a dry-run request for a named Data Transfer profile."""
    scope = config.get("request_scope", {}) or {}
    safety = config.get("safety", {}) or {}
    local_paths = config.get("local_paths", {}) or {}
    selected_fields = _fields_for_profile(config, profile)
    all_alerts = bool(scope.get("all_alerts", True)) and profile != "smoke_delivery"
    selected_tags = list(scope.get("selected_tags", []))
    selected_blocks = list(scope.get("selected_blocks", []))
    request = {
        "profile": profile,
        "survey": config.get("survey", "LSST"),
        "target_startdate": config.get("target_startdate"),
        "target_stopdate": config.get("target_stopdate"),
        "timezone": config.get("timezone", "UTC"),
        "output_format": _choose_output_format(config),
        "fallback_output_formats": config.get("fallback_output_formats", []),
        "scope": {
            "all_alerts": all_alerts,
            "selected_blocks": selected_blocks,
            "selected_tags": selected_tags,
            "selected_fields": selected_fields,
            "smallest_supported_request": profile == "smoke_delivery",
            "completeness_claim_allowed": False if profile == "smoke_delivery" else None,
        },
        "local_storage_plan": dict(local_paths),
        "confirmation": {
            "dry_run": bool(config.get("dry_run", True)),
            "user_confirmed_submission": False,
            "allow_submit": bool(safety.get("allow_submit", False)),
            "require_user_confirmation_to_submit": bool(safety.get("require_user_confirmation_to_submit", True)),
        },
        "safety_flags": {
            "never_store_credentials": bool(safety.get("never_store_credentials", True)),
            "max_local_file_size_gb_warning": safety.get("max_local_file_size_gb_warning"),
            "job_submission_attempted": False,
            "cutouts_requested": False,
            "fits_or_images_requested": False,
        },
        "contracts_available": contracts is not None,
    }
    request["risk"] = estimate_request_risk(request, config)
    return request


def validate_data_transfer_request(request: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate request safety and required fields."""
    checks = []
    checks.append(_check("dry_run", request["confirmation"]["dry_run"], "Request is dry-run only"))
    checks.append(_check("submission_disabled", not request["confirmation"]["allow_submit"], "Submission is disabled"))
    checks.append(_check("utc_timezone", request.get("timezone") == "UTC", "Request timezone is UTC"))
    checks.append(_check("date_window_present", bool(request.get("target_startdate") and request.get("target_stopdate")), "Date window is present"))
    checks.append(_check("no_binary_payloads", not request["safety_flags"]["cutouts_requested"] and not request["safety_flags"]["fits_or_images_requested"], "No cutouts/FITS/images requested"))
    checks.append(_check("fields_selected", bool(request["scope"].get("selected_fields")), "Selected fields are recorded"))
    return checks


def render_request_as_json(request: dict[str, Any]) -> str:
    """Render request as JSON."""
    return json.dumps(request, indent=2, sort_keys=True) + "\n"


def render_request_as_markdown(request: dict[str, Any]) -> str:
    """Render the dry-run request as Markdown."""
    lines = [
        "# Data Transfer Request Draft",
        "",
        f"- Survey: `{request['survey']}`",
        f"- Profile: `{request.get('profile', 'full_night_candidate')}`",
        f"- UTC window: `{request['target_startdate']}` to `{request['target_stopdate']}`",
        f"- Output format: `{request['output_format']}`",
        f"- All alerts requested: `{request['scope']['all_alerts']}`",
        f"- Selected tags: `{request['scope']['selected_tags']}`",
        f"- Selected blocks: `{request['scope']['selected_blocks']}`",
        f"- Dry run: `{request['confirmation']['dry_run']}`",
        f"- Submission allowed: `{request['confirmation']['allow_submit']}`",
        "",
        "## Requested Fields",
        "",
    ]
    for field in request["scope"]["selected_fields"]:
        lines.append(f"- `{field}`")
    lines.extend(["", "## Risk", "", f"- Level: `{request['risk']['level']}`", f"- Reason: {request['risk']['reason']}", ""])
    return "\n".join(lines)


def estimate_request_risk(request: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """Estimate request risk from scope and safety config."""
    if request["confirmation"]["allow_submit"]:
        return {"level": "high", "reason": "Submission is enabled; this is not allowed in readiness mode."}
    if request.get("profile") == "smoke_delivery":
        return {"level": "low", "reason": "Smoke request is dry-run, non-complete, and asks for the smallest portal-supported delivery."}
    if request["scope"]["all_alerts"]:
        return {"level": "medium", "reason": "All-alert full-night scope may produce large files once submitted manually."}
    return {"level": "low", "reason": "Dry-run request with restricted scope and submission disabled."}


def build_manual_portal_checklist(request: dict[str, Any]) -> str:
    """Build a manual checklist for portal-based request review."""
    lines = [
        "# Manual Fink Data Transfer Portal Checklist",
        "",
        "- Confirm registration/access is approved.",
        "- Confirm the selected survey is LSST/Rubin.",
        f"- Enter UTC start date: `{request['target_startdate']}`.",
        f"- Enter UTC stop date: `{request['target_stopdate']}`.",
        f"- Select output format: `{request['output_format']}` if available.",
        "- Request all alerts only if the portal documents this as a supported bulk mode.",
        "- For smoke delivery, choose the smallest available scope: one block, tag/filter-limited subset, or shortest portal-supported interval.",
        "- Do not request cutouts, FITS, or image payloads for this phase.",
        "- Save delivered files under `data/raw/data_transfer/`.",
        "- Do not paste or save credentials into this repository.",
        "- Run `python scripts/inspect_data_transfer_delivery.py` after files are placed locally.",
        "",
    ]
    return "\n".join(lines)


def build_smoke_manual_portal_checklist(request: dict[str, Any]) -> str:
    """Build a smoke-delivery-specific manual portal checklist."""
    lines = [
        "# Smoke Data Transfer Manual Portal Checklist",
        "",
        "- Use this only for a tiny non-complete smoke delivery.",
        "- Confirm registration/access is approved.",
        f"- Survey: `{request['survey']}`.",
        f"- UTC start date: `{request['target_startdate']}`.",
        f"- UTC stop date: `{request['target_stopdate']}`.",
        f"- Preferred output format: `{request['output_format']}`.",
        "- Select the smallest available scope in this order:",
        "  - one manually selected block, if supported",
        "  - a small field subset, if supported",
        "  - a tag/filter-limited request, if supported",
        "  - a single short interval, if supported",
        "- If the portal only supports whole-night all-alert delivery, stop and ask before submitting.",
        "- Do not request cutouts, FITS, or images.",
        "- Save delivered smoke files under `data/raw/data_transfer/smoke_delivery/`.",
        "- Do not save credentials, tokens, usernames, passwords, or auth outputs in this repo.",
        "",
        "## Minimal Fields",
        "",
    ]
    for field in request["scope"]["selected_fields"]:
        lines.append(f"- `{field}`")
    return "\n".join(lines) + "\n"


def _choose_output_format(config: dict[str, Any]) -> str:
    preferred = str(config.get("preferred_output_format", "parquet")).lower()
    fallbacks = [str(item).lower() for item in config.get("fallback_output_formats", [])]
    for candidate in [preferred, *fallbacks, "json"]:
        if candidate in {"parquet", "avro", "json"}:
            return candidate
    return "json"


def _fields_for_profile(config: dict[str, Any], profile: str) -> list[str]:
    fields = list((config.get("request_scope", {}) or {}).get("selected_fields", []))
    if profile != "smoke_delivery":
        return fields
    preferred = [
        "diaObjectId",
        "diaSourceId",
        "ra",
        "dec",
        "midpointMjdTai",
        "band",
        "psfFlux",
        "psfFluxErr",
        "fink_class",
        "fink_tags",
    ]
    return [field for field in preferred if not fields or field in fields]


def _check(name: str, passed: bool, message: str) -> dict[str, Any]:
    return {"check": name, "passed": bool(passed), "message": message}

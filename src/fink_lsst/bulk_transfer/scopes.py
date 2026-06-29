"""Central Data Transfer scope definitions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any


VALID_SCOPES = {"smoke_delivery", "full_night", "full_week", "full_week_full_packet"}
SCOPE_ALIASES = {
    "tag_filtered_smoke_delivery": "smoke_delivery",
    "full_night_all_alerts": "full_night",
}


@dataclass(frozen=True)
class ScopePolicy:
    scope: str
    path_component: str
    is_tag_filtered: bool
    is_all_alert: bool
    is_single_night: bool
    is_multi_night: bool
    packet_type: str
    content_type: str
    completeness_default: str
    allow_completeness_only_after_validation: bool = True


SCOPE_POLICIES = {
    "smoke_delivery": ScopePolicy(
        scope="smoke_delivery",
        path_component="smoke_delivery",
        is_tag_filtered=True,
        is_all_alert=False,
        is_single_night=True,
        is_multi_night=False,
        packet_type="light_static",
        content_type="Light static packet",
        completeness_default="blocked",
    ),
    "full_night": ScopePolicy(
        scope="full_night",
        path_component="full_night",
        is_tag_filtered=False,
        is_all_alert=True,
        is_single_night=True,
        is_multi_night=False,
        packet_type="light_static",
        content_type="Light static packet",
        completeness_default="unresolved",
    ),
    "full_week": ScopePolicy(
        scope="full_week",
        path_component="full_week",
        is_tag_filtered=False,
        is_all_alert=True,
        is_single_night=False,
        is_multi_night=True,
        packet_type="light_static",
        content_type="Light static packet",
        completeness_default="unresolved",
    ),
    "full_week_full_packet": ScopePolicy(
        scope="full_week_full_packet",
        path_component="full_week_full_packet",
        is_tag_filtered=False,
        is_all_alert=True,
        is_single_night=False,
        is_multi_night=True,
        packet_type="full",
        content_type="Full packet",
        completeness_default="unresolved",
    ),
}


def normalize_scope(scope: str) -> str:
    """Normalize legacy or external scope names to the canonical scope."""
    candidate = SCOPE_ALIASES.get(scope, scope)
    if candidate not in VALID_SCOPES:
        raise ValueError(f"Unsupported Data Transfer scope: {scope}")
    return candidate


def get_scope_policy(scope: str) -> ScopePolicy:
    """Return the policy for a canonical or legacy scope name."""
    return SCOPE_POLICIES[normalize_scope(scope)]


def derive_expected_nights(startdate: str, stopdate: str) -> list[str]:
    """Return half-open UTC calendar nights from startdate inclusive to stopdate exclusive."""
    start = date.fromisoformat(startdate)
    stop = date.fromisoformat(stopdate)
    if stop <= start:
        raise ValueError("stopdate must be after startdate")
    nights = []
    cursor = start
    while cursor < stop:
        nights.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return nights


def date_window_component(startdate: str, stopdate: str) -> str:
    """Return a stable path component for a UTC date window."""
    derive_expected_nights(startdate, stopdate)
    return f"{startdate}_to_{stopdate}"


def scope_paths(scope: str, startdate: str, stopdate: str, topic: str, run_id: str | None = None) -> dict[str, str]:
    """Build expected raw, processed, and output paths for a topic delivery."""
    policy = get_scope_policy(scope)
    window = date_window_component(startdate, stopdate)
    processed_tail = run_id or "<run_id>"
    return {
        "raw_delivery_dir": f"data/raw/data_transfer/{policy.path_component}/{window}/{topic}",
        "processed_dir": f"data/processed/data_transfer/{policy.path_component}/{window}/{processed_tail}",
        "output_dir": f"outputs/data_transfer/{policy.path_component}/{window}/{processed_tail}",
        "window_component": window,
        "scope_path_component": policy.path_component,
    }


def claim_policy_for_scope(scope: str) -> dict[str, Any]:
    """Return a serializable completeness claim policy."""
    policy = get_scope_policy(scope)
    key = "week_complete_default" if policy.is_multi_night else "night_complete_default"
    return {
        key: policy.completeness_default,
        "allow_completeness_only_after_validation": policy.allow_completeness_only_after_validation,
    }


def scope_metadata(scope: str, filters: list[str] | None = None) -> dict[str, Any]:
    """Return serializable scope metadata."""
    policy = get_scope_policy(scope)
    filters = filters or []
    return {
        "scope": policy.scope,
        "path_component": policy.path_component,
        "is_tag_filtered": bool(filters) or policy.is_tag_filtered,
        "is_all_alert": bool(policy.is_all_alert and not filters),
        "is_single_night": policy.is_single_night,
        "is_multi_night": policy.is_multi_night,
        "packet_type": policy.packet_type,
        "content": policy.content_type,
        "filters": filters,
        "claim_policy": claim_policy_for_scope(policy.scope),
    }

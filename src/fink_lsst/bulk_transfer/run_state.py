"""Run lifecycle, claim, and adaptive scope state rules."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class LifecycleState(str, Enum):
    DECLARED = "declared"
    TOPIC_REGISTERED = "topic_registered"
    DOWNLOAD_PENDING = "download_pending"
    DOWNLOAD_ACTIVE = "download_active"
    RAW_MISSING = "raw_missing"
    RAW_PARTIAL = "raw_partial"
    RAW_APPEARS_COMPLETE_UNVERIFIED = "raw_appears_complete_unverified"
    RAW_INSPECTED = "raw_inspected"
    INGESTION_PENDING = "ingestion_pending"
    INGESTION_PARTIAL = "ingestion_partial"
    INGESTED = "ingested"
    VALIDATED_WITH_WARNINGS = "validated_with_warnings"
    VALIDATED = "validated"
    FAILED = "failed"
    ARCHIVED = "archived"


class ClaimState(str, Enum):
    BLOCKED = "blocked"
    UNRESOLVED = "unresolved"
    ALLOWED = "allowed"
    REJECTED = "rejected"


class DataScope(str, Enum):
    SMOKE = "smoke"
    BOUNDED_TAG = "bounded_tag"
    SINGLE_NIGHT = "single_night"
    MULTI_NIGHT = "multi_night"
    FULL_WEEK = "full_week"
    FULL_MONTH = "full_month"
    PARTIAL_SCHEMA_ONLY = "partial_schema_only"


RAW_INCOMPLETE_STATES = {
    LifecycleState.DECLARED.value,
    LifecycleState.TOPIC_REGISTERED.value,
    LifecycleState.DOWNLOAD_PENDING.value,
    LifecycleState.DOWNLOAD_ACTIVE.value,
    LifecycleState.RAW_MISSING.value,
    LifecycleState.RAW_PARTIAL.value,
}

SCIENCE_READY_STATES = {
    LifecycleState.VALIDATED.value,
}

TERMINAL_STATES = {
    LifecycleState.VALIDATED.value,
    LifecycleState.FAILED.value,
    LifecycleState.ARCHIVED.value,
}

LEGAL_TRANSITIONS = {
    LifecycleState.DECLARED.value: {
        LifecycleState.TOPIC_REGISTERED.value,
        LifecycleState.DOWNLOAD_PENDING.value,
        LifecycleState.RAW_MISSING.value,
        LifecycleState.FAILED.value,
    },
    LifecycleState.TOPIC_REGISTERED.value: {
        LifecycleState.DOWNLOAD_PENDING.value,
        LifecycleState.DOWNLOAD_ACTIVE.value,
        LifecycleState.RAW_MISSING.value,
        LifecycleState.FAILED.value,
    },
    LifecycleState.DOWNLOAD_PENDING.value: {
        LifecycleState.DOWNLOAD_ACTIVE.value,
        LifecycleState.RAW_MISSING.value,
        LifecycleState.RAW_PARTIAL.value,
        LifecycleState.FAILED.value,
    },
    LifecycleState.DOWNLOAD_ACTIVE.value: {
        LifecycleState.RAW_PARTIAL.value,
        LifecycleState.RAW_APPEARS_COMPLETE_UNVERIFIED.value,
        LifecycleState.FAILED.value,
    },
    LifecycleState.RAW_MISSING.value: {
        LifecycleState.DOWNLOAD_PENDING.value,
        LifecycleState.DOWNLOAD_ACTIVE.value,
        LifecycleState.FAILED.value,
    },
    LifecycleState.RAW_PARTIAL.value: {
        LifecycleState.DOWNLOAD_ACTIVE.value,
        LifecycleState.RAW_APPEARS_COMPLETE_UNVERIFIED.value,
        LifecycleState.RAW_INSPECTED.value,
        LifecycleState.FAILED.value,
    },
    LifecycleState.RAW_APPEARS_COMPLETE_UNVERIFIED.value: {
        LifecycleState.RAW_INSPECTED.value,
        LifecycleState.INGESTION_PENDING.value,
        LifecycleState.FAILED.value,
    },
    LifecycleState.RAW_INSPECTED.value: {
        LifecycleState.INGESTION_PENDING.value,
        LifecycleState.INGESTION_PARTIAL.value,
        LifecycleState.FAILED.value,
    },
    LifecycleState.INGESTION_PENDING.value: {
        LifecycleState.INGESTION_PARTIAL.value,
        LifecycleState.INGESTED.value,
        LifecycleState.FAILED.value,
    },
    LifecycleState.INGESTION_PARTIAL.value: {
        LifecycleState.INGESTED.value,
        LifecycleState.VALIDATED_WITH_WARNINGS.value,
        LifecycleState.FAILED.value,
    },
    LifecycleState.INGESTED.value: {
        LifecycleState.VALIDATED_WITH_WARNINGS.value,
        LifecycleState.VALIDATED.value,
        LifecycleState.FAILED.value,
    },
    LifecycleState.VALIDATED_WITH_WARNINGS.value: {
        LifecycleState.VALIDATED.value,
        LifecycleState.FAILED.value,
        LifecycleState.ARCHIVED.value,
    },
    LifecycleState.VALIDATED.value: {
        LifecycleState.ARCHIVED.value,
    },
    LifecycleState.FAILED.value: {
        LifecycleState.DECLARED.value,
        LifecycleState.TOPIC_REGISTERED.value,
        LifecycleState.ARCHIVED.value,
    },
    LifecycleState.ARCHIVED.value: set(),
}


@dataclass(frozen=True)
class TransitionCheck:
    allowed: bool
    reason: str


def normalize_lifecycle_state(value: str) -> str:
    """Return a canonical lifecycle state or raise ValueError."""
    try:
        return LifecycleState(value).value
    except ValueError as exc:
        raise ValueError(f"Unsupported lifecycle state: {value}") from exc


def normalize_claim_state(value: str) -> str:
    """Return a canonical claim state or raise ValueError."""
    try:
        return ClaimState(value).value
    except ValueError as exc:
        raise ValueError(f"Unsupported claim state: {value}") from exc


def normalize_data_scope(value: str) -> str:
    """Return a canonical adaptive data scope or raise ValueError."""
    aliases = {
        "smoke_delivery": DataScope.SMOKE.value,
        "tag_filtered_smoke_delivery": DataScope.BOUNDED_TAG.value,
        "full_night": DataScope.SINGLE_NIGHT.value,
        "full_night_all_alerts": DataScope.SINGLE_NIGHT.value,
        "full_week_full_packet": DataScope.FULL_WEEK.value,
    }
    candidate = aliases.get(value, value)
    try:
        return DataScope(candidate).value
    except ValueError as exc:
        raise ValueError(f"Unsupported run scope: {value}") from exc


def check_lifecycle_transition(current: str, target: str) -> TransitionCheck:
    """Validate a lifecycle transition without mutating state."""
    current_state = normalize_lifecycle_state(current)
    target_state = normalize_lifecycle_state(target)
    if current_state == target_state:
        return TransitionCheck(True, "state unchanged")
    if current_state == LifecycleState.VALIDATED_WITH_WARNINGS.value and target_state == LifecycleState.VALIDATED.value:
        return TransitionCheck(True, "warnings must be explicitly resolved before validated")
    if target_state in LEGAL_TRANSITIONS[current_state]:
        return TransitionCheck(True, "transition allowed")
    return TransitionCheck(False, f"illegal transition: {current_state} -> {target_state}")


def lifecycle_is_science_ready(state: str) -> bool:
    """Return True only for strictly science-ready lifecycle states."""
    return normalize_lifecycle_state(state) in SCIENCE_READY_STATES


def raw_state_blocks_science(state: str) -> bool:
    """Return True when the lifecycle state cannot support science claims."""
    return normalize_lifecycle_state(state) in RAW_INCOMPLETE_STATES

"""Strict manifest model for adaptive Fink Data Transfer runs."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml

from .run_state import (
    ClaimState,
    DataScope,
    LifecycleState,
    normalize_claim_state,
    normalize_data_scope,
    normalize_lifecycle_state,
    raw_state_blocks_science,
)


SCHEMA_VERSION = 1
SECRET_MARKERS = ("password", "passwd", "token", "secret", "credential", "sasl", "jaas", "api_key", "apikey")
PACKET_TYPES = {"light_static", "full", "medium", "unknown"}
CLAIM_KEYS = ("all_alert_completeness", "night_completeness", "week_completeness")
PATH_BASES = {
    "raw_dir": Path("data/raw/data_transfer"),
    "processed_dir": Path("data/processed/data_transfer"),
    "outputs_dir": Path("outputs/data_transfer"),
}


@dataclass
class ClaimStateSet:
    all_alert_completeness: str = ClaimState.BLOCKED.value
    night_completeness: str = ClaimState.BLOCKED.value
    week_completeness: str = ClaimState.BLOCKED.value


@dataclass
class RunPaths:
    raw_dir: str = ""
    processed_dir: str = ""
    outputs_dir: str = ""


@dataclass
class ProcessingOptions:
    split_by_night: bool = True
    allow_partial: bool = False
    max_files: int | None = None
    diagnostics: bool = True
    validation: bool = True
    plots: bool = True


@dataclass
class DownloadEvidence:
    kafka_lag_zero: bool | None = None
    expected_total_messages: int | None = None
    terminal_progress_messages: int | None = None
    local_readable_rows: int | None = None
    raw_file_count: int | None = None
    raw_size_bytes: int | None = None


@dataclass
class RunManifest:
    schema_version: int
    run_name: str
    run_id: str | None
    survey: str
    broker: str
    topic: str | None
    batch_id: str | None
    startdate: str
    stopdate: str
    date_mode: str
    scope: str
    packet_type: str
    content: str
    filters: list[str]
    is_all_alert: bool
    expected_nights: list[str]
    lifecycle_state: str
    claim_state: ClaimStateSet = field(default_factory=ClaimStateSet)
    paths: RunPaths = field(default_factory=RunPaths)
    processing: ProcessingOptions = field(default_factory=ProcessingOptions)
    download_evidence: DownloadEvidence = field(default_factory=DownloadEvidence)
    notes: str = ""
    raw_payload: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)


def load_run_manifest(path: str | Path) -> RunManifest:
    """Load a run manifest from YAML."""
    manifest_path = Path(path)
    payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    return manifest_from_dict(payload)


def write_run_manifest(manifest: RunManifest, path: str | Path) -> Path:
    """Write a run manifest to YAML after validating it."""
    errors, _warnings = validate_run_manifest(manifest)
    if errors:
        raise ValueError("; ".join(errors))
    manifest_path = Path(path)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(yaml.safe_dump(manifest_to_dict(manifest), sort_keys=False), encoding="utf-8")
    return manifest_path


def validate_run_manifest(manifest: RunManifest, project_root: str | Path = ".") -> tuple[list[str], list[str]]:
    """Return validation errors and warnings for a run manifest."""
    errors: list[str] = []
    warnings: list[str] = []
    payload = manifest_to_dict(manifest)
    payload_for_secret_scan = getattr(manifest, "raw_payload", None) or payload

    if manifest.schema_version != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    if manifest.survey != "lsst":
        errors.append("survey must be lsst")
    if manifest.broker != "fink":
        errors.append("broker must be fink")
    if manifest.date_mode != "utc_window":
        errors.append("date_mode must be utc_window")

    try:
        expected = derive_expected_nights(manifest.startdate, manifest.stopdate)
    except ValueError as exc:
        errors.append(str(exc))
        expected = []
    if expected and manifest.expected_nights != expected:
        errors.append("expected_nights must match the half-open UTC startdate/stopdate window")

    try:
        normalize_data_scope(manifest.scope)
    except ValueError as exc:
        errors.append(str(exc))
    try:
        normalize_lifecycle_state(manifest.lifecycle_state)
    except ValueError as exc:
        errors.append(str(exc))
    for key, value in manifest_to_dict(manifest.claim_state).items():
        try:
            normalize_claim_state(str(value))
        except ValueError as exc:
            errors.append(f"{key}: {exc}")

    if manifest.packet_type not in PACKET_TYPES:
        errors.append(f"packet_type must be one of {sorted(PACKET_TYPES)}")
    if manifest.packet_type == "unknown":
        warnings.append("unknown packet_type keeps completeness claims strict")
    if manifest.filters and manifest.is_all_alert:
        errors.append("filters imply is_all_alert = false")
    if manifest.is_all_alert and manifest.filters:
        errors.append("all-alert manifests must not have filters")
    if not manifest.is_all_alert and not manifest.filters and manifest.scope in {DataScope.SINGLE_NIGHT.value, DataScope.MULTI_NIGHT.value, DataScope.FULL_WEEK.value, DataScope.FULL_MONTH.value}:
        warnings.append("unfiltered non-all-alert manifest has conservative completeness claims")

    project_root = Path(project_root).resolve()
    for field_name, base in PATH_BASES.items():
        path_value = getattr(manifest.paths, field_name)
        if not path_value:
            errors.append(f"paths.{field_name} is required")
            continue
        if not _is_under(Path(path_value), base, project_root):
            errors.append(f"paths.{field_name} must be under {base}")

    derived_claim = derive_claim_state(manifest)
    if raw_state_blocks_science(manifest.lifecycle_state):
        for key in CLAIM_KEYS:
            if getattr(manifest.claim_state, key) == ClaimState.ALLOWED.value:
                errors.append(f"{key} cannot be allowed while lifecycle_state is {manifest.lifecycle_state}")
    if manifest.filters:
        if manifest.claim_state.all_alert_completeness == ClaimState.ALLOWED.value:
            errors.append("tag-filtered runs cannot allow all-alert completeness claims")
    if manifest.lifecycle_state == LifecycleState.VALIDATED_WITH_WARNINGS.value and any(
        getattr(manifest.claim_state, key) == ClaimState.ALLOWED.value for key in CLAIM_KEYS
    ):
        warnings.append("validated_with_warnings requires explicit review before allowing claims")
    if derived_claim.week_completeness != ClaimState.ALLOWED.value and manifest.claim_state.week_completeness == ClaimState.ALLOWED.value:
        errors.append("week_completeness cannot be allowed by default")

    secret_paths = find_credential_like_entries(payload_for_secret_scan)
    if secret_paths:
        errors.append("manifest contains credential-looking keys or values: " + ", ".join(secret_paths[:10]))

    return errors, warnings


def derive_expected_nights(startdate: str, stopdate: str) -> list[str]:
    """Return half-open UTC calendar nights from startdate inclusive to stopdate exclusive."""
    start = date.fromisoformat(str(startdate))
    stop = date.fromisoformat(str(stopdate))
    if stop <= start:
        raise ValueError("startdate must be before stopdate")
    nights = []
    cursor = start
    while cursor < stop:
        nights.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return nights


def derive_default_paths(manifest: RunManifest, project_root: str | Path = ".") -> RunPaths:
    """Derive stable raw, processed, and output directories for a manifest."""
    del project_root
    component = _path_component_for_scope(manifest.scope, manifest.filters, manifest.packet_type)
    window = f"{manifest.startdate}_to_{manifest.stopdate}"
    topic_or_run = manifest.topic or manifest.run_id or manifest.run_name
    run_tail = manifest.run_id or topic_or_run
    return RunPaths(
        raw_dir=f"data/raw/data_transfer/{component}/{window}/{topic_or_run}",
        processed_dir=f"data/processed/data_transfer/{component}/{window}/{run_tail}",
        outputs_dir=f"outputs/data_transfer/{component}/{window}/{run_tail}",
    )


def normalize_packet_type(content: str | None) -> str:
    """Infer the packet type from content text."""
    text = (content or "").lower().replace("-", " ")
    if "full" in text:
        return "full"
    if "medium" in text:
        return "medium"
    if "light" in text or "static" in text:
        return "light_static"
    return "unknown"


def infer_scope(startdate: str, stopdate: str, filters: list[str] | None, requested_scope: str | None = None) -> str:
    """Infer an adaptive scope from dates and filters."""
    if requested_scope:
        return normalize_data_scope(requested_scope)
    nights = derive_expected_nights(startdate, stopdate)
    if filters:
        return DataScope.BOUNDED_TAG.value
    if len(nights) == 1:
        return DataScope.SINGLE_NIGHT.value
    if len(nights) <= 7:
        return DataScope.FULL_WEEK.value
    if len(nights) <= 31:
        return DataScope.FULL_MONTH.value
    return DataScope.MULTI_NIGHT.value


def derive_claim_state(manifest: RunManifest) -> ClaimStateSet:
    """Derive conservative default claim states for a manifest."""
    if raw_state_blocks_science(manifest.lifecycle_state):
        return ClaimStateSet(
            all_alert_completeness=ClaimState.BLOCKED.value,
            night_completeness=ClaimState.BLOCKED.value,
            week_completeness=ClaimState.BLOCKED.value if manifest.filters else ClaimState.UNRESOLVED.value,
        )
    if manifest.filters or not manifest.is_all_alert:
        return ClaimStateSet(
            all_alert_completeness=ClaimState.BLOCKED.value,
            night_completeness=ClaimState.BLOCKED.value,
            week_completeness=ClaimState.BLOCKED.value,
        )
    if manifest.packet_type == "unknown":
        return ClaimStateSet(
            all_alert_completeness=ClaimState.UNRESOLVED.value,
            night_completeness=ClaimState.UNRESOLVED.value,
            week_completeness=ClaimState.UNRESOLVED.value,
        )
    if manifest.scope in {DataScope.FULL_WEEK.value, DataScope.FULL_MONTH.value, DataScope.MULTI_NIGHT.value}:
        return ClaimStateSet(
            all_alert_completeness=ClaimState.UNRESOLVED.value,
            night_completeness=ClaimState.UNRESOLVED.value,
            week_completeness=ClaimState.UNRESOLVED.value,
        )
    return ClaimStateSet(
        all_alert_completeness=ClaimState.UNRESOLVED.value,
        night_completeness=ClaimState.UNRESOLVED.value,
        week_completeness=ClaimState.BLOCKED.value,
    )


def manifest_from_topic_registry(topic_record: dict[str, Any]) -> RunManifest:
    """Build a manifest from an existing non-secret topic registry entry."""
    filters = list(topic_record.get("filters") or ([topic_record["filter"]] if topic_record.get("filter") else []))
    start = str(topic_record.get("startdate") or topic_record.get("utc_start"))
    stop = str(topic_record.get("stopdate") or topic_record.get("utc_stop"))
    scope = infer_scope(start, stop, filters, requested_scope=topic_record.get("scope"))
    content = str(topic_record.get("content") or topic_record.get("content_type") or "")
    packet_type = str(topic_record.get("packet_type") or normalize_packet_type(content))
    topic = topic_record.get("topic")
    run_name = str(topic_record.get("run_name") or topic or f"{scope}_{start}_to_{stop}")
    manifest = RunManifest(
        schema_version=SCHEMA_VERSION,
        run_name=run_name,
        run_id=topic_record.get("run_id"),
        survey=str(topic_record.get("survey", "lsst")).lower(),
        broker="fink",
        topic=topic,
        batch_id=topic_record.get("batch_id"),
        startdate=start,
        stopdate=stop,
        date_mode="utc_window",
        scope=scope,
        packet_type=packet_type,
        content=content,
        filters=filters,
        is_all_alert=bool(topic_record.get("is_all_alert", topic_record.get("all_alerts", False))) and not filters,
        expected_nights=derive_expected_nights(start, stop),
        lifecycle_state=str(topic_record.get("lifecycle_state") or LifecycleState.TOPIC_REGISTERED.value),
        notes=str(topic_record.get("notes") or ""),
    )
    manifest.paths = RunPaths(
        raw_dir=str(topic_record.get("raw_delivery_dir") or derive_default_paths(manifest).raw_dir),
        processed_dir=str(topic_record.get("processed_dir_template") or topic_record.get("processed_dir") or derive_default_paths(manifest).processed_dir),
        outputs_dir=str(topic_record.get("output_dir_template") or topic_record.get("outputs_dir") or derive_default_paths(manifest).outputs_dir),
    )
    manifest.claim_state = derive_claim_state(manifest)
    return manifest


def topic_entry_from_manifest(manifest: RunManifest) -> dict[str, Any]:
    """Convert a RunManifest to the legacy topic-entry shape used by guarded scripts."""
    scope = _legacy_scope_for_manifest(manifest)
    return {
        "topic": manifest.topic,
        "scope": scope,
        "survey": manifest.survey,
        "utc_start": manifest.startdate,
        "utc_stop": manifest.stopdate,
        "startdate": manifest.startdate,
        "stopdate": manifest.stopdate,
        "content": manifest.content,
        "content_type": manifest.content,
        "packet_type": manifest.packet_type,
        "filters": manifest.filters,
        "filter": manifest.filters[0] if len(manifest.filters) == 1 else None,
        "is_all_alert": manifest.is_all_alert,
        "all_alerts": manifest.is_all_alert,
        "expected_nights": manifest.expected_nights,
        "lifecycle_state": manifest.lifecycle_state,
        "raw_delivery_dir": manifest.paths.raw_dir,
        "processed_dir_template": manifest.paths.processed_dir,
        "output_dir_template": manifest.paths.outputs_dir,
        "batch_id": manifest.batch_id,
        "expected_total_messages": manifest.download_evidence.expected_total_messages,
        "terminal_progress_messages": manifest.download_evidence.terminal_progress_messages,
        "local_readable_rows": manifest.download_evidence.local_readable_rows,
        "raw_file_count": manifest.download_evidence.raw_file_count,
        "notes": manifest.notes,
    }


def manifest_to_dict(value: Any) -> dict[str, Any]:
    """Convert a manifest or nested manifest dataclass to plain dictionaries."""
    if isinstance(value, RunManifest):
        return {item.name: manifest_to_dict(getattr(value, item.name)) if hasattr(getattr(value, item.name), "__dataclass_fields__") else getattr(value, item.name) for item in fields(value) if item.name != "raw_payload"}
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    if isinstance(value, dict):
        return value
    raise TypeError(f"Unsupported manifest value: {type(value)!r}")


def manifest_from_dict(payload: dict[str, Any]) -> RunManifest:
    """Build a RunManifest from a dictionary."""
    if not isinstance(payload, dict):
        raise ValueError("manifest payload must be a mapping")
    filters = list(payload.get("filters") or [])
    start = str(payload.get("startdate"))
    stop = str(payload.get("stopdate"))
    scope = normalize_data_scope(str(payload.get("scope") or infer_scope(start, stop, filters)))
    packet_type = str(payload.get("packet_type") or normalize_packet_type(payload.get("content")))
    expected = list(payload.get("expected_nights") or derive_expected_nights(start, stop))
    claim_payload = payload.get("claim_state") or {}
    path_payload = payload.get("paths") or {}
    processing_payload = payload.get("processing") or {}
    evidence_payload = payload.get("download_evidence") or {}
    manifest = RunManifest(
        schema_version=int(payload.get("schema_version", SCHEMA_VERSION)),
        run_name=str(payload.get("run_name") or payload.get("run_id") or "unnamed_run"),
        run_id=payload.get("run_id"),
        survey=str(payload.get("survey", "lsst")).lower(),
        broker=str(payload.get("broker", "fink")).lower(),
        topic=payload.get("topic"),
        batch_id=payload.get("batch_id"),
        startdate=start,
        stopdate=stop,
        date_mode=str(payload.get("date_mode", "utc_window")),
        scope=scope,
        packet_type=packet_type,
        content=str(payload.get("content", "")),
        filters=filters,
        is_all_alert=bool(payload.get("is_all_alert", False)),
        expected_nights=expected,
        lifecycle_state=normalize_lifecycle_state(str(payload.get("lifecycle_state", LifecycleState.DECLARED.value))),
        claim_state=ClaimStateSet(**{key: claim_payload.get(key, getattr(ClaimStateSet(), key)) for key in CLAIM_KEYS}),
        paths=RunPaths(**{key: path_payload.get(key, "") for key in PATH_BASES}),
        processing=ProcessingOptions(**{key: processing_payload.get(key, getattr(ProcessingOptions(), key)) for key in ProcessingOptions.__dataclass_fields__}),
        download_evidence=DownloadEvidence(**{key: evidence_payload.get(key, getattr(DownloadEvidence(), key)) for key in DownloadEvidence.__dataclass_fields__}),
        notes=str(payload.get("notes", "")),
        raw_payload=dict(payload),
    )
    if not all(getattr(manifest.paths, key) for key in PATH_BASES):
        manifest.paths = derive_default_paths(manifest)
    if not payload.get("claim_state"):
        manifest.claim_state = derive_claim_state(manifest)
    return manifest


def find_credential_like_entries(value: Any, prefix: str = "") -> list[str]:
    """Return manifest paths whose keys or scalar values look credential-like."""
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if any(marker in str(key).lower() for marker in SECRET_MARKERS):
                found.append(path)
            found.extend(find_credential_like_entries(item, path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(find_credential_like_entries(item, f"{prefix}[{index}]"))
    elif isinstance(value, str):
        lowered = value.lower()
        if any(marker in lowered for marker in SECRET_MARKERS):
            found.append(prefix)
    return found


def _path_component_for_scope(scope: str, filters: list[str], packet_type: str) -> str:
    canonical = normalize_data_scope(scope)
    if canonical == DataScope.SMOKE.value:
        return "smoke_delivery"
    if canonical == DataScope.BOUNDED_TAG.value:
        return "smoke_delivery" if filters else "bounded_tag"
    if canonical == DataScope.SINGLE_NIGHT.value:
        return "full_night_full_packet" if packet_type == "full" else "full_night"
    if canonical == DataScope.FULL_WEEK.value:
        return "full_week_full_packet" if packet_type == "full" else "full_week"
    return canonical


def _legacy_scope_for_manifest(manifest: RunManifest) -> str:
    canonical = normalize_data_scope(manifest.scope)
    if canonical == DataScope.BOUNDED_TAG.value:
        return "smoke_delivery"
    if canonical == DataScope.SINGLE_NIGHT.value:
        return "full_night"
    if canonical == DataScope.FULL_WEEK.value and manifest.packet_type == "full":
        return "full_week_full_packet"
    if canonical == DataScope.FULL_WEEK.value:
        return "full_week"
    return canonical


def _is_under(path: Path, base: Path, project_root: Path) -> bool:
    absolute_path = path if path.is_absolute() else project_root / path
    absolute_base = project_root / base
    try:
        absolute_path.resolve().relative_to(absolute_base.resolve())
        return True
    except ValueError:
        return False

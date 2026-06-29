"""Non-secret topic registry helpers for Fink Data Transfer deliveries."""

from __future__ import annotations

import shlex
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .scopes import claim_policy_for_scope, date_window_component, derive_expected_nights, normalize_scope, scope_paths


DEFAULT_TOPIC_REGISTRY_PATH = Path("configs/data_transfer_topics.yaml")


def load_topic_registry(path: str | Path = DEFAULT_TOPIC_REGISTRY_PATH) -> dict[str, Any]:
    """Load the local non-secret Data Transfer topic registry."""
    registry_path = Path(path)
    if not registry_path.exists():
        return {"version": 1, "topics": []}
    with registry_path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    payload.setdefault("version", 1)
    payload.setdefault("topics", [])
    return payload


def save_topic_registry(registry: dict[str, Any], path: str | Path = DEFAULT_TOPIC_REGISTRY_PATH) -> Path:
    """Save the non-secret topic registry."""
    registry_path = Path(path)
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(yaml.safe_dump(registry, sort_keys=False), encoding="utf-8")
    return registry_path


def find_topic_entry(
    registry: dict[str, Any],
    topic: str | None = None,
    scope: str | None = None,
    latest: bool = False,
) -> dict[str, Any] | None:
    """Find a topic registry entry by exact topic, scope, or newest matching scope."""
    entries = [entry for entry in registry.get("topics", []) if isinstance(entry, dict)]
    if topic:
        return next((entry for entry in entries if entry.get("topic") == topic), None)
    if scope:
        scoped = [entry for entry in entries if entry.get("scope") == scope or entry.get("delivery_scope") == scope]
        if latest and scoped:
            return sorted(scoped, key=_entry_sort_key)[-1]
        return scoped[-1] if scoped else None
    return sorted(entries, key=_entry_sort_key)[-1] if latest and entries else None


def register_topic_entry(registry: dict[str, Any], entry: dict[str, Any], force: bool = False) -> dict[str, Any]:
    """Insert or replace a topic entry in a registry payload."""
    validate_topic_entry(entry)
    topics = [item for item in registry.get("topics", []) if isinstance(item, dict)]
    existing_index = next((index for index, item in enumerate(topics) if item.get("topic") == entry["topic"]), None)
    if existing_index is not None and not force:
        raise ValueError(f"Topic already registered: {entry['topic']}")
    if existing_index is None:
        topics.append(entry)
    else:
        topics[existing_index] = entry
    registry = {**registry, "version": registry.get("version", 1), "topics": topics}
    return registry


def validate_topic_entry(entry: dict[str, Any]) -> None:
    """Validate non-secret topic metadata."""
    topic = entry.get("topic")
    if not topic or not re.fullmatch(r"ftransfer_[A-Za-z0-9_.-]+", str(topic)):
        raise ValueError(f"Invalid Fink Data Transfer topic: {topic}")
    normalize_scope(str(entry.get("scope")))
    start = entry.get("utc_start") or entry.get("startdate")
    stop = entry.get("utc_stop") or entry.get("stopdate")
    if not start or not stop:
        raise ValueError("Topic entry requires utc_start/startdate and utc_stop/stopdate")
    derive_expected_nights(str(start), str(stop))
    if _contains_secret_like_key(entry):
        raise ValueError("Topic registry entry contains a suspicious secret-like key")


def build_topic_entry(
    *,
    scope: str,
    survey: str,
    topic: str,
    startdate: str,
    stopdate: str,
    content: str,
    all_alert: bool,
    filters: list[str] | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Build a non-secret topic entry with expected paths and claim policy."""
    canonical_scope = normalize_scope(scope)
    filters = filters or []
    paths = scope_paths(canonical_scope, startdate, stopdate, topic)
    packet_type = "full" if "full" in content.lower() or canonical_scope == "full_week_full_packet" else "light_static"
    entry = {
        "topic": topic,
        "scope": canonical_scope,
        "survey": survey.lower(),
        "utc_start": startdate,
        "utc_stop": stopdate,
        "startdate": startdate,
        "stopdate": stopdate,
        "content": content,
        "content_type": content,
        "packet_type": packet_type,
        "filters": filters,
        "filter": filters[0] if len(filters) == 1 else None,
        "is_all_alert": bool(all_alert and not filters),
        "all_alerts": bool(all_alert and not filters),
        "expected_nights": derive_expected_nights(startdate, stopdate),
        "claim_policy": claim_policy_for_scope(canonical_scope),
        "raw_delivery_dir": paths["raw_delivery_dir"],
        "processed_dir_template": paths["processed_dir"],
        "output_dir_template": paths["output_dir"],
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "notes": notes or "",
    }
    validate_topic_entry(entry)
    return entry


def metadata_from_topic_entry(entry: dict[str, Any] | None, fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    """Convert a registry entry into ingestion/validation metadata."""
    fallback = fallback or {}
    entry = entry or {}
    scope = entry.get("scope") or entry.get("delivery_scope") or fallback.get("scope")
    all_alerts = bool(entry.get("all_alerts", fallback.get("all_alerts", False)))
    fink_filter = entry.get("filter", fallback.get("filter"))
    metadata = {
        **fallback,
        "topic": entry.get("topic", fallback.get("topic")),
        "survey": entry.get("survey", fallback.get("survey", "lsst")),
        "utc_start": entry.get("utc_start", fallback.get("utc_start")),
        "utc_stop": entry.get("utc_stop", fallback.get("utc_stop")),
        "filter": fink_filter,
        "content_type": entry.get("content_type", fallback.get("content_type", "Light static packet")),
        "content": entry.get("content", fallback.get("content")),
        "packet_type": entry.get("packet_type", fallback.get("packet_type")),
        "filters": entry.get("filters", fallback.get("filters", [])),
        "expected_nights": entry.get("expected_nights", fallback.get("expected_nights", [])),
        "raw_delivery_dir": entry.get("raw_delivery_dir", fallback.get("raw_delivery_dir")),
        "processed_dir_template": entry.get("processed_dir_template", fallback.get("processed_dir_template")),
        "output_dir_template": entry.get("output_dir_template", fallback.get("output_dir_template")),
        "claim_policy": entry.get("claim_policy", fallback.get("claim_policy")),
        "scope": scope,
        "all_alerts": all_alerts,
        "portal_estimate_alerts": entry.get("portal_estimate_alerts", fallback.get("portal_estimate_alerts")),
        "kafka_lag_zero": entry.get("kafka_lag_zero", fallback.get("kafka_lag_zero")),
        "truncation_warning": entry.get("truncation_warning", fallback.get("truncation_warning", False)),
        "date_window_explained": entry.get("date_window_explained", fallback.get("date_window_explained", False)),
        "notes": entry.get("notes", fallback.get("notes")),
    }
    metadata["completeness_scope"] = build_completeness_scope(metadata)
    return metadata


def build_completeness_scope(metadata: dict[str, Any]) -> dict[str, Any]:
    """Build the explicit completeness scope used by validation and manifests."""
    scope = metadata.get("scope")
    all_alerts = bool(metadata.get("all_alerts"))
    fink_filter = metadata.get("filter")
    if scope in {"full_night", "full_night_all_alerts"} and all_alerts and not fink_filter:
        return {
            "scope": "full_night",
            "full_night_complete": True,
            "reason": "all-alert full-night Data Transfer scope is recorded; validation must still pass before science claims",
        }
    if scope == "full_week_full_packet" and all_alerts and not fink_filter:
        return {
            "scope": "full_week_full_packet",
            "full_night_complete": False,
            "week_complete": False,
            "reason": "all-alert full-week full-packet scope is recorded; week completeness remains unresolved until validation",
        }
    if scope == "tag_filtered_smoke_delivery" or fink_filter:
        return {
            "scope": "tag_filtered_smoke_delivery",
            "full_night_complete": False,
            "reason": f"{fink_filter or 'tag'} filter and light static packet; not all-alert full-night production",
        }
    return {
        "scope": scope or "unresolved_delivery_scope",
        "full_night_complete": False,
        "reason": "delivery scope is not recorded as all-alert full-night",
    }


def build_download_command(entry: dict[str, Any], outdir: str | Path | None = None) -> list[str]:
    """Return the recommended fink_datatransfer command as argv tokens."""
    topic = entry.get("topic")
    if not topic:
        raise ValueError("Topic entry is missing `topic`")
    survey = str(entry.get("survey", "lsst")).lower()
    if outdir is None:
        outdir = entry.get("raw_delivery_dir") or f"data/raw/data_transfer/{entry.get('scope', 'delivery')}/{topic}"
    return [
        "fink_datatransfer",
        "-survey",
        survey,
        "-topic",
        str(topic),
        "-outdir",
        str(outdir),
        "--dump_schemas",
        "--verbose",
    ]


def render_download_command(entry: dict[str, Any], outdir: str | Path | None = None) -> str:
    """Render the recommended download command for shell display."""
    return " ".join(shlex.quote(part) for part in build_download_command(entry, outdir=outdir))


def render_download_instructions(entry: dict[str, Any], outdir: str | Path | None = None) -> str:
    """Render a safe manual download instruction block."""
    topic = entry["topic"]
    raw_dir = str(outdir or entry.get("raw_delivery_dir") or f"data/raw/data_transfer/{entry.get('scope', 'delivery')}/{topic}")
    command = render_download_command(entry, outdir=raw_dir)
    return "\n".join(
        [
            "# Manual Fink Data Transfer Download Command",
            "",
            "Do not commit raw data. Wait for Kafka lag to reach zero before treating a delivery as complete.",
            "Full-packet week deliveries may be large and may contain heavy nested or binary fields.",
            "",
            "```bash",
            f'TOPIC="{topic}"',
            f'RAW_DIR="{raw_dir}"',
            "",
            'mkdir -p "$RAW_DIR"',
            "",
            command.replace(shlex.quote(raw_dir), '"$RAW_DIR"').replace(shlex.quote(topic), '"$TOPIC"'),
            "```",
            "",
            "## Safety Checks",
            "",
            "```bash",
            "df -h .",
            'find "$RAW_DIR" -type f | wc -l',
            'du -sh "$RAW_DIR"',
            'python scripts/summarize_download_progress.py --raw-dir "$RAW_DIR" --topic "$TOPIC"',
            "```",
            "",
        ]
    )


def render_topic_registry_markdown(registry: dict[str, Any]) -> str:
    """Render a compact registry summary."""
    lines = ["# Data Transfer Topic Registry", ""]
    entries = registry.get("topics", [])
    if not entries:
        lines.append("- No topics recorded yet.")
        return "\n".join(lines) + "\n"
    for entry in entries:
        lines.extend(
            [
                f"## {entry.get('topic', 'unrecorded-topic')}",
                "",
                f"- Scope: `{entry.get('scope') or entry.get('delivery_scope')}`",
                f"- Survey: `{entry.get('survey', 'lsst')}`",
                f"- UTC window: `{entry.get('utc_start')}` to `{entry.get('utc_stop')}`",
                f"- All alerts: `{bool(entry.get('all_alerts'))}`",
                f"- Filter: `{entry.get('filter')}`",
                f"- Content: `{entry.get('content_type', 'Light static packet')}`",
                f"- Kafka lag zero: `{entry.get('kafka_lag_zero')}`",
                f"- Raw delivery dir: `{entry.get('raw_delivery_dir')}`",
                "",
            ]
        )
    return "\n".join(lines)


def new_full_night_topic_template(config: dict[str, Any]) -> dict[str, Any]:
    """Create a placeholder registry entry for the first full-night request."""
    return {
        "topic": "REPLACE_WITH_PORTAL_TOPIC",
        "scope": "full_night_all_alerts",
        "survey": str(config.get("survey", "lsst")).lower(),
        "utc_start": config.get("target_startdate"),
        "utc_stop": config.get("target_stopdate"),
        "all_alerts": True,
        "filter": None,
        "content_type": "Light static packet",
        "cutouts_requested": False,
        "fits_or_images_requested": False,
        "portal_estimate_alerts": None,
        "kafka_lag_zero": None,
        "truncation_warning": False,
        "date_window_explained": False,
        "raw_delivery_dir": (
            "data/raw/data_transfer/full_night/"
            f"{config.get('target_startdate')}_to_{config.get('target_stopdate')}/REPLACE_WITH_PORTAL_TOPIC"
        ),
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "notes": "Replace topic after manual portal submission; do not store credentials here.",
    }


def _entry_sort_key(entry: dict[str, Any]) -> tuple[str, str]:
    return (str(entry.get("created_at_utc") or ""), str(entry.get("topic") or ""))


def _contains_secret_like_key(value: Any) -> bool:
    markers = ("password", "token", "secret", "credential", "sasl", "jaas")
    if isinstance(value, dict):
        for key, item in value.items():
            if any(marker in str(key).lower() for marker in markers):
                return True
            if _contains_secret_like_key(item):
                return True
    elif isinstance(value, list):
        return any(_contains_secret_like_key(item) for item in value)
    return False

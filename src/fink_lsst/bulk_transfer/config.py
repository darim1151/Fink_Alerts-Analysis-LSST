"""Configuration helpers for Fink Data Transfer readiness."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from fink_lsst.time_windows import build_utc_date_window, validate_alert_start_date, validate_utc_timezone


SUPPORTED_OUTPUT_FORMATS = {"parquet", "avro", "json", "csv"}


def load_data_transfer_config(path: str | Path) -> dict[str, Any]:
    """Load the `data_transfer` section from the project YAML config."""
    with Path(path).open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    config = dict(raw.get("data_transfer", {}) or {})
    return validate_data_transfer_config(config)


def validate_data_transfer_config(config: dict[str, Any]) -> dict[str, Any]:
    """Validate Data Transfer readiness config without requiring access."""
    required = ["survey", "target_startdate", "target_stopdate", "timezone", "min_lsst_alert_date_utc"]
    missing = [key for key in required if not config.get(key)]
    if missing:
        raise ValueError(f"Missing data_transfer config keys: {missing}")
    validate_utc_timezone(config)
    if config.get("reject_pre_alert_dates", True):
        validate_alert_start_date(config["target_startdate"], config["min_lsst_alert_date_utc"])
    build_utc_date_window(config["target_startdate"], config["target_stopdate"])
    if not config.get("dry_run", True):
        raise ValueError("data_transfer.dry_run must remain true unless the user explicitly requests submission work")
    safety = config.get("safety", {}) or {}
    if not safety.get("require_user_confirmation_to_submit", True):
        raise ValueError("Data Transfer submission must require explicit user confirmation")
    if safety.get("allow_submit", False):
        raise ValueError("data_transfer.safety.allow_submit must be false for readiness/dry-run mode")
    output_format = str(config.get("preferred_output_format", "")).lower()
    warnings = []
    if output_format not in SUPPORTED_OUTPUT_FORMATS:
        warnings.append(f"Preferred output format support is unknown: {output_format}")
    config = dict(config)
    config["validation_warnings"] = warnings
    return config


def configured_paths(config: dict[str, Any], project_root: str | Path) -> dict[str, Path]:
    """Resolve configured local Data Transfer paths under the project root."""
    root = Path(project_root).resolve()
    local_paths = config.get("local_paths", {}) or {}
    return {
        "raw_delivery_dir": (root / local_paths.get("raw_delivery_dir", "data/raw/data_transfer")).resolve(),
        "processed_dir": (root / local_paths.get("processed_dir", "data/processed/data_transfer")).resolve(),
        "reports_dir": (root / local_paths.get("reports_dir", "outputs/data_transfer")).resolve(),
    }

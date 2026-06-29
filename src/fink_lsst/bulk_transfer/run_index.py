"""Lightweight index for manifest-driven analysis runs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .run_manifest import load_run_manifest, validate_run_manifest


DEFAULT_RUN_INDEX_PATH = Path("configs/runs/index.yaml")


def load_run_index(path: str | Path = DEFAULT_RUN_INDEX_PATH) -> dict[str, Any]:
    """Load the run index YAML file."""
    index_path = Path(path)
    payload = yaml.safe_load(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}
    payload = payload or {}
    payload.setdefault("runs", {})
    return payload


def list_runs(index: dict[str, Any]) -> list[dict[str, Any]]:
    """Return indexed runs with their names attached."""
    runs = []
    for name, record in (index.get("runs") or {}).items():
        if isinstance(record, dict):
            runs.append({"name": name, **record})
    return runs


def find_run(index: dict[str, Any], run_name: str) -> dict[str, Any] | None:
    """Find an indexed run by name."""
    record = (index.get("runs") or {}).get(run_name)
    return {"name": run_name, **record} if isinstance(record, dict) else None


def validate_run_index(path: str | Path = DEFAULT_RUN_INDEX_PATH, project_root: str | Path = ".") -> tuple[list[str], list[str]]:
    """Validate the index and every referenced manifest."""
    project_root = Path(project_root)
    index_path = Path(path)
    if not index_path.is_absolute():
        index_path = project_root / index_path
    errors: list[str] = []
    warnings: list[str] = []
    index = load_run_index(index_path)
    for run in list_runs(index):
        config = run.get("config")
        if not config:
            errors.append(f"{run['name']}: missing config")
            continue
        config_path = Path(config)
        if not config_path.is_absolute():
            config_path = project_root / config_path
        if not config_path.exists():
            errors.append(f"{run['name']}: config does not exist: {config}")
            continue
        manifest = load_run_manifest(config_path)
        manifest_errors, manifest_warnings = validate_run_manifest(manifest, project_root=project_root)
        errors.extend(f"{run['name']}: {item}" for item in manifest_errors)
        warnings.extend(f"{run['name']}: {item}" for item in manifest_warnings)
    return errors, warnings


def summarize_runs(path: str | Path = DEFAULT_RUN_INDEX_PATH, project_root: str | Path = ".") -> list[dict[str, Any]]:
    """Return compact summaries for all indexed run configs."""
    project_root = Path(project_root)
    index_path = Path(path)
    if not index_path.is_absolute():
        index_path = project_root / index_path
    summaries = []
    for run in list_runs(load_run_index(index_path)):
        config = run.get("config")
        config_path = Path(config)
        if not config_path.is_absolute():
            config_path = project_root / config_path
        manifest = load_run_manifest(config_path)
        summaries.append(
            {
                "name": run["name"],
                "role": run.get("role"),
                "config": str(config),
                "scope": manifest.scope,
                "date_window": f"{manifest.startdate} to {manifest.stopdate}",
                "packet_type": manifest.packet_type,
                "lifecycle_state": manifest.lifecycle_state,
                "claim_state": {
                    "all_alert_completeness": manifest.claim_state.all_alert_completeness,
                    "night_completeness": manifest.claim_state.night_completeness,
                    "week_completeness": manifest.claim_state.week_completeness,
                },
            }
        )
    return summaries

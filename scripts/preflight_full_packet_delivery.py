#!/usr/bin/env python
"""Run pre-download checks for a full-week full-packet delivery."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

from fink_lsst.bulk_transfer.topic_registry import find_topic_entry, load_topic_registry


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SECRET_MARKERS = ("password", "token", "secret", "credential", "sasl", "jaas")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", default="configs/data_transfer_topics.yaml")
    parser.add_argument("--topic", required=True)
    parser.add_argument("--allow-existing-raw", action="store_true")
    args = parser.parse_args()

    registry = load_topic_registry(PROJECT_ROOT / args.registry)
    entry = find_topic_entry(registry, topic=args.topic)
    report = build_preflight_report(entry, allow_existing_raw=args.allow_existing_raw, registry_path=PROJECT_ROOT / args.registry)
    output_base = output_base_for(entry)
    output_base.mkdir(parents=True, exist_ok=True)
    (output_base / "PREFLIGHT.md").write_text(render_preflight_markdown(report), encoding="utf-8")
    (output_base / "preflight.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(render_preflight_markdown(report))
    return 0 if report["decision"] != "blocked" else 2


def build_preflight_report(entry: dict[str, Any] | None, allow_existing_raw: bool, registry_path: Path) -> dict[str, Any]:
    checks = []
    checks.append(check("topic_registered", bool(entry), "Topic is registered" if entry else "Topic is missing from registry", "blocked"))
    if not entry:
        return finalize_report(None, checks)
    raw_dir = PROJECT_ROOT / entry["raw_delivery_dir"]
    checks.append(check("scope_full_week_full_packet", entry.get("scope") == "full_week_full_packet", "Scope is full_week_full_packet", "blocked"))
    complete_metadata = all(entry.get(key) for key in ("topic", "survey", "utc_start", "utc_stop", "content", "packet_type", "raw_delivery_dir"))
    checks.append(check("topic_metadata_complete", complete_metadata, "Required topic metadata is complete", "blocked"))
    checks.append(check("fink_client_importable", module_importable("fink_client"), "fink_client import checked", "warning"))
    checks.append(check("fink_datatransfer_available", fink_datatransfer_available(), "fink_datatransfer executable checked", "blocked"))
    checks.append(check("raw_path_under_data_raw", is_relative_to(raw_dir, PROJECT_ROOT / "data/raw"), "Raw path is under data/raw", "blocked"))
    checks.append(check("raw_path_ignored", git_ignored(raw_dir / "placeholder.parquet"), "Raw path is protected by .gitignore", "blocked"))
    checks.append(check("processed_path_ignored", git_ignored(PROJECT_ROOT / entry["processed_dir_template"].replace("<run_id>", "RUN") / "alerts.parquet"), "Processed path is protected by .gitignore", "blocked"))
    checks.append(check("output_path_ignored", git_ignored(PROJECT_ROOT / entry["output_dir_template"].replace("<run_id>", "RUN") / "report.json"), "Output path is protected by .gitignore", "blocked"))
    checks.append(check("credentials_not_git_visible", not visible_credential_like_files(), "No credential-like files are visible to Git", "blocked"))
    disk = shutil.disk_usage(PROJECT_ROOT)
    checks.append(check("free_disk_space", disk.free > 20 * 1024**3, "Free disk space exceeds 20 GiB", "blocked"))
    checks.append(check("previous_smoke_provenance", (PROJECT_ROOT / "docs/IMPORTANT_RUNS.md").exists(), "Smoke provenance exists", "warning"))
    checks.append(check("registry_no_secret_keys", not contains_secret_like_key(yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}), "Registry has no secret-like keys", "blocked"))
    checks.append(check("raw_directory_conflict", allow_existing_raw or not raw_dir.exists() or not any(raw_dir.iterdir()), "No conflicting non-empty raw directory", "blocked"))
    report = finalize_report(entry, checks)
    report["system"] = {
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "venv": sys.prefix,
        "disk_free_gb": round(disk.free / 1024**3, 2),
        "sizes": {path: run(["du", "-sh", path]).split()[0] if (PROJECT_ROOT / path).exists() else "missing" for path in ("data/raw", "data/processed", "outputs")},
    }
    return report


def finalize_report(entry: dict[str, Any] | None, checks: list[dict[str, Any]]) -> dict[str, Any]:
    failed_blockers = [item for item in checks if not item["passed"] and item["on_fail"] == "blocked"]
    failed_warnings = [item for item in checks if not item["passed"] and item["on_fail"] == "warning"]
    decision = "blocked" if failed_blockers else "warning_user_review" if failed_warnings else "ready_to_download"
    return {"decision": decision, "topic": (entry or {}).get("topic"), "entry": entry, "checks": checks}


def check(name: str, passed: bool, message: str, on_fail: str) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "message": message, "on_fail": on_fail}


def output_base_for(entry: dict[str, Any] | None) -> Path:
    if entry:
        return PROJECT_ROOT / "outputs/data_transfer/full_week_full_packet" / f"{entry['utc_start']}_to_{entry['utc_stop']}"
    return PROJECT_ROOT / "outputs/data_transfer/full_week_full_packet/unregistered"


def render_preflight_markdown(report: dict[str, Any]) -> str:
    lines = ["# Full-Packet Delivery Preflight", "", f"- Decision: `{report['decision']}`", f"- Topic: `{report.get('topic')}`", "", "## Checks", ""]
    for item in report["checks"]:
        status = "passed" if item["passed"] else "failed"
        lines.append(f"- `{item['name']}`: {status} - {item['message']}")
    if report.get("system"):
        lines.extend(["", "## System", ""])
        for key, value in report["system"].items():
            lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "No data was downloaded by this preflight.", ""])
    return "\n".join(lines)


def module_importable(name: str) -> bool:
    completed = subprocess.run([sys.executable, "-c", f"import {name}"], cwd=PROJECT_ROOT, capture_output=True)
    return completed.returncode == 0


def fink_datatransfer_available() -> bool:
    """Detect fink_datatransfer on PATH or beside the active Python executable."""
    if shutil.which("fink_datatransfer") is not None:
        return True
    candidates = [
        Path(sys.executable).parent / "fink_datatransfer",
        Path(sys.executable).resolve().parent / "fink_datatransfer",
    ]
    return any(path.exists() and path.is_file() for path in candidates)


def git_ignored(path: Path) -> bool:
    completed = subprocess.run(["git", "check-ignore", "-q", str(path.relative_to(PROJECT_ROOT))], cwd=PROJECT_ROOT)
    return completed.returncode == 0


def visible_credential_like_files() -> list[str]:
    completed = subprocess.run(["git", "status", "--short", "-uall"], cwd=PROJECT_ROOT, text=True, capture_output=True)
    visible = []
    for line in completed.stdout.splitlines():
        path = line[3:].strip()
        if any(marker in Path(path).name.lower() for marker in SECRET_MARKERS):
            visible.append(path)
    return visible


def contains_secret_like_key(value: Any) -> bool:
    if isinstance(value, dict):
        return any(any(marker in str(key).lower() for marker in SECRET_MARKERS) or contains_secret_like_key(item) for key, item in value.items())
    if isinstance(value, list):
        return any(contains_secret_like_key(item) for item in value)
    return False


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def run(args: list[str]) -> str:
    return subprocess.run(args, cwd=PROJECT_ROOT, text=True, capture_output=True, check=False).stdout


if __name__ == "__main__":
    raise SystemExit(main())

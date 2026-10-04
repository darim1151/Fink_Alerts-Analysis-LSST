#!/usr/bin/env python
"""Report the next safe Data Transfer action."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from fink_lsst.bulk_transfer.client_probe import run_safe_client_probes
from fink_lsst.bulk_transfer.config import configured_paths, load_data_transfer_config
from fink_lsst.bulk_transfer.ingest import find_latest_delivery_dir
from fink_lsst.bulk_transfer.output_inspector import summarize_delivery
from fink_lsst.bulk_transfer.readiness_decision import decide_data_transfer_readiness, render_next_action
from fink_lsst.bulk_transfer.reporting import render_client_probe_report
from fink_lsst.bulk_transfer.run_manifest import load_run_manifest, topic_entry_from_manifest
from fink_lsst.bulk_transfer.topic_registry import find_topic_entry, load_topic_registry
from fink_lsst.storage import write_json


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/local_smoke_test.yaml")
    parser.add_argument("--run-config")
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args()
    project_root = Path(args.project_root).resolve()
    config = load_data_transfer_config(project_root / args.config)
    paths = configured_paths(config, project_root)
    output_dir = paths["reports_dir"]
    output_dir.mkdir(parents=True, exist_ok=True)

    setup = _read(output_dir / "setup_status.json")
    client_probe = run_safe_client_probes()
    write_json(client_probe, output_dir / "client_probe.json", enforce_allowed_roots=False)
    (output_dir / "CLIENT_PROBE.md").write_text(render_client_probe_report(client_probe), encoding="utf-8")
    smoke_request = _read(output_dir / "request_drafts/SMOKE_DATA_TRANSFER_REQUEST.json") or _read(output_dir / "request_drafts/data_transfer_request.json")
    delivery_dir = find_latest_delivery_dir(paths["raw_delivery_dir"] / "smoke_delivery")
    delivery = summarize_delivery(delivery_dir)
    write_json(delivery, output_dir / "delivery_inspection.json", enforce_allowed_roots=False)
    latest_run = _read(output_dir / "smoke_delivery/latest_run.json")
    processed_manifest = _read(Path(latest_run["manifest_path"])) if latest_run and latest_run.get("manifest_path") else None
    validation = _latest_validation(output_dir / "smoke_delivery", latest_run)
    decision = decide_data_transfer_readiness(setup, client_probe, smoke_request, delivery, validation, processed_manifest)
    registry = load_topic_registry(project_root / "configs/data_transfer_topics.yaml")
    full_week_entry = None
    if args.run_config:
        manifest_entry = topic_entry_from_manifest(load_run_manifest(_abs(args.run_config, project_root)))
        if manifest_entry.get("scope") in {"full_week", "full_week_full_packet"}:
            full_week_entry = manifest_entry
    if not full_week_entry:
        full_week_entry = find_topic_entry(registry, scope="full_week_full_packet", latest=True)
    if full_week_entry:
        decision = _full_week_decision(project_root, client_probe, full_week_entry, fallback=decision)
    write_json(decision, output_dir / "data_transfer_next_action.json", enforce_allowed_roots=False)
    text = render_next_action(decision)
    (output_dir / "DATA_TRANSFER_NEXT_ACTION.md").write_text(text, encoding="utf-8")
    print(text)
    return 0


def _latest_validation(smoke_reports_dir: Path, latest_run: dict | None):
    if latest_run and latest_run.get("run_id"):
        direct = smoke_reports_dir / latest_run["run_id"] / "validation_report.json"
        if direct.exists():
            return _read(direct)
    validation_files = sorted(smoke_reports_dir.glob("*/validation_report.json"))
    return _read(validation_files[-1]) if validation_files else None


def _read(path: Path | None):
    if not path or not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _full_week_decision(project_root: Path, client_probe: dict, entry: dict, fallback: dict) -> dict:
    window = f"{entry['utc_start']}_to_{entry['utc_stop']}"
    preflight = _read(project_root / "outputs/data_transfer/full_week_full_packet" / window / "preflight.json")
    raw_dir = project_root / entry["raw_delivery_dir"]
    raw = summarize_delivery(raw_dir)
    evidence = {
        **fallback.get("evidence", {}),
        "full_week_topic_registered": True,
        "full_week_topic": entry.get("topic"),
        "full_week_scope": entry.get("scope"),
        "full_week_preflight_decision": (preflight or {}).get("decision"),
        "full_week_raw_delivery_present": not raw.get("empty", True),
        "full_week_raw_file_count": raw.get("file_count"),
        "full_week_raw_row_count": raw.get("total_rows"),
        "full_week_preflight_path": str(project_root / "outputs/data_transfer/full_week_full_packet" / window / "preflight.json"),
    }
    blockers = []
    if not preflight:
        value = "ready_for_full_week_preflight"
        next_action = f"Run `python scripts/preflight_full_packet_delivery.py --topic {entry['topic']}`."
    elif preflight.get("decision") == "blocked":
        value = "blocked"
        blockers = [check["name"] for check in preflight.get("checks", []) if not check.get("passed") and check.get("on_fail") == "blocked"]
        next_action = "Resolve blocking full-week preflight checks before downloading."
    elif raw.get("empty", True):
        value = "ready_for_full_week_download"
        next_action = f"Run `python scripts/print_data_transfer_download_command.py --topic {entry['topic']}` and execute the printed finkctl transfer command manually."
    elif raw.get("file_count", 0) > 0:
        value = "ready_for_full_week_raw_inspection"
        next_action = f"Run `python scripts/inspect_full_packet_delivery.py --topic {entry['topic']}`."
    else:
        value = "full_week_download_in_progress_or_partial"
        next_action = f"Run `python scripts/summarize_download_progress.py --raw-dir {entry['raw_delivery_dir']} --topic {entry['topic']}`."
    return {
        "decision": value,
        "blockers": blockers,
        "evidence": evidence,
        "next_action": next_action,
        "user_confirmation_required": value == "ready_for_full_week_download",
        "full_night_request_allowed": False,
        "full_week_download_allowed": value == "ready_for_full_week_download",
    }


def _abs(path: str, project_root: Path) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else project_root / candidate


if __name__ == "__main__":
    raise SystemExit(main())

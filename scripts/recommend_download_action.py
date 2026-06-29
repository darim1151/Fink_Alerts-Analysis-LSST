#!/usr/bin/env python
"""Recommend a safe next action from full-packet download triage."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from fink_lsst.bulk_transfer.topic_registry import find_topic_entry, load_topic_registry

try:
    from triage_full_packet_download import build_triage_report, resolve_raw_dir
except ImportError:  # pragma: no cover - used when imported from tests as a namespace package
    from scripts.triage_full_packet_download import build_triage_report, resolve_raw_dir


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--topic", required=True)
    parser.add_argument("--registry", default="configs/data_transfer_topics.yaml")
    parser.add_argument("--expected-total", type=int)
    args = parser.parse_args()
    registry = load_topic_registry(PROJECT_ROOT / args.registry)
    entry = find_topic_entry(registry, topic=args.topic)
    if not entry:
        raise SystemExit(f"Topic is not registered: {args.topic}")
    triage_path = output_base_for(entry) / "download_triage.json"
    if triage_path.exists():
        triage = json.loads(triage_path.read_text(encoding="utf-8"))
    else:
        triage = build_triage_report(resolve_raw_dir(None, entry), entry, args.expected_total, None)
    recommendation = recommend_action(triage)
    text = render_recommendation(recommendation, triage)
    output_base_for(entry).mkdir(parents=True, exist_ok=True)
    (output_base_for(entry) / "DOWNLOAD_RECOMMENDATION.md").write_text(text, encoding="utf-8")
    (output_base_for(entry) / "download_recommendation.json").write_text(json.dumps({"recommendation": recommendation, "triage": triage}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(text)
    return 0


def recommend_action(triage: dict[str, Any]) -> dict[str, Any]:
    state = triage.get("readiness", {}).get("state")
    raw_dir = triage.get("raw_dir")
    topic = triage.get("topic")
    if state == "raw_missing":
        value = "resume_same_topic_same_directory"
        command = f"python scripts/print_data_transfer_download_command.py --topic {topic}"
    elif state == "download_active":
        value = "wait_and_check_again"
        command = f"python scripts/triage_full_packet_download.py --topic {topic} --write-report"
    elif state == "blocked_disk_risk":
        value = "stop_due_to_disk_risk"
        command = "df -h ."
    elif state == "blocked_corrupt_raw":
        value = "inspect_partial_only"
        command = f"python scripts/inspect_full_packet_delivery.py --topic {topic}"
    elif state in {"partial_download", "partial_with_errors"}:
        value = "resume_same_topic_same_directory"
        command = f"python scripts/summarize_download_progress.py --raw-dir '{raw_dir}' --topic {topic}"
    elif state in {"ready_for_raw_schema_inspection", "appears_complete_unverified", "ready_for_ingestion"}:
        value = "ready_for_ingestion"
        command = f"python scripts/inspect_full_packet_delivery.py --topic {topic}"
    else:
        value = "wait_and_check_again"
        command = f"python scripts/triage_full_packet_download.py --topic {topic} --write-report"
    return {"action": value, "safe_next_command": command}


def render_recommendation(recommendation: dict[str, Any], triage: dict[str, Any]) -> str:
    readiness = triage.get("readiness", {})
    lines = [
        "# Download Action Recommendation",
        "",
        f"- Recommendation: `{recommendation['action']}`",
        f"- Raw state: `{readiness.get('state')}`",
        f"- Readable rows: `{readiness.get('readable_rows')}`",
        f"- Expected total: `{readiness.get('expected_total')}`",
        f"- Apparent percent complete: `{readiness.get('apparent_percent_complete')}`",
        f"- Remaining rows: `{readiness.get('remaining_rows')}`",
        f"- Unreadable/corrupt files: `{readiness.get('unreadable_parquet_count')}`",
        f"- Files active recently: `{triage.get('audit', {}).get('activity', {}).get('appears_active')}`",
        "",
        "## Safe Next Command",
        "",
        "```bash",
        recommendation["safe_next_command"],
        "```",
        "",
        "No command was executed by this recommendation script.",
        "",
    ]
    return "\n".join(lines)


def output_base_for(entry: dict[str, Any]) -> Path:
    return PROJECT_ROOT / "outputs/data_transfer/full_week_full_packet" / f"{entry['utc_start']}_to_{entry['utc_stop']}"


if __name__ == "__main__":
    raise SystemExit(main())

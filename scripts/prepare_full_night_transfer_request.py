#!/usr/bin/env python
"""Prepare the first full-night all-alert Data Transfer request draft."""

from __future__ import annotations

import argparse
from pathlib import Path

from fink_lsst.bulk_transfer.config import load_data_transfer_config
from fink_lsst.bulk_transfer.request_builder import (
    build_profiled_data_transfer_request,
    render_request_as_json,
    render_request_as_markdown,
    validate_data_transfer_request,
)
from fink_lsst.bulk_transfer.topic_registry import DEFAULT_TRANSFER_CONSUMERS, new_full_night_topic_template, render_download_command
from fink_lsst.storage import write_json


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/local_smoke_test.yaml")
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    config = load_data_transfer_config(project_root / args.config)
    output_dir = project_root / "outputs/data_transfer/request_drafts"
    output_dir.mkdir(parents=True, exist_ok=True)

    request = build_profiled_data_transfer_request(config, profile="full_night_all_alerts")
    request["validation_checks"] = validate_data_transfer_request(request)
    topic_template = new_full_night_topic_template(config)
    request["topic_registry_template"] = topic_template
    request["download_command_template"] = render_download_command(topic_template, project_root, DEFAULT_TRANSFER_CONSUMERS)

    (output_dir / "FULL_NIGHT_DATA_TRANSFER_REQUEST.json").write_text(render_request_as_json(request), encoding="utf-8")
    (output_dir / "FULL_NIGHT_DATA_TRANSFER_REQUEST.md").write_text(render_request_as_markdown(request), encoding="utf-8")
    (output_dir / "FULL_NIGHT_MANUAL_PORTAL_CHECKLIST.md").write_text(_render_full_night_checklist(request), encoding="utf-8")
    write_json(
        {
            "request_profile": "full_night_all_alerts",
            "request_file": str(output_dir / "FULL_NIGHT_DATA_TRANSFER_REQUEST.json"),
            "topic_registry_template": topic_template,
        },
        output_dir / "full_night_request_manifest.json",
        enforce_allowed_roots=False,
    )
    print(f"Wrote full-night request draft: {output_dir / 'FULL_NIGHT_DATA_TRANSFER_REQUEST.md'}")
    print(f"Wrote full-night checklist: {output_dir / 'FULL_NIGHT_MANUAL_PORTAL_CHECKLIST.md'}")
    print("Manual portal submission is still required; no job was submitted.")
    return 0


def _render_full_night_checklist(request: dict) -> str:
    lines = [
        "# Full-Night Data Transfer Manual Portal Checklist",
        "",
        "- Use this only after reviewing the smoke ingestion and deciding to request one full UTC night.",
        f"- Survey: `{request['survey']}`.",
        f"- UTC start date: `{request['target_startdate']}`.",
        f"- UTC stop date: `{request['target_stopdate']}`.",
        "- Filter: leave blank / no Fink filter / all alerts, if the portal supports it.",
        "- Content: `Light static packet`.",
        "- Do not request cutouts, FITS, or images.",
        "- Record the generated topic in `configs/data_transfer_topics.yaml` with `scope: full_night_all_alerts`.",
        "- Record Kafka lag zero after download if observed.",
        "- Save delivered files under `data/raw/data_transfer/full_night/<date-window>/<topic>/`.",
        "- Do not store credentials, tokens, passwords, or auth outputs in this repository.",
        "",
        "## After Topic Creation",
        "",
        "```bash",
        "python scripts/print_data_transfer_download_command.py --topic TOPIC --scope full_night_all_alerts",
        "python scripts/run_data_transfer_pipeline.py --scope full_night_all_alerts --topic TOPIC",
        "```",
        "",
        "## Topic Registry Template",
        "",
        "```yaml",
    ]
    for key, value in request["topic_registry_template"].items():
        rendered = "null" if value is None else f'"{value}"' if isinstance(value, str) else str(value).lower() if isinstance(value, bool) else value
        lines.append(f"{key}: {rendered}")
    lines.extend(["```", ""])
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())

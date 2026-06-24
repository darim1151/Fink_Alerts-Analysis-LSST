#!/usr/bin/env python
"""Prepare a dry-run Fink Data Transfer request draft."""

from __future__ import annotations

import argparse
from pathlib import Path

from fink_lsst.bulk_transfer.config import load_data_transfer_config
from fink_lsst.bulk_transfer.request_builder import (
    build_data_transfer_request,
    build_manual_portal_checklist,
    render_request_as_json,
    render_request_as_markdown,
    validate_data_transfer_request,
)
from fink_lsst.storage import write_json


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/local_smoke_test.yaml")
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args()
    project_root = Path(args.project_root).resolve()
    config = load_data_transfer_config(project_root / args.config)
    reports_dir = project_root / config.get("local_paths", {}).get("reports_dir", "outputs/data_transfer") / "request_drafts"
    reports_dir.mkdir(parents=True, exist_ok=True)
    request = build_data_transfer_request(config)
    checks = validate_data_transfer_request(request)
    request["validation_checks"] = checks
    (reports_dir / "data_transfer_request.json").write_text(render_request_as_json(request), encoding="utf-8")
    (reports_dir / "DATA_TRANSFER_REQUEST.md").write_text(render_request_as_markdown(request), encoding="utf-8")
    (reports_dir / "MANUAL_PORTAL_CHECKLIST.md").write_text(build_manual_portal_checklist(request), encoding="utf-8")
    write_json({"request_file": str(reports_dir / "data_transfer_request.json"), "checks": checks}, reports_dir / "request_validation.json", enforce_allowed_roots=False)
    print(f"Wrote request draft: {reports_dir / 'DATA_TRANSFER_REQUEST.md'}")
    print(f"Wrote manual checklist: {reports_dir / 'MANUAL_PORTAL_CHECKLIST.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

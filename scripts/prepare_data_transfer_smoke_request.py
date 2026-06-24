#!/usr/bin/env python
"""Prepare a tiny non-complete Data Transfer smoke request draft."""

from __future__ import annotations

import argparse
from pathlib import Path

from fink_lsst.bulk_transfer.config import load_data_transfer_config
from fink_lsst.bulk_transfer.request_builder import (
    build_profiled_data_transfer_request,
    build_smoke_manual_portal_checklist,
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
    output_dir = project_root / "outputs/data_transfer/request_drafts"
    output_dir.mkdir(parents=True, exist_ok=True)
    request = build_profiled_data_transfer_request(config, profile="smoke_delivery")
    request["validation_checks"] = validate_data_transfer_request(request)
    (output_dir / "SMOKE_DATA_TRANSFER_REQUEST.json").write_text(render_request_as_json(request), encoding="utf-8")
    (output_dir / "SMOKE_DATA_TRANSFER_REQUEST.md").write_text(render_request_as_markdown(request), encoding="utf-8")
    (output_dir / "SMOKE_MANUAL_PORTAL_CHECKLIST.md").write_text(build_smoke_manual_portal_checklist(request), encoding="utf-8")
    write_json({"request_profile": "smoke_delivery", "request_file": str(output_dir / "SMOKE_DATA_TRANSFER_REQUEST.json")}, output_dir / "smoke_request_manifest.json", enforce_allowed_roots=False)
    print(f"Wrote smoke request draft: {output_dir / 'SMOKE_DATA_TRANSFER_REQUEST.md'}")
    print(f"Wrote smoke checklist: {output_dir / 'SMOKE_MANUAL_PORTAL_CHECKLIST.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

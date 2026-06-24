#!/usr/bin/env python
"""Report the next safe Data Transfer action."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from fink_lsst.bulk_transfer.readiness_decision import decide_data_transfer_readiness, render_next_action
from fink_lsst.storage import write_json


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args()
    project_root = Path(args.project_root).resolve()
    output_dir = project_root / "outputs/data_transfer"
    setup = _read(output_dir / "setup_status.json")
    client_probe = _read(output_dir / "client_probe.json")
    smoke_request = _read(output_dir / "request_drafts/SMOKE_DATA_TRANSFER_REQUEST.json") or _read(output_dir / "request_drafts/data_transfer_request.json")
    delivery = _read(output_dir / "delivery_inspection.json")
    validation_files = sorted(output_dir.glob("**/*validation_report.json"))
    validation = _read(validation_files[-1]) if validation_files else None
    decision = decide_data_transfer_readiness(setup, client_probe, smoke_request, delivery, validation)
    write_json(decision, output_dir / "data_transfer_next_action.json", enforce_allowed_roots=False)
    text = render_next_action(decision)
    (output_dir / "DATA_TRANSFER_NEXT_ACTION.md").write_text(text, encoding="utf-8")
    print(text)
    return 0


def _read(path: Path | None):
    if not path or not path.exists():
        return None
    return json.loads(path.read_text())


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python
"""Write manual Fink Data Transfer registration/authentication checklist."""

from __future__ import annotations

import argparse
from pathlib import Path

from fink_lsst.bulk_transfer.registration import render_registration_checklist


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args()
    project_root = Path(args.project_root).resolve()
    output_dir = project_root / "outputs/data_transfer"
    output_dir.mkdir(parents=True, exist_ok=True)
    text = render_registration_checklist()
    (output_dir / "REGISTRATION_AUTH_CHECKLIST.md").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

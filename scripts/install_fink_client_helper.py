#!/usr/bin/env python
"""Write fink-client installation guidance without installing by default."""

from __future__ import annotations

import argparse
from pathlib import Path

from fink_lsst.bulk_transfer.install_helper import build_install_status, render_install_guide
from fink_lsst.storage import write_json


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--print-only", action="store_true", default=True)
    parser.add_argument("--check-after-install", action="store_true")
    args = parser.parse_args()
    project_root = Path(args.project_root).resolve()
    output_dir = project_root / "outputs/data_transfer"
    output_dir.mkdir(parents=True, exist_ok=True)
    status = build_install_status()
    status["print_only"] = bool(args.print_only)
    status["check_after_install"] = bool(args.check_after_install)
    write_json(status, output_dir / "fink_client_install_status.json", enforce_allowed_roots=False)
    guide = render_install_guide(status)
    (output_dir / "FINK_CLIENT_INSTALL_GUIDE.md").write_text(guide, encoding="utf-8")
    print(guide)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

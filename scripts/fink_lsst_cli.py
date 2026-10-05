#!/usr/bin/env python
"""Run the `fink-lsst` acquisition CLI from a checkout.

Same as the installed `fink-lsst` entry point, for example:

    python scripts/fink_lsst_cli.py acquire --start 2026-02-25 --stop 2026-03-25

See docs/RANGE_ACQUISITION_ORCHESTRATOR.md. Live Fink submission is disabled
in this release.
"""

from __future__ import annotations

from fink_lsst.acquisition.cli import main


if __name__ == "__main__":
    raise SystemExit(main())

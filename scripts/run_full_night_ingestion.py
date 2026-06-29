#!/usr/bin/env python
"""Run the Data Transfer pipeline for a recorded full-night all-alert topic."""

from __future__ import annotations

import sys

from run_data_transfer_pipeline import main


if __name__ == "__main__":
    sys.argv = [sys.argv[0], "--scope", "full_night_all_alerts", *sys.argv[1:]]
    raise SystemExit(main())

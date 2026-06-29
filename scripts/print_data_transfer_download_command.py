#!/usr/bin/env python
"""Print the fink_datatransfer command for a recorded non-secret topic."""

from __future__ import annotations

import argparse
from pathlib import Path

from fink_lsst.bulk_transfer.topic_registry import (
    build_topic_entry,
    find_topic_entry,
    load_topic_registry,
    render_download_command,
    render_download_instructions,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", default="configs/data_transfer_topics.yaml")
    parser.add_argument("--topic")
    parser.add_argument("--scope")
    parser.add_argument("--survey", default="lsst")
    parser.add_argument("--startdate")
    parser.add_argument("--stopdate")
    parser.add_argument("--content", default="Full packet")
    parser.add_argument("--all-alert", action="store_true", default=True)
    parser.add_argument("--latest", action="store_true", help="Use the latest matching topic when --topic is omitted.")
    parser.add_argument("--outdir", help="Override output directory in the printed command.")
    parser.add_argument("--instructions", action="store_true", help="Print safety instructions with the command.")
    args = parser.parse_args()

    registry = load_topic_registry(args.registry)
    entry = find_topic_entry(registry, topic=args.topic, scope=args.scope, latest=args.latest or bool(args.scope))
    if entry is None and args.topic and args.scope and args.startdate and args.stopdate:
        entry = build_topic_entry(
            scope=args.scope,
            survey=args.survey,
            topic=args.topic,
            startdate=args.startdate,
            stopdate=args.stopdate,
            content=args.content,
            all_alert=args.all_alert,
            filters=[],
            notes="Ad hoc command generation; topic not found in registry.",
        )
    if entry is None:
        raise SystemExit("No matching topic found in the registry. Add the portal topic first.")
    outdir = Path(args.outdir) if args.outdir else None
    if args.instructions or entry.get("scope") == "full_week_full_packet":
        print(render_download_instructions(entry, outdir=outdir))
    else:
        print(render_download_command(entry, outdir=outdir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

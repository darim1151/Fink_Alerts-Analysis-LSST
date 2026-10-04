#!/usr/bin/env python
"""Print the `finkctl transfer` command for a recorded non-secret topic or run manifest.

The command is printed, never executed. Its output directory is absolute and
confined to `<data root>/data/raw/data_transfer`; set FINK_LSST_DATA_ROOT to
target an external data root.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from fink_lsst.bulk_transfer.run_manifest import load_run_manifest, topic_entry_from_manifest
from fink_lsst.bulk_transfer.topic_registry import (
    DEFAULT_TRANSFER_CONSUMERS,
    build_topic_entry,
    find_topic_entry,
    load_topic_registry,
    render_download_command,
    render_download_instructions,
)
from fink_lsst.data_root import DataRootError, resolve_data_root


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", default="configs/data_transfer_topics.yaml")
    parser.add_argument("--run-config", help="Run manifest whose topic and raw_dir define the command.")
    parser.add_argument("--topic")
    parser.add_argument("--scope")
    parser.add_argument("--survey", default="lsst")
    parser.add_argument("--startdate")
    parser.add_argument("--stopdate")
    parser.add_argument("--content", default="Light static packet")
    parser.add_argument("--all-alert", action="store_true", default=True)
    parser.add_argument("--latest", action="store_true", help="Use the latest matching topic when --topic is omitted.")
    parser.add_argument("--outdir", help="Override output directory; must stay under data/raw/data_transfer of the data root.")
    parser.add_argument("--nconsumers", type=int, default=DEFAULT_TRANSFER_CONSUMERS, help="Explicit Kafka consumer count (1-32).")
    parser.add_argument("--instructions", action="store_true", help="Print safety instructions with the command.")
    args = parser.parse_args()

    try:
        data_root = resolve_data_root(repo_root=PROJECT_ROOT)
    except DataRootError as exc:
        raise SystemExit(f"error: {exc}")

    if args.run_config:
        run_config = Path(args.run_config)
        entry = topic_entry_from_manifest(load_run_manifest(run_config if run_config.is_absolute() else PROJECT_ROOT / run_config))
    else:
        registry_path = Path(args.registry)
        registry = load_topic_registry(registry_path if registry_path.is_absolute() else PROJECT_ROOT / registry_path)
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
    try:
        if args.instructions:
            print(render_download_instructions(entry, data_root, args.nconsumers, outdir=args.outdir))
        else:
            print(render_download_command(entry, data_root, args.nconsumers, outdir=args.outdir))
    except ValueError as exc:
        raise SystemExit(f"error: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

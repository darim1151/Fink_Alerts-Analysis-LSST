#!/usr/bin/env python
"""Register a non-secret Fink Data Transfer topic in the local registry."""

from __future__ import annotations

import argparse
from pathlib import Path

from fink_lsst.bulk_transfer.run_manifest import load_run_manifest, topic_entry_from_manifest
from fink_lsst.bulk_transfer.topic_registry import (
    build_topic_entry,
    load_topic_registry,
    register_topic_entry,
    save_topic_registry,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", default="configs/data_transfer_topics.yaml")
    parser.add_argument("--run-config")
    parser.add_argument("--scope")
    parser.add_argument("--survey")
    parser.add_argument("--topic")
    parser.add_argument("--startdate")
    parser.add_argument("--stopdate")
    parser.add_argument("--content")
    parser.add_argument("--all-alert", action="store_true")
    parser.add_argument("--filter", action="append", default=[], dest="filters")
    parser.add_argument("--notes", default="")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    registry_path = Path(args.registry)
    registry = load_topic_registry(registry_path)
    if args.run_config:
        entry = topic_entry_from_manifest(load_run_manifest(_abs(args.run_config)))
    else:
        missing = [name for name in ("scope", "survey", "topic", "startdate", "stopdate", "content") if getattr(args, name) is None]
        if missing:
            parser.error("missing required arguments without --run-config: " + ", ".join(f"--{name}" for name in missing))
        entry = build_topic_entry(
            scope=args.scope,
            survey=args.survey,
            topic=args.topic,
            startdate=args.startdate,
            stopdate=args.stopdate,
            content=args.content,
            all_alert=args.all_alert,
            filters=args.filters,
            notes=args.notes,
        )
    registry = register_topic_entry(registry, entry, force=args.force)
    save_topic_registry(registry, registry_path)
    print(f"Registered topic: {entry['topic']}")
    print(f"Scope: {entry['scope']}")
    print(f"Raw path: {entry['raw_delivery_dir']}")
    print("No credentials were stored.")
    return 0


def _abs(path: str) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else Path.cwd() / candidate


if __name__ == "__main__":
    raise SystemExit(main())

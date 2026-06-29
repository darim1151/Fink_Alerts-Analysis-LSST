#!/usr/bin/env python
"""Guarded entrypoint for future full-week full-packet ingestion."""

from __future__ import annotations

import argparse
from pathlib import Path

from fink_lsst.bulk_transfer.raw_audit import build_raw_audit
from fink_lsst.bulk_transfer.raw_readiness import classify_raw_readiness, partial_ingestion_blocked
from fink_lsst.bulk_transfer.topic_registry import find_topic_entry, load_topic_registry


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--topic", required=True)
    parser.add_argument("--registry", default="configs/data_transfer_topics.yaml")
    parser.add_argument("--expected-total", type=int)
    parser.add_argument("--allow-partial", action="store_true")
    args = parser.parse_args()
    registry = load_topic_registry(PROJECT_ROOT / args.registry)
    entry = find_topic_entry(registry, topic=args.topic)
    if not entry:
        raise SystemExit(f"Topic is not registered: {args.topic}")
    raw_dir = PROJECT_ROOT / entry["raw_delivery_dir"]
    audit = build_raw_audit(raw_dir)
    readiness = classify_raw_readiness(audit, expected_total=args.expected_total)
    if partial_ingestion_blocked(readiness["state"]) and not args.allow_partial:
        print(f"Refusing ingestion: raw state is `{readiness['state']}`.")
        print("Use --allow-partial only for explicitly labeled partial test ingestion.")
        return 2
    output_scope = "partial_full_week_full_packet" if args.allow_partial else "full_week_full_packet"
    print(f"Raw readiness: {readiness['state']}")
    print(f"Output scope: {output_scope}")
    print("Full-packet ingestion is not implemented in Checkpoint 8B; this guardrail exits before processing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

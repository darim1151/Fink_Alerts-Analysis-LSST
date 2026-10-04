#!/usr/bin/env python
"""Validate one or all adaptive run manifests."""

from __future__ import annotations

import argparse
from pathlib import Path

from fink_lsst.bulk_transfer.run_index import list_runs, load_run_index
from fink_lsst.bulk_transfer.run_manifest import derive_default_paths, load_run_manifest, validate_run_manifest
from fink_lsst.data_root import DataRootError, resolve_data_root


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-config")
    parser.add_argument("--all", action="store_true", dest="validate_all")
    parser.add_argument("--index", default="configs/runs/index.yaml")
    args = parser.parse_args()
    if not args.validate_all and not args.run_config:
        parser.error("provide --run-config or --all")
    try:
        data_root = resolve_data_root(repo_root=PROJECT_ROOT)
    except DataRootError as exc:
        print(f"error: {exc}")
        return 2
    print(f"data_root: {data_root}")
    print()

    paths = []
    if args.validate_all:
        index = load_run_index(PROJECT_ROOT / args.index)
        paths.extend(run["config"] for run in list_runs(index))
    else:
        paths.append(args.run_config)

    had_errors = False
    for item in paths:
        path = Path(item)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        errors, warnings = validate_one(path, data_root)
        had_errors = had_errors or bool(errors)
        print()
    return 1 if had_errors else 0


def validate_one(path: Path, data_root: Path) -> tuple[list[str], list[str]]:
    try:
        manifest = load_run_manifest(path)
    except Exception as exc:
        print(f"manifest: {path}")
        print("status: invalid")
        print(f"error: {exc}")
        return [str(exc)], []
    errors, warnings = validate_run_manifest(manifest, project_root=data_root)
    derived_paths = derive_default_paths(manifest, project_root=PROJECT_ROOT)
    print(f"manifest: {path.relative_to(PROJECT_ROOT)}")
    print(f"status: {'invalid' if errors else 'valid'}")
    print(f"run_name: {manifest.run_name}")
    print(f"scope: {manifest.scope}")
    print(f"date_window: {manifest.startdate} to {manifest.stopdate}")
    print(f"packet_type: {manifest.packet_type}")
    print(f"lifecycle_state: {manifest.lifecycle_state}")
    print(f"claim_state: {manifest.claim_state.all_alert_completeness}/{manifest.claim_state.night_completeness}/{manifest.claim_state.week_completeness}")
    print(f"expected_nights: {', '.join(manifest.expected_nights)}")
    print("configured_paths:")
    print(f"  raw_dir: {manifest.paths.raw_dir}")
    print(f"  processed_dir: {manifest.paths.processed_dir}")
    print(f"  outputs_dir: {manifest.paths.outputs_dir}")
    print("derived_default_paths:")
    print(f"  raw_dir: {derived_paths.raw_dir}")
    print(f"  processed_dir: {derived_paths.processed_dir}")
    print(f"  outputs_dir: {derived_paths.outputs_dir}")
    for warning in warnings:
        print(f"warning: {warning}")
    for error in errors:
        print(f"error: {error}")
    return errors, warnings


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Export Fink native handoff; the shared Observatory Bundle V1 remains unbound."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fink_lsst.data_root import (
    DATA_ROOT_ENV,
    REPO_ROOT,
    confine_tree,
    resolve_data_root,
    storage_base,
    validate_path_component,
)
from fink_lsst.observatory import (
    build_from_catalog,
    canonical_bytes,
    metadata_fixture,
    native_artifact,
)
from fink_lsst.observatory.qualification import file_sha256


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    fixture = sub.add_parser(
        "metadata-fixture", help="committed evidence only; emits no scientific rows"
    )
    fixture.add_argument(
        "--cohort",
        type=Path,
        default=REPO_ROOT / "configs/analysis_cohorts/month1.json",
    )
    science = sub.add_parser(
        "science", help="read a completed characterized external-view catalog"
    )
    science.add_argument("--data-root", type=Path, required=True)
    science.add_argument("--catalog", type=Path, required=True)
    science.add_argument("--cohort", type=Path, required=True)
    science.add_argument("--export-run-id", required=True)
    science.add_argument("--utc-dates", action="store_true")
    science.add_argument("--sky-order", type=int, default=3)
    science.add_argument("--sample-per-population", type=int, default=12)
    args = parser.parse_args(argv)
    if args.mode == "metadata-fixture":
        sys.stdout.buffer.write(
            canonical_bytes(native_artifact(metadata_fixture(cohort_path=args.cohort)))
        )
        return 0
    root = resolve_data_root({DATA_ROOT_ENV: str(args.data_root)})
    run_id = validate_path_component(args.export_run_id, "export run_id")
    output = confine_tree(
        storage_base(root, "outputs") / run_id, storage_base(root, "outputs")
    )
    if any(
        (storage_base(root, base) / run_id).exists()
        for base in ("outputs", "manifests", "data/processed")
    ):
        raise FileExistsError(
            "export run identity already exists; never overwrite evidence"
        )
    model = build_from_catalog(
        args.catalog,
        root,
        args.cohort,
        include_utc=args.utc_dates,
        sky_order=args.sky_order,
        sample_per_population=args.sample_per_population,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir()  # Exclusive reservation after qualification, confined to new derived output.
    artifact = output / "fink_observatory_native.json"
    with artifact.open("xb") as handle:
        handle.write(canonical_bytes(native_artifact(model)))
    with (output / "sha256.json").open("xb") as handle:
        handle.write(canonical_bytes({artifact.name: file_sha256(artifact)}))
    print(str(artifact))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

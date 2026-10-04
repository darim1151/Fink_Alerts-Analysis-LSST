#!/usr/bin/env python
"""Read-only preflight for the Arnor production deployment.

Proves that FINK_LSST_DATA_ROOT is set to exactly the production root, that
the storage directories are real (not redirected) and writable, and that
`finkctl` is installed. With --run-config it also prints the resolved raw,
processed-run, and report locations plus the generated `finkctl transfer`
command. It writes nothing and never runs finkctl or touches credentials.

This is deployment policy; the library itself accepts any safe data root.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from fink_lsst.bulk_transfer.run_ingestion import derive_run_dirs
from fink_lsst.bulk_transfer.run_manifest import load_run_manifest, resolve_run_paths, topic_entry_from_manifest, validate_run_manifest
from fink_lsst.bulk_transfer.topic_registry import DEFAULT_TRANSFER_CONSUMERS, build_download_command
from fink_lsst.data_root import DATA_ROOT_ENV, confine, resolve_data_root, storage_base


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_DATA_ROOT = "/astro/store/shire/FINK"
REQUIRED_DIRS = ("data/raw", "data/processed", "outputs", "manifests", "logs")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-root", default=PRODUCTION_DATA_ROOT, help=argparse.SUPPRESS)
    parser.add_argument("--run-config")
    parser.add_argument("--nconsumers", type=int, default=DEFAULT_TRANSFER_CONSUMERS)
    args = parser.parse_args(argv)

    report: dict = {"checks": [], "expected_root": args.expected_root}

    def check(name: str, ok: bool, detail: str = "") -> bool:
        report["checks"].append({"name": name, "ok": bool(ok), "detail": detail})
        return bool(ok)

    data_root = None
    if check(f"{DATA_ROOT_ENV}_set", DATA_ROOT_ENV in os.environ, os.environ.get(DATA_ROOT_ENV, "unset")):
        try:
            data_root = resolve_data_root(repo_root=PROJECT_ROOT)
            check("data_root_valid", True, str(data_root))
        except ValueError as exc:
            check("data_root_valid", False, str(exc))
    if data_root is not None and check("data_root_is_production_root", data_root == Path(args.expected_root).resolve(), str(data_root)):
        for relative in REQUIRED_DIRS:
            base = storage_base(data_root, relative)
            try:
                confine(base, base)
                ok = base.is_dir() and os.access(base, os.W_OK | os.X_OK)
                check(f"dir:{relative}", ok, str(base) if ok else f"{base} missing or not writable")
            except ValueError as exc:
                check(f"dir:{relative}", False, str(exc))
    finkctl = shutil.which("finkctl") or _beside_python("finkctl")
    check("finkctl_installed", finkctl is not None, str(finkctl))

    if args.run_config and data_root is not None and all(item["ok"] for item in report["checks"]):
        run_config = Path(args.run_config)
        manifest = load_run_manifest(run_config if run_config.is_absolute() else PROJECT_ROOT / run_config)
        errors, _warnings = validate_run_manifest(manifest, project_root=data_root)
        if check("manifest_valid", not errors, "; ".join(errors)):
            try:
                paths = resolve_run_paths(manifest, data_root)
                processed_run, output_run = derive_run_dirs(manifest.run_name, manifest.run_id or "timestamp", data_root)
                report["resolved"] = {
                    "raw_delivery": str(paths["raw_dir"]),
                    "processed_run": str(processed_run),
                    "run_outputs": str(output_run),
                    "progress_report": str(paths["outputs_dir"] / "download_progress.json"),
                    "transfer_log": str(storage_base(data_root, "logs") / f"{manifest.topic}.transfer.log"),
                }
                report["transfer_command"] = build_download_command(topic_entry_from_manifest(manifest), data_root, args.nconsumers)
                check("paths_confined", True)
            except ValueError as exc:
                check("paths_confined", False, str(exc))

    report["ok"] = all(item["ok"] for item in report["checks"])
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


def _beside_python(name: str) -> str | None:
    candidate = Path(sys.executable).parent / name
    return str(candidate) if candidate.exists() else None


if __name__ == "__main__":
    raise SystemExit(main())

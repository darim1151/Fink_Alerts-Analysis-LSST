#!/usr/bin/env python
"""Dry-run or remove only safe local cache files."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAFE_CACHE_NAMES = {"__pycache__", ".pytest_cache", ".ipynb_checkpoints", ".DS_Store"}
SAFE_SUFFIXES = {".pyc", ".pyo"}
DENY_ROOTS = [
    "data/raw",
    "data/processed",
    "outputs/data_transfer",
    "notebooks",
    "configs",
    "docs",
    "src",
    "tests",
    "data/raw/data_transfer/smoke_delivery/ftransfer_lsst_2026-06-24_657339",
    "data/processed/data_transfer/smoke_delivery/20260627T210030Z",
    "outputs/data_transfer/smoke_delivery/20260627T210030Z",
    "outputs/data_transfer/smoke_delivery/20260627T211648Z",
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="List cache paths without removing them. This is the default.")
    mode.add_argument("--apply", action="store_true", help="Remove only unambiguous safe cache paths.")
    args = parser.parse_args()
    apply = bool(args.apply)
    candidates = discover_cache_candidates()
    removable, skipped = partition_candidates(candidates)
    print("Safe cache cleanup")
    print(f"Mode: {'apply' if apply else 'dry-run'}")
    print(f"Removable candidates: {len(removable)}")
    for path in removable:
        print(f"{'REMOVE' if apply else 'WOULD REMOVE'} {relpath(path)}")
        if apply:
            remove_path(path)
    print(f"Skipped candidates: {len(skipped)}")
    for path, reason in skipped:
        print(f"SKIP {relpath(path)} - {reason}")
    return 0


def discover_cache_candidates() -> list[Path]:
    candidates: list[Path] = []
    for path in PROJECT_ROOT.rglob("*"):
        rel_parts = path.relative_to(PROJECT_ROOT).parts
        if ".git" in rel_parts or ".venv" in rel_parts:
            continue
        if path.name in SAFE_CACHE_NAMES or path.suffix in SAFE_SUFFIXES:
            candidates.append(path)
    return sorted(candidates, key=lambda item: item.as_posix())


def partition_candidates(candidates: list[Path]) -> tuple[list[Path], list[tuple[Path, str]]]:
    removable = []
    skipped = []
    for path in candidates:
        if not is_safe_cache_path(path):
            skipped.append((path, "not an allowed cache filename or suffix"))
            continue
        if is_protected_path(path):
            skipped.append((path, "protected scientific/source path"))
            continue
        removable.append(path)
    return removable, skipped


def is_safe_cache_path(path: Path) -> bool:
    return path.name in SAFE_CACHE_NAMES or path.suffix in SAFE_SUFFIXES


def is_protected_path(path: Path) -> bool:
    resolved = path.resolve()
    for item in DENY_ROOTS:
        deny = (PROJECT_ROOT / item).resolve()
        if resolved == deny:
            return True
        if deny in resolved.parents:
            # Allow explicit cache paths under source/config/docs/notebook/test trees,
            # but never remove protected data/output roots or their descendants.
            if item in {"configs", "docs", "notebooks", "src", "tests"} and path.name in SAFE_CACHE_NAMES:
                return False
            return True
    return False


def remove_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def relpath(path: Path) -> str:
    return path.resolve().relative_to(PROJECT_ROOT).as_posix()


if __name__ == "__main__":
    raise SystemExit(main())

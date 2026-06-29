#!/usr/bin/env python
"""Audit whether the repository is ready for commit review."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SECRET_MARKERS = ("credential", "credentials", "secret", "token", "password", "passwd", "api_key", "apikey", ".env")
PAYLOAD_SUFFIXES = (".parquet", ".avro", ".fits", ".fit", ".fz")
CUTOUT_MARKERS = ("cutout", "stamp", "fits")
SAFE_SOURCE_PREFIXES = ("src/", "scripts/", "tests/", "docs/", "configs/", "README.md", "pyproject.toml", "setup.cfg", "setup.py", "requirements.txt")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=str(PROJECT_ROOT))
    args = parser.parse_args()
    root = Path(args.project_root).resolve()
    report = build_commit_readiness_report(root)
    output_dir = root / "outputs/maintenance"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "commit_readiness.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output_dir / "COMMIT_READINESS.md").write_text(render_commit_readiness(report), encoding="utf-8")
    print(render_commit_readiness(report))
    return 0 if report["decision"] == "ready_for_commit_review" else 2


def build_commit_readiness_report(root: Path) -> dict[str, Any]:
    status_lines = _git(root, ["status", "--short", "-uall"]).splitlines()
    visible = [_parse_status_path(line) for line in status_lines if _parse_status_path(line)]
    classified = classify_visible_paths(visible)
    must_not = classified["must_not_commit_files"]
    credentials = classified["credential_like_files"]
    candidates = classified["safe_to_commit_candidates"]
    ignored_summary = _ignored_summary(root)
    warnings = []
    tests_marker = root / ".pytest_cache"
    if not tests_marker.exists():
        warnings.append("pytest cache marker not found; run tests before commit review")
    manifest_check = _run([sys.executable, "scripts/validate_run_manifest.py", "--all"], root)
    if manifest_check.returncode != 0:
        warnings.append("run manifest validation failed")
    docs_required = [
        "docs/ADAPTIVE_RUN_MANIFESTS.md",
        "docs/MANIFEST_DRIVEN_INGESTION.md",
        "docs/IMPORTANT_RUNS.md",
        "outputs/maintenance/IMPORTANT_RUNS.md",
    ]
    missing_docs = [path for path in docs_required if not (root / path).exists()]
    if missing_docs:
        warnings.append("missing required docs: " + ", ".join(missing_docs))
    ignore_checks = {
        "data/raw": _git_check_ignore(root, "data/raw/data_transfer/check/part.parquet"),
        "data/processed": _git_check_ignore(root, "data/processed/data_transfer/check/alerts.parquet"),
        "outputs": _git_check_ignore(root, "outputs/data_transfer/check/report.json"),
    }
    for name, passed in ignore_checks.items():
        if not passed:
            warnings.append(f".gitignore does not protect {name}")
    blockers = must_not + credentials
    decision = "blocked_commit_review" if blockers or not all(ignore_checks.values()) or manifest_check.returncode != 0 else "ready_for_commit_review"
    return {
        "decision": decision,
        "safe_to_commit_candidates": sorted(candidates),
        "must_not_commit_files": sorted(set(blockers)),
        "ignored_local_artifacts_summary": ignored_summary,
        "warnings": warnings,
        "ignore_checks": ignore_checks,
        "manifest_validation_returncode": manifest_check.returncode,
        "git_status_short": status_lines,
    }


def classify_visible_paths(paths: list[str]) -> dict[str, list[str]]:
    """Classify visible git-status paths into commit candidates and blockers."""
    must_not = [path for path in paths if _must_not_commit(path)]
    credentials = [path for path in paths if _credential_like(path)]
    candidates = [path for path in paths if path not in must_not and path not in credentials]
    return {
        "safe_to_commit_candidates": sorted(candidates),
        "must_not_commit_files": sorted(must_not),
        "credential_like_files": sorted(credentials),
    }


def render_commit_readiness(report: dict[str, Any]) -> str:
    lines = [
        "# Commit Readiness",
        "",
        f"- Decision: `{report['decision']}`",
        "",
        "## Safe-To-Commit Candidates",
        "",
    ]
    for path in report["safe_to_commit_candidates"]:
        lines.append(f"- `{path}`")
    if not report["safe_to_commit_candidates"]:
        lines.append("- None.")
    lines.extend(["", "## Must-Not-Commit Files", ""])
    for path in report["must_not_commit_files"]:
        lines.append(f"- `{path}`")
    if not report["must_not_commit_files"]:
        lines.append("- None detected.")
    lines.extend(["", "## Ignored Local Artifacts", ""])
    for key, value in report["ignored_local_artifacts_summary"].items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Warnings", ""])
    for warning in report["warnings"]:
        lines.append(f"- {warning}")
    if not report["warnings"]:
        lines.append("- None.")
    return "\n".join(lines) + "\n"


def _parse_status_path(line: str) -> str:
    path = line[3:].strip()
    if " -> " in path:
        path = path.split(" -> ", 1)[1]
    return path


def _must_not_commit(path: str) -> bool:
    lowered = path.lower()
    if path.startswith("data/raw/") and not path.endswith(".gitkeep"):
        return True
    if path.startswith("data/processed/") and not path.endswith(".gitkeep"):
        return True
    if any(lowered.endswith(suffix) for suffix in PAYLOAD_SUFFIXES):
        return True
    if any(marker in lowered for marker in CUTOUT_MARKERS) and path.startswith(("data/", "outputs/")):
        return True
    if path.startswith("outputs/") and not path.startswith("outputs/maintenance/"):
        return True
    return False


def _credential_like(path: str) -> bool:
    lowered = Path(path).name.lower()
    return any(marker in lowered for marker in SECRET_MARKERS)


def _ignored_summary(root: Path) -> dict[str, int]:
    lines = _git(root, ["status", "--ignored", "--short", "-uall"]).splitlines()
    ignored = [line[3:].strip() for line in lines if line.startswith("!! ")]
    return {
        "ignored_count": len(ignored),
        "ignored_data_raw": sum(path.startswith("data/raw/") for path in ignored),
        "ignored_data_processed": sum(path.startswith("data/processed/") for path in ignored),
        "ignored_outputs": sum(path.startswith("outputs/") for path in ignored),
    }


def _git_check_ignore(root: Path, path: str) -> bool:
    return _run(["git", "check-ignore", "-q", path], root).returncode == 0


def _git(root: Path, args: list[str]) -> str:
    return _run(["git", *args], root).stdout


def _run(args: list[str], root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=root, text=True, capture_output=True, check=False)


if __name__ == "__main__":
    raise SystemExit(main())

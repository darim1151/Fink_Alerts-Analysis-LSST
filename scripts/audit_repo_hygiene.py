#!/usr/bin/env python
"""Audit repository hygiene without deleting or moving files."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAINTENANCE_DIR = PROJECT_ROOT / "outputs" / "maintenance"
LARGE_FILE_BYTES = 5 * 1024 * 1024
CACHE_NAMES = {"__pycache__", ".pytest_cache", ".ipynb_checkpoints", ".DS_Store"}
NEVER_TOUCH = {
    "data/raw/data_transfer/smoke_delivery/ftransfer_lsst_2026-06-24_657339",
    "data/processed/data_transfer/smoke_delivery/20260627T210030Z",
    "outputs/data_transfer/smoke_delivery/20260627T210030Z",
    "outputs/data_transfer/smoke_delivery/20260627T211648Z",
    "configs/data_transfer_topics.yaml",
}
CREDENTIAL_MARKERS = ("credential", "credentials", "secret", "token", "password", ".env", ".pem", ".key")


def main() -> int:
    MAINTENANCE_DIR.mkdir(parents=True, exist_ok=True)
    inventory = build_inventory()
    write_json(inventory, MAINTENANCE_DIR / "repo_cleanup_inventory.json")
    (MAINTENANCE_DIR / "REPO_CLEANUP_AUDIT.md").write_text(render_markdown(inventory), encoding="utf-8")
    print(render_console_summary(inventory))
    return 0


def build_inventory() -> dict[str, Any]:
    status_short = run_git(["status", "--short"])
    status_ignored = run_git(["status", "--ignored", "--short"])
    all_paths = list(iter_repo_paths())
    categories = {
        "keep_tracked": [],
        "keep_local_ignored": [],
        "candidate_archive": [],
        "safe_cache_cleanup": [],
        "needs_user_decision": [],
    }
    for path in all_paths:
        rel = relpath(path)
        category = classify_path(path, rel)
        categories[category].append(rel)
    large_files = large_file_candidates(all_paths)
    suspicious = suspicious_credential_like(all_paths)
    sizes = directory_sizes([".", "data", "data/raw", "data/processed", "outputs"])
    recommendations = build_recommendations(categories, large_files, suspicious)
    return {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "project_root": str(PROJECT_ROOT),
        "git": {
            "branch": run_git(["branch", "--show-current"]).strip(),
            "head": run_git(["rev-parse", "HEAD"]).strip(),
            "status_short": status_short.splitlines(),
            "status_ignored_short": status_ignored.splitlines(),
        },
        "directory_sizes": sizes,
        "large_file_candidates": large_files,
        "suspicious_credential_like_files": suspicious,
        "never_touch_paths": sorted(NEVER_TOUCH),
        "categories": {key: sorted(values) for key, values in categories.items()},
        "recommendations": recommendations,
    }


def classify_path(path: Path, rel: str) -> str:
    name = path.name
    parts = set(Path(rel).parts)
    if name in CACHE_NAMES or parts.intersection(CACHE_NAMES) or rel.endswith(".pyc") or rel.endswith(".pyo"):
        return "safe_cache_cleanup"
    if rel.endswith("/.gitkeep") or name == ".gitkeep":
        return "keep_tracked"
    if rel.startswith(("src/", "scripts/", "tests/", "docs/", "notebooks/", "configs/")):
        return "keep_tracked"
    if rel in {"README.md", "requirements.txt", "setup.py", "setup.cfg", "pyproject.toml", ".gitignore"}:
        return "keep_tracked"
    if rel.startswith("data/fixtures/") or rel == "data/.gitkeep":
        return "keep_tracked"
    if rel.startswith("outputs/maintenance/"):
        return "keep_tracked"
    if rel.startswith(("data/raw/", "data/processed/")):
        return "keep_local_ignored"
    if rel.startswith("outputs/archive/"):
        return "keep_local_ignored"
    if rel.startswith("outputs/"):
        if any(part.startswith("202") for part in Path(rel).parts) or "request_drafts" in rel:
            return "candidate_archive"
        return "keep_local_ignored"
    if rel.startswith(("arrow_schema_", "avro_schema_")):
        return "keep_local_ignored"
    if any(marker in name.lower() for marker in CREDENTIAL_MARKERS):
        return "needs_user_decision"
    return "needs_user_decision"


def iter_repo_paths():
    skip_dirs = {".git", ".venv"}
    for root, dirs, files in os.walk(PROJECT_ROOT):
        dirs[:] = [item for item in dirs if item not in skip_dirs]
        root_path = Path(root)
        for dirname in dirs:
            yield root_path / dirname
        for filename in files:
            yield root_path / filename


def large_file_candidates(paths: list[Path]) -> list[dict[str, Any]]:
    candidates = []
    for path in paths:
        if not path.is_file():
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size >= LARGE_FILE_BYTES:
            candidates.append({"path": relpath(path), "bytes": int(size), "mb": round(size / (1024 * 1024), 2)})
    return sorted(candidates, key=lambda item: (-item["bytes"], item["path"]))


def suspicious_credential_like(paths: list[Path]) -> list[str]:
    suspicious = []
    for path in paths:
        rel = relpath(path)
        lowered = path.name.lower()
        if any(marker in lowered for marker in CREDENTIAL_MARKERS):
            suspicious.append(rel)
    return sorted(suspicious)


def directory_sizes(paths: list[str]) -> dict[str, str]:
    sizes = {}
    for item in paths:
        path = PROJECT_ROOT / item
        if not path.exists():
            sizes[item] = "missing"
            continue
        output = run(["du", "-sh", str(path)])
        sizes[item] = output.split()[0] if output.strip() else "unknown"
    return sizes


def build_recommendations(categories: dict[str, list[str]], large_files: list[dict[str, Any]], suspicious: list[str]) -> list[str]:
    recommendations = [
        "Keep raw and processed scientific data local and ignored.",
        "Commit source, docs, tests, notebooks, configs, fixtures, and maintenance summaries only.",
        "Review candidate_archive entries before moving anything to outputs/archive/.",
        "Run scripts/clean_safe_caches.py --dry-run before any cache deletion.",
    ]
    if large_files:
        recommendations.append("Review large_file_candidates and keep them ignored unless they are deliberate lightweight fixtures.")
    if suspicious:
        recommendations.append("Review suspicious_credential_like_files and ensure no secrets are committed.")
    if categories.get("needs_user_decision"):
        recommendations.append("Resolve needs_user_decision entries manually; do not delete them automatically.")
    return recommendations


def render_markdown(inventory: dict[str, Any]) -> str:
    lines = [
        "# Repository Cleanup Audit",
        "",
        f"- Created at UTC: `{inventory['created_at_utc']}`",
        f"- Branch: `{inventory['git']['branch']}`",
        f"- HEAD: `{inventory['git']['head']}`",
        "",
        "## Directory Sizes",
        "",
    ]
    for key, value in inventory["directory_sizes"].items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Classification Counts", ""])
    for key, values in inventory["categories"].items():
        lines.append(f"- `{key}`: `{len(values)}`")
    lines.extend(["", "## Large File Candidates", ""])
    for item in inventory["large_file_candidates"][:50]:
        lines.append(f"- `{item['path']}`: `{item['mb']} MB`")
    if not inventory["large_file_candidates"]:
        lines.append("- None above threshold.")
    lines.extend(["", "## Suspicious Credential-Like Files", ""])
    for item in inventory["suspicious_credential_like_files"]:
        lines.append(f"- `{item}`")
    if not inventory["suspicious_credential_like_files"]:
        lines.append("- None found by filename pattern.")
    lines.extend(["", "## Never-Touch Paths", ""])
    for item in inventory["never_touch_paths"]:
        lines.append(f"- `{item}`")
    lines.extend(["", "## Recommendations", ""])
    for item in inventory["recommendations"]:
        lines.append(f"- {item}")
    lines.extend(["", "## Category Samples", ""])
    for key, values in inventory["categories"].items():
        lines.extend(["", f"### {key}", ""])
        for item in values[:80]:
            lines.append(f"- `{item}`")
        if len(values) > 80:
            lines.append(f"- ... {len(values) - 80} more")
    return "\n".join(lines) + "\n"


def render_console_summary(inventory: dict[str, Any]) -> str:
    counts = ", ".join(f"{key}={len(values)}" for key, values in inventory["categories"].items())
    return (
        "Repository hygiene audit complete.\n"
        f"Categories: {counts}\n"
        f"Large file candidates: {len(inventory['large_file_candidates'])}\n"
        f"Suspicious credential-like files: {len(inventory['suspicious_credential_like_files'])}\n"
        f"Wrote: {MAINTENANCE_DIR / 'REPO_CLEANUP_AUDIT.md'}\n"
        f"Wrote: {MAINTENANCE_DIR / 'repo_cleanup_inventory.json'}"
    )


def run_git(args: list[str]) -> str:
    return run(["git", *args])


def run(args: list[str]) -> str:
    completed = subprocess.run(args, cwd=PROJECT_ROOT, text=True, capture_output=True, check=False)
    return completed.stdout


def write_json(payload: dict[str, Any], path: Path) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def relpath(path: Path) -> str:
    return path.resolve().relative_to(PROJECT_ROOT).as_posix()


if __name__ == "__main__":
    raise SystemExit(main())

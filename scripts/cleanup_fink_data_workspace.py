#!/usr/bin/env python3
"""Safely audit and quarantine generated local Fink data artifacts."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
GENERATED_TARGETS = (
    Path("data/raw/data_transfer"),
    Path("data/processed/data_transfer"),
    Path("outputs/data_transfer"),
)
PLACEHOLDER_FILES = (
    Path("data/raw/data_transfer/.gitkeep"),
    Path("data/raw/data_transfer/full_night/.gitkeep"),
    Path("data/raw/data_transfer/smoke_delivery/.gitkeep"),
    Path("data/processed/data_transfer/.gitkeep"),
)
PROTECTED_RELS = {
    Path(".git"),
    Path("src"),
    Path("scripts"),
    Path("notebooks"),
    Path("docs"),
    Path("configs"),
    Path("tests"),
}
DETECTED_ONLY_GLOBS = (
    "avro_schema_ftransfer_*.json",
    "arrow_schema_ftransfer_*.metadata",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=str(PROJECT_ROOT), help=argparse.SUPPRESS)
    parser.add_argument("--dry-run", action="store_true", help="Write an audit plan without moving or deleting files.")
    parser.add_argument("--quarantine", action="store_true", help="Move generated data targets into quarantine.")
    parser.add_argument("--delete-quarantine", action="store_true", help="Permanently delete a prior quarantine directory.")
    parser.add_argument("--quarantine-dir", help="Quarantine directory to delete with --delete-quarantine.")
    args = parser.parse_args()

    selected = sum(bool(value) for value in (args.dry_run, args.quarantine, args.delete_quarantine))
    if selected > 1:
        parser.error("choose only one of --dry-run, --quarantine, or --delete-quarantine")
    mode = "dry-run"
    if args.quarantine:
        mode = "quarantine"
    if args.delete_quarantine:
        mode = "delete-quarantine"

    root = Path(args.project_root).resolve()
    if mode == "delete-quarantine":
        if not args.quarantine_dir:
            parser.error("--delete-quarantine requires --quarantine-dir")
        result = delete_quarantine(root, Path(args.quarantine_dir))
        print(render_result(result))
        return 0 if result["status"] == "deleted" else 2

    result = run_cleanup(root, quarantine=(mode == "quarantine"))
    print(render_result(result))
    return 0 if result["status"] in {"dry-run", "quarantined"} else 2


def run_cleanup(root: Path, *, quarantine: bool) -> dict[str, Any]:
    timestamp = utc_timestamp()
    audit_dir = root / "outputs" / "maintenance" / f"fink_data_cleanup_{timestamp}"
    quarantine_dir = root / "outputs" / "maintenance" / f"fink_data_quarantine_{timestamp}"
    audit_dir.mkdir(parents=True, exist_ok=False)

    plan = build_plan(root, timestamp=timestamp, quarantine_dir=quarantine_dir)
    write_audit(audit_dir, "plan", plan)

    if not quarantine:
        result = {
            **plan,
            "status": "dry-run",
            "actions": [],
            "recreated_directories": [],
            "audit_dir": str(audit_dir),
        }
        write_audit(audit_dir, "result", result)
        return result

    actions: list[dict[str, Any]] = []
    blockers = [target for target in plan["targets"] if target["refusal_reason"]]
    if blockers:
        result = {
            **plan,
            "status": "refused",
            "actions": actions,
            "recreated_directories": [],
            "audit_dir": str(audit_dir),
            "error": "one or more targets failed safety validation",
        }
        write_audit(audit_dir, "result", result)
        return result

    quarantine_dir.mkdir(parents=True, exist_ok=False)
    for target in plan["targets"]:
        source = Path(target["absolute_path"])
        if not target["exists"]:
            actions.append({"path": target["path"], "action": "missing_noop"})
            continue
        destination = quarantine_dir / target["path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
        actions.append(
            {
                "path": target["path"],
                "action": "moved_to_quarantine",
                "quarantine_path": str(destination),
            }
        )

    recreated = []
    for rel in GENERATED_TARGETS:
        directory = root / rel
        directory.mkdir(parents=True, exist_ok=True)
        recreated.append(str(rel))
    for rel in PLACEHOLDER_FILES:
        placeholder = root / rel
        placeholder.parent.mkdir(parents=True, exist_ok=True)
        if not placeholder.exists():
            placeholder.write_text("\n", encoding="utf-8")
        recreated.append(str(rel))

    result = {
        **build_plan(root, timestamp=timestamp, quarantine_dir=quarantine_dir),
        "status": "quarantined",
        "actions": actions,
        "recreated_directories": recreated,
        "audit_dir": str(audit_dir),
    }
    write_audit(audit_dir, "result", result)
    return result


def build_plan(
    root: Path,
    *,
    timestamp: str | None = None,
    quarantine_dir: Path | None = None,
    target_relatives: Iterable[Path] = GENERATED_TARGETS,
) -> dict[str, Any]:
    root = root.resolve()
    timestamp = timestamp or utc_timestamp()
    quarantine_dir = quarantine_dir or root / "outputs" / "maintenance" / f"fink_data_quarantine_{timestamp}"
    targets = []
    for rel in target_relatives:
        rel_path = Path(rel)
        absolute = root / rel_path
        refusal = validate_operation_path(root, absolute)
        if rel_path not in GENERATED_TARGETS and not refusal:
            refusal = "not a known generated Fink data target"
        targets.append(inspect_path(root, rel_path, refusal_reason=refusal))
    return {
        "phase": "Checkpoint 11A - Clean Fink Data Reset Before Light-Packet Baseline",
        "created_at_utc": timestamp,
        "project_root": str(root),
        "quarantine_dir": str(quarantine_dir),
        "targets": targets,
        "detected_only": detect_generated_artifacts(root),
    }


def validate_operation_path(root: Path, path: Path) -> str | None:
    root = root.resolve()
    resolved = resolve_for_safety(path)
    if resolved == Path("/"):
        return "refusing to operate on filesystem root"
    if resolved == Path.home().resolve():
        return "refusing to operate on home directory"
    if resolved == root:
        return "refusing to operate on project root"
    if not is_inside(root, resolved) and not is_inside(root, path.absolute()):
        return "path is outside project root"
    try:
        rel = resolved.relative_to(root)
    except ValueError:
        rel = path.resolve(strict=False).relative_to(root)
    first = Path(rel.parts[0]) if rel.parts else rel
    if first in PROTECTED_RELS:
        return f"refusing to operate on protected project path: {first}"
    return None


def inspect_path(root: Path, rel_path: Path, *, refusal_reason: str | None = None) -> dict[str, Any]:
    path = root / rel_path
    exists = path.exists() or path.is_symlink()
    kind = path_type(path)
    return {
        "path": str(rel_path),
        "absolute_path": str(path),
        "exists": exists,
        "type": kind,
        "size_bytes": path_size(path) if exists else 0,
        "file_count": file_count(path) if exists and kind == "directory" else None,
        "inside_project_root": is_inside(root, resolve_for_safety(path)) or is_inside(root, path.resolve(strict=False)),
        "git_ignored": git_check_ignore(root, rel_path),
        "symlink_target": symlink_target(path),
        "refusal_reason": refusal_reason,
    }


def detect_generated_artifacts(root: Path) -> list[dict[str, Any]]:
    detected: list[dict[str, Any]] = []
    for pattern in DETECTED_ONLY_GLOBS:
        for path in sorted(root.glob(pattern)):
            detected.append(inspect_path(root, path.relative_to(root), refusal_reason="detected only; not auto-quarantined"))
    return detected


def delete_quarantine(root: Path, quarantine_dir: Path) -> dict[str, Any]:
    root = root.resolve()
    path = quarantine_dir.resolve(strict=False)
    expected_parent = root / "outputs" / "maintenance"
    result = {
        "phase": "Checkpoint 11A - Clean Fink Data Reset Before Light-Packet Baseline",
        "created_at_utc": utc_timestamp(),
        "project_root": str(root),
        "quarantine_dir": str(path),
        "status": "refused",
    }
    if quarantine_dir.is_symlink():
        result["error"] = "refusing to delete quarantine path because it is a symlink"
        return result
    if not is_inside(expected_parent, path) or path.parent != expected_parent:
        result["error"] = "quarantine path must be directly under outputs/maintenance"
        return result
    if not path.name.startswith("fink_data_quarantine_"):
        result["error"] = "quarantine path name does not match fink_data_quarantine_<UTC_TIMESTAMP>"
        return result
    refusal = validate_operation_path(root, path)
    if refusal:
        result["error"] = refusal
        return result
    if not path.exists():
        result["error"] = "quarantine path does not exist"
        return result
    shutil.rmtree(path)
    result["status"] = "deleted"
    return result


def write_audit(audit_dir: Path, label: str, report: dict[str, Any]) -> None:
    json_name = f"fink_data_cleanup_{label}.json"
    md_name = f"FINK_DATA_CLEANUP_{label.upper()}.md"
    (audit_dir / json_name).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (audit_dir / md_name).write_text(render_markdown(label, report), encoding="utf-8")


def render_result(result: dict[str, Any]) -> str:
    lines = [
        f"status: {result['status']}",
        f"audit_dir: {result.get('audit_dir', 'n/a')}",
        f"quarantine_dir: {result.get('quarantine_dir', 'n/a')}",
    ]
    if result.get("error"):
        lines.append(f"error: {result['error']}")
    for target in result.get("targets", []):
        lines.append(
            f"- {target['path']}: exists={target['exists']} type={target['type']} "
            f"size={target['size_bytes']} files={target['file_count']} ignored={target['git_ignored']}"
        )
    return "\n".join(lines)


def render_markdown(label: str, report: dict[str, Any]) -> str:
    title = "Plan" if label == "plan" else "Result"
    lines = [
        f"# Fink Data Cleanup {title}",
        "",
        f"- Phase: `{report['phase']}`",
        f"- Created UTC: `{report['created_at_utc']}`",
        f"- Project root: `{report['project_root']}`",
        f"- Quarantine dir: `{report['quarantine_dir']}`",
    ]
    if "status" in report:
        lines.append(f"- Status: `{report['status']}`")
    lines.extend(["", "## Targets", ""])
    for target in report.get("targets", []):
        lines.extend(
            [
                f"### `{target['path']}`",
                "",
                f"- Exists: `{target['exists']}`",
                f"- Type: `{target['type']}`",
                f"- Size bytes: `{target['size_bytes']}`",
                f"- File count: `{target['file_count']}`",
                f"- Inside project root: `{target['inside_project_root']}`",
                f"- Git ignored: `{target['git_ignored']}`",
                f"- Symlink target: `{target['symlink_target']}`",
                f"- Refusal reason: `{target['refusal_reason']}`",
                "",
            ]
        )
    lines.extend(["## Detected But Not Auto-Quarantined", ""])
    for item in report.get("detected_only", []):
        lines.append(f"- `{item['path']}` ({item['type']}, {item['size_bytes']} bytes)")
    if not report.get("detected_only"):
        lines.append("- None.")
    if report.get("actions"):
        lines.extend(["", "## Actions", ""])
        for action in report["actions"]:
            lines.append(f"- `{action['path']}`: `{action['action']}`")
    return "\n".join(lines) + "\n"


def path_type(path: Path) -> str:
    if path.is_symlink():
        return "symlink"
    if path.is_dir():
        return "directory"
    if path.is_file():
        return "file"
    return "missing"


def path_size(path: Path) -> int:
    if path.is_symlink() or path.is_file():
        return path.lstat().st_size
    if path.is_dir():
        total = path.lstat().st_size
        for current, dirs, files in os.walk(path, followlinks=False):
            current_path = Path(current)
            for name in dirs + files:
                child = current_path / name
                try:
                    total += child.lstat().st_size
                except FileNotFoundError:
                    continue
        return total
    return 0


def file_count(path: Path) -> int:
    total = 0
    for _current, _dirs, files in os.walk(path, followlinks=False):
        total += len(files)
    return total


def symlink_target(path: Path) -> str | None:
    if not path.is_symlink():
        return None
    return os.readlink(path)


def git_check_ignore(root: Path, rel_path: Path) -> bool | None:
    completed = subprocess.run(
        ["git", "check-ignore", "-q", str(rel_path)],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode == 0:
        return True
    if completed.returncode == 1:
        return False
    return None


def resolve_for_safety(path: Path) -> Path:
    return path.resolve(strict=False)


def is_inside(parent: Path, child: Path) -> bool:
    parent = parent.resolve(strict=False)
    child = child.resolve(strict=False)
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


if __name__ == "__main__":
    raise SystemExit(main())

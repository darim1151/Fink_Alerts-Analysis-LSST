"""Safe local storage helpers for small reconnaissance artifacts."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .data_root import PathConfinementError, confine, storage_base


ALLOWED_ARTIFACT_ROOTS = ("data", "outputs")


def project_path(path: str | Path, project_root: str | Path | None = None) -> Path:
    """Resolve a path relative to the project root without assuming a machine path."""
    root = Path(project_root or Path.cwd()).resolve()
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    return candidate.resolve()


def ensure_under_allowed_roots(
    path: str | Path,
    project_root: str | Path | None = None,
    allowed_roots: Iterable[str] = ALLOWED_ARTIFACT_ROOTS,
) -> Path:
    """Return a resolved path only if it is under an allowed artifact root.

    The allowed roots are canonical `<root>/<name>` bases, so a symlinked
    anchor or an existing symlinked target that leads elsewhere is rejected.
    """
    root = Path(project_root or Path.cwd()).resolve()
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    allowed_roots = tuple(allowed_roots)
    for allowed_root in allowed_roots:
        try:
            return confine(candidate, storage_base(root, allowed_root))
        except PathConfinementError:
            continue
    raise ValueError(f"Artifact path must be under one of: {', '.join(allowed_roots)}")


def safe_artifact_path(
    relative_path: str | Path,
    root_name: str = "outputs",
    project_root: str | Path | None = None,
) -> Path:
    """Build a safe artifact path below `data/` or `outputs/`."""
    if root_name not in ALLOWED_ARTIFACT_ROOTS:
        raise ValueError(f"root_name must be one of {ALLOWED_ARTIFACT_ROOTS}")
    rel = Path(relative_path)
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError("Artifact path must be a relative path without '..'")
    root = Path(project_root or Path.cwd()).resolve()
    return ensure_under_allowed_roots(root / root_name / rel, root)


def write_json(
    data: Any,
    path: str | Path,
    project_root: str | Path | None = None,
    enforce_allowed_roots: bool = True,
) -> Path:
    """Write JSON to a safe local artifact path."""
    output_path = (
        ensure_under_allowed_roots(path, project_root)
        if enforce_allowed_roots
        else project_path(path, project_root)
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True, default=str)
        handle.write("\n")
    return output_path


def read_json(path: str | Path, project_root: str | Path | None = None) -> Any:
    """Read JSON from a local path."""
    input_path = project_path(path, project_root)
    with input_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_parquet(df: Any, path: str | Path, project_root: str | Path | None = None) -> Path:
    """Write a DataFrame to Parquet if pandas and a Parquet engine are installed."""
    output_path = ensure_under_allowed_roots(path, project_root)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        df.to_parquet(output_path, index=False)
    except ImportError as exc:
        raise RuntimeError("Writing Parquet requires pyarrow or fastparquet") from exc
    return output_path


def read_parquet(path: str | Path, project_root: str | Path | None = None) -> Any:
    """Read a Parquet file into a DataFrame."""
    input_path = project_path(path, project_root)
    try:
        import pandas as pd
    except ImportError as exc:
        raise RuntimeError("Reading Parquet requires pandas") from exc
    return pd.read_parquet(input_path)


def write_manifest(
    path: str | Path,
    artifacts: list[dict[str, Any]],
    status: str = "completed",
    notes: list[str] | None = None,
    project_root: str | Path | None = None,
) -> Path:
    """Write a structured manifest for a smoke-test run."""
    manifest = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "artifacts": artifacts,
        "notes": notes or [],
    }
    return write_json(manifest, path, project_root=project_root)


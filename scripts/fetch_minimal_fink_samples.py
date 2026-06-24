#!/usr/bin/env python
"""Fetch tiny Fink LSST samples using real discovered identifiers."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fink_lsst.api import FinkApiClient
from fink_lsst.config import load_config
from fink_lsst.storage import read_json, safe_artifact_path, write_json


def main() -> int:
    """Fetch minimal object/source/fp samples when real IDs are available."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/local_smoke_test.yaml")
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    config = load_config(project_root / args.config)
    client = FinkApiClient(config=config)
    created_at = datetime.now(timezone.utc).isoformat()
    candidates_path = project_root / "outputs/id_discovery/id_candidates.json"
    if not candidates_path.exists():
        raise FileNotFoundError("Run scripts/discover_real_fink_ids.py first")
    candidates = read_json(candidates_path)
    object_id = _selected_value(candidates, "object")

    attempts: list[dict[str, Any]] = []
    artifacts: list[dict[str, str]] = []

    specs = [
        ("objects", "objects", "objects_sample_raw.json", "r:diaObjectId,r:diaSourceId,r:ra,r:dec,r:firstDiaSourceMjdTai,f:firstDiaSourceMjdTaiFink,f:main_label_classifier,f:main_label_crossmatch"),
        ("sources", "sources", "sources_sample_raw.json", "r:diaObjectId,r:diaSourceId,r:ra,r:dec,r:midpointMjdTai,r:band,r:psfFlux,r:psfFluxErr,f:clf_cats_class,f:clf_cats_score"),
        ("fp", "fp", "fp_sample_raw.json", "r:diaObjectId,r:diaForcedSourceId,r:ra,r:dec,r:midpointMjdTai,r:band,r:psfFlux,r:psfFluxErr,r:scienceFlux,r:scienceFluxErr"),
    ]
    for name, endpoint, filename, columns in specs:
        if not object_id:
            attempts.append(
                {
                    "name": name,
                    "endpoint": endpoint,
                    "ok": False,
                    "skipped": True,
                    "skip_reason": "No real diaObjectId discovered",
                    "safe_for_public_smoke": True,
                }
            )
            continue
        payload = {"diaObjectId": object_id, "columns": columns, "output-format": "json"}
        diagnostic = client.probe_endpoint(endpoint, method="POST", payload=payload)
        diagnostic["name"] = name
        diagnostic["safe_for_public_smoke"] = True
        if diagnostic.get("ok"):
            raw_path = safe_artifact_path(f"raw/minimal_samples/{filename}", root_name="data", project_root=project_root)
            write_json(diagnostic["data"], raw_path, project_root=project_root)
            diagnostic["output_file"] = str(raw_path)
            diagnostic["response_saved"] = True
            artifacts.append({"name": name, "path": str(raw_path)})
        diagnostic.pop("data", None)
        attempts.append(diagnostic)

    attempts_path = safe_artifact_path(
        "minimal_ingestion/sample_endpoint_attempts.json",
        root_name="outputs",
        project_root=project_root,
    )
    write_json(attempts, attempts_path, project_root=project_root)

    manifest = {
        "created_at_utc": created_at,
        "base_url": client.base_url,
        "selected_diaObjectId": object_id,
        "attempts": attempts,
        "artifacts": artifacts,
        "note": "Tiny real-ID sample retrieval only; not a population-level dataset.",
    }
    manifest_path = safe_artifact_path(
        "raw/minimal_samples/sample_retrieval_manifest.json",
        root_name="data",
        project_root=project_root,
    )
    write_json(manifest, manifest_path, project_root=project_root)
    print(f"Wrote endpoint attempts: {attempts_path}")
    print(f"Wrote manifest: {manifest_path}")
    return 0


def _selected_value(candidates: dict[str, Any], key: str) -> str | None:
    selected = candidates.get("selected", {}).get(key)
    if not selected:
        return None
    return str(selected.get("value"))


if __name__ == "__main__":
    raise SystemExit(main())

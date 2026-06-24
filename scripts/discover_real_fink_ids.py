#!/usr/bin/env python
"""Discover real Fink LSST DiaObject/DiaSource IDs from tiny public sources."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fink_lsst.api import FinkApiClient
from fink_lsst.config import load_config
from fink_lsst.contracts import summarize_contracts
from fink_lsst.id_discovery import (
    extract_candidate_ids_from_file,
    extract_candidate_ids_from_payload,
    merge_candidate_ids,
    select_best_candidate_id,
    summarize_candidate_ids,
)
from fink_lsst.storage import safe_artifact_path, write_json


FIXTURE_PATTERNS = [
    "data/fixtures/*.json",
    "data/fixtures/schema/*.json",
    "data/fixtures/samples/*.json",
]


def main() -> int:
    """Run minimal ID discovery and save candidates plus manifest."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/local_smoke_test.yaml")
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    config = load_config(project_root / args.config)
    client = FinkApiClient(config=config)
    smoke = dict(config.smoke_test or {})
    created_at = datetime.now(timezone.utc).isoformat()

    candidate_sets = []
    attempts: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []

    for pattern in FIXTURE_PATTERNS:
        for path in sorted(project_root.glob(pattern)):
            try:
                extracted = extract_candidate_ids_from_file(path)
            except Exception as exc:  # noqa: BLE001 - fixture scan should continue
                attempts.append({"source": str(path), "ok": False, "error": str(exc)})
                continue
            candidate_sets.append(extracted)
            attempts.append({"source": str(path), "ok": True, "candidate_summary": summarize_candidate_ids(extracted)})

    merged = merge_candidate_ids(candidate_sets)

    if not select_best_candidate_id(merged, "object"):
        tag_attempt = _try_tiny_tag_lookup(client, smoke, project_root)
        attempts.append(tag_attempt["diagnostic"])
        if tag_attempt.get("output_file"):
            artifacts.append({"name": "tiny_tag_lookup", "path": tag_attempt["output_file"]})
        if tag_attempt.get("candidates"):
            merged = merge_candidate_ids([merged, tag_attempt["candidates"]])

    if not select_best_candidate_id(merged, "object"):
        cone_attempt = _try_configured_conesearch(client, smoke, project_root)
        attempts.append(cone_attempt["diagnostic"])
        if cone_attempt.get("output_file"):
            artifacts.append({"name": "tiny_conesearch", "path": cone_attempt["output_file"]})
        if cone_attempt.get("candidates"):
            merged = merge_candidate_ids([merged, cone_attempt["candidates"]])

    payload = {
        "created_at_utc": created_at,
        "base_url": client.base_url,
        "candidates": merged,
        "summary": summarize_candidate_ids(merged),
        "selected": {
            "object": select_best_candidate_id(merged, "object"),
            "source": select_best_candidate_id(merged, "source"),
        },
        "note": "Tiny ID discovery only; not a population-level dataset.",
    }
    candidates_path = safe_artifact_path("id_discovery/id_candidates.json", root_name="outputs", project_root=project_root)
    write_json(payload, candidates_path, project_root=project_root)
    artifacts.append({"name": "id_candidates", "path": str(candidates_path)})

    manifest = {
        "created_at_utc": created_at,
        "base_url": client.base_url,
        "attempts": attempts,
        "artifacts": artifacts,
        "selected": payload["selected"],
        "note": "No Data Transfer, Kafka, Livestream, cutouts, or large downloads used.",
    }
    manifest_path = safe_artifact_path(
        "id_discovery/id_discovery_manifest.json",
        root_name="outputs",
        project_root=project_root,
    )
    write_json(manifest, manifest_path, project_root=project_root)
    print(f"Wrote candidates: {candidates_path}")
    print(f"Wrote manifest: {manifest_path}")
    print(f"Selected object: {payload['selected']['object']}")
    print(f"Selected source: {payload['selected']['source']}")
    return 0


def _try_tiny_tag_lookup(client: FinkApiClient, smoke: dict[str, Any], project_root: Path) -> dict[str, Any]:
    """Try a tiny bounded /tags lookup for real alert rows."""
    tag = smoke.get("sample_tag", "in_tns")
    max_rows = int(smoke.get("max_sample_rows", smoke.get("max_rows", 3)) or 3)
    payload = {
        "tag": tag,
        "n": str(max(1, min(max_rows, 5))),
        "columns": "r:diaObjectId,r:diaSourceId,r:ra,r:dec,r:midpointMjdTai,r:band,f:classId,f:main_label_classifier",
        "output-format": "json",
    }
    diagnostic = client.probe_endpoint("tags", method="POST", payload=payload)
    diagnostic["name"] = "tiny_tag_lookup"
    diagnostic["safe_for_public_smoke"] = True
    if not diagnostic.get("ok"):
        diagnostic.pop("data", None)
        return {"diagnostic": diagnostic}
    raw_path = safe_artifact_path(
        "fixtures/samples/fink_lsst_tags_id_lookup_sample.json",
        root_name="data",
        project_root=project_root,
    )
    write_json(diagnostic["data"], raw_path, project_root=project_root)
    candidates = extract_candidate_ids_from_payload(
        diagnostic["data"],
        origin="tiny_tag_lookup",
        source_path=str(raw_path),
    )
    diagnostic["output_file"] = str(raw_path)
    diagnostic["response_saved"] = True
    diagnostic.pop("data", None)
    return {"diagnostic": diagnostic, "output_file": str(raw_path), "candidates": candidates}


def _try_configured_conesearch(client: FinkApiClient, smoke: dict[str, Any], project_root: Path) -> dict[str, Any]:
    """Run a tiny conesearch only if coordinates are explicitly configured."""
    ra = smoke.get("sample_conesearch_ra")
    dec = smoke.get("sample_conesearch_dec")
    radius = smoke.get("sample_conesearch_radius_arcsec", 2)
    if ra in {None, ""} or dec in {None, ""}:
        return {
            "diagnostic": {
                "name": "tiny_conesearch",
                "endpoint": "conesearch",
                "ok": False,
                "skipped": True,
                "skip_reason": "sample_conesearch_ra/dec are not configured",
                "safe_for_public_smoke": True,
            }
        }
    payload = {
        "ra": str(ra),
        "dec": str(dec),
        "radius": str(radius),
        "n": str(min(int(smoke.get("max_sample_rows", 3) or 3), 5)),
        "columns": "r:diaObjectId,r:diaSourceId,r:ra,r:dec,r:midpointMjdTai,r:band",
        "output-format": "json",
    }
    diagnostic = client.probe_endpoint("conesearch", method="POST", payload=payload)
    diagnostic["name"] = "tiny_conesearch"
    diagnostic["safe_for_public_smoke"] = True
    if not diagnostic.get("ok"):
        diagnostic.pop("data", None)
        return {"diagnostic": diagnostic}
    raw_path = safe_artifact_path(
        "fixtures/samples/fink_lsst_conesearch_tiny_sample.json",
        root_name="data",
        project_root=project_root,
    )
    write_json(diagnostic["data"], raw_path, project_root=project_root)
    candidates = extract_candidate_ids_from_payload(diagnostic["data"], origin="tiny_conesearch", source_path=str(raw_path))
    diagnostic["output_file"] = str(raw_path)
    diagnostic["response_saved"] = True
    diagnostic.pop("data", None)
    return {"diagnostic": diagnostic, "output_file": str(raw_path), "candidates": candidates}


if __name__ == "__main__":
    raise SystemExit(main())

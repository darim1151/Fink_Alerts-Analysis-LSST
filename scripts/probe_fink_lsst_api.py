#!/usr/bin/env python
"""Tiny public/no-login Fink LSST REST endpoint probe."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fink_lsst.api import FinkApiClient
from fink_lsst.config import load_config
from fink_lsst.storage import safe_artifact_path, write_json


PROBES = [
    {
        "name": "schema",
        "endpoint": "schema",
        "method": "GET",
        "payload": None,
        "fixture_key": "schema_fixture",
        "default_fixture": "fixtures/fink_lsst_schema.json",
    },
    {
        "name": "tags",
        "endpoint": "tags",
        "method": "GET",
        "payload": None,
        "fixture_key": "tags_fixture",
        "default_fixture": "fixtures/fink_lsst_tags.json",
    },
    {
        "name": "statistics",
        "endpoint": "statistics",
        "method": "POST",
        "payload": None,
        "fixture_key": "statistics_fixture",
        "default_fixture": "fixtures/fink_lsst_statistics_sample.json",
    },
]


def main() -> int:
    """Run tiny endpoint probes and write a smoke-test manifest."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/local_smoke_test.yaml")
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    config = load_config(project_root / args.config)
    client = FinkApiClient(config=config)
    smoke_test = dict(config.smoke_test or {})
    attempts: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []

    for probe in PROBES:
        payload = probe["payload"]
        if probe["name"] == "statistics":
            payload = {
                "date": smoke_test.get("statistics_date", "2026"),
                "output-format": "json",
            }
        diagnostic = client.probe_endpoint(
            probe["endpoint"],
            method=probe["method"],
            payload=payload,
        )
        record = {
            key: value
            for key, value in diagnostic.items()
            if key != "data"
        }
        record["name"] = probe["name"]
        record["payload"] = payload

        if diagnostic.get("ok"):
            fixture_rel = smoke_test.get(probe["fixture_key"], probe["default_fixture"])
            fixture_path = safe_artifact_path(fixture_rel, root_name="data", project_root=project_root)
            write_json(diagnostic["data"], fixture_path, project_root=project_root)
            record["output_file"] = str(fixture_path)
            artifacts.append({"name": probe["name"], "path": str(fixture_path)})
        attempts.append(record)

    attempts_path = safe_artifact_path(
        "smoke_test/endpoint_attempts.json",
        root_name="outputs",
        project_root=project_root,
    )
    write_json(attempts, attempts_path, project_root=project_root)
    artifacts.append({"name": "endpoint_attempts", "path": str(attempts_path)})

    manifest = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "completed",
        "primary_survey": config.primary_survey,
        "base_url": client.base_url,
        "reference_api_base_url": config.reference_api_base_url,
        "attempted_endpoints": attempts,
        "artifacts": artifacts,
        "notes": [
            "Tiny public/no-login REST probes only.",
            "No Data Transfer, Kafka, Livestream, Spark, login-gated service, or bulk download was used.",
        ],
    }
    manifest_path = safe_artifact_path(
        "smoke_test/manifest.json",
        root_name="outputs",
        project_root=project_root,
    )
    write_json(manifest, manifest_path, project_root=project_root)

    print(f"Wrote manifest: {manifest_path}")
    for attempt in attempts:
        status = "ok" if attempt["ok"] else "failed"
        print(
            f"{attempt['name']}: {status} {attempt['method']} {attempt['url']} "
            f"status={attempt['status_code']} elapsed={attempt['elapsed_seconds']}s"
        )
        if attempt.get("error"):
            print(f"  error: {attempt['error']}")
        if attempt.get("output_file"):
            print(f"  saved: {attempt['output_file']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

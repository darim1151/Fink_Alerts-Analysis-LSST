#!/usr/bin/env python
"""Discover tiny public/no-login Fink LSST API contracts and schema request shapes."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fink_lsst.api import FinkApiClient
from fink_lsst.config import load_config
from fink_lsst.contracts import (
    get_endpoint_contract,
    list_endpoints,
    operation_contracts,
    summarize_contracts,
)
from fink_lsst.storage import safe_artifact_path, write_json


SCHEMA_ENDPOINTS = ["sources", "objects", "fp", "conesearch", "tags", "statistics"]
SCHEMA_FIXTURE_NAMES = {
    "sources": "fixtures/schema/fink_lsst_schema_sources.json",
    "objects": "fixtures/schema/fink_lsst_schema_objects.json",
    "fp": "fixtures/schema/fink_lsst_schema_fp.json",
    "conesearch": "fixtures/schema/fink_lsst_schema_conesearch.json",
    "tags": "fixtures/schema/fink_lsst_schema_tags.json",
    "statistics": "fixtures/schema/fink_lsst_schema_statistics.json",
}


def main() -> int:
    """Run contract discovery and write fixtures, diagnostics, and report files."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/local_smoke_test.yaml")
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    config = load_config(project_root / args.config)
    client = FinkApiClient(config=config)
    created_at = datetime.now(timezone.utc).isoformat()

    swagger_probe = client.request_json_or_diagnostic(
        method="GET",
        endpoint="/swagger.json",
        root_endpoint=True,
    )
    swagger: dict[str, Any] | None = None
    contract_summary: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []

    if swagger_probe.get("ok") and isinstance(swagger_probe.get("data"), dict):
        swagger = swagger_probe["data"]
        swagger_path = safe_artifact_path(
            "fixtures/fink_lsst_swagger.json",
            root_name="data",
            project_root=project_root,
        )
        write_json(swagger, swagger_path, project_root=project_root)
        artifacts.append({"name": "swagger", "path": str(swagger_path)})

        contract_summary = summarize_contracts(swagger)
        summary_path = safe_artifact_path(
            "fixtures/fink_lsst_api_contract_summary.json",
            root_name="data",
            project_root=project_root,
        )
        write_json(contract_summary, summary_path, project_root=project_root)
        artifacts.append({"name": "contract_summary", "path": str(summary_path)})

    schema_argument_names = _schema_argument_names(swagger)
    schema_probe_results = _probe_schema_variants(client, project_root, schema_argument_names)
    schema_results_path = safe_artifact_path(
        "api_contracts/schema_probe_results.json",
        root_name="outputs",
        project_root=project_root,
    )
    write_json(schema_probe_results, schema_results_path, project_root=project_root)
    artifacts.append({"name": "schema_probe_results", "path": str(schema_results_path)})

    endpoint_probe_results = _probe_endpoint_contracts(client, project_root, swagger)
    endpoint_results_path = safe_artifact_path(
        "api_contracts/endpoint_probe_results.json",
        root_name="outputs",
        project_root=project_root,
    )
    write_json(endpoint_probe_results, endpoint_results_path, project_root=project_root)
    artifacts.append({"name": "endpoint_probe_results", "path": str(endpoint_results_path)})

    report = _build_report(
        created_at=created_at,
        base_url=client.base_url,
        root_url=client.api_root_url,
        swagger_probe=swagger_probe,
        swagger=swagger,
        contract_summary=contract_summary,
        schema_probe_results=schema_probe_results,
        endpoint_probe_results=endpoint_probe_results,
        artifacts=artifacts,
    )
    report_path = safe_artifact_path(
        "api_contracts/FINK_LSST_API_CONTRACT_REPORT.md",
        root_name="outputs",
        project_root=project_root,
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    artifacts.append({"name": "api_contract_report", "path": str(report_path)})

    print(f"Swagger: {'ok' if swagger_probe.get('ok') else 'failed'} {swagger_probe['url']}")
    if swagger is not None:
        print(f"Discovered endpoints: {len(list_endpoints(swagger))}")
    print(f"Wrote report: {report_path}")
    return 0


def _schema_argument_names(swagger: dict[str, Any] | None) -> list[str]:
    """Return schema argument names supported by Swagger, falling back to endpoint."""
    supported = ["endpoint"]
    if not swagger:
        return supported
    contract = get_endpoint_contract(swagger, "/api/v1/schema")
    names: set[str] = set()
    for operation in contract.values():
        if not isinstance(operation, dict):
            continue
        for parameter in operation.get("parameters", []) or []:
            if not isinstance(parameter, dict):
                continue
            name = parameter.get("name")
            if name in {"endpoint", "name", "resource", "path"}:
                names.add(name)
    return sorted(names) if names else supported


def _probe_schema_variants(
    client: FinkApiClient,
    project_root: Path,
    argument_names: list[str],
) -> list[dict[str, Any]]:
    """Probe safe schema request variants and save JSON schema fixtures when found."""
    probes: list[dict[str, Any]] = []
    probes.append(_record(client.probe_endpoint("schema", method="GET"), safe=True))

    for endpoint_name in SCHEMA_ENDPOINTS:
        probes.append(
            _record(
                client.probe_endpoint(
                    "schema",
                    method="GET",
                    params={"endpoint": endpoint_name, "output-format": "json"},
                ),
                safe=True,
                schema_target=endpoint_name,
            )
        )
        probes.append(
            _record(
                client.probe_endpoint(
                    "schema",
                    method="GET",
                    params={"endpoint": f"/api/v1/{endpoint_name}", "output-format": "json"},
                ),
                safe=True,
                schema_target=endpoint_name,
            )
        )

    for argument_name in argument_names:
        for endpoint_name in SCHEMA_ENDPOINTS:
            value = f"/api/v1/{endpoint_name}" if argument_name == "path" else endpoint_name
            probes.append(
                _record(
                    client.probe_endpoint(
                        "schema",
                        method="POST",
                        payload={argument_name: value, "output-format": "json"},
                    ),
                    safe=True,
                    schema_target=endpoint_name,
                )
            )
            if argument_name == "endpoint":
                probes.append(
                    _record(
                        client.probe_endpoint(
                            "schema",
                            method="POST",
                            payload={"endpoint": f"/api/v1/{endpoint_name}", "output-format": "json"},
                        ),
                        safe=True,
                        schema_target=endpoint_name,
                    )
                )

    for probe in probes:
        if not probe.get("ok") or not probe.get("data") or not probe.get("schema_target"):
            probe.pop("data", None)
            continue
        fixture_rel = SCHEMA_FIXTURE_NAMES.get(str(probe["schema_target"]))
        if not fixture_rel:
            probe.pop("data", None)
            continue
        fixture_path = safe_artifact_path(fixture_rel, root_name="data", project_root=project_root)
        write_json(probe["data"], fixture_path, project_root=project_root)
        probe["output_file"] = str(fixture_path)
        probe["response_saved"] = True
        probe.pop("data", None)
    return probes


def _probe_endpoint_contracts(
    client: FinkApiClient,
    project_root: Path,
    swagger: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Probe tiny endpoint contracts and record safe skips for ID/cutout endpoints."""
    results: list[dict[str, Any]] = []
    operations = operation_contracts(swagger or {})

    probes = [
        {
            "name": "tags",
            "endpoint": "tags",
            "method": "GET",
            "payload": None,
            "sample": "fixtures/samples/fink_lsst_tags_sample.json",
        },
        {
            "name": "statistics_2026",
            "endpoint": "statistics",
            "method": "POST",
            "payload": {"date": "2026", "output-format": "json"},
            "sample": "fixtures/samples/fink_lsst_statistics_2026_sample.json",
        },
    ]
    if _operation_exists(operations, "/api/v1/blocks", "GET"):
        probes.append(
            {
                "name": "blocks",
                "endpoint": "blocks",
                "method": "GET",
                "payload": None,
                "sample": "fixtures/samples/fink_lsst_blocks_sample.json",
            }
        )

    for probe in probes:
        diagnostic = client.probe_endpoint(
            probe["endpoint"],
            method=probe["method"],
            payload=probe["payload"],
        )
        record = _record(diagnostic, safe=True, name=probe["name"])
        if record.get("ok") and record.get("data"):
            sample_path = safe_artifact_path(probe["sample"], root_name="data", project_root=project_root)
            write_json(record["data"], sample_path, project_root=project_root)
            record["output_file"] = str(sample_path)
            record["response_saved"] = True
        record.pop("data", None)
        results.append(record)

    skip_specs = [
        ("resolver", "/api/v1/resolver", "requires harmless documented input; not probed"),
        ("conesearch", "/api/v1/conesearch", "requires documented safe coordinates/radius; not probed"),
        ("sources", "/api/v1/sources", "requires known diaSourceId or valid query contract; not probed"),
        ("objects", "/api/v1/objects", "requires known diaObjectId; not probed"),
        ("fp", "/api/v1/fp", "requires known diaObjectId/diaSourceId; not probed"),
        ("cutouts", "/api/v1/cutouts", "deferred because it may fetch image/cutout data"),
        ("sso", "/api/v1/sso", "requires valid SSO identifier; not probed"),
        ("skymap", "/api/v1/skymap", "requires valid skymap payload; not probed"),
    ]
    for name, path, reason in skip_specs:
        if swagger is not None and path not in list_endpoints(swagger):
            reason = f"not present in discovered Swagger paths; {reason}"
        results.append(
            {
                "name": name,
                "endpoint": path,
                "method": None,
                "url": client.build_url(path),
                "ok": False,
                "skipped": True,
                "skip_reason": reason,
                "safe_for_public_smoke": False if name == "cutouts" else True,
                "response_saved": False,
            }
        )
    return results


def _operation_exists(
    operations: dict[tuple[str, str], dict[str, Any]],
    path: str,
    method: str,
) -> bool:
    """Return true if an operation exists in the summarized Swagger operations."""
    return (path, method.upper()) in operations


def _record(
    diagnostic: dict[str, Any],
    safe: bool,
    name: str | None = None,
    schema_target: str | None = None,
) -> dict[str, Any]:
    """Normalize diagnostics with provenance fields used by reports."""
    record = dict(diagnostic)
    if name is not None:
        record["name"] = name
    else:
        record["name"] = record.get("endpoint")
    record["schema_target"] = schema_target
    record["safe_for_public_smoke"] = safe
    record["response_saved"] = False
    return record


def _build_report(
    created_at: str,
    base_url: str,
    root_url: str,
    swagger_probe: dict[str, Any],
    swagger: dict[str, Any] | None,
    contract_summary: list[dict[str, Any]],
    schema_probe_results: list[dict[str, Any]],
    endpoint_probe_results: list[dict[str, Any]],
    artifacts: list[dict[str, str]],
) -> str:
    """Build a human-readable markdown API contract report."""
    lines = [
        "# Fink LSST API Contract Report",
        "",
        f"- Created UTC: `{created_at}`",
        f"- API base URL: `{base_url}`",
        f"- API root URL: `{root_url}`",
        "- Scope: tiny public/no-login REST contract discovery only.",
        "- Deferred: Data Transfer, Kafka, Livestream, Spark, login-gated services, and cutout/image downloads.",
        "",
        "## Swagger",
        "",
        f"- URL: `{swagger_probe.get('url')}`",
        f"- Success: `{swagger_probe.get('ok')}`",
        f"- Status code: `{swagger_probe.get('status_code')}`",
        f"- Content type: `{swagger_probe.get('content_type')}`",
    ]
    if swagger_probe.get("error"):
        lines.append(f"- Error: `{swagger_probe.get('error')}`")
    if swagger is not None:
        lines.append(f"- Discovered path count: `{len(list_endpoints(swagger))}`")
    lines.extend(["", "## Discovered Endpoint Contracts", ""])
    if contract_summary:
        lines.append("| Path | Methods | Summary | Response content types |")
        lines.append("|---|---:|---|---|")
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in contract_summary:
            grouped.setdefault(row["path"], []).append(row)
        for path, rows in grouped.items():
            methods = ", ".join(row["method"] for row in rows)
            summary = rows[0].get("summary") or rows[0].get("description") or ""
            content_types = sorted(
                {
                    content_type
                    for row in rows
                    for content_type in row.get("response_content_types", [])
                }
            )
            lines.append(f"| `{path}` | {methods} | {summary} | {', '.join(content_types)} |")
    else:
        lines.append("No Swagger contract summary was available.")

    lines.extend(["", "## Schema Probe Results", ""])
    lines.append("| Target | Method | URL | OK | Status | Content type | Saved | Error/preview |")
    lines.append("|---|---|---|---:|---:|---|---|---|")
    for result in schema_probe_results:
        preview = result.get("error") or result.get("response_preview") or ""
        lines.append(
            f"| `{result.get('schema_target') or 'bare'}` | {result.get('method')} | "
            f"`{result.get('url')}` | `{result.get('ok')}` | {result.get('status_code')} | "
            f"{result.get('content_type')} | {result.get('output_file', '')} | {preview} |"
        )

    lines.extend(["", "## Endpoint Probe Results", ""])
    lines.append("| Name | Method | URL | OK | Skipped | Saved | Error/skip reason |")
    lines.append("|---|---|---|---:|---:|---|---|")
    for result in endpoint_probe_results:
        reason = result.get("error") or result.get("skip_reason") or ""
        lines.append(
            f"| `{result.get('name')}` | {result.get('method')} | `{result.get('url')}` | "
            f"`{result.get('ok')}` | `{result.get('skipped', False)}` | "
            f"{result.get('output_file', '')} | {reason} |"
        )

    lines.extend(["", "## Artifacts", ""])
    for artifact in artifacts:
        lines.append(f"- `{artifact['name']}`: `{artifact['path']}`")
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- JSON endpoint success means the endpoint returned parseable JSON during this tiny probe.",
            "- HTML/text responses are preserved as diagnostics and are not interpreted as schema data.",
            "- Object/source/forced-photometry endpoints remain locked until a valid public example identifier is available.",
            "- This differs from the earlier ANTARES search-limit/tiling problem: this checkpoint is contract discovery, not sky tiling or ingestion.",
            "",
        ]
    )
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())

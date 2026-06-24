"""Capability diagnosis for full-night REST completeness feasibility."""

from __future__ import annotations

from typing import Any


LIMIT_NAMES = {"n", "limit", "max", "max_rows", "page_size"}
PAGINATION_NAMES = {"offset", "page", "cursor", "continuation", "next", "token"}
DATE_NAMES = {"date", "night", "startdate", "stopdate", "window"}
TAG_NAMES = {"tag", "class", "classification"}
BAND_NAMES = {"band", "filter"}
SKY_NAMES = {"ra", "dec", "radius", "radius_arcsec", "cone"}
OBJECT_ID_NAMES = {"diaobjectid", "objectid"}


def diagnose_completeness_capabilities(contract_summary: list[dict[str, Any]]) -> dict[str, Any]:
    """Inspect Swagger-derived contracts for safe enumeration and completeness signals."""
    endpoints = {path: _endpoint_diagnosis(path, contract_summary) for path in _interesting_paths(contract_summary)}
    tags = endpoints.get("/api/v1/tags", _empty_endpoint("/api/v1/tags"))
    sources = endpoints.get("/api/v1/sources", _empty_endpoint("/api/v1/sources"))
    conesearch = endpoints.get("/api/v1/conesearch", _empty_endpoint("/api/v1/conesearch"))
    statistics = endpoints.get("/api/v1/statistics", _empty_endpoint("/api/v1/statistics"))

    tag_specific = bool(tags["supports_row_cap"] and tags["supports_startdate"] and tags["supports_stopdate"] and tags["supports_tag_filter"])
    source_enumeration = bool(
        sources["supports_row_cap"]
        and sources["supports_startdate"]
        and not sources["requires_known_object_id"]
    )
    sky_enumeration = bool(conesearch["supports_row_cap"] and conesearch["supports_sky_filter"])
    all_alert = bool(source_enumeration)

    return {
        "endpoints": endpoints,
        "all_alert_completeness": {
            "public_rest_enumeration_supported": all_alert,
            "reason": (
                "A public row-capped source enumeration path appears available."
                if all_alert
                else "No public all-alert endpoint with safe row cap and required filters was proven from the contract. Bounded conesearch is a regional candidate path, not by itself full-night all-alert enumeration."
            ),
        },
        "sky_region_enumeration": {
            "supported": sky_enumeration,
            "endpoint": "/api/v1/conesearch" if sky_enumeration else None,
            "note": "A bounded sky-region endpoint can support regional tests only if sky partitioning is enabled and exhausts every region.",
        },
        "tag_specific_completeness": {
            "public_rest_enumeration_supported": tag_specific,
            "endpoint": "/api/v1/tags" if tag_specific else None,
            "reason": (
                "/api/v1/tags exposes tag, startdate, stopdate, and n parameters."
                if tag_specific
                else "/api/v1/tags does not expose all required bounded tag/date parameters."
            ),
        },
        "candidate_level_lookup": {
            "supported": any(
                endpoints.get(path, {}).get("requires_known_object_id")
                for path in ("/api/v1/objects", "/api/v1/sources", "/api/v1/fp")
            ),
            "note": "Known-ID lookup/enrichment is not an all-alert enumeration path.",
        },
        "object_enrichment": {"supported": endpoints.get("/api/v1/objects", {}).get("requires_known_object_id", False)},
        "forced_photometry_enrichment": {"supported": endpoints.get("/api/v1/fp", {}).get("requires_known_object_id", False)},
        "pagination_exposed": any(row["supports_pagination"] for row in endpoints.values()),
        "row_caps_exposed": any(row["supports_row_cap"] for row in endpoints.values()),
        "statistics_denominator": {
            "available": bool(statistics["supports_date_filter"]),
            "endpoint": "/api/v1/statistics",
            "note": "Statistics can be a denominator only when its date/window and filters match the extraction.",
        },
        "distinctions": {
            "all_alert_completeness": "Requires enumeration of every alert/source for the selected UTC night.",
            "tag_specific_completeness": "Can only prove completeness for rows matching a tag/filter.",
            "candidate_level_lookup": "Requires known IDs and cannot establish a full-night denominator.",
            "object_enrichment": "Adds context after enumeration; not a completeness basis.",
            "forced_photometry_enrichment": "Adds light-curve context after enumeration; not a completeness basis.",
        },
    }


def render_capability_diagnosis_markdown(diagnosis: dict[str, Any]) -> str:
    """Render full-night capability diagnosis as Markdown."""
    lines = [
        "# Full-Night REST Completeness Capability Diagnosis",
        "",
        f"- All-alert enumeration supported: `{diagnosis['all_alert_completeness']['public_rest_enumeration_supported']}`",
        f"- Tag-specific enumeration supported: `{diagnosis['tag_specific_completeness']['public_rest_enumeration_supported']}`",
        f"- Pagination exposed: `{diagnosis['pagination_exposed']}`",
        f"- Row caps exposed: `{diagnosis['row_caps_exposed']}`",
        f"- Sky-region enumeration supported: `{diagnosis['sky_region_enumeration']['supported']}`",
        "",
        "## Endpoint Signals",
        "",
        "| Endpoint | Cap | Pagination | Start | Stop | Tag | Band | Sky | Known ID |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for path, row in diagnosis["endpoints"].items():
        lines.append(
            f"| `{path}` | `{row['supports_row_cap']}` | `{row['supports_pagination']}` | "
            f"`{row['supports_startdate']}` | `{row['supports_stopdate']}` | "
            f"`{row['supports_tag_filter']}` | `{row['supports_band_filter']}` | "
            f"`{row['supports_sky_filter']}` | `{row['requires_known_object_id']}` |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            f"- {diagnosis['all_alert_completeness']['reason']}",
            f"- {diagnosis['tag_specific_completeness']['reason']}",
            "- Absence of exposed pagination in Swagger does not prove pagination is impossible; it means this phase cannot rely on it.",
            "- Known-ID object/source/forced-photometry endpoints are enrichment paths, not full-night enumeration paths.",
            "",
        ]
    )
    return "\n".join(lines)


def _endpoint_diagnosis(path: str, contract_summary: list[dict[str, Any]]) -> dict[str, Any]:
    operations = [row for row in contract_summary if row.get("path") == path]
    params = []
    for operation in operations:
        params.extend(operation.get("parameters", []))
    names = {_norm(parameter.get("name")) for parameter in params if parameter.get("name")}
    required = {_norm(parameter.get("name")) for operation in operations for parameter in operation.get("required_parameters", [])}
    return {
        "path": path,
        "methods": sorted({row.get("method") for row in operations if row.get("method")}),
        "parameter_names": sorted(names),
        "required_parameter_names": sorted(required),
        "supports_row_cap": bool(names & LIMIT_NAMES),
        "row_cap_parameters": sorted(names & LIMIT_NAMES),
        "supports_pagination": bool(names & PAGINATION_NAMES),
        "pagination_parameters": sorted(names & PAGINATION_NAMES),
        "supports_startdate": "startdate" in names or bool(names & {"date", "night"}),
        "supports_stopdate": "stopdate" in names or bool(names & {"date", "night"}),
        "supports_date_filter": bool(names & DATE_NAMES),
        "supports_tag_filter": bool(names & TAG_NAMES),
        "supports_band_filter": bool(names & BAND_NAMES),
        "supports_sky_filter": bool(names & SKY_NAMES),
        "requires_known_object_id": bool(required & OBJECT_ID_NAMES) or (path in {"/api/v1/objects", "/api/v1/sources", "/api/v1/fp"} and bool(names & OBJECT_ID_NAMES)),
    }


def _interesting_paths(contract_summary: list[dict[str, Any]]) -> list[str]:
    preferred = ["/api/v1/tags", "/api/v1/sources", "/api/v1/conesearch", "/api/v1/statistics", "/api/v1/objects", "/api/v1/fp"]
    existing = {row.get("path") for row in contract_summary}
    return [path for path in preferred if path in existing or path in preferred]


def _empty_endpoint(path: str) -> dict[str, Any]:
    return _endpoint_diagnosis(path, [])


def _norm(value: Any) -> str:
    return str(value or "").lower()

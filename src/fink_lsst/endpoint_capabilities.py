"""Endpoint capability analysis for bounded public REST extraction."""

from __future__ import annotations

from typing import Any


RELEVANT_PATHS = [
    "/api/v1/tags",
    "/api/v1/statistics",
    "/api/v1/objects",
    "/api/v1/sources",
    "/api/v1/fp",
    "/api/v1/conesearch",
    "/api/v1/blocks",
]

LIMIT_NAMES = {"n", "limit", "max", "max_rows", "page_size"}
PAGINATION_NAMES = {"offset", "page", "cursor", "continuation", "next", "token", "limit"}
DATE_NAMES = {"date", "night", "startdate", "stopdate", "window"}
TAG_NAMES = {"tag", "class", "classification"}
OBJECT_ID_NAMES = {"diaobjectid", "objectid"}
SOURCE_ID_NAMES = {"diasourceid", "sourceid", "midpointmjdtai"}


def analyze_endpoint_capabilities(contract_summary: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Summarize bounded-extraction capabilities for relevant Fink LSST endpoints."""
    rows = []
    for path in RELEVANT_PATHS:
        operations = [row for row in contract_summary if row.get("path") == path]
        params = _unique_parameters(operations)
        names = {_norm(parameter.get("name")) for parameter in params if parameter.get("name")}
        methods = sorted({row.get("method") for row in operations if row.get("method")})
        requires_object = any(name in OBJECT_ID_NAMES for name in names)
        requires_source = any(name in SOURCE_ID_NAMES for name in names) and path not in {"/api/v1/tags"}
        supports_limit = bool(names & LIMIT_NAMES)
        supports_pagination = bool(names & PAGINATION_NAMES)
        supports_date = bool(names & DATE_NAMES)
        supports_tag = bool(names & TAG_NAMES)
        row = {
            "path": path,
            "endpoint": path.rsplit("/", 1)[-1],
            "methods": methods,
            "summary": operations[0].get("summary") if operations else None,
            "parameters": params,
            "required_parameters": _unique_required(operations),
            "optional_parameter_names": sorted({parameter.get("name") for parameter in params if parameter.get("name")}),
            "supports_row_limit": supports_limit,
            "row_limit_parameters": sorted(name for name in names if name in LIMIT_NAMES),
            "supports_pagination_or_continuation": supports_pagination,
            "pagination_parameters": sorted(name for name in names if name in PAGINATION_NAMES),
            "supports_date_or_night_filter": supports_date,
            "date_filter_parameters": sorted(name for name in names if name in DATE_NAMES),
            "supports_tag_or_class_filter": supports_tag,
            "tag_filter_parameters": sorted(name for name in names if name in TAG_NAMES),
            "requires_diaObjectId": requires_object and path in {"/api/v1/objects", "/api/v1/sources", "/api/v1/fp"},
            "requires_diaSourceId_or_time": requires_source and path == "/api/v1/sources",
            "safe_for_bounded_public_rest": _is_safe(path, supports_limit, supports_date, requires_object),
        }
        rows.append(row)
    return rows


def capability_by_endpoint(capabilities: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Index capability rows by endpoint name."""
    return {row["endpoint"]: row for row in capabilities}


def render_capabilities_markdown(capabilities: list[dict[str, Any]]) -> str:
    """Render endpoint capabilities as a markdown report."""
    lines = [
        "# Endpoint Capabilities",
        "",
        "| Endpoint | Methods | Limit | Pagination | Date/window | Tag/class | ID-gated | Safe bounded use |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in capabilities:
        id_gated = bool(row["requires_diaObjectId"] or row["requires_diaSourceId_or_time"])
        lines.append(
            f"| `{row['path']}` | {', '.join(row['methods'])} | "
            f"`{row['supports_row_limit']}` | `{row['supports_pagination_or_continuation']}` | "
            f"`{row['supports_date_or_night_filter']}` | `{row['supports_tag_or_class_filter']}` | "
            f"`{id_gated}` | `{row['safe_for_bounded_public_rest']}` |"
        )
    lines.extend(
        [
            "",
            "Notes:",
            "- `supports_pagination_or_continuation` records exposed parameters only; absence does not prove complete retrieval.",
            "- `safe_for_bounded_public_rest` means the endpoint can be used with explicit caps or known IDs in this local phase.",
            "- Cutouts and binary/image endpoints are intentionally excluded.",
        ]
    )
    return "\n".join(lines)


def _unique_parameters(operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    params = []
    for operation in operations:
        for parameter in operation.get("parameters", []):
            key = (parameter.get("name"), parameter.get("in"))
            if key in seen:
                continue
            seen.add(key)
            params.append(parameter)
    return params


def _unique_required(operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    params = []
    for operation in operations:
        for parameter in operation.get("required_parameters", []):
            key = (parameter.get("name"), parameter.get("in"))
            if key in seen:
                continue
            seen.add(key)
            params.append(parameter)
    return params


def _norm(value: Any) -> str:
    return str(value or "").lower()


def _is_safe(path: str, supports_limit: bool, supports_date: bool, requires_object: bool) -> bool:
    if path == "/api/v1/blocks":
        return True
    if path == "/api/v1/statistics":
        return True
    if path == "/api/v1/tags":
        return supports_limit
    if path in {"/api/v1/objects", "/api/v1/sources", "/api/v1/fp"}:
        return requires_object
    if path == "/api/v1/conesearch":
        return supports_limit and supports_date
    return False

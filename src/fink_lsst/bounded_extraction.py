"""Bounded public REST extraction helpers for Fink LSST."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from .api import FinkApiClient
from .normalize import payload_to_dataframe


def build_tag_query_payload(config: dict[str, Any], capabilities: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a bounded `/tags` payload from config and endpoint capabilities."""
    max_rows = int(config.get("max_rows", 50))
    if max_rows <= 0:
        raise ValueError("bounded_extraction.max_rows must be positive")
    if max_rows > 500:
        raise ValueError("bounded_extraction.max_rows is too high for this local checkpoint")
    supports_limit = True if capabilities is None else bool(capabilities.get("supports_row_limit"))
    if not supports_limit and config.get("fail_on_unbounded_query", True):
        raise ValueError("/tags does not expose a row limit in capabilities")
    payload = {
        "tag": config.get("tag", "in_tns"),
        "n": str(max_rows),
        "columns": config.get("columns", {}).get("tag_rows"),
        "output-format": config.get("output_format", "json"),
    }
    if config.get("startdate"):
        payload["startdate"] = config["startdate"]
    if config.get("stopdate"):
        payload["stopdate"] = config["stopdate"]
    return {key: value for key, value in payload.items() if value not in {None, ""}}


def extract_bounded_tag_sample(
    client: FinkApiClient,
    config: dict[str, Any],
    capabilities: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Retrieve one bounded tag/window sample."""
    payload = build_tag_query_payload(config, capabilities)
    diagnostic = client.probe_endpoint("tags", method="POST", payload=payload)
    diagnostic["name"] = "bounded_tag_sample"
    diagnostic["truncation"] = detect_pagination_or_truncation(diagnostic.get("data"), payload, capabilities or {})
    return diagnostic


def extract_object_details_for_ids(
    client: FinkApiClient,
    object_ids: Iterable[Any],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    """Fetch tiny object details for a capped list of real object IDs."""
    return _extract_details(client, "objects", object_ids, config.get("columns", {}).get("objects"), config)


def extract_source_details_for_ids(
    client: FinkApiClient,
    object_ids_or_source_ids: Iterable[Any],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    """Fetch tiny source details for a capped list of real object IDs."""
    return _extract_details(client, "sources", object_ids_or_source_ids, config.get("columns", {}).get("sources"), config)


def extract_fp_for_ids(
    client: FinkApiClient,
    object_ids_or_source_ids: Iterable[Any],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    """Fetch tiny forced-photometry details for a capped list of real object IDs."""
    return _extract_details(client, "fp", object_ids_or_source_ids, config.get("columns", {}).get("forced_photometry"), config)


def detect_pagination_or_truncation(
    response: Any,
    request_payload: dict[str, Any],
    capabilities: dict[str, Any],
) -> dict[str, Any]:
    """Detect basic limit/truncation risks from response size and capabilities."""
    frame = payload_to_dataframe(response)
    requested_n = _safe_int(request_payload.get("n"))
    row_count = int(len(frame))
    has_pagination = bool(capabilities.get("supports_pagination_or_continuation"))
    hit_limit = requested_n is not None and row_count >= requested_n
    return {
        "row_count": row_count,
        "requested_n": requested_n,
        "hit_requested_limit": hit_limit,
        "pagination_exposed": has_pagination,
        "completeness_claim": False,
        "warning": "Response hit requested limit; additional rows may exist"
        if hit_limit
        else "No requested-limit truncation detected in this bounded response",
    }


def compare_to_statistics(extracted_summary: dict[str, Any], statistics_payload: Any) -> dict[str, Any]:
    """Compare bounded extraction counts to broad statistics payload where possible."""
    stats = payload_to_dataframe(statistics_payload)
    if stats.empty:
        return {"available": False, "message": "No statistics payload available"}
    extracted_rows = int(extracted_summary.get("tag_rows", 0))
    return {
        "available": True,
        "extracted_tag_rows": extracted_rows,
        "statistics_rows": int(len(stats)),
        "message": "Statistics are broad nightly/yearly counts; not directly comparable to tag-window rows without matching filters.",
        "completeness_claim": False,
    }


def build_bounded_extraction_manifest(
    run_id: str,
    base_url: str,
    config: dict[str, Any],
    attempts: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
    summary: dict[str, Any],
) -> dict[str, Any]:
    """Build a provenance manifest for one bounded extraction run."""
    return {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "base_url": base_url,
        "config": config,
        "attempts": attempts,
        "artifacts": artifacts,
        "summary": summary,
        "note": "Bounded public REST extraction only; not a full-night or population-level dataset.",
    }


def _extract_details(
    client: FinkApiClient,
    endpoint: str,
    object_ids: Iterable[Any],
    columns: str | None,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    max_objects = int(config.get("max_detail_objects", 10))
    unique_ids = []
    for object_id in object_ids:
        if pd.isna(object_id):
            continue
        value = str(object_id)
        if value not in unique_ids:
            unique_ids.append(value)
        if len(unique_ids) >= max_objects:
            break
    diagnostics = []
    for object_id in unique_ids:
        payload = {"diaObjectId": object_id, "columns": columns, "output-format": config.get("output_format", "json")}
        payload = {key: value for key, value in payload.items() if value not in {None, ""}}
        diagnostic = client.probe_endpoint(endpoint, method="POST", payload=payload)
        diagnostic["name"] = f"{endpoint}_{object_id}"
        diagnostics.append(diagnostic)
    return diagnostics


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

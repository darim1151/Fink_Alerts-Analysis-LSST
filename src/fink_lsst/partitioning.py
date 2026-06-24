"""Partition planning for full-night REST completeness feasibility."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .time_windows import split_utc_window


def build_time_partitions(startdate: str, stopdate: str, count: int) -> list[dict[str, Any]]:
    """Build UTC time partitions."""
    return [
        {
            "partition_id": f"time_{item['index']:03d}",
            "dimension": "time",
            "startdate": item["startdate"],
            "stopdate": item["stopdate"],
            "timezone": "UTC",
            "stopdate_convention": item["stopdate_convention"],
        }
        for item in split_utc_window(startdate, stopdate, count)
    ]


def build_band_partitions(bands: list[str]) -> list[dict[str, Any]]:
    """Build band partitions."""
    return [{"partition_id": f"band_{band}", "dimension": "band", "band": band} for band in bands]


def build_tag_partitions(tags: list[str]) -> list[dict[str, Any]]:
    """Build tag partitions."""
    return [{"partition_id": f"tag_{tag}", "dimension": "tag", "tag": tag} for tag in tags]


def build_sky_partitions(ra_bins: int, dec_bins: int) -> list[dict[str, Any]]:
    """Build coarse sky partitions as RA/Dec bounding boxes."""
    if ra_bins <= 0 or dec_bins <= 0:
        raise ValueError("sky partition bins must be positive")
    partitions = []
    for ra_index in range(ra_bins):
        for dec_index in range(dec_bins):
            ra_min = 360.0 * ra_index / ra_bins
            ra_max = 360.0 * (ra_index + 1) / ra_bins
            dec_min = -90.0 + 180.0 * dec_index / dec_bins
            dec_max = -90.0 + 180.0 * (dec_index + 1) / dec_bins
            partitions.append(
                {
                    "partition_id": f"sky_ra{ra_index:02d}_dec{dec_index:02d}",
                    "dimension": "sky",
                    "sky_bounds": {"ra_min": ra_min, "ra_max": ra_max, "dec_min": dec_min, "dec_max": dec_max},
                }
            )
    return partitions


def build_partition_plan(config: dict[str, Any], capabilities: dict[str, Any]) -> dict[str, Any]:
    """Build a safe partition plan from config and diagnosed public REST capabilities."""
    target_tag = config.get("target_tag")
    fallback_tag = config.get("fallback_tag")
    max_rows = int(config.get("max_rows_per_partition", 50))
    initial_count = int(config.get("time_partitions", {}).get("initial_count", 8))
    tags_endpoint = capabilities.get("endpoints", {}).get("/api/v1/tags", {})
    tag_supported = bool(capabilities.get("tag_specific_completeness", {}).get("public_rest_enumeration_supported"))
    all_alert_supported = bool(capabilities.get("all_alert_completeness", {}).get("public_rest_enumeration_supported"))
    allow_unfiltered = bool(config.get("allow_unfiltered_full_night_query", False))

    unsupported_dimensions = []
    notes = []
    completeness_scope = "unsupported"
    tag_for_query = target_tag

    if target_tag:
        if not tag_supported:
            return _unsupported_plan(config, "Configured target_tag but /tags lacks required bounded tag/date parameters")
        completeness_scope = "tag_specific"
    elif all_alert_supported and allow_unfiltered:
        completeness_scope = "all_alert"
    elif tag_supported and fallback_tag:
        completeness_scope = "tag_specific"
        tag_for_query = fallback_tag
        notes.append("All-alert REST enumeration was not proven; using fallback tag-specific feasibility test.")
    else:
        return _unsupported_plan(config, "No safe all-alert or tag-specific enumeration path is available")

    if not tags_endpoint.get("supports_startdate") or not tags_endpoint.get("supports_stopdate"):
        unsupported_dimensions.append("time")
        return _unsupported_plan(config, "No supported time/date filter is available")

    if "band" in config.get("partition_dimensions_preference", []) and not tags_endpoint.get("supports_band_filter"):
        unsupported_dimensions.append("band")
    sky_config = config.get("sky_partitions", {}) or {}
    if sky_config.get("enabled") and not capabilities.get("endpoints", {}).get("/api/v1/conesearch", {}).get("supports_sky_filter"):
        unsupported_dimensions.append("sky")

    base_partitions = build_time_partitions(config["target_startdate"], config["target_stopdate"], initial_count)
    partitions = []
    for index, partition in enumerate(base_partitions):
        payload = {
            "startdate": partition["startdate"],
            "stopdate": partition["stopdate"],
            "n": str(max_rows),
            "output-format": config.get("output_format", "json"),
        }
        columns = config.get("columns", {}).get("tag_rows") or config.get("tag_columns")
        if columns:
            payload["columns"] = columns
        if tag_for_query:
            payload["tag"] = tag_for_query
        if completeness_scope == "all_alert" and not allow_unfiltered:
            partition["status"] = "unsafe"
            partition["reason"] = "Unfiltered full-night query is disabled"
        partition.update(
            {
                "partition_id": f"{completeness_scope}_time_{index:03d}",
                "endpoint": "tags",
                "method": "POST",
                "query_payload": payload,
                "maximum_rows_requested": max_rows,
                "intended_completeness": completeness_scope,
                "target_tag": tag_for_query,
                "supports_refinement": True,
            }
        )
        partitions.append(partition)

    return {
        "strategy": config.get("partition_strategy", "auto"),
        "target_startdate": config["target_startdate"],
        "target_stopdate": config["target_stopdate"],
        "timezone": "UTC",
        "api_date_window_convention": config.get("api_date_window_convention"),
        "completeness_scope": completeness_scope,
        "target_tag": tag_for_query,
        "endpoint": "tags",
        "partition_count": len(partitions),
        "partitions": partitions,
        "unsupported_dimensions": unsupported_dimensions,
        "notes": notes,
        "safety": {
            "max_rows_per_partition": max_rows,
            "max_total_rows_safety": int(config.get("max_total_rows_safety", 5000)),
            "allow_unfiltered_full_night_query": allow_unfiltered,
        },
    }


def refine_capped_partitions(
    partitions: list[dict[str, Any]],
    results: list[dict[str, Any]],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    """Create smaller time partitions for capped results, up to the configured maximum."""
    capped = {result.get("partition_id") for result in results if result.get("status") == "possibly_truncated"}
    if not capped:
        return []
    current_count = len(partitions)
    max_count = int(config.get("time_partitions", {}).get("max_count", current_count))
    if current_count >= max_count:
        return []
    next_count = min(max_count, current_count * 2)
    refined = []
    base = build_time_partitions(config["target_startdate"], config["target_stopdate"], next_count)
    capped_prefixes = sorted(capped)
    for item in base:
        parent_hint = capped_prefixes[min(len(refined) * len(capped_prefixes) // max(next_count, 1), len(capped_prefixes) - 1)]
        payload = {
            "startdate": item["startdate"],
            "stopdate": item["stopdate"],
            "n": str(int(config.get("max_rows_per_partition", 50))),
            "output-format": config.get("output_format", "json"),
        }
        if config.get("fallback_tag"):
            payload["tag"] = config.get("target_tag") or config.get("fallback_tag")
        target_tag = payload.get("tag")
        refined_item = deepcopy(item)
        refined_item.update(
            {
                "partition_id": f"refined_{item['partition_id']}",
                "endpoint": "tags",
                "method": "POST",
                "query_payload": payload,
                "maximum_rows_requested": int(config.get("max_rows_per_partition", 50)),
                "intended_completeness": "tag_specific" if payload.get("tag") else "all_alert",
                "target_tag": target_tag,
                "refined_from": parent_hint,
                "supports_refinement": next_count < max_count,
            }
        )
        refined.append(refined_item)
    return refined


def _unsupported_plan(config: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "strategy": config.get("partition_strategy", "auto"),
        "target_startdate": config.get("target_startdate"),
        "target_stopdate": config.get("target_stopdate"),
        "timezone": "UTC",
        "completeness_scope": "unsupported",
        "partition_count": 1,
        "partitions": [
            {
                "partition_id": "unsupported_000",
                "status": "unsupported",
                "reason": reason,
                "query_payload": {},
                "maximum_rows_requested": 0,
                "intended_completeness": "unsupported",
            }
        ],
        "unsupported_dimensions": ["enumeration"],
        "notes": [reason],
        "safety": {
            "max_rows_per_partition": int(config.get("max_rows_per_partition", 50)),
            "max_total_rows_safety": int(config.get("max_total_rows_safety", 5000)),
            "allow_unfiltered_full_night_query": bool(config.get("allow_unfiltered_full_night_query", False)),
        },
    }

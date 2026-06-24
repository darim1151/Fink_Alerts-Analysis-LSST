"""Partitioned REST extraction helpers for full-night completeness feasibility."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .api import FinkApiClient
from .bounded_extraction import compare_to_statistics
from .normalize import normalize_sources, payload_to_dataframe
from .storage import write_json, write_parquet


TERMINAL_STATUSES = {"success", "failed", "skipped", "empty", "possibly_truncated", "exhausted", "unsafe", "unsupported"}


def run_partitioned_extraction(
    client: FinkApiClient,
    config: dict[str, Any],
    partition_plan: dict[str, Any],
    raw_partition_dir: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Run a safe partitioned extraction plan."""
    results = []
    total_rows = 0
    safety_cap = int(config.get("max_total_rows_safety", 5000))
    stop_on_cap = bool(config.get("stop_if_safety_cap_hit", True))
    partitions = partition_plan.get("partitions", [])
    for partition in partitions:
        if stop_on_cap and total_rows >= safety_cap:
            results.append(_skipped_for_safety(partition, total_rows, safety_cap))
            continue
        result = query_partition(client, partition)
        payload = result.pop("data", None)
        if result.get("success") and raw_partition_dir is not None:
            path = Path(raw_partition_dir) / f"{partition['partition_id']}.json"
            write_json(payload, path, enforce_allowed_roots=False)
            result["raw_response_file"] = str(path)
            result["raw_response_saved"] = True
        elif result.get("success"):
            result["raw_response_saved"] = False
        result["rows"] = payload_to_dataframe(payload).to_dict(orient="records") if payload is not None else []
        total_rows += int(result.get("row_count", 0))
        results.append(result)
    return results


def query_partition(client: FinkApiClient, partition: dict[str, Any]) -> dict[str, Any]:
    """Query one partition and classify the result."""
    if partition.get("status") == "unsupported":
        return _partition_status(partition, "unsupported", "Partition is unsupported")
    if partition.get("status") == "unsafe":
        return _partition_status(partition, "unsafe", partition.get("reason", "Partition is unsafe"))
    payload = partition.get("query_payload", {})
    if not payload.get("n"):
        return _partition_status(partition, "unsafe", "Partition query has no row cap")
    diagnostic = client.probe_endpoint(partition.get("endpoint", "tags"), method=partition.get("method", "POST"), payload=payload)
    return classify_partition_result(diagnostic, int(partition.get("maximum_rows_requested", payload.get("n", 0))), partition)


def classify_partition_result(
    response: dict[str, Any],
    requested_n: int,
    partition: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify one partition response."""
    partition = partition or {}
    base = {
        "partition_id": partition.get("partition_id"),
        "endpoint": partition.get("endpoint"),
        "method": partition.get("method"),
        "query_payload": partition.get("query_payload", {}),
        "requested_n": requested_n,
        "intended_completeness": partition.get("intended_completeness"),
        "target_tag": partition.get("target_tag"),
        "startdate": partition.get("startdate"),
        "stopdate": partition.get("stopdate"),
        "timezone": partition.get("timezone", "UTC"),
        "success": bool(response.get("ok")),
        "status_code": response.get("status_code"),
        "elapsed_seconds": response.get("elapsed_seconds"),
        "error": response.get("error"),
        "url": response.get("url"),
        "raw_response_saved": False,
    }
    if not response.get("ok"):
        base.update({"status": "failed", "row_count": 0, "message": response.get("error", "request failed")})
        return base
    frame = payload_to_dataframe(response.get("data"))
    row_count = int(len(frame))
    base["row_count"] = row_count
    base["data"] = response.get("data")
    if row_count == 0:
        base.update({"status": "empty", "message": "Partition returned zero rows"})
    elif requested_n and row_count >= requested_n:
        base.update({"status": "possibly_truncated", "message": "Partition returned the requested row cap"})
    else:
        base.update({"status": "exhausted", "message": "Partition returned fewer rows than requested cap"})
    return base


def deduplicate_rows(rows: list[dict[str, Any]], key_candidates: list[str] | None = None) -> dict[str, Any]:
    """Deduplicate rows by the first available key candidate."""
    frame = payload_to_dataframe(rows)
    if frame.empty:
        return {"rows": [], "row_count": 0, "deduplicated_count": 0, "duplicate_count": 0, "duplicate_rate": 0.0, "dedup_key": None}
    keys = key_candidates or ["r:diaSourceId", "diaSourceId", "internal_source_id", "r:diaObjectId", "diaObjectId", "internal_object_id"]
    key = next((candidate for candidate in keys if candidate in frame.columns and frame[candidate].notna().any()), None)
    before = len(frame)
    deduped = frame.drop_duplicates(subset=[key]) if key else frame.drop_duplicates()
    duplicate_count = before - len(deduped)
    return {
        "rows": deduped.to_dict(orient="records"),
        "row_count": before,
        "deduplicated_count": int(len(deduped)),
        "duplicate_count": int(duplicate_count),
        "duplicate_rate": float(duplicate_count / before) if before else 0.0,
        "dedup_key": key,
    }


def summarize_partition_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize partition statuses and row counts."""
    status_counts: dict[str, int] = {}
    for result in results:
        status_counts[result.get("status", "unknown")] = status_counts.get(result.get("status", "unknown"), 0) + 1
    return {
        "partition_count": len(results),
        "status_counts": status_counts,
        "attempted": sum(1 for result in results if result.get("status") not in {"skipped", "unsupported", "unsafe"}),
        "exhausted": status_counts.get("exhausted", 0) + status_counts.get("empty", 0),
        "possibly_truncated": status_counts.get("possibly_truncated", 0),
        "failed": status_counts.get("failed", 0),
        "skipped": status_counts.get("skipped", 0),
        "unsupported": status_counts.get("unsupported", 0),
        "unsafe": status_counts.get("unsafe", 0),
        "total_raw_rows": int(sum(result.get("row_count", 0) for result in results)),
    }


def detect_possible_truncation(partition_result: dict[str, Any]) -> bool:
    """Return true if a partition result blocks completeness due to possible truncation."""
    return partition_result.get("status") == "possibly_truncated" or (
        int(partition_result.get("row_count", 0)) >= int(partition_result.get("requested_n", 0) or 0) > 0
    )


def build_completeness_accounting(
    partition_results: list[dict[str, Any]],
    statistics_payload: Any,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Build denominator and completeness accounting without misleading fractions."""
    summary = summarize_partition_results(partition_results)
    stats_comparison = compare_to_statistics({"tag_rows": summary["total_raw_rows"]}, statistics_payload)
    direct = False
    reason = "Statistics are unavailable or not proven to match the target UTC window and filters."
    denominator_count = None
    denominator_type = "unknown"
    if stats_comparison.get("available"):
        denominator_type = "yearly_or_broad"
        reason = stats_comparison.get("message", reason)
    return {
        "target_startdate": config.get("target_startdate"),
        "target_stopdate": config.get("target_stopdate"),
        "timezone": "UTC",
        "denominator_available": bool(stats_comparison.get("available")),
        "denominator_type": denominator_type,
        "directly_comparable": direct,
        "reason": reason,
        "extracted_count": summary["total_raw_rows"],
        "denominator_count": denominator_count,
        "completeness_fraction": None,
        "partition_summary": summary,
        "statistics_comparison": stats_comparison,
    }


def write_full_night_artifacts(
    processed_dir: str | Path,
    rows: list[dict[str, Any]],
    partition_results: list[dict[str, Any]],
    accounting: dict[str, Any],
    project_root: str | Path,
) -> dict[str, str]:
    """Write processed full-night feasibility artifacts."""
    processed = Path(processed_dir)
    processed.mkdir(parents=True, exist_ok=True)
    artifacts: dict[str, str] = {}
    dedup = deduplicate_rows(rows)
    frame = normalize_sources(dedup["rows"])
    if not frame.empty:
        parquet = processed / "deduplicated_rows.parquet"
        write_parquet(frame, parquet, project_root=project_root)
        csv = processed / "deduplicated_rows.csv"
        frame.head(200).to_csv(csv, index=False)
        artifacts["deduplicated_rows_parquet"] = str(parquet)
        artifacts["deduplicated_rows_csv"] = str(csv)
        object_summary = _object_summary(frame)
        object_path = processed / "object_summary.parquet"
        write_parquet(object_summary, object_path, project_root=project_root)
        artifacts["object_summary_parquet"] = str(object_path)
        source_summary = _source_summary(frame)
        source_path = processed / "source_summary.parquet"
        write_parquet(source_summary, source_path, project_root=project_root)
        artifacts["source_summary_parquet"] = str(source_path)
    write_json(partition_results, processed / "partition_results.json", enforce_allowed_roots=False)
    write_json(accounting, processed / "completeness_accounting.json", enforce_allowed_roots=False)
    artifacts["partition_results_json"] = str(processed / "partition_results.json")
    artifacts["completeness_accounting_json"] = str(processed / "completeness_accounting.json")
    return artifacts


def build_full_night_manifest(
    run_id: str,
    base_url: str,
    config: dict[str, Any],
    partition_plan: dict[str, Any],
    partition_results: list[dict[str, Any]],
    artifacts: dict[str, str],
    decision: dict[str, Any],
) -> dict[str, Any]:
    """Build a full-night feasibility manifest."""
    return {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "base_url": base_url,
        "config": config,
        "partition_plan": partition_plan,
        "partition_summary": summarize_partition_results(partition_results),
        "artifacts": artifacts,
        "decision": decision,
        "note": "REST full-night completeness feasibility only; no Data Transfer/Kafka/Livestream/Spark/login/cutouts.",
    }


def _partition_status(partition: dict[str, Any], status: str, message: str) -> dict[str, Any]:
    return {
        "partition_id": partition.get("partition_id"),
        "endpoint": partition.get("endpoint"),
        "query_payload": partition.get("query_payload", {}),
        "requested_n": partition.get("maximum_rows_requested", 0),
        "intended_completeness": partition.get("intended_completeness"),
        "status": status,
        "success": False,
        "row_count": 0,
        "message": message,
        "raw_response_saved": False,
    }


def _skipped_for_safety(partition: dict[str, Any], total_rows: int, safety_cap: int) -> dict[str, Any]:
    result = _partition_status(partition, "skipped", "Skipped because max_total_rows_safety was reached")
    result["safety_total_rows_before_skip"] = total_rows
    result["max_total_rows_safety"] = safety_cap
    return result


def _object_summary(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty or "internal_object_id" not in frame.columns:
        return pd.DataFrame()
    grouped = frame.groupby("internal_object_id", dropna=True).agg(row_count=("internal_object_id", "size")).reset_index()
    if "internal_source_id" in frame.columns:
        source_counts = frame.groupby("internal_object_id")["internal_source_id"].nunique().reset_index(name="source_count")
        grouped = grouped.merge(source_counts, on="internal_object_id", how="left")
    if "ra" in frame.columns:
        grouped["ra"] = frame.groupby("internal_object_id")["ra"].first().values
    if "dec" in frame.columns:
        grouped["dec"] = frame.groupby("internal_object_id")["dec"].first().values
    grouped.insert(0, "internal_table_type", "object_summary")
    grouped["_unmapped_fields"] = "[]"
    return grouped


def _source_summary(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty or "internal_source_id" not in frame.columns:
        return pd.DataFrame()
    columns = [column for column in ["internal_source_id", "internal_object_id", "ra", "dec", "time_mjd", "band"] if column in frame.columns]
    summary = frame[columns].drop_duplicates(subset=["internal_source_id"]).copy()
    summary.insert(0, "internal_table_type", "source_summary")
    summary["_unmapped_fields"] = "[]"
    return summary

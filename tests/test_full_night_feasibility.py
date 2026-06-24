import pandas as pd
import pytest

from fink_lsst.completeness_capabilities import diagnose_completeness_capabilities
from fink_lsst.feasibility import decide_full_night_feasibility
from fink_lsst.full_night_extraction import (
    build_completeness_accounting,
    classify_partition_result,
    deduplicate_rows,
)
from fink_lsst.full_night_validation import validate_full_night_run
from fink_lsst.partitioning import build_partition_plan, build_time_partitions, refine_capped_partitions
from fink_lsst.time_windows import (
    build_utc_date_window,
    date_window_to_payload,
    parse_utc_date,
    split_utc_window,
    validate_alert_start_date,
)


def test_utc_date_parsing_and_pre_alert_rejection():
    assert parse_utc_date("2026-02-25").isoformat() == "2026-02-25"
    assert build_utc_date_window("2026-02-25", "2026-02-26")["timezone"] == "UTC"
    with pytest.raises(ValueError):
        validate_alert_start_date("2026-01-01", "2026-02-25")


def test_local_timezone_does_not_affect_payload_dates():
    payload = date_window_to_payload("2026-02-25", "2026-02-26")
    assert payload == {"startdate": "2026-02-25", "stopdate": "2026-02-26"}
    pieces = split_utc_window("2026-02-25", "2026-02-26", 2)
    assert pieces[0]["startdate"] == "2026-02-25"
    assert pieces[0]["stopdate"].endswith("Z")


def test_capability_diagnosis_from_synthetic_contracts():
    summary = [
        {
            "path": "/api/v1/tags",
            "method": "POST",
            "parameters": [
                {"name": "tag"},
                {"name": "startdate"},
                {"name": "stopdate"},
                {"name": "n"},
                {"name": "columns"},
            ],
            "required_parameters": [],
        },
        {
            "path": "/api/v1/sources",
            "method": "POST",
            "parameters": [{"name": "diaObjectId"}],
            "required_parameters": [{"name": "diaObjectId"}],
        },
        {
            "path": "/api/v1/statistics",
            "method": "POST",
            "parameters": [{"name": "date"}],
            "required_parameters": [],
        },
    ]
    diagnosis = diagnose_completeness_capabilities(summary)
    assert diagnosis["tag_specific_completeness"]["public_rest_enumeration_supported"] is True
    assert diagnosis["all_alert_completeness"]["public_rest_enumeration_supported"] is False
    assert diagnosis["pagination_exposed"] is False


def test_conesearch_is_regional_not_full_night_by_itself():
    diagnosis = diagnose_completeness_capabilities(
        [
            {
                "path": "/api/v1/conesearch",
                "method": "POST",
                "parameters": [
                    {"name": "ra"},
                    {"name": "dec"},
                    {"name": "radius"},
                    {"name": "n"},
                    {"name": "startdate"},
                    {"name": "stopdate"},
                ],
                "required_parameters": [],
            }
        ]
    )
    assert diagnosis["sky_region_enumeration"]["supported"] is True
    assert diagnosis["all_alert_completeness"]["public_rest_enumeration_supported"] is False


def test_partition_plan_uses_fallback_tag_not_unfiltered_full_night():
    diagnosis = diagnose_completeness_capabilities(
        [
            {
                "path": "/api/v1/tags",
                "method": "POST",
                "parameters": [{"name": "tag"}, {"name": "startdate"}, {"name": "stopdate"}, {"name": "n"}],
                "required_parameters": [],
            }
        ]
    )
    config = _config()
    plan = build_partition_plan(config, diagnosis)
    assert plan["completeness_scope"] == "tag_specific"
    assert plan["partitions"][0]["query_payload"]["tag"] == "in_tns"
    assert plan["partitions"][0]["query_payload"]["n"] == "50"


def test_time_partition_generation():
    partitions = build_time_partitions("2026-02-25", "2026-02-26", 8)
    assert len(partitions) == 8
    assert partitions[0]["timezone"] == "UTC"


def test_partition_refinement_when_caps_are_hit():
    config = _config()
    partitions = build_time_partitions("2026-02-25", "2026-02-26", 2)
    for item in partitions:
        item.update({"endpoint": "tags", "query_payload": {"tag": "in_tns", "n": "50"}, "maximum_rows_requested": 50})
    results = [{"partition_id": partitions[0]["partition_id"], "status": "possibly_truncated"}]
    refined = refine_capped_partitions(partitions, results, config)
    assert len(refined) == 4
    assert refined[0]["partition_id"].startswith("refined_")


def test_partition_result_classification():
    partition = {"partition_id": "p0", "endpoint": "tags", "query_payload": {"n": "2"}, "maximum_rows_requested": 2}
    exhausted = classify_partition_result({"ok": True, "data": [{"a": 1}]}, 2, partition)
    capped = classify_partition_result({"ok": True, "data": [{"a": 1}, {"a": 2}]}, 2, partition)
    failed = classify_partition_result({"ok": False, "error": "boom"}, 2, partition)
    assert exhausted["status"] == "exhausted"
    assert capped["status"] == "possibly_truncated"
    assert failed["status"] == "failed"


def test_deduplication_by_source_id():
    dedup = deduplicate_rows([{"r:diaSourceId": 1, "x": "a"}, {"r:diaSourceId": 1, "x": "b"}, {"r:diaSourceId": 2}])
    assert dedup["deduplicated_count"] == 2
    assert dedup["duplicate_count"] == 1
    assert dedup["dedup_key"] == "r:diaSourceId"


def test_completeness_accounting_without_direct_denominator():
    accounting = build_completeness_accounting(
        [{"status": "exhausted", "row_count": 3}],
        [{"f:night": 1, "f:alerts": 10}],
        _config(),
    )
    assert accounting["denominator_available"] is True
    assert accounting["directly_comparable"] is False
    assert accounting["completeness_fraction"] is None


def test_validation_prevents_false_completeness_claims():
    config = _config()
    plan = {"completeness_scope": "tag_specific"}
    results = [
        {
            "partition_id": "p0",
            "status": "possibly_truncated",
            "query_payload": {"n": "50"},
            "requested_n": 50,
            "row_count": 50,
            "success": True,
            "raw_response_saved": True,
        }
    ]
    checks = validate_full_night_run(config, plan, results, pd.DataFrame(), {"directly_comparable": False}, "yes")
    failed = [check for check in checks if check["check"] == "completeness_claim_validity" and not check["passed"]]
    assert failed


def test_feasibility_decision_unresolved_for_capped_tag_specific():
    decision = decide_full_night_feasibility(
        {
            "all_alert_completeness": {"public_rest_enumeration_supported": False},
            "tag_specific_completeness": {"public_rest_enumeration_supported": True},
            "pagination_exposed": False,
            "row_caps_exposed": True,
        },
        [{"status": "possibly_truncated", "intended_completeness": "tag_specific", "row_count": 50}],
        [],
        {"directly_comparable": False, "extracted_count": 50},
    )
    assert decision["rest_full_night_complete"] == "unresolved"


def test_feasibility_decision_yes_only_for_all_alert_exhausted_with_denominator():
    decision = decide_full_night_feasibility(
        {
            "all_alert_completeness": {"public_rest_enumeration_supported": True},
            "tag_specific_completeness": {"public_rest_enumeration_supported": True},
            "pagination_exposed": True,
            "row_caps_exposed": True,
        },
        [{"status": "exhausted", "intended_completeness": "all_alert", "row_count": 2}],
        [],
        {"directly_comparable": True, "extracted_count": 2},
    )
    assert decision["rest_full_night_complete"] == "yes"


def _config():
    return {
        "target_startdate": "2026-02-25",
        "target_stopdate": "2026-02-26",
        "timezone": "UTC",
        "api_date_window_convention": "UTC calendar dates; stopdate assumed exclusive unless Fink contract proves inclusive",
        "min_lsst_alert_date_utc": "2026-02-25",
        "reject_pre_alert_dates": True,
        "target_tag": None,
        "fallback_tag": "in_tns",
        "output_format": "json",
        "max_rows_per_partition": 50,
        "max_total_rows_safety": 5000,
        "allow_unfiltered_full_night_query": False,
        "time_partitions": {"initial_count": 8, "max_count": 96},
        "partition_strategy": "auto",
    }

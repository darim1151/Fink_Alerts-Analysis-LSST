import pandas as pd

from fink_lsst.bounded_extraction import build_tag_query_payload, detect_pagination_or_truncation
from fink_lsst.bounded_validation import validate_bounded_extraction
from fink_lsst.endpoint_capabilities import analyze_endpoint_capabilities, capability_by_endpoint
from fink_lsst.feasibility import generate_feasibility_report
from fink_lsst.normalize import normalize_sources


def test_endpoint_capability_analysis_from_synthetic_contracts():
    summary = [
        {
            "path": "/api/v1/tags",
            "method": "POST",
            "summary": "tags",
            "parameters": [
                {"name": "tag", "in": "query"},
                {"name": "n", "in": "query"},
                {"name": "startdate", "in": "query"},
            ],
            "required_parameters": [],
        },
        {
            "path": "/api/v1/objects",
            "method": "POST",
            "summary": "objects",
            "parameters": [{"name": "diaObjectId", "in": "query"}],
            "required_parameters": [],
        },
    ]
    capabilities = analyze_endpoint_capabilities(summary)
    by_endpoint = capability_by_endpoint(capabilities)
    assert by_endpoint["tags"]["supports_row_limit"]
    assert by_endpoint["tags"]["supports_date_or_night_filter"]
    assert by_endpoint["objects"]["requires_diaObjectId"]


def test_build_tag_query_payload_is_bounded():
    config = {
        "tag": "in_tns",
        "startdate": "2026-01-01",
        "stopdate": "2026-01-02",
        "max_rows": 12,
        "columns": {"tag_rows": "r:diaObjectId"},
        "output_format": "json",
        "fail_on_unbounded_query": True,
    }
    payload = build_tag_query_payload(config, {"supports_row_limit": True})
    assert payload["n"] == "12"
    assert payload["tag"] == "in_tns"
    assert payload["columns"] == "r:diaObjectId"


def test_detect_truncation_when_hit_limit():
    result = detect_pagination_or_truncation([{"a": 1}, {"a": 2}], {"n": "2"}, {"supports_pagination_or_continuation": False})
    assert result["hit_requested_limit"] is True
    assert result["completeness_claim"] is False


def test_normalize_multiple_payload_lists():
    df = normalize_sources([[{"r:diaObjectId": 1}], [{"r:diaObjectId": 2}]])
    assert len(df) == 2
    assert set(df["internal_object_id"]) == {1, 2}


def test_bounded_validation_and_feasibility():
    table = pd.DataFrame(
        {
            "internal_table_type": ["sources"],
            "internal_object_id": [1],
            "internal_source_id": [2],
            "ra": [10.0],
            "dec": [0.0],
            "_unmapped_fields": ["[]"],
        }
    )
    checks = validate_bounded_extraction(
        {"sources": table},
        {"tag_rows": 1, "unique_object_ids": 1, "detail_attempts": {"objects": {"attempted": 1, "succeeded": 1}}},
        [{"name": "bounded_tag_sample", "truncation": {"hit_requested_limit": False}}],
    )
    report = generate_feasibility_report(
        {"tag_rows": 1, "unique_object_ids": 1, "detail_attempts": {"objects": {"attempted": 1, "succeeded": 1}}},
        checks,
        [],
    )
    assert report["public_rest_minimal_lookup"] == "yes"
    assert report["public_rest_complete_full_night_all_alert_extraction"] == "unresolved"

from fink_lsst.id_discovery import (
    extract_candidate_ids_from_payload,
    merge_candidate_ids,
    select_best_candidate_id,
    summarize_candidate_ids,
)


def test_extract_candidate_ids_from_nested_payload():
    payload = {
        "rows": [
            {"r:diaObjectId": 12345, "r:diaSourceId": 67890},
            {"diaObjectId": "222", "other": {"sourceId": "src-1"}},
        ]
    }
    candidates = extract_candidate_ids_from_payload(payload, origin="synthetic")
    assert {item["value"] for item in candidates["diaObjectId"]} == {"12345", "222"}
    assert candidates["diaSourceId"][0]["value"] == "67890"
    assert candidates["other_source_ids"][0]["value"] == "src-1"


def test_merge_select_and_summarize_candidates():
    first = extract_candidate_ids_from_payload({"diaObjectId": 10}, origin="a")
    second = extract_candidate_ids_from_payload({"diaObjectId": 10, "diaSourceId": 99}, origin="b")
    merged = merge_candidate_ids([first, second])
    assert select_best_candidate_id(merged, "object")["value"] == "10"
    assert select_best_candidate_id(merged, "source")["value"] == "99"
    summary = summarize_candidate_ids(merged)
    assert any(row["id_type"] == "diaObjectId" for row in summary)

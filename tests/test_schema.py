from pathlib import Path

import pytest

from fink_lsst.schema import (
    infer_internal_concept_map,
    internal_data_model_table,
    summarize_schema_fields,
    summarize_tags,
)
from fink_lsst.storage import read_json


def test_summarize_nested_fink_schema():
    payload = {
        "ZTF original fields (i:)": {
            "objectId": {"type": "string", "doc": "Object identifier"},
            "ra": {"type": "double", "doc": "Right ascension"},
        },
        "Fink science module outputs (d:)": {
            "classification": {"type": "string", "doc": "Class label"},
        },
    }
    frame = summarize_schema_fields(payload)
    assert set(frame["field"]) == {"objectId", "ra", "classification"}
    assert "family" in frame.columns


def test_infer_internal_concept_map_includes_core_fields():
    mapping = infer_internal_concept_map(["i:objectId", "i:candid", "i:ra", "i:dec", "i:jd"])
    assert "i:objectId" in mapping["object_id"]
    assert "i:candid" in mapping["source_id"]
    assert "i:ra" in mapping["ra"]


def test_summarize_tags_from_dict_and_list():
    frame = summarize_tags({"valid": "good", "upper": {"description": "upper limit"}})
    assert set(frame["tag"]) == {"valid", "upper"}
    frame = summarize_tags([{"name": "SN"}, "unknown"])
    assert set(frame["tag"]) == {"SN", "unknown"}


def test_internal_data_model_table():
    table = internal_data_model_table({"object_id": ["diaObjectId"], "ra": ["ra"]})
    assert set(table["internal_concept"]) == {"object_id", "ra"}


def test_schema_fixture_parses_if_present():
    fixtures = sorted(Path("data/fixtures/schema").glob("fink_lsst_schema_*.json"))
    if not fixtures:
        pytest.skip("No schema fixture captured yet")
    frame = summarize_schema_fields(read_json(fixtures[0]))
    assert "field" in frame.columns


def test_tags_fixture_parses_if_present():
    fixture = Path("data/fixtures/fink_lsst_tags.json")
    if not fixture.exists():
        pytest.skip("No tags fixture captured yet")
    frame = summarize_tags(read_json(fixture))
    assert "tag" in frame.columns

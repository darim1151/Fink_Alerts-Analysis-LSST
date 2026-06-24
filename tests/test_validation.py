import pandas as pd

from fink_lsst.validation import (
    check_coordinate_ranges,
    check_duplicate_keys,
    check_non_empty,
    check_required_columns,
    validate_normalized_table,
    validate_basic_dataframe,
)


def test_validation_success_cases():
    df = pd.DataFrame(
        {
            "i:objectId": ["a", "b"],
            "i:ra": [10.0, 359.9],
            "i:dec": [-1.0, 45.0],
            "i:jd": [2459000.0, 2459001.0],
        }
    )
    assert check_non_empty(df)["passed"]
    assert check_required_columns(df, ["i:objectId"])["passed"]
    assert all(item["passed"] for item in check_coordinate_ranges(df))
    assert check_duplicate_keys(df, ["i:objectId"])["passed"]
    assert all(item["passed"] for item in validate_basic_dataframe(df, ["i:objectId"], ["i:objectId"]))


def test_validation_detects_problems():
    df = pd.DataFrame(
        {
            "i:objectId": ["a", "a"],
            "i:ra": [10.0, 361.0],
            "i:dec": [-91.0, 45.0],
        }
    )
    coord_checks = check_coordinate_ranges(df)
    assert any(not item["passed"] for item in coord_checks)
    assert not check_duplicate_keys(df, ["i:objectId"])["passed"]
    assert not check_required_columns(df, ["missing"])["passed"]


def test_validate_normalized_table_sources():
    df = pd.DataFrame(
        {
            "internal_table_type": ["sources"],
            "internal_object_id": [1],
            "internal_source_id": [2],
            "ra": [10.0],
            "dec": [20.0],
            "_unmapped_fields": ["[]"],
        }
    )
    checks = validate_normalized_table(df, "sources")
    assert all(check["passed"] for check in checks if check["severity"] == "error")
    assert any(check["check"] == "id_presence" and check["passed"] for check in checks)

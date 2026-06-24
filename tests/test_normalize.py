import pandas as pd

from fink_lsst.normalize import (
    normalize_forced_photometry,
    normalize_objects,
    normalize_sources,
    normalize_statistics,
    normalize_tags_or_classifications,
)


def test_normalize_sources_adds_internal_columns():
    payload = [{"r:diaObjectId": 1, "r:diaSourceId": 2, "r:ra": 10.0, "r:dec": -5.0, "r:band": "g"}]
    df = normalize_sources(payload)
    assert df.loc[0, "internal_object_id"] == 1
    assert df.loc[0, "internal_source_id"] == 2
    assert df.loc[0, "ra"] == 10.0
    assert "_unmapped_fields" in df.columns


def test_normalize_objects_and_fp():
    object_df = normalize_objects({"diaObjectId": 1, "ra": 10, "dec": 5})
    fp_df = normalize_forced_photometry([{"diaObjectId": 1, "diaForcedSourceId": 4, "psfFlux": 2.5}])
    assert object_df.loc[0, "internal_object_id"] == 1
    assert fp_df.loc[0, "internal_source_id"] == 4
    assert fp_df.loc[0, "flux"] == 2.5


def test_normalize_tags_and_statistics():
    tags = normalize_tags_or_classifications({"in_tns": {"description": "known in TNS"}})
    stats = normalize_statistics([{"f:night": "20260101", "f:alerts": "10"}])
    assert tags.loc[0, "tag"] == "in_tns"
    assert stats.loc[0, "f:night"] == "20260101"


def test_normalize_empty_payload():
    df = normalize_sources([])
    assert isinstance(df, pd.DataFrame)
    assert df.empty
    assert df.attrs["diagnostics"]["empty"] is True

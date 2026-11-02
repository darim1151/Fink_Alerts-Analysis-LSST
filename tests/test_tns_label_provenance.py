import importlib.util
import json
from pathlib import Path

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "tns_label_provenance.py"


def load_module():
    spec = importlib.util.spec_from_file_location("tns_label_provenance", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def xm_struct(names, types, zs, with_name=True):
    fields = {"tns_type": pa.array(types, pa.string()), "tns_redshift": pa.array(zs, pa.float32()),
              "simbad_otype": pa.array(["x"] * len(types), pa.string())}
    if with_name:
        fields = {"tns_fullname": pa.array(names, pa.string()), **fields}
    return pa.StructArray.from_arrays(list(fields.values()), list(fields))


def write_fixture(tmp_path):
    a = pa.table({"diaObjectId": pa.array([1, 1, 2, 3, 0], pa.int64()), "diaSourceId": pa.array([10, 11, 20, 30, 99], pa.int64()),
                  "midpointMjdTai": [61100.0, 61101.0, 61100.5, 61102.0, 61100.0], "band": ["i", "r", "g", "i", "r"],
                  "fink_broker_version": ["4.1"] * 5,
                  "tns_type_recomputed": ["(TNS) SN Ia", "Unknown", None, "(TNS) SN II", None],
                  "xm": xm_struct(["SN 2026a", None, None, None, None], ["SN Ia", None, None, None, None], [0.1, None, None, None, None]),
                  "pred": pa.StructArray.from_arrays([pa.array([False, False, False, False, True])], ["is_sso"])})
    b = pa.table({"diaObjectId": pa.array([2, 1], pa.int64()), "diaSourceId": pa.array([21, 12], pa.int64()),
                  "midpointMjdTai": [61103.0, 61104.0], "band": ["i", "i"], "fink_broker_version": ["5.0rc0"] * 2,
                  "xm": xm_struct(None, ["SN II", None], [None, None], with_name=False),
                  "pred": pa.StructArray.from_arrays([pa.array([False, False])], ["is_sso"])})
    pq.write_table(a, tmp_path / "a.parquet")
    pq.write_table(b, tmp_path / "b.parquet")
    cat = tmp_path / "catalog.duckdb"
    con = duckdb.connect(str(cat))
    files = [str(tmp_path / "a.parquet"), str(tmp_path / "b.parquet")]
    con.execute("CREATE VIEW sources AS SELECT CASE WHEN pred.is_sso = false AND diaObjectId > 0 THEN diaObjectId END::BIGINT "
                f"AS dia_object_id, filename::VARCHAR AS raw_file FROM read_parquet({files!r}, union_by_name=true, filename=true)")
    con.close()
    return cat


def test_alert_label_follows_the_science_suite_rule():
    m = load_module()
    assert m.alert_label("(TNS) SN Ia", "SN II") == ("SN Ia", "tns_type_recomputed")
    assert m.alert_label("Unknown", "SN II") == ("SN II", "xm.tns_type")
    assert m.alert_label(None, None) == (None, None)


def test_extract_is_read_only_and_complete(tmp_path):
    m = load_module()
    cat = write_fixture(tmp_path)
    before = cat.read_bytes()
    res = m.extract(cat, ["1", "2", "4"])
    meta = res["meta"]
    assert cat.read_bytes() == before
    assert meta["n_files"] == 2 and meta["n_catalog_sources"] == 5 and meta["dia_rows_equal_catalog"]
    assert meta["sids_unique"] and meta["ids_without_sources"] == [4]
    assert meta["files_missing_field"] == {"tns_type_recomputed": 1, "xm.tns_fullname": 1}
    assert sorted(r["sid"] for r in res["rows"]) == [10, 11, 12, 20, 21]
    json.dumps(res)


def test_summarise_traces_each_label(tmp_path):
    m = load_module()
    cat = write_fixture(tmp_path)
    (tmp_path / "x.json").write_text(json.dumps(m.extract(cat, ["1", "2", "3"])))
    pd.DataFrame({"dia_object_id": [1, 2, 3], "tns_type": ["SN Ia", "SN II", "SN II"], "confirmed_by_api": [True, False, False],
                  "api_alerts_in_window": [3, 2, 5], "group_size": [1, 2, 2], "gaia_category": ["No Gaia source"] * 3}
                 ).to_csv(tmp_path / "labels.csv", index=False)
    s = m.summarise(tmp_path / "x.json", tmp_path / "labels.csv", tmp_path / "out")
    per = pd.read_csv(tmp_path / "out" / "per_object.csv").set_index("dia_object_id")
    # object 1: last alert (b.parquet) has no label, so the last non-null is the first alert's recomputed SN Ia
    assert per.loc[1, "derived_tns_type"] == "SN Ia" and per.loc[1, "derived_from"] == "tns_type_recomputed"
    assert per.loc[1, "n_with_tns_name"] == 1 and per.loc[1, "n_recomputed_unknown"] == 1
    assert per.loc[2, "derived_from"] == "xm.tns_type" and per.loc[2, "n_with_tns_name"] == 0
    assert per.loc[3, "derived_tns_type"] == "SN II" and per.loc[3, "derived_from"] == "tns_type_recomputed"
    assert s["derived_equals_notebook"] == 3
    assert s["unconfirmed"]["label_from"] == {"xm.tns_type": 1, "tns_type_recomputed": 1}
    assert s["confirmed"]["delivered_alerts_equal_api_in_window"] == 1
    with pytest.raises(FileExistsError):
        m.summarise(tmp_path / "x.json", tmp_path / "labels.csv", tmp_path / "out")

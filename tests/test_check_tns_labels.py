import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "check_tns_labels.py"


def load_module():
    spec = importlib.util.spec_from_file_location("check_tns_labels", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_names_only_count_inside_the_window():
    m = load_module()
    recs = [{"r:midpointMjdTai": 61100.0, "f:xm_tns_fullname": "SN 2026abc"},
            {"r:midpointMjdTai": 61100.5, "f:xm_tns_fullname": "nan"},
            {"r:midpointMjdTai": 61100.6, "f:xm_tns_fullname": None},
            {"r:midpointMjdTai": 61300.0, "f:xm_tns_fullname": "SN 2026zzz"}]
    assert m.tns_names(recs) == ["SN 2026abc"]
    assert m.tns_names(recs[1:]) == []


def test_groups_join_same_label_within_radius():
    m = load_module()
    ra = [10.0, 10.0 + 5 / 60 / np.cos(np.radians(-30)), 10.0 + 9 / 60 / np.cos(np.radians(-30)), 10.0, 50.0]
    dec = [-30.0, -30.0, -30.0, -30.0 + 1 / 3600, -30.0]
    gid, size, span = m.groups(ra, dec, ["Ia", "Ia", "Ia", "II", "Ia"], 6.0)
    assert list(size) == [3, 3, 3, 1, 1]                          # friends of friends: 0-1-2 chained, 3 differs in type
    assert gid[0] == gid[1] == gid[2] != gid[3] and span[0] == pytest.approx(9.0, abs=0.01)


def test_run_with_a_fake_api(tmp_path):
    m = load_module()
    ids = np.array([11, 12, 13, 14], dtype=np.int64)
    pd.DataFrame({"dia_object_id": np.arange(10, 16, dtype=np.int64),
                  "tns_type": [None, "SN Ia", "SN Ia", "SN II", "SLSN-I", "AGN"], "tns_z": np.nan,
                  "field": "Low-dust WFD"}).to_parquet(tmp_path / "science.parquet", index=False)
    pd.DataFrame({"dia_object_id": np.arange(10, 16, dtype=np.int64), "ra_deg": [1, 20, 20.02, 40, 60, 80.0],
                  "dec_deg": 0.0}).to_parquet(tmp_path / "objects.parquet", index=False)
    pd.DataFrame({"dia_object_id": ids, "category_r_star": ["No Gaia source", "Gaia star", "No Gaia source",
                                                            "No Gaia source"], "nearest_sep_arcsec": np.nan}
                 ).to_parquet(tmp_path / "cp.parquet", index=False)
    named = {11: "SN 2026a", 13: "SN 2026b", 14: "SN 2026c"}

    def fake(oid):
        return [{"r:diaSourceId": 1, "r:midpointMjdTai": 61150.0, "f:xm_tns_fullname": named.get(oid),
                 "f:xm_tns_type": None, "f:fink_broker_version": "5.0rc0"}]

    s = m.run(tmp_path / "science.parquet", tmp_path / "objects.parquet", tmp_path / "out",
              counterparts=tmp_path / "cp.parquet", fetch=fake, pause=0)
    assert s["n_labelled"] == 4 and s["n_confirmed"] == 3 and s["n_in_groups"] == 2 and s["n_groups"] == 1
    assert s["n_confirmed_in_groups"] == 1 and s["gaia_category_by_confirmation"]["False"]["Gaia star"] == 1
    t = pd.read_csv(tmp_path / "out" / "tns_labels.csv")
    assert list(t.confirmed_by_api) == [True, False, True, True] and json.loads((tmp_path / "out" / "summary.json").read_text())

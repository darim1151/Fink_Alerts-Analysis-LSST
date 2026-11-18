"""Offline tests of the classifier splits in scripts/edp2_split_figures.py."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import edp2_split_figures as sf  # noqa: E402

AB = 31.4


def flux(m):
    return 10 ** ((AB - m) / 2.5)


def test_three_bins_edges_are_inclusive_in_the_middle():
    v = [0.0, 0.0199, 0.02, 0.035, 0.05, 0.0501, np.nan, np.inf]
    assert sf.three_bins(v, (0.02, 0.05)).tolist() == [0, 0, 1, 1, 1, 2, -1, -1]
    assert sf.three_bins([0.19, 0.2, 0.8, 0.81], (0.2, 0.8)).tolist() == [0, 1, 1, 2]


def table():
    rows = []
    for k, (field, sep, x, G, cm_flag, snr_i) in enumerate([
        ("Low-dust WFD", 0.1, 0.0, 0.05, False, 20),        # 1 point-like by both
        ("Low-dust WFD", 0.1, 0.03, 0.5, False, 20),        # 2 intermediate by both
        ("Low-dust WFD", 0.1, 0.30, 0.95, False, 20),       # 3 extended by both
        ("Galactic bulge", 0.1, 0.0, 0.05, False, 20),      # 4 dusty field: out
        ("Low-dust WFD", 0.7, 0.0, 0.05, False, 20),        # 5 beyond 0.5": out
        ("Low-dust WFD", 0.1, 0.0, 0.05, True, 20),         # 6 cModel flagged: out
        ("Low-dust WFD", 0.1, 0.0, np.nan, False, 20),      # 7 no griz_model_extendedness: out
        ("Low-dust WFD", 0.1, 0.0, 0.05, False, 3),         # 8 i S/N < 5: out
    ]):
        r = {"dia_object_id": k + 1, "field": field, "sep_arcsec": sep, "griz_model_extendedness": G,
             "r_cModel_flag": cm_flag, "r_extendedness": 0.0, "r_extendedness_flag": False}
        for b, m in (("g", 20.5), ("r", 20.0), ("i", 19.8)):
            r[f"{b}_psfFlux"] = flux(m)
            r[f"{b}_psfFluxErr"] = flux(m) / (snr_i if b == "i" else 20)
            r[f"{b}_psfFlux_flag"] = False
        r["r_cModelFlux"] = flux(20.0 - x)        # r_psfMag - r_cModelMag = x
        rows.append(r)
    return pd.DataFrame(rows)


def test_common_sample_rules_and_values():
    s, steps = sf.common_sample(table())
    assert s.dia_object_id.tolist() == [1, 2, 3]
    assert list(steps.values()) == [7, 6, 5, 4, 3]
    assert s.x.to_numpy() == pytest.approx([0.0, 0.03, 0.30], abs=1e-9)
    assert s.xbin.tolist() == [0, 1, 2] and s.gbin.tolist() == [0, 1, 2]
    assert s.gr.to_numpy() == pytest.approx([0.5] * 3) and s.ri.to_numpy() == pytest.approx([0.2] * 3)

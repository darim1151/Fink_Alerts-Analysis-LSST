"""Offline tests of the photometry swaps in scripts/edp2_paper_figures_v2.py."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import edp2_paper_figures_v2 as v2  # noqa: E402


def flux(m):
    return 10 ** ((31.4 - m) / 2.5)


def tables():
    one = pd.DataFrame({"dia_object_id": [1, 2, 3, 4], "sep_arcsec": [0.1, 0.9, 1.5, np.nan]})
    cm = {"dia_object_id": [1, 2, 3], "objectId": [11, 12, 13]}
    for b in v2.BANDS:
        cm[f"{b}_cModelFlux"] = [flux(20.0), flux(21.0), flux(19.0)]
        cm[f"{b}_cModelFluxErr"] = [flux(20.0) / 50, flux(21.0) / 3, flux(19.0) / 50]   # object 2: S/N 3
        cm[f"{b}_cModel_flag"] = [False, False, False]
    tp = pd.DataFrame({"dia_object_id": [1, 2, 3, 4], "field": ["Low-dust WFD"] * 4})
    for b in v2.BANDS:
        tp[f"ok_{b}"] = [True, True, True, True]
        tp[f"mag_{b}"] = [18.0, 18.5, 19.5, 22.0]
    return one, pd.DataFrame(cm), tp


def test_edp2_bands_match_radius_and_snr():
    one, cm, _ = tables()
    e = v2.edp2_bands(one, cm)
    assert e.matched.tolist() == [True, True, False, False]       # 1.5" is beyond 1"; 4 has no match
    assert e.ok_g.tolist() == [True, False, False, False]          # 2 fails S/N >= 5
    assert e.mag_r.iloc[0] == pytest.approx(20.0) and np.isnan(e.mag_r.iloc[1])


def test_swap_unmatched_and_hybrid():
    one, cm, tp = tables()
    e = v2.edp2_bands(one, cm)
    s = v2.swap_to_edp2(tp, e)
    assert s.panel_a.tolist() == [True, False, False, False] and s.gr.iloc[0] == pytest.approx(0.0)
    u = v2.only_unmatched(tp, e)
    assert u.dia_object_id.tolist() == [3, 4] and u.mag_g.tolist() == [19.5, 22.0]   # template values kept
    mag, use_e, use_t = v2.hybrid(tp, e, "i")
    assert use_e.tolist() == [True, False, False, False] and use_t.tolist() == [False, True, True, True]
    assert mag.tolist() == pytest.approx([20.0, 18.5, 19.5, 22.0])   # 2 falls back to its template


def test_red_flags_recomputed():
    one, cm, tp = tables()
    s = v2.swap_to_edp2(tp, v2.edp2_bands(one, cm))
    s.loc[0, "mag_g"], s.loc[0, "mag_i"] = 23.0, 20.5
    s = v2.red_flags(v2.recolour(s), 2.0)
    assert s.red.tolist() == [True, False, False, False]

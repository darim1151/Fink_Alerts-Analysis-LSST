"""Offline tests of the EDP2 figure selections in scripts/edp2_figures.py (synthetic tables only)."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import edp2_figures as ef  # noqa: E402


def mag_to_flux(m):
    return 10 ** ((ef.AB_ZP - m) / 2.5)


def one_table():
    """Eight DIAObjects that exercise every selection rule."""
    rows = []
    base = dict(matched=True, r_extendedness=0.0, r_extendedness_flag=False, field="Low-dust WFD")
    mags = dict(g=20.5, r=20.0, i=19.8)
    for k, over in enumerate([
        {},                                                  # 1 ok, unresolved
        {"r_extendedness": 1.0},                             # 2 ok, resolved
        {"field": "Galactic bulge"},                         # 3 dusty field -> not in F6
        {"matched": False},                                  # 4 no match within the radius
        {"r_extendedness_flag": True},                       # 5 flagged extendedness
        {"r_extendedness": np.nan, "r_extendedness_flag": np.nan, "matched": False},  # 6 unmatched
        {"snr_i": 4.0},                                      # 7 i-band S/N < 5 -> not in F6
        {"g_flag": True},                                    # 8 g flagged -> not in F6
    ]):
        r = dict(base, dia_object_id=k + 1, **{x: y for x, y in over.items() if x not in ("snr_i", "g_flag")})
        for b in "gri":
            f = mag_to_flux(mags[b])
            snr = over.get("snr_i", 20.0) if b == "i" else 20.0
            r[f"{b}_psfFlux"], r[f"{b}_psfFluxErr"] = f, f / snr
            r[f"{b}_psfFlux_flag"] = bool(over.get("g_flag", False)) if b == "g" else False
        rows.append(r)
    return pd.DataFrame(rows)


def test_flag_treats_missing_as_set():
    s = pd.Series([True, False, np.nan], dtype=object)
    assert ef.flag(s).tolist() == [True, False, True]


def test_ext_class_rules():
    c = ef.ext_class(one_table())
    assert c.iloc[0] == 0 and c.iloc[1] == 1
    assert np.isnan(c.iloc[3]) and np.isnan(c.iloc[4]) and np.isnan(c.iloc[5])


def test_select_f6_rules_and_colours():
    s = ef.select_f6(one_table())
    assert s.dia_object_id.tolist() == [1, 2]
    assert s.cls.tolist() == [0, 1]
    assert s.gr.to_numpy() == pytest.approx([0.5, 0.5], abs=1e-9)    # g - r = 20.5 - 20.0
    assert s.ri.to_numpy() == pytest.approx([0.2, 0.2], abs=1e-9)    # r - i = 20.0 - 19.8


def test_select_f9_keeps_c1_sample_rule_and_needs_usable_class():
    one = one_table()
    sci = pd.DataFrame({"dia_object_id": range(1, 9), "panel_a": [True, True, True, True, True, False, False, False],
                        "panel_b": [False, False, False, False, False, True, False, True],
                        "panel_c": [True, True, True, True, True, False, False, False]})
    f9 = ef.select_f9(one, sci)
    # 1, 2, 3 (dusty field allowed: all fields) have usable classes; 4, 5, 6 do not; 7 has no panel; 8 panel_b only
    assert sorted(f9.dia_object_id) == [1, 2, 3, 8]
    cnt = ef.recount(ef.select_f6(one), f9)
    assert cnt["f9_total"] == 4 and cnt["f9_total_resolved"] == 1 and cnt["f9_total_unresolved"] == 3
    assert cnt["f9_a_n"] == 3 and cnt["f9_c_unresolved"] == 1
    assert cnt["f6_unresolved"] == 1 and cnt["f6_resolved"] == 1


def test_psf_mag_zero_point():
    assert ef.psf_mag([3631e9])[0] == pytest.approx(0.0, abs=2e-3)   # 3631 Jy is AB = 0 (31.4 zero point in nJy)
    assert np.isnan(ef.psf_mag([-1.0])[0])


def test_select_f9_edp2_uses_edp2_colours_for_the_same_objects():
    one = one_table()
    for b in "u":
        one[f"{b}_psfFlux"], one[f"{b}_psfFluxErr"], one[f"{b}_psfFlux_flag"] = mag_to_flux(21.5), mag_to_flux(21.5) / 20, False
    sci = pd.DataFrame({"dia_object_id": range(1, 9), "panel_a": [True] * 5 + [False] * 3,
                        "panel_b": [False] * 5 + [True, False, True], "panel_c": [True] * 5 + [False] * 3})
    e = ef.select_f9_edp2(one, sci)
    # same objects as select_f9 ([1, 2, 3, 8]) except 8, whose EDP2 g flux is flagged, so no panel can use it
    assert sorted(e.dia_object_id) == [1, 2, 3]
    r = e.set_index("dia_object_id")
    assert r.loc[1, "gr"] == pytest.approx(0.5) and r.loc[1, "ri"] == pytest.approx(0.2)
    assert r.loc[1, "gi"] == pytest.approx(0.7) and r.loc[1, "ug"] == pytest.approx(1.0)
    assert r.loc[1, "mag_r"] == pytest.approx(20.0)

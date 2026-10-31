import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "select_image_targets.py"


def load_module():
    spec = importlib.util.spec_from_file_location("select_image_targets", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def synthetic_table(n=4000, seed=7):
    """Same columns as scripts/export_colour_plot_table.py writes."""
    rng = np.random.default_rng(seed)
    t = pd.DataFrame({
        "dia_object_id": [str(170000000000000000 + 7919 * i) for i in range(n)],
        "ra_deg": rng.uniform(0, 360, n),
        "dec_deg": rng.uniform(-60, 5, n),
        "field": rng.choice(["Deep drilling fields", "Low-dust WFD", "M49 field", "Galactic bulge", "Galactic plane"], n),
    })
    for b in "ugrizy":
        t[f"n_alerts_{b}"] = rng.integers(0, 3, n)
        mag = rng.uniform(15, 23, n)
        mag[rng.random(n) < 0.3] = np.nan
        t[f"mag_{b}"] = mag
        t[f"magerr_{b}"] = np.where(np.isnan(mag), np.nan, 0.01)
    t["fink_cats_code"] = rng.choice([11, 12, 13, 21, 22], n)
    t["fink_cats_label"] = t.fink_cats_code.map({11: "SN-like", 12: "Fast", 13: "Long", 21: "Periodic", 22: "non-Periodic (AGN)"})
    t["simbad_otype"] = None
    t["simbad_group"] = rng.choice(["Star", "QSO / AGN", "Galaxy", "No SIMBAD object within 1″"], n)
    t["legacy_pstar"] = np.where(rng.random(n) < 0.5, rng.uniform(0, 1, n), np.nan)
    t["gaia_parallax_mas"] = np.where(rng.random(n) < 0.5, rng.uniform(0, 3, n), np.nan)
    t["gaia_parallax_err_mas"] = np.where(t.gaia_parallax_mas.notna(), 0.2, np.nan)
    return t


def test_groups_are_exclusive_and_meet_their_definitions():
    m = load_module()
    t = synthetic_table()
    sel, pools = m.select(t, per_group=5, seed=1, max_r=21.0)
    assert list(sel.columns) == m.OUTPUT_COLUMNS
    assert sel.dia_object_id.is_unique and sel.name.is_unique
    assert sel.groupby("group").size().max() <= 5
    assert set(sel.group) == set(pools)
    assert not sel.field.isin(m.DUSTY_FIELDS).any()
    assert (sel.r_tmpl < 21.0).all()
    g = {name: rows for name, rows in sel.groupby("group")}
    stars = g["Gaia parallax star, g - i <= 2"]
    assert (stars.gaia_parallax_snr > 5).all() and (stars.g_i_tmpl <= 2).all()
    assert (g["Red, g - i > 2"].g_i_tmpl > 2).all()
    assert (g["SIMBAD QSO / AGN"].simbad_group == "QSO / AGN").all()
    assert (g["SIMBAD galaxy"].simbad_group == "Galaxy").all()
    blue = g["Blue u - g < 0.6, no Gaia parallax"]
    assert (blue.u_g_tmpl < 0.6).all() and not (blue.gaia_parallax_snr > 5).any()
    sn = g["SN-like, no SIMBAD match, Legacy pstar < 0.5"]
    assert (sn.fink_cats_label == "SN-like").all() and (sn.simbad_group == m.NO_SIMBAD).all() and (sn.legacy_pstar < 0.5).all()


def test_choice_is_lowest_hash_and_independent_of_row_order():
    m = load_module()
    t = synthetic_table()
    a, _ = m.select(t, per_group=5, seed=3)
    b, _ = m.select(t.sample(frac=1.0, random_state=11), per_group=5, seed=3)
    pd.testing.assert_frame_equal(a.reset_index(drop=True), b.reset_index(drop=True))
    c, _ = m.select(t, per_group=5, seed=4)
    assert set(a.dia_object_id) != set(c.dia_object_id)
    # the first group takes the lowest-ranked qualifying objects
    t2 = m.add_colours(t)
    mask = list(m.group_masks(t2, 21.0).values())[0]
    ranked = sorted(t2[mask].dia_object_id, key=lambda i: m.rank_key(3, i))[:5]
    assert list(a[a.name.str.startswith("G1-")].dia_object_id) == ranked


def test_main_writes_new_run_and_refuses_existing(tmp_path):
    m = load_module()
    data = tmp_path / "fink_dia_template_colours.csv"
    synthetic_table().to_csv(data, index=False)
    out = tmp_path / "run"
    m.main(["--data", str(data), "--out", str(out), "--code-sha", "test"])
    summary = json.loads((out / "summary.json").read_text())
    radec = pd.read_csv(out / "image_targets_radec.csv", dtype={"name": str})
    full = pd.read_csv(out / "image_targets.csv", dtype={"dia_object_id": str})
    assert list(radec.columns) == ["name", "ra_deg", "dec_deg"]
    assert len(radec) == len(full) == summary["selected"]
    assert full.dia_object_id.str.fullmatch(r"\d{18}").all()
    with pytest.raises(FileExistsError):
        m.main(["--data", str(data), "--out", str(out), "--code-sha", "test"])

import hashlib
import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("astropy")

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "analyse_gaia_counterparts.py"


def load_module():
    spec = importlib.util.spec_from_file_location("analyse_gaia_counterparts", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def gaia_rows(n, kind, rng):
    """Gaia-like columns for n sources of one kind: 'star', 'qso' or 'other'."""
    d = {"parallax": np.full(n, np.nan), "parallax_error": np.full(n, np.nan), "pmra": np.full(n, np.nan),
         "pmra_error": np.full(n, np.nan), "pmdec": np.full(n, np.nan), "pmdec_error": np.full(n, np.nan),
         "phot_g_mean_mag": rng.uniform(16, 20.5, n).astype("float32"), "ruwe": np.full(n, np.nan),
         "astrometric_params_solved": np.full(n, 3, dtype=np.int16),
         "classprob_dsc_combmod_star": np.full(n, np.nan), "classprob_dsc_combmod_quasar": np.full(n, np.nan),
         "classprob_dsc_combmod_galaxy": np.full(n, np.nan), "in_qso_candidates": np.zeros(n, dtype=np.int16),
         "in_galaxy_candidates": np.zeros(n, dtype=np.int16)}
    if kind in ("star", "qso"):
        d["astrometric_params_solved"][:] = 31
        d["ruwe"][:] = 1.0
        d["parallax_error"][:] = d["pmra_error"][:] = d["pmdec_error"][:] = 0.2
        d["parallax"][:] = 2.0 if kind == "star" else 0.0
        d["pmra"][:] = 0.0
        d["pmdec"][:] = 0.0
        d["classprob_dsc_combmod_quasar"][:] = 0.0 if kind == "star" else 0.95
    return pd.DataFrame(d)


def offsets(ra, dec, r_arcsec, theta):
    return (ra + r_arcsec * np.sin(theta) / np.cos(np.radians(dec)) / 3600.0) % 360.0, dec + r_arcsec * np.cos(theta) / 3600.0


def write_run(path, up, matches, shift, objects_sha, radius=5.0):
    path.mkdir(parents=True)
    matches.to_parquet(path / "matches.parquet", index=False)
    up.to_parquet(path / "uploaded.parquet", index=False)
    files = {f: hashlib.sha256((path / f).read_bytes()).hexdigest() for f in ("matches.parquet", "uploaded.parquet")}
    (path / "summary.json").write_text(json.dumps({
        "run": path.name, "code_sha": "test", "objects_sha256": objects_sha, "radius_arcsec": radius,
        "shift_arcsec_north": shift, "sample_fraction": 1.0, "n_uploaded": len(up), "n_rows": len(matches),
        "files": files}))


def background(up, density, rng, next_id, epoch_dt):
    """Poisson background of Gaia sources within 5 arcsec of every position: 70% stars, 30% unclassified."""
    counts = rng.poisson(density * np.pi * 25.0, len(up))
    oid = np.repeat(up.oid.to_numpy(), counts)
    ra0, dec0 = np.repeat(up.ra.to_numpy(), counts), np.repeat(up.dec.to_numpy(), counts)
    r, th = 5.0 * np.sqrt(rng.uniform(0, 1, len(oid))) * 0.999, rng.uniform(0, 2 * np.pi, len(oid))
    is_star = rng.uniform(0, 1, len(oid)) < 0.7
    rows = pd.concat([gaia_rows(int(is_star.sum()), "star", rng), gaia_rows(int((~is_star).sum()), "other", rng)],
                     ignore_index=True)
    order = np.argsort(~is_star, kind="stable")
    oid, ra0, dec0, r, th = oid[order], ra0[order], dec0[order], r[order], th[order]
    ra, dec = offsets(ra0, dec0, r, th)
    rows.insert(0, "oid", oid)
    rows.insert(1, "source_id", next_id + np.arange(len(oid), dtype=np.int64))
    rows.insert(2, "ra", ra)
    rows.insert(3, "dec", dec)
    return rows


def simulated_sky(tmp_path, n_per_field=2500, seed=11):
    """Two fields with different background densities. 40% of objects are Gaia stars (moving 50 mas/yr north), 10% are
    QSO-like Gaia sources and 50% have no counterpart; the control run sees background only."""
    m = load_module()
    rng = np.random.default_rng(seed)
    dt = m.window_epoch() - m.GAIA_EPOCH
    fields = {"Low-dust WFD": 0.03, "Galactic bulge": 0.10}
    n = n_per_field * len(fields)
    oid = 170000000000000000 + np.arange(n, dtype=np.int64) * 13
    field = np.repeat(list(fields), n_per_field)
    ra, dec = rng.uniform(10, 20, n), rng.uniform(-30, -20, n)
    truth = rng.choice(["star", "qso", "none"], size=n, p=[0.4, 0.1, 0.5])
    template = pd.DataFrame({"dia_object_id": oid, "field": field, "gal_b_deg": np.where(field == "Galactic bulge", -3.0, -45.0),
                             "ok_i": True, "mag_i": rng.uniform(16, 24, n),
                             "cats_class_latest": np.where(truth == "star", 21, 11).astype(np.int32),
                             "cats_label": "x", "simbad_group": "No SIMBAD object within 1″"})
    obj_path = tmp_path / "objects.parquet"
    template.to_parquet(obj_path, index=False)
    objects_sha = hashlib.sha256(obj_path.read_bytes()).hexdigest()

    up = pd.DataFrame({"oid": oid, "ra": ra, "dec": dec})
    parts, next_id = [], 10 ** 15
    for kind in ("star", "qso"):
        sel = truth == kind
        g = gaia_rows(int(sel.sum()), kind, rng)
        r, th = rng.uniform(0, 0.05, sel.sum()), rng.uniform(0, 2 * np.pi, sel.sum())
        gra, gdec = offsets(ra[sel], dec[sel], r, th)
        if kind == "star":                          # J2016 position 0.52 arcsec south; proper motion brings it back
            g["pmdec"] = 50.0
            gdec = gdec - 50.0 * dt / 3.6e6
        g.insert(0, "oid", oid[sel])
        g.insert(1, "source_id", next_id + np.arange(sel.sum(), dtype=np.int64))
        g.insert(2, "ra", gra)
        g.insert(3, "dec", gdec)
        parts.append(g)
        next_id += 10 ** 6
    bg = []
    for f, dens in fields.items():
        bg.append(background(up[field == f], dens, rng, next_id, dt))
        next_id += 10 ** 7
    main = pd.concat(parts + bg, ignore_index=True)
    ctrl_up = up.assign(dec=up.dec + 60.0 / 3600.0)
    ctrl = pd.concat([background(ctrl_up[field == f], dens, rng, next_id + 10 ** 8 * (i + 1), dt)
                      for i, (f, dens) in enumerate(fields.items())], ignore_index=True)
    write_run(tmp_path / "main", up, main, 0.0, objects_sha)
    write_run(tmp_path / "ctrl", ctrl_up, ctrl, 60.0, objects_sha)

    star_ids = main[main.oid.isin(oid[truth == "star"]) & (main.pmdec == 50.0)].set_index("oid").source_id
    names = pd.Series([None] * n, dtype=object)
    names[truth == "star"] = [f"Gaia DR3 {star_ids[o]}" for o in oid[truth == "star"]]
    lonely = np.flatnonzero(truth == "none")[:200]
    far_bg = main[main.oid.isin(oid[lonely]) & (main.ra.notna())]
    far_bg = far_bg.assign(sep=m.angsep_arcsec(up.set_index("oid").loc[far_bg.oid].ra, up.set_index("oid").loc[far_bg.oid].dec,
                                               far_bg.ra, far_bg.dec))
    far = far_bg[far_bg.sep > 2.0].drop_duplicates("oid").set_index("oid").source_id
    for o, sid in far.items():
        names[np.flatnonzero(oid == o)[0]] = f"Gaia DR3 {sid}"
    ghost = np.flatnonzero(truth == "none")[-10:]
    names[ghost] = "Gaia DR3 4999999999999999999"
    science = pd.DataFrame({"dia_object_id": oid, "field": field, "cats": template.cats_class_latest, "gaia_name": names,
                            "plx": np.where(truth == "star", 2.0, np.nan).astype("float32"),
                            "vsx_type": np.where(truth == "star", "E", None), "tns_type": None})
    sci_path = tmp_path / "science.parquet"
    science.to_parquet(sci_path, index=False)
    return {"truth": truth, "field": field, "oid": oid, "n_far": len(far), "n_ghost": len(ghost),
            "args": ["--main", str(tmp_path / "main"), "--control", str(tmp_path / "ctrl"), "--objects", str(obj_path),
                     "--science", str(sci_path), "--code-sha", "test", "--n-mc", "400", "--r-star", "1.0"]}


def test_separations_and_proper_motion():
    m = load_module()
    assert m.angsep_arcsec(10.0, 0.0, 10.0, 1 / 3600) == pytest.approx(1.0, rel=1e-9)
    assert m.angsep_arcsec(359.9999, -5.0, 0.0001, -5.0) == pytest.approx(0.72 * np.cos(np.radians(5.0)), rel=1e-6)
    ra, dec = m.propagate([50.0, 50.0, 50.0], [60.0, 60.0, 60.0], [100.0, 0.0, np.nan], [0.0, 100.0, np.nan], 10.0)
    assert m.angsep_arcsec(50.0, 60.0, ra[0], dec[0]) == pytest.approx(1.0, rel=1e-4)
    assert dec[1] - 60.0 == pytest.approx(1 / 3600) and ra[2] == 50.0 and dec[2] == 60.0
    assert 2026.33 < m.window_epoch() < 2026.35
    assert m.pm_significance(3.0, 1.0, 4.0, 1.0) == pytest.approx(5.0)


def test_classification_rules():
    m = load_module()
    rng = np.random.default_rng(0)
    star = gaia_rows(1, "star", rng)
    df = pd.concat([star,                                                    # parallax 10 sigma: star
                    star.assign(ruwe=2.0),                                   # star only without the RUWE cut
                    star.assign(parallax=0.0, pmra=1.2, pmdec=0.0),          # proper motion 6 sigma: star
                    star.assign(parallax=-3.0, classprob_dsc_combmod_quasar=0.6),  # negative parallax: not a star
                    gaia_rows(1, "qso", rng).assign(classprob_dsc_combmod_galaxy=0.9),
                    gaia_rows(1, "other", rng).assign(classprob_dsc_combmod_galaxy=0.7),
                    gaia_rows(1, "other", rng)], ignore_index=True)
    assert list(m.classify(df)) == [m.STAR, m.OTHER, m.STAR, m.QSO, m.QSO, m.GALAXY, m.OTHER]
    assert list(m.classify(df, use_ruwe=False))[:2] == [m.STAR, m.STAR]


def test_corrected_shares_invert_the_chance_model():
    m = load_module()
    truth = np.array([0.30, 0.05, 0.02, 0.03, 0.60])                 # categories, no counterpart last
    q = np.array([0.05, 0.01, 0.00, 0.04, 0.90])                     # at shifted positions
    s = truth.copy()
    s[:-1] = truth[:-1] + truth[-1] * q[:-1]
    s[-1] = truth[-1] * q[-1]
    obs, est, lo, hi = m.corrected_shares(s * 1e6, q * 1e5, np.random.default_rng(1), n_mc=2000)
    assert np.allclose(obs, s) and np.allclose(est, truth, atol=1e-9)
    assert np.all(lo <= truth + 1e-9) and np.all(truth - 1e-9 <= hi) and np.isclose(est.sum(), 1.0)
    assert np.all(np.isnan(m.corrected_shares(s * 100, np.zeros(5), np.random.default_rng(1))[1]))


def test_wilson_interval():
    m = load_module()
    lo, hi = m.wilson(np.array([0, 50]), np.array([100, 100]))
    assert lo[0] == pytest.approx(0.0, abs=1e-12) and 0.0 < hi[0] < 0.05
    assert lo[1] == pytest.approx(0.4038, abs=1e-3) and hi[1] == pytest.approx(0.5962, abs=1e-3)


def test_simulated_sky_recovers_the_truth(tmp_path):
    m = load_module()
    sim = simulated_sky(tmp_path)
    out = tmp_path / "analysis"
    summary = m.main(sim["args"] + ["--out", str(out)])
    comp = pd.read_csv(out / "composition_by_field.csv")
    for f in ("Low-dust WFD", "Galactic bulge"):
        c = comp[comp.field == f].set_index("category")
        sel = sim["field"] == f
        true = {m.STAR: np.mean(sim["truth"][sel] == "star"), m.QSO: np.mean(sim["truth"][sel] == "qso"),
                m.GALAXY: 0.0, m.OTHER: 0.0, m.NONE: np.mean(sim["truth"][sel] == "none")}
        for cat, v in true.items():
            assert abs(c.loc[cat, "corrected_share"] - v) < 0.03, (f, cat)
        assert c.loc[m.STAR, "observed_share"] > true[m.STAR] + 0.02      # chance matches inflate the raw share
        assert abs(c.corrected_share.sum() - 1.0) < 1e-5                   # CSV keeps 6 significant digits
    fm = pd.read_csv(out / "false_match_by_field.csv")
    one = fm[np.isclose(fm.radius_arcsec, 1.0)].set_index("field")
    for f, dens in (("Low-dust WFD", 0.03), ("Galactic bulge", 0.10)):
        expected = 1 - np.exp(-dens * np.pi)
        assert abs(one.loc[f, "f_matched_control"] - expected) < 0.025
        assert abs(one.loc[f, "f_chance_from_annulus"] - expected) < 0.02
        assert abs(one.loc[f, "f_with_real_counterpart"] - 0.5) < 0.03
    cp = pd.read_parquet(out / "counterparts.parquet").set_index("dia_object_id")
    stars = sim["oid"][sim["truth"] == "star"]
    assert (cp.loc[stars, "nearest_sep_arcsec"] < 0.06).mean() > 0.97          # proper motion applied, right sign
    assert (cp.loc[stars, "nearest_sep_2016_arcsec"] > 0.45).mean() > 0.9
    fink = pd.read_csv(out / "fink_gaia_check.csv").set_index("field").loc["all"]
    assert fink["n_fink_gaia"] == len(stars) + sim["n_far"] + sim["n_ghost"]
    assert fink["not within 5 arcsec of our position"] == sim["n_ghost"]
    assert fink["our nearest"] + fink["within r_star, not our nearest"] >= len(stars) - 5
    assert fink["within 5 arcsec, beyond r_star"] + fink["our nearest, beyond r_star"] == sim["n_far"]
    assert summary["r_star_arcsec"] == 1.0
    curve = summary["real_counterpart_fraction_by_radius_all"]           # true counterparts all lie within 0.06"
    assert len(curve) == 40 and all(abs(v - 0.5) < 0.03 for v in curve.values())
    assert summary["fink_gaia"]["unparsed_names"] == 0 and summary["fink_gaia"]["n_parallax_difference_gt_0.001"] == 0
    for name in ("fig_gaia_radial_density.png", "fig_gaia_false_match_vs_radius.png",
                 "fig_gaia_composition_by_magnitude.png", "fig_label_check_sn_like.png"):
        assert (out / name).stat().st_size > 1000
    label = pd.read_csv(out / "label_check_by_cats.csv")
    assert len(label) == 2 * len(m.CATEGORIES) and set(label.cats_class_latest) == {11, 21}
    sn_star = label[(label.cats_class_latest == 11) & (label.category == m.STAR)].iloc[0]
    assert abs(sn_star.corrected_share) < 0.03                      # no SN-like object is a star in the simulation
    assert summary["sn_like_gaia_star"]["corrected_share"] == pytest.approx(sn_star.corrected_share, abs=1e-5)
    assert set(json.loads((out / "summary.json").read_text())["files"]) >= {"counterparts.parquet", "composition_all.csv"}
    with pytest.raises(FileExistsError):
        m.main(sim["args"] + ["--out", str(out)])


def test_inputs_are_checked(tmp_path):
    m = load_module()
    sim = simulated_sky(tmp_path, n_per_field=300)
    run = tmp_path / "main"
    s = json.loads((run / "summary.json").read_text())
    s["radius_arcsec"] = 1.0                                   # matches now lie outside the claimed radius
    (run / "summary.json").write_text(json.dumps(s))
    with pytest.raises(ValueError, match="outside the query radius"):
        m.load_run(run, m.window_epoch())
    s["radius_arcsec"] = 5.0
    (run / "summary.json").write_text(json.dumps(s))
    (run / "uploaded.parquet").write_bytes((run / "uploaded.parquet").read_bytes() + b"x")
    with pytest.raises(ValueError, match="checksum mismatch"):
        m.main(sim["args"] + ["--out", str(tmp_path / "a")])

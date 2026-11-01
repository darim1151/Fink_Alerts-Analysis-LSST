#!/usr/bin/env python
"""Gaia DR3 counterparts of the Fink DIA objects, corrected for chance coincidences.

Inputs are two runs of scripts/crossmatch_gaia_irsa.py, the per-object template table and the per-object science table:
  --main     every DIA object, all Gaia DR3 sources within 5 arcsec;
  --control  a hash-sampled subset with each position moved 60 arcsec north (chance coincidences only).
Every Gaia position is moved from its J2016.0 epoch to the middle of the cohort window with its proper motion (sources
without one stay where they are) before separations are measured. The nearest Gaia source within --r-star is an
object's counterpart, and it is put in one category:
  Gaia star          5- or 6-parameter solution, RUWE < 1.4, and parallax / error > 5 or proper-motion significance > 5
  Gaia QSO-like      not a star, DSC (combmod) quasar probability >= 0.5
  Gaia galaxy-like   not a star or QSO-like, DSC (combmod) galaxy probability >= 0.5
  Gaia unclassified  any other Gaia source
  No Gaia source     nothing within --r-star
Shares are corrected for chance coincidences with the control positions of the same objects. If s_c are the observed
shares and q_c the shares at the shifted positions (q_none: no source there), the corrected shares are
s_c - s_none * q_c / q_none and, for no counterpart, s_none / q_none. This assumes that chance sources fall
independently of real counterparts. Intervals (95%) come from Dirichlet draws for both sets of counts (Jeffreys prior); intervals on single
fractions are Wilson score intervals.

Outputs, in a new directory: per-object counterparts, false-match rates per field and radius, composition tables (by
field, Galactic latitude, template i magnitude), the label check (Fink CATS class against Gaia identity), calibration on
objects with external labels, a check of Fink's own Gaia association, figures, a README and summary.json.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

GAIA_EPOCH = 2016.0
WINDOW_UTC = ("2026-02-25", "2026-07-14")            # half-open cohort window
STAR, QSO, GALAXY, OTHER, NONE = CATEGORIES = ("Gaia star", "Gaia QSO-like", "Gaia galaxy-like", "Gaia unclassified",
                                               "No Gaia source")
MAG_EDGES = (-np.inf, 17.0, 18.0, 19.0, 20.0, 21.0, 22.0, 23.0, np.inf)
GALB_EDGES = (0.0, 5.0, 10.0, 20.0, 30.0, 50.0, 90.0)
RADII = tuple(np.round(np.arange(0.1, 4.0001, 0.1), 2))
ANNULUS = (3.0, 5.0)
MAIN_FIELDS = ("Low-dust WFD", "Galactic bulge", "Galactic plane", "Deep drilling fields")
NO_I = "no i template detection"


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def window_epoch(window=WINDOW_UTC):
    """Julian epoch of the middle of the cohort window."""
    from astropy.time import Time

    mjd = (Time(window[0], scale="utc").mjd + Time(window[1], scale="utc").mjd) / 2.0
    return float(Time(mjd, format="mjd", scale="utc").jyear)


def angsep_arcsec(ra1, dec1, ra2, dec2):
    ra1, dec1, ra2, dec2 = (np.radians(np.asarray(x, dtype=float)) for x in (ra1, dec1, ra2, dec2))
    h = np.sin((dec2 - dec1) / 2) ** 2 + np.cos(dec1) * np.cos(dec2) * np.sin((ra2 - ra1) / 2) ** 2
    return np.degrees(2 * np.arcsin(np.sqrt(np.clip(h, 0.0, 1.0)))) * 3600.0


def propagate(ra, dec, pmra, pmdec, dt_yr):
    """Positions moved linearly by their proper motion (mas/yr, pmra includes cos dec) over dt_yr years."""
    pmra = np.nan_to_num(np.asarray(pmra, dtype=float), nan=0.0)
    pmdec = np.nan_to_num(np.asarray(pmdec, dtype=float), nan=0.0)
    dec = np.asarray(dec, dtype=float)
    ra_new = (np.asarray(ra, dtype=float) + pmra * dt_yr / 3.6e6 / np.cos(np.radians(dec))) % 360.0
    return ra_new, dec + pmdec * dt_yr / 3.6e6


def pm_significance(pmra, pmra_error, pmdec, pmdec_error):
    """sqrt((pmra/err)^2 + (pmdec/err)^2), ignoring the pmra-pmdec correlation; NaN without a proper motion."""
    return np.sqrt((np.asarray(pmra, float) / np.asarray(pmra_error, float)) ** 2
                   + (np.asarray(pmdec, float) / np.asarray(pmdec_error, float)) ** 2)


def classify(df, snr=5.0, ruwe_max=1.4, prob=0.5, use_ruwe=True):
    """Category of each Gaia source (see the module docstring)."""
    five = df.astrometric_params_solved.isin([31, 95]).to_numpy()
    plx_snr = (df.parallax / df.parallax_error).to_numpy(dtype=float)
    pm_sig = pm_significance(df.pmra, df.pmra_error, df.pmdec, df.pmdec_error)
    with np.errstate(invalid="ignore"):
        star = five & ((plx_snr > snr) | (pm_sig > snr))
        if use_ruwe:
            star &= df.ruwe.to_numpy(dtype=float) < ruwe_max
        qso = ~star & (df.classprob_dsc_combmod_quasar.to_numpy(dtype=float) >= prob)
        gal = ~star & ~qso & (df.classprob_dsc_combmod_galaxy.to_numpy(dtype=float) >= prob)
    return np.select([star, qso, gal], [STAR, QSO, GALAXY], default=OTHER)


def load_run(run, epoch, radius_tol_arcsec=0.01):
    """(summary, uploaded positions, matches with separations and categories) of a crossmatch run, after checking the
    files against the checksums in its summary.json and every match against the query radius."""
    run = Path(run)
    s = json.loads((run / "summary.json").read_text())
    for name, digest in s["files"].items():
        if sha256_file(run / name) != digest:
            raise ValueError(f"checksum mismatch: {run / name}")
    up = pd.read_parquet(run / "uploaded.parquet")
    m = pd.read_parquet(run / "matches.parquet")
    if not up.oid.is_unique or len(up) != s["n_uploaded"] or len(m) != s["n_rows"]:
        raise ValueError(f"{run}: uploaded or match counts differ from summary.json")
    if m.duplicated(["oid", "source_id"]).any():
        raise ValueError(f"{run}: duplicate (object, source) pairs")
    pos = up.set_index("oid").reindex(m.oid.to_numpy())
    if pos.ra.isna().any():
        raise ValueError(f"{run}: a match refers to an object that was not uploaded")
    ux, uy = pos.ra.to_numpy(), pos.dec.to_numpy()
    m = m.assign(sep_2016=angsep_arcsec(ux, uy, m.ra, m.dec))
    if (m.sep_2016 > s["radius_arcsec"] + radius_tol_arcsec).any():
        raise ValueError(f"{run}: a match lies outside the query radius")
    ra_e, dec_e = propagate(m.ra, m.dec, m.pmra, m.pmdec, epoch - GAIA_EPOCH)
    m["sep"] = angsep_arcsec(ux, uy, ra_e, dec_e)
    m["category"] = classify(m)
    m["category_no_ruwe_cut"] = classify(m, use_ruwe=False)
    m["plx_snr"] = m.parallax / m.parallax_error
    m["pm_sig"] = pm_significance(m.pmra, m.pmra_error, m.pmdec, m.pmdec_error)
    return s, up, m


def nearest_table(up, m, r_star):
    """One row per uploaded object: its nearest Gaia source (proper-motion corrected), counts, and its category."""
    near = m.sort_values(["oid", "sep", "source_id"]).drop_duplicates("oid")
    keep = ["oid", "source_id", "sep", "sep_2016", "category", "category_no_ruwe_cut", "phot_g_mean_mag", "plx_snr",
            "pm_sig", "ruwe", "astrometric_params_solved", "classprob_dsc_combmod_quasar",
            "classprob_dsc_combmod_galaxy", "in_qso_candidates", "in_galaxy_candidates"]
    t = up[["oid"]].merge(near[keep], on="oid", how="left")
    within = m[m.sep <= r_star].groupby("oid").size()
    annulus = m[(m.sep_2016 >= ANNULUS[0]) & (m.sep_2016 < ANNULUS[1])].groupby("oid").size()
    t["n_within_r_star"] = t.oid.map(within).fillna(0).astype(np.int64)
    t["n_annulus"] = t.oid.map(annulus).fillna(0).astype(np.int64)
    matched = t.sep.to_numpy() <= r_star
    for col in ("category", "category_no_ruwe_cut"):
        t[f"{col}_r_star"] = np.where(matched, t[col].to_numpy(dtype=object), NONE)
    return t


def wilson(k, n, z=1.959964):
    """Wilson score interval (95% by default) for k successes in n trials."""
    k, n = np.asarray(k, float), np.asarray(n, float)
    with np.errstate(invalid="ignore", divide="ignore"):
        p = k / n
        centre = (p + z * z / (2 * n)) / (1 + z * z / n)
        half = z / (1 + z * z / n) * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return centre - half, centre + half


def fraction_within(sep, radii):
    s = np.sort(np.nan_to_num(np.asarray(sep, dtype=float), nan=np.inf))
    return np.searchsorted(s, np.asarray(radii), side="right") / max(len(s), 1)


def false_match_table(main_near, ctrl_near, objects, by="field", radii=RADII):
    """Per stratum and radius: matched fraction at the real and the shifted positions, the implied fraction of objects
    with a real counterpart, and the expected share of chance matches among matched objects."""
    rows = []
    mn = main_near.merge(objects[["oid", by]], on="oid")
    cn = ctrl_near.merge(objects[["oid", by]], on="oid")
    groups = [("all", mn, cn)] + [(g, mn[mn[by] == g], cn[cn[by] == g]) for g in sorted(mn[by].dropna().unique())]
    for g, a, c in groups:
        fa, fc = fraction_within(a.sep, radii), fraction_within(c.sep, radii)
        kc = np.round(fc * len(c)).astype(int)
        lo, hi = wilson(kc, len(c))
        rho = a.n_annulus.mean() / (np.pi * (ANNULUS[1] ** 2 - ANNULUS[0] ** 2)) if len(a) else np.nan
        for r, x, y, l, h in zip(radii, fa, fc, lo, hi):
            with np.errstate(invalid="ignore", divide="ignore"):
                p_true = 1 - (1 - x) / (1 - y)
                false_share = (1 - x) * y / ((1 - y) * x) if x > 0 else np.nan
            rows.append({by: g, "radius_arcsec": r, "n_objects": len(a), "n_control": len(c), "f_matched": x,
                         "f_matched_control": y, "f_control_lo": l, "f_control_hi": h,
                         "f_chance_from_annulus": 1 - np.exp(-rho * np.pi * r ** 2),
                         "f_with_real_counterpart": p_true, "chance_share_of_matched": false_share})
    return pd.DataFrame(rows)


def corrected_shares(n, m, rng, n_mc=4000):
    """(observed, corrected, lo, hi) per category for counts n (objects) and m (their shifted positions); NONE last."""
    n, m = np.asarray(n, float), np.asarray(m, float)

    def point(s, q):
        out = s - s[..., -1:] * q / q[..., -1:]
        out[..., -1] = s[..., -1] / q[..., -1]
        return out

    obs = n / n.sum() if n.sum() else np.full(len(n), np.nan)
    if n.sum() == 0 or m.sum() == 0:
        nan = np.full(len(n), np.nan)
        return obs, nan, nan, nan
    est = point(obs, m / m.sum()) if m[-1] > 0 else np.full(len(n), np.nan)
    draws = point(rng.dirichlet(n + 0.5, size=n_mc), rng.dirichlet(m + 0.5, size=n_mc))
    lo, hi = np.percentile(draws, [2.5, 97.5], axis=0)
    return obs, est, lo, hi


def composition(main_near, ctrl_near, objects, by, rng, column="category_r_star", n_mc=4000, min_objects=1):
    """Long table: per stratum (columns `by`) and category, counts, observed and corrected shares with 95% intervals."""
    by = list(by)
    mn = main_near[["oid", column]].merge(objects[["oid", *by]], on="oid")
    cn = ctrl_near[["oid", column]].merge(objects[["oid", *by]], on="oid")
    a = mn.groupby(by + [column], dropna=False).size().unstack(column, fill_value=0).reindex(columns=CATEGORIES,
                                                                                             fill_value=0)
    c = cn.groupby(by + [column], dropna=False).size().unstack(column, fill_value=0).reindex(columns=CATEGORIES,
                                                                                             fill_value=0)
    c = c.reindex(a.index, fill_value=0)
    rows = []
    for key in a.index:
        n, m = a.loc[key].to_numpy(), c.loc[key].to_numpy()
        if n.sum() < min_objects:
            continue
        obs, est, lo, hi = corrected_shares(n, m, rng, n_mc)
        key = key if isinstance(key, tuple) else (key,)
        for i, cat in enumerate(CATEGORIES):
            rows.append({**dict(zip(by, key)), "category": cat, "n_objects": int(n.sum()), "n_control": int(m.sum()),
                         "n": int(n[i]), "n_control_in_category": int(m[i]), "observed_share": obs[i],
                         "corrected_share": est[i], "corrected_lo": lo[i], "corrected_hi": hi[i]})
    return pd.DataFrame(rows)


def object_strata(template, science):
    """Per-object strata and labels from the two Arnor tables (checked to describe the same objects)."""
    t = template[["dia_object_id", "field", "gal_b_deg", "ok_i", "mag_i", "cats_class_latest", "cats_label",
                  "simbad_group"]].rename(columns={"dia_object_id": "oid"})
    s = science[["dia_object_id", "field", "cats", "gaia_name", "plx", "vsx_type", "tns_type"]].rename(
        columns={"dia_object_id": "oid", "field": "field_science"})
    if not t.oid.is_unique or set(t.oid) != set(s.oid):
        raise ValueError("the template and science tables describe different objects")
    o = t.merge(s, on="oid")
    # The template table's field (most frequent programme group of the object's alerts, ties alphabetical) is used.
    # The science table can differ for objects with alerts in several groups; the number is recorded, not hidden.
    o["field_differs_between_tables"] = o.field.fillna("") != o.field_science.fillna("")
    if not (o.cats_class_latest == o.cats).all():
        raise ValueError("CATS class differs between the two tables")
    mag = np.where(o.ok_i.fillna(False).to_numpy(dtype=bool), o.mag_i.to_numpy(dtype=float), np.nan)
    labels = [f"i < {MAG_EDGES[1]:.0f}"] + [f"{a:.0f}-{b:.0f}" for a, b in zip(MAG_EDGES[1:-2], MAG_EDGES[2:-1])] + \
             [f"i >= {MAG_EDGES[-2]:.0f}"]
    o["i_bin"] = pd.cut(mag, list(MAG_EDGES), right=False, labels=labels).astype(object)
    o.loc[np.isnan(mag), "i_bin"] = NO_I
    o["abs_b_bin"] = pd.cut(np.abs(o.gal_b_deg), list(GALB_EDGES), right=False, include_lowest=True,
                            labels=[f"{a:.0f}-{b:.0f}" for a, b in zip(GALB_EDGES[:-1], GALB_EDGES[1:])]).astype(object)
    o.loc[np.abs(o.gal_b_deg) >= GALB_EDGES[-1], "abs_b_bin"] = f"{GALB_EDGES[-2]:.0f}-{GALB_EDGES[-1]:.0f}"
    tns_sn = o.tns_type.fillna("").str.startswith(("SN", "SLSN")).to_numpy()
    o["external_label"] = np.select(
        [tns_sn, o.tns_type.notna().to_numpy(), o.vsx_type.notna().to_numpy(),
         o.simbad_group.isin(["Star", "QSO / AGN", "Galaxy"]).to_numpy()],
        ["TNS: supernova", "TNS: other type", "VSX variable star", ("SIMBAD: " + o.simbad_group.fillna("")).to_numpy()],
        default="")
    return o


def fink_gaia_check(objects, m, near, r_star):
    """Where Fink names a Gaia DR3 source for an object: is it ours, and at what (proper-motion corrected) separation?"""
    name = objects.gaia_name
    fid = name.str.extract(r"^Gaia DR3 (\d+)$")[0]
    unparsed = int((name.notna() & fid.isna()).sum())
    has = fid.notna()
    f = pd.DataFrame({"oid": objects.oid[has], "field": objects.field[has],
                      "fink_source_id": fid[has].astype(np.int64),      # parsed from text: exact, never via float
                      "fink_plx": objects.plx[has]})
    pairs = f.merge(m[["oid", "source_id", "sep", "sep_2016", "parallax"]], left_on=["oid", "fink_source_id"],
                    right_on=["oid", "source_id"], how="left")
    pairs = pairs.merge(near[["oid", "source_id"]].rename(columns={"source_id": "our_nearest"}), on="oid", how="left")
    pairs["status"] = np.select(
        [pairs.sep.isna(), pairs.fink_source_id == pairs.our_nearest, pairs.sep <= r_star],
        ["not within 5 arcsec of our position", "our nearest", "within r_star, not our nearest"],
        default="within 5 arcsec, beyond r_star")
    pairs.loc[(pairs.status == "our nearest") & (pairs.sep > r_star), "status"] = "our nearest, beyond r_star"
    plx_diff = (pairs.fink_plx - pairs.parallax).abs()
    rows = []
    for g, p in [("all", pairs)] + [(g, pairs[pairs.field == g]) for g in sorted(pairs.field.dropna().unique())]:
        found = p.sep.notna()
        row = {"field": g, "n_fink_gaia": len(p), "n_found_within_5": int(found.sum())}
        for st in ("our nearest", "our nearest, beyond r_star", "within r_star, not our nearest",
                   "within 5 arcsec, beyond r_star", "not within 5 arcsec of our position"):
            row[st] = int((p.status == st).sum())
        q = p.sep_2016[found].quantile([0.5, 0.9, 0.99, 0.999]).to_dict() if found.any() else {}
        row.update({f"sep_2016_q{k:g}": v for k, v in q.items()})
        row["sep_2016_max"] = float(p.sep_2016[found].max()) if found.any() else np.nan
        rows.append(row)
    stats = {"unparsed_names": unparsed, "n_parallax_compared": int(plx_diff.notna().sum()),
             "max_abs_parallax_difference_mas": float(plx_diff.max()) if plx_diff.notna().any() else None,
             "n_parallax_difference_gt_0.001": int((plx_diff > 1e-3).sum())}
    return pd.DataFrame(rows), pairs, stats


def plots(out, fm, comp_fm, label, radial, r_star):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    colours = {STAR: "#2a78d6", QSO: "#eb6834", GALAXY: "#1baf7a", OTHER: "#eda100", NONE: "#d6d5d0"}
    field_colours = dict(zip(MAIN_FIELDS, ("#2a78d6", "#eb6834", "#1baf7a", "#6250d6")))
    plt.rcParams.update({"font.size": 8, "axes.spines.top": False, "axes.spines.right": False,
                         "axes.edgecolor": "#52514e", "axes.labelcolor": "#0b0b0b", "xtick.color": "#52514e",
                         "ytick.color": "#52514e", "axes.grid": True, "grid.color": "#e6e5e0", "grid.linewidth": 0.5})

    # 1. Surface density of Gaia sources around the objects and around the shifted positions.
    fig, axes = plt.subplots(1, len(MAIN_FIELDS), figsize=(10, 2.6), sharey=True)
    for ax, field in zip(axes, MAIN_FIELDS):
        r = radial[radial.field == field]
        ax.plot(r.r_mid, r.density_main, color="#2a78d6", lw=1.5, label="object positions")
        ax.plot(r.r_mid, r.density_control, color="#52514e", lw=1.5, ls="--", label="positions moved 60″ north")
        ax.axvline(r_star, color="#888780", lw=0.8, ls=":")
        ax.set_yscale("log")
        ax.set_title(field, fontsize=8)
        ax.set_xlabel("separation (arcsec)")
    axes[0].set_ylabel("Gaia sources per object per arcsec²")
    axes[0].legend(frameon=False, fontsize=7, loc="upper right")
    fig.tight_layout()
    fig.savefig(out / "fig_gaia_radial_density.png", dpi=200)
    fig.savefig(out / "fig_gaia_radial_density.pdf")
    plt.close(fig)

    # 2. Expected share of chance matches among matched objects, against radius.
    fig, ax = plt.subplots(figsize=(4.6, 3.2))
    for field in MAIN_FIELDS:
        f = fm[fm.field == field]
        if f.empty:
            continue
        ax.plot(f.radius_arcsec, 100 * f.chance_share_of_matched, color=field_colours[field], lw=1.5)
        ax.text(f.radius_arcsec.iloc[-1] + 0.05, 100 * f.chance_share_of_matched.iloc[-1], field, fontsize=7,
                va="center", color="#0b0b0b")
    ax.axvline(r_star, color="#888780", lw=0.8, ls=":")
    ax.set_yscale("log")
    ax.set_xlim(0, 5.6)
    ax.set_xlabel("match radius (arcsec)")
    ax.set_ylabel("chance matches among matched objects (%)")
    fig.tight_layout()
    fig.savefig(out / "fig_gaia_false_match_vs_radius.png", dpi=200)
    fig.savefig(out / "fig_gaia_false_match_vs_radius.pdf")
    plt.close(fig)

    # 3. Corrected composition against template i magnitude, one panel per field.
    bins = [b for b in comp_fm.i_bin.unique() if b != NO_I]
    order = sorted(bins, key=lambda b: (b.startswith("i >="), b != "i < 17", b)) + [NO_I]
    fig, axes = plt.subplots(1, len(MAIN_FIELDS), figsize=(10, 3.0), sharey=True)
    for ax, field in zip(axes, MAIN_FIELDS):
        c = comp_fm[comp_fm.field == field]
        x = np.arange(len(order))
        bottom = np.zeros(len(order))
        for cat in CATEGORIES:
            v = c[c.category == cat].set_index("i_bin").reindex(order).corrected_share.clip(lower=0).fillna(0).to_numpy()
            ax.bar(x, v, bottom=bottom, color=colours[cat], width=0.8, edgecolor="white", linewidth=0.6, label=cat)
            bottom += v
        ax.set_xticks(x)
        ax.set_xticklabels([b.replace(NO_I, "none").replace("i < ", "<").replace("i >= ", "≥") for b in order],
                           rotation=60, fontsize=6.5)
        ax.set_title(field, fontsize=8)
        ax.set_xlabel("template i (mag)")
        ax.grid(False)
    axes[0].set_ylabel("share of objects (corrected)")
    axes[-1].legend(frameon=False, fontsize=6.5, loc="upper left", bbox_to_anchor=(1.0, 1.0))
    fig.tight_layout()
    fig.savefig(out / "fig_gaia_composition_by_magnitude.png", dpi=200)
    fig.savefig(out / "fig_gaia_composition_by_magnitude.pdf")
    plt.close(fig)

    # 4. Label check: corrected share of Gaia stars among objects Fink calls SN-like, against template i magnitude.
    fig, ax = plt.subplots(figsize=(5.2, 3.2))
    sn = label[(label.cats_class_latest == 11) & (label.category == STAR) & (label.i_bin != NO_I)]
    for field in MAIN_FIELDS:
        f = sn[sn.field == field].set_index("i_bin").reindex([b for b in order if b != NO_I]).dropna(subset=["n_objects"])
        f = f[f.n_objects >= 100]
        x = [order.index(b) for b in f.index]
        ax.fill_between(x, 100 * f.corrected_lo, 100 * f.corrected_hi, color=field_colours[field], alpha=0.18, lw=0)
        ax.plot(x, 100 * f.corrected_share, color=field_colours[field], lw=1.5, marker="o", ms=3, label=field)
    ax.set_xticks(range(len(order) - 1))
    ax.set_xticklabels([b.replace("i < ", "<").replace("i >= ", "≥") for b in order[:-1]], fontsize=7)
    ax.set_xlabel("template i (mag)")
    ax.set_ylabel("SN-like objects that are Gaia stars (%)")
    ax.legend(frameon=False, fontsize=7)
    fig.tight_layout()
    fig.savefig(out / "fig_label_check_sn_like.png", dpi=200)
    fig.savefig(out / "fig_label_check_sn_like.pdf")
    plt.close(fig)


def radial_density(m, up_objects, objects, step=0.1, rmax=4.0):
    """Gaia sources per object per arcsec^2 in rings of `step` arcsec (proper-motion corrected), per field."""
    edges = np.round(np.arange(0.0, rmax + step / 2, step), 6)
    area = np.pi * (edges[1:] ** 2 - edges[:-1] ** 2)
    mm = m[["oid", "sep"]].merge(objects[["oid", "field"]], on="oid")
    n_obj = up_objects.merge(objects[["oid", "field"]], on="oid").groupby("field").size()
    out = {}
    for field, g in mm.groupby("field"):
        counts, _ = np.histogram(g.sep, bins=edges)
        out[field] = counts / area / n_obj[field]
    return edges, out


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--main", required=True, help="crossmatch run of all objects")
    p.add_argument("--control", required=True, help="crossmatch run at shifted positions")
    p.add_argument("--objects", required=True, help="dia_object_template_photometry.parquet")
    p.add_argument("--science", required=True, help="dia_object_science_table.parquet")
    p.add_argument("--out", required=True, help="new output directory (must not exist)")
    p.add_argument("--r-star", type=float, default=0.5, help="match radius in arcsec")
    p.add_argument("--seed", type=int, default=20261101)
    p.add_argument("--n-mc", type=int, default=4000)
    p.add_argument("--code-sha", required=True)
    p.add_argument("--no-plots", action="store_true")
    a = p.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=False)
    rng = np.random.default_rng(a.seed)
    epoch = window_epoch()

    objects_sha = sha256_file(a.objects)
    s_main, up_main, m_main = load_run(a.main, epoch)
    s_ctrl, up_ctrl, m_ctrl = load_run(a.control, epoch)
    for s in (s_main, s_ctrl):
        if s["objects_sha256"] != objects_sha:
            raise ValueError("a crossmatch run used a different object table")
    if s_main["shift_arcsec_north"] != 0 or s_main["sample_fraction"] != 1.0 or s_ctrl["shift_arcsec_north"] <= 0:
        raise ValueError("--main must be unshifted and complete, --control shifted")
    if s_main["radius_arcsec"] != s_ctrl["radius_arcsec"]:
        raise ValueError("the two runs used different radii")

    template = pd.read_parquet(a.objects)
    science = pd.read_parquet(a.science)
    objects = object_strata(template, science)
    if set(up_main.oid) != set(objects.oid) or not set(up_ctrl.oid) <= set(objects.oid):
        raise ValueError("uploaded objects do not match the object table")

    near_main = nearest_table(up_main, m_main, a.r_star)
    near_ctrl = nearest_table(up_ctrl, m_ctrl, a.r_star)

    fm = false_match_table(near_main, near_ctrl, objects, "field")
    fm_b = false_match_table(near_main, near_ctrl, objects, "abs_b_bin")
    comp_field = composition(near_main, near_ctrl, objects, ["field"], rng, n_mc=a.n_mc)
    comp_all = composition(near_main, near_ctrl, objects.assign(all="all"), ["all"], rng, n_mc=a.n_mc)
    comp_b = composition(near_main, near_ctrl, objects, ["abs_b_bin"], rng, n_mc=a.n_mc)
    comp_fm = composition(near_main, near_ctrl, objects, ["field", "i_bin"], rng, n_mc=a.n_mc)
    label = composition(near_main, near_ctrl, objects, ["cats_class_latest", "field", "i_bin"], rng, n_mc=a.n_mc)
    label_cf = composition(near_main, near_ctrl, objects, ["cats_class_latest", "field"], rng, n_mc=a.n_mc)
    label_c = composition(near_main, near_ctrl, objects, ["cats_class_latest"], rng, n_mc=a.n_mc)
    label_c_noruwe = composition(near_main, near_ctrl, objects, ["cats_class_latest"], rng,
                                 column="category_no_ruwe_cut_r_star", n_mc=a.n_mc)
    calib = composition(near_main, near_ctrl, objects[objects.external_label != ""], ["external_label"], rng,
                        n_mc=a.n_mc)
    fink_tab, fink_pairs, fink_stats = fink_gaia_check(objects, m_main, near_main, a.r_star)

    edges, dens_main = radial_density(m_main, up_main, objects)
    _, dens_ctrl = radial_density(m_ctrl, up_ctrl, objects)
    radial = pd.DataFrame([{"field": f, "r_lo": lo, "r_hi": hi, "r_mid": (lo + hi) / 2, "density_main": dm,
                            "density_control": dens_ctrl.get(f, np.full(len(edges) - 1, np.nan))[i]}
                           for f, d in dens_main.items() for i, (lo, hi, dm) in enumerate(zip(edges[:-1], edges[1:], d))])

    counterparts = near_main.merge(objects[["oid", "field", "gal_b_deg", "mag_i", "ok_i", "cats_class_latest",
                                            "simbad_group", "external_label"]], on="oid")
    counterparts = counterparts.merge(fink_pairs[["oid", "fink_source_id", "status"]].rename(
        columns={"status": "fink_gaia_status"}), on="oid", how="left")
    counterparts = counterparts.rename(columns={"oid": "dia_object_id", "source_id": "nearest_gaia_source_id",
                                                "sep": "nearest_sep_arcsec", "sep_2016": "nearest_sep_2016_arcsec"})
    counterparts.to_parquet(out / "counterparts.parquet", index=False)
    tables = {"false_match_by_field.csv": fm, "false_match_by_abs_b.csv": fm_b, "composition_all.csv": comp_all,
              "composition_by_field.csv": comp_field, "composition_by_abs_b.csv": comp_b,
              "composition_by_field_i_mag.csv": comp_fm, "label_check_by_cats_field_i_mag.csv": label,
              "label_check_by_cats_field.csv": label_cf, "label_check_by_cats.csv": label_c,
              "label_check_by_cats_no_ruwe_cut.csv": label_c_noruwe, "calibration_external_labels.csv": calib,
              "fink_gaia_check.csv": fink_tab, "radial_density.csv": radial}
    for name, df in tables.items():
        df.to_csv(out / name, index=False, float_format="%.6g")
    if not a.no_plots:
        plots(out, fm, comp_fm, label, radial, a.r_star)

    def share(df, cat, **keys):
        r = df[(df.category == cat)]
        for k, v in keys.items():
            r = r[r[k] == v]
        return {c: float(r[c].iloc[0]) for c in ("observed_share", "corrected_share", "corrected_lo", "corrected_hi")} \
            if len(r) else None

    summary = {"run": out.name, "code_sha": a.code_sha, "script_sha256": sha256_file(Path(__file__)),
               "epoch_jyear": epoch, "gaia_epoch": GAIA_EPOCH, "r_star_arcsec": a.r_star, "seed": a.seed,
               "n_mc": a.n_mc, "category_rules": __doc__.split("category:")[1].split("Shares are")[0].strip(),
               "inputs": {"main_run": s_main["run"], "main_code_sha": s_main["code_sha"],
                          "main_matches_sha256": s_main["files"]["matches.parquet"], "control_run": s_ctrl["run"],
                          "control_code_sha": s_ctrl["code_sha"],
                          "control_matches_sha256": s_ctrl["files"]["matches.parquet"],
                          "control_shift_arcsec": s_ctrl["shift_arcsec_north"],
                          "control_sample_fraction": s_ctrl["sample_fraction"], "objects": Path(a.objects).name,
                          "objects_sha256": objects_sha, "science": Path(a.science).name,
                          "science_sha256": sha256_file(a.science)},
               "n_objects": int(len(near_main)), "n_control": int(len(near_ctrl)),
               "field_rule": "template table: most frequent programme group of the object's alerts, ties alphabetical",
               "n_field_differs_between_tables": int(objects.field_differs_between_tables.sum()),
               "n_main_pairs": int(len(m_main)), "n_control_pairs": int(len(m_ctrl)),
               "n_matched_r_star": int((near_main.sep <= a.r_star).sum()),
               "n_multiple_within_r_star": int((near_main.n_within_r_star > 1).sum()),
               "n_gaia_sources_shared_by_objects_r_star": int(
                   near_main[near_main.sep <= a.r_star].source_id.duplicated(keep=False).sum()),
               "false_match_all_r_star": fm[(fm.field == "all") & np.isclose(fm.radius_arcsec, a.r_star)]
               .drop(columns="field").to_dict("records"),
               "real_counterpart_fraction_by_radius_all": {f"{r:.1f}": round(float(v), 6) for r, v in zip(
                   fm[fm.field == "all"].radius_arcsec, fm[fm.field == "all"].f_with_real_counterpart)},
               "radius_of_max_real_counterpart_fraction": float(fm[fm.field == "all"].set_index("radius_arcsec")
                                                                 .f_with_real_counterpart.idxmax()),
               "composition_all": {c: share(comp_all, c) for c in CATEGORIES},
               "sn_like_gaia_star": share(label_c, STAR, cats_class_latest=11),
               "sn_like_gaia_star_no_ruwe_cut": share(label_c_noruwe, STAR, cats_class_latest=11),
               "fink_gaia": fink_stats,
               "finished_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    summary["files"] = {f.name: sha256_file(f) for f in sorted(out.iterdir()) if f.is_file()}
    (out / "summary.json").write_text(json.dumps(summary, indent=1, default=float) + "\n")
    print(json.dumps({k: v for k, v in summary.items() if k != "files"}, indent=1, default=float))
    return summary


if __name__ == "__main__":
    main()

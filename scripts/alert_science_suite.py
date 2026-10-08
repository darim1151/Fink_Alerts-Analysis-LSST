#!/usr/bin/env python
"""Science figure and table suite for the five-window Fink LSST Light Static cohort.

Implements the analyses in docs/FINK_ANALYSIS_RECOMMENDATIONS_20260225_20260714.md:

  S  survey context: sky density, nightly delivered alerts, field composition, field tables
  C  star/galaxy colour panels from Legacy DR8 pstar (extended = red contours, point-like = blue dots)
  K  classifier validation: CATS class vs external labels (TNS, VSX, SIMBAD), recall vs detections,
     SuperNNova SN-vs-others ROC
  L  label evolution across delivered detections
  V  variability amplitude and skewness (Fink lc_features on difference flux, relative to template flux)
  D  distances: Gaia-parallax HR diagram, observed peak absolute magnitudes vs redshift
  Q  data quality: reliability by band and brightness; colour-panel robustness to a reliability cut
  O  SSO population (kept separate per analysis_contract_v1)

The analytical catalog is opened read-only through ``open_catalog`` (raw stat fingerprints verified); only
its pinned raw Parquet files are read. The per-object template photometry comes from the audited
colour-diagram run. Outputs go to a new run directory that must not exist.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from fink_lsst.analytics.catalog import open_catalog  # noqa: E402

AB_ZP_NJY = 31.4
BANDS = ("u", "g", "r", "i", "z", "y")

# Reference categorical palette (fixed order; colour follows the entity in every figure).
CATS_ORDER = [11, 12, 13, 21, 22, -1]
CATS_NAME = {11: "SN-like", 12: "Fast", 13: "Long", 21: "Periodic", 22: "Non-periodic (AGN)", -1: "Not processed (first alert)"}
CATS_COLOR = {11: "#2a78d6", 12: "#eb6834", 13: "#1baf7a", 21: "#eda100", 22: "#e87ba4", -1: "#b8b7b2"}
BAND_COLOR = {"u": "#4a3aa7", "g": "#1baf7a", "r": "#eb6834", "i": "#e34948", "z": "#2a78d6", "y": "#52514e"}
BLUE, RED, ORANGE, AQUA = "#2a78d6", "#e34948", "#eb6834", "#1baf7a"
INK, INK_MUTED, GRID, NEUTRAL = "#0b0b0b", "#52514e", "#e4e3df", "#d9d8d4"

FIELD_ORDER = ["Low-dust WFD", "Galactic bulge", "Galactic plane", "Deep drilling fields", "M49 field",
               "North ecliptic spur", "Target of opportunity", "Engineering"]


def field_group(target):
    t = str(target)
    parts = {p.strip() for p in t.split(",")}
    if any(p.startswith("ddf_") for p in parts):
        return "Deep drilling fields"
    if "field_m49" in parts:
        return "M49 field"
    if any(p.startswith("neutrino") for p in parts):
        return "Target of opportunity"
    if t.startswith("alt:"):
        return "Engineering"
    if "dusty_plane" in parts:
        return "Galactic plane"
    if "bulgy" in parts:
        return "Galactic bulge"
    if "nes" in parts:
        return "North ecliptic spur"
    if "lowdust" in parts:
        return "Low-dust WFD"
    return "Other"


# External-label groups and the CATS class each one should receive.
TRUTH_EXPECTED = {
    "SN (TNS spectroscopic)": 11, "SLSN / TDE (TNS spectroscopic)": 13, "CV / nova (VSX)": 12,
    "Eclipsing binary (VSX)": 21, "Pulsating star (VSX)": 21, "Rotational variable (VSX)": 21,
    "QSO / AGN (SIMBAD)": 22,
}
TRUTH_ORDER = list(TRUTH_EXPECTED) + ["Other VSX type", "Star, no VSX type (SIMBAD)", "Galaxy (SIMBAD)"]
VSX_ECLIPSING = ("E", "EA", "EB", "EW", "EC", "ED", "ESD", "EP", "ELL", "AR", "DM", "DS", "DW", "K", "KE", "KW", "SD", "WD", "WR")
VSX_PULSATING = ("RR", "RRAB", "RRC", "RRD", "DSCT", "DSCTC", "GDOR", "SXPHE", "CEP", "DCEP", "DCEPS", "CW", "CWA", "CWB",
                 "M", "SR", "SRA", "SRB", "SRC", "SRD", "L", "LB", "LC", "LPV", "BCEP", "ACYG", "RV", "RVA", "RVB", "ZZ",
                 "ZZA", "ZZB", "HADS", "ACEP", "BLAP", "PVTEL", "SPB", "ROAP", "PUL", "MIRA", "BXCIR")
VSX_ROTATIONAL = ("ROT", "RS", "BY", "ACV", "SXARI", "FKCOM", "ELL", "PSR", "R")
VSX_CV = ("UG", "UGSU", "UGSS", "UGZ", "UGWZ", "UGER", "NL", "NA", "NB", "NC", "NR", "N", "CV", "AM", "DQ", "IBWD", "ZAND", "V838MON")
SIMBAD_QSO_AGN = {"QSO", "AGN", "Sy1", "Sy2", "SyG", "Sy?", "BLL", "BL?", "Bla", "Bz?", "Q?", "AG?", "LIN", "LI?"}
SIMBAD_GALAXY = {"G", "G?", "rG", "GiG", "GiC", "GiP", "BiC", "GrG", "EmG", "CGG", "SBG", "H2G", "LSB", "bCG", "PaG",
                 "ClG", "IG", "Gr?", "SB?", "HzG", "BCG", "LeG", "Sy3"}


def vsx_group(v):
    if not isinstance(v, str) or not v:
        return None
    tok = v.replace(":", "").replace("+", "|").replace("/", "|").split("|")[0].strip().upper()
    if tok in VSX_CV:
        return "CV / nova (VSX)"
    if tok in VSX_ECLIPSING:
        return "Eclipsing binary (VSX)"
    if tok in VSX_PULSATING:
        return "Pulsating star (VSX)"
    if tok in VSX_ROTATIONAL:
        return "Rotational variable (VSX)"
    return "Other VSX type"


def truth_group(row):
    t = row["tns_type"]
    if isinstance(t, str):
        if t.startswith("SLSN") or t.startswith("TDE"):
            return "SLSN / TDE (TNS spectroscopic)"
        if t.startswith("SN"):
            return "SN (TNS spectroscopic)"
    g = vsx_group(row["vsx_type"])
    if g:
        return g
    o = row["simbad_otype"]
    if isinstance(o, str):
        if o in SIMBAD_QSO_AGN:
            return "QSO / AGN (SIMBAD)"
        if o in SIMBAD_GALAXY:
            return "Galaxy (SIMBAD)"
        if "*" in o:
            return "Star, no VSX type (SIMBAD)"
    return None


def wilson(k, n, z=1.96):
    if n == 0:
        return (np.nan, np.nan)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, min(p, c - h)), min(1.0, max(p, c + h)))


def auc(pos, neg):
    s = np.concatenate([pos, neg])
    r = pd.Series(s).rank().to_numpy()
    return (r[: len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))


def flat_lcdm_distmod(z):
    """Distance modulus for flat LCDM with Planck18 H0 and Om0 (radiation neglected; < 0.01 mag for z < 1.5)."""
    from astropy.cosmology import Planck18
    h0, om = Planck18.H0.value, Planck18.Om0
    grid = np.linspace(0.0, 3.0, 30001)
    inv_e = 1.0 / np.sqrt(om * (1 + grid) ** 3 + (1 - om))
    dc = np.concatenate([[0.0], np.cumsum(0.5 * (inv_e[1:] + inv_e[:-1]) * np.diff(grid))]) * 299792.458 / h0  # Mpc
    dl = (1 + np.asarray(z)) * np.interp(np.asarray(z), grid, dc)
    return 5 * np.log10(dl * 1e6 / 10.0)


def jsonable(x):
    if isinstance(x, dict):
        return {str(k): jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [jsonable(v) for v in x]
    if isinstance(x, pd.DataFrame):
        return jsonable(x.to_dict(orient="records"))
    if hasattr(x, "item"):
        return x.item()
    return x


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ----------------------------------------------------------------------------- loading
SCALARS = ["diaObjectId", "diaSourceId", "band", "midpointMjdTai", "ra", "dec", "psfFlux", "psfFluxErr", "scienceFlux",
           "templateFlux", "templateFluxErr", "snr", "reliability", "observation_reason", "target_name",
           "fink_broker_version", "tns_type_recomputed"]
STRUCT_FIELDS = {
    "pred": ["is_sso", "main_label_classifier"],
    "clf": ["cats_score", "snnSnVsOthers_score"],
    "xm": ["simbad_otype", "gaiadr3_DR3Name", "gaiadr3_Plx", "gaiadr3_e_Plx", "gaiadr3_VarFlag", "legacydr8_zphot",
           "legacydr8_pstar", "tns_type", "tns_redshift", "vsx_Type", "mangrove_lum_dist"],
}


def load_sources(files):
    t = ds.dataset(files, format="parquet").to_table(columns=SCALARS + list(STRUCT_FIELDS))
    df = t.select(SCALARS).to_pandas()
    for s, fields in STRUCT_FIELDS.items():
        col = t.column(s).combine_chunks()
        for f in fields:
            df[f"{s}.{f}"] = col.field(f).to_pandas()
    del t
    df = df.rename(columns={"diaObjectId": "oid", "diaSourceId": "sid", "midpointMjdTai": "mjd", "pred.is_sso": "is_sso",
                            "pred.main_label_classifier": "cats", "clf.cats_score": "cats_score",
                            "clf.snnSnVsOthers_score": "snn"})
    for c in ("xm.simbad_otype", "xm.gaiadr3_DR3Name"):
        df.loc[df[c] == "Fail", c] = None
    tr = df.pop("tns_type_recomputed")
    tr = tr.where(tr != "Unknown").str.replace("(TNS) ", "", regex=False)
    df["tns_type_any"] = tr.fillna(df["xm.tns_type"])
    df["field"] = df.target_name.map(field_group)
    return df


def load_lc_features(files, source_ids, band):
    """Fink SNAD features for one band at the given source rows (difference-flux statistics)."""
    d = ds.dataset(files, format="parquet")
    t = d.to_table(columns=["diaSourceId", "lc_features"], filter=pc.field("diaSourceId").isin(pa.array(source_ids, pa.int64())))
    lc = t.column("lc_features").combine_chunks()
    offsets = lc.offsets.to_numpy()
    parent = np.repeat(np.arange(len(lc)), np.diff(offsets))
    keys = lc.keys.to_numpy(zero_copy_only=False)
    items = lc.items
    assert len(keys) == len(parent) == offsets[-1] - offsets[0]
    sel = keys == band
    out = pd.DataFrame({"sid": t.column("diaSourceId").to_numpy()[parent[sel]]})
    for f in ("standard_deviation", "skew", "stetson_K", "median", "chi2", "amplitude"):
        out[f"lc_{f}"] = items.field(f).to_numpy(zero_copy_only=False)[sel]
    return out


# ----------------------------------------------------------------------------- plotting helpers
def style(plt):
    plt.rcParams.update({
        "font.size": 9, "axes.edgecolor": INK_MUTED, "axes.labelcolor": INK, "xtick.color": INK_MUTED,
        "ytick.color": INK_MUTED, "axes.titlesize": 9.5, "axes.titlecolor": INK, "figure.dpi": 150,
        "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.5, "axes.axisbelow": True,
        "legend.frameon": False, "legend.fontsize": 8, "savefig.bbox": "tight",
    })


def save(fig, out, name, figlist, caption):
    for ext in ("png", "pdf"):
        fig.savefig(out / "figures" / f"{name}.{ext}")
    figlist.append({"file": f"figures/{name}.png", "caption": caption})
    import matplotlib.pyplot as plt
    plt.close(fig)


def smooth_hist(x, y, xr, yr, bins):
    h, xe, ye = np.histogram2d(x, y, bins=bins, range=(xr, yr))
    p = np.pad(h, 1)
    hs = sum(p[i:i + h.shape[0], j:j + h.shape[1]] for i in range(3) for j in range(3)) / 9.0
    return h, hs, xe, ye


def mass_levels(hs, fracs=(0.5, 0.8, 0.95)):
    v = np.sort(hs.ravel())[::-1]
    if v.sum() == 0:
        return []
    cum = np.cumsum(v) / v.sum()
    return sorted({v[min(np.searchsorted(cum, f), len(v) - 1)] for f in fracs if v[min(np.searchsorted(cum, f), len(v) - 1)] > 0})


def contours(ax, x, y, xr, yr, color, bins=None, lw=1.0):
    n = len(x)
    if n < 20:
        return
    bins = bins or int(np.clip(np.sqrt(n) / 2, 20, 60))
    _, hs, xe, ye = smooth_hist(x, y, xr, yr, bins)
    lv = mass_levels(hs)
    if lv:
        ax.contour(0.5 * (xe[1:] + xe[:-1]), 0.5 * (ye[1:] + ye[:-1]), hs.T, levels=lv, colors=color, linewidths=lw)


def note(ax, text, loc="tl"):
    x, ha = (0.03, "left") if loc[1] == "l" else (0.97, "right")
    y, va = (0.97, "top") if loc[0] == "t" else (0.03, "bottom")
    ax.text(x, y, text, transform=ax.transAxes, ha=ha, va=va, fontsize=7.5, color=INK)


COLOUR_PANELS = (
    ("panel_a", "gi", "mag_r", r"$(g-i)_\mathrm{tmpl}$", r"$r_\mathrm{tmpl}$ (template PSF, AB)", (-1.0, 4.0), (14.0, 26.0), True),
    ("panel_b", "ug", "gr", r"$(u-g)_\mathrm{tmpl}$", r"$(g-r)_\mathrm{tmpl}$", (-1.0, 4.0), (-0.75, 2.25), False),
    ("panel_c", "gr", "ri", r"$(g-r)_\mathrm{tmpl}$", r"$(r-i)_\mathrm{tmpl}$", (-0.75, 2.25), (-0.75, 2.75), False),
)


# ----------------------------------------------------------------------------- analyses
def survey(src, req, out, plt, figs, tabs):
    from astropy.coordinates import SkyCoord, Galactic, BarycentricMeanEcliptic
    import astropy.units as u
    dia, sso = src[src.pop_ == "DIA"], src[src.pop_ == "SSO"]

    # S1 sky density, DIA and SSO, Mollweide (RA increases to the left)
    def to_xy(ra, dec):
        w = ((np.asarray(ra) + 180.0) % 360.0) - 180.0
        return -np.radians(w), np.radians(np.asarray(dec))

    lon_e = np.linspace(-180, 180, 181)
    lat_e = np.linspace(-90, 90, 91)
    area = (np.radians(2.0) * (np.sin(np.radians(lat_e[1:])) - np.sin(np.radians(lat_e[:-1]))))[None, :] * (180 / np.pi) ** 2
    gl = SkyCoord(l=np.linspace(0, 360, 721) * u.deg, b=np.zeros(721) * u.deg, frame=Galactic).icrs
    ec = SkyCoord(lon=np.linspace(0, 360, 721) * u.deg, lat=np.zeros(721) * u.deg, frame=BarycentricMeanEcliptic).icrs
    fig, axes = plt.subplots(1, 2, figsize=(13, 3.9), subplot_kw={"projection": "mollweide"}, constrained_layout=True)
    for ax, d, title, cmap in ((axes[0], dia, "DIA alerts", "Blues"), (axes[1], sso, "SSO alerts", "Oranges")):
        w = ((d.ra.to_numpy() + 180.0) % 360.0) - 180.0
        h, _, _ = np.histogram2d(-w, d.dec.to_numpy(), bins=(lon_e, lat_e))
        dens = np.where(h > 0, h / area, np.nan)
        m = ax.pcolormesh(np.radians(lon_e), np.radians(lat_e), np.log10(dens).T, cmap=cmap, rasterized=True)
        for c, ls, lab in ((gl, "-", "Galactic plane"), (ec, "--", "Ecliptic")):
            x, y = to_xy(c.ra.deg, c.dec.deg)
            brk = np.where(np.abs(np.diff(x)) > np.pi)[0] + 1
            for k, (xs, ys) in enumerate(zip(np.split(x, brk), np.split(y, brk))):
                ax.plot(xs, ys, ls, color=INK_MUTED, lw=0.8, label=lab if k == 0 else None)
        ax.set_xticks(np.radians(np.arange(-150, 181, 60)))
        ax.set_xticklabels([f"{int((-t) % 360 / 15)}h" for t in np.arange(-150, 181, 60)], fontsize=7)
        ax.set_title(f"{title} ({len(d):,}); colour = log10 alerts per deg²", loc="left")
        fig.colorbar(m, ax=ax, shrink=0.6, label="log10 alerts deg$^{-2}$")
    axes[0].legend(loc="upper center", bbox_to_anchor=(0.5, -0.04), ncol=2, fontsize=7)
    save(fig, out, "S1_sky_density", figs, "Sky density of delivered DIA and SSO alerts (equatorial Mollweide, RA increasing to the left), with the Galactic plane and ecliptic. SSO alerts trace the ecliptic, which independently supports the is_sso discriminator.")

    # S2 nightly delivered alerts per requested UTC date (qualified TAI boundaries from the catalog)
    req = req.sort_values("start_mjd_tai").reset_index(drop=True)
    starts = req.start_mjd_tai.to_numpy()
    idx = np.searchsorted(starts, src.mjd.to_numpy(), side="right") - 1
    ok = (idx >= 0) & (src.mjd.to_numpy() < req.stop_mjd_tai.to_numpy()[np.clip(idx, 0, None)])
    nightly = pd.crosstab(idx[ok], src.pop_.to_numpy()[ok]).reindex(range(len(req)), fill_value=0)
    nightly.index = pd.to_datetime(req.utc_date)
    nightly.to_csv(out / "tables" / "S2_nightly_alerts.csv")
    tabs["nightly_out_of_window_rows"] = int((~ok).sum())
    fig, ax = plt.subplots(figsize=(12, 3.6), constrained_layout=True)
    ax.bar(nightly.index, nightly["DIA"], width=0.85, color=BLUE, label="DIA")
    ax.bar(nightly.index, nightly["SSO"], bottom=nightly["DIA"], width=0.85, color=ORANGE, label="SSO")
    zero = nightly.index[(nightly.sum(axis=1) == 0)]
    ax.plot(zero, np.full(len(zero), 1), "|", color=INK_MUTED, ms=6, label=f"zero delivered rows ({len(zero)} dates)")
    ax.set_yscale("log")
    ax.set_ylim(0.8, None)
    ax.set_ylabel("alerts per UTC date")
    ax.legend(loc="upper right", ncol=3)
    ax.set_title("Delivered alerts per requested UTC date, 2026-02-25 to 2026-07-13 (zero rows ≠ zero Rubin alerts)", loc="left")
    save(fig, out, "S2_nightly_alerts", figs, "Delivered alerts per requested UTC date (half-open TAI boundaries from the catalog). Ticks at the bottom mark dates with zero delivered rows; these are not proof of zero Rubin alerts.")

    # S3 field composition: CATS class mix (DIA sources) and band mix per field
    dia_f = dia.groupby(["field", "cats"]).size().unstack(fill_value=0)
    band_f = src.groupby(["field", "band"]).size().unstack(fill_value=0)
    fields = [f for f in FIELD_ORDER if f in band_f.index]
    fig, axes = plt.subplots(1, 2, figsize=(13, 3.9), constrained_layout=True, sharey=True)
    y = np.arange(len(fields))[::-1]
    left = np.zeros(len(fields))
    frac = dia_f.reindex(fields).fillna(0)
    frac = frac.div(frac.sum(axis=1), axis=0)
    for c in CATS_ORDER:
        v = frac.get(c, pd.Series(0, index=fields)).to_numpy()
        axes[0].barh(y, v, left=left, color=CATS_COLOR[c], edgecolor="white", linewidth=1, label=CATS_NAME[c])
        left += v
    axes[0].set_yticks(y)
    axes[0].set_yticklabels([f"{f}  ({int(band_f.loc[f].sum()):,})" for f in fields])
    axes[0].set_xlim(0, 1)
    axes[0].set_xlabel("fraction of DIA alerts")
    axes[0].set_title("Fink CATS class at each DIA alert", loc="left")
    axes[0].legend(loc="upper center", bbox_to_anchor=(0.5, -0.18), ncol=3)
    left = np.zeros(len(fields))
    bf = band_f.reindex(fields).fillna(0)
    bf = bf.div(bf.sum(axis=1), axis=0)
    for b in BANDS:
        v = bf.get(b, pd.Series(0, index=fields)).to_numpy()
        axes[1].barh(y, v, left=left, color=BAND_COLOR[b], edgecolor="white", linewidth=1, label=b)
        left += v
    axes[1].set_xlim(0, 1)
    axes[1].set_xlabel("fraction of all alerts (DIA + SSO)")
    axes[1].set_title("Band of each alert", loc="left")
    axes[1].legend(loc="upper center", bbox_to_anchor=(0.5, -0.18), ncol=6)
    save(fig, out, "S3_field_composition", figs, "Composition of each observing-programme group (from target_name; alert counts in brackets): Fink CATS class mix of DIA alerts and band mix of all alerts.")

    # T1 field summary, T2 observation_reason x band
    g = src.groupby("field")
    t1 = pd.DataFrame({
        "alerts": g.size(), "dia_alerts": g.apply(lambda d: int((d.pop_ == "DIA").sum())),
        "sso_alerts": g.apply(lambda d: int((d.pop_ == "SSO").sum())),
        "dia_objects": dia.groupby("field").oid.nunique(),
        "median_reliability": g.reliability.median().round(3), "median_snr": g.snr.median().round(2),
        "median_abs_gal_b_deg": g.gal_b.apply(lambda v: float(np.median(np.abs(v)))).round(1),
    }).reindex(fields)
    for c in CATS_ORDER:
        t1[f"frac_dia_{CATS_NAME[c]}"] = frac.get(c, pd.Series(0, index=fields)).round(4)
    for col, name in (("xm.simbad_otype", "simbad"), ("xm.gaiadr3_DR3Name", "gaia"), ("xm.legacydr8_pstar", "legacy"), ("xm.vsx_Type", "vsx")):
        t1[f"frac_dia_with_{name}"] = dia.groupby("field")[col].apply(lambda v: v.notna().mean()).reindex(fields).round(4)
    t1.to_csv(out / "tables" / "T1_field_summary.csv")
    pd.crosstab(src.observation_reason, src.band, margins=True).sort_values("All", ascending=False).to_csv(out / "tables" / "T2_observation_reason_by_band.csv")
    tabs["T1"] = t1


def star_galaxy(obj, out, plt, figs, tabs):
    o = obj[obj.panel_a | obj.panel_b]
    ps = o.pstar
    ext, pt = o[ps < 0.5], o[ps >= 0.5]
    tabs["C_counts"] = {"plotted_objects": int(len(o)), "with_pstar": int(ps.notna().sum()), "extended_pstar_lt_0.5": int(len(ext)),
                        "point_pstar_ge_0.5": int(len(pt)), "pstar_exactly_0_or_1_frac": float(((ps == 0) | (ps == 1)).sum() / max(ps.notna().sum(), 1))}
    fig, axes = plt.subplots(2, 2, figsize=(11, 9), constrained_layout=True)
    # Top-left substitute: point-like fraction vs r_tmpl (with counts)
    ax = axes[0, 0]
    a = o[o.panel_a & ps.notna()]
    bins = np.arange(15, 25.01, 0.5)
    k, _ = np.histogram(a.mag_r[a.pstar >= 0.5], bins)
    n, _ = np.histogram(a.mag_r, bins)
    c = 0.5 * (bins[1:] + bins[:-1])
    good = n >= 10
    lo, hi = np.array([wilson(kk, nn) for kk, nn in zip(k, n)]).T
    ax.fill_between(c[good], lo[good], hi[good], color=BLUE, alpha=0.2, lw=0)
    ax.plot(c[good], (k / np.maximum(n, 1))[good], "-o", color=BLUE, ms=3.5, lw=1.6)
    ax2 = ax.twinx()
    ax2.bar(c, n, width=0.45, color=NEUTRAL, alpha=0.6, zorder=0)
    ax2.set_ylabel("objects per bin", color=INK_MUTED)
    ax2.grid(False)
    ax.set_zorder(ax2.get_zorder() + 1)
    ax.patch.set_visible(False)
    ax.set_xlabel(r"$r_\mathrm{tmpl}$ (template PSF, AB)")
    ax.set_ylabel("fraction point-like (Legacy DR8 pstar ≥ 0.5)")
    ax.set_ylim(0, 1)
    ax.set_title("Replaces r PSF − CModel: point-like fraction vs brightness", loc="left")
    note(ax, f"n = {len(a):,}; band = 95% Wilson interval", "tr")
    pos = [(0, 1), (1, 0), (1, 1)]
    for (key, x, y, xl, yl, xr, yr, inv), (i, j) in zip(COLOUR_PANELS, pos):
        ax = axes[i, j]
        e, p = ext[ext[key]], pt[pt[key]]
        contours(ax, e[x], e[y], xr, yr, RED, lw=1.1)
        inside = p[x].between(*xr) & p[y].between(*yr)
        ax.scatter(p.loc[inside, x], p.loc[inside, y], s=6, color=BLUE, alpha=0.75, edgecolors="none", rasterized=True)
        ax.set_xlim(*xr)
        ax.set_ylim(*(yr[::-1] if inv else yr))
        ax.set_xlabel(xl)
        ax.set_ylabel(yl)
        note(ax, f"extended (red contours, 50/80/95%): {len(e):,}\npoint-like (blue dots): {len(p):,}")
    fig.suptitle("Template colours of alerting DIA objects split by Legacy Survey DR8 morphology (pstar), the reference-figure layout", fontsize=10)
    save(fig, out, "C1_star_galaxy_colour_panels", figs, "Reference-figure layout rebuilt with the only morphology in the data: Legacy DR8 pstar from Fink's crossmatch. Red contours: extended (pstar < 0.5); blue dots: point-like (pstar ≥ 0.5). Top left replaces r PSF − CModel with the point-like fraction vs template r magnitude.")


def classifier_validation(obj, out, plt, figs, tabs):
    o = obj[obj.tns_type.notna() | obj.vsx_type.notna() | obj.simbad_otype.notna()].copy()
    o["truth"] = o.apply(truth_group, axis=1)
    o = o[o.truth.notna()]
    ct = pd.crosstab(o.truth, o.cats).reindex(index=[t for t in TRUTH_ORDER if t in set(o.truth)], columns=CATS_ORDER, fill_value=0)
    ct.columns = [CATS_NAME[c] for c in ct.columns]
    ct.to_csv(out / "tables" / "K1_confusion_counts_latest_snapshot.csv")
    # recall of the expected class, all objects and classified objects only (cats != -1)
    rows = []
    for t, exp in TRUTH_EXPECTED.items():
        d = o[o.truth == t]
        if not len(d):
            continue
        for scope, dd in (("all", d), ("classified (≥2 detections in Rubin)", d[d.cats != -1])):
            k = int((dd.cats == exp).sum())
            lo, hi = wilson(k, len(dd))
            rows.append({"truth_group": t, "expected_cats": CATS_NAME[exp], "scope": scope, "n": len(dd), "hits": k,
                         "recall": round(k / len(dd), 4) if len(dd) else np.nan, "ci95_lo": round(lo, 4), "ci95_hi": round(hi, 4)})
    rec = pd.DataFrame(rows)
    rec.to_csv(out / "tables" / "K2_recall_expected_class.csv", index=False)
    tabs["K2"] = rec

    fig, axes = plt.subplots(1, 2, figsize=(14, 5.2), constrained_layout=True, gridspec_kw={"width_ratios": [1.25, 1]})
    ax = axes[0]
    frac = ct.div(ct.sum(axis=1), axis=0)
    im = ax.imshow(frac.to_numpy(), cmap="Blues", vmin=0, vmax=1, aspect="auto")
    for i in range(frac.shape[0]):
        for j in range(frac.shape[1]):
            v, n = frac.iat[i, j], ct.iat[i, j]
            ax.text(j, i, f"{v:.2f}\n({n:,})", ha="center", va="center", fontsize=6.8, color="white" if v > 0.55 else INK)
    for i, t in enumerate(frac.index):
        if t in TRUTH_EXPECTED:
            j = list(frac.columns).index(CATS_NAME[TRUTH_EXPECTED[t]])
            ax.add_patch(__import__("matplotlib").patches.Rectangle((j - 0.5, i - 0.5), 1, 1, fill=False, ec=RED, lw=1.6))
    ax.set_xticks(range(frac.shape[1]))
    ax.set_xticklabels(frac.columns, rotation=25, ha="right")
    ax.set_yticks(range(frac.shape[0]))
    ax.set_yticklabels([f"{t}  (n={int(ct.loc[t].sum()):,})" for t in frac.index])
    ax.grid(False)
    ax.set_xlabel("Fink CATS class at the object's latest delivered alert")
    ax.set_title("Row-normalised fraction (count); red box = expected class", loc="left")
    fig.colorbar(im, ax=ax, shrink=0.7, label="fraction of row")
    # recall vs number of delivered detections
    ax = axes[1]
    edges = [1, 2, 3, 5, 10, 20, 10_000]
    labels = ["1", "2", "3–4", "5–9", "10–19", "≥20"]
    groups = [g for g in TRUTH_EXPECTED if (o.truth == g).sum() >= 100]
    markers = {"SN (TNS spectroscopic)": "o", "SLSN / TDE (TNS spectroscopic)": "D", "CV / nova (VSX)": "v",
               "Eclipsing binary (VSX)": "s", "Pulsating star (VSX)": "^", "Rotational variable (VSX)": "P", "QSO / AGN (SIMBAD)": "o"}
    rows = []
    for gi, g in enumerate(groups):
        d = o[o.truth == g]
        cut = pd.cut(d.n_det, edges, right=False, labels=labels)
        xs, ys, los, his = [], [], [], []
        for li, lab in enumerate(labels):
            dd = d[cut == lab]
            if len(dd) < 15:
                continue
            k = int((dd.cats == TRUTH_EXPECTED[g]).sum())
            lo, hi = wilson(k, len(dd))
            xs.append(li); ys.append(k / len(dd)); los.append(lo); his.append(hi)
            rows.append({"truth_group": g, "n_det_bin": lab, "n": len(dd), "recall": k / len(dd), "ci95_lo": lo, "ci95_hi": hi})
        ax.errorbar(np.array(xs) + (gi - len(groups) / 2) * 0.06, ys, yerr=[np.clip(np.array(ys) - los, 0, None), np.clip(np.array(his) - ys, 0, None)],
                    fmt="-" + markers[g], ms=4.5, lw=1.4, capsize=2, color=CATS_COLOR[TRUTH_EXPECTED[g]], label=f"{g} → {CATS_NAME[TRUTH_EXPECTED[g]]}")
    pd.DataFrame(rows).to_csv(out / "tables" / "K3_recall_vs_detections.csv", index=False)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels)
    ax.set_xlabel("delivered detections of the object in this cohort")
    ax.set_ylabel("recall of expected CATS class (95% Wilson)")
    ax.set_ylim(0, 1)
    ax.set_title("Recall vs detections (groups with ≥100 objects; bins with ≥15)", loc="left")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.14), ncol=2, fontsize=7)
    save(fig, out, "K1_classifier_validation", figs, "Fink CATS broad class (latest delivered snapshot) against external labels from Fink's own crossmatches: TNS spectroscopic types, VSX variable-star types and SIMBAD QSO/AGN. Left: confusion matrix. Right: recall of the expected class vs delivered detections.")

    # K2 SuperNNova SN-vs-others ROC: TNS SNe vs VSX periodic stars and SIMBAD QSO/AGN
    pos = o[o.truth == "SN (TNS spectroscopic)"].snn.dropna().to_numpy()
    neg_groups = ["Eclipsing binary (VSX)", "Pulsating star (VSX)", "Rotational variable (VSX)", "QSO / AGN (SIMBAD)"]
    negd = o[o.truth.isin(neg_groups)]
    neg = negd.snn.dropna().to_numpy()
    rng = np.random.default_rng(20261008)
    res = {"n_pos": int(len(pos)), "n_neg": int(len(neg))}
    if len(pos) >= 20 and len(neg) >= 20:
        a = auc(pos, neg)
        boots = [auc(rng.choice(pos, len(pos)), rng.choice(neg, len(neg))) for _ in range(1000)]
        res.update({"auc": round(float(a), 4), "auc_ci95": [round(float(np.percentile(boots, 2.5)), 4), round(float(np.percentile(boots, 97.5)), 4)]})
        fig, axes = plt.subplots(1, 2, figsize=(11, 4.4), constrained_layout=True)
        thr = np.linspace(0, 1, 501)
        tpr = [(pos >= t).mean() for t in thr]
        fpr = [(neg >= t).mean() for t in thr]
        axes[0].plot(fpr, tpr, color=BLUE, lw=2, label="all objects")
        pc_ = o[(o.truth == "SN (TNS spectroscopic)") & (o.cats != -1)].snn.dropna().to_numpy()
        nc_ = negd[negd.cats != -1].snn.dropna().to_numpy()
        a2 = auc(pc_, nc_)
        b2 = [auc(rng.choice(pc_, len(pc_)), rng.choice(nc_, len(nc_))) for _ in range(1000)]
        res.update({"auc_classified_only": round(float(a2), 4), "auc_classified_only_ci95": [round(float(np.percentile(b2, 2.5)), 4), round(float(np.percentile(b2, 97.5)), 4)],
                    "n_pos_classified": int(len(pc_)), "n_neg_classified": int(len(nc_))})
        axes[0].plot([(nc_ >= t).mean() for t in thr], [(pc_ >= t).mean() for t in thr], color=ORANGE, lw=2, label="excluding first alerts (CATS ≠ −1)")
        axes[0].legend(loc="upper left")
        axes[0].plot([0, 1], [0, 1], ":", color=INK_MUTED, lw=1)
        axes[0].set_xlabel("false-positive rate (non-SN reference labels)")
        axes[0].set_ylabel("true-positive rate (TNS SNe)")
        axes[0].set_title("SuperNNova SN-vs-others at latest snapshot", loc="left")
        note(axes[0], f"all: AUC = {a:.3f} [{res['auc_ci95'][0]:.3f}, {res['auc_ci95'][1]:.3f}]; SNe {len(pos):,}, non-SNe {len(neg):,}\n"
                      f"excl. first alerts: AUC = {a2:.3f} [{res['auc_classified_only_ci95'][0]:.3f}, {res['auc_classified_only_ci95'][1]:.3f}]; SNe {len(pc_):,}, non-SNe {len(nc_):,}\n(1,000-sample bootstrap 95%)", "br")
        b = np.linspace(0, 1, 41)
        axes[1].hist(neg, b, density=True, histtype="step", lw=1.8, color=ORANGE, label="VSX periodic + SIMBAD QSO/AGN")
        axes[1].hist(pos, b, density=True, histtype="step", lw=1.8, color=BLUE, label="TNS spectroscopic SNe")
        axes[1].set_xlabel("snnSnVsOthers_score")
        axes[1].set_ylabel("density")
        axes[1].legend(loc="upper left")
        axes[1].set_title("Score distributions", loc="left")
        save(fig, out, "K2_supernnova_roc", figs, "SuperNNova SN-vs-others score at each object's latest snapshot: TNS spectroscopic SNe vs VSX periodic variables and SIMBAD QSO/AGN. AUC with 1,000-sample bootstrap interval.")
    tabs["K_roc"] = res
    tabs["K_truth_counts"] = o.truth.value_counts().to_dict()


def label_evolution(dia, out, plt, figs, tabs):
    d = dia[["oid", "mjd", "sid", "cats"]].sort_values(["oid", "mjd", "sid"])
    d["k"] = d.groupby("oid").cumcount() + 1
    nxt = d.groupby("oid").cats.shift(-1)
    m = nxt.notna()
    tm = pd.crosstab(d.cats[m], nxt[m].astype(int)).reindex(index=CATS_ORDER, columns=CATS_ORDER, fill_value=0)
    tm.to_csv(out / "tables" / "L1_transition_counts.csv")
    final = d.groupby("oid").cats.transform("last")
    ndet = d.groupby("oid").k.transform("max")
    fig, axes = plt.subplots(1, 3, figsize=(15.5, 4.5), constrained_layout=True, gridspec_kw={"width_ratios": [1, 1.25, 1]})
    ax = axes[0]
    f = tm.div(tm.sum(axis=1).replace(0, np.nan), axis=0)
    ax.imshow(f.to_numpy(), cmap="Blues", vmin=0, vmax=1)
    for i in range(6):
        for j in range(6):
            v = f.iat[i, j]
            if np.isfinite(v):
                ax.text(j, i, f"{v:.2f}", ha="center", va="center", fontsize=7, color="white" if v > 0.55 else INK)
    names = ["SN", "Fast", "Long", "Per.", "AGN", "−1"]
    ax.set_xticks(range(6)); ax.set_xticklabels(names)
    ax.set_yticks(range(6)); ax.set_yticklabels([f"{n} ({int(tm.iloc[i].sum()):,})" for i, n in enumerate(names)])
    ax.grid(False)
    ax.set_xlabel("class at next delivered detection")
    ax.set_ylabel("class at detection k")
    ax.set_title("Transition probabilities", loc="left")
    ax = axes[1]
    kk = d.k.clip(upper=30)
    share = pd.crosstab(kk, d.cats, normalize="index").reindex(columns=CATS_ORDER, fill_value=0)
    ax.stackplot(share.index, *[share[c] for c in CATS_ORDER], colors=[CATS_COLOR[c] for c in CATS_ORDER],
                 labels=[CATS_NAME[c] for c in CATS_ORDER], edgecolor="white", linewidth=0.6)
    ax.set_xlim(1, 30)
    ax.set_ylim(0, 1)
    ax.set_xlabel("delivered detection index k (30 = 30 or more)")
    ax.set_ylabel("fraction of alerts")
    ax.set_title("Class mix vs detection index", loc="left")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.16), ncol=3, fontsize=7)
    ax = axes[2]
    for nmin, col in ((5, ORANGE), (10, BLUE), (20, AQUA)):
        sel = ndet >= nmin
        agree = (d.cats[sel] == final[sel]).groupby(d.k[sel].clip(upper=nmin)).mean()
        n_obj = int(d[sel].oid.nunique())
        ax.plot(agree.index, agree.to_numpy(), "-o", ms=3, color=col, lw=1.6, label=f"objects with ≥{nmin} detections ({n_obj:,})")
    ax.set_xlabel("delivered detection index k")
    ax.set_xticks([1, 5, 10, 15, 20])
    ax.set_ylabel("P(class at k = class at final detection)")
    ax.set_ylim(0, 1.02)
    ax.set_title("How early the final label is reached", loc="left")
    ax.legend(loc="lower right", fontsize=7)
    save(fig, out, "L1_label_evolution", figs, "Evolution of the Fink CATS class across an object's delivered detections. k counts detections inside this cohort; the classifier itself sees Rubin's full previous-source history, which can be longer.")
    first_alerts = d[d.cats == -1].groupby("oid").size()
    tabs["L"] = {"consecutive_pairs": int(m.sum()), "pairs_changing_class": int((d.cats[m] != nxt[m]).sum()),
                 "objects_with_ge2_first_alerts": int((first_alerts >= 2).sum()), "first_alert_followed_by_first_alert": int(tm.loc[-1, -1]),
                 "objects_ge2_det": int((d.groupby("oid").size() >= 2).sum())}


def variability(obj, files, out, plt, figs, tabs):
    sel = obj[(obj.n_det_i >= 5) & obj.ok_i]
    lc = load_lc_features(files, sel.latest_sid_i.astype("int64").to_numpy(), "i")
    v = sel.merge(lc, left_on="latest_sid_i", right_on="sid", how="inner")
    v["frac_std"] = v.lc_standard_deviation / v.tf_i
    v = v[np.isfinite(v.frac_std) & (v.frac_std > 0) & np.isfinite(v.lc_skew)]
    v[["dia_object_id", "cats", "mag_i", "frac_std", "lc_skew", "lc_stetson_K", "n_det_i"]].to_parquet(out / "tables" / "V_variability_i_band.parquet", index=False)
    tabs["V"] = {"objects": int(len(v))}
    fig, axes = plt.subplots(1, 5, figsize=(17, 3.9), constrained_layout=True, sharex=True, sharey=True)
    xr, yr = (15, 24.5), (-2.5, 1.5)
    for ax, c in zip(axes, [11, 12, 13, 21, 22]):
        contours(ax, v.mag_i, np.log10(v.frac_std), xr, yr, INK_MUTED, lw=0.7)
        s = v[v.cats == c]
        ax.scatter(s.mag_i, np.log10(s.frac_std), s=3 if len(s) > 3000 else 6, color=CATS_COLOR[c], alpha=0.6, edgecolors="none", rasterized=True)
        ax.set_title(f"{CATS_NAME[c]} (n={len(s):,})", loc="left")
        ax.set_xlim(*xr)
        ax.set_ylim(*yr)
        ax.set_xlabel(r"$i_\mathrm{tmpl}$")
    axes[0].set_ylabel(r"log$_{10}$ [σ(i difference flux) / $f_{i,\mathrm{tmpl}}$]")
    fig.suptitle(f"i-band variability amplitude relative to the static flux, by latest CATS class (grey: all {len(v):,} objects with ≥5 delivered i detections)", fontsize=10)
    save(fig, out, "V1_amplitude_vs_template_mag", figs, "Fractional i-band variability: standard deviation of the difference flux over Fink's light-curve history divided by the template flux, against template i magnitude. Only detected epochs enter (|psfFlux| > 5σ), so amplitudes are biased high near the detection limit.")
    # V2: Sesar-style colour-colour coloured by skewness
    g = v[v.panel_a]
    from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm
    cmap = LinearSegmentedColormap.from_list("div", [BLUE, NEUTRAL, ORANGE])
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.6), constrained_layout=True)
    for ax, (key, x, y, xl, yl, xr2, yr2, inv) in zip(axes, (COLOUR_PANELS[2], COLOUR_PANELS[0])):
        sc = ax.scatter(g[x], g[y], c=g.lc_skew.clip(-2, 2), cmap=cmap, norm=TwoSlopeNorm(0, -2, 2), s=9, edgecolors="none", alpha=0.85, rasterized=True)
        ax.set_xlim(*xr2)
        ax.set_ylim(*(yr2[::-1] if inv else yr2))
        ax.set_xlabel(xl)
        ax.set_ylabel(yl)
        note(ax, f"n = {len(g):,}")
    fig.colorbar(sc, ax=axes, shrink=0.8, label="skewness of i difference flux (clipped ±2)\n< 0: dimming episodes (eclipses)   > 0: brightening (flares, outbursts)")
    fig.suptitle("Light-curve skewness across colour space (after Sesar et al. 2007, Fig. 10)", fontsize=10)
    save(fig, out, "V2_colour_colour_by_skewness", figs, "Template colours coloured by the skewness of the i-band difference-flux light curve (flux convention: negative = dimming, the opposite sign of Sesar's magnitude skewness).")


def distances(obj, out, plt, figs, tabs):
    # D1 Gaia parallax HR diagram from template photometry
    plx_snr = obj.plx / obj.eplx
    h = obj[(plx_snr > 5) & (obj.plx > 0) & obj.ok_r & obj.ok_i].copy()
    h["M_i"] = h.mag_i + 5 * np.log10(h.plx) - 10
    h["ri"] = h.mag_r - h.mag_i
    h["dust"] = np.where(h.field.isin(["Galactic bulge", "Galactic plane"]), "Bulge and plane fields", "Other fields")
    tabs["D_gaia"] = {"objects": int(len(h)), "by_field": h.dust.value_counts().to_dict()}
    fig, axes = plt.subplots(1, 2, figsize=(11, 5), constrained_layout=True, sharex=True, sharey=True)
    for ax, lab, col in zip(axes, ["Other fields", "Bulge and plane fields"], [BLUE, ORANGE]):
        s = h[h.dust == lab]
        contours(ax, h.ri, h.M_i, (-0.5, 2.5), (-4, 16), INK_MUTED, lw=0.7)
        ax.scatter(s.ri, s.M_i, s=5, color=col, alpha=0.6, edgecolors="none", rasterized=True)
        ax.set_xlim(-0.5, 2.5)
        ax.set_ylim(16, -4)
        ax.set_xlabel(r"$(r-i)_\mathrm{tmpl}$")
        ax.set_title(f"{lab} (n={len(s):,})", loc="left")
    axes[0].set_ylabel(r"$M_{i,\mathrm{tmpl}} = i_\mathrm{tmpl} + 5\log_{10}\varpi[\mathrm{mas}] - 10$")
    fig.suptitle("Absolute-magnitude diagram of alerting DIA objects with Gaia DR3 parallax S/N > 5 (no extinction correction)", fontsize=10)
    save(fig, out, "D1_gaia_hr_diagram", figs, "Template-photometry HR diagram for alerting objects whose Fink Gaia DR3 crossmatch has parallax S/N > 5. Grey contours: all such objects. Bulge and plane fields are shown separately because uncorrected reddening shifts them down and to the right.")

    # D2 observed peak absolute magnitude vs redshift
    distmod = flat_lcdm_distmod
    e = obj[(obj.pstar < 0.5) & (obj.zphot > 0.02) & (obj.zphot < 1.5) & (obj.peak_snr_i >= 5)].copy()
    e["M_peak_i"] = e.peak_mag_i - distmod(e.zphot.to_numpy())
    t = obj[obj.tns_type.notna() & (obj.tns_z > 0) & (obj.peak_snr_any >= 5)].copy()
    t["M_peak"] = t.peak_mag_any - distmod(t.tns_z.to_numpy())
    t["tns_grp"] = np.select([t.tns_type.str.startswith("SN Ia"), t.tns_type.str.startswith("SN II"), t.tns_type.str.startswith("SLSN")],
                             ["SN Ia", "SN II", "SLSN"], "Other")
    tabs["D_peak"] = {"legacy_zphot_objects": int(len(e)), "tns_spec_z_objects": int(len(t)), "tns_groups": t.tns_grp.value_counts().to_dict()}
    t[["dia_object_id", "tns_type", "tns_z", "peak_band_any", "peak_mag_any", "M_peak", "n_det", "cats"]].to_csv(out / "tables" / "D2_tns_peak_absolute_mags.csv", index=False)
    fig, axes = plt.subplots(1, 4, figsize=(17, 4.3), constrained_layout=True, gridspec_kw={"width_ratios": [1, 1, 1, 1.25]})
    for ax, c in zip(axes[:3], [11, 13, 22]):
        contours(ax, e.zphot, e.M_peak_i, (0, 1.5), (-26, -10), INK_MUTED, lw=0.7)
        s = e[e.cats == c]
        ax.scatter(s.zphot, s.M_peak_i, s=3 if len(s) > 3000 else 6, color=CATS_COLOR[c], alpha=0.55, edgecolors="none", rasterized=True)
        ax.set_xlim(0, 1.5)
        ax.set_ylim(-10, -26)
        ax.set_xlabel("Legacy DR8 host photo-z")
        ax.set_title(f"{CATS_NAME[c]} (n={len(s):,})", loc="left")
    axes[0].set_ylabel(r"brightest delivered $M_i$ (difference flux; no K-corr.)")
    ax = axes[3]
    mk = {"SN Ia": ("o", BLUE), "SN II": ("s", ORANGE), "SLSN": ("D", AQUA), "Other": ("^", INK_MUTED)}
    for g2, (m, col) in mk.items():
        s = t[t.tns_grp == g2]
        ax.scatter(s.tns_z, s.M_peak, marker=m, s=22, color=col, edgecolors="white", linewidths=0.4, label=f"{g2} ({len(s)})")
    ax.axhline(-19.3, color=BLUE, ls=":", lw=1)
    ax.text(0.98, -19.45, "typical SN Ia peak (−19.3)", color=INK_MUTED, fontsize=7, ha="right", va="bottom", transform=ax.get_yaxis_transform())
    ax.set_ylim(-10, -24)
    ax.set_xlabel("TNS redshift")
    ax.set_ylabel("brightest delivered M (any band)")
    ax.set_title("TNS-typed transients (spectroscopic z)", loc="left")
    ax.legend(loc="lower right", fontsize=7)
    fig.suptitle("Brightest delivered difference-flux absolute magnitude vs redshift (flat ΛCDM, Planck18 H0 and Ωm; lower limit on peak luminosity; no K or extinction correction)", fontsize=10)
    save(fig, out, "D2_peak_absolute_magnitude", figs, "Brightest delivered difference-flux magnitude converted to absolute magnitude with host photo-z (Legacy DR8, extended hosts) or TNS redshift. Peaks are rarely sampled in Light Static, so values are lower limits on peak luminosity; the faint envelope rising with redshift is the alert detection limit (Malmquist bias), not physics.")


def quality(src, obj, out, plt, figs, tabs):
    dia = src[src.pop_ == "DIA"]
    fig, axes = plt.subplots(1, 3, figsize=(15.5, 4.3), constrained_layout=True)
    ax = axes[0]
    for b in BANDS:
        r = np.sort(dia.reliability[dia.band == b].dropna().to_numpy())
        if len(r) < 100:
            continue
        ax.plot(r, np.arange(1, len(r) + 1) / len(r), color=BAND_COLOR[b], lw=1.6, label=f"{b} ({len(r):,})")
    ax.set_xlabel("reliability (real/bogus score)")
    ax.set_ylabel("cumulative fraction of DIA alerts")
    ax.set_title("Reliability by band", loc="left")
    ax.legend(loc="upper left")
    ax = axes[1]
    s = dia[(dia.band == "i") & (dia.templateFlux > 0) & (dia.templateFlux / dia.templateFluxErr >= 5)]
    m = AB_ZP_NJY - 2.5 * np.log10(s.templateFlux)
    bins = np.arange(14, 25.01, 0.5)
    cut = pd.cut(m, bins)
    q = s.reliability.groupby(cut, observed=True).quantile([0.16, 0.5, 0.84]).unstack()
    nb = s.reliability.groupby(cut, observed=True).size()
    q = q[nb.reindex(q.index) >= 200]
    c = np.array([iv.mid for iv in q.index])
    ax.fill_between(c, q[0.16], q[0.84], color=BLUE, alpha=0.2, lw=0)
    ax.plot(c, q[0.5], "-o", color=BLUE, ms=3, lw=1.6)
    ax.set_xlabel(r"per-alert $i_\mathrm{tmpl}$ (template S/N ≥ 5)")
    ax.set_ylabel("reliability: median and 16–84%")
    ax.set_title("Reliability vs static brightness (i band)", loc="left")
    note(ax, f"n = {len(s):,} alerts; bins with ≥200 alerts", "br")
    ax = axes[2]
    key, x, y, xl, yl, xr, yr, _ = COLOUR_PANELS[2]
    a = obj[obj[key]]
    hi = a[a.median_reliability >= 0.9]
    contours(ax, a[x], a[y], xr, yr, INK_MUTED, lw=1.0)
    contours(ax, hi[x], hi[y], xr, yr, BLUE, lw=1.2)
    ax.set_xlim(*xr)
    ax.set_ylim(*yr)
    ax.set_xlabel(xl)
    ax.set_ylabel(yl)
    ax.set_title("Colour panel robustness to a reliability cut", loc="left")
    note(ax, f"grey: all ({len(a):,})\nblue: median reliability ≥ 0.9 ({len(hi):,})")
    save(fig, out, "Q1_data_quality", figs, "Alert reliability by band and by static brightness, and the (g−r, r−i) template colour distribution before and after requiring median reliability ≥ 0.9. Contours enclose 50/80/95%.")
    tabs["Q"] = {"dia_reliability_quantiles": dia.reliability.quantile([0.1, 0.5, 0.9]).round(4).to_dict(),
                 "colour_objects_all": int(len(a)), "colour_objects_rel_ge_0.9": int(len(hi))}


def sso(src, out, plt, figs, tabs):
    s = src[src.pop_ == "SSO"]
    d = src[src.pop_ == "DIA"]
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.2), constrained_layout=True)
    ax = axes[0]
    b = np.arange(0, 72.1, 2)
    off = ~src.field.isin(["Galactic bulge", "Galactic plane"])
    ns, _ = np.histogram(s.ecl_lat[off[s.index]].abs(), b)
    nd, _ = np.histogram(d.ecl_lat[off[d.index]].abs(), b)
    n = ns + nd
    good = n >= 1000
    c = 0.5 * (b[1:] + b[:-1])
    f = ns / np.maximum(n, 1)
    lo, hi = np.array([wilson(k, nn) for k, nn in zip(ns, n)]).T
    ax.fill_between(c[good], lo[good], hi[good], color=ORANGE, alpha=0.25, lw=0)
    ax.plot(c[good], f[good], "-o", ms=3.5, color=ORANGE, lw=1.6)
    ax.set_xlabel("|ecliptic latitude β| (deg)")
    ax.set_ylabel("SSO share of all alerts in the bin")
    ax.set_title("SSO share of alerts vs |β| (bulge and plane fields excluded)", loc="left")
    note(ax, "2° bins with ≥1,000 alerts; 95% Wilson band", "tr")
    tabs["O_sso_share_by_abs_beta"] = {f"{int(b[i])}-{int(b[i+1])}": [int(ns[i]), int(n[i])] for i in range(len(c)) if good[i]}
    ax = axes[1]
    for band in BANDS:
        f = s.psfFlux[(s.band == band) & (s.psfFlux > 0)]
        if len(f) < 500:
            continue
        ax.hist(AB_ZP_NJY - 2.5 * np.log10(f), np.arange(16, 25.01, 0.2), histtype="step", lw=1.6, color=BAND_COLOR[band], label=f"{band} ({len(f):,})")
    ax.set_yscale("log")
    ax.set_xlabel("SSO difference-flux magnitude (AB)")
    ax.set_ylabel("alerts per 0.2 mag")
    ax.set_title("SSO brightness by band (no object identity, no colours)", loc="left")
    ax.legend(loc="upper left")
    save(fig, out, "O1_sso_population", figs, "SSO alerts (kept separate per analysis_contract_v1). Left: SSO share of all alerts per 2° bin of |ecliptic latitude|, outside the dense bulge and plane fields; the share is a ratio, so it still depends on where the survey pointed. Right: difference-flux magnitude distribution per band.")
    tabs["O"] = {"sso_alerts": int(len(s)), "sso_frac_abs_beta_lt_10": round(float((s.ecl_lat.abs() < 10).mean()), 4),
                 "dia_frac_abs_beta_lt_10": round(float((d.ecl_lat.abs() < 10).mean()), 4)}


# ----------------------------------------------------------------------------- main
def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--catalog", required=True)
    p.add_argument("--data-root", required=True)
    p.add_argument("--template-photometry", required=True, help="dia_object_template_photometry.parquet from the colour run")
    p.add_argument("--out", required=True)
    p.add_argument("--code-sha", required=True)
    a = p.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=False)
    (out / "figures").mkdir()
    (out / "tables").mkdir()
    t0 = time.time()

    conn = open_catalog(a.catalog, a.data_root)
    files = [r[0] for r in conn.execute("SELECT DISTINCT raw_file FROM sources ORDER BY 1").fetchall()]
    qc = conn.execute("SELECT sum(total_rows), sum(dia_rows), sum(sso_rows), sum(ambiguous_rows) FROM source_qc").fetchone()
    req = conn.execute("SELECT utc_date, start_mjd_tai, stop_mjd_tai FROM requested_date_boundaries").df()
    conn.close()

    src = load_sources(files)
    src["pop_"] = np.where((src.is_sso == False) & (src.oid > 0), "DIA",  # noqa: E712
                           np.where((src.is_sso == True) & ((src.oid == 0) | src.oid.isna()), "SSO", "AMBIGUOUS"))  # noqa: E712
    counts = src.pop_.value_counts().to_dict()
    assert len(src) == qc[0] and counts.get("DIA", 0) == qc[1] and counts.get("SSO", 0) == qc[2] and counts.get("AMBIGUOUS", 0) == qc[3], (counts, qc)
    assert src.sid.is_unique

    from astropy.coordinates import SkyCoord
    import astropy.units as u
    sc = SkyCoord(ra=src.ra.to_numpy() * u.deg, dec=src.dec.to_numpy() * u.deg, frame="icrs")
    src["gal_b"] = sc.galactic.b.deg
    from astropy.coordinates import BarycentricMeanEcliptic
    src["ecl_lat"] = sc.transform_to(BarycentricMeanEcliptic()).lat.deg
    del sc

    # Object table (DIA only)
    dia = src[src.pop_ == "DIA"].sort_values(["oid", "mjd", "sid"])
    g = dia.groupby("oid")
    obj = pd.DataFrame({"n_det": g.size(), "cats": g.cats.last(), "cats_score": g.cats_score.last(), "snn": g.snn.last(),
                        "median_reliability": g.reliability.median(), "field": g.field.agg(lambda v: v.value_counts().index[0])})
    lastvalid = g[["xm.simbad_otype", "xm.gaiadr3_DR3Name", "xm.gaiadr3_Plx", "xm.gaiadr3_e_Plx", "xm.legacydr8_zphot",
                   "xm.legacydr8_pstar", "xm.vsx_Type", "tns_type_any", "xm.tns_redshift"]].last()
    obj = obj.join(lastvalid.rename(columns={"xm.simbad_otype": "simbad_otype", "xm.gaiadr3_DR3Name": "gaia_name", "xm.gaiadr3_Plx": "plx",
                                             "xm.gaiadr3_e_Plx": "eplx", "xm.legacydr8_zphot": "zphot", "xm.legacydr8_pstar": "pstar",
                                             "xm.vsx_Type": "vsx_type", "tns_type_any": "tns_type", "xm.tns_redshift": "tns_z"}))
    di = dia[dia.band == "i"]
    gi = di.groupby("oid")
    obj = obj.join(pd.DataFrame({"n_det_i": gi.size(), "latest_sid_i": gi.sid.last()}))
    obj["n_det_i"] = obj.n_det_i.fillna(0).astype(int)
    pk = di[(di.psfFlux > 0)].copy()
    pk["s"] = pk.psfFlux / pk.psfFluxErr
    pk = pk.sort_values("psfFlux").groupby("oid").tail(1).set_index("oid")
    obj = obj.join(pd.DataFrame({"peak_mag_i": AB_ZP_NJY - 2.5 * np.log10(pk.psfFlux), "peak_snr_i": pk.s}))
    pa_ = dia[dia.psfFlux > 0].copy()
    pa_["s"] = pa_.psfFlux / pa_.psfFluxErr
    pa_ = pa_.sort_values("psfFlux").groupby("oid").tail(1).set_index("oid")
    obj = obj.join(pd.DataFrame({"peak_mag_any": AB_ZP_NJY - 2.5 * np.log10(pa_.psfFlux), "peak_snr_any": pa_.s, "peak_band_any": pa_.band}))
    tp = pd.read_parquet(a.template_photometry).set_index("dia_object_id")
    keep = ["panel_a", "panel_b", "panel_c", "mag_u", "mag_g", "mag_r", "mag_i", "ok_r", "ok_i", "tf_i", "gi", "gr", "ri", "ug"]
    obj = obj.join(tp[keep], how="left")
    assert len(obj) == len(tp) and obj.panel_a.notna().all()
    for c in ("panel_a", "panel_b", "panel_c", "ok_r", "ok_i"):
        obj[c] = obj[c].astype(bool)
    obj.index.name = "dia_object_id"
    obj = obj.reset_index()

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    style(plt)
    figs, tabs = [], {}
    steps = [("survey", lambda: survey(src, req, out, plt, figs, tabs)),
             ("star_galaxy", lambda: star_galaxy(obj, out, plt, figs, tabs)),
             ("classifier_validation", lambda: classifier_validation(obj, out, plt, figs, tabs)),
             ("label_evolution", lambda: label_evolution(dia, out, plt, figs, tabs)),
             ("variability", lambda: variability(obj, files, out, plt, figs, tabs)),
             ("distances", lambda: distances(obj, out, plt, figs, tabs)),
             ("quality", lambda: quality(src, obj, out, plt, figs, tabs)),
             ("sso", lambda: sso(src, out, plt, figs, tabs))]
    timings = {}
    for name, fn in steps:
        t1 = time.time()
        fn()
        timings[name] = round(time.time() - t1, 1)
        print(f"{name}: {timings[name]}s", flush=True)

    obj.drop(columns=["latest_sid_i"]).to_parquet(out / "tables" / "dia_object_science_table.parquet", index=False)
    summary = {
        "run": out.name, "code_sha": a.code_sha, "script_sha256": sha256(Path(__file__)),
        "catalog": a.catalog, "catalog_sha256": sha256(a.catalog),
        "template_photometry": a.template_photometry, "template_photometry_sha256": sha256(a.template_photometry),
        "python": platform.python_version(),
        "packages": {m: __import__(m).__version__ for m in ("numpy", "pandas", "pyarrow", "matplotlib", "astropy", "duckdb")},
        "raw_files": len(files), "population_counts": counts, "dia_objects": int(len(obj)),
        "figures": figs, "results": jsonable({k: v for k, v in tabs.items() if k != "T1"}),
        "timings_s": timings, "runtime_s": round(time.time() - t0, 1),
    }
    (out / "summary.json").write_text(json.dumps(jsonable(summary), indent=2, default=str) + "\n")
    print(json.dumps({"runtime_s": summary["runtime_s"], "figures": len(figs)}))


if __name__ == "__main__":
    main()

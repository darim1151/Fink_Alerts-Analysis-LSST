#!/usr/bin/env python
"""Figures and checks that use the EDP2 crossmatch of the Fink DIAObjects (see scripts/edp2_crossmatch_rsp.py).

  F6E  Fig. 6 (left panel) redrawn from EDP2 PSF colours, split by EDP2 r_extendedness: (a) unresolved, (b) resolved
  F9E  Fig. 9 (C1) with r_extendedness in place of Legacy DR8 pstar (template colours, same sample rule as C1)

Selections are pure functions of the tables (tested offline in tests/test_edp2_figures.py); every count printed in a
caption is computed from the same selection and re-counted independently by recount() before anything is written.

Usage on the data host:
    python edp2_figures.py --match <edp2_match_one_per_object.parquet> --run-dir <notebook run> \
        --locus <RubinStellarLocus_COSMOS.txt> --out <new folder>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "colour_plot_kit"))

N_OBJECTS = 3_487_175
AB_ZP = 31.4
DUSTY = ("Galactic bulge", "Galactic plane")
R_MAIN = 1.0          # arcsec: the agreed match radius
RADIUS = R_MAIN       # radius in use for this build (build(..., radius=0.5) is the sensitivity check)
SNR_MIN = 5.0


# ----------------------------------------------------------------------------- selections (pure, tested)
def flag(s):
    """A boolean flag column after a left join: missing counts as set (not usable)."""
    return s.astype("boolean").fillna(True).astype(bool)


def psf_mag(flux_njy):
    with np.errstate(divide="ignore", invalid="ignore"):
        return AB_ZP - 2.5 * np.log10(np.asarray(flux_njy, dtype=float))


def good_psf(one, band):
    f, e = one[f"{band}_psfFlux"].astype(float), one[f"{band}_psfFluxErr"].astype(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        snr = f / e
    return (f > 0) & (e > 0) & (snr >= SNR_MIN) & ~flag(one[f"{band}_psfFlux_flag"])


def ext_class(one):
    """0 = unresolved, 1 = resolved, NaN = no usable r_extendedness (no match within RADIUS, flagged, or not 0/1).
    `matched` is set by build(): nearest EDP2 Object within RADIUS arcsec."""
    re = one["r_extendedness"].astype(float)
    ok = one["matched"].astype("boolean").fillna(False).astype(bool) & re.isin([0.0, 1.0]) \
        & ~flag(one["r_extendedness_flag"])
    return re.where(ok)


def select_f6(one):
    """Fig. 6 sample: outside the bulge/plane fields, matched within RADIUS, usable r_extendedness, g, r, i PSF
    S/N >= 5 and unflagged. Returns gr, ri (EDP2 PSF colours, no dust or AB-offset correction) and the class."""
    cls = ext_class(one)
    sel = ~one["field"].isin(DUSTY) & cls.notna() & good_psf(one, "g") & good_psf(one, "r") & good_psf(one, "i")
    s = one.loc[sel, ["dia_object_id", "field"]].copy()
    g, r, i = (one.loc[sel, f"{b}_psfFlux"].astype(float).to_numpy() for b in "gri")
    s["gr"] = 2.5 * np.log10(r / g)
    s["ri"] = 2.5 * np.log10(i / r)
    s["cls"] = cls[sel].astype(int).to_numpy()
    return s


def select_f9(one, sci):
    """Fig. 9 sample: the C1 rule (usable g, r, i or u, g, r templates; all fields) with a 1-arcsec EDP2 match and a
    usable r_extendedness (match within RADIUS) instead of a Legacy pstar. Template colours and r_tmpl are taken from the science table."""
    cls = ext_class(one).rename("cls")
    o = sci[sci.panel_a | sci.panel_b].merge(pd.concat([one["dia_object_id"], cls], axis=1), on="dia_object_id",
                                             how="left", validate="one_to_one")
    return o[o.cls.notna()].copy()


def select_f9_edp2(one, sci):
    """Fig. 9 in EDP2 photometry: the same objects as select_f9 (the C1 sample with a usable r_extendedness), with
    every colour and r taken from the matched EDP2 Object's PSF fluxes. Panel flags are redefined from EDP2:
    panel_a = panel_c = g, r, i all S/N >= 5 and unflagged; panel_b = u, g, r likewise. No dust or AB-offset correction."""
    base = select_f9(one, sci)[["dia_object_id", "cls"]]
    e = base.merge(one, on="dia_object_id", how="left", validate="one_to_one")
    good = {b: good_psf(e, b).to_numpy() for b in "ugri"}
    m = {b: psf_mag(e[f"{b}_psfFlux"].to_numpy(dtype=float)) for b in "ugri"}
    out = pd.DataFrame({"dia_object_id": e.dia_object_id.to_numpy(), "cls": e.cls.to_numpy(),
                        "mag_r": m["r"], "gi": m["g"] - m["i"], "gr": m["g"] - m["r"], "ri": m["r"] - m["i"],
                        "ug": m["u"] - m["g"]})
    out["panel_a"] = good["g"] & good["r"] & good["i"]
    out["panel_b"] = good["u"] & good["g"] & good["r"]
    out["panel_c"] = out["panel_a"]
    return out[out.panel_a | out.panel_b].reset_index(drop=True)


def recount(f6, f9):
    """Independent recount of every figure number, straight from the selected frames."""
    out = {"f6_unresolved": int((f6.cls == 0).sum()), "f6_resolved": int((f6.cls == 1).sum())}
    for key, name in (("panel_a", "b"), ("panel_b", "c"), ("panel_c", "d")):
        sub = f9[f9[key]]
        out[f"f9_{name}_resolved"] = int((sub.cls == 1).sum())
        out[f"f9_{name}_unresolved"] = int((sub.cls == 0).sum())
    s = f9[f9.panel_a]
    out["f9_a_n"] = int(len(s))
    out["f9_total"] = int(len(f9))
    out["f9_total_resolved"] = int((f9.cls == 1).sum())
    out["f9_total_unresolved"] = int((f9.cls == 0).sum())
    return out


# ----------------------------------------------------------------------------- validation
def robust_sigma(x):
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    return float(1.4826 * np.median(np.abs(x - np.median(x)))) if len(x) else float("nan")


def validate(one, tp, sci):
    rep = {}
    rep["n_objects"] = int(len(one))
    rep["radius_arcsec"] = RADIUS
    rep["matched"] = int(one.matched.sum())
    rep["inside_edp2"] = int(one.inside_edp2.sum())
    cls = ext_class(one)
    rep["usable_r_extendedness"] = int(cls.notna().sum())
    rep["unresolved"] = int((cls == 0).sum())
    rep["resolved"] = int((cls == 1).sum())
    m = one.matched.astype(bool)
    rep["matched_but_unusable_r_extendedness"] = int((m & cls.isna()).sum())
    # photometry: template PSF magnitude vs EDP2 PSF magnitude, unresolved matches with a usable template
    t = tp.set_index("dia_object_id")
    rows = []
    for b in "gri":
        ok_t = t[f"ok_{b}"].reindex(one.dia_object_id).fillna(False).to_numpy().astype(bool)
        mt = t[f"mag_{b}"].reindex(one.dia_object_id).to_numpy()
        sel = ok_t & (cls == 0).to_numpy() & good_psf(one, b).to_numpy()
        f, e = one[f"{b}_psfFlux"].to_numpy(dtype=float), one[f"{b}_psfFluxErr"].to_numpy(dtype=float)
        bright = sel & (f / np.where(e > 0, e, np.nan) >= 50)
        d = mt - psf_mag(f)
        rows.append({"band": b, "n": int(sel.sum()), "median_dmag": float(np.nanmedian(d[sel])),
                     "robust_sigma": robust_sigma(d[sel]), "n_bright_snr50": int(bright.sum()),
                     "median_dmag_bright": float(np.nanmedian(d[bright])) if bright.any() else float("nan"),
                     "frac_bright_over_0p5": float(np.mean(np.abs(d[bright]) > 0.5)) if bright.any() else float("nan")})
    rep["template_minus_edp2_psf_mag"] = rows
    # classification checks against the Fink crossmatches
    s = sci.set_index("dia_object_id").reindex(one.dia_object_id)
    rmag = psf_mag(one.r_psfFlux.to_numpy(dtype=float))
    with np.errstate(divide="ignore", invalid="ignore"):
        plx_snr = (s.plx / s.eplx).to_numpy()
    gaia_star = np.isfinite(plx_snr) & (plx_snr > 5) & cls.notna().to_numpy() & (rmag < 21)
    rep["gaia_plx_snr5_r_lt_21"] = {"n": int(gaia_star.sum()),
                                     "frac_resolved": float(np.mean(cls.to_numpy()[gaia_star] == 1)) if gaia_star.any() else None}
    grp = tp.set_index("dia_object_id").simbad_group.reindex(one.dia_object_id).to_numpy()
    rep["simbad_group_frac_resolved"] = {g: {"n": int(((grp == g) & cls.notna().to_numpy()).sum()),
                                             "frac_resolved": float(np.mean(cls.to_numpy()[(grp == g) & cls.notna().to_numpy()] == 1))}
                                         for g in ("Star", "QSO / AGN", "Galaxy") if ((grp == g) & cls.notna().to_numpy()).any()}
    ps = s.pstar.to_numpy()
    both = np.isfinite(ps) & cls.notna().to_numpy()
    rep["pstar_vs_r_extendedness"] = {
        "pstar>=0.5 & unresolved": int((both & (ps >= 0.5) & (cls.to_numpy() == 0)).sum()),
        "pstar>=0.5 & resolved": int((both & (ps >= 0.5) & (cls.to_numpy() == 1)).sum()),
        "pstar<0.5 & unresolved": int((both & (ps < 0.5) & (cls.to_numpy() == 0)).sum()),
        "pstar<0.5 & resolved": int((both & (ps < 0.5) & (cls.to_numpy() == 1)).sum())}
    sep = one.sep_arcsec.to_numpy(dtype=float)
    rep["sep_median_arcsec"] = {"unresolved": float(np.nanmedian(sep[(cls == 0).to_numpy()])),
                                "resolved": float(np.nanmedian(sep[(cls == 1).to_numpy()]))}
    return rep


# ----------------------------------------------------------------------------- figures (paper style)
def fig_f6(f6, sl, out, book, pub):
    plt = pub._plt()
    xr, yr = pub.T_RANGES
    fig, axes = plt.subplots(1, 2, figsize=(pub.WIDTH, 3.45), gridspec_kw={"wspace": 0.28})
    info = {}
    for ax, c, let, name in ((axes[0], 0, "(a)", "unresolved"), (axes[1], 1, "(b)", "resolved")):
        s = f6[f6.cls == c]
        pub._tbase(ax, s, sl)
        ax.set_xlabel(r"$(g-r)_{\rm EDP2}$")
        ax.set_ylabel(r"$(r-i)_{\rm EDP2}$")
        pub._label(ax, let, f"{name}, r_extendedness = {c}", f"N = {pub._fmt(len(s))}")
        inside = s.gr.between(*xr) & s.ri.between(*yr)
        info[name] = {"n": int(len(s)), "outside_plot": int((~inside).sum())}
    dens, cont, locus = pub._locus_handles()
    dens.set_label("EDP2 PSF colours (density)")
    cap = ("Figure F6E. EDP2 colours of DIAObjects outside the Galactic bulge and plane fields that have an EDP2 Object "
           f"within {RADIUS:g}″, split by the EDP2 r_extendedness of the matched Object: (a) unresolved "
           f"(r_extendedness = 0, {pub._fmt(info['unresolved']['n'])} objects), (b) resolved (r_extendedness = 1, "
           f"{pub._fmt(info['resolved']['n'])}). Colours are EDP2 PSF colours with S/N ≥ 5 and no flags in g, r and i; "
           "grey shading is the logarithmic number density and the contours enclose 50, 80 and 95% of the objects in each "
           f"panel; {info['unresolved']['outside_plot']} and {info['resolved']['outside_plot']} objects fall outside the "
           "plotted range. The red band is the region within 2σ of the Rubin COSMOS stellar locus in both colours. "
           "No extinction correction and no DP2 AB-offset correction (≤ 0.022 mag in these colours).")
    return pub._finish(fig, cap, out, "F6E_edp2_colours_by_r_extendedness", book, legend=([dens, cont, locus], 3)), info, cap


def fig_f9(f9, out, book, pub):
    plt = pub._plt()
    from matplotlib.lines import Line2D
    suite = pub.suite
    o = f9
    res, unres = o[o.cls == 1], o[o.cls == 0]
    fig, axes = plt.subplots(2, 2, figsize=(pub.WIDTH, 6.0), gridspec_kw={"hspace": 0.28, "wspace": 0.42})
    a = axes[0, 0]
    s = o[o.panel_a]
    bins = np.arange(15, 25.01, 0.5)
    k, _ = np.histogram(s.mag_r[s.cls == 0], bins)
    n, _ = np.histogram(s.mag_r, bins)
    cc = 0.5 * (bins[1:] + bins[:-1])
    good = n >= 10
    lo, hi = np.array([suite.wilson(kk, nn) for kk, nn in zip(k, n)]).T
    a2 = a.twinx()
    a2.bar(cc, n, width=0.46, color="#d9d8d4", zorder=0)
    a2.set_ylabel("Objects per 0.5 mag", color=pub.GREY)
    a2.tick_params(axis="y", colors=pub.GREY, direction="in")
    a.set_zorder(a2.get_zorder() + 1)
    a.patch.set_visible(False)
    a.fill_between(cc[good], lo[good], hi[good], color=pub.BLUE, alpha=0.2, lw=0)
    a.plot(cc[good], (k / np.maximum(n, 1))[good], "-o", color=pub.BLUE, ms=3, lw=1.3)
    a.set_ylim(0, 1.08)
    a.set_xlim(15, 25)
    a.set_xlabel(r"$r_{\rm EDP2}$ (PSF, AB)")
    a.set_ylabel("Fraction with r_extendedness = 0")
    a.tick_params(right=False)
    a.minorticks_on()
    pub._label(a, "(a)", None, f"N = {pub._fmt(len(s))}", x=0.04, y=0.44)
    counts = {}
    panels = [(key, x, y, xl.replace(r"\mathrm{tmpl}", r"\mathrm{EDP2}"), yl.replace(r"\mathrm{tmpl}", r"\mathrm{EDP2}"), xr, yr, inv)
              for key, x, y, xl, yl, xr, yr, inv in suite.COLOUR_PANELS]
    for (key, x, y, xl, yl, xr, yr, inv), ax, let in zip(panels, (axes[0, 1], axes[1, 0], axes[1, 1]), ("(b)", "(c)", "(d)")):
        e, p = res[res[key]], unres[unres[key]]
        suite.contours(ax, e[x], e[y], xr, yr, pub.RED, lw=1.0)
        ins = p[x].between(*xr) & p[y].between(*yr)
        ax.scatter(p.loc[ins, x], p.loc[ins, y], s=5, color=pub.BLUE, alpha=0.8, edgecolors="none", rasterized=True)
        counts[let] = (int(len(e)), int(len(p)), int((~ins).sum()))
        ax.set_xlim(*xr)
        ax.set_ylim(*(yr[::-1] if inv else yr))
        ax.set_xlabel(xl)
        ax.set_ylabel(r"$r_{\rm EDP2}$ (PSF, AB)" if y == "mag_r" else yl)
        ax.minorticks_on()
        pub._label(ax, let)
    handles = [Line2D([], [], color=pub.RED, lw=1.2, label="resolved, r_extendedness = 1 (contours enclose 50, 80, 95%)"),
               Line2D([], [], marker="o", ls="none", color=pub.BLUE, markersize=4, label="unresolved, r_extendedness = 0")]
    cap = (f"Figure F9E. EDP2 PSF colours of the Fig. 9 DIAObjects that have an EDP2 Object within {RADIUS:g}″, split by the "
           f"EDP2 r_extendedness of that Object. Of the {pub._fmt(len(o))} objects with a usable r_extendedness and S/N ≥ 5 "
           "in the EDP2 bands of at least one panel, "
           f"{pub._fmt(len(res))} are resolved (r_extendedness = 1, red contours) and {pub._fmt(len(unres))} unresolved "
           f"(r_extendedness = 0, blue points). (a) Fraction with r_extendedness = 0 against r_EDP2 for the {pub._fmt(len(s))} "
           "objects with S/N ≥ 5 in EDP2 g, r and i (blue, with 95% Wilson interval; bins with at least 10 objects), and "
           "the number of these objects per 0.5 mag (grey bars, right axis). (b) r_EDP2 against (g − i)_EDP2 "
           f"({pub._fmt(counts['(b)'][0])} resolved and {pub._fmt(counts['(b)'][1])} unresolved). (c) (g − r)_EDP2 against "
           f"(u − g)_EDP2 ({pub._fmt(counts['(c)'][0])} and {pub._fmt(counts['(c)'][1])}). (d) (r − i)_EDP2 against "
           f"(g − r)_EDP2 ({pub._fmt(counts['(d)'][0])} and {pub._fmt(counts['(d)'][1])}). No extinction correction and no DP2 "
           "AB-offset correction; all fields.")
    return pub._finish(fig, cap, out, "F9E_edp2_colours_by_r_extendedness", book, legend=(handles, 2)), {"counts": counts}, cap


def fig_validation(one, ctrl, tp, sci, out, book, pub):
    """Internal check page (not for the paper): match radius vs chance, template vs EDP2 photometry, classifications."""
    plt = pub._plt()
    fig, axes = plt.subplots(2, 2, figsize=(pub.WIDTH, 6.2), gridspec_kw={"hspace": 0.34, "wspace": 0.34})
    info = {}
    # (a) cumulative match fraction against radius, real and 30-arcsec-offset control
    a = axes[0, 0]
    r = np.linspace(0, 2.0, 201)
    for d, col, lab in ((one, pub.BLUE, "DIAObject positions"), (ctrl, pub.GREY, "positions moved by 30″")):
        sep = np.sort(d.sep_arcsec.astype(float).to_numpy())
        sep = sep[np.isfinite(sep)]
        a.plot(r, np.searchsorted(sep, r, side="right") / len(d), color=col, lw=1.4, label=lab)
    a.axvline(RADIUS, color=pub.INK, lw=0.8, ls="--")
    a.set_xlim(0, 2)
    a.set_ylim(0, 1)
    a.set_xlabel("Radius (arcsec)")
    a.set_ylabel("Fraction with an EDP2 Object")
    a.legend(loc="center right", fontsize=8)
    pub._label(a, "(a)")
    # (b) template minus EDP2 PSF magnitude, i band, unresolved matches with a usable template
    b = axes[0, 1]
    cls = ext_class(one)
    t = tp.set_index("dia_object_id")
    ok_t = t["ok_i"].reindex(one.dia_object_id).fillna(False).to_numpy().astype(bool)
    sel = ok_t & (cls == 0).to_numpy() & good_psf(one, "i").to_numpy()
    mi = psf_mag(one.i_psfFlux.to_numpy(dtype=float))[sel]
    dm = t["mag_i"].reindex(one.dia_object_id).to_numpy()[sel] - mi
    b.hist2d(mi, dm, bins=[np.arange(14, 24.01, 0.2), np.arange(-1, 1.001, 0.02)], cmap="Greys", cmin=1, rasterized=True)
    edges = np.arange(14, 24.01, 0.5)
    k = np.digitize(mi, edges)
    med = [np.median(dm[k == j]) if (k == j).sum() >= 20 else np.nan for j in range(1, len(edges))]
    b.plot(0.5 * (edges[1:] + edges[:-1]), med, "-o", color=pub.ORANGE, ms=3, lw=1.2, label="median")
    b.axhline(0, color=pub.INK, lw=0.6)
    b.set_xlabel(r"$i_{\rm EDP2}$ (PSF, AB)")
    b.set_ylabel(r"$i_{\rm tmpl} - i_{\rm EDP2}$")
    b.legend(loc="lower left", fontsize=8)
    pub._label(b, "(b)", None, f"N = {pub._fmt(sel.sum())} unresolved")
    info["b"] = {"n": int(sel.sum()), "median": float(np.median(dm)), "robust_sigma": robust_sigma(dm)}
    # (c) resolved fraction against EDP2 r magnitude
    c = axes[1, 0]
    rm = psf_mag(one.r_psfFlux.to_numpy(dtype=float))
    s = sci.set_index("dia_object_id").reindex(one.dia_object_id)
    with np.errstate(divide="ignore", invalid="ignore"):
        gaia = ((s.plx / s.eplx) > 5).to_numpy()
    use = cls.notna().to_numpy() & np.isfinite(rm)
    edges = np.arange(15, 26.01, 0.5)
    cc = 0.5 * (edges[1:] + edges[:-1])
    for m, col, lab in ((use, pub.BLUE, "all matched"), (use & gaia, pub.RED, "Gaia parallax S/N > 5")):
        n, _ = np.histogram(rm[m], edges)
        kk, _ = np.histogram(rm[m & (cls == 1).to_numpy()], edges)
        g = n >= 20
        c.plot(cc[g], (kk / np.maximum(n, 1))[g], "-o", color=col, ms=3, lw=1.2, label=lab)
    c.set_ylim(0, 1.02)
    c.set_xlabel(r"$r_{\rm EDP2}$ (PSF, AB)")
    c.set_ylabel("Fraction with r_extendedness = 1")
    c.legend(loc="upper left", fontsize=8)
    pub._label(c, "(c)", x=0.04, y=0.70)
    # (d) resolved fraction by Fink SIMBAD group and for Gaia stars
    d = axes[1, 1]
    grp = tp.set_index("dia_object_id").simbad_group.reindex(one.dia_object_id).to_numpy()
    names, fr, ns = [], [], []
    for g, lab in (("Star", "SIMBAD Star"), ("QSO / AGN", "SIMBAD QSO/AGN"), ("Galaxy", "SIMBAD Galaxy")):
        m = (grp == g) & cls.notna().to_numpy()
        names.append(lab); ns.append(int(m.sum())); fr.append(float(np.mean(cls.to_numpy()[m] == 1)) if m.any() else np.nan)
    m = gaia & cls.notna().to_numpy()
    names.append("Gaia S/N > 5"); ns.append(int(m.sum())); fr.append(float(np.mean(cls.to_numpy()[m] == 1)))
    y = np.arange(len(names))[::-1]
    d.barh(y, fr, color=pub.BLUE, height=0.6)
    for yy, f, n in zip(y, fr, ns):
        txt = f"{f:.2f} (N = {pub._fmt(n)})"
        if f > 0.55:   # long bar: write the label inside it so it stays within the axes
            d.text(f - 0.02, yy, txt, va="center", ha="right", fontsize=8, color="white")
        else:
            d.text(f + 0.02, yy, txt, va="center", ha="left", fontsize=8)
    d.set_yticks(y)
    d.set_yticklabels(names)
    d.set_xlim(0, 1)
    d.set_xlabel("Fraction with r_extendedness = 1")
    pub._label(d, "(d)", x=0.80, y=0.97)
    info["d"] = dict(zip(names, zip(fr, ns)))
    cap = "Check page for the EDP2 match (not a paper figure)."
    return pub._finish(fig, cap, out, "V1_edp2_match_checks", book), info


def build(match_path, run_dir, locus, out, control_path=None, radius=R_MAIN):
    global RADIUS
    RADIUS = float(radius)
    import paper_figures_pub as pub
    import plot_colour_diagrams as kit
    from matplotlib.backends.backend_pdf import PdfPages
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    one = pd.read_parquet(match_path)
    if len(one) != N_OBJECTS or not one.dia_object_id.is_unique:
        raise ValueError("match table must have one row per DIAObject")
    one["matched"] = (one.sep_arcsec.astype(float) <= RADIUS).to_numpy(dtype=bool)
    if RADIUS == R_MAIN and not (one.matched == one.matched_1arcsec.astype(bool)).all():
        raise ValueError("recomputed 1-arcsec match flag differs from the stored one")
    if not (one.matched == (one[f"n_within_{str(RADIUS).replace('.', 'p')}"] > 0)).all():
        raise ValueError("match flag disagrees with the neighbour count at this radius")
    R = Path(run_dir)
    tp = pd.read_parquet(R / "01_template_colours/tables/dia_object_template_photometry.parquet")
    sci = pd.read_parquet(R / "02_science_suite/tables/dia_object_science_table.parquet")
    if set(one.dia_object_id) != set(tp.dia_object_id):
        raise ValueError("match table ids differ from the template table ids")
    # field labels must agree with the paper tables
    chk = one[["dia_object_id", "field"]].merge(tp[["dia_object_id", "field"]], on="dia_object_id", suffixes=("", "_tp"))
    if not (chk.field == chk.field_tp).all():
        raise ValueError("field labels differ between the match table and the template table")
    f6, f9 = select_f6(one), select_f9_edp2(one, sci)
    counts = recount(f6, f9)
    rep = validate(one, tp, sci)
    sl = kit.load_locus(locus)
    pub.SHOW_CAPTIONS = False
    caps = {}
    with PdfPages(out / "Fink_LSST_EDP2_figures.pdf", metadata={"Title": "Fink LSST alert sample: EDP2 figures",
                                                                "Author": "Darim Shamsi"}) as book:
        _, info6, caps["F6E"] = fig_f6(f6, sl, out, book, pub)
        _, info9, caps["F9E"] = fig_f9(f9, out, book, pub)
    if control_path:
        ctrl = pd.read_parquet(control_path)
        if len(ctrl) != N_OBJECTS:
            raise ValueError("control table must have one row per DIAObject")
        with PdfPages(out / "EDP2_match_checks.pdf") as vbook:
            _, rep["check_page"] = fig_validation(one, ctrl, tp, sci, out, vbook, pub)
    # the numbers drawn on / written for the figures must equal the independent recount
    assert info6["unresolved"]["n"] == counts["f6_unresolved"] and info6["resolved"]["n"] == counts["f6_resolved"]
    for let, nm in (("(b)", "b"), ("(c)", "c"), ("(d)", "d")):
        assert info9["counts"][let][:2] == (counts[f"f9_{nm}_resolved"], counts[f"f9_{nm}_unresolved"])
    (out / "captions.txt").write_text("\n\n".join(caps[k] for k in ("F6E", "F9E")) + "\n")
    (out / "edp2_summary.json").write_text(json.dumps({"recount": counts, "f6": info6, "f9": info9, "validation": rep},
                                                      indent=2, default=str) + "\n")
    print(json.dumps({"recount": counts, "validation": rep}, indent=2, default=str))
    return out


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--match", required=True)
    p.add_argument("--run-dir", required=True)
    p.add_argument("--locus", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--control", help="offset-control table (one row per DIAObject) for the check page")
    p.add_argument("--radius", type=float, default=R_MAIN, choices=(0.5, 1.0, 2.0), help="match radius, arcsec")
    a = p.parse_args()
    build(a.match, a.run_dir, a.locus, a.out, a.control, a.radius)

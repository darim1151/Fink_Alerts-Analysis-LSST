#!/usr/bin/env python
"""Plots asked for in Z. Ivezic's review of 2026-11-18 (on Fink_LSST_figures_EDP2_revision_v2.pdf).

  R1  why ~20% of DIAObjects have no EDP2 Object within 1": sky map of the unmatched fraction, RA/Dec zooms with
      different symbols, and the unmatched fraction against alerts per object, reliability, peak S/N and field
  F3  Fig. 3 again: (a) bars checked to add up to all alerts; (b) unmatched DIAObjects overplotted and the number with
      >= 20 alerts stated; (c), (d) linear y axes
  SN  quick check: alert detection S/N and difference-flux magnitude uncertainty against difference-flux magnitude
  F4  Fig. 4 again with (b) limited to +-0.3 mag, plus fluxR_i = 1 + iflux_diff/iflux_tmpl against i_tmpl and (g - i)_EDP2
  FR  fluxR_band against (g - i)_EDP2 for u, g, r, i, z, y; unresolved and resolved (EDP2 r_extendedness)

Alerts come from the analytical catalog (read only). fluxR uses every DIA alert whose template flux in that band is
positive with S/N >= 5 (template_flux / template_flux_err). (g - i)_EDP2 is the cModel colour of the matched EDP2
Object (both bands usable). Matched = an EDP2 Object within 1".
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

AB = 31.4
BANDS = ("u", "g", "r", "i", "z", "y")
N_ALERTS, N_DIA, N_SSO, N_OBJ = 7_101_947, 6_311_364, 790_583, 3_487_175


# ----------------------------------------------------------------------------- pure, tested
def mag(f):
    f = np.asarray(f, float)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(f > 0, AB - 2.5 * np.log10(np.where(f > 0, f, 1.0)), np.nan)


def flux_ratio(diff, tmpl, tmpl_err):
    """fluxR = 1 + diff/tmpl for alerts whose template flux is positive with S/N >= 5; NaN otherwise."""
    diff, tmpl, tmpl_err = (np.asarray(x, float) for x in (diff, tmpl, tmpl_err))
    with np.errstate(divide="ignore", invalid="ignore"):
        ok = (tmpl > 0) & (tmpl_err > 0) & (tmpl / tmpl_err >= 5) & np.isfinite(diff)
        return np.where(ok, 1.0 + diff / np.where(ok, tmpl, 1.0), np.nan)


def nalert_bins(n):
    """Label used for the 'alerts per object' groups."""
    n = np.asarray(n)
    return np.select([n == 1, n == 2, n <= 4, n <= 9, n <= 19], ["1", "2", "3-4", "5-9", "10-19"], ">= 20")


# ----------------------------------------------------------------------------- plots
def _hist2d(ax, x, y, xb, yb, logy=False):
    from matplotlib.colors import LogNorm
    if logy:
        y = np.log10(y)
    h, xe, ye = np.histogram2d(x, y, bins=[xb, yb])
    ax.pcolormesh(xe, (10 ** ye) if logy else ye, h.T, cmap="Greys", norm=LogNorm(vmin=1, vmax=max(h.max(), 2)), rasterized=True)
    if logy:
        ax.set_yscale("log")


def _median_line(ax, x, y, edges, color, min_n=50, logy=False):
    k = np.digitize(x, edges)
    med = [np.median(y[k == j]) if (k == j).sum() >= min_n else np.nan for j in range(1, len(edges))]
    ax.plot(0.5 * (edges[1:] + edges[:-1]), med, "-o", color=color, ms=2.6, lw=1.2, label="median")


def fig_f3(al, tp, e, out, book, pub):
    plt = pub._plt()
    fig, axes = plt.subplots(2, 2, figsize=(pub.WIDTH, 6.0), gridspec_kw={"hspace": 0.34, "wspace": 0.3})
    a, b, c, d = axes.ravel()
    nd = [int(((al.population == "DIA") & (al.band == x)).sum()) for x in BANDS]
    ns = [int(((al.population == "SSO") & (al.band == x)).sum()) for x in BANDS]
    assert sum(nd) == N_DIA and sum(ns) == N_SSO and sum(nd) + sum(ns) == N_ALERTS, "Fig. 3a bars do not add up"
    x = np.arange(6)
    a.bar(x - 0.2, nd, 0.4, color=pub.BLUE, label="DIA alerts")
    a.bar(x + 0.2, ns, 0.4, color=pub.ORANGE, label="SSO alerts")
    a.set_yscale("log"); a.set_ylim(1e3, 1e8); a.set_xticks(x); a.set_xticklabels(BANDS)
    a.set_xlabel("Band"); a.set_ylabel("Number of alerts"); a.legend(loc="upper right", fontsize=8, ncol=2)
    pub._label(a, "(a)")
    m = tp.dia_object_id.isin(e.dia_object_id[~e.matched]).to_numpy()
    n = tp.n_sources.to_numpy()
    edges = np.arange(0.5, 61.5, 1)
    b.hist(n, edges, color=pub.BLUE, label=f"all DIAObjects ({pub._fmt(len(n))})")
    b.hist(n[m], edges, histtype="step", color=pub.ORANGE, lw=1.3, label=f"no EDP2 match within 1″ ({pub._fmt(m.sum())})")
    b.set_yscale("log"); b.set_xlim(0, 60); b.set_xlabel("Alerts per DIA object"); b.set_ylabel("Number of objects")
    b.legend(loc="upper right", fontsize=7.5)
    n20, n20m = int((n >= 20).sum()), int((n[m] >= 20).sum())
    pub._label(b, "(b)", None, f"≥ 20 alerts: {pub._fmt(n20)} objects ({pub._fmt(n20m)} unmatched)", x=0.30, y=0.70)
    dia = al[(al.population == "DIA") & (al.psf_flux > 0)]
    me = np.arange(12, 27.01, 0.2)
    for x_ in BANDS:
        v = mag(dia.psf_flux[dia.band == x_])
        c.stairs(np.histogram(v, me)[0], me, color=pub.BAND_COLOR[x_], lw=1.2, label=x_)
        ok = e[f"ok_{x_}"].to_numpy(bool)
        d.stairs(np.histogram(e[f"mag_{x_}"].to_numpy()[ok], me)[0], me, color=pub.BAND_COLOR[x_], lw=1.2, label=x_)
    for ax, lab, let in ((c, "Difference-flux magnitude (AB)", "(c)"), (d, "EDP2 cModel magnitude (AB)", "(d)")):
        ax.set_xlim(12, 27); ax.set_ylim(0, None); ax.set_xlabel(lab); ax.set_ylabel("Number per 0.2 mag")
        ax.legend(ncol=3, fontsize=7.5, loc="upper left" if ax is c else "upper right")
        ax.ticklabel_format(axis="y", style="sci", scilimits=(0, 0))
        pub._label(ax, let, x=0.04, y=0.62)
    for ax in (b, c, d):
        ax.minorticks_on()
    pub._finish(fig, "", out, "F3_sample_edp2_v3", book)
    v_all = mag(dia.psf_flux)
    return {"alerts_dia": sum(nd), "alerts_sso": sum(ns), "alerts_total": sum(nd) + sum(ns),
            "dia_psfflux_pos": int(len(dia)), "dia_psfflux_pos_outside_12_27": int(((v_all < 12) | (v_all >= 27)).sum()),
            "objects_ge20_alerts": n20, "unmatched_ge20_alerts": n20m, "matched_ge20_alerts": n20 - n20m,
            "unmatched_objects": int(m.sum()), "dia_psfflux_pos_fainter_23": int((v_all >= 23).sum())}


def fig_sn(al, out, book, pub):
    plt = pub._plt()
    dia = al[(al.population == "DIA") & (al.psf_flux > 0)]
    m = mag(dia.psf_flux)
    snr = dia.snr.to_numpy(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        sig = 2.5 / np.log(10) * dia.psf_flux_err.to_numpy(float) / dia.psf_flux.to_numpy(float)
        ratio = dia.psf_flux.to_numpy(float) / dia.psf_flux_err.to_numpy(float)
    fig, (a, b) = plt.subplots(1, 2, figsize=(pub.WIDTH, 3.2), gridspec_kw={"wspace": 0.3})
    ok = np.isfinite(m) & np.isfinite(snr) & (snr > 0)
    _hist2d(a, m[ok], snr[ok], np.arange(12, 27.01, 0.1), np.linspace(np.log10(1), np.log10(1e4), 80), logy=True)
    a.axhline(5, color=pub.RED, lw=0.9, ls="--"); a.axvline(23, color=pub.BLUE, lw=0.9, ls="--")
    a.set_xlabel("Difference-flux magnitude (AB)"); a.set_ylabel("Alert detection S/N (snr)")
    pub._label(a, "(a)")
    ok2 = np.isfinite(m) & np.isfinite(sig) & (sig > 0)
    _hist2d(b, m[ok2], sig[ok2], np.arange(12, 27.01, 0.1), np.linspace(-3, 0.5, 80), logy=True)
    b.axvline(23, color=pub.BLUE, lw=0.9, ls="--"); b.axhline(0.2171, color=pub.RED, lw=0.9, ls="--")
    b.set_xlabel("Difference-flux magnitude (AB)"); b.set_ylabel("σ of difference-flux magnitude")
    pub._label(b, "(b)")
    pub._finish(fig, "", out, "SN_alert_snr_vs_mag", book)
    edges = np.arange(18, 27.01, 1.0)
    k = np.digitize(m, edges)
    med = {f"{edges[j-1]:.0f}-{edges[j]:.0f}": float(np.nanmedian(snr[k == j])) for j in range(1, len(edges)) if (k == j).sum()}
    fin = np.isfinite(ratio) & np.isfinite(snr)
    return {"median_snr_by_mag": med, "frac_snr_below_5": float(np.mean(snr[ok] < 5)),
            "median_abs_snr_minus_flux_over_err": float(np.median(np.abs(snr[fin] - ratio[fin]))),
            "n_mag_ge_25": int((m >= 25).sum()), "median_snr_mag_ge_25": float(np.nanmedian(snr[m >= 25]))}


def fig_f4(one, ctrl, tp, al, ecol, out, book, pub, ef):
    plt = pub._plt()
    fig, axes = plt.subplots(2, 2, figsize=(pub.WIDTH, 6.2), gridspec_kw={"hspace": 0.34, "wspace": 0.34})
    a, b, c, d = axes.ravel()
    r = np.linspace(0, 2.0, 201)
    for dd, col, lab in ((one, pub.BLUE, "DIAObject positions"), (ctrl, pub.GREY, "positions moved by 30″")):
        sep = np.sort(dd.sep_arcsec.astype(float).to_numpy()); sep = sep[np.isfinite(sep)]
        a.plot(r, np.searchsorted(sep, r, side="right") / len(dd), color=col, lw=1.4, label=lab)
    a.axvline(1.0, color=pub.INK, lw=0.8, ls="--"); a.set_xlim(0, 2); a.set_ylim(0, 1)
    a.set_xlabel("Radius (arcsec)"); a.set_ylabel("Fraction with an EDP2 Object"); a.legend(loc="center right", fontsize=8)
    pub._label(a, "(a)")
    cls = ef.ext_class(one)
    t = tp.set_index("dia_object_id")
    ok_t = t["ok_i"].reindex(one.dia_object_id).fillna(False).to_numpy().astype(bool)
    sel = ok_t & (cls == 0).to_numpy() & ef.good_psf(one, "i").to_numpy()
    mi = ef.psf_mag(one.i_psfFlux.to_numpy(dtype=float))[sel]
    dm = t["mag_i"].reindex(one.dia_object_id).to_numpy()[sel] - mi
    b.hist2d(mi, dm, bins=[np.arange(14, 24.01, 0.2), np.arange(-0.3, 0.3001, 0.01)], cmap="Greys", cmin=1, rasterized=True)
    _median_line(b, mi, dm, np.arange(14, 24.01, 0.5), pub.ORANGE, min_n=20)
    b.axhline(0, color=pub.INK, lw=0.6); b.set_ylim(-0.3, 0.3)
    b.set_xlabel(r"$i_{\rm EDP2}$ (PSF, AB)"); b.set_ylabel(r"$i_{\rm tmpl} - i_{\rm EDP2}$"); b.legend(loc="lower right", fontsize=8)
    pub._label(b, "(b)", None, f"N = {pub._fmt(sel.sum())} unresolved")
    ia = al[(al.population == "DIA") & (al.band == "i")]
    fr = flux_ratio(ia.psf_flux, ia.template_flux, ia.template_flux_err)
    mt = mag(ia.template_flux)
    use = np.isfinite(fr) & (fr > 0)
    _hist2d(c, mt[use], fr[use], np.arange(14, 25.01, 0.1), np.linspace(-1, 3, 90), logy=True)
    _median_line(c, mt[use], fr[use], np.arange(14, 25.01, 0.5), pub.ORANGE)
    c.axhline(1, color=pub.INK, lw=0.6)
    c.set_xlabel(r"$i_{\rm tmpl}$ of the alert (AB)"); c.set_ylabel(r"fluxR$_i$ = 1 + $f_{\rm diff}/f_{\rm tmpl}$")
    pub._label(c, "(c)", None, f"N = {pub._fmt(use.sum())} i alerts", x=0.04, y=0.955)
    gi = ecol.set_index("dia_object_id").gi_edp2.reindex(ia.dia_object_id).to_numpy()
    use2 = use & np.isfinite(gi)
    _hist2d(d, gi[use2], fr[use2], np.arange(-1, 4.01, 0.05), np.linspace(-1, 3, 90), logy=True)
    _median_line(d, gi[use2], fr[use2], np.arange(-1, 4.01, 0.25), pub.ORANGE)
    d.axhline(1, color=pub.INK, lw=0.6)
    d.set_xlabel(r"$(g-i)_{\rm EDP2}$ (cModel)"); d.set_ylabel(r"fluxR$_i$")
    pub._label(d, "(d)", None, f"N = {pub._fmt(use2.sum())} i alerts", x=0.04, y=0.955)
    for ax in axes.ravel():
        ax.minorticks_on()
    pub._finish(fig, "", out, "F4_match_checks_fluxratio", book)
    ok_t_all = np.isfinite(fr)
    return {"b_n": int(sel.sum()), "c_n": int(use.sum()), "d_n": int(use2.sum()),
            "i_alerts": int(len(ia)), "i_alerts_tmpl_snr5": int(ok_t_all.sum()), "i_alerts_fluxR_le_0": int((ok_t_all & (fr <= 0)).sum()),
            "median_fluxR_i": float(np.median(fr[use]))}


def fig_fr6(al, ecol, cls_value, title, out, book, pub, name):
    plt = pub._plt()
    fig, axes = plt.subplots(2, 3, figsize=(pub.WIDTH, 5.0), sharex=True, sharey=True, gridspec_kw={"hspace": 0.08, "wspace": 0.08})
    ec = ecol.set_index("dia_object_id")
    info = {}
    dia = al[al.population == "DIA"]
    for k, (ax, x_) in enumerate(zip(axes.ravel(), BANDS)):
        s = dia[dia.band == x_]
        fr = flux_ratio(s.psf_flux, s.template_flux, s.template_flux_err)
        gi = ec.gi_edp2.reindex(s.dia_object_id).to_numpy()
        cl = ec.cls.reindex(s.dia_object_id).to_numpy()
        use = np.isfinite(fr) & (fr > 0) & np.isfinite(gi) & (cl == cls_value)
        if use.sum():
            _hist2d(ax, gi[use], fr[use], np.arange(-1, 4.01, 0.05), np.linspace(-1, 3, 90), logy=True)
            _median_line(ax, gi[use], fr[use], np.arange(-1, 4.01, 0.25), pub.ORANGE)
        else:
            ax.set_yscale("log")
        ax.axhline(1, color=pub.INK, lw=0.6)
        ax.set_ylim(0.1, 1000); ax.set_xlim(-1, 4)
        pub._label(ax, f"({chr(97 + k)})", f"{x_} band, {title}", f"N = {pub._fmt(use.sum())}")
        info[x_] = {"n": int(use.sum()), "median": float(np.median(fr[use])) if use.sum() else None}
        if k >= 3:
            ax.set_xlabel(r"$(g-i)_{\rm EDP2}$")
        if k % 3 == 0:
            ax.set_ylabel(r"fluxR = 1 + $f_{\rm diff}/f_{\rm tmpl}$")
    pub._finish(fig, "", out, name, book)
    return info


def fig_r1(one, e, tp, sci, out, book, pub):
    """Where and what the unmatched DIAObjects are."""
    import sky_projection as sp
    plt = pub._plt()
    d = tp[["dia_object_id", "ra_deg", "dec_deg", "field", "n_sources"]].merge(
        sci[["dia_object_id", "median_reliability", "peak_snr_any"]], on="dia_object_id", validate="one_to_one")
    d["unmatched"] = d.dia_object_id.isin(e.dia_object_id[~e.matched]).to_numpy()
    # page 1: unmatched fraction on the sky, then four 1-degree zooms (densest 1-degree cell of each field)
    fig = plt.figure(figsize=(pub.WIDTH, 8.2))
    gs = fig.add_gridspec(3, 2, height_ratios=[1.25, 1, 1], hspace=0.35, wspace=0.28)
    ax = fig.add_subplot(gs[0, :], projection="aitoff")
    i_ra, i_dec = sp.cell_index(d.ra_deg.to_numpy(), d.dec_deg.to_numpy())
    tot = sp.count_grid(i_ra, i_dec, np.ones(len(d)))
    un = sp.count_grid(i_ra, i_dec, d.unmatched.to_numpy(float))
    with np.errstate(divide="ignore", invalid="ignore"):
        frac = np.where(tot >= 20, un / tot, np.nan)
    xe, ye, fm = sp.to_map_order(frac)
    pc = ax.pcolormesh(xe, ye, np.ma.masked_invalid(fm), cmap="viridis", vmin=0, vmax=1, rasterized=True)
    ax.grid(True, lw=0.3, color="#bbbbbb"); ax.set_xticklabels([])
    cb = fig.colorbar(pc, ax=ax, fraction=0.025, pad=0.02); cb.set_label("Fraction with no EDP2 match")
    ax.set_yticklabels([])
    pub._label(ax, "(a)", "fraction with no EDP2 match, 1° cells with ≥ 20 DIAObjects", x=0.0, y=1.10)
    zooms = []
    for fld in ("Deep drilling fields", "M49 field", "Galactic bulge", "Low-dust WFD"):
        s = d[d.field == fld]
        key = np.floor(s.ra_deg).astype(int) * 1000 + np.floor(s.dec_deg + 90).astype(int)
        best = key.value_counts().index[0]
        ra0, dec0 = best // 1000, best % 1000 - 90
        zooms.append((fld, ra0, dec0))
    info = {"zooms": []}
    for k, (fld, ra0, dec0) in enumerate(zooms):
        z = fig.add_subplot(gs[1 + k // 2, k % 2])
        box = d[(d.ra_deg >= ra0) & (d.ra_deg < ra0 + 1) & (d.dec_deg >= dec0) & (d.dec_deg < dec0 + 1)]
        mt, um = box[~box.unmatched], box[box.unmatched]
        z.scatter(mt.ra_deg, mt.dec_deg, s=1.2, marker=".", color=pub.GREY, alpha=0.6, rasterized=True, label="EDP2 match")
        z.scatter(um.ra_deg, um.dec_deg, s=4, marker="x", lw=0.5, color=pub.RED, rasterized=True, label="no EDP2 match")
        z.set_xlim(ra0 + 1, ra0); z.set_ylim(dec0, dec0 + 1)
        z.set_xlabel("RA (deg)"); z.set_ylabel("Dec (deg)")
        z.text(0.03, 0.97, f"({chr(98 + k)}) {fld}: {pub._fmt(len(um))} of {pub._fmt(len(box))} unmatched", transform=z.transAxes,
               va="top", ha="left", fontsize=8, zorder=8, bbox=dict(facecolor="white", edgecolor="none", alpha=0.85, pad=1.5))
        info["zooms"].append({"field": fld, "ra0": int(ra0), "dec0": int(dec0), "n": int(len(box)), "unmatched": int(len(um))})
        if k == 0:
            z.legend(loc="lower right", fontsize=7, markerscale=3)
    pub._finish(fig, "", out, "R1a_unmatched_on_the_sky", book)
    # page 2: unmatched fraction against alerts per object, reliability, peak S/N; and by field
    fig, axes = plt.subplots(2, 2, figsize=(pub.WIDTH, 5.8), gridspec_kw={"hspace": 0.42, "wspace": 0.32})
    a, b, c, f = axes.ravel()
    order = ["1", "2", "3-4", "5-9", "10-19", ">= 20"]
    g = d.groupby(nalert_bins(d.n_sources.to_numpy())).unmatched.agg(["mean", "size"]).reindex(order)
    a.bar(range(6), g["mean"], color=pub.ORANGE)
    for i_, (fr_, n_) in enumerate(zip(g["mean"], g["size"])):
        a.text(i_, fr_ + 0.01, f"{fr_:.2f}\n{pub._fmt(n_)}", ha="center", va="bottom", fontsize=6.5)
    a.set_xticks(range(6)); a.set_xticklabels(order); a.set_ylim(0, 0.6)
    a.set_xlabel("Alerts per DIA object"); a.set_ylabel("Fraction with no EDP2 match")
    pub._label(a, "(a)", x=0.04, y=0.955)
    info["by_nalerts"] = {k_: {"frac": float(v_["mean"]), "n": int(v_["size"])} for k_, v_ in g.iterrows()}
    for ax_, col, edges, lab, let in ((b, "median_reliability", np.linspace(0, 1, 21), "Median alert reliability", "(b)"),
                                     (c, "peak_snr_any", np.logspace(np.log10(5), 3, 21), "Peak alert S/N", "(c)")):
        v = d[col].to_numpy(float)
        k = np.digitize(v, edges)
        cc = np.sqrt(edges[1:] * edges[:-1]) if let == "(c)" else 0.5 * (edges[1:] + edges[:-1])
        fr_ = np.array([d.unmatched.to_numpy()[k == j].mean() if (k == j).sum() >= 50 else np.nan for j in range(1, len(edges))])
        ax_.plot(cc, fr_, "-o", color=pub.ORANGE, ms=3)
        ax_.set_ylim(0, 1); ax_.set_xlabel(lab); ax_.set_ylabel("Fraction with no EDP2 match")
        if let == "(c)":
            ax_.set_xscale("log")
        pub._label(ax_, let, x=0.04, y=0.955)
        info[f"by_{col}"] = {f"{cc[j]:.3g}": (None if np.isnan(fr_[j]) else float(fr_[j])) for j in range(len(cc))}
    gf = d.groupby("field").unmatched.agg(["mean", "size"]).sort_values("size", ascending=False)
    f.barh(range(len(gf))[::-1], gf["mean"], color=pub.ORANGE)
    f.set_yticks(range(len(gf))[::-1]); f.set_yticklabels(gf.index, fontsize=7); f.set_xlim(0, 1)
    f.set_xlabel("Fraction with no EDP2 match")
    pub._label(f, "(d)", x=0.80, y=0.955)
    info["by_field"] = {k_: {"frac": float(v_["mean"]), "n": int(v_["size"])} for k_, v_ in gf.iterrows()}
    pub._finish(fig, "", out, "R1b_unmatched_by_property", book)
    return info


def build(match_path, cmodel_path, control_path, run_dir, catalog, data_root, out):
    import alert_colour_diagrams as acd
    import edp2_figures as ef
    import edp2_paper_figures_v2 as v2
    import paper_figures_pub as pub
    from matplotlib.backends.backend_pdf import PdfPages
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    one = pd.read_parquet(match_path)
    cm = pd.read_parquet(cmodel_path)
    ctrl = pd.read_parquet(control_path)
    R = Path(run_dir)
    tp = pd.read_parquet(R / "01_template_colours/tables/dia_object_template_photometry.parquet")
    sci = pd.read_parquet(R / "02_science_suite/tables/dia_object_science_table.parquet")
    assert len(one) == len(tp) == len(sci) == N_OBJ
    conn = acd.open_catalog(catalog, data_root)
    al = conn.execute("""SELECT population, dia_object_id, band, psf_flux, psf_flux_err, snr, template_flux,
                                template_flux_err FROM sources""").df()
    conn.close()
    assert len(al) == N_ALERTS and int((al.population == "DIA").sum()) == N_DIA
    assert int(al[al.population == "DIA"].groupby("dia_object_id").size().reindex(tp.dia_object_id).sub(tp.n_sources.to_numpy()).abs().sum()) == 0, \
        "alerts per object differ from the paper table"
    e = v2.edp2_bands(one, cm)
    ef.RADIUS = 1.0
    one["matched"] = (one.sep_arcsec.astype(float) <= 1.0).to_numpy(bool)
    cls = ef.ext_class(one)
    cls_by_id = pd.Series(cls.to_numpy(), index=one.dia_object_id.astype("int64").to_numpy())
    assert cls_by_id.index.is_unique
    ecol = pd.DataFrame({"dia_object_id": e.dia_object_id.astype("int64").to_numpy(),
                         "gi_edp2": np.where(e.ok_g & e.ok_i, e.mag_g - e.mag_i, np.nan)})
    ecol["cls"] = cls_by_id.reindex(ecol.dia_object_id).to_numpy()      # aligned by id, not by row order
    assert int((ecol.cls == 0).sum()) == int((cls == 0).sum()) and int((ecol.cls == 1).sum()) == int((cls == 1).sum())
    pub.SHOW_CAPTIONS = False
    info = {"answers": {"dia_objects": N_OBJ, "matched_1arcsec": int(e.matched.sum()), "unmatched_1arcsec": int((~e.matched).sum()),
                        "unresolved_r_ext0": int((cls == 0).sum()), "resolved_r_ext1": int((cls == 1).sum()),
                        "matched_unusable_r_ext": int((one.matched & cls.isna()).sum())}}
    with PdfPages(out / "Fink_LSST_review_20261118.pdf", metadata={"Title": "Fink LSST: plots for the 2026-11-18 review",
                                                                     "Author": "Darim Shamsi"}) as book:
        info["R1"] = fig_r1(one, e, tp, sci, out, book, pub)
        info["F3"] = fig_f3(al, tp, e, out, book, pub)
        info["SN"] = fig_sn(al, out, book, pub)
        info["F4"] = fig_f4(one, ctrl, tp, al, ecol, out, book, pub, ef)
        info["FR_unresolved"] = fig_fr6(al, ecol, 0, "unresolved", out, book, pub, "FR6_fluxratio_unresolved")
        info["FR_resolved"] = fig_fr6(al, ecol, 1, "resolved", out, book, pub, "FR6_fluxratio_resolved")
    (out / "review_summary.json").write_text(json.dumps(info, indent=2, default=str) + "\n")
    print(json.dumps(info, indent=1, default=str))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    for k in ("match", "cmodel", "control", "run-dir", "catalog", "data-root", "out"):
        p.add_argument(f"--{k}", required=True)
    a = p.parse_args()
    build(a.match, a.cmodel, a.control, a.run_dir, a.catalog, a.data_root, a.out)

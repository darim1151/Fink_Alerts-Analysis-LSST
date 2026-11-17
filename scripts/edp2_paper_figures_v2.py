#!/usr/bin/env python
"""Paper figures redone after Z. Ivezic's comments of 2026-11-16 (figure numbers of the draft "paper-2"):

  fig03   Fig. 3 with panel (d) from EDP2 cModel magnitudes (DIAObjects with an EDP2 Object within 1 arcsec)
  fig04   Fig. 4 = the top two panels of the EDP2 match-check page
  fig10   Fig. 10: (a) unchanged (template, three SIMBAD groups); (b)-(d) SIMBAD type / no SIMBAD object only, each
          object's magnitude from EDP2 cModel where it has a usable EDP2 flux in that band, otherwise from its template
  fig11   Fig. 11 as four panels: (a) EDP2 cModel colours (matched), (b) template colours of the DIAObjects without an
          EDP2 match, (c) same-night transient colours over (a), (d) time span; no legend row
  fig14   Fig. 14 twice: template colours of DIAObjects without an EDP2 match; EDP2 cModel colours of matched ones
  fig15   Fig. 15 twice, likewise

"Matched" = an EDP2 Object within 1 arcsec (nearest). A band is usable in EDP2 when its cModel flux and error are
positive, S/N >= 5 and the cModel flag is not set. Template rules are the paper's (unchanged). No dust correction.
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

RADIUS = 1.0
BANDS = ("u", "g", "r", "i", "z", "y")
AB_ZP = 31.4
N_OBJECTS = 3_487_175
EDP2_SUBS = ((r"\mathrm{tmpl}", r"\mathrm{EDP2}"), (r"{\rm tmpl}", r"{\rm EDP2}"), ("template PSF, AB", "cModel, AB"),
             ("Template PSF magnitude (AB)", "EDP2 cModel magnitude (AB)"))


# ----------------------------------------------------------------------------- pure, tested
def edp2_bands(one, cm, radius=RADIUS):
    """Per DIAObject: `matched` (nearest EDP2 Object within radius) and, per band, ok_b / mag_b from EDP2 cModel."""
    d = one[["dia_object_id", "sep_arcsec"]].merge(cm, on="dia_object_id", how="left", validate="one_to_one")
    matched = (d.sep_arcsec.astype(float) <= radius).to_numpy(bool) & d.objectId.notna().to_numpy()
    out = pd.DataFrame({"dia_object_id": d.dia_object_id.to_numpy(), "matched": matched})
    for b in BANDS:
        f = d[f"{b}_cModelFlux"].astype(float).to_numpy()
        e = d[f"{b}_cModelFluxErr"].astype(float).to_numpy()
        flg = d[f"{b}_cModel_flag"].astype("boolean").fillna(True).to_numpy(bool)
        with np.errstate(divide="ignore", invalid="ignore"):
            ok = matched & (f > 0) & (e > 0) & (f / e >= 5) & ~flg
            out[f"ok_{b}"] = ok
            out[f"mag_{b}"] = np.where(ok, AB_ZP - 2.5 * np.log10(np.where(ok, f, 1.0)), np.nan)
    return out


def recolour(t):
    """Colours and colour-panel flags from ok_b / mag_b (same definitions as the template tables)."""
    t = t.copy()
    t["gi"], t["gr"], t["ri"], t["ug"] = t.mag_g - t.mag_i, t.mag_g - t.mag_r, t.mag_r - t.mag_i, t.mag_u - t.mag_g
    for c in ("gi", "gr", "ri", "ug"):
        t[c] = t[c].astype(float)
    t["panel_a"] = t.ok_g & t.ok_r & t.ok_i
    t["panel_b"] = t.ok_u & t.ok_g & t.ok_r
    t["panel_c"] = t["panel_a"]
    return t


def swap_to_edp2(t, e):
    """Copy of a per-object table with every band's ok/mag (and colours) taken from EDP2; unmatched rows get no band."""
    t = t.merge(e[["dia_object_id", "matched"]], on="dia_object_id", how="left", validate="one_to_one")
    ee = e.set_index("dia_object_id").reindex(t.dia_object_id)
    for b in BANDS:
        t[f"ok_{b}"] = ee[f"ok_{b}"].to_numpy(bool)
        t[f"mag_{b}"] = ee[f"mag_{b}"].to_numpy(float)
    return recolour(t)


def only_unmatched(t, e):
    """Rows of a per-object table whose DIAObject has no EDP2 Object within the radius (template values unchanged)."""
    keep = set(e.dia_object_id[~e.matched])
    return t[t.dia_object_id.isin(keep)].copy()


def hybrid(tp, e, band):
    """Per object: EDP2 cModel magnitude where usable in this band, else the template magnitude where usable."""
    ee = e.set_index("dia_object_id").reindex(tp.dia_object_id)
    use_e = ee[f"ok_{band}"].to_numpy(bool)
    use_t = ~use_e & tp[f"ok_{band}"].fillna(False).to_numpy(bool)
    mag = np.where(use_e, ee[f"mag_{band}"].to_numpy(float), np.where(use_t, tp[f"mag_{band}"].to_numpy(float), np.nan))
    return mag, use_e, use_t


def red_flags(v, red_cut):
    """Recompute the R3 derived columns after a photometry swap (same rules as red_objects_and_red_bands.prepare)."""
    v = v.copy()
    v["iz"], v["zy"] = v.mag_i - v.mag_z, v.mag_z - v.mag_y
    v["z_a"], v["z_b"], v["z_c"] = v.ok_i & v.ok_z, v.ok_r & v.ok_i & v.ok_z, v.ok_i & v.ok_z & v.ok_y
    v["red"] = v.panel_a & (v.gi > red_cut)
    v["blue"] = v.panel_a & (v.gi <= red_cut)
    return v


# ----------------------------------------------------------------------------- new layouts
def fig04(one, ctrl, tp, out, book, pub, ef):
    plt = pub._plt()
    fig, (a, b) = plt.subplots(1, 2, figsize=(pub.WIDTH, 3.1), gridspec_kw={"wspace": 0.34})
    r = np.linspace(0, 2.0, 201)
    for d, col, lab in ((one, pub.BLUE, "DIAObject positions"), (ctrl, pub.GREY, "positions moved by 30″")):
        sep = np.sort(d.sep_arcsec.astype(float).to_numpy())
        sep = sep[np.isfinite(sep)]
        a.plot(r, np.searchsorted(sep, r, side="right") / len(d), color=col, lw=1.4, label=lab)
    a.axvline(RADIUS, color=pub.INK, lw=0.8, ls="--")
    a.set_xlim(0, 2); a.set_ylim(0, 1)
    a.set_xlabel("Radius (arcsec)"); a.set_ylabel("Fraction with an EDP2 Object")
    a.legend(loc="center right", fontsize=8)
    pub._label(a, "(a)")
    cls = ef.ext_class(one)
    t = tp.set_index("dia_object_id")
    ok_t = t["ok_i"].reindex(one.dia_object_id).fillna(False).to_numpy().astype(bool)
    sel = ok_t & (cls == 0).to_numpy() & ef.good_psf(one, "i").to_numpy()
    mi = ef.psf_mag(one.i_psfFlux.to_numpy(dtype=float))[sel]
    dm = t["mag_i"].reindex(one.dia_object_id).to_numpy()[sel] - mi
    b.hist2d(mi, dm, bins=[np.arange(14, 24.01, 0.2), np.arange(-1, 1.001, 0.02)], cmap="Greys", cmin=1, rasterized=True)
    edges = np.arange(14, 24.01, 0.5)
    k = np.digitize(mi, edges)
    med = [np.median(dm[k == j]) if (k == j).sum() >= 20 else np.nan for j in range(1, len(edges))]
    b.plot(0.5 * (edges[1:] + edges[:-1]), med, "-o", color=pub.ORANGE, ms=3, lw=1.2, label="median")
    b.axhline(0, color=pub.INK, lw=0.6)
    b.set_xlabel(r"$i_{\rm EDP2}$ (PSF, AB)"); b.set_ylabel(r"$i_{\rm tmpl} - i_{\rm EDP2}$")
    b.legend(loc="lower left", fontsize=8)
    pub._label(b, "(b)", None, f"N = {pub._fmt(sel.sum())} unresolved")
    pub._finish(fig, "", out, "Fig04_edp2_match_checks", book)
    return {"b_n": int(sel.sum()), "b_median": float(np.median(dm)), "b_robust_sigma": ef.robust_sigma(dm)}


def fig10(tp, e, sci, out, book, pub):
    acd = pub.acd
    plt = pub._plt()
    from matplotlib.lines import Line2D
    d = tp.merge(sci[["dia_object_id", "gaia_name"]], on="dia_object_id", how="left", validate="one_to_one")
    typed, nom, fail = d.simbad_group.isin(pub.TYPED), d.simbad_group == acd.NO_MATCH, d.simbad_group == acd.XM_FAILED
    dusty = d.field.isin(pub.DUSTY)
    fig = plt.figure(figsize=(pub.WIDTH, 8.4))
    gs = fig.add_gridspec(3, 2, height_ratios=[1, 1, 1.08], hspace=0.42, wspace=0.28)
    ha, hb, hc, hd = (fig.add_subplot(gs[0, 0]), fig.add_subplot(gs[0, 1]), fig.add_subplot(gs[1, 0]), fig.add_subplot(gs[1, 1]))
    edges = np.arange(13, 25.01, 0.25)
    info = {}
    groups3 = [("SIMBAD type", typed, pub.BLUE), ("No SIMBAD object within 1″", nom, pub.GREY), ("Crossmatch failed", fail, pub.ORANGE)]
    for gname, m, col in groups3:                     # (a): unchanged definition (template i, all fields)
        v = d.loc[m & d.ok_i, "mag_i"]
        info[("a", gname)] = (int(len(v)), float(v.median()))
        h, _ = np.histogram(v, edges)
        ha.stairs(h / h.sum() / 0.25, edges, color=col, lw=1.2)
    ha.set_xlabel(r"$i_{\rm tmpl}$ (AB)")
    for ax, band, sm, let, lab in ((hb, "i", ~dusty, "(b)", "i band, outside bulge and plane"),
                                   (hc, "i", dusty, "(c)", "i band, bulge and plane fields"),
                                   (hd, "r", pd.Series(True, index=d.index), "(d)", "r band, all fields")):
        mag, use_e, use_t = hybrid(d, e, band)
        for gname, m, col in groups3[:2]:
            s = (sm & m).to_numpy() & np.isfinite(mag)
            info[(let, gname)] = {"n": int(s.sum()), "edp2": int((s & use_e).sum()), "template": int((s & use_t).sum()),
                                  "median": float(np.median(mag[s])) if s.any() else float("nan")}
            if s.any():
                h, _ = np.histogram(mag[s], edges)
                ax.stairs(h / h.sum() / 0.25, edges, color=col, lw=1.2)
        ax.set_xlabel(rf"${band}_{{\rm EDP2}}$ or ${band}_{{\rm tmpl}}$ (AB)")
        info[(let, "label")] = lab
    for ax, let, lab in ((ha, "(a)", "i band, all fields"), (hb, "(b)", info[("(b)", "label")]),
                         (hc, "(c)", info[("(c)", "label")]), (hd, "(d)", info[("(d)", "label")])):
        ax.set_xlim(13, 25); ax.set_ylim(0, None); ax.set_ylabel("Normalised density")
        pub._label(ax, let, lab)
        ax.set_ylim(0, ax.get_ylim()[1] * (2.0 if ax is ha else 1.6))
    ha.legend(handles=[Line2D([], [], color=c, lw=1.6, label=g) for g, _, c in groups3], loc="upper right",
              bbox_to_anchor=(1.0, 0.86), fontsize=7.5, handlelength=1.4)
    fb = np.arange(14, 25.01, 0.5)                  # (e), (f): unchanged (template i)
    ex = fig.add_subplot(gs[2, 0])
    ans = d.ok_i & ~fail
    for (sm, col, short) in ((pd.Series(True, index=d.index), pub.INK, "all fields"), (~dusty, pub.BLUE, "outside bulge/plane"),
                             (dusty, pub.ORANGE, "bulge/plane")):
        s = ans & sm
        pub._frac(ex, d.mag_i[s], typed[s], fb, col, short)
    ex.set_ylim(0, 0.34); ex.set_xlim(14, 25); ex.set_xlabel(r"$i_{\rm tmpl}$ (AB)"); ex.set_ylabel("Fraction with a SIMBAD type")
    ex.legend(loc="upper right", fontsize=7.5); pub._label(ex, "(e)")
    fx = fig.add_subplot(gs[2, 1])
    gaia = d.gaia_name.notna()
    for (gname, m, col), short in zip(groups3[:2], ("SIMBAD type", "No SIMBAD match")):
        s = d.ok_i & m
        pub._frac(fx, d.mag_i[s], gaia[s], fb, col, short)
    fx.set_ylim(0, 1.18); fx.set_xlim(14, 25); fx.set_xlabel(r"$i_{\rm tmpl}$ (AB)"); fx.set_ylabel("Fraction with a Gaia DR3 source")
    fx.legend(loc="center left", fontsize=7.5); pub._label(fx, "(f)")
    for ax in fig.axes:
        ax.minorticks_on()
    pub._finish(fig, "", out, "Fig10_simbad_comparison_edp2", book)
    return {f"{k[0]} {k[1]}": v for k, v in info.items()}


def fig11(tc, tpE, tpU, sl, out, book, pub):
    plt = pub._plt()
    xr, yr = pub.T_RANGES
    fig, axes = plt.subplots(2, 2, figsize=(pub.WIDTH, 5.9), gridspec_kw={"wspace": 0.32, "hspace": 0.34})
    a, b, c, d4 = axes.ravel()
    t = tc[~tc.field.isin(pub.DUSTY)]
    sE = tpE[tpE.panel_a & ~tpE.field.isin(pub.DUSTY)]
    sU = tpU[tpU.panel_a & ~tpU.field.isin(pub.DUSTY)]
    pub._tbase(a, sE, sl)
    a.set_xlabel(r"$(g-r)_{\rm EDP2}$"); a.set_ylabel(r"$(r-i)_{\rm EDP2}$")
    pub._label(a, "(a)", "EDP2 match", f"N = {pub._fmt(len(sE))}")
    import plot_colour_diagrams as kit
    if len(sU) >= 500:
        pub._tbase(b, sU, sl)
    else:                                          # too few for a density map: points over the locus band
        kit.draw_locus(b, sl, "gr", "ri")
        b.set_xlim(*xr); b.set_ylim(*yr); b.set_xticks([-1, 0, 1, 2]); b.set_yticks([-1, 0, 1, 2]); b.minorticks_on()
    off_u = pub._tpoints(b, sU, 9) if len(sU) < 500 else 0
    b.set_xlabel(r"$(g-r)_{\rm tmpl}$"); b.set_ylabel(r"$(r-i)_{\rm tmpl}$")
    pub._label(b, "(b)", "no EDP2 match", f"N = {pub._fmt(len(sU))}")
    pub._tbase(c, sE, sl)
    off = pub._tpoints(c, t, 5)
    c.set_xlabel(r"$(g-r)_{\rm diff}$"); c.set_ylabel(r"$(r-i)_{\rm diff}$")
    pub._label(c, "(c)", None, f"N = {pub._fmt(len(t))}")
    top = max(6.0, float(np.ceil(t.span_hours.max())))
    d4.hist(t.span_hours, bins=np.arange(0, top + 0.01, 0.25), color=pub.BLUE, edgecolor="white", linewidth=0.3)
    d4.set_xlim(0, top); d4.set_ylim(0, None); d4.set_ylim(0, d4.get_ylim()[1] * 1.12)
    d4.set_xlabel("Time span of g, r, i alerts (h)"); d4.set_ylabel("Number of objects"); d4.minorticks_on()
    pub._label(d4, "(d)")
    pub._finish(fig, "", out, "Fig11_transient_colours_edp2", book)
    return {"a_edp2_static": int(len(sE)), "b_template_unmatched": int(len(sU)), "b_outside_plot": int(off_u),
            "c_transients": int(len(t)), "c_outside_plot": int(off)}


# ----------------------------------------------------------------------------- driver
def build(match_path, cmodel_path, control_path, run_dir, catalog, data_root, locus, out):
    import edp2_figures as ef
    import paper_figures_pub as pub
    import plot_colour_diagrams as kit
    import red_objects_and_red_bands as red
    from matplotlib.backends.backend_pdf import PdfPages
    from matplotlib.text import Text
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    one = pd.read_parquet(match_path)
    cm = pd.read_parquet(cmodel_path)
    ctrl = pd.read_parquet(control_path)
    # --- consistency of the two RSP passes: same nearest Object for every 1-arcsec match, nothing else
    assert len(one) == N_OBJECTS and one.dia_object_id.is_unique and cm.dia_object_id.is_unique
    m1 = one[one.sep_arcsec.astype(float) <= RADIUS]
    j = m1[["dia_object_id", "objectId"]].merge(cm[["dia_object_id", "objectId"]], on="dia_object_id", how="outer",
                                                  suffixes=("_first", "_cmodel"), indicator=True)
    assert (j._merge == "both").all(), j._merge.value_counts().to_dict()
    assert (j.objectId_first.astype("int64") == j.objectId_cmodel.astype("int64")).all(), "the two RSP passes disagree"
    e = edp2_bands(one, cm)
    assert int(e.matched.sum()) == len(m1)
    D = pub.load(run_dir, catalog, data_root)
    tp, sci, tc, v = D["tp"], D["sci"], D["tc"], D["v"]
    tpE, tpU = swap_to_edp2(tp, e), only_unmatched(tp, e)
    sciE, sciU = swap_to_edp2(sci, e), only_unmatched(sci, e)
    vE, vU = red_flags(swap_to_edp2(v, e), red.RED_CUT), only_unmatched(v, e)
    assert len(tpU) + int(e.matched.sum()) == N_OBJECTS and not tpU.dia_object_id.isin(e.dia_object_id[e.matched]).any()
    for b in BANDS:
        assert not tpE[f"ok_{b}"][~tpE.matched].any(), "an unmatched object has an EDP2 band"
    sl = kit.load_locus(locus)
    ef.RADIUS = RADIUS
    one["matched"] = (one.sep_arcsec.astype(float) <= RADIUS).to_numpy(bool)
    pub.SHOW_CAPTIONS = False
    original_finish = pub._finish
    texts = {}

    def finish(fig, caption, out_dir, name, book, legend=None, subs=()):
        for t in fig.findobj(Text):                 # label substitution, then a check that it took everywhere
            s0 = s1 = t.get_text()
            for a_, b_ in subs:
                s1 = s1.replace(a_, b_)
            if s1 != s0:
                t.set_text(s1)
        for ax in fig.axes:                         # large counts on the C1 twin axis: show thousands so the
            if ax.get_ylabel() == "Objects per 0.5 mag" and ax.get_ylim()[1] > 9999:   # label clears panel (b)
                from matplotlib.ticker import FuncFormatter
                ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _p: f"{v / 1000:g}"))
                ax.set_ylabel("Thousands per 0.5 mag")
        words = [t.get_text() for t in fig.findobj(Text) if t.get_text()]
        texts[name] = words
        if subs:
            assert not any("tmpl" in w for w in words), f"{name}: a template label survived"
        return original_finish(fig, caption, out_dir, name, book, legend=legend)

    info = {}
    with PdfPages(out / "Fink_LSST_figures_EDP2_revision.pdf", metadata={"Title": "Fink LSST paper figures, EDP2 revision",
                                                                           "Author": "Darim Shamsi"}) as book:
        def run(sub, fn, subs=()):
            d_ = out / sub
            d_.mkdir()
            pub._finish = lambda fig, cap, o, name, bk, legend=None: finish(fig, cap, o, f"{sub}_{name}", bk, legend, subs)
            try:
                return fn(d_)
            finally:
                pub._finish = original_finish
        info["fig03"] = run("fig03", lambda o: pub.fig_p1(D["bands"], D["det"], D["dmag"], D["n_pos"], tpE, o, book)[1], EDP2_SUBS)
        info["fig03"]["usable_edp2_per_band"] = {b: int(tpE[f"ok_{b}"].sum()) for b in BANDS}
        info["fig04"] = run("fig04", lambda o: fig04(one, ctrl, tp, o, book, pub, ef))
        info["fig10"] = run("fig10", lambda o: fig10(tp, e, sci, o, book, pub))
        info["fig11"] = run("fig11", lambda o: fig11(tc, tpE, tpU, sl, o, book, pub))
        info["fig14_template_unmatched"] = run("fig14_template_unmatched", lambda o: pub.fig_c1(sciU, o, book)[1])
        info["fig14_edp2_matched"] = run("fig14_edp2_matched", lambda o: pub.fig_c1(sciE, o, book)[1], EDP2_SUBS)
        for tag, vv, subs in (("fig15_template_unmatched", vU, ()), ("fig15_edp2_matched", vE, EDP2_SUBS)):
            n_red = int(vv.red.sum())
            info[tag] = run(tag, lambda o: pub.fig_r3(vv, list(red.PLX_PSTAR_ORDER), o, book)[1], subs) if n_red else {"skipped": "no object with g - i > 2"}
            info[tag]["n_red"] = n_red
    # independent recounts straight from the tables
    rc = {"fig14_template_unmatched_with_pstar": int((sciU.panel_a | sciU.panel_b)[sciU.pstar.notna()].sum()),
          "fig14_edp2_matched_with_pstar": int((sciE.panel_a | sciE.panel_b)[sciE.pstar.notna()].sum()),
          "fig11_a": int((tpE.panel_a & ~tpE.field.isin(pub.DUSTY)).sum()),
          "fig11_b": int((tpU.panel_a & ~tpU.field.isin(pub.DUSTY)).sum()),
          "unmatched_objects": int((~e.matched).sum()), "matched_objects": int(e.matched.sum())}
    assert rc["fig11_a"] == info["fig11"]["a_edp2_static"] and rc["fig11_b"] == info["fig11"]["b_template_unmatched"]
    (out / "figure_summary.json").write_text(json.dumps({"radius_arcsec": RADIUS, "recount": rc, **info}, indent=2, default=str) + "\n")
    print(json.dumps({"recount": rc, **info}, indent=1, default=str))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    for k in ("match", "cmodel", "control", "run-dir", "catalog", "data-root", "locus", "out"):
        p.add_argument(f"--{k}", required=True)
    a = p.parse_args()
    build(a.match, a.cmodel, a.control, a.run_dir, a.catalog, a.data_root, a.locus, a.out)

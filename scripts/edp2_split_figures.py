#!/usr/bin/env python
"""Two 3-panel versions of the EDP2 r-i vs g-r diagram, as asked by Z. Ivezic (2026-11-16):

  S1X  split by x = r_psfMag - r_cModelMag:          x < 0.02,  0.02 <= x <= 0.05,  x > 0.05 mag
  S1G  split by EDP2 griz_model_extendedness (G):   G < 0.2,   0.2 <= G <= 0.8,    G > 0.8
  S1C  comparison page: locus fraction per subsample, the 3 x 3 cross-tabulation, locus fraction vs r magnitude

Both figures use ONE common sample, so they can be compared object for object: DIAObjects outside the Galactic bulge
and plane fields with an EDP2 Object within 0.5 arcsec, EDP2 g, r, i PSF S/N >= 5 and unflagged, a positive unflagged
r cModel flux and a finite griz_model_extendedness in [0, 1]. No dust or AB-offset correction.

    python edp2_split_figures.py --match <edp2_match_one_per_object.parquet> --locus <RubinStellarLocus_COSMOS.txt> --out <new folder>
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
import edp2_figures as ef  # noqa: E402

RADIUS = 0.5
X_EDGES = (0.02, 0.05)     # mag
G_EDGES = (0.2, 0.8)
X_LABELS = ("x < 0.02 mag", "0.02 ≤ x ≤ 0.05 mag", "x > 0.05 mag")
G_LABELS = ("G < 0.2", "0.2 ≤ G ≤ 0.8", "G > 0.8")


# ----------------------------------------------------------------------------- pure, tested
def three_bins(v, edges):
    """0 below edges[0]; 1 for edges[0] <= v <= edges[1] (both ends inclusive); 2 above edges[1]; -1 if not finite."""
    v = np.asarray(v, dtype=float)
    lo, hi = edges
    out = np.full(v.shape, -1, dtype=int)
    fin = np.isfinite(v)
    out[fin & (v < lo)] = 0
    out[fin & (v >= lo) & (v <= hi)] = 1
    out[fin & (v > hi)] = 2
    return out


def common_sample(one):
    """The shared sample with gr, ri, r magnitude, x and G. Returns (frame, counts of each cut, in order)."""
    steps = {}
    m = ~one["field"].isin(ef.DUSTY)
    steps["outside bulge/plane"] = int(m.sum())
    m &= one["sep_arcsec"].astype(float).le(RADIUS).to_numpy()
    steps[f"EDP2 Object within {RADIUS:g}″"] = int(m.sum())
    for b in "gri":
        m &= ef.good_psf(one, b).to_numpy()
    steps["g, r, i PSF S/N ≥ 5, unflagged"] = int(m.sum())
    cm = one["r_cModelFlux"].astype(float).to_numpy()
    m &= (cm > 0) & np.isfinite(cm) & ~ef.flag(one["r_cModel_flag"]).to_numpy()
    steps["r cModel flux > 0, unflagged"] = int(m.sum())
    g = one["griz_model_extendedness"].astype(float).to_numpy()
    m &= np.isfinite(g) & (g >= 0) & (g <= 1)
    steps["griz_model_extendedness in [0, 1]"] = int(m.sum())
    s = one.loc[m, ["dia_object_id", "field"]].reset_index(drop=True)
    f = {b: one.loc[m, f"{b}_psfFlux"].astype(float).to_numpy() for b in "gri"}
    s["gr"] = 2.5 * np.log10(f["r"] / f["g"])
    s["ri"] = 2.5 * np.log10(f["i"] / f["r"])
    s["rmag"] = ef.psf_mag(f["r"])
    s["x"] = -2.5 * np.log10(f["r"] / cm[m])            # r_psfMag - r_cModelMag
    s["G"] = g[m]
    s["xbin"] = three_bins(s.x, X_EDGES)
    s["gbin"] = three_bins(s.G, G_EDGES)
    re = one.loc[m, "r_extendedness"].astype(float).to_numpy()
    s["r_ext"] = np.where(~ef.flag(one.loc[m, "r_extendedness_flag"]).to_numpy(), re, np.nan)
    return s, steps


def on_locus(gr, ri, X, Y, mask):
    """True where (gr, ri) falls on a cell of the stellar-locus band grid (kit.locus_region)."""
    gr, ri = np.asarray(gr, float), np.asarray(ri, float)
    i = np.clip(np.searchsorted(X[0], gr), 0, X.shape[1] - 1)
    j = np.clip(np.searchsorted(Y[:, 0], ri), 0, Y.shape[0] - 1)
    inside = (gr >= X[0, 0]) & (gr <= X[0, -1]) & (ri >= Y[0, 0]) & (ri <= Y[-1, 0])
    return inside & mask[j, i]


# ----------------------------------------------------------------------------- figures
def fig_three(s, col, labels, name, title_word, sl, out, book, pub):
    plt = pub._plt()
    xr, yr = pub.T_RANGES
    fig, axes = plt.subplots(1, 3, figsize=(pub.WIDTH, 2.75), gridspec_kw={"wspace": 0.12})
    info = []
    for k, (ax, lab) in enumerate(zip(axes, labels)):
        sub = s[s[col] == k]
        pub._tbase(ax, sub, sl)
        ax.set_xlabel(r"$(g-r)_{\rm EDP2}$")
        if k == 0:
            ax.set_ylabel(r"$(r-i)_{\rm EDP2}$")
        else:
            ax.tick_params(labelleft=False)
        pub._label(ax, f"({chr(97 + k)})", lab, f"N = {pub._fmt(len(sub))}")
        ins = sub.gr.between(*xr) & sub.ri.between(*yr)
        info.append({"bin": lab, "n": int(len(sub)), "outside_plot": int((~ins).sum()),
                     "on_locus": float(sub.onloc.mean()) if len(sub) else float("nan"),
                     "on_red_branch": float(sub.redbranch.mean()) if len(sub) else float("nan")})
    dens, cont, locus = pub._locus_handles()
    dens.set_label("EDP2 PSF colours (density)")
    parts = "; ".join(f"({chr(97 + k)}) {d['bin']}: {pub._fmt(d['n'])}" for k, d in enumerate(info))
    cap = (f"Figure {name}. EDP2 colours of the {pub._fmt(len(s))} DIAObjects outside the Galactic bulge and plane fields "
           f"with an EDP2 Object within {RADIUS:g}″ (S/N ≥ 5 and no flags in g, r and i), split by {title_word}: {parts}. "
           "Grey shading is the logarithmic number density and the contours enclose 50, 80 and 95% of the objects in each "
           f"panel; {', '.join(str(d['outside_plot']) for d in info)} objects fall outside the plotted range. The red band is "
           "the region within 2σ of the Rubin COSMOS stellar locus in both colours. No extinction or DP2 AB-offset correction.")
    return pub._finish(fig, cap, out, f"{name}_{col}", book, legend=([dens, cont, locus], 3)), info, cap


def fig_compare(s, out, book, pub):
    plt = pub._plt()
    fig = plt.figure(figsize=(pub.WIDTH, 6.4))
    gs = fig.add_gridspec(2, 2, hspace=0.42, wspace=0.38)
    a, b, c = fig.add_subplot(gs[0, 0]), fig.add_subplot(gs[0, 1]), fig.add_subplot(gs[1, :])
    # (a) fraction on the red stellar branch (locus band with r - i > 0.8, where galaxies are rare) per subsample
    w = 0.38
    for off, col, colour, nm in ((-w / 2, "xbin", pub.BLUE, "x = PSF − cModel"), (w / 2, "gbin", pub.ORANGE, "G = griz_model_ext.")):
        fr = [s.redbranch[s[col] == k].mean() for k in range(3)]
        bars = a.bar(np.arange(3) + off, fr, width=w, color=colour, label=nm)
        for bar, f in zip(bars, fr):
            a.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.004, f"{f:.3f}", ha="center", va="bottom", fontsize=7)
    a.set_xticks(range(3))
    a.set_xticklabels(["x < 0.02\nG < 0.2", "x 0.02–0.05\nG 0.2–0.8", "x > 0.05\nG > 0.8"], fontsize=7.5)
    a.set_ylim(0, max(s.redbranch[s.xbin == 0].mean(), s.redbranch[s.gbin == 0].mean()) * 1.45)
    a.set_ylabel("Fraction on the red stellar branch")
    a.legend(loc="upper right", fontsize=7.5)
    pub._label(a, "(a)", x=0.04, y=0.955)
    # (b) cross-tabulation, percent of the common sample
    t = pd.crosstab(s.xbin, s.gbin).reindex(index=range(3), columns=range(3), fill_value=0)
    pct = 100 * t.to_numpy() / len(s)
    b.imshow(pct, cmap="Greys", vmin=0, vmax=max(pct.max(), 1) * 1.6)
    for i in range(3):
        for j in range(3):
            b.text(j, i, f"{pct[i, j]:.1f}%\n{pub._fmt(t.iat[i, j])}", ha="center", va="center", fontsize=7.5)
    b.set_xticks(range(3))
    b.set_xticklabels(["< 0.2", "0.2–0.8", "> 0.8"])
    b.set_yticks(range(3))
    b.set_yticklabels(["< 0.02", "0.02–0.05", "> 0.05"])
    b.set_xlabel("griz_model_extendedness")
    b.set_ylabel("x = PSF − cModel (mag)")
    b.tick_params(top=False, right=False, which="both")
    b.minorticks_off()
    pub._label(b, "(b)", x=-0.42, y=1.08)
    # (c) locus fraction against r for the 'extended' subsample of each method, and for r_extendedness = 1
    edges = np.arange(16, 25.01, 0.5)
    cc = 0.5 * (edges[1:] + edges[:-1])
    for m, colour, lab in ((s.xbin == 2, pub.BLUE, "x > 0.05 mag"), (s.gbin == 2, pub.ORANGE, "G > 0.8"),
                           (s.r_ext == 1, pub.GREY, "r_extendedness = 1 (current figure)")):
        n, _ = np.histogram(s.rmag[m], edges)
        k, _ = np.histogram(s.rmag[m & s.redbranch], edges)
        g = n >= 50
        c.plot(cc[g], (k / np.maximum(n, 1))[g], "-o", ms=3, lw=1.3, color=colour, label=lab)
    c.set_ylim(0, None)
    c.set_xlim(16, 25)
    c.set_xlabel(r"$r_{\rm EDP2}$ (PSF, AB)")
    c.set_ylabel("Fraction on the red stellar branch")
    c.legend(loc="upper left", bbox_to_anchor=(0.06, 1.0), fontsize=8)
    c.minorticks_on()
    pub._label(c, "(c)", x=0.02, y=0.955)
    cap = "Comparison page for the two classifier splits (not a paper figure)."
    return pub._finish(fig, cap, out, "S1C_classifier_comparison", book), {"crosstab_counts": t.to_numpy().tolist()}


def build(match_path, locus, out):
    import paper_figures_pub as pub
    import plot_colour_diagrams as kit
    from matplotlib.backends.backend_pdf import PdfPages
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    one = pd.read_parquet(match_path)
    if len(one) != ef.N_OBJECTS or not one.dia_object_id.is_unique:
        raise ValueError("match table must have one row per DIAObject")
    s, steps = common_sample(one)
    sl = kit.load_locus(locus)
    X, Y, mask = kit.locus_region(sl, "gr", "ri")
    s["onloc"] = on_locus(s.gr, s.ri, X, Y, mask)
    s["redbranch"] = s.onloc & (s.ri > 0.8)       # M-dwarf part of the locus, which galaxies rarely reach
    # independent recount with np.histogram on explicit edges (separate from three_bins)
    for col, v, (lo, hi) in (("xbin", s.x, X_EDGES), ("gbin", s.G, G_EDGES)):
        below, above = int((v < lo).sum()), int((v > hi).sum())
        assert [below, len(s) - below - above, above] == [int((s[col] == k).sum()) for k in range(3)], col
        assert (s[col] >= 0).all(), f"{col}: an object fell in no bin"
    pub.SHOW_CAPTIONS = False
    caps, info = {}, {}
    with PdfPages(out / "EDP2_classifier_split_comparison.pdf",
                  metadata={"Title": "EDP2 r-i vs g-r split two ways", "Author": "Darim Shamsi"}) as book:
        _, info["S1X"], caps["S1X"] = fig_three(s, "xbin", X_LABELS, "S1X", "x = r_psfMag − r_cModelMag", sl, out, book, pub)
        _, info["S1G"], caps["S1G"] = fig_three(s, "gbin", G_LABELS, "S1G", "EDP2 griz_model_extendedness G", sl, out, book, pub)
        _, info["S1C"] = fig_compare(s, out, book, pub)
    for key in ("S1X", "S1G"):
        assert sum(d["n"] for d in info[key]) == len(s), f"{key}: panels do not add up to the sample"
    (out / "captions.txt").write_text("\n\n".join(caps[k] for k in ("S1X", "S1G")) + "\n")
    summary = {"sample_steps": steps, "n_common": int(len(s)), "x_edges": X_EDGES, "g_edges": G_EDGES, **info,
               "r_extendedness_in_sample": {"0": int((s.r_ext == 0).sum()), "1": int((s.r_ext == 1).sum()),
                                            "unusable": int(s.r_ext.isna().sum())},
               "on_locus_r_ext_1": float(s.onloc[s.r_ext == 1].mean()),
               "on_red_branch_r_ext_1": float(s.redbranch[s.r_ext == 1].mean())}
    (out / "split_summary.json").write_text(json.dumps(summary, indent=2, default=str) + "\n")
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--match", required=True)
    p.add_argument("--locus", required=True)
    p.add_argument("--out", required=True)
    a = p.parse_args()
    build(a.match, a.locus, a.out)

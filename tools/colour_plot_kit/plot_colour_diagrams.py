#!/usr/bin/env python
"""Colour-colour diagrams of Fink LSST DIA objects with the Rubin stellar locus overlaid.

Reads the exported table fink_dia_template_colours.csv.gz (template PSF magnitudes per DIA object, AB) and
a stellar-locus file such as RubinStellarLocus_COSMOS.txt, and draws four colour-colour panels:

    g-r vs u-g,  r-i vs g-r,  i-z vs r-i,  z-y vs i-z

Each panel shows all DIA objects with the needed bands as a grey log-density map with contours enclosing 50/80/95%
of them, the stellar locus as a red band (mean +/- 2 sigma, drawn as in the mentor's example code), and optionally a
selected subsample as blue points. With --facet, one row is drawn per group of a table column.

Examples (run from this folder):
    python plot_colour_diagrams.py --data fink_dia_template_colours.csv.gz --locus RubinStellarLocus_COSMOS.txt
    python plot_colour_diagrams.py --data ... --locus ... --select "has_gri and gi > 2" --name red_objects
    python plot_colour_diagrams.py --data ... --locus ... --facet simbad_group
    python plot_colour_diagrams.py --data ... --locus ... --facet fink_cats_label --select "has_gri and gi > 2" --name red_by_label
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

BANDS = ("u", "g", "r", "i", "z", "y")
COLOURS = ("ug", "gr", "ri", "iz", "zy")           # adjacent-band colours, bluer minus redder band
LOCUS_COLS = {"ug": ("u-g", "ugSig"), "gr": ("g-r", "grSig"), "ri": ("r-i", "riSig"), "iz": ("i-z", "izSig"), "zy": ("z-y", "zySig")}
# (x colour, y colour, x range, y range): x is the bluer colour, y the next redder one.
PANELS = (("ug", "gr", (-1.0, 4.0), (-0.75, 2.25)),
          ("gr", "ri", (-0.75, 2.25), (-0.75, 2.75)),
          ("ri", "iz", (-0.75, 2.75), (-0.75, 1.75)),
          ("iz", "zy", (-0.75, 1.75), (-0.75, 1.25)))
LABEL = {c: f"$({c[0]}-{c[1]})_\\mathrm{{tmpl}}$" for c in COLOURS}
DUSTY_FIELDS = ("Galactic bulge", "Galactic plane")
INK, INK_MUTED, GRID, BLUE = "#0b0b0b", "#52514e", "#e4e3df", "#2a78d6"
LOCUS_COLOR, LOCUS_ALPHA = "firebrick", 0.45


def load_data(path):
    d = pd.read_csv(path)
    for c in COLOURS:
        d[c] = d[f"mag_{c[0]}"] - d[f"mag_{c[1]}"]
    d["gi"] = d.mag_g - d.mag_i
    # Usable-band flags for each panel set; e.g. the 661 "g-i > 2" objects of the earlier figures are has_gri and gi > 2.
    for bands in ("ugr", "gri", "riz", "izy"):
        d[f"has_{bands}"] = d[[f"mag_{b}" for b in bands]].notna().all(axis=1)
    return d


def load_locus(path):
    """Read the stellar-locus table.

    The COSMOS locus file starts with '#' comment lines and its column header is itself a comment line
    ('#  g-i   Npts   u-g   ugSig ...'), so the example's whitespace read_csv with header=0 fails on it. We take the column
    names from the last comment line that lists 'u-g' and read the numeric rows; a file with a plain header line is
    read as in the example code.
    """
    header = None
    with open(path) as fh:
        for line in fh:
            t = line.strip()
            if not t:
                continue
            if not t.startswith("#"):
                break
            tokens = t.lstrip("#").split()
            if "u-g" in tokens:
                header = tokens
    if header is None:
        sl = pd.read_csv(path, sep=r"\s+", header=0)
    else:
        sl = pd.read_csv(path, sep=r"\s+", comment="#", header=None, names=header)
    missing = [c for pair in LOCUS_COLS.values() for c in pair if c not in sl.columns]
    if missing:
        raise SystemExit(f"stellar-locus file is missing columns {missing}; found {list(sl.columns)}")
    cols = [c for pair in LOCUS_COLS.values() for c in pair]
    sl[cols] = sl[cols].apply(pd.to_numeric, errors="raise")
    if sl[cols].isna().any().any() or len(sl) < 2:
        raise SystemExit("stellar-locus file has missing values or fewer than two rows")
    return sl


def draw_locus(ax, sl, xc, yc):
    """Red band: x = mean locus colour xc, y = mean locus colour yc +/- 2 sigma(yc) (mentor's recipe)."""
    x = sl[LOCUS_COLS[xc][0]].to_numpy()
    y, sig = sl[LOCUS_COLS[yc][0]].to_numpy(), sl[LOCUS_COLS[yc][1]].to_numpy()
    ax.fill_between(x, y + 2 * sig, y - 2 * sig, color=LOCUS_COLOR, alpha=LOCUS_ALPHA, interpolate=True,
                    antialiased=True, linewidth=0, zorder=2)


def draw_background(ax, d, xc, yc, xr, yr):
    """Grey log-density of all objects plus contours enclosing 50/80/95% (3x3-smoothed histogram)."""
    n = len(d)
    if n == 0:
        return
    bins = int(np.clip(np.sqrt(n) / 2, 25, 60))
    h, xe, ye = np.histogram2d(d[xc], d[yc], bins=bins, range=(xr, yr))
    ax.pcolormesh(xe, ye, np.log10(h.T + 1), cmap="Greys", alpha=0.45, shading="flat", rasterized=True, zorder=1)
    p = np.pad(h, 1)
    hs = sum(p[i:i + h.shape[0], j:j + h.shape[1]] for i in range(3) for j in range(3)) / 9.0
    v = np.sort(hs.ravel())[::-1]
    if v.sum() == 0:
        return
    cum = np.cumsum(v) / v.sum()
    levels = sorted({v[min(np.searchsorted(cum, f), len(v) - 1)] for f in (0.5, 0.8, 0.95)})
    levels = [lv for lv in levels if lv > 0]
    if levels:
        ax.contour(0.5 * (xe[1:] + xe[:-1]), 0.5 * (ye[1:] + ye[:-1]), hs.T, levels=levels, colors=INK_MUTED,
                   linewidths=0.7, zorder=3)


def panel(ax, base, sub, sl, xc, yc, xr, yr, show_xlabel=True):
    b = base.dropna(subset=[xc, yc])
    draw_background(ax, b, xc, yc, xr, yr)
    if sl is not None:
        draw_locus(ax, sl, xc, yc)
    lines = [f"grey: n = {len(b):,}"]
    if sub is not None:
        s = sub.dropna(subset=[xc, yc])
        ins = s[xc].between(*xr) & s[yc].between(*yr)
        ax.scatter(s.loc[ins, xc], s.loc[ins, yc], s=8 if len(s) < 2000 else 4, color=BLUE, edgecolors="white",
                   linewidths=0.2, alpha=0.9, rasterized=True, zorder=4)
        off = int((~ins).sum())
        lines.append(f"blue: n = {len(s):,}" + (f" ({off} off-axis)" if off else ""))
    ax.text(0.03, 0.97, "\n".join(lines), transform=ax.transAxes, va="top", ha="left", fontsize=7.5, color=INK, zorder=5)
    ax.set_xlim(*xr)
    ax.set_ylim(*yr)
    if show_xlabel:
        ax.set_xlabel(LABEL[xc])
    ax.set_ylabel(LABEL[yc])


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0], formatter_class=argparse.RawDescriptionHelpFormatter,
                                epilog=__doc__.split("Examples")[1] if "Examples" in __doc__ else None)
    p.add_argument("--data", required=True, help="fink_dia_template_colours.csv.gz")
    p.add_argument("--locus", help="stellar-locus file (e.g. RubinStellarLocus_COSMOS.txt); omit to draw no locus")
    p.add_argument("--out", default="figures", help="output folder (created if needed)")
    p.add_argument("--name", default=None, help="file name stem; default depends on the options")
    p.add_argument("--select", default=None, help='pandas query for the blue subsample, e.g. "has_gri and gi > 2" or \'simbad_group == "Star"\'')
    p.add_argument("--facet", default=None, help="column to split into rows, e.g. simbad_group, fink_cats_label, field")
    p.add_argument("--include-dusty", action="store_true", help="also include Galactic bulge and plane fields (no extinction correction)")
    a = p.parse_args(argv)

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.size": 9, "axes.edgecolor": INK_MUTED, "axes.labelcolor": INK, "xtick.color": INK_MUTED,
                         "ytick.color": INK_MUTED, "figure.dpi": 150, "axes.grid": True, "grid.color": GRID,
                         "grid.linewidth": 0.5, "axes.axisbelow": True, "savefig.bbox": "tight"})

    d = load_data(a.data)
    if not a.include_dusty:
        d = d[~d.field.isin(DUSTY_FIELDS)]
    sl = load_locus(a.locus) if a.locus else None
    sub = d.query(a.select) if a.select else None
    scope = "all fields" if a.include_dusty else "outside Galactic bulge and plane fields"
    locus_txt = f"red band: stellar locus ({Path(a.locus).name}) mean ± 2σ" if a.locus else "no stellar locus drawn"
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    if a.facet:
        base = d if sub is None else sub
        groups = [g for g in base[a.facet].dropna().value_counts().index]
        fig, axes = plt.subplots(len(groups), 4, figsize=(17, 3.4 * len(groups)), constrained_layout=True, squeeze=False)
        for i, g in enumerate(groups):
            for j, (xc, yc, xr, yr) in enumerate(PANELS):
                panel(axes[i, j], base, base[base[a.facet] == g], sl, xc, yc, xr, yr, i == len(groups) - 1)
            axes[i, 0].set_title(f"{a.facet} = {g}", loc="left", fontsize=9, fontweight="bold")
        what = f"grey: {'objects with ' + a.select if a.select else 'all DIA objects'}; blue: each {a.facet}"
        name = a.name or f"colour_colour_by_{a.facet}" + ("_selected" if a.select else "")
    else:
        fig, axes = plt.subplots(1, 4, figsize=(17, 4), constrained_layout=True)
        for ax, (xc, yc, xr, yr) in zip(axes, PANELS):
            panel(ax, d, sub, sl, xc, yc, xr, yr)
        what = "grey: all DIA objects" + (f"; blue: {a.select}" if a.select else "")
        name = a.name or ("colour_colour_overview" if not a.select else "colour_colour_selected")
    fig.suptitle(f"Fink LSST DIA objects, template PSF colours ({scope}). {what}; {locus_txt}.", fontsize=9.5)
    for ext in ("png", "pdf"):
        fig.savefig(out / f"{name}.{ext}")
    print(f"wrote {out / (name + '.png')} and .pdf")


if __name__ == "__main__":
    main()

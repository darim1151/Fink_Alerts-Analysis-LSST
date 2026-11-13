#!/usr/bin/env python
"""Publication versions of the paper figures, collected into one PDF.

Journal style throughout: figures drawn at final print width (7.2 in, two-column page), no titles or headings on the
figure, panel letters (a), (b), ... inside the axes, legends inside or directly under the panels, and the full caption
printed under each figure. Every count quoted in a caption is computed from the data when the figure is drawn.

Figures (names follow the paper notes):
  S1  all alerts on the sky, equatorial Aitoff projection (DIA and SSO)
  M1  schematic of the three alert fluxes (templateFlux, scienceFlux, psfFlux)
  P1  alerts per band, alerts per object, difference-flux and template magnitudes
  P2  Galactic latitude of DIA objects and SSO alerts
  P3  template magnitudes with and without a SIMBAD counterpart; SIMBAD and Gaia match fractions
  T1  transient (difference-flux) colours over template colours; time span
  T2  transient colours by Fink CATS label
  T3  transient colours by SIMBAD group
  C1  template colours split by Legacy DR8 pstar
  R3  the g - i > 2 objects by Gaia parallax and Legacy DR8 pstar

Inputs are the tables of a complete notebook run (01, 02, 06) plus the analytical catalog (read-only) for the
alert-level counts. Usage on the data host:

    python paper_figures_pub.py --run-dir <notebook run> --catalog <analytics.duckdb> --data-root /astro/store/shire/FINK \
        --locus <RubinStellarLocus_COSMOS.txt> --out <new folder>
"""
from __future__ import annotations

import argparse
import json
import sys
import textwrap
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "colour_plot_kit"))
import alert_colour_diagrams as acd  # noqa: E402
import alert_science_suite as suite  # noqa: E402

AB_ZP = 31.4
BANDS = ("u", "g", "r", "i", "z", "y")
DUSTY = ("Galactic bulge", "Galactic plane")
TYPED = ("Star", "QSO / AGN", "Galaxy", "Other SIMBAD type")
WIDTH = 7.2  # inches: full text width of a two-column journal page
BLUE, ORANGE, GREY, RED, INK = "#2a78d6", "#e8692f", "#7d7b76", "#d0413b", "#111111"
BAND_COLOR = suite.BAND_COLOR
T_RANGES = ((-1.25, 2.25), (-1.25, 2.75))
CATS_SHORT = {11: "SN-like", 12: "Fast", 13: "Long", 21: "Periodic", 22: "non-Periodic (AGN)"}
STYLE = {
    "font.family": "DejaVu Sans", "font.size": 9, "axes.labelsize": 10, "xtick.labelsize": 8.5, "ytick.labelsize": 8.5,
    "legend.fontsize": 8, "axes.linewidth": 0.8, "xtick.major.width": 0.8, "ytick.major.width": 0.8,
    "xtick.minor.width": 0.6, "ytick.minor.width": 0.6, "xtick.major.size": 3.5, "ytick.major.size": 3.5,
    "xtick.minor.size": 2, "ytick.minor.size": 2, "xtick.direction": "in", "ytick.direction": "in", "xtick.top": True,
    "ytick.right": True, "axes.grid": False, "axes.edgecolor": INK, "axes.labelcolor": INK, "xtick.color": INK,
    "ytick.color": INK, "text.color": INK, "mathtext.default": "regular", "savefig.dpi": 300, "pdf.fonttype": 42,
    "legend.frameon": True, "legend.framealpha": 0.92, "legend.edgecolor": "#c8c8c8", "legend.borderpad": 0.4,
    "legend.handlelength": 1.5, "lines.linewidth": 1.2, "hatch.linewidth": 0.6,
}
CAPTION_PT = 8.6


def _plt():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update(matplotlib.rcParamsDefault)
    plt.rcParams.update(STYLE)
    return plt


def _label(ax, letter, line1=None, line2=None, x=0.035, y=0.955):
    """Panel letter in bold, followed by an optional label; an optional second line (counts) underneath."""
    t = ax.text(x, y, letter, transform=ax.transAxes, va="top", ha="left", fontsize=9.5, fontweight="bold", zorder=7)
    if line1:
        ax.annotate(" " + line1, xycoords=t, xy=(1, 0), va="bottom", ha="left", fontsize=9, zorder=7)
    if line2:
        ax.text(x, y - 0.085, line2, transform=ax.transAxes, va="top", ha="left", fontsize=8.5, zorder=7)


def _finish(fig, caption, out, name, pdf_pages, legend=None):
    """Optional legend row under the panels, then the caption under everything; save PNG + PDF and add to the book."""
    fig.canvas.draw()
    r = fig.canvas.get_renderer()
    axes = [a for a in fig.axes if a.get_visible() and a.axison]
    boxes = [a.get_tightbbox(r).transformed(fig.transFigure.inverted()) for a in axes]
    x0 = min(a.get_position().x0 for a in axes)
    x1 = max(a.get_position().x1 for a in axes)
    y = min(b.y0 for b in boxes)
    H = fig.get_figheight()
    if legend:
        handles, ncol = legend
        lg = fig.legend(handles=handles, loc="upper center", bbox_to_anchor=((x0 + x1) / 2, y - 0.06 / H), ncol=ncol,
                        frameon=False, fontsize=8.5, handlelength=1.6, columnspacing=1.6)
        fig.canvas.draw()
        y = lg.get_window_extent(r).transformed(fig.transFigure.inverted()).y0
    width_in = (x1 - x0) * fig.get_figwidth()
    chars = int(width_in * 72 / (0.49 * CAPTION_PT))
    for keep in ("psfFlux > 0", "S/N ≥ 5", "± 2σ", "pstar ≥ 0.5", "pstar < 0.5", "(a)", "(b)", "(c)",
                 "(d)", "(e)", "(f)", "(g)", "(h)", "(i)", "(j)", "(k)", "(l)", "S/N > 5", "g − i > 2", "m = 31.4"):
        caption = caption.replace(keep, keep.replace(" ", " "))
    text = "\n".join(textwrap.wrap(caption, chars))
    fig.text(x0, y - 0.14 / H, text, ha="left", va="top", fontsize=CAPTION_PT, linespacing=1.4)
    out = Path(out)
    for ext in ("png", "pdf"):
        fig.savefig(out / f"{name}.{ext}", bbox_inches="tight", pad_inches=0.08)
    pdf_pages.savefig(fig, bbox_inches="tight", pad_inches=0.25)
    import matplotlib.pyplot as plt
    plt.close(fig)
    return out / f"{name}.png"


def _fmt(n):
    return f"{int(n):,}"


# ============================================================================= S1 Aitoff sky map
FIELD_TOKENS = (("COSMOS", ("ddf_cosmos",)), ("EDFS", ("ddf_edfs_a", "ddf_edfs_b")), ("ECDFS", ("ddf_ecdfs",)),
                ("ELAIS-S1", ("ddf_elaiss1",)), ("M49", ("field_m49",)), ("Galactic bulge", ("bulgy",)),
                ("North Ecliptic Spur", ("nes",)))
# Label offsets in points (dx, dy) chosen so labels do not overlap each other or the dense regions.
FIELD_OFFSETS = {"COSMOS": (-6, 16), "EDFS": (-34, -18), "ECDFS": (8, 14), "ELAIS-S1": (14, -16), "M49": (-30, 14),
                 "Galactic bulge": (-46, -30), "North Ecliptic Spur": (10, 14)}


def fig_s1(sky, out, book):
    """All delivered alerts on the sky, equatorial Aitoff projection (RA increasing to the left, RA = 0 at centre)."""
    import sky_projection as sp
    import matplotlib.patheffects as pe
    from matplotlib.colors import LogNorm
    from matplotlib.lines import Line2D
    plt = _plt()
    fig = plt.figure(figsize=(WIDTH, 7.6))
    gs = fig.add_gridspec(2, 2, width_ratios=[1, 0.025], hspace=0.22, wspace=0.04)
    plane = sp.break_at_wrap(sp.ra_to_x(sky["plane"][0]), sp.dec_to_y(sky["plane"][1]))
    eclip = sp.break_at_wrap(sp.ra_to_x(sky["ecliptic"][0]), sp.dec_to_y(sky["ecliptic"][1]))
    ra_ticks = np.array([150, 120, 90, 60, 30, 0, 330, 300, 270, 240, 210])
    for row, (pop, let, name) in enumerate((("DIA", "(a)", "DIA alerts"), ("SSO", "(b)", "Solar-system (SSO) alerts"))):
        ax = fig.add_subplot(gs[row, 0], projection="aitoff")
        cax = fig.add_subplot(gs[row, 1])
        xe, ye, m = sp.to_map_order(sp.density_grid(sky["grid"][pop]))
        mk = np.ma.masked_where(m <= 0, m)
        pos = m[m > 0]
        mesh = ax.pcolormesh(xe, ye, mk, cmap="viridis", norm=LogNorm(vmin=max(pos.min(), 0.1), vmax=pos.max()),
                             shading="flat", rasterized=True, zorder=1)
        ax.plot(*plane, color="#333333", lw=1.0, zorder=3)
        ax.plot(*eclip, color="#333333", lw=1.0, ls=(0, (4, 2.5)), zorder=3)
        order = np.argsort(sp.ra_to_x(ra_ticks))
        ax.set_xticks(sp.ra_to_x(ra_ticks)[order])
        ax.set_xticklabels([])
        # RA labels along Dec = +45 deg, where there are no alerts, instead of on the equator
        for r in ra_ticks[order]:
            ax.text(sp.ra_to_x(r), np.radians(45.0), f"{r}°", fontsize=7.5, ha="center", va="center", zorder=5,
                    path_effects=[pe.withStroke(linewidth=2.2, foreground="white")])
        ax.set_yticks(np.radians([-60, -30, 0, 30, 60]))
        ax.set_yticklabels(["−60°", "−30°", "0°", "+30°", "+60°"], fontsize=8)
        ax.grid(True, color="#9a9a9a", lw=0.4, alpha=0.8)
        ax.tick_params(top=False, right=False)
        ax.set_ylabel("Declination", labelpad=2)
        ax.set_xlabel("Right ascension", labelpad=4)
        ax.text(0.0, 1.06, let, transform=ax.transAxes, fontsize=9.5, fontweight="bold", va="bottom", ha="left")
        ax.text(0.055, 1.06, f"{name}, N = {_fmt(sky['n'][pop])}", transform=ax.transAxes, fontsize=9, va="bottom", ha="left")
        cb = fig.colorbar(mesh, cax=cax)
        cb.set_label(r"Alerts per deg$^2$", fontsize=9)
        cb.ax.tick_params(labelsize=8, direction="in")
        if pop == "DIA":
            for name_f, (ra, dec) in sky["fields"].items():
                dx, dy = FIELD_OFFSETS[name_f]
                ax.annotate(name_f, xy=(sp.ra_to_x(ra), sp.dec_to_y(dec)), xytext=(dx, dy), textcoords="offset points",
                            fontsize=7.5, ha="center", va="center", zorder=6,
                            path_effects=[pe.withStroke(linewidth=2.4, foreground="white")],
                            arrowprops=dict(arrowstyle="-", color="#222222", lw=0.6, shrinkA=0, shrinkB=1.5))
    handles = [Line2D([], [], color="#333333", lw=1.0, label="Galactic plane (b = 0°)"),
               Line2D([], [], color="#333333", lw=1.0, ls=(0, (4, 2.5)), label="Ecliptic")]
    cap = (f"Figure S1. Sky distribution of the {_fmt(sky['n']['DIA'] + sky['n']['SSO'])} alerts delivered by Fink for the "
           f"UTC dates {sky['window'][0]} to {sky['window'][1]}, in equatorial coordinates (ICRS) and an Aitoff projection. "
           "Right ascension increases to the left, with RA = 0° at the centre; grid lines are every 30° in RA and Dec, "
           "and RA values are written along Dec = +45°. "
           "Colour is the number of alerts per deg² in cells of 1° × 1° in RA and Dec, divided by each cell's solid angle, "
           "on a logarithmic scale; cells without alerts are white. (a) "
           f"{_fmt(sky['n']['DIA'])} DIA alerts (from {_fmt(sky['n_dia_objects'])} DIA objects); (b) "
           f"{_fmt(sky['n']['SSO'])} solar-system alerts. The solid curve is the Galactic plane and the dashed curve the "
           "ecliptic. Labels in (a) mark the deep-drilling fields (COSMOS, EDFS, ECDFS, ELAIS-S1), the M49 field and the "
           "Galactic bulge and North Ecliptic Spur programmes, each at the mean position of the alerts from visits with "
           "that target name. Alert positions are those reported in each alert.")
    return _finish(fig, cap, out, "S1_sky_aitoff_pub", book, legend=(handles, 2)), {}


def load_sky(catalog, data_root, raw_files, step=1.0):
    """Counts per 1 deg cell for DIA and SSO alerts, field label positions from target names, and the checks that
    guard the figure. Every check raises if it fails."""
    import sky_projection as sp
    import duckdb
    conn = acd.open_catalog(catalog, data_root)
    qc = conn.execute("SELECT sum(total_rows), sum(dia_rows), sum(sso_rows), sum(ambiguous_rows) FROM source_qc").fetchone()
    bad = conn.execute("""SELECT count(*) FROM sources WHERE ra_deg IS NULL OR dec_deg IS NULL OR NOT isfinite(ra_deg)
                          OR NOT isfinite(dec_deg) OR ra_deg < 0 OR ra_deg >= 360 OR dec_deg < -90 OR dec_deg > 90""").fetchone()[0]
    assert bad == 0, f"{bad} alerts with missing or out-of-range coordinates"
    cells = conn.execute("""SELECT population, floor(ra_deg)::INT AS i_ra, least(floor(dec_deg + 90)::INT, 179) AS i_dec,
                                   count(*) AS n
                            FROM sources GROUP BY 1, 2, 3""").df()
    box = dict(conn.execute("""SELECT population, count(*) FROM sources WHERE ra_deg >= 148 AND ra_deg < 152
                                AND dec_deg >= 0 AND dec_deg < 4 GROUP BY 1""").fetchall())
    n_obj = conn.execute("SELECT count(DISTINCT dia_object_id) FROM sources WHERE population = 'DIA'").fetchone()[0]
    window = acd.cohort_window(conn)
    conn.close()
    assert set(cells.population) <= {"DIA", "SSO"}, set(cells.population)
    grid = {}
    for pop in ("DIA", "SSO"):
        c = cells[cells.population == pop]
        i_ra, i_dec = sp.cell_index(c.i_ra.to_numpy() + 0.5, c.i_dec.to_numpy() - 89.5, step)
        assert (i_ra == c.i_ra.to_numpy()).all() and (i_dec == c.i_dec.to_numpy()).all()
        grid[pop] = sp.count_grid(i_ra, i_dec, c.n.to_numpy(), step)
    n = {"DIA": int(grid["DIA"].sum()), "SSO": int(grid["SSO"].sum())}
    # Checkpoints: the map holds every alert, split exactly as in the catalog's own QC totals.
    assert n["DIA"] == qc[1] and n["SSO"] == qc[2] and qc[3] == 0 and n["DIA"] + n["SSO"] == qc[0], (n, qc)
    # Independent check of the binning: a 4 x 4 deg box counted directly from coordinates equals the sum of its cells.
    for pop in ("DIA", "SSO"):
        assert int(grid[pop][90:94, 148:152].sum()) == int(box.get(pop, 0)), (pop, grid[pop][90:94, 148:152].sum(), box)
    # Field label positions: mean direction of the alerts whose visit target name carries the token.
    mem = duckdb.connect()
    mem.execute("SET threads=16")
    fields = {}
    for name, tokens in FIELD_TOKENS:
        cond = " OR ".join(f"list_contains(string_split(replace(target_name, ' ', ''), ','), '{tok}')" for tok in tokens)
        r = mem.execute(f"""SELECT count(*), avg(cos(radians(dec))*cos(radians(ra))), avg(cos(radians(dec))*sin(radians(ra))),
                                   avg(sin(radians(dec)))
                            FROM read_parquet(?) WHERE {cond}""", [raw_files]).fetchone()
        assert r[0] > 0, name
        ra = float(np.mod(np.degrees(np.arctan2(r[2], r[1])), 360.0))
        dec = float(np.degrees(np.arctan2(r[3], np.hypot(r[1], r[2]))))
        i_ra, i_dec = sp.cell_index([ra], [dec], step)
        near = grid["DIA"][max(i_dec[0] - 2, 0):i_dec[0] + 3, [(i_ra[0] + k) % 360 for k in range(-2, 3)]].sum()
        assert near > 0, f"no DIA alerts within 2 deg of the {name} label"
        fields[name] = (ra, dec)
    mem.close()
    plane = sp.galactic_plane_radec()
    eclip = sp.ecliptic_radec()
    checks = {"total_alerts": int(qc[0]), "dia": n["DIA"], "sso": n["SSO"], "bad_coordinates": int(bad),
              "box_148_152_0_4": {k: int(v) for k, v in box.items()}, "field_positions": fields}
    return {"grid": grid, "n": n, "n_dia_objects": int(n_obj), "fields": fields, "plane": plane, "ecliptic": eclip,
            "window": window, "checks": checks}


# ============================================================================= M1 schematic
def fig_m1(out, book):
    plt = _plt()
    fig, ax = plt.subplots(figsize=(WIDTH, 3.4))
    tl, v1, v2 = 0.45, 0.85, 0.22
    ax.axvspan(0.0, 0.32, color="#f3ece4", zorder=0)
    ax.axhline(tl, color="#b8862f", ls="--", lw=1.3, zorder=1)
    ax.text(0.995, tl + 0.02, "template level", color="#8a5f17", ha="right", va="bottom", fontsize=9)
    ax.annotate("", xy=(0.04, tl), xytext=(0.04, 0.0), arrowprops=dict(arrowstyle="-|>", color="#b8862f", lw=1.8, mutation_scale=14))
    ax.text(0.065, 0.24, "templateFlux", color="#8a5f17", fontsize=9.5, fontweight="bold", va="center")
    ax.text(0.065, 0.06, "template epochs (coadd)", color="#8a5f17", fontsize=8.5, ha="left", va="bottom")
    for x, y, lab in ((0.55, v1, "visit 1"), (0.74, v2, "visit 2")):
        ax.plot([x], [y], "o", color=BLUE, ms=9, zorder=4)
        ax.text(x + 0.022, y, lab, color=BLUE, fontsize=9, va="center", fontweight="bold")
    ax.annotate("", xy=(0.55, v1 - 0.035), xytext=(0.55, tl), arrowprops=dict(arrowstyle="-|>", color="#2f8f5b", lw=1.8, mutation_scale=14))
    ax.text(0.565, (tl + v1) / 2 + 0.03, "psfFlux > 0", color="#2f8f5b", fontsize=9.5, fontweight="bold", va="center")
    ax.text(0.565, (tl + v1) / 2 - 0.045, "brighter than the template", color="#2f8f5b", fontsize=8.5, va="center")
    ax.annotate("", xy=(0.74, v2 + 0.035), xytext=(0.74, tl), arrowprops=dict(arrowstyle="-|>", color="#c0392b", lw=1.8, mutation_scale=14))
    ax.text(0.755, (tl + v2) / 2 + 0.03, "psfFlux < 0", color="#c0392b", fontsize=9.5, fontweight="bold", va="center")
    ax.text(0.755, (tl + v2) / 2 - 0.045, "fainter than the template", color="#c0392b", fontsize=8.5, va="center")
    ax.text(0.55, v1 + 0.07, "scienceFlux: level at the visit", color=GREY, fontsize=8.5, ha="center")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.tick_params(top=False, right=False)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.set_xlabel("time →")
    ax.set_ylabel("flux at the object's position →")
    cap = ("Figure M1. Schematic of the three fluxes reported in each alert (all in nJy). templateFlux is the forced PSF "
           "flux on the template, a coadd of earlier images, at the DiaObject position: the static-sky level. At a later "
           "visit, scienceFlux is the forced PSF flux on the visit image, and psfFlux is the PSF flux on the difference "
           "image (visit minus template): positive when the source is brighter than in the template (visit 1) and "
           "negative when it is fainter (visit 2). Template colours are built from templateFlux in two bands; transient "
           "colours from positive psfFlux measured on the same night. The three fluxes are separate measurements, so "
           "psfFlux need not equal scienceFlux − templateFlux exactly. Magnitudes are m = 31.4 − 2.5 log10(f/nJy) (AB).")
    return _finish(fig, cap, out, "M1_alert_fluxes_schematic", book), {}


# ============================================================================= P1
def fig_p1(bands, det_hist, dmag_hist, n_pos, tp, out, book):
    plt = _plt()
    fig, axes = plt.subplots(2, 2, figsize=(WIDTH, 5.6), gridspec_kw={"hspace": 0.32, "wspace": 0.3})
    a, b, c, d = axes.ravel()
    x = np.arange(len(BANDS))
    nd = [bands.get(("DIA", k), 0) for k in BANDS]
    ns = [bands.get(("SSO", k), 0) for k in BANDS]
    a.bar(x - 0.2, nd, 0.38, color=BLUE, label="DIA alerts")
    a.bar(x + 0.2, ns, 0.38, color=ORANGE, label="SSO alerts")
    a.set_yscale("log")
    a.set_xticks(x)
    a.set_xticklabels(BANDS)
    a.tick_params(axis="x", which="minor", bottom=False, top=False)
    a.set_xlabel("Band")
    a.set_ylabel("Number of alerts")
    a.set_ylim(1e3, 1e8)
    a.legend(loc="upper right", ncol=2, fontsize=8, columnspacing=1.0)
    _label(a, "(a)")
    k = det_hist.index.to_numpy()
    b.bar(k, det_hist.to_numpy(), width=1.0, color=BLUE, edgecolor="none")
    b.set_yscale("log")
    b.set_xlim(0, 60.5)
    b.set_xlabel("Alerts per DIA object")
    b.set_ylabel("Number of objects")
    _label(b, "(b)", x=0.88)
    edges = np.arange(12.0, 27.01, 0.2)
    for band in BANDS:
        h = dmag_hist.get(band)
        if h is not None and h.sum():
            c.stairs(h, edges, color=BAND_COLOR[band], lw=1.2, label=band)
        t = tp.loc[tp[f"ok_{band}"], f"mag_{band}"]
        if len(t):
            hh, _ = np.histogram(t, edges)
            d.stairs(hh, edges, color=BAND_COLOR[band], lw=1.2, label=band)
    for ax, lab, let in ((c, "Difference-flux magnitude (AB)", "(c)"), (d, "Template PSF magnitude (AB)", "(d)")):
        ax.set_yscale("log")
        ax.set_xlim(12, 27)
        ax.set_ylim(0.6, 3e7)
        ax.set_xticks(np.arange(14, 27, 2))
        ax.set_xlabel(lab)
        ax.set_ylabel("Number per 0.2 mag")
        ax.legend(loc="upper right", ncol=3, fontsize=8, handlelength=1.2, columnspacing=0.8)
        _label(ax, let)
    n_out = n_pos - int(sum(h.sum() for h in dmag_hist.values()))
    n_obj = len(tp)
    n_gt60 = int((tp.n_sources > 60).sum())
    cap = (f"Figure P1. The delivered sample. (a) Alerts per band: {_fmt(sum(nd))} DIA alerts (blue) and {_fmt(sum(ns))} "
           f"solar-system alerts (orange). (b) Delivered alerts per DIA object for all {_fmt(n_obj)} DIA objects; "
           f"{_fmt(n_gt60)} objects with more than 60 alerts lie beyond the plotted range. (c) Magnitudes of the difference "
           f"fluxes of the {_fmt(n_pos)} DIA alerts with psfFlux > 0, per band ({_fmt(n_out)} lie outside 12\u201327 mag). (d) Template PSF magnitudes of DIA objects "
           f"in each band where the template is usable: the median templateFlux over the object's alerts in that band, "
           f"positive and with median S/N ≥ 5. Usable objects per band: "
           + ", ".join(f"{bd} {_fmt(tp[f'ok_{bd}'].sum())}" for bd in BANDS) + ".")
    return _finish(fig, cap, out, "P1_sample_pub", book), {"dia_alerts": sum(nd), "sso_alerts": sum(ns), "pos_alerts": n_pos}


# ============================================================================= P2
def fig_p2(tp, sso_b, out, book):
    plt = _plt()
    fig, ax = plt.subplots(figsize=(WIDTH, 3.0))
    bins = np.arange(-90, 90.01, 2)
    h1, _ = np.histogram(tp.gal_b_deg, bins)
    h2, _ = np.histogram(sso_b, bins)
    ax.stairs(h1, bins, color=BLUE, lw=1.4, label=f"DIA objects ({_fmt(len(tp))})")
    ax.stairs(h2, bins, color=ORANGE, lw=1.4, label=f"SSO alerts ({_fmt(len(sso_b))})")
    ax.set_yscale("log")
    ax.set_xlim(-90, 90)
    ax.set_ylim(0.8, 3e7)
    ax.set_xticks(np.arange(-90, 91, 30))
    ax.set_xlabel("Galactic latitude b (deg)")
    ax.set_ylabel("Number per 2 deg")
    ax.legend(loc="upper right")
    cap = (f"Figure P2. Galactic latitude of the {_fmt(len(tp))} DIA objects (blue; mean position of their delivered "
           f"alerts) and of the {_fmt(len(sso_b))} solar-system alerts (orange), in 2° bins. "
           f"{100 * (tp.gal_b_deg.abs() < 10).mean():.1f}% of the DIA objects lie at |b| < 10° and "
           f"{100 * (tp.gal_b_deg.abs() >= 30).mean():.1f}% at |b| ≥ 30°.")
    return _finish(fig, cap, out, "P2_galactic_latitude_pub", book), {}


# ============================================================================= P3
def _frac(ax, mag, flag, bins, color, label, min_n=20):
    k, _ = np.histogram(mag[flag], bins)
    n, _ = np.histogram(mag, bins)
    c = 0.5 * (bins[1:] + bins[:-1])
    good = n >= min_n
    lo, hi = np.array([suite.wilson(kk, nn) for kk, nn in zip(k, n)]).T
    ax.fill_between(c[good], lo[good], hi[good], color=color, alpha=0.18, lw=0)
    ax.plot(c[good], (k / np.maximum(n, 1))[good], "-o", color=color, ms=2.6, lw=1.2, label=label)


def fig_p3(tp, sci, out, book):
    plt = _plt()
    from matplotlib.lines import Line2D
    d = tp.merge(sci[["dia_object_id", "gaia_name"]], on="dia_object_id", how="left", validate="one_to_one")
    typed = d.simbad_group.isin(TYPED)
    nom = d.simbad_group == acd.NO_MATCH
    fail = d.simbad_group == acd.XM_FAILED
    dusty = d.field.isin(DUSTY)
    groups = [("SIMBAD type", typed, BLUE), ("No SIMBAD object within 1″", nom, GREY), ("Crossmatch failed", fail, ORANGE)]
    scopes = [("all fields", pd.Series(True, index=d.index)), ("outside bulge and plane", ~dusty), ("bulge and plane fields", dusty)]
    fig = plt.figure(figsize=(WIDTH, 8.4))
    gs = fig.add_gridspec(3, 2, height_ratios=[1, 1, 1.08], hspace=0.42, wspace=0.28)
    ha, hb, hc, hd = (fig.add_subplot(gs[0, 0]), fig.add_subplot(gs[0, 1]), fig.add_subplot(gs[1, 0]), fig.add_subplot(gs[1, 1]))
    edges = np.arange(13, 25.01, 0.25)
    med = {}
    for ax, band, (sname, sm), let, lab in ((ha, "i", scopes[0], "(a)", "i band, all fields"),
                                            (hb, "i", scopes[1], "(b)", "i band, outside bulge and plane"),
                                            (hc, "i", scopes[2], "(c)", "i band, bulge and plane fields"),
                                            (hd, "r", scopes[0], "(d)", "r band, all fields")):
        for gname, m, col in groups:
            v = d.loc[sm & m & d[f"ok_{band}"], f"mag_{band}"]
            med[(sname, gname, band)] = (len(v), float(v.median()) if len(v) else np.nan)
            if len(v):
                h, _ = np.histogram(v, edges)
                ax.stairs(h / h.sum() / 0.25, edges, color=col, lw=1.2)
        ax.set_xlim(13, 25)
        ax.set_ylim(0, None)
        ax.set_xlabel(rf"${band}_{{\rm tmpl}}$ (AB)")
        ax.set_ylabel("Normalised density")
        _label(ax, let, lab)
        top = ax.get_ylim()[1]
        ax.set_ylim(0, top * (2.0 if ax is ha else 1.6))
    handles = [Line2D([], [], color=c, lw=1.6, label=g) for g, _, c in groups]
    ha.legend(handles=handles, loc="upper right", bbox_to_anchor=(1.0, 0.86), fontsize=7.5, handlelength=1.4)
    fb = np.arange(14, 25.01, 0.5)
    e = fig.add_subplot(gs[2, 0])
    ans = d.ok_i & ~fail
    for (sname, sm), col, short in zip(scopes, (INK, BLUE, ORANGE), ("all fields", "outside bulge/plane", "bulge/plane")):
        s = ans & sm
        _frac(e, d.mag_i[s], typed[s], fb, col, short)
    e.set_ylim(0, 0.34)
    e.set_xlim(14, 25)
    e.set_xlabel(r"$i_{\rm tmpl}$ (AB)")
    e.set_ylabel("Fraction with a SIMBAD type")
    e.legend(loc="upper right", fontsize=7.5)
    _label(e, "(e)")
    f = fig.add_subplot(gs[2, 1])
    gaia = d.gaia_name.notna()
    for (gname, m, col), short in zip(groups[:2], ("SIMBAD type", "No SIMBAD match")):
        s = d.ok_i & m
        _frac(f, d.mag_i[s], gaia[s], fb, col, short)
    f.set_ylim(0, 1.18)
    f.set_xlim(14, 25)
    f.set_xlabel(r"$i_{\rm tmpl}$ (AB)")
    f.set_ylabel("Fraction with a Gaia DR3 source")
    f.legend(loc="center left", fontsize=7.5)
    _label(f, "(f)")
    for ax in fig.axes:
        ax.minorticks_on()
    def m(s, g, b="i"):
        n, v = med[(s, g, b)]
        return f"{v:.2f} ({_fmt(n)})"
    cap = ("Figure P3. Template magnitudes of DIA objects with a SIMBAD type within 1″ (blue), with no SIMBAD object "
           "within 1″ (grey), and with a SIMBAD crossmatch that failed on every alert (orange), each histogram normalised "
           "to unit area and using the objects with a usable template in that band. (a) i band, all fields; (b) outside the "
           "Galactic bulge and plane fields; (c) bulge and plane fields; (d) r band, all fields. Median i_tmpl (number of "
           f"objects) for typed, unmatched and failed objects: all fields {m('all fields', 'SIMBAD type')}, "
           f"{m('all fields', groups[1][0])}, {m('all fields', 'Crossmatch failed')}; outside bulge and plane "
           f"{m('outside bulge and plane', 'SIMBAD type')}, {m('outside bulge and plane', groups[1][0])}, "
           f"{m('outside bulge and plane', 'Crossmatch failed')}; bulge and plane {m('bulge and plane fields', 'SIMBAD type')}, "
           f"{m('bulge and plane fields', groups[1][0])}, {m('bulge and plane fields', 'Crossmatch failed')}. (e) Fraction "
           f"with a SIMBAD type against i_tmpl for the {_fmt(ans.sum())} objects with a usable i template whose crossmatch "
           "was answered. (f) Fraction with a Gaia DR3 source within 1″ in Fink's crossmatch, for typed and unmatched "
           "objects (a lower limit where Fink's Gaia crossmatch failed). Shaded bands are 95% Wilson intervals; bins with "
           "fewer than 20 objects are not shown.")
    return _finish(fig, cap, out, "P3_simbad_comparison_pub", book), {}


# ============================================================================= T1-T3
def _locus_handles():
    import plot_colour_diagrams as kit
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch
    return (Patch(facecolor="#9a9a9a", edgecolor="none", alpha=0.6, label="density"),
            Line2D([], [], color=acd.INK_MUTED, lw=0.9, label="50, 80, 95% contours"),
            Patch(facecolor=kit.LOCUS_COLOR, edgecolor="none", alpha=kit.LOCUS_ALPHA, label=r"stellar locus $\pm$ 2$\sigma$"))


def _tbase(ax, base, sl):
    import plot_colour_diagrams as kit
    xr, yr = T_RANGES
    acd.background(ax, base, "gr", "ri", xr, yr)
    kit.draw_locus(ax, sl, "gr", "ri")
    ax.set_xlim(*xr)
    ax.set_ylim(*yr)
    ax.set_xticks([-1, 0, 1, 2])
    ax.set_yticks([-1, 0, 1, 2])
    ax.minorticks_on()


def _tpoints(ax, s, size=7):
    xr, yr = T_RANGES
    ins = s.gr.between(*xr) & s.ri.between(*yr)
    ax.scatter(s.loc[ins, "gr"], s.loc[ins, "ri"], s=size, color=BLUE, edgecolors="white", linewidths=0.2, alpha=0.9,
               rasterized=True, zorder=4)
    return int((~ins).sum())


def fig_t1(tc, tp, sl, out, book):
    plt = _plt()
    from matplotlib.lines import Line2D
    t = tc[~tc.field.isin(DUSTY)]
    static = tp[tp.panel_a & ~tp.field.isin(DUSTY)]
    fig, axes = plt.subplots(1, 3, figsize=(WIDTH, 2.55), gridspec_kw={"width_ratios": [1, 1, 1], "wspace": 0.38})
    a, b, c = axes
    _tbase(a, static, sl)
    _tbase(b, static, sl)
    off = _tpoints(b, t, 5)
    a.set_xlabel(r"$(g-r)_{\rm tmpl}$")
    a.set_ylabel(r"$(r-i)_{\rm tmpl}$")
    b.set_xlabel(r"$(g-r)_{\rm diff}$")
    b.set_ylabel(r"$(r-i)_{\rm diff}$")
    _label(a, "(a)", f"N = {_fmt(len(static))}")
    _label(b, "(b)", f"N = {_fmt(len(t))}")
    top = max(6.0, float(np.ceil(t.span_hours.max())))
    c.hist(t.span_hours, bins=np.arange(0, top + 0.01, 0.25), color=BLUE, edgecolor="white", linewidth=0.3)
    c.set_xlim(0, top)
    c.set_ylim(0, None)
    c.set_ylim(0, c.get_ylim()[1] * 1.12)
    c.set_xlabel("Time span of g, r, i alerts (h)")
    c.set_ylabel("Number of objects")
    c.minorticks_on()
    _label(c, "(c)")
    dens, cont, locus = _locus_handles()
    dens.set_label("template colours (density)")
    pts = Line2D([], [], marker="o", ls="none", color=BLUE, markeredgecolor="white", markersize=5,
                 label="transients, same-night difference fluxes")
    cap = ("Figure T1. Colours of the transient light compared with the static sky, for DIA objects outside the Galactic "
           "bulge and plane fields (no extinction correction). (a) Template colours of the "
           f"{_fmt(len(static))} objects with usable g, r and i templates; grey shading is the logarithmic number density "
           "and the contours enclose 50, 80 and 95% of the objects. (b) Colours of the change for "
           f"{_fmt(len(t))} objects (blue), from difference fluxes with psfFlux > 0 and S/N ≥ 5 in g, r and i on the "
           "same night (inverse-variance-weighted nightly means; one night per object, the one whose weakest band has the "
           f"highest S/N), over the template colours of (a) in grey; {off} objects fall outside the plotted range. (c) Time "
           "from the first to the last g, r and i alert used for each object in (b). In (a) and (b) the red band is the "
           "Rubin COSMOS stellar locus ± 2σ.")
    return _finish(fig, cap, out, "T1_transient_colours_pub", book, legend=([dens, cont, locus, pts], 2)), {"off": off}


def _t_facets(tc, sl, col, order, labels, fig_name, cap_head, cap_tail, out, book):
    plt = _plt()
    from matplotlib.lines import Line2D
    t = tc[~tc.field.isin(DUSTY)]
    rows = [g for g in order if (t[col] == g).any()]
    nrow = int(np.ceil(len(rows) / 3))
    fig, axes = plt.subplots(nrow, 3, figsize=(WIDTH, 2.45 * nrow), sharex=True, sharey=True,
                             gridspec_kw={"hspace": 0.08, "wspace": 0.08})
    counts = []
    for k, (ax, g) in enumerate(zip(axes.ravel(), rows)):
        _tbase(ax, t, sl)
        s = t[t[col] == g]
        off = _tpoints(ax, s, 7 if len(s) < 500 else 5)
        counts.append((labels.get(g, g), len(s), off))
        _label(ax, f"({chr(97 + k)})", labels.get(g, g), f"N = {_fmt(len(s))}")
    for ax in axes.ravel()[len(rows):]:
        ax.set_visible(False)
    for ax in axes[:, 0]:
        ax.set_ylabel(r"$(r-i)_{\rm diff}$")
    for j in range(3):
        col_axes = [a for a in axes[:, j] if a.get_visible()]
        col_axes[-1].set_xlabel(r"$(g-r)_{\rm diff}$")
        col_axes[-1].tick_params(labelbottom=True)
    dens, cont, locus = _locus_handles()
    dens.set_label(f"all {_fmt(len(t))} transients (density)")
    pts = Line2D([], [], marker="o", ls="none", color=BLUE, markeredgecolor="white", markersize=5, label="objects in the group")
    parts = "; ".join(f"({chr(97 + k)}) {n} ({_fmt(c)}{', ' + str(o) + ' outside the plotted range' if o else ''})"
                      for k, (n, c, o) in enumerate(counts))
    cap = f"{cap_head} {parts}. {cap_tail}"
    return _finish(fig, cap, out, fig_name, book, legend=([pts, dens, cont, locus], 2)), {"rows": counts}


def fig_t2(tc, sl, out, book):
    order = [acd.CATS_LABELS[k] for k in (11, 12, 13, 21, 22, -1)]
    labels = {acd.CATS_LABELS[k]: v for k, v in CATS_SHORT.items()}
    return _t_facets(tc, sl, "cats_label", order, labels, "T2_transient_colours_by_fink_label_pub",
                     "Figure T2. Same-night difference-flux colours (Figure T1b) split by the Fink CATS classifier label at "
                     "each object's latest delivered alert (blue), over all transients outside the bulge and plane fields "
                     "(grey density and 50, 80, 95% contours). Panels and numbers of objects:",
                     "No object in this sample has the first-alert code −1 at its latest alert. CATS labels are "
                     "classifier outputs, not verified types. The red band is the Rubin COSMOS stellar locus ± 2σ.",
                     out, book)


def fig_t3(tc, sl, out, book):
    order = list(TYPED) + [acd.NO_MATCH, acd.XM_FAILED]
    labels = {acd.NO_MATCH: "No SIMBAD match", acd.XM_FAILED: "Crossmatch failed"}
    return _t_facets(tc, sl, "simbad_group", order, labels, "T3_transient_colours_by_simbad_group_pub",
                     "Figure T3. Same-night difference-flux colours (Figure T1b) split by SIMBAD group from Fink's "
                     "crossmatch within 1″ (the latest type returned by any of the object's alerts; blue), over all "
                     "transients outside the bulge and plane fields (grey density and 50, 80, 95% contours). Panels and "
                     "numbers of objects:",
                     "“Crossmatch failed” means the SIMBAD query failed on every alert, so the status is unknown. "
                     "The red band is the Rubin COSMOS stellar locus ± 2σ.",
                     out, book)


# ============================================================================= C1
def fig_c1(obj, out, book):
    plt = _plt()
    from matplotlib.lines import Line2D
    o = obj[obj.panel_a | obj.panel_b]
    ps = o.pstar
    ext, pt = o[ps < 0.5], o[ps >= 0.5]
    fig, axes = plt.subplots(2, 2, figsize=(WIDTH, 6.0), gridspec_kw={"hspace": 0.28, "wspace": 0.42})
    a = axes[0, 0]
    s = o[o.panel_a & ps.notna()]
    bins = np.arange(15, 25.01, 0.5)
    k, _ = np.histogram(s.mag_r[s.pstar >= 0.5], bins)
    n, _ = np.histogram(s.mag_r, bins)
    cc = 0.5 * (bins[1:] + bins[:-1])
    good = n >= 10
    lo, hi = np.array([suite.wilson(kk, nn) for kk, nn in zip(k, n)]).T
    a2 = a.twinx()
    a2.bar(cc, n, width=0.46, color="#d9d8d4", zorder=0)
    a2.set_ylabel("Objects per 0.5 mag", color=GREY)
    a2.tick_params(axis="y", colors=GREY, direction="in")
    a.set_zorder(a2.get_zorder() + 1)
    a.patch.set_visible(False)
    a.fill_between(cc[good], lo[good], hi[good], color=BLUE, alpha=0.2, lw=0)
    a.plot(cc[good], (k / np.maximum(n, 1))[good], "-o", color=BLUE, ms=3, lw=1.3)
    a.set_ylim(0, 1.08)
    a.set_xlim(15, 25)
    a.set_xlabel(r"$r_{\rm tmpl}$ (AB)")
    a.set_ylabel(r"Fraction with pstar $\geq$ 0.5")
    a.tick_params(right=False)
    a.minorticks_on()
    _label(a, "(a)", None, f"N = {_fmt(len(s))}", x=0.04, y=0.44)
    counts = {}
    for (key, x, y, xl, yl, xr, yr, inv), ax, let in zip(suite.COLOUR_PANELS, (axes[0, 1], axes[1, 0], axes[1, 1]), ("(b)", "(c)", "(d)")):
        e, p = ext[ext[key]], pt[pt[key]]
        suite.contours(ax, e[x], e[y], xr, yr, RED, lw=1.0)
        ins = p[x].between(*xr) & p[y].between(*yr)
        ax.scatter(p.loc[ins, x], p.loc[ins, y], s=5, color=BLUE, alpha=0.8, edgecolors="none", rasterized=True)
        counts[let] = (len(e), len(p))
        ax.set_xlim(*xr)
        ax.set_ylim(*(yr[::-1] if inv else yr))
        ax.set_xlabel(xl)
        ax.set_ylabel(r"$r_{\rm tmpl}$ (AB)" if y == "mag_r" else yl)
        ax.minorticks_on()
        _label(ax, let)
    handles = [Line2D([], [], color=RED, lw=1.2, label="pstar < 0.5 (contours enclose 50, 80, 95%)"),
               Line2D([], [], marker="o", ls="none", color=BLUE, markersize=4, label=r"pstar $\geq$ 0.5")]
    cap = ("Figure C1. Template colours of DIA objects with a Legacy Surveys DR8 counterpart, split by its pstar value: a "
           "star likelihood from the Legacy colours (Duncan 2022), valid only for Legacy PSF-type sources. The alerts carry "
           "no morphology, so this is a split by colour, not by shape. Of the "
           f"{_fmt(ps.notna().sum())} colour-eligible objects with a pstar, {_fmt(len(ext))} have pstar < 0.5 (red contours) "
           f"and {_fmt(len(pt))} have pstar ≥ 0.5 (blue points). (a) Fraction with pstar ≥ 0.5 against r_tmpl for "
           f"the {_fmt(len(s))} objects with usable g, r and i templates and a pstar (blue, with 95% Wilson interval; bins "
           "with at least 10 objects), and the number of these objects per 0.5 mag (grey bars, right axis), in place of the "
           "PSF − model magnitude panel of the SDSS reference figure. (b) r_tmpl against (g − i)_tmpl "
           f"({_fmt(counts['(b)'][0])} and {_fmt(counts['(b)'][1])} objects). (c) (g − r)_tmpl against (u − g)_tmpl "
           f"({_fmt(counts['(c)'][0])} and {_fmt(counts['(c)'][1])}). (d) (r − i)_tmpl against (g − r)_tmpl "
           f"({_fmt(counts['(d)'][0])} and {_fmt(counts['(d)'][1])}). No extinction correction; all fields.")
    return _finish(fig, cap, out, "C1_colours_by_legacy_pstar_pub", book, legend=(handles, 2)), {"counts": counts}


# ============================================================================= R3
def fig_r3(v, order, out, book):
    plt = _plt()
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch
    red = v[v.red]
    rows = [g for g in order if ((red.plx_pstar == g) & (red.panel_a | red.panel_b)).any()]
    short = {order[0]: "Gaia S/N > 5", order[1]: "pstar ≥ 0.5", order[2]: "pstar < 0.5", order[3]: "neither"}
    long = {order[0]: "Gaia DR3 parallax S/N > 5", order[1]: "pstar ≥ 0.5 without such a parallax",
            order[2]: "pstar < 0.5", order[3]: "neither"}
    fig, axes = plt.subplots(len(rows), 3, figsize=(WIDTH, 2.05 * len(rows)), sharex="col", sharey="col",
                             gridspec_kw={"hspace": 0.08, "wspace": 0.5})
    counts = []
    letter = 0
    for i, g in enumerate(rows):
        sub = red[red.plx_pstar == g]
        row = []
        for j, (key, x, y, xl, yl, xr, yr, inv) in enumerate(acd.PANELS):
            ax = axes[i, j]
            base = red[red[key]]
            acd.background(ax, base, x, y, xr, yr)
            s = sub[sub[key]]
            ins = s[x].between(*xr) & s[y].between(*yr)
            ax.scatter(s.loc[ins, x], s.loc[ins, y], s=5, color=BLUE, edgecolors="white", linewidths=0.2, rasterized=True, zorder=4)
            ax.set_xlim(*xr)
            ax.set_ylim(*(yr[::-1] if inv else yr))
            ax.minorticks_on()
            row.append(len(s))
            _label(ax, f"({chr(97 + letter)})", short[g] if j == 0 else None, f"N = {len(s)}", x=0.035)
            letter += 1
            if i == len(rows) - 1:
                ax.set_xlabel(xl)
            if j == 0:
                ax.set_ylabel(r"$r_{\rm tmpl}$ (AB)")
            else:
                ax.set_ylabel(yl)
        counts.append((long[g], int(len(sub)), row))
    handles = [Line2D([], [], marker="o", ls="none", color=BLUE, markeredgecolor="white", markersize=5, label="objects in the row"),
               Patch(facecolor="#9a9a9a", edgecolor="none", alpha=0.6, label=f"all {_fmt(len(red))} objects with g − i > 2 (density)"),
               Line2D([], [], color=acd.INK_MUTED, lw=0.9, label="50, 80, 95% contours")]
    rowtxt = "; ".join(f"{n} ({c})" for n, c, _ in counts)
    cap = (f"Figure R3. The {_fmt(len(red))} DIA objects with (g − i)_tmpl > 2 (among objects with usable g, r and i "
           "templates), split by Gaia DR3 parallax and Legacy DR8 pstar from Fink's crossmatches. Rows are exclusive and "
           f"applied in order: {rowtxt}. Columns: r_tmpl against (g − i)_tmpl; (g − r)_tmpl against "
           "(u − g)_tmpl, which needs a u-band template and so contains few objects, all in the COSMOS deep-drilling "
           "field; (r − i)_tmpl against (g − r)_tmpl. Blue: objects in the row; grey: all objects with "
           "g − i > 2. pstar is a star likelihood from the Legacy colours (Duncan 2022), valid only for Legacy "
           "PSF-type sources, not a morphological measure. No extinction correction.")
    return _finish(fig, cap, out, "R3_red_objects_by_parallax_pstar_pub", book, legend=(handles, 3)), {"rows": counts}


# ============================================================================= data and driver
def raw_files_list(catalog, data_root):
    conn = acd.open_catalog(catalog, data_root)
    files = [r[0] for r in conn.execute("SELECT DISTINCT raw_file FROM sources ORDER BY 1").fetchall()]
    conn.close()
    return files


def load(run_dir, catalog, data_root):
    """Per-object tables from the notebook run, alert-level counts from the catalog (read-only)."""
    import red_objects_and_red_bands as red
    R = Path(run_dir)
    tp = pd.read_parquet(R / "01_template_colours/tables/dia_object_template_photometry.parquet")
    sci = pd.read_parquet(R / "02_science_suite/tables/dia_object_science_table.parquet")
    tc = pd.read_parquet(R / "06_paper_additions/tables/transient_colours_per_object.parquet")
    v = red.prepare(R / "01_template_colours/tables/dia_object_template_photometry.parquet",
                    R / "02_science_suite/tables/dia_object_science_table.parquet", catalog, data_root)
    conn = acd.open_catalog(catalog, data_root)
    bands = {(p, b): int(n) for p, b, n in conn.execute("SELECT population, band, count(*) FROM sources GROUP BY 1, 2").fetchall()}
    dm = conn.execute("""SELECT band, floor((31.4 - 2.5 * log10(psf_flux) - 12.0) / 0.2)::INT AS k, count(*) FROM sources
                         WHERE population = 'DIA' AND psf_flux > 0 GROUP BY 1, 2""").df()
    n_pos = int(conn.execute("SELECT count(*) FROM sources WHERE population = 'DIA' AND psf_flux > 0").fetchone()[0])
    sso = conn.execute("SELECT ra_deg, dec_deg FROM sources WHERE population = 'SSO'").df()
    conn.close()
    nb = int(round((27.0 - 12.0) / 0.2))
    dmag = {}
    for b in BANDS:
        h = np.zeros(nb, dtype=np.int64)
        s = dm[(dm.band == b) & (dm.k >= 0) & (dm.k < nb)]
        h[s.k.to_numpy()] = s.iloc[:, 2].to_numpy()
        dmag[b] = h
    det = tp.n_sources.value_counts().sort_index()
    det = det[det.index <= 60]
    from astropy.coordinates import SkyCoord
    import astropy.units as u
    sso_b = SkyCoord(ra=sso.ra_deg.to_numpy() * u.deg, dec=sso.dec_deg.to_numpy() * u.deg).galactic.b.deg
    sky = load_sky(catalog, data_root, raw_files_list(catalog, data_root))
    return {"sky": sky, "tp": tp, "sci": sci, "tc": tc, "v": v, "bands": bands, "dmag": dmag, "n_pos": n_pos,
            "det": det, "sso_b": sso_b, "red_order": list(red.PLX_PSTAR_ORDER)}


def build(run_dir, catalog, data_root, locus, out):
    import plot_colour_diagrams as kit
    from matplotlib.backends.backend_pdf import PdfPages
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    D = load(run_dir, catalog, data_root)
    sl = kit.load_locus(locus)
    info = {}
    book_path = out / "Fink_LSST_paper_figures.pdf"
    with PdfPages(book_path, metadata={"Title": "Fink LSST alert sample: figures", "Author": "Darim Shamsi"}) as book:
        for name, fn in (("S1", lambda: fig_s1(D["sky"], out, book)),
                         ("M1", lambda: fig_m1(out, book)),
                         ("P1", lambda: fig_p1(D["bands"], D["det"], D["dmag"], D["n_pos"], D["tp"], out, book)),
                         ("P2", lambda: fig_p2(D["tp"], D["sso_b"], out, book)),
                         ("P3", lambda: fig_p3(D["tp"], D["sci"], out, book)),
                         ("T1", lambda: fig_t1(D["tc"], D["tp"], sl, out, book)),
                         ("T2", lambda: fig_t2(D["tc"], sl, out, book)),
                         ("T3", lambda: fig_t3(D["tc"], sl, out, book)),
                         ("C1", lambda: fig_c1(D["sci"], out, book)),
                         ("R3", lambda: fig_r3(D["v"], D["red_order"], out, book))):
            path, extra = fn()
            info[name] = {"png": str(path), **extra}
            print(name, path, flush=True)
    assert sum(h.sum() for h in D["dmag"].values()) <= D["n_pos"]
    info["sky_checks"] = D["sky"]["checks"]
    info["n_pos_alerts_catalog"] = D["n_pos"]
    info["n_pos_alerts_in_12_27"] = int(sum(h.sum() for h in D["dmag"].values()))
    (out / "figures_summary.json").write_text(json.dumps(info, indent=2, default=str) + "\n")
    print("book:", book_path)
    return book_path


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--run-dir", required=True)
    p.add_argument("--catalog", required=True)
    p.add_argument("--data-root", required=True)
    p.add_argument("--locus", required=True)
    p.add_argument("--out", required=True)
    a = p.parse_args()
    build(a.run_dir, a.catalog, a.data_root, a.locus, a.out)

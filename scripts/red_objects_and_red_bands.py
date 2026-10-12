#!/usr/bin/env python
"""Follow-up diagrams requested in mentor review: the g-i > 2 objects and colour diagrams in the red bands.

  R0  g-i > 2 objects over all colour-eligible DIA objects (template photometry, panels as in the colour run)
  R1  g-i > 2 objects faceted by Fink CATS label        R2  ... by Fink SIMBAD group
  R3  g-i > 2 objects faceted by Gaia parallax / Legacy DR8 morphology
  D1  why their faint r limit is brighter: g and r template-magnitude limits, alerts by band, first-detected band
  Z1  red-band diagrams i vs i-z, r-i vs i-z, i-z vs z-y, split by programme (outside vs inside bulge/plane)
  Z2  r-i vs i-z and i vs i-z faceted by Fink SIMBAD group

Inputs are the audited per-object tables of runs alert-colour-diagrams-20261011-v3 and alert-science-suite-20261008-v1;
the first-detected band comes from the analytical catalog opened read-only with ``open_catalog``. Outputs go to a new
run directory that must not exist.
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import alert_colour_diagrams as acd  # noqa: E402  (same panels, labels, background and styling)

RED_CUT = 2.0  # (g - i)_tmpl > 2
BANDS = ("u", "g", "r", "i", "z", "y")
RED, BLUE, INK, INK_MUTED, GRID = "#e34948", "#2a78d6", acd.INK, acd.INK_MUTED, acd.GRID
DUSTY = ("Galactic bulge", "Galactic plane")

ZPANELS = (
    # key, x, y, x label, y label, x range, y range, invert y
    ("z_a", "iz", "mag_i", r"$(i-z)_\mathrm{tmpl}$", r"$i_\mathrm{tmpl}$ (template PSF, AB)", (-0.75, 1.75), (14.0, 26.0), True),
    ("z_b", "iz", "ri", r"$(i-z)_\mathrm{tmpl}$", r"$(r-i)_\mathrm{tmpl}$", (-0.75, 1.75), (-0.75, 2.75), False),
    ("z_c", "zy", "iz", r"$(z-y)_\mathrm{tmpl}$", r"$(i-z)_\mathrm{tmpl}$", (-0.75, 1.25), (-0.75, 1.75), False),
)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def generic_panel(ax, base, sub, x, y, xr, yr, inv, xl, yl, show_xlabel=True, sub_label=None):
    """Grey density of `base`, blue dots for `sub`. The count reports `sub` in facet mode (no sub_label),
    otherwise `base`, with the highlighted count on a second line."""
    acd.background(ax, base, x, y, xr, yr)
    if sub is not None and len(sub):
        ins = sub[x].between(*xr) & sub[y].between(*yr)
        ax.scatter(sub.loc[ins, x], sub.loc[ins, y], s=8 if len(sub) < 2000 else 4, color=BLUE,
                   edgecolors="white", linewidths=0.2, alpha=0.9, rasterized=True)
    d = sub if (sub is not None and sub_label is None) else base
    ins_d = d[x].between(*xr) & d[y].between(*yr)
    n_off = int((~ins_d).sum())
    txt = f"n = {len(d):,}" + (f" ({n_off:,} off-axis)" if n_off else "")
    if sub_label is not None and sub is not None:
        txt += f"\n{sub_label}: {len(sub):,}"
    ax.text(0.03, 0.97, txt, transform=ax.transAxes, va="top", ha="left", fontsize=7.5, color=INK)
    ax.set_xlim(*xr)
    ax.set_ylim(*(yr[::-1] if inv else yr))
    if show_xlabel:
        ax.set_xlabel(xl)
    ax.set_ylabel(yl)


def save(fig, figs, name, plt, record, caption):
    for ext in ("png", "pdf"):
        fig.savefig(figs / f"{name}.{ext}")
    plt.close(fig)
    record.append({"file": f"figures/{name}.png", "caption": caption})


def facet_rows(df_bg, df, column, order, plt, figs, name, suptitle, record, caption, panels=None):
    """Rows = groups in `order` that have at least one plotted object; grey = df_bg, blue = group members."""
    if panels is None:
        rows = [g for g in order if ((df[column] == g) & (df.panel_a | df.panel_b)).any()]
        fig, axes = plt.subplots(len(rows), 3, figsize=(12, 3.1 * len(rows)), constrained_layout=True, squeeze=False)
        for i, g in enumerate(rows):
            acd.draw_panels(axes[i], df_bg, df[df[column] == g], g, i == len(rows) - 1)
    else:
        rows = [g for g in order if any(((df[column] == g) & df[p[0]]).any() for p in panels)]
        fig, axes = plt.subplots(len(rows), len(panels), figsize=(4.2 * len(panels), 3.1 * len(rows)), constrained_layout=True, squeeze=False)
        for i, g in enumerate(rows):
            for j, (key, x, y, xl, yl, xr, yr, inv) in enumerate(panels):
                generic_panel(axes[i, j], df_bg[df_bg[key]], df[(df[column] == g) & df[key]], x, y, xr, yr, inv, xl, yl, i == len(rows) - 1)
            axes[i, 0].set_title(g, loc="left", fontsize=9, fontweight="bold")
    fig.suptitle(suptitle, fontsize=10, color=INK)
    save(fig, figs, name, plt, record, caption)
    keys = ["panel_a", "panel_b"] if panels is None else [p[0] for p in panels]
    return {g: {k: int(((df[column] == g) & df[k]).sum()) for k in keys} for g in rows}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--template-photometry", required=True)
    p.add_argument("--science-table", required=True)
    p.add_argument("--catalog", required=True)
    p.add_argument("--data-root", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--code-sha", required=True)
    a = p.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=False)
    (out / "figures").mkdir()
    (out / "tables").mkdir()
    figs = out / "figures"
    t0 = time.time()

    v = pd.read_parquet(a.template_photometry)
    sci = pd.read_parquet(a.science_table, columns=["dia_object_id", "pstar", "plx", "eplx"])
    assert v.dia_object_id.is_unique and sci.dia_object_id.is_unique and len(v) == len(sci)
    v = v.merge(sci, on="dia_object_id", how="left", validate="one_to_one")
    for b in BANDS:
        v[f"n_src_{b}"] = v[f"n_src_{b}"].fillna(0).astype(int)
    v["n_alerts"] = v[[f"n_src_{b}" for b in BANDS]].sum(axis=1)
    v["iz"] = v.mag_i - v.mag_z
    v["zy"] = v.mag_z - v.mag_y
    v["z_a"] = v.ok_i & v.ok_z
    v["z_b"] = v.ok_r & v.ok_i & v.ok_z
    v["z_c"] = v.ok_i & v.ok_z & v.ok_y
    v["red"] = v.panel_a & (v.gi > RED_CUT)
    v["blue"] = v.panel_a & (v.gi <= RED_CUT)
    plx_snr = v.plx / v.eplx
    v["morph"] = np.select(
        [(plx_snr > 5) & (v.plx > 0), v.pstar >= 0.5, v.pstar < 0.5],
        ["Gaia DR3 parallax S/N > 5", "Legacy DR8 point-like (pstar ≥ 0.5), no Gaia parallax S/N > 5",
         "Legacy DR8 extended (pstar < 0.5)"], "No Gaia parallax S/N > 5 and no Legacy DR8 morphology")

    # First detected band (catalog, read-only)
    conn = acd.open_catalog(a.catalog, a.data_root)
    ids = v.loc[v.panel_a, ["dia_object_id"]]
    conn.register("ids", ids)
    fb = conn.execute("""
        SELECT s.dia_object_id, s.band FROM (
          SELECT dia_object_id, band, row_number() OVER (PARTITION BY dia_object_id ORDER BY observation_mjd_tai, source_id) AS rk
          FROM sources WHERE population = 'DIA') s
        JOIN ids USING (dia_object_id) WHERE s.rk = 1""").df()
    conn.close()
    assert len(fb) == len(ids) and fb.dia_object_id.is_unique
    v = v.merge(fb.rename(columns={"band": "first_band"}), on="dia_object_id", how="left", validate="one_to_one")

    red, blue, elig = v[v.red], v[v.blue], v[v.panel_a | v.panel_b]
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    acd.style(plt)
    plt.rcParams.update({"legend.frameon": False, "legend.fontsize": 8, "savefig.bbox": "tight"})
    record, res = [], {"n_red": int(len(red)), "n_blue": int(len(blue)), "red_cut": f"(g-i)_tmpl > {RED_CUT}, among objects with usable g, r, i templates"}

    # R0 red subset over all colour-eligible objects
    fig, axes = plt.subplots(1, 3, figsize=(12, 4), constrained_layout=True)
    acd.draw_panels(axes, elig, red, f"(g−i)_tmpl > {RED_CUT:g} (blue) over all colour-eligible DIA objects (grey)", True)
    axes[0].axvline(RED_CUT, ls="--", color=INK_MUTED, lw=0.9)
    g95 = float(red.mag_g.quantile(0.95))
    axes[0].text(0.97, 0.03, f"95% of these have g_tmpl < {g95:.1f}; with g−r ≈ {float((red.mag_g - red.mag_r).median()):.2f}\n"
                 "the required g-band alert limits how faint they reach in r",
                 transform=axes[0].transAxes, ha="right", va="bottom", fontsize=7, color=INK)
    fig.suptitle("Objects redder than g−i = 2 in template photometry (selection: alerts and usable templates in g, r and i)", fontsize=10)
    save(fig, figs, "R0_red_subset_overview", plt, record,
         "Objects with (g−i)_tmpl > 2 (blue) over all colour-eligible DIA objects (grey). Every plotted object must have alerted in g, r and i, so red objects need a g detection; that sets their brighter faint limit in r.")

    order_cats = [acd.CATS_LABELS[k] for k in (11, 12, 13, 21, 22, -1)]
    res["R1_rows"] = facet_rows(red, red, "cats_label", order_cats, plt, figs, "R1_red_by_fink_cats",
                                f"(g−i)_tmpl > {RED_CUT:g} objects only: Fink CATS label at the latest alert (blue) over all g−i > 2 objects (grey).\n"
                                "Labels are Fink's broad classes, not verified object types.", record,
                                "The g−i > 2 objects faceted by Fink CATS label; grey shows all g−i > 2 objects.")
    order_sim = ["Star", "QSO / AGN", "Galaxy", "Other SIMBAD type", acd.NO_MATCH, acd.XM_FAILED]
    res["R2_rows"] = facet_rows(red, red, "simbad_group", order_sim, plt, figs, "R2_red_by_simbad",
                                f"(g−i)_tmpl > {RED_CUT:g} objects only: Fink SIMBAD crossmatch (1″, latest successful) (blue) over all g−i > 2 objects (grey)",
                                record, "The g−i > 2 objects faceted by SIMBAD group (Fink crossmatch within 1″).")
    order_m = ["Gaia DR3 parallax S/N > 5", "Legacy DR8 point-like (pstar ≥ 0.5), no Gaia parallax S/N > 5",
               "Legacy DR8 extended (pstar < 0.5)", "No Gaia parallax S/N > 5 and no Legacy DR8 morphology"]
    res["R3_rows"] = facet_rows(red, red, "morph", order_m, plt, figs, "R3_red_by_parallax_and_morphology",
                                f"(g−i)_tmpl > {RED_CUT:g} objects only: Gaia DR3 parallax and Legacy DR8 morphology from Fink's crossmatches (blue) over all g−i > 2 objects (grey)",
                                record, "The g−i > 2 objects split by Gaia parallax and Legacy DR8 morphology (rows are exclusive, in the order shown).")

    # D1 why the faint r limit is brighter for red objects
    fig, axes = plt.subplots(1, 4, figsize=(17, 4), constrained_layout=True)
    for ax, col, lab in ((axes[0], "mag_g", r"$g_\mathrm{tmpl}$"), (axes[1], "mag_r", r"$r_\mathrm{tmpl}$")):
        bins = np.arange(14, 26.01, 0.25)
        for d, c, name in ((blue, BLUE, f"g−i ≤ 2 ({len(blue):,})"), (red, RED, f"g−i > 2 ({len(red):,})")):
            ax.hist(d[col], bins, density=True, histtype="step", lw=1.8, color=c, label=name)
            q95 = float(d[col].quantile(0.95))
            ax.axvline(q95, color=c, ls="--", lw=1)
            res[f"D1_{col}_q95_{'red' if d is red else 'blue'}"] = round(q95, 2)
        ax.set_xlabel(lab + " (template PSF, AB)")
        ax.set_ylabel("density")
        ax.legend(loc="upper left")
    dg = res["D1_mag_g_q95_blue"] - res["D1_mag_g_q95_red"]
    dr = res["D1_mag_r_q95_blue"] - res["D1_mag_r_q95_red"]
    axes[0].set_title(f"Similar faint g limit: 95th percentiles differ by {dg:.1f} mag", loc="left")
    axes[1].set_title(f"In r, the red objects stop {dr:.1f} mag brighter", loc="left")
    share = {}
    for key, d in (("blue", blue), ("red", red)):
        tot = sum(int(d[f"n_src_{b}"].sum()) for b in BANDS)
        share[key] = [d[f"n_src_{b}"].sum() / tot for b in BANDS]
        res[f"D1_alerts_by_band_{key}"] = {b: int(d[f"n_src_{b}"].sum()) for b in BANDS}
        res[f"D1_first_band_{key}"] = {b: int((d.first_band == b).sum()) for b in BANDS}
    x = np.arange(len(BANDS))
    for ax, key, title in ((axes[2], "share", "Share of all their alerts, by band"), (axes[3], "first", "Band of the first delivered alert")):
        for k, (name, d, c) in enumerate((("g−i ≤ 2", blue, BLUE), ("g−i > 2", red, RED))):
            vals = share["blue" if d is blue else "red"] if key == "share" else [(d.first_band == b).mean() for b in BANDS]
            ax.bar(x + (k - 0.5) * 0.38, vals, width=0.36, color=c, label=name)
        ax.set_xticks(x)
        ax.set_xticklabels(BANDS)
        ax.set_ylabel("fraction")
        ax.set_title(title, loc="left")
        ax.legend(loc="upper right")
    fig.suptitle("Why g−i > 2 objects have a brighter faint limit in r: the plots require a g-band alert", fontsize=10)
    save(fig, figs, "D1_red_detection_bands", plt, record,
         "Template g and r magnitude distributions (dashed: 95th percentile), share of alerts by band, and first-alert band for g−i > 2 vs g−i ≤ 2 objects.")

    # Z1 red-band diagrams by programme
    outside = v[~v.field.isin(DUSTY)]
    inside = v[v.field.isin(DUSTY)]
    fig, axes = plt.subplots(2, 3, figsize=(13, 8.4), constrained_layout=True)
    for row, (d, title) in enumerate(((outside, "Outside Galactic bulge and plane fields"), (inside, "Galactic bulge and plane fields (no extinction correction)"))):
        for j, (key, x_, y_, xl, yl, xr, yr, inv) in enumerate(ZPANELS):
            base = d[d[key]]
            generic_panel(axes[row, j], base, base[base.red], x_, y_, xr, yr, inv, xl, yl, sub_label="blue, g−i > 2")
            if len(base) == 0:
                axes[row, j].text(0.5, 0.5, "no objects with usable i, z and y templates", transform=axes[row, j].transAxes,
                                  ha="center", va="center", fontsize=8, color=INK_MUTED)
            res[f"Z1_{'outside' if row == 0 else 'inside'}_{key}_n"] = int(len(base))
            res[f"Z1_{'outside' if row == 0 else 'inside'}_{key}_red_n"] = int(base.red.sum())
        axes[row, 0].set_title(title, loc="left", fontsize=9, fontweight="bold")
    fig.suptitle("Red-band template colours (grey: density and 50/80/95% contours; blue: g−i > 2 objects where g is usable)", fontsize=10)
    save(fig, figs, "Z1_red_band_diagrams", plt, record,
         "i vs i−z, r−i vs i−z and i−z vs z−y from template photometry, split by programme because bulge and plane colours are uncorrected for extinction. y-band templates exist for few objects.")

    # Z2 red-band diagrams by SIMBAD group (outside bulge/plane)
    res["Z2_rows"] = facet_rows(outside, outside, "simbad_group", ["Star", "QSO / AGN", "Galaxy", acd.NO_MATCH], plt, figs,
                                "Z2_red_bands_by_simbad",
                                "Red-band template colours outside bulge and plane fields, by Fink SIMBAD group (blue) over all such objects (grey)",
                                record, "i vs i−z and r−i vs i−z by SIMBAD group, outside bulge and plane fields.", panels=ZPANELS[:2])

    # tables
    for key in ("R1", "R2", "R3", "Z2"):
        pd.DataFrame(res[f"{key}_rows"]).T.rename_axis("group").to_csv(out / "tables" / f"{key}_plotted_counts.csv")
    cols = ["dia_object_id", "field", "cats_class_latest", "simbad_group", "morph", "first_band", "mag_u", "mag_g", "mag_r", "mag_i", "mag_z", "mag_y",
            "gi", "gr", "ri", "iz", "zy", "pstar", "plx", "eplx"] + [f"n_src_{b}" for b in BANDS]
    red[cols].to_csv(out / "tables" / "red_gi_gt2_objects.csv", index=False)
    res["red_field"] = red.field.value_counts().to_dict()
    res["red_morph"] = red.morph.value_counts().to_dict()
    summary = {"run": out.name, "code_sha": a.code_sha, "script_sha256": sha256(Path(__file__)),
               "helper_sha256": sha256(Path(acd.__file__)), "template_photometry_sha256": sha256(a.template_photometry),
               "science_table_sha256": sha256(a.science_table), "catalog_sha256": sha256(a.catalog),
               "python": platform.python_version(), "figures": record, "results": res, "runtime_s": round(time.time() - t0, 1)}
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=lambda o: o.item() if hasattr(o, "item") else str(o)) + "\n")
    print(json.dumps({"n_red": res["n_red"], "figures": len(record), "runtime_s": summary["runtime_s"]}))


if __name__ == "__main__":
    main()

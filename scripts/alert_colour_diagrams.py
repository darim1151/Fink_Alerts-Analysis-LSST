#!/usr/bin/env python
"""Template-photometry colour-magnitude and colour-colour diagrams for DIA alerts.

Light Static alerts carry no coadd CModel or PSF-minus-model photometry. The only
static-sky quantity per band is ``templateFlux``: forced PSF photometry on the
difference-imaging template at each DIA source position (nJy). This script
aggregates it per DIA object and band, converts to AB magnitudes and draws:

  (a) r_tmpl vs (g - i)_tmpl     (b) (g - r)_tmpl vs (u - g)_tmpl
  (c) (r - i)_tmpl vs (g - r)_tmpl

for all colour-eligible DIA objects and for subsamples defined by the Fink
labels present in the delivery (CATS broad class, SIMBAD crossmatch label).
SSO sources are counted but not plotted: they have no object key and no static
template counterpart.

The catalog is opened read-only through ``open_catalog``. Outputs go to a new,
run-scoped directory that must not already exist.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
import time
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from fink_lsst.analytics.catalog import open_catalog  # noqa: E402

BANDS = ("u", "g", "r", "i", "z", "y")
AB_ZP_NJY = 31.4  # m_AB = 31.4 - 2.5 log10(f / nJy)
MIN_TEMPLATE_SNR = 5.0

CATS_LABELS = {
    11: "SN-like",
    12: "Fast (KN, ulens, novae)",
    13: "Long (SLSN, TDE, PISN)",
    21: "Periodic (RR Lyr, EB, LPV)",
    22: "Non-periodic (AGN)",
    -1: "Not processed (single detection)",
}

SIMBAD_QSO_AGN = {"QSO", "AGN", "Sy1", "Sy2", "SyG", "Sy?", "BLL", "BL?", "Bla", "Bz?", "Q?", "AG?", "LIN", "LI?"}
SIMBAD_GALAXY = {"G", "G?", "rG", "GiG", "GiC", "GiP", "BiC", "GrG", "EmG", "CGG", "SBG", "H2G", "LSB", "bCG",
                 "PaG", "ClG", "IG", "Gr?", "SB?", "HzG", "BCG", "LeG", "Sy3"}
# Stellar candidate codes that do not contain '*'.
SIMBAD_STAR_EXTRA = {"WD?", "AB?", "LP?", "RR?", "HS?", "CV?", "EB?", "RG?", "HB?", "BS?", "Ce?", "dS?", "PM?",
                     "s?r", "s?b", "s?y", "s?g", "Mi?", "Pu?", "RB?", "BD?", "TT?", "Ae?", "Be?", "Em?", "WR?",
                     "N*?", "Sy*?", "No?", "cC?", "RS?", "BY?", "Ir?", "Or?", "pA?", "OH?", "CH?", "HXB?",
                     "LXB?", "XB?", "WV?", "ZZ?", "El?", "SX?", "gD?", "S*?", "C*?", "Y*?", "pr?", "LM?", "Ro?",
                     "Er?", "RC?", "sg?", "PN?"}


def simbad_group(otype):
    if otype is None or (isinstance(otype, float) and np.isnan(otype)):
        return "No SIMBAD match"
    if otype == "Fail":
        return "Crossmatch failed"
    if otype in SIMBAD_QSO_AGN:
        return "QSO / AGN"
    if otype in SIMBAD_GALAXY:
        return "Galaxy"
    if "*" in otype or otype in SIMBAD_STAR_EXTRA:
        return "Star"
    return "Other SIMBAD type"


OBJECT_SQL = """
WITH dia AS (
  SELECT dia_object_id, band, template_flux, template_flux_err, ra_deg, dec_deg
  FROM sources WHERE population = 'DIA'
), per_band AS (
  SELECT dia_object_id, band, count(*) AS n_src,
         count(*) FILTER (WHERE isfinite(template_flux) AND isfinite(template_flux_err)) AS n_finite,
         median(template_flux) FILTER (WHERE isfinite(template_flux) AND isfinite(template_flux_err)) AS tf,
         median(template_flux_err) FILTER (WHERE isfinite(template_flux) AND isfinite(template_flux_err)) AS tfe,
         min(template_flux) FILTER (WHERE isfinite(template_flux)) AS tf_min,
         max(template_flux) FILTER (WHERE isfinite(template_flux)) AS tf_max
  FROM dia GROUP BY 1, 2
), pos AS (
  SELECT dia_object_id, count(*) AS n_sources, avg(ra_deg) AS ra_deg, avg(dec_deg) AS dec_deg
  FROM dia GROUP BY 1
), latest AS (
  SELECT dia_object_id,
         TRY_CAST(pred_json->>'main_label_classifier' AS INTEGER) AS cats_class_latest,
         pred_json->>'main_label_crossmatch' AS simbad_otype_latest,
         observation_mjd_tai AS label_mjd_tai,
         row_number() OVER (PARTITION BY dia_object_id ORDER BY observation_mjd_tai DESC, source_id DESC) AS rk
  FROM dia_source_snapshots
), label_stability AS (
  SELECT dia_object_id,
         count(DISTINCT TRY_CAST(pred_json->>'main_label_classifier' AS INTEGER)) AS n_distinct_cats_class
  FROM dia_source_snapshots GROUP BY 1
)
SELECT pos.*, latest.cats_class_latest, latest.simbad_otype_latest, latest.label_mjd_tai,
       label_stability.n_distinct_cats_class,
       {pivot}
FROM pos
JOIN latest USING (dia_object_id)
JOIN label_stability USING (dia_object_id)
LEFT JOIN per_band USING (dia_object_id)
WHERE latest.rk = 1
GROUP BY ALL
"""


def object_table(conn):
    cols = []
    for b in BANDS:
        for name in ("n_src", "n_finite", "tf", "tfe", "tf_min", "tf_max"):
            cols.append(f"max({name}) FILTER (WHERE band = '{b}') AS {name}_{b}")
    return conn.execute(OBJECT_SQL.format(pivot=",\n       ".join(cols))).df()


def add_magnitudes(df):
    for b in BANDS:
        tf, tfe = df[f"tf_{b}"].astype(float), df[f"tfe_{b}"].astype(float)
        ok = np.isfinite(tf) & np.isfinite(tfe) & (tf > 0) & (tfe > 0) & (tf / tfe >= MIN_TEMPLATE_SNR)
        df[f"ok_{b}"] = ok
        with np.errstate(divide="ignore", invalid="ignore"):
            df[f"mag_{b}"] = np.where(ok, AB_ZP_NJY - 2.5 * np.log10(tf), np.nan)
            df[f"magerr_{b}"] = np.where(ok, 2.5 / np.log(10) * tfe / tf, np.nan)
    df["gi"] = df.mag_g - df.mag_i
    df["gr"] = df.mag_g - df.mag_r
    df["ri"] = df.mag_r - df.mag_i
    df["ug"] = df.mag_u - df.mag_g
    df["panel_a"] = df.ok_g & df.ok_r & df.ok_i
    df["panel_c"] = df.panel_a
    df["panel_b"] = df.ok_u & df.ok_g & df.ok_r
    df["cats_label"] = df.cats_class_latest.map(lambda c: CATS_LABELS.get(int(c), f"code {int(c)}") if pd.notna(c) else "Missing")
    df["simbad_group"] = df.simbad_otype_latest.map(simbad_group)
    return df


def galactic_latitude(ra, dec):
    from astropy.coordinates import SkyCoord
    import astropy.units as u
    return SkyCoord(ra=np.asarray(ra) * u.deg, dec=np.asarray(dec) * u.deg, frame="icrs").galactic.b.deg


PANELS = (
    # key, x column, y column, x label, y label, x range, y range, invert y
    ("panel_a", "gi", "mag_r", r"$(g-i)_\mathrm{tmpl}$", r"$r_\mathrm{tmpl}$ (template PSF, AB)", (-1.0, 4.0), (14.0, 26.0), True),
    ("panel_b", "ug", "gr", r"$(u-g)_\mathrm{tmpl}$", r"$(g-r)_\mathrm{tmpl}$", (-1.0, 4.0), (-0.75, 2.25), False),
    ("panel_c", "gr", "ri", r"$(g-r)_\mathrm{tmpl}$", r"$(r-i)_\mathrm{tmpl}$", (-0.75, 2.25), (-0.75, 2.75), False),
)

INK, INK_MUTED, GRID = "#0b0b0b", "#52514e", "#e4e3df"
HIGHLIGHT = "#2a78d6"  # categorical slot 1 (reference palette); one highlight hue per facet


def style(plt):
    plt.rcParams.update({
        "font.size": 9, "axes.edgecolor": INK_MUTED, "axes.labelcolor": INK, "xtick.color": INK_MUTED,
        "ytick.color": INK_MUTED, "axes.titlesize": 9, "axes.titlecolor": INK, "figure.dpi": 150,
        "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.5, "axes.axisbelow": True,
    })


def background(ax, d, x, y, xr, yr):
    n = len(d)
    if n == 0:
        return
    bins = int(np.clip(np.sqrt(n) / 2, 25, 60))
    h, xe, ye = np.histogram2d(d[x], d[y], bins=bins, range=(xr, yr))
    xc, yc = 0.5 * (xe[1:] + xe[:-1]), 0.5 * (ye[1:] + ye[:-1])
    ax.pcolormesh(xe, ye, np.log10(h.T + 1), cmap="Greys", alpha=0.45, shading="flat", rasterized=True)
    # Contours on a 3x3 box-smoothed histogram; levels enclose 50/80/95% of the objects.
    p = np.pad(h, 1)
    hsm = sum(p[i:i + h.shape[0], j:j + h.shape[1]] for i in range(3) for j in range(3)) / 9.0
    hs = np.sort(hsm.ravel())[::-1]
    cum = np.cumsum(hs) / hs.sum()
    levels = sorted({hs[min(np.searchsorted(cum, f), len(hs) - 1)] for f in (0.5, 0.8, 0.95)})
    levels = [lv for lv in levels if lv > 0]
    if levels:
        ax.contour(xc, yc, hsm.T, levels=levels, colors=INK_MUTED, linewidths=0.7)


def draw_panels(axes, df, sub, title_prefix, show_labels):
    for ax, (key, x, y, xl, yl, xr, yr, inv) in zip(axes, PANELS):
        base = df[df[key]]
        background(ax, base, x, y, xr, yr)
        n_out = 0
        if sub is not None:
            s = sub[sub[key]]
            inside = s[x].between(*xr) & s[y].between(*yr)
            n_out = int((~inside).sum())
            ax.scatter(s.loc[inside, x], s.loc[inside, y], s=4 if len(s) > 2000 else 8, color=HIGHLIGHT,
                       edgecolors="white", linewidths=0.2, alpha=0.8 if len(s) > 2000 else 0.95, rasterized=True)
            txt = f"n = {len(s):,}" + (f" ({n_out:,} off-axis)" if n_out else "")
        else:
            inside = base[x].between(*xr) & base[y].between(*yr)
            txt = f"n = {len(base):,}" + (f" ({int((~inside).sum()):,} off-axis)" if (~inside).any() else "")
        ax.text(0.03, 0.97, txt, transform=ax.transAxes, va="top", ha="left", fontsize=7.5, color=INK)
        ax.set_xlim(*xr)
        ax.set_ylim(*(yr[::-1] if inv else yr))
        if show_labels:
            ax.set_xlabel(xl)
        ax.set_ylabel(yl)
    axes[0].set_title(title_prefix, loc="left", fontsize=9, fontweight="bold")


def figure_overview(df, plt, out):
    fig, axes = plt.subplots(1, 3, figsize=(12, 4), constrained_layout=True)
    draw_panels(axes, df, None, "All colour-eligible DIA objects (grey: log density; contours enclose 50/80/95%)", True)
    fig.suptitle("Fink LSST Light Static alerts, 2026-02-25 to 2026-07-13: template PSF photometry per DIA object",
                 fontsize=10, color=INK)
    for ext in ("png", "pdf"):
        fig.savefig(out / f"fig1_overview_all_dia.{ext}")
    plt.close(fig)


def figure_facets(df, column, order, plt, out, name, suptitle):
    present = [g for g in order if ((df[column] == g) & (df.panel_a | df.panel_b)).any()]
    fig, axes = plt.subplots(len(present), 3, figsize=(12, 3.1 * len(present)), constrained_layout=True, squeeze=False)
    for row, g in enumerate(present):
        draw_panels(axes[row], df, df[df[column] == g], g, row == len(present) - 1)
    fig.suptitle(suptitle, fontsize=10, color=INK)
    for ext in ("png", "pdf"):
        fig.savefig(out / f"{name}.{ext}")
    plt.close(fig)


def counts_table(df, column):
    rows = []
    for g, d in df.groupby(column, dropna=False):
        rows.append({column: g, "dia_objects": len(d), "panel_a_and_c_gri": int(d.panel_a.sum()),
                     "panel_b_ugr": int(d.panel_b.sum())})
    return pd.DataFrame(rows).sort_values("dia_objects", ascending=False)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--catalog", required=True)
    p.add_argument("--data-root", required=True)
    p.add_argument("--out", required=True, help="new run-scoped output directory (must not exist)")
    p.add_argument("--code-sha", required=True)
    p.add_argument("--temp-dir", required=True)
    p.add_argument("--threads", type=int, default=16)
    p.add_argument("--memory-limit", default="24GB")
    a = p.parse_args(argv)

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=False)
    (out / "figures").mkdir()
    (out / "tables").mkdir()
    t0 = time.time()

    conn = open_catalog(a.catalog, a.data_root)
    conn.execute(f"SET threads={a.threads}; SET memory_limit='{a.memory_limit}'; SET temp_directory='{a.temp_dir}'")
    meta = dict(conn.execute("SELECT key, value FROM catalog_metadata").fetchall())

    pop = dict(conn.execute("SELECT population, count(*) FROM sources GROUP BY 1").fetchall())
    band_pop = conn.execute("""
        SELECT population, band, count(*) AS sources,
               count(*) FILTER (WHERE isfinite(template_flux)) AS finite_template,
               count(*) FILTER (WHERE template_flux > 0) AS positive_template,
               median(template_flux) AS median_template_flux_njy
        FROM sources GROUP BY 1, 2 ORDER BY 1, 2""").df()
    raw_files = [r[0] for r in conn.execute("SELECT DISTINCT raw_file FROM sources ORDER BY 1").fetchall()]
    mem = duckdb.connect()
    mem.execute(f"SET threads={a.threads}")
    broker_versions = mem.execute("""
        SELECT fink_broker_version, fink_science_version, count(*) AS rows
        FROM read_parquet(?, union_by_name=false) GROUP BY 1, 2 ORDER BY 3 DESC""", [raw_files]).df()
    mem.close()

    df = object_table(conn)
    conn.close()
    df = add_magnitudes(df)
    df["gal_b_deg"] = galactic_latitude(df.ra_deg, df.dec_deg)

    # Template stability across an object's alerts in one band (templates may be rebuilt).
    stab = {}
    for b in BANDS:
        m = df[f"ok_{b}"] & (df[f"n_finite_{b}"] >= 2)
        rel = ((df.loc[m, f"tf_max_{b}"] - df.loc[m, f"tf_min_{b}"]) / df.loc[m, f"tf_{b}"]).astype(float)
        stab[b] = {"objects_with_ge2_sources": int(m.sum()),
                   "median_rel_range": float(rel.median()) if len(rel) else None,
                   "p90_rel_range": float(rel.quantile(0.9)) if len(rel) else None}

    df.to_parquet(out / "tables" / "dia_object_template_photometry.parquet", index=False)
    cats = counts_table(df, "cats_label")
    simb = counts_table(df, "simbad_group")
    cats.to_csv(out / "tables" / "counts_by_cats_class.csv", index=False)
    simb.to_csv(out / "tables" / "counts_by_simbad_group.csv", index=False)
    otypes = (df.groupby(["simbad_otype_latest", "simbad_group"], dropna=False)
                .agg(dia_objects=("dia_object_id", "size"), panel_a_and_c_gri=("panel_a", "sum"), panel_b_ugr=("panel_b", "sum"))
                .reset_index().sort_values("dia_objects", ascending=False))
    otypes.to_csv(out / "tables" / "simbad_otype_to_group.csv", index=False)
    band_pop.to_csv(out / "tables" / "template_flux_by_population_band.csv", index=False)
    broker_versions.to_csv(out / "tables" / "fink_versions.csv", index=False)

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    style(plt)
    figs = out / "figures"
    figure_overview(df, plt, figs)
    figure_facets(df, "cats_label", [CATS_LABELS[k] for k in (11, 12, 13, 21, 22, -1)], plt, figs,
                  "fig2_by_fink_cats_class",
                  "Fink CATS broad class at each object's latest delivered alert (blue) over all colour-eligible DIA objects (grey)")
    figure_facets(df, "simbad_group", ["Star", "QSO / AGN", "Galaxy", "Other SIMBAD type", "No SIMBAD match", "Crossmatch failed"],
                  plt, figs, "fig3_by_fink_simbad_crossmatch",
                  "Fink SIMBAD crossmatch label at each object's latest delivered alert (blue) over all colour-eligible DIA objects (grey)")

    elig = df[df.panel_a | df.panel_b]
    summary = {
        "run": out.name,
        "code_sha": a.code_sha,
        "script_sha256": sha256(Path(__file__)),
        "catalog": a.catalog,
        "catalog_sha256": sha256(a.catalog),
        "catalog_contract": json.loads(meta["contract_id"]),
        "python": platform.python_version(),
        "packages": {m: __import__(m).__version__ for m in ("duckdb", "numpy", "pandas", "matplotlib", "astropy", "pyarrow")},
        "source_rows_by_population": pop,
        "dia_objects": int(len(df)),
        "objects_with_band_ok": {b: int(df[f"ok_{b}"].sum()) for b in BANDS},
        "panel_a_and_c_gri_objects": int(df.panel_a.sum()),
        "panel_b_ugr_objects": int(df.panel_b.sum()),
        "eligible_abs_gal_b_lt_15_fraction": float((elig.gal_b_deg.abs() < 15).mean()) if len(elig) else None,
        "objects_with_changing_cats_class": int((df.n_distinct_cats_class > 1).sum()),
        "eligible_with_changing_cats_class": int((elig.n_distinct_cats_class > 1).sum()),
        "template_stability": stab,
        "cuts": {"population": "DIA", "template_flux_aggregate": "per object and band median over delivered sources with finite flux and error",
                 "min_template_snr": MIN_TEMPLATE_SNR, "positive_flux": True, "ab_zero_point_njy": AB_ZP_NJY,
                 "extinction_correction": None, "label_rule": "latest delivered snapshot per DIA object (max observation_mjd_tai, then max source_id)"},
        "runtime_seconds": round(time.time() - t0, 1),
    }
    for col, t in (("cats_label", cats), ("simbad_group", simb)):
        summary[f"counts_by_{col}"] = t.to_dict(orient="records")
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=int) + "\n")
    print(json.dumps({k: summary[k] for k in ("dia_objects", "objects_with_band_ok", "panel_a_and_c_gri_objects",
                                               "panel_b_ugr_objects", "runtime_seconds")}, default=int))


if __name__ == "__main__":
    main()

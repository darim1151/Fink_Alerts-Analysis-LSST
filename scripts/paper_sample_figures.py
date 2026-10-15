#!/usr/bin/env python
"""Sample description, SIMBAD-matched vs unmatched brightness, and the transient (difference-flux) colour diagram.

  sample_summary               alerts per band and population; difference-flux and template magnitude distributions;
                               Galactic latitude; detections per DIA object
  fig_p1_sample                P1: alerts per band, detections per object, alert (difference-flux) magnitudes, template magnitudes
  fig_p2_galactic_latitude     P2: Galactic latitude of DIA objects and SSO alerts
  fig_p3_simbad_magnitudes     P3: template magnitudes of DIA objects with a SIMBAD type, with no SIMBAD object within 1",
                               and with a failed crossmatch (all fields, outside and inside bulge/plane); fractions with a
                               SIMBAD type and with a Gaia DR3 source vs template i magnitude
  transient_colours            same-night difference-flux colours g-r and r-i (the colour of the brightening itself)
  fig_t1_t3_transient_colours  T1 overview, T2 by Fink CATS label, T3 by SIMBAD group
  paper_figure_counts          objects per panel and per row of figures 1-3 (notebook 01) and L3-L4 (notebook 04)

Inputs: notebook 01's per-object template table, notebook 02's science table (Gaia name), notebook 04's exported colour
table, the catalog's pinned raw files
(read through alert_science_suite) and the stellar-locus file. Used by notebooks/science/06_paper_additions.ipynb.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "colour_plot_kit"))
import alert_colour_diagrams as acd  # noqa: E402
import alert_science_suite as suite  # noqa: E402

AB_ZP_NJY = 31.4
BANDS = ("u", "g", "r", "i", "z", "y")
DUSTY = ("Galactic bulge", "Galactic plane")
BAND_COLOR = suite.BAND_COLOR
BLUE, ORANGE, GREY = "#2a78d6", "#eb6834", "#8d8b85"
INK = acd.INK
SIMBAD_TYPED = ("Star", "QSO / AGN", "Galaxy", "Other SIMBAD type")
MAG_BINS = np.arange(12.0, 27.01, 0.2)
TRANSIENT_MIN_SNR = 5.0
T_RANGES = ((-1.25, 2.25), (-1.25, 2.75))   # (g-r, r-i) axis ranges of the transient-colour panels


def _plt():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    suite.style(plt)
    return plt


def _save(fig, figs_dir, name, plt, record, caption):
    for ext in ("png", "pdf"):
        fig.savefig(Path(figs_dir) / f"{name}.{ext}")
    plt.close(fig)
    record.append({"file": f"figures/{name}.png", "caption": caption})
    return Path(figs_dir) / f"{name}.png"


def _q(s, ps=(0.05, 0.5, 0.95)):
    """Quantiles rounded to 0.01 (default 5%, 50%, 95%); None for an empty sample."""
    s = pd.Series(s).dropna()
    return [round(float(x), 2) for x in s.quantile(list(ps))] if len(s) else None


def _diff_mag(flux):
    """AB magnitude of a positive difference flux in nJy (NaN where flux <= 0)."""
    f = pd.Series(flux, dtype=float)
    return AB_ZP_NJY - 2.5 * np.log10(f.where(f > 0))


# ============================================================================= sample description
def sample_summary(src, tp):
    """Numbers describing the delivered sample. src: one row per alert (alert_science_suite.prepare_sources);
    tp: per-object template table from notebook 01 (one row per DIA object)."""
    dia, sso = src[src.pop_ == "DIA"], src[src.pop_ == "SSO"]
    r = {"alerts": int(len(src)), "dia_alerts": int(len(dia)), "sso_alerts": int(len(sso)), "dia_objects": int(len(tp))}
    assert int(tp.n_sources.sum()) == len(dia)
    r["alerts_by_band"] = {p: {b: int((d.band == b).sum()) for b in BANDS} for p, d in (("DIA", dia), ("SSO", sso))}
    r["dia_alerts_positive_difference_flux_fraction"] = round(float((dia.psfFlux > 0).mean()), 4)
    r["dia_alerts_positive_difference_flux_fraction_by_band"] = {b: round(float((dia.psfFlux[dia.band == b] > 0).mean()), 4) for b in BANDS}
    dm, sm = _diff_mag(dia.psfFlux).to_numpy(), _diff_mag(sso.psfFlux).to_numpy()
    r["dia_alert_difference_mag_q05_50_95_by_band"] = {b: _q(dm[(dia.band == b).to_numpy()]) for b in BANDS}
    r["sso_alert_difference_mag_q05_50_95_by_band"] = {b: _q(sm[(sso.band == b).to_numpy()]) for b in BANDS}
    r["dia_alert_difference_mag_outside_12_27"] = int(((dm < 12) | (dm > 27)).sum())
    r["dia_objects_usable_template_by_band"] = {b: int(tp[f"ok_{b}"].sum()) for b in BANDS}
    r["dia_objects_with_any_usable_template"] = int(tp[[f"ok_{b}" for b in BANDS]].any(axis=1).sum())
    r["template_mag_q05_50_95_by_band"] = {b: _q(tp.loc[tp[f"ok_{b}"], f"mag_{b}"]) for b in BANDS}
    tm = pd.concat([tp.loc[tp[f"ok_{b}"], f"mag_{b}"] for b in BANDS])
    r["template_mag_outside_12_27"] = int(((tm < 12) | (tm > 27)).sum())
    n = tp.n_sources
    r["detections_per_dia_object"] = {"median": float(n.median()), "mean": round(float(n.mean()), 3), "q90": float(n.quantile(0.9)),
                                      "q99": float(n.quantile(0.99)), "max": int(n.max()), "objects_1": int((n == 1).sum()),
                                      "objects_ge2": int((n >= 2).sum()), "objects_ge5": int((n >= 5).sum()), "objects_ge20": int((n >= 20).sum())}
    nb = tp[[f"n_src_{b}" for b in BANDS]].fillna(0).gt(0).sum(axis=1)
    r["dia_objects_by_number_of_alert_bands"] = {int(k): int(v) for k, v in nb.value_counts().sort_index().items()}
    b = tp.gal_b_deg.abs()
    r["dia_objects_abs_gal_b_fractions"] = {"<10": round(float((b < 10).mean()), 4), "10-20": round(float(((b >= 10) & (b < 20)).mean()), 4),
                                            "20-30": round(float(((b >= 20) & (b < 30)).mean()), 4), ">=30": round(float((b >= 30).mean()), 4)}
    r["dia_objects_abs_gal_b_q05_50_95"] = _q(b)
    r["dia_objects_by_field"] = {k: int(v) for k, v in tp.field.value_counts().items()}
    r["dia_objects_abs_gal_b_median_by_field"] = {k: round(float(v), 1) for k, v in tp.groupby("field").gal_b_deg.apply(lambda x: x.abs().median()).items()}
    r["sso_alerts_abs_gal_b_q05_50_95"] = _q(sso.gal_b.abs())
    r["sso_alerts_abs_ecl_lat_q05_50_95"] = _q(sso.ecl_lat.abs())
    return r


def fig_p1_sample(src, tp, figs_dir, record):
    """P1: alerts per band (DIA, SSO); delivered detections per DIA object; magnitudes of the positive difference fluxes
    of DIA alerts; template magnitudes of DIA objects."""
    plt = _plt()
    dia, sso = src[src.pop_ == "DIA"], src[src.pop_ == "SSO"]
    fig, axes = plt.subplots(2, 2, figsize=(12, 8.2), constrained_layout=True)
    ax = axes[0, 0]
    x = np.arange(len(BANDS))
    ax.bar(x - 0.19, [int((dia.band == b).sum()) for b in BANDS], width=0.36, color=BLUE, label=f"DIA alerts ({len(dia):,})")
    ax.bar(x + 0.19, [int((sso.band == b).sum()) for b in BANDS], width=0.36, color=ORANGE, label=f"SSO alerts ({len(sso):,})")
    ax.set_yscale("log")
    ax.set_xticks(x)
    ax.set_xticklabels(BANDS)
    ax.set_ylabel("alerts")
    ax.set_title("Alerts per band", loc="left")
    ax.legend(loc="upper left")
    ax = axes[0, 1]
    n = tp.n_sources.to_numpy()
    ax.hist(n, bins=np.arange(0.5, 61.0, 1.0), color=BLUE)
    ax.set_yscale("log")
    ax.set_xlabel("delivered alerts (detections) per DIA object")
    ax.set_ylabel("DIA objects")
    ax.set_title(f"Detections per DIA object ({len(tp):,} objects; {int((n > 60).sum()):,} with more than 60 not shown)", loc="left")
    pos = (dia.psfFlux > 0).to_numpy()
    dm = _diff_mag(dia.psfFlux).to_numpy()
    for b in BANDS:
        m = dm[pos & (dia.band == b).to_numpy()]
        if len(m):
            axes[1, 0].hist(m, MAG_BINS, histtype="step", lw=1.6, color=BAND_COLOR[b], label=f"{b} ({len(m):,})")
        t = tp.loc[tp[f"ok_{b}"], f"mag_{b}"]
        if len(t):
            axes[1, 1].hist(t, MAG_BINS, histtype="step", lw=1.6, color=BAND_COLOR[b], label=f"{b} ({len(t):,})")
    axes[1, 0].set_xlabel("difference-flux magnitude of the alert (AB; alerts with psfFlux > 0)")
    axes[1, 0].set_title("DIA alerts: brightness of the change", loc="left")
    axes[1, 1].set_xlabel("template PSF magnitude of the DIA object (AB)")
    axes[1, 1].set_title("DIA objects: brightness on the template (static sky)", loc="left")
    for ax in axes[1]:
        ax.set_yscale("log")
        ax.set_ylabel("per 0.2 mag")
        ax.set_xlim(MAG_BINS[0], MAG_BINS[-1])
        ax.legend(loc="upper left", fontsize=7)
    return _save(fig, figs_dir, "P1_sample_bands_detections_magnitudes", plt, record,
                 "Alerts per band; delivered detections per DIA object; magnitudes of the positive difference fluxes of DIA "
                 "alerts; template PSF magnitudes of DIA objects (per band: median templateFlux over the object's alerts in "
                 "that band, kept when positive with median S/N >= 5).")


def fig_p2_galactic_latitude(src, tp, figs_dir, record):
    """P2: Galactic latitude of DIA objects (mean alert position) and of SSO alerts."""
    plt = _plt()
    sso = src[src.pop_ == "SSO"]
    fig, ax = plt.subplots(figsize=(8.5, 4), constrained_layout=True)
    bins = np.arange(-90, 90.1, 2)
    ax.hist(tp.gal_b_deg, bins, histtype="step", lw=1.8, color=BLUE, label=f"DIA objects ({len(tp):,})")
    ax.hist(sso.gal_b, bins, histtype="step", lw=1.8, color=ORANGE, label=f"SSO alerts ({len(sso):,})")
    ax.set_yscale("log")
    ax.set_xlim(-90, 90)
    ax.set_xlabel("Galactic latitude b (deg)")
    ax.set_ylabel("per 2 deg")
    ax.set_title("Galactic latitude of the sample", loc="left")
    ax.legend(loc="upper left")
    return _save(fig, figs_dir, "P2_galactic_latitude", plt, record,
                 "Galactic latitude of DIA objects (mean position of their delivered alerts) and of SSO alerts.")


def _fraction(ax, mag, flag, bins, color, label, min_n=20):
    """Fraction of objects with `flag` per magnitude bin, with 95% Wilson intervals (bins with >= min_n objects)."""
    k, _ = np.histogram(mag[flag], bins)
    n, _ = np.histogram(mag, bins)
    c = 0.5 * (bins[1:] + bins[:-1])
    good = n >= min_n
    lo, hi = np.array([suite.wilson(kk, nn) for kk, nn in zip(k, n)]).T
    ax.fill_between(c[good], lo[good], hi[good], color=color, alpha=0.18, lw=0)
    ax.plot(c[good], (k / np.maximum(n, 1))[good], "-o", color=color, ms=3, lw=1.5, label=label)
    return [{"lo": float(bins[j]), "hi": float(bins[j + 1]), "objects": int(n[j]), "with_flag": int(k[j])} for j in range(len(n))]


def fig_p3_simbad_magnitudes(tp, sci, figs_dir, record, res):
    """P3: template magnitudes of DIA objects with a SIMBAD type, with no SIMBAD object within 1", and with a crossmatch
    that failed on every alert (the groups of figure 3), for all fields, outside the bulge and plane fields, and inside
    them; the fraction with a SIMBAD type vs i_tmpl; and the fraction with a Gaia DR3 source within 1" vs i_tmpl.
    sci: the science table of notebook 02 (dia_object_id, gaia_name)."""
    plt = _plt()
    d = tp.merge(sci[["dia_object_id", "gaia_name"]], on="dia_object_id", how="left", validate="one_to_one")
    assert len(d) == len(tp)
    typed = d.simbad_group.isin(SIMBAD_TYPED)
    nomatch = d.simbad_group == acd.NO_MATCH
    failed = d.simbad_group == acd.XM_FAILED
    assert int(typed.sum() + nomatch.sum() + failed.sum()) == len(d)
    groups = [("SIMBAD type", typed, BLUE), (acd.NO_MATCH, nomatch, GREY), (acd.XM_FAILED, failed, ORANGE)]
    dusty = d.field.isin(DUSTY)
    scopes = [("all fields", pd.Series(True, index=d.index)), ("outside bulge and plane", ~dusty), ("bulge and plane fields", dusty)]
    fig, axes = plt.subplots(2, 3, figsize=(17, 9), constrained_layout=True)
    bins = np.arange(12, 27.01, 0.25)
    out = {}
    panels = [(axes[0, 0], "i", scopes[0]), (axes[0, 1], "i", scopes[1]), (axes[0, 2], "i", scopes[2]), (axes[1, 0], "r", scopes[0])]
    for ax, b, (sname, sm) in panels:
        top = 0.0
        for name, m, c in groups:
            v = d.loc[sm & m & d[f"ok_{b}"], f"mag_{b}"]
            out[f"{sname} | {name} | {b}"] = {"objects_in_group": int((sm & m).sum()), "with_usable_template": int(len(v)),
                                              "mag_q05_50_95": _q(v)}
            if len(v):
                h, _, _ = ax.hist(v, bins, density=True, histtype="step", lw=1.8, color=c,
                                  label=f"{name}: {len(v):,} of {int((sm & m).sum()):,}; median {v.median():.2f}")
                top = max(top, float(h.max()))
        ax.set_ylim(0, 1.55 * top if top else 1)
        ax.set_xlim(bins[0], bins[-1])
        ax.set_xlabel(f"${b}_\\mathrm{{tmpl}}$ (template PSF, AB)")
        ax.set_ylabel("normalised per 0.25 mag")
        ax.set_title(f"{b} band, {sname}", loc="left")
        ax.legend(loc="upper left", fontsize=7, title=f"objects with usable {b} template / all in group", title_fontsize=7)
    fb = np.arange(14, 25.01, 0.5)
    ax = axes[1, 1]
    ans = d.ok_i & ~failed
    for (sname, sm), col in zip(scopes, (INK, BLUE, ORANGE)):
        s = ans & sm
        out[f"fraction_typed_vs_i_tmpl | {sname}"] = _fraction(ax, d.mag_i[s], typed[s], fb, col, f"{sname} ({int(s.sum()):,})")
    ax.set_ylim(0, None)
    ax.set_xlabel(r"$i_\mathrm{tmpl}$ (template PSF, AB)")
    ax.set_ylabel("fraction with a SIMBAD type within 1″")
    ax.set_title("Objects whose SIMBAD crossmatch was answered", loc="left")
    ax.legend(loc="upper right", fontsize=7)
    ax = axes[1, 2]
    gaia = d.gaia_name.notna()
    for name, m, c in groups[:2]:
        s = d.ok_i & m
        out[f"fraction_gaia_vs_i_tmpl | {name}"] = _fraction(ax, d.mag_i[s], gaia[s], fb, c, f"{name} ({int(s.sum()):,})")
    ax.set_ylim(0, 1.05)
    ax.set_xlabel(r"$i_\mathrm{tmpl}$ (template PSF, AB)")
    ax.set_ylabel("fraction with a Gaia DR3 source within 1″")
    ax.set_title("Gaia DR3 counterparts (Fink crossmatch)", loc="left")
    ax.legend(loc="lower left", fontsize=7)
    fig.suptitle("DIA objects with and without a SIMBAD counterpart (Fink crossmatch within 1″): brightness on the template", fontsize=10)
    res["P3_simbad_magnitudes"] = out
    return _save(fig, figs_dir, "P3_simbad_matched_vs_unmatched", plt, record,
                 "Top and bottom left: template magnitudes of DIA objects with a SIMBAD type, with no SIMBAD object within "
                 "1 arcsec, and with a crossmatch that failed on every alert (normalised), for all fields, outside the bulge and "
                 "plane fields, and inside them. Bottom middle: fraction with a SIMBAD type against template i magnitude. Bottom "
                 "right: fraction with a Gaia DR3 source within 1 arcsec (a lower limit where Fink's Gaia crossmatch failed).")


# ============================================================================= transient colours
def transient_colours(src, tp, min_snr=TRANSIENT_MIN_SNR):
    """Colours of the change itself, from difference fluxes measured in g, r and i during the same night.

    Uses DIA alerts with positive difference flux and psfFlux / psfFluxErr >= min_snr. Night = floor(MJD - 0.5), a
    boundary at 12:00 UTC (daytime in Chile). For each object, night and band the inverse-variance-weighted mean
    difference flux is taken; object-nights with all three bands are kept. Each object contributes the night whose
    weakest band has the highest S/N (earliest night on ties). Returns (one row per object, all object-nights)."""
    d = src[(src.pop_ == "DIA") & src.band.isin(["g", "r", "i"]) & (src.psfFlux > 0)
            & (src.psfFlux / src.psfFluxErr >= min_snr)][["oid", "band", "mjd", "psfFlux", "psfFluxErr"]].copy()
    d["night"] = np.floor(d.mjd - 0.5).astype(int)
    d["w"] = 1.0 / d.psfFluxErr ** 2
    d["wf"] = d.w * d.psfFlux
    g = d.groupby(["oid", "night", "band"]).agg(sw=("w", "sum"), swf=("wf", "sum"), n=("w", "size"), t0=("mjd", "min"), t1=("mjd", "max"))
    g["flux"] = g.swf / g.sw
    g["err"] = 1.0 / np.sqrt(g.sw)
    w = g[["flux", "err", "n", "t0", "t1"]].unstack("band")
    w = w[w["flux"][["g", "r", "i"]].notna().all(axis=1)]
    on = pd.DataFrame(index=w.index)
    for b in "gri":
        on[f"flux_{b}"] = w[("flux", b)]
        on[f"err_{b}"] = w[("err", b)]
        on[f"n_alerts_{b}"] = w[("n", b)].astype(int)
        on[f"mag_{b}"] = AB_ZP_NJY - 2.5 * np.log10(on[f"flux_{b}"])
        on[f"snr_{b}"] = on[f"flux_{b}"] / on[f"err_{b}"]
    on["span_hours"] = (w["t1"][["g", "r", "i"]].max(axis=1) - w["t0"][["g", "r", "i"]].min(axis=1)) * 24.0
    on["min_snr"] = on[["snr_g", "snr_r", "snr_i"]].min(axis=1)
    on["gr"] = on.mag_g - on.mag_r
    on["ri"] = on.mag_r - on.mag_i
    on = on.reset_index()
    best = on.sort_values(["oid", "min_snr", "night"], ascending=[True, False, True]).drop_duplicates("oid")
    lab = tp.set_index("dia_object_id")[["cats_label", "simbad_group", "field", "gr", "ri", "panel_a"]].rename(
        columns={"gr": "gr_tmpl", "ri": "ri_tmpl", "panel_a": "has_gri_template"})
    best = best.join(lab, on="oid").rename(columns={"oid": "dia_object_id"})
    assert best.dia_object_id.is_unique and best.cats_label.notna().all()
    return best.reset_index(drop=True), on


def _tpanel(ax, base, sub, sl, label_sub):
    """Grey: density of `base` (columns gr, ri); red band: stellar locus; blue: `sub` (columns gr, ri)."""
    xr, yr = T_RANGES
    if base is not None and len(base):
        acd.background(ax, base, "gr", "ri", xr, yr)
    if sl is not None:
        import plot_colour_diagrams as kit
        kit.draw_locus(ax, sl, "gr", "ri")
    ins = sub.gr.between(*xr) & sub.ri.between(*yr)
    ax.scatter(sub.loc[ins, "gr"], sub.loc[ins, "ri"], s=7 if len(sub) < 3000 else 3, color=BLUE, edgecolors="white",
               linewidths=0.2, alpha=0.9, rasterized=True, zorder=4)
    off = int((~ins).sum())
    ax.text(0.03, 0.97, f"{label_sub}: n = {len(sub):,}" + (f" ({off} off-axis)" if off else ""), transform=ax.transAxes,
            va="top", ha="left", fontsize=7.5, color=INK, zorder=5)
    ax.set_xlim(*xr)
    ax.set_ylim(*yr)
    ax.set_xlabel(r"$(g-r)_\mathrm{diff}$ (same night)")
    ax.set_ylabel(r"$(r-i)_\mathrm{diff}$ (same night)")


def fig_t1_t3_transient_colours(tc, tp, locus_file, figs_dir, record, res):
    """T1: template colours; transient colours over template colours; time span of each g, r, i set.
    T2: transient colours by Fink CATS label; T3: by SIMBAD group. Galactic bulge and plane fields are excluded
    (no extinction correction)."""
    import plot_colour_diagrams as kit
    plt = _plt()
    sl = kit.load_locus(locus_file) if locus_file else None
    t = tc[~tc.field.isin(DUSTY)]
    static = tp[tp.panel_a & ~tp.field.isin(DUSTY)]
    res["T_counts"] = {"objects_all_fields": int(len(tc)), "objects_outside_bulge_plane": int(len(t)),
                       "objects_bulge_plane": int(tc.field.isin(DUSTY).sum()),
                       "objects_with_gri_template_too": int(t.has_gri_template.sum()),
                       "span_hours_q50_q90_max": [round(float(x), 2) for x in t.span_hours.quantile([0.5, 0.9])] + [round(float(t.span_hours.max()), 2)],
                       "min_snr_q50": round(float(t.min_snr.median()), 1),
                       "mag_r_q05_50_95": _q(t.mag_r), "gr_q05_50_95": _q(t.gr), "ri_q05_50_95": _q(t.ri),
                       "by_fink_label": {k: int(v) for k, v in t.cats_label.value_counts().items()},
                       "by_simbad_group": {k: int(v) for k, v in t.simbad_group.value_counts().items()},
                       "by_field": {k: int(v) for k, v in t.field.value_counts().items()}}

    fig, axes = plt.subplots(1, 3, figsize=(16, 4.8), constrained_layout=True)
    acd.background(axes[0], static, "gr", "ri", *T_RANGES)
    if sl is not None:
        kit.draw_locus(axes[0], sl, "gr", "ri")
    axes[0].text(0.03, 0.97, f"template colours: n = {len(static):,}", transform=axes[0].transAxes, va="top", fontsize=7.5, color=INK)
    axes[0].set_xlim(*T_RANGES[0])
    axes[0].set_ylim(*T_RANGES[1])
    axes[0].set_xlabel(r"$(g-r)_\mathrm{tmpl}$")
    axes[0].set_ylabel(r"$(r-i)_\mathrm{tmpl}$")
    axes[0].set_title("Static sky: template colours", loc="left")
    _tpanel(axes[1], static, t, sl, "transients (blue)")
    axes[1].set_title("The change: same-night difference-flux colours (blue)\nover the template colours (grey)", loc="left")
    axes[2].hist(t.span_hours, bins=np.arange(0, max(6.0, np.ceil(t.span_hours.max())) + 0.01, 0.25), color=BLUE)
    axes[2].set_xlabel("time from first to last g, r, i alert used (hours)")
    axes[2].set_ylabel("objects")
    axes[2].set_title("How close in time the three bands are", loc="left")
    fig.suptitle("r−i vs g−r of the transient light (difference fluxes > 0 with S/N ≥ 5 in g, r and i in one night), outside bulge "
                 "and plane fields; red band: stellar locus ± 2σ", fontsize=9.5)
    paths = [_save(fig, figs_dir, "T1_transient_colours", plt, record,
                   "Left: template (static-sky) colours. Middle: colours of the change, from inverse-variance-weighted nightly "
                   "mean difference fluxes in g, r and i from the same night (one point per object: the night whose weakest band "
                   "has the highest S/N), over the template colours. Right: time span of the alerts used.")]

    for col, order, name, title in (
            ("cats_label", [acd.CATS_LABELS[k] for k in (11, 12, 13, 21, 22, -1)], "T2_transient_colours_by_fink_label",
             "Fink CATS label at the latest alert"),
            ("simbad_group", list(SIMBAD_TYPED) + [acd.NO_MATCH, acd.XM_FAILED], "T3_transient_colours_by_simbad_group",
             "SIMBAD group (Fink crossmatch within 1″)")):
        rows = [g for g in order if (t[col] == g).any()]
        ncol = 3
        nrow = int(np.ceil(len(rows) / ncol))
        fig, axes = plt.subplots(nrow, ncol, figsize=(15, 4.4 * nrow), constrained_layout=True, squeeze=False)
        for ax, g in zip(axes.ravel(), rows):
            _tpanel(ax, t, t[t[col] == g], sl, "this group (blue)")
            ax.set_title(g, loc="left", fontsize=9, fontweight="bold")
        for ax in axes.ravel()[len(rows):]:
            ax.axis("off")
        fig.suptitle(f"Same-night difference-flux colours by {title} (blue) over all such transients (grey); red band: stellar locus ± 2σ",
                     fontsize=9.5)
        paths.append(_save(fig, figs_dir, name, plt, record, f"Transient (difference-flux) colours split by {title}."))
    return paths


# ============================================================================= counts for the paper figures
def paper_figure_counts(export_table, tp):
    """Objects per panel and per row of figures L3 (by SIMBAD group) and L4 (by Fink label) of notebook 04, which exclude
    bulge and plane fields, and of figures 2 (by Fink label) and 3 (by SIMBAD group) of notebook 01, which use all fields.
    Rows of L3/L4 are in the kit's order (most populous first)."""
    import plot_colour_diagrams as kit
    d = kit.load_data(export_table) if not isinstance(export_table, pd.DataFrame) else export_table
    d = d[~d.field.isin(DUSTY)]
    rows = []
    for fig, col in (("L3 (SIMBAD group), outside bulge/plane", "simbad_group"), ("L4 (Fink label), outside bulge/plane", "fink_cats_label")):
        for g in [None] + list(d[col].dropna().value_counts().index):
            s = d if g is None else d[d[col] == g]
            rows.append({"figure": fig, "row": "all (grey)" if g is None else g, "u-g vs g-r": int(s.has_ugr.sum()),
                         "g-r vs r-i": int(s.has_gri.sum()), "r-i vs i-z": int(s.has_riz.sum()), "i-z vs z-y": int(s.has_izy.sum())})
    for fig, col, order in (("Fig. 2 (Fink label), all fields", "cats_label", [acd.CATS_LABELS[k] for k in (11, 12, 13, 21, 22, -1)]),
                            ("Fig. 3 (SIMBAD group), all fields", "simbad_group", list(SIMBAD_TYPED) + [acd.NO_MATCH, acd.XM_FAILED])):
        for g in [None] + order:
            s = tp if g is None else tp[tp[col] == g]
            rows.append({"figure": fig, "row": "all (grey)" if g is None else g, "r vs g-i; r-i vs g-r": int(s.panel_a.sum()),
                         "g-r vs u-g": int(s.panel_b.sum())})
    return pd.DataFrame(rows)

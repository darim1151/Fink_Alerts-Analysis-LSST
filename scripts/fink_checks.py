#!/usr/bin/env python
"""Independent checks and follow-up measurements for the five-window Fink LSST analysis.

Each function is read-only and returns a JSON-serialisable dict. They are used by
notebooks/science/05_checks_and_audits.ipynb and reproduce, from one place, the checks run during
review (2026-10-08 to 2026-10-13):

  raw_template_rederivation   template magnitudes, panel samples and labels recomputed from raw Parquet with pandas
  crossmatch_failure_stats    Fink SIMBAD crossmatch: matched / no match / "Fail" per alert, broker version and night
  rr_lyrae_and_label_checks   known RR Lyrae, what the "Periodic" label contains, u-band reference populations, AGN shares
  rtn099_u_conversion         RR Lyrae u-g converted to SDSS with Rubin RTN-099 (LSSTComCam) relations
  magnitude_assembly_facts    how per-object multi-band magnitudes are assembled from single-band alerts
  gaia_locus_residuals        Gaia-parallax stars against the Rubin COSMOS stellar locus

Raw Parquet files are always the catalog's pinned file list (never a directory glob), so the checks follow
the cohort the catalog was built from.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.dataset as ds

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "colour_plot_kit"))
from fink_lsst.analytics.catalog import open_catalog  # noqa: E402

BANDS = ("u", "g", "r", "i", "z", "y")
AB_ZP_EXACT = 2.5 * np.log10(3631e9)  # 31.40007...; the analysis uses the rounded 31.4
SIMBAD_QSO_AGN = {"QSO", "AGN", "Sy1", "Sy2", "BLL", "Bla", "Q?", "AG?", "LIN", "Bz?", "BL?", "Sy?", "SyG"}
DUSTY = ("Galactic bulge", "Galactic plane")


def _q(s, ps=(0.16, 0.5, 0.84)):
    return None if len(s) == 0 else [round(float(x), 3) for x in s.quantile(list(ps))]


def pinned_raw_files(catalog, data_root):
    """The catalog's pinned raw Parquet files (stat fingerprints verified by open_catalog)."""
    conn = open_catalog(catalog, data_root)
    files = [r[0] for r in conn.execute("SELECT DISTINCT raw_file FROM sources ORDER BY 1").fetchall()]
    conn.close()
    return files


# ============================================================================= 1. raw re-derivation
def raw_template_rederivation(raw_files, template_table):
    """Recompute per-object template magnitudes from the raw alerts with pandas (no catalog views, no SQL) and
    compare with the template-photometry table produced by the colour step."""
    t = ds.dataset(raw_files, format="parquet").to_table(
        columns=["diaObjectId", "diaSourceId", "band", "templateFlux", "templateFluxErr", "midpointMjdTai", "ra", "pred",
                 "fink_broker_version"])
    pred = t.column("pred").combine_chunks()
    a = pd.DataFrame({"oid": t.column("diaObjectId").to_numpy(), "sid": t.column("diaSourceId").to_numpy(),
                      "band": t.column("band").to_pandas(), "mjd": t.column("midpointMjdTai").to_numpy(),
                      "ra": t.column("ra").to_numpy(),
                      "tf": t.column("templateFlux").to_numpy(zero_copy_only=False).astype(float),
                      "tfe": t.column("templateFluxErr").to_numpy(zero_copy_only=False).astype(float),
                      "is_sso": pred.field("is_sso").to_pandas(), "cats": pred.field("main_label_classifier").to_pandas(),
                      "is_first": pred.field("is_first").to_pandas(), "ver": t.column("fink_broker_version").to_pandas()})
    del t, pred
    r = {"rows": int(len(a)), "unique_source_ids": int(a.sid.nunique())}
    dia_m = (a.is_sso == False) & (a.oid > 0)  # noqa: E712
    sso_m = (a.is_sso == True) & ((a.oid == 0) | a.oid.isna())  # noqa: E712
    r.update({"dia": int(dia_m.sum()), "sso": int(sso_m.sum()), "ambiguous": int(len(a) - dia_m.sum() - sso_m.sum())})
    # Fink CATS code -1 ("not processed") should coincide with pred.is_first for DIA alerts; SSO alerts are all -1.
    d_, s_ = a[dia_m], a[sso_m]
    r["dia_alerts_where_cats_minus1_differs_from_is_first"] = int(((d_.cats == -1) != (d_.is_first == True)).sum())  # noqa: E712
    r["sso_alerts_with_cats_minus1"] = int((s_.cats == -1).sum())
    r["cats_codes_by_broker_version"] = {str(k): {str(c): int(n) for c, n in v.items()}
                                         for k, v in a.groupby("ver").cats.value_counts().unstack(fill_value=0).to_dict(orient="index").items()}
    dia = a[dia_m]
    r["dia_objects"] = int(dia.oid.nunique())
    span = dia.groupby("oid").ra.agg(lambda v: v.max() - v.min())
    r["objects_with_ra_span_gt_180deg"] = int((span > 180).sum())

    fin = dia[np.isfinite(dia.tf) & np.isfinite(dia.tfe)]
    m = fin.groupby(["oid", "band"]).agg(tf=("tf", "median"), tfe=("tfe", "median")).reset_index()
    m = m[(m.tf > 0) & (m.tfe > 0) & (m.tf / m.tfe >= 5)]
    m["mag"] = AB_ZP_EXACT - 2.5 * np.log10(m.tf)
    w = m.pivot(index="oid", columns="band", values="mag")
    for b in BANDS:
        if b not in w:
            w[b] = np.nan
    r["usable_objects_per_band"] = {b: int(w[b].notna().sum()) for b in BANDS}
    gri = w.g.notna() & w.r.notna() & w.i.notna()
    ugr = w.u.notna() & w.g.notna() & w.r.notna()
    r.update({"gri_objects": int(gri.sum()), "ugr_objects": int(ugr.sum()), "gri_or_ugr_objects": int((gri | ugr).sum())})

    tt = pd.read_parquet(template_table).set_index("dia_object_id")
    r["table_rows"] = int(len(tt))
    r["table_unique_ids"] = bool(tt.index.is_unique)
    diffs, presence = {}, {}
    for b in BANDS:
        j = w[[b]].join(tt[[f"mag_{b}"]], how="outer")
        both = j[b].notna() & j[f"mag_{b}"].notna()
        presence[b] = int((j[b].notna() ^ j[f"mag_{b}"].notna()).sum())
        diffs[b] = float((j.loc[both, b] - j.loc[both, f"mag_{b}"]).abs().max()) if both.any() else None
    r["max_abs_mag_difference_by_band"] = diffs  # expected ~6.6e-5: exact vs rounded AB zero point
    r["band_presence_mismatches"] = presence
    r["panel_a_mismatches"] = int((tt.panel_a != gri.reindex(tt.index, fill_value=False)).sum())
    r["panel_b_mismatches"] = int((tt.panel_b != ugr.reindex(tt.index, fill_value=False)).sum())
    latest = dia.sort_values(["oid", "mjd", "sid"]).groupby("oid").cats.last()
    r["latest_cats_mismatches"] = int((latest.reindex(tt.index).fillna(-999).astype(int)
                                       != tt.cats_class_latest.fillna(-999).astype(int)).sum())
    return r


# ============================================================================= 2. crossmatch failures
def crossmatch_failure_stats(raw_files):
    """Fink's SIMBAD crossmatch per DIA alert: a type (match), null (the CDS service answered, nothing within 1 arcsec)
    or "Fail" (the request errored, so SIMBAD status is unknown)."""
    t = ds.dataset(raw_files, format="parquet").to_table(columns=["diaObjectId", "midpointMjdTai", "fink_broker_version", "xm", "pred"])
    xm = t.column("xm").combine_chunks()
    a = pd.DataFrame({"oid": t.column("diaObjectId").to_numpy(), "mjd": t.column("midpointMjdTai").to_numpy(),
                      "ver": t.column("fink_broker_version").to_pandas(), "otype": xm.field("simbad_otype").to_pandas(),
                      "gaia": xm.field("gaiadr3_DR3Name").to_pandas(), "sso": t.column("pred").combine_chunks().field("is_sso").to_pandas()})
    del t, xm
    a = a[(a.sso == False) & (a.oid > 0)]  # noqa: E712
    a["state"] = np.where(a.otype == "Fail", "Fail", np.where(a.otype.isna(), "no match", "match"))
    a["night"] = np.floor(a.mjd).astype(int)
    r = {"alerts_by_state": a.state.value_counts().to_dict(),
         "alerts_by_state_and_broker_version": {str(k): v for k, v in a.groupby("ver").state.value_counts().unstack(fill_value=0)
                                                .to_dict(orient="index").items()}}
    nf = a.groupby("night").state.apply(lambda s: (s == "Fail").mean())
    r.update({"nights_total": int(len(nf)), "nights_with_any_fail": int((nf > 0).sum()), "nights_fail_fraction_gt_0.9": int((nf > 0.9).sum()),
              "gaia_also_fail_when_simbad_fail": round(float((a.gaia[a.state == "Fail"] == "Fail").mean()), 4)})
    states = a.groupby("oid").state.agg(lambda s: tuple(sorted(set(s))))
    r["objects_fail_and_match"] = int(states.apply(lambda s: "Fail" in s and "match" in s).sum())
    r["objects_fail_and_no_match"] = int(states.apply(lambda s: "Fail" in s and "no match" in s).sum())
    r["objects_only_fail"] = int(states.apply(lambda s: s == ("Fail",)).sum())
    return r


# ============================================================================= 3. RR Lyrae, labels, u band, AGN
def _with_labels(template_table, science_table):
    tp = pd.read_parquet(template_table, columns=["dia_object_id", "ok_u", "ok_g", "n_src_u", "magerr_u", "panel_a", "panel_b"])
    sci = pd.read_parquet(science_table)
    o = sci.drop(columns=["panel_a", "panel_b"]).merge(tp, on="dia_object_id", validate="one_to_one")
    v = o.vsx_type.where(o.vsx_type.notna(), "").astype(str).str.upper()
    s = o.simbad_otype
    o["xlabel"] = np.select([v.str.startswith("RR"), v != "", s.isin(SIMBAD_QSO_AGN), s.fillna("").str.contains(r"\*", regex=True), s.notna()],
                            ["VSX RR Lyrae", "VSX other variable", "SIMBAD QSO/AGN", "SIMBAD star", "SIMBAD other"], "no SIMBAD/VSX label")
    o["is_rrl"] = v.str.replace(":", "", regex=False).str.startswith("RR") | s.isin(["RR*", "RR?"])
    return o


def rr_lyrae_and_label_checks(template_table, science_table):
    """Known RR Lyrae (VSX RR* types or SIMBAD RR*/RR?), the content of Fink's "Periodic" label in the u-band panel,
    u-band reference populations, and the share of AGN in each sample. Labels: VSX > SIMBAD precedence."""
    o = _with_labels(template_table, science_table)
    ug_ok = o.ok_u & o.ok_g
    rr, ugr = o.is_rrl, o.panel_b
    r = {"rr_lyrae_objects": int(rr.sum()), "rr_lyrae_with_u_g_r": int((rr & ugr).sum()), "rr_lyrae_with_g_r_i": int((rr & o.panel_a).sum()),
         "rr_lyrae_ug_q16_50_84": _q((o.mag_u - o.mag_g)[rr & ug_ok]),
         "rr_lyrae_gr_q16_50_84": _q(o.gr[rr & o.mag_g.notna() & o.mag_r.notna()]),
         "rr_lyrae_by_field": o[rr].field.value_counts().to_dict(),
         "rr_lyrae_cats_latest": {str(k): int(n) for k, n in o[rr].cats.value_counts().items()}}
    per = (o.cats == 21) & ugr
    r["periodic_in_ugr_panel"] = int(per.sum())
    r["periodic_in_ugr_panel_labels"] = o[per].xlabel.value_counts().to_dict()
    r["periodic_in_ugr_panel_vsx_types"] = o[per & o.vsx_type.notna()].vsx_type.value_counts().to_dict()
    qso = o.simbad_otype.isin(["QSO", "AGN", "Sy1", "Sy2", "BLL", "Bla", "Q?", "AG?"]) & ugr
    star = o.simbad_otype.fillna("").str.contains(r"\*", regex=True) & ugr
    gaia = ugr & (o.plx / o.eplx > 5)
    for name, m in (("simbad_qso_agn", qso), ("simbad_stars", star), ("gaia_parallax_snr_gt5", gaia), ("all_ugr", ugr)):
        d = o[m]
        r[f"ugr_reference_{name}"] = {"n": int(len(d)), "ug_q16_50_84": _q(d.ug), "gr_q16_50_84": _q(d.gr),
                                      "frac_ug_lt_0.6": None if not len(d) else round(float((d.ug < 0.6).mean()), 3)}
    d = o[gaia | star]
    bins = [-0.2, 0.2, 0.4, 0.6, 0.8, 1.0, 1.2, 1.6]
    r["stars_ug_median_by_gr_bin"] = {str(k): [int(len(g)), None if not len(g) else round(float(g.ug.median()), 3)]
                                      for k, g in d.groupby(pd.cut(d.gr, bins), observed=True)}
    r["ugr_panel_by_field"] = o[ugr].field.value_counts().to_dict()
    for name, m in (("all", np.ones(len(o), bool)), ("gri_panel", o.panel_a), ("ugr_panel", ugr),
                    ("deep_drilling", o.field == "Deep drilling fields"), ("not_deep_drilling", o.field != "Deep drilling fields")):
        d = o[m]
        r[f"agn_share_{name}"] = {"n": int(len(d)), "fink_cats_agn": int((d.cats == 22).sum()),
                                  "simbad_qso_agn": int((d.xlabel == "SIMBAD QSO/AGN").sum()),
                                  "simbad_star_or_vsx": int(d.xlabel.isin(["SIMBAD star", "VSX RR Lyrae", "VSX other variable"]).sum()),
                                  "simbad_matched": int(d.simbad_otype.notna().sum())}
    r["fink_cats_agn_objects_by_label"] = o[o.cats == 22].xlabel.value_counts().to_dict()
    return r


# ============================================================================= 4. RTN-099 u-band conversion
def rtn099_u_conversion(template_table, science_table):
    """RR Lyrae (u-g) on the LSST system converted to SDSS-equivalent with Rubin RTN-099 (rtn-099.lsst.io):
    section 1.2.3 synthetic (Pickles) LSSTComCam->SDSS u relation in (g-i), and section 1.3.5 observed DR18 g relation
    (valid 0.2 < g-i <= 3.0). Relations are for LSSTComCam, not LSSTCam."""
    def du(x):  # u_sdss - u_ComCam
        return np.where(x <= -0.4, 0.587 * x**2 + 1.424 * x + 0.758,
                        np.where(x <= 0.8, 0.063 * x**2 - 0.192 * x + 0.263, -0.003 * x**2 + 0.174 * x + 0.059))

    def dg(x):  # g_sdss - g_ComCam
        return 0.066 * x - 0.006

    o = _with_labels(template_table, science_table)
    q = o[o.is_rrl & o.panel_b].copy()
    q["gi"] = q.mag_g - q.mag_i
    q["ug_lsst"] = q.mag_u - q.mag_g
    qq = q[q.gi.notna()].copy()
    qq["ug_sdss_est"] = qq.ug_lsst + du(qq.gi) - dg(qq.gi)
    return {"rr_lyrae_with_u_g_r": int(len(q)), "with_i_band": int(len(qq)),
            "with_gi_in_g_relation_range": int(((qq.gi > 0.2) & (qq.gi <= 3.0)).sum()),
            "gi_q16_50_84": _q(qq.gi), "ug_lsst_q16_50_84": _q(qq.ug_lsst), "ug_sdss_estimate_q16_50_84": _q(qq.ug_sdss_est),
            "mean_conversion_offset": None if not len(qq) else round(float((qq.ug_sdss_est - qq.ug_lsst).mean()), 3),
            "per_object": qq[["dia_object_id", "vsx_type", "simbad_otype", "mag_u", "mag_g", "mag_r", "mag_i", "gi", "ug_lsst",
                              "ug_sdss_est"]].round(4).to_dict(orient="records")}


# ============================================================================= 5. magnitude assembly
def magnitude_assembly_facts(catalog, data_root, template_table):
    """For objects in the g, r, i panels: alerts per band, same-night g/r/i coverage (Chile nights: boundary at
    12:00 UTC, i.e. floor(MJD - 0.5)), days between first g, r and i alerts, and template-flux spread across alerts."""
    v = pd.read_parquet(template_table)
    gri = v[v.panel_a]
    conn = open_catalog(catalog, data_root)
    conn.register("ids", gri[["dia_object_id"]])
    s = conn.execute("""SELECT s.dia_object_id, s.band, s.observation_mjd_tai AS t FROM sources s JOIN ids USING (dia_object_id)
                        WHERE s.population = 'DIA' AND s.band IN ('g', 'r', 'i')""").df()
    conn.close()
    r = {"gri_objects": int(len(gri))}
    for b in "gri":
        n = gri[f"n_finite_{b}"]
        r[f"alerts_per_object_{b}_q50_q90_max"] = [int(n.median()), int(n.quantile(0.9)), int(n.max())]
        r[f"objects_with_single_{b}_alert"] = int((n == 1).sum())
    s["night"] = np.floor(s.t - 0.5).astype(int)
    nb = s.groupby(["dia_object_id", "night"]).band.agg(lambda x: "".join(sorted(set(x))))
    r["objects_with_g_r_i_alerts_in_one_night"] = int(nb.groupby("dia_object_id").apply(
        lambda x: bool(np.any(x.str.contains("g").values & x.str.contains("r").values & x.str.contains("i").values))).sum())
    first = s.groupby(["dia_object_id", "band"]).t.min().unstack()
    span = first.max(axis=1) - first.min(axis=1)
    r["days_between_first_g_r_i_alerts_q50_q90"] = [round(float(span.quantile(0.5)), 2), round(float(span.quantile(0.9)), 2)]
    for b in "gri":
        m = gri[f"n_finite_{b}"] >= 2
        rel = (gri.loc[m, f"tf_max_{b}"] - gri.loc[m, f"tf_min_{b}"]) / gri.loc[m, f"tf_{b}"]
        r[f"template_flux_rel_range_{b}_q50_q90"] = [round(float(rel.quantile(0.5)), 3), round(float(rel.quantile(0.9)), 3)]
    return r


# ============================================================================= 6. stellar-locus residuals
def gaia_locus_residuals(export_table, locus_file):
    """Gaia-parallax stars (S/N > 5, outside bulge and plane fields) minus the stellar locus interpolated at each star's
    own g-i, per adjacent colour. The locus is read with the plot kit's reader."""
    from plot_colour_diagrams import load_locus
    d = pd.read_csv(export_table) if not isinstance(export_table, pd.DataFrame) else export_table
    d = d[~d.field.isin(DUSTY)]
    L = load_locus(locus_file)
    gi = d.mag_g - d.mag_i
    stars = d[(d.gaia_parallax_mas / d.gaia_parallax_err_mas > 5) & gi.between(L["g-i"].min(), L["g-i"].max())].copy()
    stars["gi"] = stars.mag_g - stars.mag_i
    r = {"locus_rows": int(len(L)), "stars_with_g_i_in_locus_range": int(len(stars))}
    pairs = {"u-g": ("mag_u", "mag_g"), "g-r": ("mag_g", "mag_r"), "r-i": ("mag_r", "mag_i"), "i-z": ("mag_i", "mag_z"), "z-y": ("mag_z", "mag_y")}
    for c, (a, b) in pairs.items():
        s = stars.dropna(subset=[a, b])
        loc = np.interp(s.gi, L["g-i"], L[c])
        sig = np.interp(s.gi, L["g-i"], L[c[0] + c[2] + "Sig"])
        res = (s[a] - s[b]) - loc
        q25, q75 = np.percentile(res, [25, 75])
        r[c] = {"n": int(len(s)), "median_offset_mag": round(float(np.median(res)), 3), "robust_sigma_mag": round(float(0.741 * (q75 - q25)), 3),
                "median_locus_sigma_mag": round(float(np.median(sig)), 3), "frac_within_2_locus_sigma": round(float((np.abs(res) <= 2 * sig).mean()), 3)}
    for name, m in (("g-i<1.5", stars.gi < 1.5), ("g-i>=1.5", stars.gi >= 1.5)):
        s = stars[m].dropna(subset=["mag_r"])
        res = (s.mag_g - s.mag_r) - np.interp(s.gi, L["g-i"], L["g-r"])
        q25, q75 = np.percentile(res, [25, 75])
        r[f"g-r_{name}"] = {"n": int(len(s)), "median_offset_mag": round(float(np.median(res)), 3), "robust_sigma_mag": round(float(0.741 * (q75 - q25)), 3)}
    return r

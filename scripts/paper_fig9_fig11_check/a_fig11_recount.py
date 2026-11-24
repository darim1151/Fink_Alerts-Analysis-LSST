"""Fig. 11 (template colours by EDP2 r_extendedness): independent recount on Arnor, read-only.

Run:  ssh arnor '<py> -B -' < a_fig11_recount.py > a_fig11.zip
Everything is computed in memory; the only output is a ZIP written to standard output.

The selection is rebuilt from the raw DIA alerts (cat.sources) and the EDP2 match table, without reading the
published template-photometry or science tables. Those tables are then compared with the rebuild.
"""
import io
import json
import sys
import zipfile

import duckdb
import numpy as np
import pandas as pd

ROOT = '/astro/store/shire/FINK'
CAT = f'{ROOT}/data/processed/final-five-window-analytics-20261006-v1/analytics.duckdb'
MATCH = f'{ROOT}/outputs/edp2-crossmatch-20261116-v1/edp2_match_one_per_object.parquet'
RUN = f'{ROOT}/outputs/notebooks-paper-20261015-v2'
TP = f'{RUN}/01_template_colours/tables/dia_object_template_photometry.parquet'
SCI = f'{RUN}/02_science_suite/tables/dia_object_science_table.parquet'
DUSTY = ('Galactic bulge', 'Galactic plane')


def log(*a):
    print(*a, file=sys.stderr, flush=True)


db = duckdb.connect(':memory:', config={'threads': 8, 'memory_limit': '24GB', 'temp_directory': ''})
db.execute(f"ATTACH '{CAT}' AS cat (READ_ONLY)")
db.execute(f"CREATE VIEW m AS FROM read_parquet('{MATCH}')")
db.execute(f"CREATE VIEW p AS FROM read_parquet('{TP}')")
db.execute(f"CREATE VIEW s AS FROM read_parquet('{SCI}')")
q = lambda sql: db.execute(sql).fetchone()
out = {}

# ---------------------------------------------------------------- 1. template photometry rebuilt from raw alerts
db.execute("""CREATE TEMP TABLE pb AS
SELECT dia_object_id, band, count(*) AS n_alerts,
       median(template_flux) FILTER (WHERE isfinite(template_flux) AND isfinite(template_flux_err)) AS tf,
       median(template_flux_err) FILTER (WHERE isfinite(template_flux) AND isfinite(template_flux_err)) AS tfe
FROM cat.sources WHERE population = 'DIA' GROUP BY 1, 2""")
n_dia, n_src = q("SELECT count(DISTINCT dia_object_id), sum(n_alerts) FROM pb")
assert (n_dia, n_src) == (3487175, 6311364), (n_dia, n_src)
piv = ",\n".join(
    f"""max(n_alerts) FILTER (WHERE band='{b}') AS n_{b},
       max(tf) FILTER (WHERE band='{b}') AS tf_{b}, max(tfe) FILTER (WHERE band='{b}') AS tfe_{b},
       coalesce(bool_or(tf > 0 AND tfe > 0 AND tf / tfe >= 5) FILTER (WHERE band='{b}'), false) AS ok_{b}"""
    for b in 'ugri')
db.execute(f"CREATE TEMP TABLE o AS SELECT dia_object_id, {piv} FROM pb GROUP BY 1")
assert q("SELECT count(*) FROM o")[0] == 3487175

# ---------------------------------------------------------------- 2. EDP2 morphology of the nearest Object within 1"
db.execute("""CREATE TEMP TABLE e AS SELECT dia_object_id, field, sep_arcsec, matched_1arcsec,
  coalesce(sep_arcsec <= 1.0, false) AS matched,
  CASE WHEN coalesce(sep_arcsec <= 1.0, false) AND r_extendedness IN (0, 1)
            AND NOT coalesce(r_extendedness_flag, true) THEN r_extendedness::INT END AS cls,
  u_psfFlux, u_psfFluxErr, u_psfFlux_flag, g_psfFlux, g_psfFluxErr, g_psfFlux_flag,
  r_psfFlux, r_psfFluxErr, r_psfFlux_flag, i_psfFlux, i_psfFluxErr, i_psfFlux_flag
FROM m""")
assert q("SELECT count(*) = 3487175 AND count(*) FILTER (WHERE matched IS DISTINCT FROM matched_1arcsec) = 0 FROM e")[0]

# ---------------------------------------------------------------- 3. the Fig. 11 selection and every number in it
db.execute("""CREATE TEMP TABLE f AS SELECT o.*, e.field, e.sep_arcsec, e.matched, e.cls,
  (ok_g AND ok_r AND ok_i) AS gri, (ok_u AND ok_g AND ok_r) AS ugr,
  CASE WHEN ok_u THEN 31.4 - 2.5 * log10(tf_u) END AS tu, CASE WHEN ok_g THEN 31.4 - 2.5 * log10(tf_g) END AS tg,
  CASE WHEN ok_r THEN 31.4 - 2.5 * log10(tf_r) END AS tr, CASE WHEN ok_i THEN 31.4 - 2.5 * log10(tf_i) END AS ti,
  e.u_psfFlux, e.u_psfFluxErr, e.u_psfFlux_flag, e.g_psfFlux, e.g_psfFluxErr, e.g_psfFlux_flag,
  e.r_psfFlux, e.r_psfFluxErr, e.r_psfFlux_flag, e.i_psfFlux, e.i_psfFluxErr, e.i_psfFlux_flag
FROM o JOIN e USING (dia_object_id) WHERE (ok_g AND ok_r AND ok_i) OR (ok_u AND ok_g AND ok_r)""")
c = {}
c['template_gri_or_ugr_all_objects'] = q("SELECT count(*) FROM f")[0]
c['template_gri_all_objects'] = q("SELECT count(*) FROM f WHERE gri")[0]
c['template_ugr_all_objects'] = q("SELECT count(*) FROM f WHERE ugr")[0]
c['by_match'] = dict(db.execute("""SELECT CASE WHEN NOT matched THEN 'no EDP2 Object within 1 arcsec'
   WHEN cls IS NULL THEN 'matched, r_extendedness unusable' WHEN cls = 0 THEN 'matched, unresolved'
   ELSE 'matched, resolved' END, count(*) FROM f GROUP BY 1 ORDER BY 1""").fetchall())
c['fig11_total'] = q("SELECT count(*) FROM f WHERE cls IS NOT NULL")[0]
c['fig11_unresolved'] = q("SELECT count(*) FROM f WHERE cls = 0")[0]
c['fig11_resolved'] = q("SELECT count(*) FROM f WHERE cls = 1")[0]
c['fig11_gri'] = q("SELECT count(*) FROM f WHERE cls IS NOT NULL AND gri")[0]
c['fig11_ugr'] = q("SELECT count(*) FROM f WHERE cls IS NOT NULL AND ugr")[0]
c['fig11_gri_and_ugr'] = q("SELECT count(*) FROM f WHERE cls IS NOT NULL AND gri AND ugr")[0]
for lab, cond in (('gri', 'gri'), ('ugr', 'ugr')):
    c[f'fig11_{lab}_unresolved'] = q(f"SELECT count(*) FROM f WHERE cls = 0 AND {cond}")[0]
    c[f'fig11_{lab}_resolved'] = q(f"SELECT count(*) FROM f WHERE cls = 1 AND {cond}")[0]
c['fig11_by_field'] = {k: [int(a), int(b)] for k, a, b in db.execute(
    "SELECT field, count(*) FILTER (WHERE cls = 0), count(*) FILTER (WHERE cls = 1) FROM f WHERE cls IS NOT NULL GROUP BY 1 ORDER BY 1").fetchall()}
# how restrictive the template requirement is: alerts in each band, finite templates, S/N >= 5
c['objects_with_alerts_in_g_r_i'] = q("SELECT count(*) FROM o WHERE n_g > 0 AND n_r > 0 AND n_i > 0")[0]
c['objects_with_alerts_in_u_g_r'] = q("SELECT count(*) FROM o WHERE n_u > 0 AND n_g > 0 AND n_r > 0")[0]
c['objects_with_finite_template_g_r_i'] = q("SELECT count(*) FROM o WHERE tf_g IS NOT NULL AND tf_r IS NOT NULL AND tf_i IS NOT NULL")[0]
c['objects_with_template_ok_per_band'] = {b: q(f"SELECT count(*) FROM o WHERE ok_{b}")[0] for b in 'ugri'}
# morphology of every matched DIAObject (context for "many blue points")
c['all_matched_by_class'] = dict((str(k), v) for k, v in db.execute(
    "SELECT coalesce(cls::VARCHAR, 'unusable'), count(*) FROM e WHERE matched GROUP BY 1 ORDER BY 1").fetchall())
c['all_matched_alerts_gri_by_class'] = dict((str(k), v) for k, v in db.execute(
    "SELECT coalesce(cls::VARCHAR, 'unusable'), count(*) FROM e JOIN o USING (dia_object_id) WHERE matched AND n_g > 0 AND n_r > 0 AND n_i > 0 GROUP BY 1 ORDER BY 1").fetchall())
out['counts'] = c
log(json.dumps(c, indent=1, default=int))

# ---------------------------------------------------------------- 4. compare with the published tables that were plotted
fd = db.execute("SELECT * FROM f").df()
pub = db.execute("""SELECT p.dia_object_id, p.ok_u, p.ok_g, p.ok_r, p.ok_i, p.mag_u, p.mag_g, p.mag_r, p.mag_i, p.panel_a, p.panel_b,
  p.field AS field_tp, p.simbad_group, p.cats_label, p.n_sources,
  s.gi AS s_gi, s.gr AS s_gr, s.ri AS s_ri, s.ug AS s_ug, s.mag_r AS s_mag_r, s.panel_a AS s_panel_a, s.panel_b AS s_panel_b,
  s.plx, s.eplx, s.pstar
FROM p JOIN s USING (dia_object_id)""").df()
assert len(pub) == 3487175
chk = {}
sel_pub = set(pub.dia_object_id[pub.panel_a | pub.panel_b])
sel_new = set(fd.dia_object_id)
chk['gri_or_ugr_id_sets_identical'] = sel_pub == sel_new
j = fd.merge(pub, on='dia_object_id', how='left', validate='one_to_one')
for b, t in zip('ugri', ('tu', 'tg', 'tr', 'ti')):
    chk[f'ok_{b}_identical'] = bool((j[f'ok_{b}_x'] == j[f'ok_{b}_y'].astype(bool)).all())
    d = (j[t] - j[f'mag_{b}']).abs()
    chk[f'mag_{b}_max_abs_diff'] = float(d.max()) if d.notna().any() else 0.0
    chk[f'mag_{b}_nan_pattern_identical'] = bool((j[t].isna() == j[f'mag_{b}'].isna()).all())
for col, expr in (('s_gi', j.tg - j.ti), ('s_gr', j.tg - j.tr), ('s_ri', j.tr - j.ti), ('s_ug', j.tu - j.tg), ('s_mag_r', j.tr)):
    d = (expr - j[col]).abs()
    chk[f'{col}_max_abs_diff_vs_rebuild'] = float(d.max())
chk['field_labels_identical'] = bool((j.field == j.field_tp).all())
chk['panel_flags_identical'] = bool((j.gri == j.panel_a).all() and (j.ugr == j.panel_b).all() and (j.panel_a == j.s_panel_a).all())
out['checks_vs_published_tables'] = chk
log(json.dumps(chk, indent=1))
assert all(v is True or (isinstance(v, float) and v < 1e-9) for v in chk.values()), chk

# ---------------------------------------------------------------- 5. EDP2 PSF photometry of the same objects (for comparison)
for b in 'ugri':
    f_, e_ = j[f'{b}_psfFlux'].astype(float), j[f'{b}_psfFluxErr'].astype(float)
    ok = (f_ > 0) & (e_ > 0) & (f_ / e_ >= 5) & ~j[f'{b}_psfFlux_flag'].astype('boolean').fillna(True).astype(bool)
    j[f'eok_{b}'] = ok.to_numpy(bool)
    with np.errstate(divide='ignore', invalid='ignore'):
        j[f'e{b}'] = np.where(ok, 31.4 - 2.5 * np.log10(f_.where(ok)), np.nan)
with np.errstate(divide='ignore', invalid='ignore'):
    j['plx_snr'] = j.plx / j.eplx
keep = ['dia_object_id', 'field', 'sep_arcsec', 'matched', 'cls', 'gri', 'ugr', 'n_u', 'n_g', 'n_r', 'n_i', 'n_sources',
        'tu', 'tg', 'tr', 'ti', 'eu', 'eg', 'er', 'ei', 'eok_u', 'eok_g', 'eok_r', 'eok_i',
        'tf_u', 'tfe_u', 'tf_g', 'tfe_g', 'tf_r', 'tfe_r', 'tf_i', 'tfe_i',
        'simbad_group', 'cats_label', 'plx_snr', 'pstar']
tab = j[keep].sort_values('dia_object_id').reset_index(drop=True)

buf = io.BytesIO()
with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
    z.writestr('fig11_recount.json', json.dumps(out, indent=1, default=int) + '\n')
    pb_ = io.BytesIO()
    tab.to_parquet(pb_, index=False)
    z.writestr('fig11_template_objects.parquet', pb_.getvalue())
sys.stdout.buffer.write(buf.getvalue())

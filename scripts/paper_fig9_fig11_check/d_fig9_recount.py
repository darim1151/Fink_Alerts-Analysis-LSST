"""Fig. 9 (transient colours, four panels): independent recount on Arnor, read-only.

Run:  ssh arnor '<py> -B -' < d_fig9_recount.py > d_fig9.zip

Rebuilds every sample of the four-panel figure from the raw DIA alerts, the EDP2 match table and the EDP2 cModel
table, then compares with the per-object tables the figure was drawn from. Output: a ZIP on standard output.
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
X = f'{ROOT}/outputs/edp2-crossmatch-20261116-v1'
RUN = f'{ROOT}/outputs/notebooks-paper-20261015-v2'
FIGRUN = f'{ROOT}/outputs/paper-figures-20261117-edp2-revision-v3'
XR, YR = (-1.25, 2.25), (-1.25, 2.75)


def log(*a):
    print(*a, file=sys.stderr, flush=True)


db = duckdb.connect(':memory:', config={'threads': 8, 'memory_limit': '24GB', 'temp_directory': ''})
db.execute(f"ATTACH '{CAT}' AS cat (READ_ONLY)")
db.execute(f"CREATE VIEW m AS FROM read_parquet('{X}/edp2_match_one_per_object.parquet')")
db.execute(f"CREATE VIEW c AS FROM read_parquet('{X}/edp2_cmodel_1arcsec.parquet')")
db.execute(f"CREATE VIEW p AS FROM read_parquet('{RUN}/01_template_colours/tables/dia_object_template_photometry.parquet')")
db.execute(f"CREATE VIEW tc AS FROM read_parquet('{RUN}/06_paper_additions/tables/transient_colours_per_object.parquet')")
q = lambda sql: db.execute(sql).fetchone()
res = {}

# field labels: the figure takes them from the template table; check the match table agrees everywhere
assert q("SELECT count(*) FROM p JOIN m USING (dia_object_id) WHERE p.field IS DISTINCT FROM m.field")[0] == 0
res['fields_all_objects'] = dict(db.execute("SELECT field, count(*) FROM p GROUP BY 1 ORDER BY 1").fetchall())

# ---------------------------------------------------------------- (a) EDP2 cModel colours of matched objects
ok = lambda b: f"(c.{b}_cModelFlux > 0 AND c.{b}_cModelFluxErr > 0 AND c.{b}_cModelFlux / c.{b}_cModelFluxErr >= 5 AND NOT coalesce(c.{b}_cModel_flag, true))"
db.execute(f"""CREATE TEMP TABLE a AS SELECT m.dia_object_id, p.field,
  CASE WHEN m.r_extendedness IN (0, 1) AND NOT coalesce(m.r_extendedness_flag, true) THEN m.r_extendedness::INT END AS cls,
  {ok('g')} AS okg, {ok('r')} AS okr, {ok('i')} AS oki,
  CASE WHEN {ok('g')} AND {ok('r')} THEN -2.5 * log10(c.g_cModelFlux / c.r_cModelFlux) END AS gr,
  CASE WHEN {ok('r')} AND {ok('i')} THEN -2.5 * log10(c.r_cModelFlux / c.i_cModelFlux) END AS ri
FROM m JOIN p USING (dia_object_id) LEFT JOIN c ON c.dia_object_id = m.dia_object_id
WHERE coalesce(m.sep_arcsec <= 1.0, false)""")
A = {}
A['matched_within_1arcsec'] = q("SELECT count(*) FROM a")[0]
assert A['matched_within_1arcsec'] == 2765151
A['matched_with_cmodel_row'] = q("SELECT count(*) FROM a JOIN c USING (dia_object_id)")[0]
A['outside_bulge_plane'] = q("SELECT count(*) FROM a WHERE field NOT IN ('Galactic bulge', 'Galactic plane')")[0]
base = "field NOT IN ('Galactic bulge', 'Galactic plane')"
A['plus_g'] = q(f"SELECT count(*) FROM a WHERE {base} AND okg")[0]
A['plus_g_r'] = q(f"SELECT count(*) FROM a WHERE {base} AND okg AND okr")[0]
A['panel_a_N'] = q(f"SELECT count(*) FROM a WHERE {base} AND okg AND okr AND oki")[0]
A['panel_a_by_class'] = {str(k): v for k, v in db.execute(
    f"SELECT coalesce(cls::VARCHAR, 'unusable'), count(*) FROM a WHERE {base} AND okg AND okr AND oki GROUP BY 1 ORDER BY 1").fetchall()}
A['panel_a_by_field'] = dict(db.execute(
    f"SELECT field, count(*) FROM a WHERE {base} AND okg AND okr AND oki GROUP BY 1 ORDER BY 1").fetchall())
A['panel_a_in_frame'] = q(f"""SELECT count(*) FROM a WHERE {base} AND okg AND okr AND oki
  AND gr BETWEEN {XR[0]} AND {XR[1]} AND ri BETWEEN {YR[0]} AND {YR[1]}""")[0]
res['panel_a'] = A
log(json.dumps(A, indent=1))

# ---------------------------------------------------------------- (b) template colours of unmatched objects (rebuilt from raw)
db.execute("""CREATE TEMP TABLE tb AS
SELECT dia_object_id, band,
       median(template_flux) FILTER (WHERE isfinite(template_flux) AND isfinite(template_flux_err)) AS tf,
       median(template_flux_err) FILTER (WHERE isfinite(template_flux) AND isfinite(template_flux_err)) AS tfe
FROM cat.sources WHERE population = 'DIA' AND band IN ('g', 'r', 'i') GROUP BY 1, 2""")
db.execute("""CREATE TEMP TABLE tgri AS SELECT dia_object_id,
  max(31.4 - 2.5 * log10(tf)) FILTER (WHERE band = 'g') AS g, max(31.4 - 2.5 * log10(tf)) FILTER (WHERE band = 'r') AS r,
  max(31.4 - 2.5 * log10(tf)) FILTER (WHERE band = 'i') AS i
FROM tb WHERE tf > 0 AND tfe > 0 AND tf / tfe >= 5 GROUP BY 1 HAVING count(*) = 3""")
B = {}
B['template_gri_all'] = q("SELECT count(*) FROM tgri")[0]
B['template_gri_unmatched_all_fields'] = q("SELECT count(*) FROM tgri JOIN m USING (dia_object_id) WHERE NOT coalesce(m.sep_arcsec <= 1.0, false)")[0]
B['template_gri_outside_bulge_plane_all_match_states'] = q(
    "SELECT count(*) FROM tgri JOIN p USING (dia_object_id) WHERE p.field NOT IN ('Galactic bulge', 'Galactic plane')")[0]
B['panel_b_N'] = q("""SELECT count(*) FROM tgri JOIN m USING (dia_object_id) JOIN p USING (dia_object_id)
  WHERE NOT coalesce(m.sep_arcsec <= 1.0, false) AND p.field NOT IN ('Galactic bulge', 'Galactic plane')""")[0]
B['panel_b_ids_equal_published_rule'] = q("""SELECT count(*) = 0 FROM
  (SELECT dia_object_id FROM tgri JOIN m USING (dia_object_id) JOIN p USING (dia_object_id)
     WHERE NOT coalesce(m.sep_arcsec <= 1.0, false) AND p.field NOT IN ('Galactic bulge', 'Galactic plane')) x
  FULL JOIN (SELECT dia_object_id FROM p JOIN m USING (dia_object_id)
     WHERE p.panel_a AND NOT m.matched_1arcsec AND p.field NOT IN ('Galactic bulge', 'Galactic plane')) y USING (dia_object_id)
  WHERE x.dia_object_id IS NULL OR y.dia_object_id IS NULL""")[0]
B['panel_b_in_frame'] = q(f"""SELECT count(*) FROM tgri JOIN m USING (dia_object_id) JOIN p USING (dia_object_id)
  WHERE NOT coalesce(m.sep_arcsec <= 1.0, false) AND p.field NOT IN ('Galactic bulge', 'Galactic plane')
  AND g - r BETWEEN {XR[0]} AND {XR[1]} AND r - i BETWEEN {YR[0]} AND {YR[1]}""")[0]
B['panel_b_by_field'] = dict(db.execute("""SELECT p.field, count(*) FROM tgri JOIN m USING (dia_object_id) JOIN p USING (dia_object_id)
  WHERE NOT coalesce(m.sep_arcsec <= 1.0, false) AND p.field NOT IN ('Galactic bulge', 'Galactic plane') GROUP BY 1""").fetchall())
res['panel_b'] = B
log(json.dumps(B, indent=1))

# ---------------------------------------------------------------- (c), (d) same-night difference colours, rebuilt from raw alerts
d = db.execute("""SELECT dia_object_id AS oid, band, observation_mjd_tai AS mjd, psf_flux AS f, psf_flux_err AS e
FROM cat.sources WHERE population = 'DIA' AND band IN ('g', 'r', 'i') AND psf_flux > 0 AND psf_flux / psf_flux_err >= 5""").df()
d['night'] = np.floor(d.mjd - 0.5).astype(int)
d['w'] = 1.0 / d.e ** 2
d['wf'] = d.w * d.f
g = d.groupby(['oid', 'night', 'band']).agg(sw=('w', 'sum'), swf=('wf', 'sum'), n=('w', 'size'), t0=('mjd', 'min'), t1=('mjd', 'max'))
g['flux'] = g.swf / g.sw
g['err'] = 1.0 / np.sqrt(g.sw)
w = g[['flux', 'err', 'n', 't0', 't1']].unstack('band')
w = w[w['flux'][['g', 'r', 'i']].notna().all(axis=1)]
on = pd.DataFrame(index=w.index)
for b in 'gri':
    on[f'flux_{b}'] = w[('flux', b)]
    on[f'snr_{b}'] = w[('flux', b)] / w[('err', b)]
    on[f'n_{b}'] = w[('n', b)].astype(int)
    on[f'mag_{b}'] = 31.4 - 2.5 * np.log10(on[f'flux_{b}'])
on['span_hours'] = (w['t1'][['g', 'r', 'i']].max(axis=1) - w['t0'][['g', 'r', 'i']].min(axis=1)) * 24.0
on['min_snr'] = on[['snr_g', 'snr_r', 'snr_i']].min(axis=1)
on['gr'] = on.mag_g - on.mag_r
on['ri'] = on.mag_r - on.mag_i
on = on.reset_index()
best = on.sort_values(['oid', 'min_snr', 'night'], ascending=[True, False, True]).drop_duplicates('oid')
info = db.execute("""SELECT p.dia_object_id AS oid, p.field, coalesce(m.sep_arcsec <= 1.0, false) AS matched,
  CASE WHEN coalesce(m.sep_arcsec <= 1.0, false) AND m.r_extendedness IN (0, 1) AND NOT coalesce(m.r_extendedness_flag, true)
       THEN m.r_extendedness::INT END AS cls FROM p JOIN m USING (dia_object_id)""").df()
best = best.merge(info, on='oid', how='left', validate='one_to_one')
C = {}
C['object_nights_with_gri'] = int(len(on))
C['objects_all_fields'] = int(len(best))
C['objects_by_field'] = best.field.value_counts().to_dict()
t = best[~best.field.isin(['Galactic bulge', 'Galactic plane'])]
C['panel_c_N'] = int(len(t))
C['panel_c_object_nights_outside_bulge_plane'] = int(on.oid.isin(set(t.oid)).sum())
ins = t.gr.between(*XR) & t.ri.between(*YR)
C['panel_c_outside_frame'] = int((~ins).sum())
C['panel_c_by_match_class'] = {
    'unmatched': int((~t.matched).sum()), 'matched_unresolved': int((t.cls == 0).sum()),
    'matched_resolved': int((t.cls == 1).sum()), 'matched_unusable': int((t.matched & t.cls.isna()).sum())}
C['panel_c_by_field'] = t.field.value_counts().to_dict()
C['alerts_per_band_used_median'] = {b: float(t[f'n_{b}'].median()) for b in 'gri'}
C['objects_with_more_than_one_eligible_night'] = int(on[on.oid.isin(set(t.oid))].groupby('oid').size().gt(1).sum())
C['min_snr_range'] = [float(t.min_snr.min()), float(t.min_snr.max())]
C['diff_mag_range'] = {b: [float(t[f'mag_{b}'].min()), float(t[f'mag_{b}'].max())] for b in 'gri'}
C['span_hours'] = {'min': float(t.span_hours.min()), 'median': float(t.span_hours.median()), 'max': float(t.span_hours.max()),
                   'zero_span': int((t.span_hours == 0).sum()), 'lt_1h': int((t.span_hours < 1).sum()),
                   'lt_3h': int((t.span_hours < 3).sum()), 'ge_4h': int((t.span_hours >= 4).sum())}
top = max(6.0, float(np.ceil(t.span_hours.max())))
edges = np.arange(0, top + 0.01, 0.25)
C['panel_d_bins_hours'] = [float(edges[0]), float(edges[-1]), 0.25]
C['panel_d_counts'] = np.histogram(t.span_hours, edges)[0].tolist()
assert sum(C['panel_d_counts']) == C['panel_c_N']
# compare with the per-object table the figure was drawn from
pubt = db.execute("SELECT * FROM tc").df().rename(columns={'dia_object_id': 'oid'})
jj = best.merge(pubt, on='oid', how='outer', suffixes=('', '_pub'), indicator=True)
C['ids_identical_to_published_table'] = bool((jj._merge == 'both').all())
C['nights_identical'] = bool((jj.night == jj.night_pub).all())
C['max_abs_diff_gr'] = float((jj.gr - jj.gr_pub).abs().max())
C['max_abs_diff_ri'] = float((jj.ri - jj.ri_pub).abs().max())
C['max_abs_diff_span_hours'] = float((jj.span_hours - jj.span_hours_pub).abs().max())
C['published_table_rows_in_bulge_plane'] = int(pubt.field.isin(['Galactic bulge', 'Galactic plane']).sum())
res['panel_c_d'] = C
log(json.dumps(C, indent=1, default=str))

# published figure summary for the same run
with open(f'{FIGRUN}/figure_summary.json') as fh:
    res['published_figure_summary_fig11'] = json.load(fh)['fig11']

buf = io.BytesIO()
with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
    z.writestr('fig9_recount.json', json.dumps(res, indent=1, default=str) + '\n')
    pb_ = io.BytesIO()
    t[['oid', 'night', 'field', 'matched', 'cls', 'gr', 'ri', 'span_hours', 'min_snr', 'n_g', 'n_r', 'n_i',
       'mag_g', 'mag_r', 'mag_i']].rename(columns={'oid': 'dia_object_id'}).to_parquet(pb_, index=False)
    z.writestr('fig9_panel_c_objects.parquet', pb_.getvalue())
sys.stdout.buffer.write(buf.getvalue())

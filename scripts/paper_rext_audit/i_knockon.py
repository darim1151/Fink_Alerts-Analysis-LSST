"""CP5: how much of the 'resolved' (r_extendedness = 1) class in the paper's figures is point-like by the other two
EDP2 classifiers. Read-only on Arnor; ZIP to standard output.

    ssh arnor '<py> -B -' < i_knockon.py > i_knockon.zip

Fig. 11   template-colour sample (usable gri or ugr templates), 1 arcsec, usable r_extendedness, all fields
Figs. 13/14  flux ratio R = 1 + psfFlux/templateFlux per DIA alert, template S/N >= 5, R > 0, usable EDP2 cModel g and i,
          r_extendedness 0 (Fig. 13) or 1 (Fig. 14), all fields
The per-band alert counts are first reproduced against the N printed in the published panels, in SQL (B) and pandas (A).
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
# N values read from the panel labels of Figs. 13 and 14 in outputs/paper5/Paper_revised.pdf (pages 15-16)
PUBLISHED = {0: {'u': 12809, 'g': 238722, 'r': 501390, 'i': 1872460, 'z': 1081445, 'y': 1187},
             1: {'u': 6847, 'g': 101173, 'r': 145510, 'i': 443982, 'z': 191257, 'y': 230}}
res = {}


def log(*a):
    print(*a, file=sys.stderr, flush=True)


db = duckdb.connect(':memory:', config={'threads': 8, 'memory_limit': '24GB', 'temp_directory': ''})
db.execute(f"ATTACH '{CAT}' AS cat (READ_ONLY)")
db.execute(f"CREATE VIEW mm AS FROM read_parquet('{X}/edp2_match_one_per_object.parquet')")
db.execute(f"CREATE VIEW cc AS FROM read_parquet('{X}/edp2_cmodel_1arcsec.parquet')")
db.execute(f"CREATE VIEW p AS FROM read_parquet('{RUN}/01_template_colours/tables/dia_object_template_photometry.parquet')")
ok = lambda b: (f"(isfinite(cc.{b}_cModelFlux) AND isfinite(cc.{b}_cModelFluxErr) AND cc.{b}_cModelFlux > 0 AND cc.{b}_cModelFluxErr > 0 "
                f"AND cc.{b}_cModelFlux / cc.{b}_cModelFluxErr >= 5 AND NOT coalesce(cc.{b}_cModel_flag, true))")
db.execute(f"""CREATE TEMP TABLE o AS SELECT mm.dia_object_id, mm.field IN ('Galactic bulge', 'Galactic plane') AS dusty,
  CASE WHEN coalesce(mm.sep_arcsec <= 1, false) AND NOT coalesce(mm.r_extendedness_flag, true) AND mm.r_extendedness IN (0, 1)
       THEN mm.r_extendedness::INT ELSE -1 END AS cls,
  CASE WHEN coalesce(mm.sep_arcsec <= 1, false) AND isfinite(mm.griz_model_extendedness) THEN mm.griz_model_extendedness END AS G,
  CASE WHEN coalesce(mm.sep_arcsec <= 1, false) AND mm.r_psfFlux > 0 AND mm.r_cModelFlux > 0 AND isfinite(mm.r_psfFlux)
            AND isfinite(mm.r_cModelFlux) AND NOT coalesce(mm.r_cModel_flag, true) AND NOT coalesce(mm.r_psfFlux_flag, true)
       THEN -2.5 * log10(mm.r_psfFlux / mm.r_cModelFlux) END AS x,
  coalesce(mm.sep_arcsec <= 1, false) AND coalesce({ok('g')} AND {ok('i')}, false) AS gi_ok,
  p.panel_a OR p.panel_b AS tmpl_colour
FROM mm JOIN p USING (dia_object_id) LEFT JOIN cc USING (dia_object_id)""")
q = lambda sql: db.execute(sql).fetchall()

# ---------------------------------------------------------------- Fig. 11
f11 = {r[0]: dict(zip(('n', 'G<0.2', 'G<0.2 & x<0.02', 'G>0.8', 'G 0.2-0.8 or missing', 'bulge/plane'), r[1:])) for r in q(
    """SELECT cls, count(*), count(*) FILTER (WHERE G < 0.2), count(*) FILTER (WHERE G < 0.2 AND x < 0.02),
       count(*) FILTER (WHERE G > 0.8), count(*) FILTER (WHERE G IS NULL OR G BETWEEN 0.2 AND 0.8), count(*) FILTER (WHERE dusty)
       FROM o WHERE tmpl_colour AND cls >= 0 GROUP BY 1 ORDER BY 1""")}
assert f11[0]['n'] == 5374 and f11[1]['n'] == 2664, f11
res['fig11'] = f11
# what Fig. 11's numbers would be with griz_model_extendedness (G < 0.2 point-like, G > 0.8 extended)
res['fig11_with_G'] = dict(zip(('point G<0.2', 'extended G>0.8', 'middle or missing'), q(
    """SELECT count(*) FILTER (WHERE G < 0.2), count(*) FILTER (WHERE G > 0.8), count(*) FILTER (WHERE G IS NULL OR G BETWEEN 0.2 AND 0.8)
       FROM o WHERE tmpl_colour AND cls >= 0""")[0]))
log(json.dumps({k: res[k] for k in ('fig11', 'fig11_with_G')}, indent=1, default=int))

# ---------------------------------------------------------------- Figs. 13/14, alert level
db.execute("""CREATE TEMP TABLE a AS SELECT s.band, s.dia_object_id, o.cls, o.G, o.x, o.dusty
FROM cat.sources s JOIN o USING (dia_object_id)
WHERE s.population = 'DIA' AND s.template_flux > 0 AND s.template_flux_err > 0 AND s.template_flux / s.template_flux_err >= 5
  AND isfinite(s.template_flux) AND isfinite(s.template_flux_err) AND isfinite(s.psf_flux)
  AND 1 + s.psf_flux / s.template_flux > 0 AND o.gi_ok AND o.cls >= 0""")
per_band = {(int(k), b): n for k, b, n in q("SELECT cls, band, count(*) FROM a GROUP BY 1, 2")}
for k in (0, 1):
    for b, n in PUBLISHED[k].items():
        assert per_band.get((k, b), 0) == n, ('published N', k, b, per_band.get((k, b)), n)
# A: pandas recount of the per-band numbers from the raw alerts
al = db.execute("""SELECT band, dia_object_id, psf_flux, template_flux, template_flux_err FROM cat.sources WHERE population = 'DIA'""").df()
ob = db.execute("SELECT dia_object_id, cls, gi_ok FROM o").df().set_index('dia_object_id')
with np.errstate(invalid='ignore', divide='ignore'):
    t, te, d = (al[c].to_numpy(float) for c in ('template_flux', 'template_flux_err', 'psf_flux'))
    use = (t > 0) & (te > 0) & (t / te >= 5) & np.isfinite(d) & (1 + d / t > 0)
clsA = ob.cls.reindex(al.dia_object_id).to_numpy()
giA = ob.gi_ok.reindex(al.dia_object_id).to_numpy(bool)
for k in (0, 1):
    for b, n in PUBLISHED[k].items():
        assert int((use & giA & (clsA == k) & (al.band.to_numpy() == b)).sum()) == n, ('pandas N', k, b)
res['fig13_14_published_N_reproduced'] = True
res['fig13_14'] = {f'cls={r[0]}': dict(zip(('alerts', 'objects', 'G<0.2 alerts', 'G<0.2 & x<0.02 alerts', 'G>0.8 alerts',
                                              'bulge/plane alerts', 'G<0.2 objects', 'G>0.8 objects'), r[1:])) for r in q(
    """SELECT cls, count(*), count(DISTINCT dia_object_id), count(*) FILTER (WHERE G < 0.2),
       count(*) FILTER (WHERE G < 0.2 AND x < 0.02), count(*) FILTER (WHERE G > 0.8), count(*) FILTER (WHERE dusty),
       count(DISTINCT dia_object_id) FILTER (WHERE G < 0.2), count(DISTINCT dia_object_id) FILTER (WHERE G > 0.8)
       FROM a GROUP BY 1 ORDER BY 1""")}
res['fig14_by_band'] = {b: dict(zip(('alerts', 'G<0.2', 'G>0.8', 'bulge/plane'), r)) for b, *r in q(
    """SELECT band, count(*), count(*) FILTER (WHERE G < 0.2), count(*) FILTER (WHERE G > 0.8), count(*) FILTER (WHERE dusty)
       FROM a WHERE cls = 1 GROUP BY 1 ORDER BY 1""")}
log(json.dumps({k: res[k] for k in ('fig13_14', 'fig14_by_band')}, indent=1, default=int))
buf = io.BytesIO()
with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
    z.writestr('i_knockon.json', json.dumps(res, indent=1, default=int) + '\n')
sys.stdout.buffer.write(buf.getvalue())

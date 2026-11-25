"""Follow-up to g_rext_audit.py: r_extendedness against the other EDP2 classifiers, and which classifier separates
stars from galaxies in our matched DIAObjects. Read-only on Arnor; the ZIP goes to standard output.

    ssh arnor '<py> -B -' < h_rext_followup.py > h_rext.zip

Classifiers compared (all from the same EDP2 row of the nearest Object within 1 arcsec):
  r_extendedness            0/1, single-band PSF/cModel flux-ratio flag (what the figures use)
  griz_model_extendedness   G in [0, 1]; DP2 docs recommend it over refExtendedness. Point: G < 0.2, extended: G > 0.8
  x = r_PSF - r_cModel      forced fluxes in the Object table. Point: x < 0.02, extended: x > 0.05 (as in the S1X figure)
Truth sets from the Fink crossmatches: Gaia DR3 parallax S/N > 5 (stars), SIMBAD Star, SIMBAD Galaxy.
Headline contingency counts are computed in pandas (A) and DuckDB SQL (B) and must agree.
DuckDB sorts NaN above every number, so NaN must be caught explicitly before a > comparison.
"""
import io
import json
import sys
import zipfile

import duckdb
import numpy as np
import pandas as pd

ROOT = '/astro/store/shire/FINK'
X = f'{ROOT}/outputs/edp2-crossmatch-20261116-v1'
RUN = f'{ROOT}/outputs/notebooks-paper-20261015-v2'
LOCUS = f'{ROOT}/manifests/stellar-locus-figures-20261013-v1/inputs/RubinStellarLocus_COSMOS.txt'
DUSTY = ('Galactic bulge', 'Galactic plane')
GR, RI = (-0.75, 2.25), (-0.75, 3.5)
res, hists = {}, {}


def log(*a):
    print(*a, file=sys.stderr, flush=True)


mcols = ['dia_object_id', 'field', 'sep_arcsec', 'r_extendedness', 'r_extendedness_flag', 'refExtendedness',
         'griz_model_extendedness', 'r_sizeExtendedness', 'r_sizeExtendedness_flag',
         'r_cModelFlux', 'r_cModel_flag', 'detect_isIsolated', 'deblend_blendNChild'] + \
        [f'{b}_psf{s}' for b in 'gri' for s in ('Flux', 'FluxErr', 'Flux_flag')]
m = pd.read_parquet(f'{X}/edp2_match_one_per_object.parquet', columns=mcols).set_index('dia_object_id').sort_index()
c = pd.read_parquet(f'{X}/edp2_cmodel_1arcsec.parquet').set_index('dia_object_id').reindex(m.index)
s = pd.read_parquet(f'{RUN}/02_science_suite/tables/dia_object_science_table.parquet', columns=['dia_object_id', 'plx', 'eplx']).set_index('dia_object_id').reindex(m.index)
p = pd.read_parquet(f'{RUN}/01_template_colours/tables/dia_object_template_photometry.parquet', columns=['dia_object_id', 'simbad_group']).set_index('dia_object_id').reindex(m.index)
A = lambda col: m[col].to_numpy(dtype=float, na_value=np.nan)
clear = lambda ser: ~ser.astype('boolean').fillna(True).to_numpy(bool)
matched = np.nan_to_num(A('sep_arcsec'), nan=9.9) <= 1.0
dusty = m.field.isin(DUSTY).to_numpy()
ext = A('r_extendedness')
cls = np.where(matched & clear(m.r_extendedness_flag) & np.isin(ext, [0, 1]), ext, -1).astype(int)
G = np.where(matched, A('griz_model_extendedness'), np.nan)
with np.errstate(invalid='ignore', divide='ignore'):
    xr = np.where(matched & clear(m.r_cModel_flag) & clear(m.r_psfFlux_flag) & (A('r_psfFlux') > 0) & (A('r_cModelFlux') > 0),
                  -2.5 * np.log10(A('r_psfFlux') / A('r_cModelFlux')), np.nan)
    gaia = (s.plx.to_numpy(float, na_value=np.nan) / s.eplx.to_numpy(float, na_value=np.nan)) > 5
grp = p.simbad_group.to_numpy()
iso = (m.detect_isIsolated.astype('boolean').fillna(False).to_numpy(bool) | (A('deblend_blendNChild') == 1))
gb = np.select([G < 0.2, G > 0.8, (G >= 0.2) & (G <= 0.8)], ['G<0.2', 'G>0.8', '0.2-0.8'], 'G missing')
xb = np.select([xr < 0.02, xr > 0.05, (xr >= 0.02) & (xr <= 0.05)], ['x<0.02', 'x>0.05', '0.02-0.05'], 'x missing')


def good(tab, b, kind):
    f = tab[f'{b}_{kind}Flux'].to_numpy(dtype=float, na_value=np.nan)
    e = tab[f'{b}_{kind}FluxErr'].to_numpy(dtype=float, na_value=np.nan)
    fl = tab[f'{b}_psfFlux_flag' if kind == 'psf' else f'{b}_cModel_flag']
    with np.errstate(invalid='ignore', divide='ignore'):
        ok = np.isfinite(f) & np.isfinite(e) & (f > 0) & (e > 0) & (f / e >= 5) & clear(fl)
        return ok, np.where(ok, 31.4 - 2.5 * np.log10(np.where(ok, f, 1.0)), np.nan)


cok, cmag = zip(*[good(c, b, 'cModel') for b in 'gri'])
cgri = matched & cok[0] & cok[1] & cok[2]
c_gr, c_ri, rcm = cmag[0] - cmag[1], cmag[1] - cmag[2], cmag[1]
pok, pmag = zip(*[good(m, b, 'psf') for b in 'gri'])
pgri = matched & pok[0] & pok[1] & pok[2]

# ---------------------------------------------------------------- contingency of the classifiers (A and B)
cont = {}
for fg, fsel in (('elsewhere', ~dusty), ('bulge/plane', dusty)):
    sel = fsel & (cls >= 0)
    cont[f'{fg} | r_ext x G'] = pd.crosstab(cls[sel], gb[sel]).to_dict()
    cont[f'{fg} | r_ext x x'] = pd.crosstab(cls[sel], xb[sel]).to_dict()
db = duckdb.connect(':memory:', config={'threads': 8, 'memory_limit': '24GB', 'temp_directory': ''})
db.execute(f"CREATE VIEW mm AS FROM read_parquet('{X}/edp2_match_one_per_object.parquet')")
sqlrows = db.execute("""SELECT field IN ('Galactic bulge', 'Galactic plane') AS dusty, r_extendedness::INT AS cls,
  CASE WHEN griz_model_extendedness IS NULL OR isnan(griz_model_extendedness) THEN 'G missing'
       WHEN griz_model_extendedness < 0.2 THEN 'G<0.2' WHEN griz_model_extendedness > 0.8 THEN 'G>0.8'
       WHEN griz_model_extendedness BETWEEN 0.2 AND 0.8 THEN '0.2-0.8' ELSE 'G missing' END AS gb, count(*)
  FROM mm WHERE coalesce(sep_arcsec <= 1, false) AND NOT coalesce(r_extendedness_flag, true) AND r_extendedness IN (0, 1)
  GROUP BY 1, 2, 3""").fetchall()
for d_, k, g_, n in sqlrows:
    fg = 'bulge/plane' if d_ else 'elsewhere'
    assert cont[f'{fg} | r_ext x G'].get(g_, {}).get(k, 0) == n, ('contingency', fg, k, g_, n)
res['contingency'] = {k: {str(a): {str(b): int(v) for b, v in d.items()} for a, d in t.items()} for k, t in cont.items()}
log(json.dumps(res['contingency'], indent=1))

# ---------------------------------------------------------------- classifier performance against truth sets, outside bulge/plane
mb = [(-np.inf, 18), (18, 20), (20, 22), (22, 23), (23, 24)]
perf = {}
classifiers = {
    'r_extendedness = 1 (resolved) / 0': (cls == 1, cls == 0, cls >= 0),
    'G > 0.8 / G < 0.2': (G > 0.8, G < 0.2, np.isfinite(G)),
    'x > 0.05 / x < 0.02': (xr > 0.05, xr < 0.02, np.isfinite(xr)),
}
for name, (extd, point, valid) in classifiers.items():
    d = {}
    for lab, truth in (('Gaia stars', gaia), ('SIMBAD Star', grp == 'Star'), ('SIMBAD Galaxy', grp == 'Galaxy')):
        sel = truth & ~dusty & matched & valid
        d[f'{lab}: frac extended'] = [int(sel.sum()), float(np.mean(extd[sel])) if sel.any() else None]
        d[f'{lab}: frac point'] = float(np.mean(point[sel])) if sel.any() else None
    for lo, hi in mb:
        sel = gaia & ~dusty & matched & valid & (rcm >= lo) & (rcm < hi)
        d[f'Gaia stars r_cModel {lo}-{hi}: frac extended'] = [int(sel.sum()), float(np.mean(extd[sel])) if sel.any() else None]
    perf[name] = d
res['classifier_vs_truth_elsewhere'] = perf
log(json.dumps(perf, indent=1))

# ---------------------------------------------------------------- locus fraction and panel (d) histograms of subsets
sl = None
hdr = None
for line in open(LOCUS):
    if line.startswith('#') and 'u-g' in line:
        hdr = line.lstrip('#').split()
sl = pd.read_csv(LOCUS, comment='#', sep=r'\s+', header=None, names=hdr)


def locus_mask(xv, yv, nsig=2.0, n=700, over=4):
    gi = sl['g-i'].to_numpy(float)
    fine = np.linspace(gi.min(), gi.max(), (len(gi) - 1) * over + 1)
    x = np.interp(fine, gi, sl['g-r'].to_numpy(float)); y = np.interp(fine, gi, sl['r-i'].to_numpy(float))
    sx = np.interp(fine, gi, sl['grSig'].to_numpy(float)) * nsig; sy = np.interp(fine, gi, sl['riSig'].to_numpy(float)) * nsig
    xg = np.linspace((x - sx).min() - 0.02, (x + sx).max() + 0.02, n); yg = np.linspace((y - sy).min() - 0.02, (y + sy).max() + 0.02, n)
    X_, Y_ = np.meshgrid(xg, yg)
    mk = np.zeros(X_.shape, bool)
    for k in range(0, len(x), 16):
        dx = (X_[..., None] - x[k:k + 16]) / sx[k:k + 16]; dy = (Y_[..., None] - y[k:k + 16]) / sy[k:k + 16]
        mk |= ((dx * dx + dy * dy) <= 1.0).any(axis=-1)
    i = np.clip(np.searchsorted(xg, xv), 0, n - 1); j = np.clip(np.searchsorted(yg, yv), 0, n - 1)
    inside = np.isfinite(xv) & np.isfinite(yv) & (xv >= xg[0]) & (xv <= xg[-1]) & (yv >= yg[0]) & (yv <= yg[-1])
    return inside & mk[j, i]


on_c = locus_mask(c_gr, c_ri)
red_branch = (c_ri > 0.8) & on_c              # M-dwarf part of the stellar locus
sub = {}


def describe(tag, msk):
    sub[tag] = {'n': int(msk.sum()), 'on_locus': float(np.mean(on_c[msk])), 'on_red_branch_ri_gt_0p8': float(np.mean(red_branch[msk])),
                'gaia': float(np.mean(gaia[msk])), 'median_r_cModel': float(np.nanmedian(rcm[msk])),
                'isolated_or_single_child': float(np.mean(iso[msk]))}
    h, _, _ = np.histogram2d(c_gr[msk], c_ri[msk], bins=60, range=(GR, RI))
    hists[tag] = h.astype(np.int64)


f14o = cgri & ~dusty
describe('F14 elsewhere r_ext=1', f14o & (cls == 1))
describe('F14 elsewhere r_ext=0', f14o & (cls == 0))
describe('F14 elsewhere r_ext=1 & G<0.2', f14o & (cls == 1) & (G < 0.2))
describe('F14 elsewhere r_ext=1 & G 0.2-0.8', f14o & (cls == 1) & (G >= 0.2) & (G <= 0.8))
describe('F14 elsewhere r_ext=1 & G>0.8', f14o & (cls == 1) & (G > 0.8))
describe('F14 elsewhere r_ext=1 & G<0.2 & x<0.02', f14o & (cls == 1) & (G < 0.2) & (xr < 0.02))
describe('F14 elsewhere r_ext=0 & G>0.8', f14o & (cls == 0) & (G > 0.8))
describe('F14 elsewhere G<0.2', f14o & (G < 0.2))
describe('F14 elsewhere G>0.8', f14o & (G > 0.8))
describe('F14 elsewhere x<0.02', f14o & (xr < 0.02))
describe('F14 elsewhere x>0.05', f14o & (xr > 0.05))
describe('F14 bulge/plane r_ext=1', cgri & dusty & (cls == 1))
describe('F14 bulge/plane G>0.8', cgri & dusty & (G > 0.8))
f6 = pgri & ~dusty & (cls >= 0)
sub['F6E r_ext=1 (Fig. 8b)'] = {'n': int((f6 & (cls == 1)).sum()),
                                'G<0.2': int((f6 & (cls == 1) & (G < 0.2)).sum()),
                                'G<0.2 & x<0.02': int((f6 & (cls == 1) & (G < 0.2) & (xr < 0.02)).sum()),
                                'G>0.8': int((f6 & (cls == 1) & (G > 0.8)).sum())}
res['subsets'] = sub
log(json.dumps(sub, indent=1))

buf = io.BytesIO()
with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
    z.writestr('h_rext_followup.json', json.dumps(res, indent=1, default=str) + '\n')
    nb = io.BytesIO()
    keys = {f'h{i:02d}': k for i, k in enumerate(hists)}
    np.savez_compressed(nb, **{a: hists[k] for a, k in keys.items()})
    z.writestr('h_rext_hists.npz', nb.getvalue())
    z.writestr('h_hist_keys.json', json.dumps(keys, indent=1) + '\n')
sys.stdout.buffer.write(buf.getvalue())

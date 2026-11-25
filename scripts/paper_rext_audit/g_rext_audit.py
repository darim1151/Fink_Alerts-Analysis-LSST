"""Audit of the EDP2 r_extendedness split (Zeljko, 2026-11-25: "resolved looks like the stellar distribution").

Run:  ssh arnor '<py> -B -' < g_rext_audit.py > g_rext.zip      (read-only; the ZIP goes to standard output)

CP0  reproduce the 02_Fig14_cModel_morphology sample and its panel (d) histograms (compared locally with counts.json)
CP1  (code audit, done by reading the code)
CP2  r_extendedness against the PSF/cModel flux ratio in the same EDP2 row, and against the other EDP2 classifiers
CP3  Gaia-parallax stars and SIMBAD types by field group and magnitude
CP4  the resolved class split by field group, magnitude and blending; stellar-locus fraction of each subset
CP5  the same for Fig. 8 (F6E, EDP2 PSF colours outside the bulge and plane, 1 arcsec)
Every headline count is computed twice: pandas/NumPy (A) and DuckDB SQL (B); the script stops if they differ.
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
GR, RI = (-0.75, 2.25), (-0.75, 3.5)       # panel (d) of the morphology figure
NB = 60
res, hists = {}, {}


def log(*a):
    print(*a, file=sys.stderr, flush=True)


# ------------------------------------------------------------------ inputs (A: pandas)
mcols = ['dia_object_id', 'field', 'sep_arcsec', 'r_extendedness', 'r_extendedness_flag', 'refExtendedness',
         'refSizeExtendedness', 'griz_model_extendedness', 'r_sizeExtendedness', 'r_sizeExtendedness_flag',
         'r_blendedness', 'detect_isIsolated', 'detect_fromBlend', 'deblend_blendNChild', 'r_inputCount',
         'r_cModelFlux', 'r_cModelFluxErr', 'r_cModel_flag', 'ebv'] + \
        [f'{b}_psf{s}' for b in 'gri' for s in ('Flux', 'FluxErr', 'Flux_flag')]
m = pd.read_parquet(f'{X}/edp2_match_one_per_object.parquet', columns=mcols).set_index('dia_object_id').sort_index()
c = pd.read_parquet(f'{X}/edp2_cmodel_1arcsec.parquet').set_index('dia_object_id').reindex(m.index)
s = pd.read_parquet(f'{RUN}/02_science_suite/tables/dia_object_science_table.parquet',
                    columns=['dia_object_id', 'plx', 'eplx']).set_index('dia_object_id').reindex(m.index)
p = pd.read_parquet(f'{RUN}/01_template_colours/tables/dia_object_template_photometry.parquet',
                    columns=['dia_object_id', 'simbad_group', 'field']).set_index('dia_object_id').reindex(m.index)
assert len(m) == 3487175 and m.index.is_unique and (m.field == p.field).all()

A = lambda col: m[col].to_numpy(dtype=float, na_value=np.nan)
clear = lambda ser: ~ser.astype('boolean').fillna(True).to_numpy(bool)
sep = A('sep_arcsec')
matched = np.nan_to_num(sep, nan=9.9) <= 1.0
dusty = m.field.isin(DUSTY).to_numpy()
ext = A('r_extendedness')
cls = np.where(matched & clear(m.r_extendedness_flag) & np.isin(ext, [0, 1]), ext, -1).astype(int)


def good(tab, b, kind):
    f = tab[f'{b}_{kind}Flux'].to_numpy(dtype=float, na_value=np.nan)
    e = tab[f'{b}_{kind}FluxErr'].to_numpy(dtype=float, na_value=np.nan)
    fl = tab[f'{b}_psfFlux_flag' if kind == 'psf' else f'{b}_cModel_flag']
    with np.errstate(invalid='ignore', divide='ignore'):
        ok = np.isfinite(f) & np.isfinite(e) & (f > 0) & (e > 0) & (f / e >= 5) & clear(fl)
        mag = np.where(ok, 31.4 - 2.5 * np.log10(np.where(ok, f, 1.0)), np.nan)
    return ok, mag


cok, cmag = {}, {}
for b in 'gri':
    cok[b], cmag[b] = good(c, b, 'cModel')
pok, pmag = {}, {}
for b in 'gri':
    pok[b], pmag[b] = good(m, b, 'psf')
cgri = matched & cok['g'] & cok['r'] & cok['i']
pgri = matched & pok['g'] & pok['r'] & pok['i']
c_gr, c_ri = cmag['g'] - cmag['r'], cmag['r'] - cmag['i']
p_gr, p_ri = pmag['g'] - pmag['r'], pmag['r'] - pmag['i']
rcm = cmag['r']

# ------------------------------------------------------------------ CP0: the 02_Fig14 sample
f14 = cgri & (cls >= 0)
res['cp0'] = {'f14_gri_unresolved': int((f14 & (cls == 0)).sum()), 'f14_gri_resolved': int((f14 & (cls == 1)).sum())}
for k in (0, 1):
    msk = f14 & (cls == k)
    n = int(msk.sum())
    bins = int(np.clip(np.sqrt(n) / 2, 20, 60))
    h, _, _ = np.histogram2d(c_gr[msk], c_ri[msk], bins=bins, range=(GR, RI))
    hists[f'cp0_F14_d_{k}'] = h.astype(np.int64)

# B: SQL for the same counts, split by field group
db = duckdb.connect(':memory:', config={'threads': 8, 'memory_limit': '24GB', 'temp_directory': ''})
db.execute(f"CREATE VIEW mm AS FROM read_parquet('{X}/edp2_match_one_per_object.parquet')")
db.execute(f"CREATE VIEW cc AS FROM read_parquet('{X}/edp2_cmodel_1arcsec.parquet')")
okc = lambda b: f"(cc.{b}_cModelFlux > 0 AND cc.{b}_cModelFluxErr > 0 AND cc.{b}_cModelFlux / cc.{b}_cModelFluxErr >= 5 AND NOT coalesce(cc.{b}_cModel_flag, true))"
db.execute(f"""CREATE TEMP TABLE q AS SELECT mm.dia_object_id, mm.field IN ('Galactic bulge', 'Galactic plane') AS dusty,
  CASE WHEN coalesce(mm.sep_arcsec <= 1, false) AND NOT coalesce(mm.r_extendedness_flag, true) AND mm.r_extendedness IN (0, 1)
       THEN mm.r_extendedness::INT ELSE -1 END AS cls,
  coalesce(mm.sep_arcsec <= 1, false) AND coalesce({okc('g')} AND {okc('r')} AND {okc('i')}, false) AS cgri
FROM mm LEFT JOIN cc USING (dia_object_id)""")
sqlc = {(bool(d), int(k)): int(n) for d, k, n in db.execute("SELECT dusty, cls, count(*) FROM q WHERE cgri AND cls >= 0 GROUP BY 1, 2").fetchall()}
for d in (False, True):
    for k in (0, 1):
        a = int((f14 & (cls == k) & (dusty == d)).sum())
        assert a == sqlc[(d, k)], ('field split', d, k, a, sqlc[(d, k)])
res['cp0']['f14_by_field_group'] = {('bulge/plane' if d else 'elsewhere') + f' cls={k}': sqlc[(d, k)] for d in (False, True) for k in (0, 1)}
log(json.dumps(res['cp0'], indent=1))

# ------------------------------------------------------------------ CP2: r_extendedness vs PSF/cModel ratio, same EDP2 row
rpf, rcf = A('r_psfFlux'), A('r_cModelFlux')
rpe = A('r_psfFluxErr')
with np.errstate(invalid='ignore', divide='ignore'):
    xr = -2.5 * np.log10(rpf / rcf)                        # r_PSF - r_cModel (mag); > 0 when cModel is brighter
    snr = rpf / rpe
use = (cls >= 0) & np.isfinite(xr) & (rcf > 0) & (rpf > 0)
cp2 = {'n_classified_with_ratio': int(use.sum())}
# the pipeline rule is a threshold on the flux ratio; find the threshold that best separates the stored classes
grid = np.round(np.arange(0.0, 0.06001, 0.0005), 4)
xs, ks = xr[use], cls[use]
agree = [float(np.mean((xs > t) == (ks == 1))) for t in grid]
tbest = float(grid[int(np.argmax(agree))])
cp2['best_threshold_mag'] = tbest
cp2['agreement_at_best_threshold'] = max(agree)
cp2['flux_ratio_at_best_threshold'] = float(10 ** (-0.4 * tbest))
for lab, sel in (('all', np.ones_like(xs, bool)), ('r_psf_snr_ge_50', (snr[use] >= 50)), ('bulge/plane', dusty[use]),
                 ('elsewhere', ~dusty[use])):
    cp2[f'agreement_{lab}'] = float(np.mean(((xs > tbest) == (ks == 1))[sel])) if sel.any() else None
    cp2[f'n_{lab}'] = int(sel.sum())
for k in (0, 1):
    v = xs[ks == k]
    cp2[f'x_quantiles_cls{k}'] = [float(np.quantile(v, qq)) for qq in (0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99)]
hists['cp2_x_cls'] = np.array([np.histogram(xs[ks == k], bins=np.arange(-0.2, 1.0001, 0.005))[0] for k in (0, 1)])
# other EDP2 classifiers in the same row
G = A('griz_model_extendedness')
refx = A('refExtendedness')
for name, v in (('griz_model_extendedness', G), ('refExtendedness', refx)):
    ok = (cls >= 0) & np.isfinite(v)
    cp2[f'{name}_mean_by_cls'] = [float(np.mean(v[ok & (cls == k)])) for k in (0, 1)]
    if name == 'refExtendedness':
        cp2['refExtendedness_equals_r_extendedness'] = float(np.mean(v[ok] == cls[ok]))
# B: SQL version of the agreement at the same threshold
sql_agree = db.execute(f"""SELECT avg(CASE WHEN (-2.5 * log10(r_psfFlux / r_cModelFlux) > {tbest}) = (r_extendedness = 1) THEN 1.0 ELSE 0.0 END), count(*)
  FROM mm WHERE coalesce(sep_arcsec <= 1, false) AND NOT coalesce(r_extendedness_flag, true) AND r_extendedness IN (0, 1)
  AND r_psfFlux > 0 AND r_cModelFlux > 0 AND isfinite(r_psfFlux) AND isfinite(r_cModelFlux)""").fetchone()
assert abs(sql_agree[0] - cp2['agreement_at_best_threshold']) * sql_agree[1] <= 2 and sql_agree[1] == cp2['n_classified_with_ratio'], sql_agree
res['cp2'] = cp2
log(json.dumps(cp2, indent=1))

# ------------------------------------------------------------------ CP3: external truth
plx, eplx = s.plx.to_numpy(dtype=float, na_value=np.nan), s.eplx.to_numpy(dtype=float, na_value=np.nan)
with np.errstate(invalid='ignore', divide='ignore'):
    gaia = (plx / eplx) > 5
mbins = [(-np.inf, 18), (18, 20), (20, 22), (22, 24), (24, np.inf)]
cp3 = {}
grp = p.simbad_group.to_numpy()
for fg, fsel in (('elsewhere', ~dusty), ('bulge/plane', dusty)):
    for lab, base in (('Gaia parallax S/N>5', gaia), ('SIMBAD Star', grp == 'Star'), ('SIMBAD Galaxy', grp == 'Galaxy'),
                      ('SIMBAD QSO/AGN', grp == 'QSO / AGN')):
        sel = base & fsel & (cls >= 0)
        cp3[f'{fg} | {lab}'] = {'n': int(sel.sum()), 'frac_resolved': float(np.mean(cls[sel] == 1)) if sel.any() else None}
    for lo, hi in mbins:
        sel = gaia & fsel & (cls >= 0) & (rcm >= lo) & (rcm < hi)
        cp3[f'{fg} | Gaia | r_cModel {lo}-{hi}'] = {'n': int(sel.sum()), 'frac_resolved': float(np.mean(cls[sel] == 1)) if sel.any() else None}
res['cp3'] = cp3
log(json.dumps(cp3, indent=1))


# ------------------------------------------------------------------ CP4/CP5: what the resolved class contains
def load_locus(path):
    hdr = None
    for line in open(path):
        if line.startswith('#') and 'u-g' in line:
            hdr = line.lstrip('#').split()
    d = pd.read_csv(path, comment='#', sep=r'\s+', header=None, names=hdr)
    return d


sl = load_locus(LOCUS)


def locus_mask(xv, yv, nsig=2.0, n=700, over=4):
    gi = sl['g-i'].to_numpy(float)
    fine = np.linspace(gi.min(), gi.max(), (len(gi) - 1) * over + 1)
    x = np.interp(fine, gi, sl['g-r'].to_numpy(float))
    y = np.interp(fine, gi, sl['r-i'].to_numpy(float))
    sx = np.interp(fine, gi, sl['grSig'].to_numpy(float)) * nsig
    sy = np.interp(fine, gi, sl['riSig'].to_numpy(float)) * nsig
    xg = np.linspace((x - sx).min() - 0.02, (x + sx).max() + 0.02, n)
    yg = np.linspace((y - sy).min() - 0.02, (y + sy).max() + 0.02, n)
    X_, Y_ = np.meshgrid(xg, yg)
    mk = np.zeros(X_.shape, bool)
    for k in range(0, len(x), 16):
        dx = (X_[..., None] - x[k:k + 16]) / sx[k:k + 16]
        dy = (Y_[..., None] - y[k:k + 16]) / sy[k:k + 16]
        mk |= ((dx * dx + dy * dy) <= 1.0).any(axis=-1)
    i = np.clip(np.searchsorted(xg, xv), 0, n - 1)
    j = np.clip(np.searchsorted(yg, yv), 0, n - 1)
    inside = np.isfinite(xv) & np.isfinite(yv) & (xv >= xg[0]) & (xv <= xg[-1]) & (yv >= yg[0]) & (yv <= yg[-1])
    return inside & mk[j, i]


on_c = locus_mask(c_gr, c_ri)
on_p = locus_mask(p_gr, p_ri)
iso = m.detect_isIsolated.astype('boolean').fillna(False).to_numpy(bool)
fromblend = m.detect_fromBlend.astype('boolean').fillna(False).to_numpy(bool)
bl = A('r_blendedness')
cp4 = {}


def describe(tag, msk, gr, ri, on):
    d = {'n': int(msk.sum()), 'on_locus_frac': float(np.mean(on[msk])) if msk.any() else None,
         'isolated_frac': float(np.mean(iso[msk])) if msk.any() else None,
         'fromBlend_frac': float(np.mean(fromblend[msk])) if msk.any() else None,
         'blendedness_gt_0p1_frac': float(np.mean(np.nan_to_num(bl[msk]) > 0.1)) if msk.any() else None,
         'gaia_frac': float(np.mean(gaia[msk])) if msk.any() else None,
         'median_r_cModel': float(np.nanmedian(rcm[msk])) if msk.any() else None,
         'median_ebv': float(np.nanmedian(A('ebv')[msk])) if msk.any() else None}
    for lo, hi in mbins:
        sub = msk & (rcm >= lo) & (rcm < hi)
        d[f'r {lo}-{hi}'] = [int(sub.sum()), float(np.mean(on[sub])) if sub.any() else None]
    cp4[tag] = d
    h, _, _ = np.histogram2d(gr[msk], ri[msk], bins=NB, range=(GR, RI))
    hists[tag] = h.astype(np.int64)


for fg, fsel in (('elsewhere', ~dusty), ('bulge/plane', dusty)):
    for k in (0, 1):
        describe(f'F14 {fg} cls={k}', f14 & fsel & (cls == k), c_gr, c_ri, on_c)
for lo, hi in mbins:
    describe(f'F14 elsewhere cls=1 r_cModel {lo}-{hi}', f14 & ~dusty & (cls == 1) & (rcm >= lo) & (rcm < hi), c_gr, c_ri, on_c)
describe('F14 elsewhere cls=1 isolated', f14 & ~dusty & (cls == 1) & iso, c_gr, c_ri, on_c)
describe('F14 elsewhere cls=1 not isolated', f14 & ~dusty & (cls == 1) & ~iso, c_gr, c_ri, on_c)
f6 = pgri & ~dusty & (cls >= 0)
assert int((f6 & (cls == 0)).sum()) == 1278454 and int((f6 & (cls == 1)).sum()) == 259736, 'F6E counts'
for k in (0, 1):
    describe(f'F6E cls={k}', f6 & (cls == k), p_gr, p_ri, on_p)
for lo, hi in mbins:
    describe(f'F6E cls=1 r_cModel {lo}-{hi}', f6 & (cls == 1) & (rcm >= lo) & (rcm < hi), p_gr, p_ri, on_p)
res['cp4'] = cp4
log(json.dumps(cp4, indent=1))
# by programme for the F14 resolved class
res['f14_resolved_by_programme'] = m.field[f14 & (cls == 1)].value_counts().to_dict()
res['f14_unresolved_by_programme'] = m.field[f14 & (cls == 0)].value_counts().to_dict()

buf = io.BytesIO()
with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
    z.writestr('g_rext_audit.json', json.dumps(res, indent=1, default=str) + '\n')
    nb = io.BytesIO()
    np.savez_compressed(nb, **{k.replace(' ', '_').replace('/', '_').replace('|', '_'): v for k, v in hists.items()})
    z.writestr('g_rext_hists.npz', nb.getvalue())
    z.writestr('hist_keys.json', json.dumps({k.replace(' ', '_').replace('/', '_').replace('|', '_'): k for k in hists}) + '\n')
sys.stdout.buffer.write(buf.getvalue())

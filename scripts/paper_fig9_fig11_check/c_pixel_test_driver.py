"""Driver appended to the bundled plotting modules of commit d6761c5 (see make_c_bundle.py). Runs on Arnor, read-only.

Re-draws Fig. 11 (F9E) with the original plotting function twice: once with the template colours it was written for,
once with EDP2 PSF photometry swapped in for the same objects. Each re-draw is decoded and compared pixel by pixel
with the PNG that the original run saved next to the delivered PDF. Output: a ZIP on standard output.
"""
import io
import json
import sys
import zipfile

import numpy as np
import pandas as pd

ROOT = '/astro/store/shire/FINK'
MATCH = f'{ROOT}/outputs/edp2-crossmatch-20261116-v1/edp2_match_one_per_object.parquet'
SCI = f'{ROOT}/outputs/notebooks-paper-20261015-v2/02_science_suite/tables/dia_object_science_table.parquet'
RUN = f'{ROOT}/outputs/edp2-figures-20261116-v3-r1p0'

import matplotlib
matplotlib.use('Agg')
import matplotlib.figure as mfig
import matplotlib.image as mimg

import edp2_figures as ef
import paper_figures_pub as pub

STORE = {}
_real_savefig = mfig.Figure.savefig


def _capture(self, fname, *a, **kw):
    """Keep the bytes in memory instead of writing a file (same arguments otherwise)."""
    name = str(fname).rsplit('/', 1)[-1]
    b = io.BytesIO()
    _real_savefig(self, b, *a, format=name.rsplit('.', 1)[-1], **kw)
    STORE[name] = b.getvalue()


class _Book:
    def savefig(self, fig, **kw):
        pass


mfig.Figure.savefig = _capture
pub.SHOW_CAPTIONS = False

one = pd.read_parquet(MATCH)
one['matched'] = (one.sep_arcsec.astype(float) <= 1.0).to_numpy(bool)
assert (one.matched == one.matched_1arcsec.astype(bool)).all()
sci = pd.read_parquet(SCI)
f9 = ef.select_f9(one, sci)
info = {'template_rows': int(len(f9))}

# variant E: same objects, EDP2 PSF magnitudes and colours (S/N >= 5, unflagged) in place of the template ones
e = one.set_index('dia_object_id').reindex(f9.dia_object_id)
mag, ok = {}, {}
for b in 'ugri':
    ok[b] = ef.good_psf(e, b).to_numpy(bool)
    mag[b] = np.where(ok[b], ef.psf_mag(e[f'{b}_psfFlux'].to_numpy(float)), np.nan)
fE = f9.copy()
fE['mag_r'] = mag['r']
fE['gi'], fE['gr'], fE['ri'], fE['ug'] = mag['g'] - mag['i'], mag['g'] - mag['r'], mag['r'] - mag['i'], mag['u'] - mag['g']
fE['panel_a'] = f9.panel_a.to_numpy(bool) & ok['g'] & ok['r'] & ok['i']
fE['panel_c'] = fE['panel_a']
fE['panel_b'] = f9.panel_b.to_numpy(bool) & ok['u'] & ok['g'] & ok['r']
fE = fE[fE.panel_a | fE.panel_b]
info['edp2_rows'] = int(len(fE))
info['edp2_panel_a'] = int(fE.panel_a.sum())
info['edp2_panel_b'] = int(fE.panel_b.sum())

renders = {}
for tag, frame in (('template', f9), ('edp2_psf', fE)):
    STORE.clear()
    _, inf, cap = ef.fig_f9(frame, '/nonexistent', _Book(), pub)
    renders[tag] = dict(STORE)
    info[f'{tag}_panel_counts'] = inf['counts']


def decode(png):
    return (mimg.imread(io.BytesIO(png), format='png') * 255).round().astype(np.int16)


with open(f'{RUN}/F9E_template_colours_by_r_extendedness.png', 'rb') as fh:
    published_png = fh.read()
P = decode(published_png)
res = {'published_shape': list(P.shape)}
imgs = {'published': P}
for tag in renders:
    R = decode(renders[tag]['F9E_template_colours_by_r_extendedness.png'])
    imgs[tag] = R
    r = {'shape': list(R.shape)}
    if R.shape == P.shape:
        d = np.abs(R - P).max(axis=-1)
        r['pixels'] = int(d.size)
        r['pixels_differing'] = int((d > 0).sum())
        r['pixels_differing_gt_32'] = int((d > 32).sum())
        r['fraction_differing'] = float((d > 0).mean())
        blueP = (P[..., 2] > 150) & (P[..., 0] < 120)
        blueR = (R[..., 2] > 150) & (R[..., 0] < 120)
        r['blue_pixels_published'] = int(blueP.sum())
        r['blue_pixels_render'] = int(blueR.sum())
        r['blue_pixels_jaccard'] = float((blueP & blueR).sum() / max(1, (blueP | blueR).sum()))
    res[tag] = r
info['pixel_test'] = res
info['matplotlib'] = matplotlib.__version__
print(json.dumps(info, indent=1), file=sys.stderr)

buf = io.BytesIO()
with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
    z.writestr('c_pixel_test.json', json.dumps(info, indent=1) + '\n')
    z.writestr('published_F9E.png', published_png)
    for tag in renders:
        for name, data in renders[tag].items():
            z.writestr(f'rerender_{tag}_{name}', data)
sys.stdout.buffer.write(buf.getvalue())

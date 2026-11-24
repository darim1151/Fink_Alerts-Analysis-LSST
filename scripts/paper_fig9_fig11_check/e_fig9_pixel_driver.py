"""Driver appended to the bundled modules of commit d3e1153 (make_bundle.py). Runs on Arnor, read-only.

Re-draws the four-panel transient-colour figure (manuscript Fig. 9; 'fig11' in that code) with the original function
and inputs, in memory, and compares it pixel by pixel with the PNG saved next to the delivered PDF.
"""
import io
import json
import sys
import zipfile

import numpy as np
import pandas as pd

ROOT = '/astro/store/shire/FINK'
X = f'{ROOT}/outputs/edp2-crossmatch-20261116-v1'
RUN = f'{ROOT}/outputs/notebooks-paper-20261015-v2'
FIGRUN = f'{ROOT}/outputs/paper-figures-20261117-edp2-revision-v3'
LOCUS = f'{ROOT}/manifests/stellar-locus-figures-20261013-v1/inputs/RubinStellarLocus_COSMOS.txt'

import matplotlib
matplotlib.use('Agg')
import matplotlib.figure as mfig
import matplotlib.image as mimg

import edp2_paper_figures_v2 as v2
import paper_figures_pub as pub
import plot_colour_diagrams as kit

STORE = {}
_real = mfig.Figure.savefig


def _capture(self, fname, *a, **kw):
    name = str(fname).rsplit('/', 1)[-1]
    b = io.BytesIO()
    _real(self, b, *a, format=name.rsplit('.', 1)[-1], **kw)
    STORE[name] = b.getvalue()


class _Book:
    def savefig(self, fig, **kw):
        pass


mfig.Figure.savefig = _capture
pub.SHOW_CAPTIONS = False
one = pd.read_parquet(f'{X}/edp2_match_one_per_object.parquet')
cm = pd.read_parquet(f'{X}/edp2_cmodel_1arcsec.parquet')
e = v2.edp2_bands(one, cm)
tp = pd.read_parquet(f'{RUN}/01_template_colours/tables/dia_object_template_photometry.parquet')
tc = pd.read_parquet(f'{RUN}/06_paper_additions/tables/transient_colours_per_object.parquet')
tpE, tpU = v2.swap_to_edp2(tp, e), v2.only_unmatched(tp, e)
sl = kit.load_locus(LOCUS)
info = v2.fig11(tc, tpE, tpU, sl, '/nonexistent', _Book(), pub)


def decode(png):
    return (mimg.imread(io.BytesIO(png), format='png') * 255).round().astype(np.int16)


with open(f'{FIGRUN}/fig11/fig11_Fig11_transient_colours_edp2.png', 'rb') as fh:
    published = fh.read()
P, R = decode(published), decode(STORE['Fig11_transient_colours_edp2.png'])
res = {'figure_info': info, 'published_shape': list(P.shape), 'render_shape': list(R.shape), 'matplotlib': matplotlib.__version__}
if P.shape == R.shape:
    d = np.abs(P - R).max(axis=-1)
    res['pixels'] = int(d.size)
    res['pixels_differing'] = int((d > 0).sum())
print(json.dumps(res, indent=1), file=sys.stderr)
buf = io.BytesIO()
with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
    z.writestr('e_fig9_pixel.json', json.dumps(res, indent=1) + '\n')
    z.writestr('published_fig9.png', published)
    for k, v in STORE.items():
        z.writestr('rerender_' + k, v)
sys.stdout.buffer.write(buf.getvalue())

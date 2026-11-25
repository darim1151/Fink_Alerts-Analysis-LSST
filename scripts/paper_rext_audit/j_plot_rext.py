"""Diagnostic figure for the r_extendedness question, from the Arnor histograms (g_rext_audit.py, h_rext_followup.py).

    python j_plot_rext.py <run folder> <RubinStellarLocus_COSMOS.txt>

Panel (a) reproduces panel (d) of 02_Fig14_cModel_morphology (its histograms are identical to the archived ones), and
(b)-(d) take it apart. Contours use the same rule as that figure: 3x3-smoothed counts on a 60 x 60 grid, levels that
enclose 50, 80 and 95% of each class inside the axes.
"""
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2] / 'tools' / 'colour_plot_kit'))
import plot_colour_diagrams as kit  # noqa: E402
import matplotlib  # noqa: E402

matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

run, locus = Path(sys.argv[1]), sys.argv[2]
GR, RI = (-0.75, 2.25), (-0.75, 3.5)
BLUE, RED, ORANGE, INK = '#2a78d6', '#d0413b', '#e8692f', '#111111'
plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 9, 'axes.labelsize': 10, 'xtick.labelsize': 8.5,
                     'ytick.labelsize': 8.5, 'xtick.direction': 'in', 'ytick.direction': 'in', 'xtick.top': True,
                     'ytick.right': True, 'axes.linewidth': 0.8, 'mathtext.default': 'regular', 'savefig.dpi': 300,
                     'pdf.fonttype': 42})
g = np.load(run / 'arnor/g_rext/g_rext_hists.npz')
hk = json.loads((run / 'arnor/h_rext/h_hist_keys.json').read_text())
h_ = np.load(run / 'arnor/h_rext/h_rext_hists.npz')
H = {v: h_[k] for k, v in hk.items()}
H['all r_ext=0'], H['all r_ext=1'] = g['cp0_F14_d_0'], g['cp0_F14_d_1']
sl = kit.load_locus(locus)


def contour(ax, h, color, lw=1.1):
    assert h.shape == (60, 60)
    p = np.pad(h.astype(float), 1)
    hs = sum(p[i:i + 60, j:j + 60] for i in range(3) for j in range(3)) / 9.0
    v = np.sort(hs.ravel())[::-1]
    cum = np.cumsum(v) / v.sum()
    lv = sorted({v[min(np.searchsorted(cum, f), len(v) - 1)] for f in (0.5, 0.8, 0.95)})
    xe, ye = np.linspace(*GR, 61), np.linspace(*RI, 61)
    ax.contour(0.5 * (xe[1:] + xe[:-1]), 0.5 * (ye[1:] + ye[:-1]), hs.T, levels=[l for l in lv if l > 0], colors=color, linewidths=lw)


def n(key):
    return int(H[key].sum())


panels = [
    ('(a) as delivered: all fields', [('all r_ext=0', BLUE, 'r_ext = 0'), ('all r_ext=1', RED, 'r_ext = 1')]),
    ('(b) outside bulge and plane', [('F14 elsewhere r_ext=0', BLUE, 'r_ext = 0'), ('F14 elsewhere r_ext=1', RED, 'r_ext = 1')]),
    ('(c) r_ext = 1 only, split by G', [('F14 elsewhere r_ext=1 & G<0.2', ORANGE, 'r_ext = 1, G < 0.2'), ('F14 elsewhere r_ext=1 & G>0.8', RED, 'r_ext = 1, G > 0.8')]),
    ('(d) split by G instead', [('F14 elsewhere G<0.2', BLUE, 'G < 0.2'), ('F14 elsewhere G>0.8', RED, 'G > 0.8')]),
]
fig, axes = plt.subplots(2, 2, figsize=(7.2, 6.6), gridspec_kw={'hspace': 0.30, 'wspace': 0.28})
for ax, (title, sets) in zip(axes.ravel(), panels):
    kit.draw_locus(ax, sl, 'gr', 'ri')
    handles = []
    for key, col, lab in sets:
        contour(ax, H[key], col)
        handles.append(Line2D([], [], color=col, lw=1.2, label=f'{lab}: {n(key):,}'))
    ax.set_xlim(*GR); ax.set_ylim(*RI)
    ax.set_xlabel(r'$(g-r)_{\rm EDP2\,cModel}$'); ax.set_ylabel(r'$(r-i)_{\rm EDP2\,cModel}$')
    ax.minorticks_on()
    ax.text(0.035, 0.965, title, transform=ax.transAxes, va='top', fontsize=8.6, fontweight='bold')
    ax.legend(handles=handles, loc='upper left', bbox_to_anchor=(0.0, 0.92), fontsize=7.8, frameon=False, handlelength=1.4)
fig.text(0.5, 0.0, 'r_ext = EDP2 r_extendedness; G = EDP2 griz_model_extendedness. (b)-(d): outside the Galactic bulge and plane programmes.', ha='center', fontsize=8.2)
fig.legend(handles=[Patch(facecolor=kit.LOCUS_COLOR, alpha=kit.LOCUS_ALPHA, label=r'stellar locus $\pm$ 2$\sigma$'),
                    Line2D([], [], color=INK, lw=1, label='contours: 50, 80, 95% of each set (N inside the axes)')],
           loc='lower center', ncol=2, frameon=False, fontsize=8.5, bbox_to_anchor=(0.5, -0.065))
out = run / 'figures'
out.mkdir(exist_ok=True)
for ext in ('pdf', 'png'):
    fig.savefig(out / f'rext_diagnostic_gr_ri.{ext}', bbox_inches='tight', pad_inches=0.08)
print({k: n(k) for s in panels for k, *_ in s[1]})

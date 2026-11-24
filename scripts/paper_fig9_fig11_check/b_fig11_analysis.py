"""Fig. 11 follow-up, run locally on the per-object table returned by a_fig11_recount.py.

    python b_fig11_analysis.py <run folder> <RubinStellarLocus_COSMOS.txt>

Writes into <run folder>/tables and <run folder>/figures:
  Fig11_numbers.csv             every number in the Fig. 11 caption against the independent recount
  Fig11_composition.csv         what the unresolved and resolved objects are (fields, Gaia, SIMBAD, locus, red branch)
  Fig11_template_vs_EDP2.csv    template PSF minus EDP2 PSF photometry for the plotted objects
  Fig11_template_vs_EDP2_same_objects.pdf/png   the Fig. 11 colour panels drawn twice for the same objects
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2] / 'tools' / 'colour_plot_kit'))
import plot_colour_diagrams as kit  # noqa: E402

run, locus_path = Path(sys.argv[1]), sys.argv[2]
tabs, figs = run / 'tables', run / 'figures'
tabs.mkdir(exist_ok=True)
figs.mkdir(exist_ok=True)
rc = json.loads((run / 'arnor/a_fig11/fig11_recount.json').read_text())['counts']
px = json.loads((run / 'arnor/c_pixel/c_pixel_test.json').read_text())
t = pd.read_parquet(run / 'arnor/a_fig11/fig11_template_objects.parquet')
f = t[t.cls.notna()].copy()
f['cls'] = f.cls.astype(int)
U, R = f[f.cls == 0], f[f.cls == 1]
for d in (f, U, R):
    pass
f['gi'], f['gr'], f['ri'], f['ug'] = f.tg - f.ti, f.tg - f.tr, f.tr - f.ti, f.tu - f.tg
f['egi'], f['egr'], f['eri'], f['eug'] = f.eg - f.ei, f.eg - f.er, f.er - f.ei, f.eu - f.eg
U, R = f[f.cls == 0], f[f.cls == 1]

# ------------------------------------------------------------------ numbers in the caption vs recount
rows = [
    ('Objects with usable gri or ugr templates, EDP2 Object within 1", usable r_extendedness', 8038, rc['fig11_total']),
    ('  of which unresolved (r_extendedness = 0)', 5374, rc['fig11_unresolved']),
    ('  of which resolved (r_extendedness = 1)', 2664, rc['fig11_resolved']),
    ('Panel (a): objects with usable gri templates', 7952, rc['fig11_gri']),
    ('Panels (b), (d): unresolved points (gri)', 5324, rc['fig11_gri_unresolved']),
    ('Panels (b), (d): resolved objects in contours (gri)', 2628, rc['fig11_gri_resolved']),
    ('Panel (c): unresolved points (ugr)', 628, rc['fig11_ugr_unresolved']),
    ('Panel (c): resolved objects in contours (ugr)', 400, rc['fig11_ugr_resolved']),
    ('Objects in both gri and ugr', 942, rc['fig11_gri_and_ugr']),
]
num = pd.DataFrame(rows, columns=['quantity', 'in figure / caption', 'independent recount'])
num['agrees'] = num['in figure / caption'] == num['independent recount']
assert num.agrees.all()
num.to_csv(tabs / 'Fig11_numbers.csv', index=False)

# ------------------------------------------------------------------ composition
sl = kit.load_locus(locus_path)
X, Y, mask = kit.locus_region(sl, 'gr', 'ri')
xs, ys = X[0], Y[:, 0]


def on_locus(gr, ri):
    i = np.clip(np.searchsorted(xs, gr), 0, len(xs) - 1)
    j = np.clip(np.searchsorted(ys, ri), 0, len(ys) - 1)
    inside = (gr >= xs[0]) & (gr <= xs[-1]) & (ri >= ys[0]) & (ri <= ys[-1])
    return inside & mask[j, i]


f['gri_on_locus'] = f.gri & on_locus(f.gr.to_numpy(float), f.ri.to_numpy(float))
U, R = f[f.cls == 0], f[f.cls == 1]


def row(name, sel_u, sel_r, base_u=None, base_r=None):
    bu = len(U) if base_u is None else int(base_u)
    br = len(R) if base_r is None else int(base_r)
    nu, nr = int(sel_u.sum()), int(sel_r.sum())
    return {'category': name, 'unresolved_N': nu, 'unresolved_%': round(100 * nu / bu, 1) if bu else None,
            'resolved_N': nr, 'resolved_%': round(100 * nr / br, 1) if br else None, 'denominators': f'{bu:,} / {br:,}'}


comp = [row('All objects in Fig. 11', U.cls == 0, R.cls == 1)]
for fld in ('Deep drilling fields', 'Low-dust WFD', 'M49 field', 'Galactic bulge'):
    comp.append(row(f'Field: {fld}', U.field == fld, R.field == fld))
comp.append(row('Gaia DR3 parallax S/N > 5 (Fink crossmatch)', U.plx_snr > 5, R.plx_snr > 5))
for grp in ('Star', 'QSO / AGN', 'Galaxy', 'Other SIMBAD type', 'No SIMBAD object within 1″'):
    comp.append(row(f'SIMBAD: {grp}', U.simbad_group == grp, R.simbad_group == grp))
Ug, Rg = U[U.gri], R[R.gri]
comp.append(row('gri: within 2σ of the COSMOS stellar locus in g−r, r−i', Ug.gri_on_locus, Rg.gri_on_locus, len(Ug), len(Rg)))
comp.append(row('gri: (g−i)_tmpl > 2 (red branch)', Ug.gi > 2, Rg.gi > 2, len(Ug), len(Rg)))
comp.append(row('gri: (g−i)_tmpl < 1 and r_tmpl > 20', (Ug.gi < 1) & (Ug.tr > 20), (Rg.gi < 1) & (Rg.tr > 20), len(Ug), len(Rg)))
Uu, Ru = U[U.ugr], R[R.ugr]
comp.append(row('ugr: (u−g)_tmpl < 0.6 (UV excess)', Uu.ug < 0.6, Ru.ug < 0.6, len(Uu), len(Ru)))
comp.append(row('gri: r_tmpl < 20', Ug.tr < 20, Rg.tr < 20, len(Ug), len(Rg)))
comp = pd.DataFrame(comp)
med = {'median r_tmpl (gri)': (float(Ug.tr.median()), float(Rg.tr.median())),
       'median alerts per object': (float(U.n_sources.median()), float(R.n_sources.median()))}
comp.to_csv(tabs / 'Fig11_composition.csv', index=False)
# blue clump at g-i < 1, r > 20 among unresolved: what are they?
blue = Ug[(Ug.gi < 1) & (Ug.tr > 20)]
clump = {'N': int(len(blue)), 'by_field': blue.field.value_counts().to_dict(),
         'by_simbad_group': blue.simbad_group.value_counts().to_dict(),
         'gaia_plx_snr_gt5': int((blue.plx_snr > 5).sum()),
         'on_locus': int(blue.gri_on_locus.sum()),
         'with_ugr': int(blue.ugr.sum()), 'with_ugr_and_u_minus_g_lt_0p6': int((blue.ugr & (blue.ug < 0.6)).sum()),
         'by_cats_label': blue.cats_label.value_counts().to_dict()}


# ------------------------------------------------------------------ template minus EDP2 PSF, same objects
def rsig(x):
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    return float(1.4826 * np.median(np.abs(x - np.median(x)))) if len(x) else np.nan


cmp_rows = []
for cname, D in (('unresolved', U), ('resolved', R)):
    for q, tcol, ecol, okt, oke in (('r', 'tr', 'er', 'gri', 'eok_r'), ('g−r', 'gr', 'egr', 'gri', None),
                                    ('r−i', 'ri', 'eri', 'gri', None), ('g−i', 'gi', 'egi', 'gri', None),
                                    ('u−g', 'ug', 'eug', 'ugr', None)):
        for lab, cut in (('all', np.ones(len(D), bool)), ('r_tmpl < 21', (D.tr < 21).to_numpy()), ('r_tmpl ≥ 21', (D.tr >= 21).to_numpy())):
            dd = (D[tcol] - D[ecol]).to_numpy(float)[D[okt].to_numpy(bool) & cut]
            dd = dd[np.isfinite(dd)]
            cmp_rows.append({'class': cname, 'quantity': q, 'subset': lab, 'N_both': int(len(dd)),
                             'median_template_minus_EDP2': round(float(np.median(dd)), 4) if len(dd) else None,
                             'robust_sigma': round(rsig(dd), 4) if len(dd) else None,
                             'frac_abs_gt_0p2': round(float(np.mean(np.abs(dd) > 0.2)), 4) if len(dd) else None})
cmp = pd.DataFrame(cmp_rows)
cmp.to_csv(tabs / 'Fig11_template_vs_EDP2.csv', index=False)
n_edp2_u = int((f.eok_u & f.eok_g & f.eok_r).sum())

# ------------------------------------------------------------------ comparison figure (same objects, two photometries)
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

BLUE, RED, INK, GREY = '#2a78d6', '#d0413b', '#111111', '#7d7b76'
plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 9, 'axes.labelsize': 10, 'xtick.labelsize': 8.5,
                     'ytick.labelsize': 8.5, 'xtick.direction': 'in', 'ytick.direction': 'in', 'xtick.top': True,
                     'ytick.right': True, 'mathtext.default': 'regular', 'savefig.dpi': 300, 'pdf.fonttype': 42})


def contours(ax, x, y, xr, yr):
    x, y = np.asarray(x, float), np.asarray(y, float)
    ok = np.isfinite(x) & np.isfinite(y)
    x, y = x[ok], y[ok]
    bins = int(np.clip(np.sqrt(len(x)) / 2, 20, 60))
    h, xe, ye = np.histogram2d(x, y, bins=bins, range=(xr, yr))
    p = np.pad(h, 1)
    hs = sum(p[i:i + h.shape[0], j:j + h.shape[1]] for i in range(3) for j in range(3)) / 9.0
    v = np.sort(hs.ravel())[::-1]
    cum = np.cumsum(v) / v.sum()
    lv = sorted({v[min(np.searchsorted(cum, q), len(v) - 1)] for q in (0.5, 0.8, 0.95)})
    ax.contour(0.5 * (xe[1:] + xe[:-1]), 0.5 * (ye[1:] + ye[:-1]), hs.T, levels=[l for l in lv if l > 0], colors=RED, linewidths=0.9)


panels = (('gi', 'tr', 'egi', 'er', r'$(g-i)$', r'$r$ (AB)', (-1, 4), (14, 26), True),
          ('gr', 'ri', 'egr', 'eri', r'$(g-r)$', r'$(r-i)$', (-0.75, 2.25), (-0.75, 2.75), False))
fig, axes = plt.subplots(2, 3, figsize=(7.2, 5.2), gridspec_kw={'wspace': 0.52, 'hspace': 0.36})
letters = iter('abcdef')
for row_, (xt, yt, xe_, ye_, xl, yl, xr, yr, inv) in zip(axes, panels):
    sameU = Ug[np.isfinite(Ug[xe_]) & np.isfinite(Ug[ye_])]
    sameR = Rg[np.isfinite(Rg[xe_]) & np.isfinite(Rg[ye_])]
    for ax, (xc, yc, sub) in zip(row_[:2], ((xt, yt, 'tmpl'), (xe_, ye_, 'EDP2\\,PSF'))):
        contours(ax, sameR[xc], sameR[yc], xr, yr)
        ax.scatter(sameU[xc], sameU[yc], s=3, color=BLUE, alpha=0.8, edgecolors='none', rasterized=True)
        ax.set_xlim(*xr)
        ax.set_ylim(*(yr[::-1] if inv else yr))
        ax.set_xlabel(xl.replace(')$', r')_{\rm ' + sub + '}$'))
        ax.set_ylabel(yl.replace('$r$', r'$r_{\rm ' + sub + '}$') if yt == 'tr' else yl.replace(')$', r')_{\rm ' + sub + '}$'))
        ax.text(0.04, 0.95, f'({next(letters)})', transform=ax.transAxes, va='top', fontweight='bold')
        ax.minorticks_on()
    ax = row_[2]
    if yt == 'tr':
        d1 = sameU.tr - sameU.er
        ax.scatter(sameU.tr, d1, s=2, color=BLUE, alpha=0.6, edgecolors='none', rasterized=True)
        ax.set_ylabel(r'$\Delta r$ (tmpl − EDP2 PSF)')
    else:
        ax.scatter(sameU.tr, sameU.gr - sameU.egr, s=2, color=BLUE, alpha=0.6, edgecolors='none', rasterized=True, label='g−r')
        ax.scatter(sameU.tr, sameU.ri - sameU.eri, s=2, color='#e8692f', alpha=0.6, edgecolors='none', rasterized=True, label='r−i')
        ax.set_ylabel(r'$\Delta$colour (tmpl − EDP2 PSF)')
        ax.legend(loc='lower left', fontsize=7.5, markerscale=3, frameon=False)
    ax.axhline(0, color=INK, lw=0.6)
    ax.set_xlim(15, 25)
    ax.set_xticks([16, 18, 20, 22, 24])
    ax.set_ylim(-0.6, 0.6)
    ax.set_xlabel(r'$r_{\rm tmpl}$ (AB)')
    ax.text(0.04, 0.95, f'({next(letters)})', transform=ax.transAxes, va='top', fontweight='bold')
    ax.minorticks_on()
handles = [Line2D([], [], color=RED, lw=1.2, label='resolved (contours 50, 80, 95%)'),
           Line2D([], [], marker='o', ls='none', color=BLUE, markersize=4, label='unresolved')]
fig.legend(handles=handles, loc='lower center', ncol=2, frameon=False, bbox_to_anchor=(0.5, -0.01), fontsize=8.5)
for ext in ('pdf', 'png'):
    fig.savefig(figs / f'Fig11_template_vs_EDP2_same_objects.{ext}', bbox_inches='tight', pad_inches=0.08)
plt.close(fig)
cmp_fig_n = {'unresolved_points_top': int(np.isfinite(Ug.egi).sum() and (np.isfinite(Ug.egi) & np.isfinite(Ug.er)).sum()),
             'unresolved_points_bottom': int((np.isfinite(Ug.egr) & np.isfinite(Ug.eri)).sum()),
             'resolved_top': int((np.isfinite(Rg.egi) & np.isfinite(Rg.er)).sum()),
             'resolved_bottom': int((np.isfinite(Rg.egr) & np.isfinite(Rg.eri)).sum())}

summary = {'recount': rc, 'pixel_test': px['pixel_test'], 'medians': med, 'blue_clump_unresolved': clump,
           'edp2_psf_ugr_usable_among_fig11': n_edp2_u, 'comparison_figure_counts': cmp_fig_n,
           'unresolved_fraction_fig11': rc['fig11_unresolved'] / rc['fig11_total'],
           'unresolved_fraction_all_matched_usable': rc['all_matched_by_class']['0'] / (rc['all_matched_by_class']['0'] + rc['all_matched_by_class']['1'])}
(tabs / 'Fig11_summary.json').write_text(json.dumps(summary, indent=1, default=str) + '\n')
print(num.to_string(index=False))
print(comp.to_string(index=False))
print(json.dumps({k: summary[k] for k in ('medians', 'blue_clump_unresolved', 'edp2_psf_ugr_usable_among_fig11', 'comparison_figure_counts',
                                         'unresolved_fraction_fig11', 'unresolved_fraction_all_matched_usable')}, indent=1, default=str))
print(cmp[cmp.subset != 'x'].to_string(index=False))

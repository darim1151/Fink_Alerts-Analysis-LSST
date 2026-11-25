"""Schematic of the three alert fluxes (templateFlux, scienceFlux, psfFlux), drawn in the style of the paper's figures.

    python draw_flux_schematic.py <out dir>

Same content as fig_m1 in scripts/paper_figures_pub.py. The only addition is a scienceFlux arrow from zero up to
each visit, so all three fluxes are drawn the same way (templateFlux and scienceFlux from zero, psfFlux from the
template level). Style constants (font, sizes, frame, palette) are copied from
paper_figures_pub.STYLE. Static-sky quantities are grey, as the template and EDP2 densities are in the colour figures,
difference fluxes are blue, as the transient colours are, and the one negative difference flux is the paper's red.
"""
import sys
from pathlib import Path

import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt

WIDTH = 7.2
BLUE, ORANGE, GREY, RED, INK = '#2a78d6', '#e8692f', '#7d7b76', '#d0413b', '#111111'
STATIC = '#52514e'        # INK_MUTED of the colour-diagram contours
BAND = '#eeedea'
plt.rcParams.update({
    'font.family': 'DejaVu Sans', 'font.size': 9, 'axes.labelsize': 10, 'xtick.labelsize': 8.5, 'ytick.labelsize': 8.5,
    'axes.linewidth': 0.8, 'xtick.major.width': 0.8, 'ytick.major.width': 0.8, 'xtick.major.size': 3.5,
    'ytick.major.size': 3.5, 'xtick.direction': 'in', 'ytick.direction': 'in', 'axes.edgecolor': INK,
    'axes.labelcolor': INK, 'xtick.color': INK, 'ytick.color': INK, 'text.color': INK, 'mathtext.default': 'regular',
    'savefig.dpi': 300, 'pdf.fonttype': 42})

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
fig, ax = plt.subplots(figsize=(WIDTH, 2.9))
tl, v1, v2 = 0.45, 0.85, 0.22          # template level, visit 1 (brighter), visit 2 (fainter); flux 0 at the bottom
x0, x1, x2 = 0.06, 0.50, 0.685          # templateFlux arrow, visit 1, visit 2
dx = 0.024                              # the psfFlux arrow runs just right of each visit's scienceFlux arrow


def arrow(x, ya, yb, color, lw=1.5):
    ax.annotate('', xy=(x, yb), xytext=(x, ya),
                arrowprops=dict(arrowstyle='-|>', color=color, lw=lw, mutation_scale=11, shrinkA=0, shrinkB=0))


# template: earlier images and the static-sky level they define
ax.axvspan(0.0, 0.30, color=BAND, lw=0, zorder=0)
ax.text(0.016, 0.955, 'template images (coadd)', color=GREY, fontsize=8.5, ha='left', va='top')
ax.axhline(tl, color=STATIC, ls='--', lw=1.0, zorder=1)
ax.text(0.995, tl + 0.025, 'template level', color=STATIC, fontsize=8.5, ha='right', va='bottom')
arrow(x0, 0.0, tl, STATIC)
ax.text(x0 + 0.018, 0.29, 'templateFlux', color=STATIC, fontsize=9, fontweight='bold', va='center')
ax.text(x0 + 0.018, 0.21, 'static sky', color=STATIC, fontsize=8.5, va='center')

# visits: scienceFlux from zero up to the visit, psfFlux from the template level to the visit
for x, y, lab in ((x1, v1, 'visit 1'), (x2, v2, 'visit 2')):
    arrow(x, 0.0, y - 0.03, ORANGE, lw=1.2)
    ax.plot([x], [y], 'o', color=INK, ms=6.5, zorder=5)
    ax.text(x - 0.018, y, lab, fontsize=9, fontweight='bold', ha='right', va='center')
ax.text(x1 - 0.016, 0.30, 'scienceFlux', color=ORANGE, fontsize=9, fontweight='bold', ha='right', va='center')
ax.text(x1 - 0.016, 0.22, 'level at the visit', color=ORANGE, fontsize=8.5, ha='right', va='center')
arrow(x1 + dx, tl, v1, BLUE)
ax.text(x1 + dx + 0.016, (tl + v1) / 2 + 0.04, 'psfFlux > 0', color=BLUE, fontsize=9, fontweight='bold', va='center')
ax.text(x1 + dx + 0.016, (tl + v1) / 2 - 0.04, 'brighter than the template', color=BLUE, fontsize=8.5, va='center')
arrow(x2 + dx, tl, v2, RED)
ax.text(x2 + dx + 0.016, (tl + v2) / 2 + 0.035, 'psfFlux < 0', color=RED, fontsize=9, fontweight='bold', va='center')
ax.text(x2 + dx + 0.016, (tl + v2) / 2 - 0.045, 'fainter than the template', color=RED, fontsize=8.5, va='center')

ax.set_xlim(0, 1)
ax.set_ylim(0, 1)
ax.set_xticks([])
ax.set_yticks([0])
ax.tick_params(axis='y', left=True, right=False)
ax.set_xlabel('time →')
ax.set_ylabel("flux at the object's position →")
for ext in ('pdf', 'png'):
    fig.savefig(out / f'Fig04_alert_fluxes_schematic.{ext}', bbox_inches='tight', pad_inches=0.08)
print(out)

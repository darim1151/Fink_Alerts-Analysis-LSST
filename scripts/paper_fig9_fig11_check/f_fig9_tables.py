"""Fig. 9 number table from the Arnor recount (d_fig9_recount.py) and the pixel re-draw (e_fig9_pixel_driver.py).

    python f_fig9_tables.py <run folder>
"""
import json
import sys
from pathlib import Path

import pandas as pd

run = Path(sys.argv[1])
r = json.loads((run / 'arnor/d_fig9/fig9_recount.json').read_text())
px = json.loads((run / 'arnor/e_fig9_pixel/e_fig9_pixel.json').read_text())
A, B, C = r['panel_a'], r['panel_b'], r['panel_c_d']
fi = px['figure_info']
rows = [
    ('(a)', 'DIAObjects with nearest EDP2 Object within 1"', '', A['matched_within_1arcsec']),
    ('(a)', '  outside the Galactic bulge and plane programmes', '', A['outside_bulge_plane']),
    ('(a)', '  + usable EDP2 cModel g', '', A['plus_g']),
    ('(a)', '  + usable EDP2 cModel r', '', A['plus_g_r']),
    ('(a)', '  + usable EDP2 cModel i = panel (a)', fi['a_edp2_static'], A['panel_a_N']),
    ('(a)', '    of which unresolved / resolved / unusable r_extendedness',
     '', f"{A['panel_a_by_class']['0']:,} / {A['panel_a_by_class']['1']:,} / {A['panel_a_by_class']['unusable']:,}"),
    ('(a)', '    inside the plotted colour range', '', A['panel_a_in_frame']),
    ('(b)', 'Usable g, r, i templates, no EDP2 Object within 1", outside bulge/plane', fi['b_template_unmatched'], B['panel_b_N']),
    ('(b)', '  same rule over all fields', '', B['template_gri_unmatched_all_fields']),
    ('(b)', '  outside the plotted range', fi['b_outside_plot'], B['panel_b_N'] - B['panel_b_in_frame']),
    ('old (a)', 'Usable g, r, i templates outside bulge/plane, any match state (old caption 8,154)', 8154,
     B['template_gri_outside_bulge_plane_all_match_states']),
    ('(c)', 'Object-nights with same-night g, r, i difference flux > 0, S/N >= 5', '', C['object_nights_with_gri']),
    ('(c)', 'Objects (one night each) outside bulge/plane = panel (c)', fi['c_transients'], C['panel_c_N']),
    ('(c)', '  objects with such a night inside bulge/plane', '', C['objects_all_fields'] - C['panel_c_N']),
    ('(c)', '  outside the plotted range', fi['c_outside_plot'], C['panel_c_outside_frame']),
    ('(c)', '  EDP2 unresolved / resolved / unusable / no EDP2 Object within 1"', '',
     ' / '.join(f"{C['panel_c_by_match_class'][k]:,}" for k in ('matched_unresolved', 'matched_resolved', 'matched_unusable', 'unmatched'))),
    ('(c)', '  by programme', '', '; '.join(f'{k} {v:,}' for k, v in C['panel_c_by_field'].items())),
    ('(d)', 'Objects in histogram (0.25 h bins, 0-6 h)', '', sum(C['panel_d_counts'])),
    ('(d)', 'Time span min / median / max (h)', '', ' / '.join(f"{C['span_hours'][k]:.2f}" for k in ('min', 'median', 'max'))),
]
t = pd.DataFrame(rows, columns=['panel', 'quantity', 'value drawn in the figure', 'independent recount'])
t.to_csv(run / 'tables/Fig9_numbers.csv', index=False)
checks = {
    'figure re-drawn with original code and inputs: pixels differing from delivered PNG': f"{px['pixels_differing']} of {px['pixels']:,}",
    'difference colours rebuilt from raw alerts: same objects and nights as the plotted table': C['ids_identical_to_published_table'] and C['nights_identical'],
    'max |g-r| / |r-i| difference vs plotted table (mag)': f"{C['max_abs_diff_gr']:.1e} / {C['max_abs_diff_ri']:.1e}",
    'max time-span difference vs plotted table (h)': C['max_abs_diff_span_hours'],
    'panel (b) ID set equals the plotted rule': B['panel_b_ids_equal_published_rule'],
}
(run / 'tables/Fig9_checks.json').write_text(json.dumps(checks, indent=1) + '\n')
print(t.to_string(index=False))
print(json.dumps(checks, indent=1))

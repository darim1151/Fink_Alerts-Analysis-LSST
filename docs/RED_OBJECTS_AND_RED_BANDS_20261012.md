# g−i > 2 objects and red-band diagrams: methods note

Run `red-objects-and-red-bands-20261012-v2`, made at the mentor's request (review of 2026-10-12). Inputs are the audited
per-object tables of `alert-colour-diagrams-20261011-v3` (template photometry, Fink labels, programme) and
`alert-science-suite-20261008-v1` (Gaia parallax, Legacy DR8 `pstar`). The first-alert band comes from the
analytical catalog, opened read-only (SHA256 `bb308e31…cefb`, unchanged). Nothing in the protected data was written.

## Is the brighter faint limit of the red objects an error?

No. The colour panels contain only objects that raised an alert in g, r and i and have a usable template in each
(median template S/N ≥ 5). Red and blue objects reach a similar faint g limit: 95% have `g_tmpl` < 23.07 (g−i > 2)
and < 23.29 (g−i ≤ 2). The red objects have a median `(g−r)_tmpl` of 1.36, so the same g requirement stops them
1.3 mag brighter in r (95th percentiles `r_tmpl` 21.51 vs 22.85). This is a selection effect of the sample
definition, not a calculation error or a physical cutoff. Earlier figures did not say so; R0 and D1 now do.

## Mentor's hypothesis: mostly detected in u and g

- u plays no role: 12 of the 661 objects have u, g and r templates (59 u alerts in total). u-band alerts come almost
  only from the COSMOS deep-drilling field.
- g triggers them: 339 of 661 (51%) were first detected in g, against 25% of the bluer objects.
- Over all their alerts the band mix is g 27%, r 34%, i 31%, z 7.5%, u 0.3%, y 0.6%. That is not u/g-dominated, and
  only 128 objects have most of their alerts in u or g.

## What the g−i > 2 objects are

661 objects: 524 in deep-drilling fields, 72 in low-dust WFD and 65 in the M49 field. 402 have a Gaia DR3
parallax S/N > 5, 128 more are Legacy point-like, 34 are Legacy extended and 97 have neither. 23 match a SIMBAD
star and 624 have no SIMBAD object within 1″. Fink labels: "SN-like" 381, "non-Periodic (AGN)" 124, "Periodic" 82,
"Long" 53, "Fast" 21. The 145 with i and z templates lie along the M-dwarf sequence in r−i vs i−z. These are consistent with nearby, probably
active, M dwarfs, as the mentor proposed. The 34 extended objects form the faint tail (r_tmpl ≈ 20.5–24, r−i ≈
0.4–1.3), probably galaxies.

## Red-band diagrams

| Diagram | Objects outside bulge and plane | Inside bulge and plane | of them g−i > 2 |
|---|---:|---:|---:|
| i vs i−z | 69,968 | 33,093 | 145 |
| r−i vs i−z | 10,018 | 2,056 | 145 |
| i−z vs z−y | 312 | 0 | 34 |

Bulge and plane fields are shown separately because no extinction correction is applied. Any diagram using y is
limited to 312 objects.

## Figures

R0 g−i > 2 objects over all colour-eligible objects. R1 by Fink CATS label. R2 by SIMBAD group (1″, latest successful
crossmatch). R3 by Gaia parallax and Legacy morphology (rows exclusive, in order). D1 g and r limits, alert share by
band, first-alert band. Z1 red-band diagrams by programme. Z2 red-band diagrams by SIMBAD group outside bulge and
plane.

## Verification and provenance

Independent recount from the 71,042 raw Parquet files with pandas
(`/astro/store/shire/FINK/outputs/red-objects-verification-20261012-v1/verification.json`). The g−i > 2 sample (661),
the g and r 95th percentiles, alert counts by band, first-alert bands, and all i/z/y sample sizes match exactly. The
object IDs are identical and the magnitude difference is ≤ 4e-15. v2 differs from v1 only in the position of the
note in R0; every result and table is identical.

Code: `scripts/red_objects_and_red_bands.py` at `d7a7319c16e653113322e459bf61d02662e6ccc4`, script SHA256
`944aee47584b2b3cb9e8672dda04186f60628752b4885036c4c134c8ebf3184a` (uses `scripts/alert_colour_diagrams.py`, SHA256
`b79e5f24af1356ed5c4c90cda86564695c8c2674a7d2bb9422bd47b439cf5c81`). Outputs:
`/astro/store/shire/FINK/outputs/red-objects-and-red-bands-20261012-v2/`; code copy and run log under `manifests/`.

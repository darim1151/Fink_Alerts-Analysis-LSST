# Alert colour diagrams from template photometry — methods note

Current run `alert-colour-diagrams-20261011-v3` (supersedes the figures of `alert-colour-diagrams-20261008-v1`;
see "Revision after mentor review"). Cohort `accepted_five_window_20260225_20260714`
(`[2026-02-25, 2026-07-14)`, 7,101,947 delivered sources), contract `analysis_contract_v1`.

## What these figures are, and are not

The reference figure (SDSS-style stars vs galaxies) uses coadd CModel and PSF magnitudes.
Fink Light Static alerts carry neither. Per alert, the delivered photometry is, in one band only:

| Field | Meaning (LSST DiaSource, nJy) | Used |
|---|---|---|
| `psfFlux` | PSF flux on the difference image | no (variable part only) |
| `scienceFlux` | forced PSF flux on that epoch's direct image | no (single epoch, bands at different epochs) |
| `templateFlux` | forced PSF flux on the difference-imaging template at the source position | **yes** |
| `lc_features[band]` | Fink SNAD features computed on the `psfFlux` (difference flux) history | no (not static-sky) |

There are no static-sky or catalogue magnitudes in the crossmatch block (`xm` has SIMBAD/Gaia/Legacy
names, types, parallax and photo-z, but no photometry). So:

- Every plotted magnitude is a **template PSF magnitude**, `m_tmpl = 31.4 − 2.5 log10(templateFlux / nJy)` (AB).
  Axes say exactly this. For point sources this approximates the quiescent PSF magnitude; for galaxies it
  is the PSF-weighted flux at the transient's position, not a total magnitude.
- The top-left reference panel (r PSF − CModel vs r) **cannot be made**: there is no extended-source model flux.
  Rubin morphology is therefore unavailable. The science suite substitutes Legacy DR8 `pstar` from Fink's crossmatch.
- Photometric calibration of delivered alert fluxes is not independently validated (project limitation).
- No Galactic extinction correction is applied. 9.9% of colour-eligible objects lie at |b| < 15°.

## Construction

1. Source: read-only `open_catalog` on `final-five-window-analytics-20261006-v1/analytics.duckdb`
   (SHA256 `bb308e31…cefb`, unchanged; raw stat fingerprints re-verified on open).
2. Population `DIA` only (6,311,364 sources, 3,487,175 DIA objects). SSO is kept separate per
   `analysis_contract_v1` and **not plotted**: SSO sources have no object key in Light Static, each alert is
   single-band, and the template has no static counterpart (median SSO `templateFlux` per band lies between −68 and +25 nJy, i.e. consistent with zero against DIA medians of
   15,000–99,000 nJy).
3. Per DIA object and band: median `templateFlux` and median `templateFluxErr` over delivered sources with finite
   flux and error. A band is usable when median flux > 0 and median S/N ≥ 5.
4. Panels: (a) `r_tmpl` vs `(g−i)_tmpl` and (c) `(r−i)_tmpl` vs `(g−r)_tmpl` need usable g, r, i;
   (b) `(g−r)_tmpl` vs `(u−g)_tmpl` needs usable u, g, r.
5. CATS label: Fink `pred.main_label_classifier` from each object's **latest delivered alert** (max
   `observation_mjd_tai`, then max `source_id`). Labels evolve: 5,562 of the 8,293 objects in either panel sample
   (and 929,783 of all DIA objects) changed CATS class across their delivered alerts.
6. CATS codes and Fink's own class names (`fink_science/rubin/cats/utilities.py`): 11 "SN-like", 12 "Fast",
   13 "Long", 21 "Periodic", 22 "non-Periodic (AGN)"; −1 is a first alert that was not classified (identical to
   `pred.is_first`). The broker code comments describe the training content of each class (for 21: "RRLyrae, EB,
   LPV, ..."). **These are classifier outputs, not verified object types.** Against VSX and SIMBAD labels, only
   2–5% of periodic variables and AGN receive their expected class (science suite, K1). Figure titles show the
   label and code only.
7. SIMBAD label: Fink crossmatches each alert with SIMBAD through the CDS xmatch service within **1″**
   (`fink_science/rubin/xmatch/processor.py`; radius in the broker's `CAT_PROPERTIES`, same in 4.1 and 5.0rc0). A null
   type means the service answered and nothing lies within 1″. `"Fail"` means the request errored (HTTP status not 200,
   timeout, connection or parse error), so SIMBAD status is unknown. Per object: the latest SIMBAD type among alerts
   whose crossmatch succeeded; otherwise **No SIMBAD object within 1″** if any alert's crossmatch succeeded;
   otherwise **Crossmatch failed: SIMBAD status unknown** (every alert returned `"Fail"`). The type is grouped
   into Star / QSO-AGN / Galaxy / Other; the full table is `tables/simbad_otype_to_group.csv`.
8. Programme: the observing-programme group (from `target_name`, read from the pinned raw files) held by most of
   the object's alerts, ties alphabetical.

## Coverage and counts

Usable template bands (objects): u 2,085; g 81,757; r 572,538; i 1,610,680; z 1,093,700; y 844.
The delivery is dominated by i and z, so only **8,196** DIA objects (0.24%) enter panels (a, c) and **1,050** enter
panel (b). These are objects detected in ≥3 bands within the cohort, which biases the sample toward long-lived,
repeatedly detected variables and AGN and against short transients. **Panel (b) is a deep-drilling sample:** all
1,050 objects have most of their alerts in deep-drilling visits (99.6% of their 324,818 alerts) and all lie within
1.9° of the COSMOS field centre, because u-band alerts come almost only from there. Panels (a, c) are 60% deep-drilling
(4,890), 27% low-dust WFD (2,234), 13% M49 field (1,030) and 0.5% bulge (42).

| Fink CATS label at latest alert (code) | DIA objects | panels a, c (gri) | panel b (ugr) |
|---|---:|---:|---:|
| "SN-like" (11) | 977,424 | 5,319 | 546 |
| "Fast" (12) | 952 | 134 | 30 |
| "Long" (13) | 2,776 | 346 | 74 |
| "Periodic" (21) | 11,503 | 939 | 101 |
| "non-Periodic (AGN)" (22) | 8,074 | 1,458 | 299 |
| first alert, not classified (−1) | 2,486,446 | 0 | 0 |

| SIMBAD group (v3 rule, step 7) | DIA objects | panels a, c (gri) | panel b (ugr) |
|---|---:|---:|---:|
| Star | 34,138 | 557 | 55 |
| QSO / AGN | 10,001 | 1,045 | 431 |
| Galaxy | 5,531 | 117 | 34 |
| Other SIMBAD type | 730 | 50 | 13 |
| No SIMBAD object within 1″ | 3,195,020 | 6,409 | 517 |
| Crossmatch failed: SIMBAD status unknown | 241,755 | 18 | 0 |

Template stability: for objects with ≥2 sources in a band, the median (max − min)/median of `templateFlux` is
3–5% in g, r, i, z (90th percentile 11–29%), consistent with templates that are mostly but not always identical
across alerts. Fink versions in the data: broker 4.1 and 5.0rc0, science 8.39.0–8.52.0 (`tables/fink_versions.csv`).

## Figures

- `fig1_overview_all_dia` — all colour-eligible DIA objects; grey log density, contours enclose 50/80/95%
  (3×3-smoothed histogram). This is the background in every facet and will be replaced by the control sample.
- `fig2_by_fink_cats_class` — one row per Fink CATS label (blue), over the fig1 background.
- `fig3_by_fink_simbad_crossmatch` — one row per SIMBAD group (blue), over the fig1 background.
- Panel (b) of every figure states that its objects are deep-drilling-field objects.

Observations (inferred from the figures, not tested): the template colours trace a stellar locus with an M-dwarf
branch at `g−r ≈ 1.4`, and SIMBAD QSO/AGN objects sit at `u−g ≈ 0–0.5` as expected for UV-excess sources.
Of the 642 red-branch objects (`g−r > 1.2`, `r−i > 0.6`), 367 carry the Fink label "SN-like" and 128
"non-Periodic (AGN)", while 23 have a SIMBAD stellar type and 608 have no SIMBAD object within 1″. With Legacy DR8
morphology, 455 of 483 are point-like (science suite). These are M dwarfs, probably flaring, carrying transient and
AGN labels.

## Revision after mentor review (2026-10-11, run v3)

Feedback from the project mentor flagged that "Periodic (RR Lyr, EB, LPV)" objects sit at u−g ≈ 0–0.5, where RR Lyrae
(u−g ≈ 1.0–1.1 in SDSS) should not be. Checks (`/astro/store/shire/FINK/outputs/mentor-feedback-checks-20261011-v1/`)
found the photometry sound and the title wrong:

- The 101 Fink "Periodic" objects in panel (b) are 37 SIMBAD QSO/AGN, 45 unlabelled, 8 non-RR-Lyrae VSX variables,
  5 SIMBAD stars, 4 other SIMBAD types and 2 VSX RR Lyrae. The title had copied Fink's training-content comment.
- Known RR Lyrae (VSX RR* or SIMBAD RR*) with u, g, r: 10 objects, `(u−g)_tmpl` 16/50/84% = 0.76 / 0.86 / 0.88.
  Rubin's published conversion (RTN-099, LSSTComCam vs SDSS: §1.2.3 synthetic Pickles u relation,
  RMS 0.021 mag for −0.4 < g−i ≤ 0.8; §1.3.5 observed DR18 g relation, valid 0.2 < g−i ≤ 3.0) shifts the 8 with
  i-band templates by +0.23 mag on average, to SDSS-equivalent u−g 16/50/84% = 1.01 / 1.06 / 1.15. That matches the
  expected 1.0–1.1. Caveats: the relations are for LSSTComCam, not LSSTCam; the u relation is synthetic; and only 4 of
  the 8 have g−i inside the g relation's validity range. Per-object values:
  `/astro/store/shire/FINK/outputs/mentor-corrections-verification-20261011-v1/rrlyrae_rtn099_u_conversion.csv`.
- SIMBAD QSO/AGN in panel (b) (431) have `(u−g)_tmpl` median 0.22, and SIMBAD stars plus stars with Gaia parallax
  S/N > 5 follow a stellar-locus shape (median u−g 1.19 → 2.51 for g−r 0.4–0.6 → 1.2–1.6).

Changes in v3, code only (photometry, cuts and samples unchanged):
1. CATS rows are titled with Fink's label and code, and the figure states that labels are classifier outputs.
2. SIMBAD groups use the latest successful crossmatch (step 7). In v1, 4,385 objects whose latest alert returned
   `"Fail"` but which matched SIMBAD on another alert were shown as "Crossmatch failed" (312 of them in panels a, c),
   93,782 more were "Crossmatch failed" although another alert's crossmatch had succeeded with no match, and 810
   objects whose latest alert had no match now carry the type an earlier alert matched.
3. Panel (b) states its deep-drilling-field composition.

Verification (`mentor-corrections-verification-20261011-v1/verification.json`, independent pandas re-derivation from
the 71,042 raw files): SIMBAD groups match on all 3,487,175 objects; `pred.main_label_crossmatch` equals
`xm.simbad_otype` on every alert; latest CATS labels match raw and v1 exactly; panel flags and magnitudes are
identical to v1 (maximum difference 0). v3 and v2 tables are identical apart from object mean RA/Dec, which differ by
≤ 2e-13° from floating-point summation order.

## Independent audit (2026-10-08)

All figure inputs were re-derived from the raw Parquet files on disk (71,042 files, globbed per delivery, read with
pyarrow and aggregated in pandas), bypassing the analytical catalog and its views. Results:

- Rows 7,101,947, unique `diaSourceId` 7,101,947, DIA 6,311,364, SSO 790,583, ambiguous 0, DIA objects 3,487,175.
- Usable-band object counts, panel samples (8,196 gri; 1,050 ugr; 8,293 in either) and latest CATS and SIMBAD
  labels match the run table exactly; one row per DIA object, no duplicates.
- Magnitudes agree to 6.6e-5 mag; the only difference is the rounded AB zero point (31.4 used; exact
  2.5 log10(3631e9) = 31.40007). Colours agree to floating-point precision; colour signs are bluer-minus-redder band.
- No object's RA straddles 0/360, so the mean RA/Dec used for Galactic latitude is valid.
- CATS codes were checked against broker 4.1 and 5.0rc0 data: code −1 always has score 0 and coincides exactly with
  `pred.is_first`; no SN-like (11) source has score 0, so unprocessed first alerts do not leak into class 11.
  8 plotted objects carry a class with a null score at their latest snapshot.

Corrections to this note: the SSO template-flux range and the label-evolution denominator above were corrected.
Axis ranges are slightly wider than the reference figure (r_tmpl 14–26, because 489 of 8,196 objects are brighter
than r_tmpl = 18; colour axes extended by 0.25–0.5 mag). Contour levels are computed over objects inside each panel's range.
Not available, and therefore not applied: pixel flags (saturation, blending, edge), so bright or crowded templates
cannot be cleaned; the S/N cut uses median flux over median error, not a per-alert S/N.

## Provenance

- Current code: `scripts/alert_colour_diagrams.py` at commit `0c5f5dd247c89835ca05fda748152a9090ae83ea`
  (branch `claude/project-thread-kfqlox`), script SHA256 `b79e5f24af1356ed5c4c90cda86564695c8c2674a7d2bb9422bd47b439cf5c81`;
  outputs `/astro/store/shire/FINK/outputs/alert-colour-diagrams-20261011-v3/`, code copy and run log
  `/astro/store/shire/FINK/manifests/alert-colour-diagrams-20261011-v3/`. Run v2 (commit `bd68e10`) differs from v3 only
  in the wording of the panel (b) note.
- Original v1 code: commit `e4a5b8ec307706c8511f5e40c295bd4a0d0ebc97`, script SHA256 `9ea5d4af653265ffd97753bcc271d88342210d88c5eebc303ac6a50bf1805c48`.
- Environment: `/astro/store/shiren/mdarim/envs/fink-lsst-analysis-g4b-py311` (Python 3.11.16, duckdb 1.4.4,
  numpy 2.4.6, pandas 3.0.6, matplotlib 3.11.2, astropy 8.0.1, pyarrow 25.0.1).
- v1 outputs (data plane, kept unchanged): `/astro/store/shire/FINK/outputs/alert-colour-diagrams-20261008-v1/`
  (`figures/`, `tables/`, `summary.json`, per-object table `tables/dia_object_template_photometry.parquet`).
- Exact code copy and run log: `/astro/store/shire/FINK/manifests/alert-colour-diagrams-20261008-v1/`.
- No raw, manifest, catalog or cohort file was written. No Fink, Kafka or API query was made.

# Alert colour diagrams from template photometry — methods note

Run `alert-colour-diagrams-20261008-v1`, cohort `accepted_five_window_20260225_20260714`
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
  Morphological star/galaxy separation is therefore unavailable; the label facets below are the only split.
- Photometric calibration of delivered alert fluxes is not independently validated (project limitation).
- No Galactic extinction correction is applied. 9.9% of colour-eligible objects lie at |b| < 15°.

## Construction

1. Source: read-only `open_catalog` on `final-five-window-analytics-20261006-v1/analytics.duckdb`
   (SHA256 `bb308e31…cefb`, unchanged; raw stat fingerprints re-verified on open).
2. Population `DIA` only (6,311,364 sources, 3,487,175 DIA objects). SSO is kept separate per
   `analysis_contract_v1` and **not plotted**: SSO sources have no object key in Light Static, each alert is
   single-band, and the template has no static counterpart (median SSO `templateFlux` is −8 to −68 nJy per band).
3. Per DIA object and band: median `templateFlux` and median `templateFluxErr` over delivered sources with finite
   flux and error. A band is usable when median flux > 0 and median S/N ≥ 5.
4. Panels: (a) `r_tmpl` vs `(g−i)_tmpl` and (c) `(r−i)_tmpl` vs `(g−r)_tmpl` need usable g, r, i;
   (b) `(g−r)_tmpl` vs `(u−g)_tmpl` needs usable u, g, r.
5. Labels: Fink `pred.main_label_classifier` (= CATS broad class) and `pred.main_label_crossmatch`
   (= SIMBAD `otype`), taken from each object's **latest delivered snapshot** (max `observation_mjd_tai`,
   then max `source_id`). Labels evolve: 5,562 of the colour-eligible objects (and 929,783 of all DIA objects)
   changed CATS class across their delivered snapshots.
6. CATS codes, from `fink_broker/rubin/science.py`: 11 SN-like, 12 Fast (KN, µlens, novae), 13 Long (SLSN, TDE, PISN),
   21 Periodic (RR Lyr, EB, LPV), 22 Non-periodic (AGN), −1 not processed (single detection).
   SIMBAD `otype` is grouped into Star / QSO-AGN / Galaxy / Other; the full otype→group table is
   `tables/simbad_otype_to_group.csv`.

## Coverage and counts

Usable template bands (objects): u 2,085; g 81,757; r 572,538; i 1,610,680; z 1,093,700; y 844.
The delivery is dominated by i and z, so only **8,196** DIA objects (0.24%) enter panels (a, c) and **1,050** enter
panel (b). These are objects detected in ≥3 bands within the cohort, which biases the sample toward long-lived,
repeatedly detected variables and AGN and against short transients.

| CATS class (latest) | DIA objects | panels a, c (gri) | panel b (ugr) |
|---|---:|---:|---:|
| SN-like (11) | 977,424 | 5,319 | 546 |
| Fast (12) | 952 | 134 | 30 |
| Long (13) | 2,776 | 346 | 74 |
| Periodic (21) | 11,503 | 939 | 101 |
| Non-periodic / AGN (22) | 8,074 | 1,458 | 299 |
| Not processed (−1) | 2,486,446 | 0 | 0 |

| SIMBAD group (latest) | DIA objects | panels a, c (gri) | panel b (ugr) |
|---|---:|---:|---:|
| Star | 30,470 | 342 | 51 |
| QSO / AGN | 9,313 | 952 | 430 |
| Galaxy | 4,734 | 110 | 33 |
| Other SIMBAD type | 688 | 48 | 13 |
| No SIMBAD match | 3,102,048 | 5,493 | 521 |
| Crossmatch failed (`Fail`) | 339,922 | 1,251 | 2 |

Template stability: for objects with ≥2 sources in a band, the median (max − min)/median of `templateFlux` is
3–5% in g, r, i, z (90th percentile 11–29%), consistent with templates that are mostly but not always identical
across alerts. Fink versions in the data: broker 4.1 and 5.0rc0, science 8.39.0–8.52.0 (`tables/fink_versions.csv`).

## Figures

- `fig1_overview_all_dia` — all colour-eligible DIA objects; grey log density, contours enclose 50/80/95%
  (3×3-smoothed histogram). This is the background in every facet and will be replaced by the control sample.
- `fig2_by_fink_cats_class` — one row per CATS class (blue), over the fig1 background.
- `fig3_by_fink_simbad_crossmatch` — one row per SIMBAD group (blue), over the fig1 background.

Observations (inferred from the figures, not tested): the template colours trace a stellar locus with an M-dwarf
branch at `g−r ≈ 1.4`, and SIMBAD QSO/AGN objects sit at `u−g ≈ 0–0.5` as expected for UV-excess sources.
Of the 642 red-branch objects (`g−r > 1.2`, `r−i > 0.6`), 367 are CATS "SN-like" and 128 "Non-periodic (AGN)",
while only 20 have a SIMBAD stellar type (562 have no SIMBAD match). This suggests flaring or variable M dwarfs
carrying transient/AGN labels.

## Provenance

- Code: `scripts/alert_colour_diagrams.py` at commit `e4a5b8ec307706c8511f5e40c295bd4a0d0ebc97`
  (branch `claude/project-thread-kfqlox`), script SHA256 `9ea5d4af653265ffd97753bcc271d88342210d88c5eebc303ac6a50bf1805c48`.
- Environment: `/astro/store/shiren/mdarim/envs/fink-lsst-analysis-g4b-py311` (Python 3.11.16, duckdb 1.4.4,
  numpy 2.4.6, pandas 3.0.6, matplotlib 3.11.2, astropy 8.0.1, pyarrow 25.0.1).
- Outputs (data plane): `/astro/store/shire/FINK/outputs/alert-colour-diagrams-20261008-v1/`
  (`figures/`, `tables/`, `summary.json`, per-object table `tables/dia_object_template_photometry.parquet`).
- Exact code copy and run log: `/astro/store/shire/FINK/manifests/alert-colour-diagrams-20261008-v1/`.
- No raw, manifest, catalog or cohort file was written. No Fink, Kafka or API query was made.

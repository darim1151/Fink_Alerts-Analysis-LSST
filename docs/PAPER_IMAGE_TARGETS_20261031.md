# Paper image targets (2026-10-31)

Thirty DIA objects for the paper's image figures: Rubin alert stamps (science, template, difference) and gri colour
mosaics of the template stamps. Selected by `scripts/select_image_targets.py` at commit `42473a7de5ff0ebd09442ca6ca388dcc11b1949a` (run
`image-targets-20261031-v1`), from the exported colour table `fink_dia_template_colours.csv` (sha256 `b9435f31c8049a87281ca9b91ca780a5ab0abeec4cc3801cb65cf7bce3328587`,
159,777 rows; written by `scripts/export_colour_plot_table.py`). It holds the same rows as the paper run's export
`notebooks-paper-20261015-v2/04_stellar_locus/fink_dia_template_colours.csv.gz` in a different row order (both files,
line-sorted, hash to `f9628664ed60fb3702b1fd58770bfe052e1f42d274fef47f2d0c6c03362a51bb`); the selection does not depend on row order. The table reproduces the paper's panel counts:
8,196 objects with usable g, r and i templates, 1,050 with u, g and r, and 661 with (g − i) > 2.

## Selection

Objects outside the Galactic bulge and plane fields with template r < 21. Each object goes to the first group it
qualifies for. Within a group the five objects with the lowest sha256(`"20261030:<diaObjectId>"`) are taken.

| Group | Definition | Qualifying |
|---|---|---|
| G1 | Gaia parallax star, g - i <= 2 | 196 |
| G2 | Red, g - i > 2 | 607 |
| G3 | SIMBAD QSO / AGN | 786 |
| G4 | SIMBAD galaxy | 53 |
| G5 | Blue u - g < 0.6, no Gaia parallax | 465 |
| G6 | SN-like, no SIMBAD match, Legacy pstar < 0.5 | 789 |

Definitions: G1 Gaia DR3 parallax S/N > 5 (Fink crossmatch) and (g − i) ≤ 2; G2 (g − i) > 2; G3 SIMBAD QSO/AGN; G4
SIMBAD galaxy; G5 (u − g) < 0.6 without Gaia parallax S/N > 5; G6 CATS SN-like, no SIMBAD object within 1″ and Legacy
DR8 pstar < 0.5. G1–G4 and G6 need usable g, r and i templates, G5 needs u, g and r. Colours are template PSF colours in
the native LSST system without extinction correction; the G5 cut is not converted to SDSS.

## Checks

- Every row was re-derived from the input table with independent code: positions, group definitions and the hash
  order of G1 agree.
- Positions are plain means of the delivered alert positions. Every target has at least 169 other colour-table objects
  of the same programme within 1.75°, so none is displaced by averaging RA across 0°/360°.

## Targets

| Name | Group | diaObjectId | RA (deg) | Dec (deg) | Field | r_tmpl | g−r | r−i | CATS label | SIMBAD |
|---|---|---|---|---|---|---|---|---|---|---|
| G1-01 | Gaia parallax star, g - i <= 2 | `170525501750248741` | 289.005416 | -19.482804 | Low-dust WFD | 17.32 | 0.80 | 0.29 | SN-like |  |
| G1-02 | Gaia parallax star, g - i <= 2 | `170028485848334522` | 59.693709 | -48.046674 | Deep drilling fields | 16.70 | 0.81 | 0.29 | SN-like |  |
| G1-03 | Gaia parallax star, g - i <= 2 | `170529889038369425` | 288.099283 | -20.615491 | Low-dust WFD | 17.37 | 0.63 | 0.31 | SN-like | EB* |
| G1-04 | Gaia parallax star, g - i <= 2 | `170028485656969247` | 63.996095 | -48.557448 | Deep drilling fields | 17.81 | 1.19 | 0.78 | non-Periodic (AGN) |  |
| G1-05 | Gaia parallax star, g - i <= 2 | `170600262194954455` | 222.309564 | -39.043363 | Low-dust WFD | 16.84 | 1.13 | 0.06 | SN-like | EB* |
| G2-01 | Red, g - i > 2 | `313831458355544128` | 60.762642 | -48.787452 | Deep drilling fields | 19.35 | 1.41 | 1.96 | non-Periodic (AGN) |  |
| G2-02 | Red, g - i > 2 | `170028488860893394` | 65.391456 | -47.792610 | Deep drilling fields | 17.19 | 1.27 | 0.88 | SN-like |  |
| G2-03 | Red, g - i > 2 | `170631089870079235` | 289.364479 | -20.054570 | Low-dust WFD | 19.86 | 1.34 | 1.11 | SN-like |  |
| G2-04 | Red, g - i > 2 | `313888627022102701` | 150.968860 | 3.570247 | Deep drilling fields | 19.95 | 1.40 | 1.28 | non-Periodic (AGN) |  |
| G2-05 | Red, g - i > 2 | `170028526450245754` | 185.477197 | 7.806916 | M49 field | 17.82 | 1.27 | 1.07 | SN-like |  |
| G3-01 | SIMBAD QSO / AGN | `313871014355271825` | 150.659380 | 1.629572 | Deep drilling fields | 19.07 | -0.04 | -0.08 | SN-like | QSO |
| G3-02 | SIMBAD QSO / AGN | `170028511095423114` | 151.038440 | 0.991485 | Deep drilling fields | 20.69 | 0.03 | 0.37 | SN-like | QSO |
| G3-03 | SIMBAD QSO / AGN | `170028510925554093` | 150.782104 | 3.711878 | Deep drilling fields | 20.33 | 0.36 | 0.02 | SN-like | QSO |
| G3-04 | SIMBAD QSO / AGN | `170028512566050823` | 150.477318 | 0.757192 | Deep drilling fields | 18.22 | 0.09 | 0.04 | SN-like | QSO |
| G3-05 | SIMBAD QSO / AGN | `313690716955475971` | 7.867969 | -44.320100 | Deep drilling fields | 20.05 | 0.34 | 0.28 | non-Periodic (AGN) | AGN |
| G4-01 | SIMBAD galaxy | `313893021597302819` | 149.669190 | 2.907092 | Deep drilling fields | 18.84 | 0.21 | -0.08 | non-Periodic (AGN) | G |
| G4-02 | SIMBAD galaxy | `170028510777180307` | 149.455862 | 1.683390 | Deep drilling fields | 19.36 | 0.72 | 0.11 | SN-like | G |
| G4-03 | SIMBAD galaxy | `170028527121858636` | 185.978062 | 8.315352 | Low-dust WFD | 20.61 | 0.81 | 0.53 | Long | rG |
| G4-04 | SIMBAD galaxy | `313875415229267970` | 150.492472 | 1.459877 | Deep drilling fields | 19.57 | 0.17 | 0.19 | SN-like | G |
| G4-05 | SIMBAD galaxy | `313980947898302501` | 149.218250 | 2.596434 | Deep drilling fields | 19.84 | 0.15 | 0.36 | SN-like | G |
| G5-01 | Blue u - g < 0.6, no Gaia parallax | `170028520410447938` | 149.070933 | 2.483821 | Deep drilling fields | 19.38 | 0.35 | 0.04 | Long | QSO |
| G5-02 | Blue u - g < 0.6, no Gaia parallax | `170226393761185825` | 150.862624 | 3.438101 | Deep drilling fields | 20.38 | 0.12 | 0.16 | non-Periodic (AGN) | QSO |
| G5-03 | Blue u - g < 0.6, no Gaia parallax | `313875416811044971` | 149.432414 | 1.432030 | Deep drilling fields | 20.66 | 0.13 | -0.22 | non-Periodic (AGN) | QSO |
| G5-04 | Blue u - g < 0.6, no Gaia parallax | `313888627036782664` | 150.580886 | 2.847099 | Deep drilling fields | 20.38 | 0.36 | -0.11 | Periodic | QSO |
| G5-05 | Blue u - g < 0.6, no Gaia parallax | `313853517533544491` | 149.631864 | 3.802024 | Deep drilling fields | 20.67 | 0.14 | -0.05 | non-Periodic (AGN) | QSO |
| G6-01 | SN-like, no SIMBAD match, Legacy pstar < 0.5 | `170028485718835325` | 61.985830 | -46.495061 | Deep drilling fields | 20.63 | 0.10 | 0.15 | SN-like |  |
| G6-02 | SN-like, no SIMBAD match, Legacy pstar < 0.5 | `170028526619590752` | 185.870163 | 7.155427 | M49 field | 20.33 | 0.19 | -0.07 | SN-like |  |
| G6-03 | SN-like, no SIMBAD match, Legacy pstar < 0.5 | `313871013683134625` | 61.382545 | -47.526338 | Deep drilling fields | 19.43 | 0.91 | 0.19 | SN-like |  |
| G6-04 | SN-like, no SIMBAD match, Legacy pstar < 0.5 | `170028485807964347` | 62.018012 | -49.434828 | Deep drilling fields | 19.47 | -0.12 | 0.19 | SN-like |  |
| G6-05 | SN-like, no SIMBAD match, Legacy pstar < 0.5 | `313963359506530828` | 63.721362 | -46.190519 | Deep drilling fields | 20.61 | 0.17 | 0.14 | SN-like |  |

Outputs (not in Git): `image_targets.csv`, `image_targets_radec.csv` (name, RA, Dec), `summary.json` and `README.txt` in
the project folder `fink-paper/image-targets-20261031-v1/`. `image_targets_radec.csv` sha256 `fd97cd2d8edf0f408e3f7a6d6b4e592cce538e62274ca5de3d27eeb1ab955628`.

## Alert stamps

Run `alert-stamps-20261031-v1` of `scripts/fetch_alert_stamps.py` (commit `84d9f7a`), made on 2026-10-31 from the owner's
laptop because the cloud environment could not reach the Fink API. For each target the alert with the largest
|psfFlux|/psfFluxErr between 2026-02-25 and 2026-07-13 (midpointMjdTai compared with UTC-date MJDs; no target has an
alert within the 37 s TAI−UTC offset of either boundary) was taken, and its science, template and difference stamps
were downloaded as FITS: 30 of 30 targets, 120 requests, no failures. Each stamp has the image (nJy), a variance
extension `UNCERT` and a `PSFIMAGE`. All 121 files were re-checked against their recorded SHA-256 after transfer.

Fink's stamp headers carry CRVAL/CRPIX/PC (0.2″ pixels) but no CTYPE. Read as a tangent projection, the position angle
of the pixel +Y axis agrees with the header ROTPA within 2.2° for all 90 stamps, so the north/east arrows in the
figure (`scripts/plot_alert_stamps.py`, run `alert-stamps-figure-20261031-v2`, commit `fe9c100`) are drawn from it.

Alert-count check against our delivered data: 27 of 30 targets have the same number of in-window alerts per band in
Fink's API. G3-03, G4-01 and G4-02 each have one more i-band alert in the API. All three have alerts from two
consecutive i-band exposures around midnight UTC on 2026-04-04/05 (midpointMjdTai 61134.99953 and 61135.00000);
G3-03 has only the first. A read-only query of the analysis catalog on Arnor (2026-10-31) settles it: the later
exposure (61135.000003) is in our data with 834 i-band alerts, including the API's alerts for G4-01, G4-02 and G5-05,
while the earlier one (61134.99953, 2026-04-04 23:58:43 UTC) has no rows at all, and our data has no alerts between
61134.95 and 61135.0. That whole exposure is absent from Fink's Data Transfer delivery although Fink's API serves it
(diaSourceIds 170226393356435857, 170226393325502602, 170226393341755507). Our delivery validation still holds (every
message in the delivered topics was received). Fink's own nightly statistics (`api/v1/statistics`, checked 2026-11-01
with `scripts/compare_fink_statistics.py`, run `fink-statistics-check-20261101-v1`) agree with our delivery exactly
over the window, 7,101,947 alerts on both sides, and they do not count this exposure either (Fink lists no night
2026-04-04). So the exposure is missing from the data behind Fink's statistics and Data Transfer, not lost in our
transfer; only the database behind Fink's API has it. No plotted alert comes from that exposure.

The |S/N| rule favours extreme residuals: 17 of the 30 chosen alerts are from the first five UTC days of the window,
10 from 2026-02-25 alone.

# Gaia DR3 counterparts, false-match rates and the label check (2026-11-01)

Items 1-3 of Prof. Ivezić's list for the first paper, done for Gaia DR3. Inputs are the two per-object tables of Arnor
run `notebooks-paper-20261015-v2` (3,487,175 DIA objects of cohort `accepted_five_window_20260225_20260714`), copied
read-only and checked by SHA-256 (Arnor = Mac = cloud). Outputs are in the project folder `fink-paper/`; this note
records how they were made and what they show.

## Runs

| Run | Code | What |
| --- | --- | --- |
| `gaia-dr3-irsa-20261101-v1` | `scripts/crossmatch_gaia_irsa.py` @ `281ffee` | All Gaia DR3 sources within 5″ of every object, IRSA TAP upload, 175 chunks of 20,000 |
| `gaia-dr3-irsa-shift60-20261101-v1` | same | 10% hash sample (`sha256("20261101:<id>") < 0.1`, 348,482 objects) moved 60″ north |
| `gaia-dr3-irsa-spotcheck-20261101-v1` | ad hoc, in the folder | 60 objects re-queried one by one with plain cone searches |
| `gaia-counterparts-20261101-v1` | `scripts/analyse_gaia_counterparts.py` @ `7261267` | Counterparts within 0.5″, false-match rates, composition, label check, figures |
| `gaia-counterparts-r1arcsec-20261101-v1` | same | Sensitivity run at 1.0″ |
| `tns-label-check-20261101-v1` | `scripts/check_tns_labels.py` @ `118db5a` | TNS supernova labels against Fink's public API |
| `fink-statistics-check-20261101-v1` | `scripts/compare_fink_statistics.py` @ `9031452` | Delivered rows per UTC date against Fink's nightly statistics |

The crossmatch goes through IRSA because the CDS services (xMatch, VizieR, SIMBAD) refuse connections from the cloud
environment and the ESA Gaia archive and NOIRLab Data Lab TAP rejected large uploads. The IRSA upload join needs the
`WHERE CONTAINS(...)=1` form; truncated (OVERFLOW) results are rejected, chunks are checksummed and resumable.

## Method

- Gaia positions are propagated from J2016.0 to J2026.34 (middle of the window) with their proper motions; sources
  without a proper motion stay put. Median separation of the nearest source drops from 0.085″ to 0.042″.
- Counterpart: nearest Gaia source within r\* = 0.5″. Categories: Gaia star (5/6-parameter solution, RUWE < 1.4,
  parallax/error > 5 or proper-motion significance > 5), QSO-like (DSC combmod quasar probability ≥ 0.5), galaxy-like
  (DSC galaxy probability ≥ 0.5), unclassified, none.
- Chance coincidences: the same query at positions moved 60″ north. With observed shares s and shifted-position
  shares q, the corrected shares are s_c − s_none·q_c/q_none and s_none/q_none (independent chance sources).
  Label-defined strata take q from all shifted positions in the same field, weighted by where the objects are.
  95% intervals from Dirichlet draws (Jeffreys prior).
- Radius: the chance-corrected share of objects with a real counterpart is 54.8% at 0.1″, 71.1% at 0.3″, 72.2% at
  0.5″ and peaks at 0.6″. At 0.5″ the shifted positions hit a Gaia source 0.51% of the time (bulge 1.35%), so 0.20%
  of matches are expected to be chance ones (bulge 0.10%). The 1″ run changes every headline by 0.2 points or less (Gaia star 56.5% → 56.3%).
- Around the real objects Gaia sources are depleted out to at least 4″ compared with the shifted positions
  (`fig_gaia_radial_density`), so the shifted positions overestimate chance matches; the corrections are conservative.

## Checks

- Every returned source lies within the 5″ query radius; both runs used the same object table (`236dbe09…`).
- 60 of 60 objects re-queried with plain cone searches give the same Gaia sources (70 in total) as the upload run.
- A simulated sky with known true counterparts and Poisson backgrounds is recovered within 3 points
  (`tests/test_analyse_gaia_counterparts.py`).
- No object's averaged RA wrapped across 0°/360°: no object lies within 1″ of RA = 360°·k/n_sources (1 ≤ k < n) in the
  declination range of the objects near RA 0, and the count of such near-coincidences elsewhere scales with the
  tolerance as chance does.
- The two Arnor tables disagree on the programme group of 24,663 objects (0.7%, all with alerts in more than one
  group); the template table's documented rule is used. CATS classes agree for every object.

## Results

- 72.2% of DIA objects have a real Gaia counterpart within 0.5″. Corrected composition: Gaia star 56.5%, QSO-like
  1.1%, galaxy-like 0.13%, unclassified 14.5%, none 27.8%. Gaia star / none by field: Low-dust WFD 45.9 / 37.6,
  bulge 77.8 / 7.0, plane 64.7 / 17.8, DDF 28.1 / 63.6, M49 15.3 / 79.5. Gaia's limit (G ≈ 21) means no counterpart
  is expected at template i > 20.5 whatever the object is.
- At |b| ≥ 30° and template i < 20 (259,708 objects), 83.6% are Gaia stars and 5.0% QSO-like. Stripe 82 variables at
  g < 20.5 were about two thirds quasars (Sesar et al. 2007); the alert selection (5σ difference-image detections)
  and our incomplete QSO identification both differ, so this needs Legacy/WISE before it is compared in the paper.
- Label check: 57.7% [57.6, 57.8] of the 977,424 objects Fink calls SN-like are Gaia stars (bulge 80.3%, plane
  70.8%, WFD 47.4%, DDF 37.9%, M49 25.6%), 89-97% at template i < 19 in each of the four main fields. No RUWE cut: 58.7%; 1″: 57.6%.
- The star rule passes 0.3% [0.2, 0.4] of SIMBAD QSO/AGN and 2.8% of SIMBAD galaxies, and 91.6% of VSX variables.
- Fink's Gaia names (2,271,622 objects) are our nearest source within 0.5″ in 99.57% of cases and always within 2.2″;
  Fink's parallaxes equal Gaia DR3's. But 260,165 objects (10.3% of those with a counterpart; bulge 17%) have no
  Fink Gaia name.
- 80,857 objects share their 0.5″ counterpart with at least one other DIA object.
- TNS supernova labels: 167 of 231 are confirmed by Fink's API. 60 of the 64 unconfirmed sit in groups of 2-6
  same-type objects up to 11.4′ apart; 25 of them are Gaia stars and 7 QSO-like, while no confirmed one is either.
  K1/K2 use these labels as truth and should be redone with the confirmed 167.
- Fink's nightly statistics agree with our delivery exactly: 7,101,947 alerts over the window on both sides; nightly
  differences are only alerts Fink files under a later night. The 4 April exposure that Fink's API serves is absent
  from both, so it is missing on Fink's side.

## Open

- Whether the 64 unconfirmed TNS labels come from the delivered packets or from the notebook (read-only Arnor query).
- Legacy Surveys DR10 morphology and WISE colours: the Data Lab query manager answers small inline-VALUES joins
  anonymously, but closed the connection for 1,000-object batches; needs a different batching or a Data Lab account.
- COSMOS2020 / Chandra COSMOS and Euclid Q1 through IRSA (same upload script with another table).

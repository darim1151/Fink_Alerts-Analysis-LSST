# What the five-window Fink dataset can support — recommended analyses

Cohort `accepted_five_window_20260225_20260714` (7,101,947 alerts; 6,311,364 DIA, 790,583 SSO; 3,487,175 DIA objects).
All numbers below were measured on 2026-10-08 directly from the raw Parquet, read-only.

## 1. What is actually in the data

| Group | Fields | Coverage (DIA sources unless stated) | Usable for |
|---|---|---|---|
| Photometry | `psfFlux` (difference), `scienceFlux`, `templateFlux` + errors, `band`, `snr` | 100%; bands i 50%, z 26%, r 16%, g 7%, u 0.4%, y 0.03% | colours (template), variability amplitude, detection S/N |
| Quality | `reliability` (real/bogus) | 100%; median 0.67, 10th pct 0.53 | quality cuts, robustness tests |
| Survey context | `target_name` (lowdust, bulgy, dusty_plane, DDFs COSMOS / EDFS-a,b / ELAIS-S1, M49 field), `observation_reason` (pairs_iz/ri/gr, template_blob_*, alert_*, ddf_*) | 100%; 24 and 25 values | per-field populations, explains i/z dominance |
| Fink classifier | CATS broad class + score; SuperNNova SN-vs-others score | 100% | class subsamples, score thresholds |
| Fink classifier, not usable | `earlySNIa_score` (all −1, not running), ELEPHANT hostless (629 sources), `spicy_class` (empty) | — | nothing yet |
| Light-curve features | 26 SNAD features per band (`lc_features`), computed on **difference flux** over the alert's previous-detection history (`prvDiaSources`), which reaches beyond our delivery | per band present | variability statistics (std, skew, Stetson K, chi2, trends) |
| Crossmatch | SIMBAD otype 12.6%; Gaia DR3 name 69% (plx 55%, VarFlag); Legacy DR8 zphot + pstar 26%; VSX 1.4%; TNS 0.4%; Mangrove host 0.4%; GCVS, 3HSP, 4LAC < 0.03% | objects: Gaia 2.27M, Legacy 454k, VSX 35k, Mangrove 11.7k, TNS 2.5k (176 spectroscopically typed) | external truth labels, distances, morphology |
| Depth per object | detections in cohort: median 1, 90% ≤ 3; 104k objects ≥ 5, 8.4k ≥ 20; 161k with ≥ 30 d baseline | — | limits light-curve science |

Among the 8,293 objects in the current colour panels: 5,552 have Legacy `pstar` (952 > 0.5, 4,600 < 0.5),
631 have Gaia parallax S/N > 5, 532 have a VSX type, 25 a TNS type.

## 2. Recommended next analyses, in priority order

1. **Star/galaxy split from Legacy DR8 `pstar`.** This is the closest honest substitute for the missing PSF − CModel
   panel. Draw the three panels with `pstar ≥ 0.5` and `< 0.5` as two contour sets (the red/blue control idea), and add a
   `pstar` vs `r_tmpl` panel in place of the top-left one. First result already visible: most plotted objects (4,600 of
   5,552 with `pstar`) are extended, so the colour panels are dominated by transients on galaxies.
2. **Classifier validation tables.** Confusion matrices of the Fink CATS class (latest snapshot) against external
   labels: VSX/GCVS variable types (35k objects), SIMBAD QSO/AGN (9.3k), Gaia `VarFlag`, and TNS spectroscopic types
   (176 objects; small). Report precision and recall per class with bootstrap intervals, and as a function of number of
   detections and of CATS score. This answers "how far can we trust the labels we split by".
3. **Label evolution.** 929,783 DIA objects change CATS class between snapshots. A transition matrix (class at detection
   n vs n+1), class purity vs detection count, and score vs detection count show when a label becomes stable. This
   uses the per-alert snapshots exactly as the contract intends.
4. **Sesar-style variability diagrams.** Per object and band: rms and skewness of `psfFlux / templateFlux`
   (fractional variability relative to the static flux), or the `lc_features` standard deviation, skew and Stetson K.
   Plot amplitude vs `r_tmpl`, and the u−g vs g−r panel coloured by g-band skewness (Sesar et al. 2007, Fig. 10).
   Separates RR Lyrae, eclipsing binaries, QSOs and flaring M dwarfs.
5. **Absolute-magnitude diagrams.**
   (a) Variable stars: `M_r,tmpl` from Gaia parallax (631 plotted objects with S/N > 5) vs `(g−i)_tmpl`, an HR diagram
   of the variable stars that raise alerts.
   (b) Extragalactic transients: peak observed difference magnitude with Legacy `zphot` (454k objects) or Mangrove
   luminosity distance (11.7k), giving M vs z per CATS class, compared with TNS types.
6. **Survey-strategy and field table.** Alerts, objects, bands, reliability and class mix per `target_name` and per
   `observation_reason`, plus a HEALPix sky map. The data come from distinct programmes (Galactic bulge and plane
   fields vs low-dust extragalactic fields and DDFs); population plots should be split by field first.
7. **Quality and robustness.** Reliability and S/N distributions by field and band, reliability vs `r_tmpl` (bright-star
   artifacts), and the colour panels re-made under stricter reliability cuts to show the science plots are stable.
8. **SSO population (kept separate).** Ecliptic-latitude distribution (checks `is_sso`), band and magnitude
   distributions, alert rate per night. No colours or phase curves: there is no SSO identity in Light Static.

Supporting tables worth producing alongside: crossmatch coverage per field; class × field counts; label-stability
summary; per-class detection-count distribution.

## 3. What the data cannot support

PSF − CModel morphology from Rubin itself; complete light curves or forced photometry (Light Static carries current
detections only, so SN light-curve fitting and peak magnitudes are lower limits); SSO colours, orbits or identities;
early-SN Ia and hostless scores (not populated); Rubin completeness or rates (zero-delivery days are not zero alerts).

## 4. Cautions that apply to every item

SIMBAD, Gaia and Legacy matches are positional crossmatches by Fink with no separation delivered (only Mangrove gives an
angular distance), so chance coincidences in crowded bulge and plane fields are possible. Broker labels are snapshots that evolve. Photometric
calibration and extinction are not yet validated or corrected.

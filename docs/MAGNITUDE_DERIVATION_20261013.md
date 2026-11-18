# How our multi-band magnitudes are derived

Answer to the mentor's question (2026-10-13): Rubin observes in one band at a time, so where do our multi-band
magnitudes and colours come from?

## What one alert contains

Each Rubin alert is one DIA source: one detection, in one band, at one time. Rubin's alert schema (versions v10.0,
v11.0 and v11.1, the versions present in our deliveries) defines three fluxes for it, all in nJy and all in the
alert's own band:

| Field | Rubin definition (alert schema) |
|---|---|
| `psfFlux` | point-source flux on the difference image (visit minus template) |
| `scienceFlux` | forced point-source photometry on the visit image, centred at the DiaSource position |
| `templateFlux` | forced point-source photometry on the template image, centred at the DiaObject position |

The template is a coadd of earlier images in that band. In Rubin Data Preview 1, templates were built per band from
the best-seeing third of the visits (at least 12), combined with an inverse-variance weighted mean. The alerts do not
say which visits built the operational 2026 templates, or when templates were rebuilt.

## How we build a magnitude per band

1. Group alerts by DIA object (`diaObjectId`).
2. For each band the object alerted in, take the median `templateFlux` and median `templateFluxErr` over those alerts.
3. Keep the band if that flux is positive and the median S/N is ≥ 5.
4. Convert to an AB magnitude: `m = 31.4 − 2.5 log10(flux / nJy)`.

A colour such as g−r is the difference of two such template magnitudes.

## What this means

- **Colours compare static templates, not simultaneous measurements.** For a constant source this is fine. For a
  variable, each band's template averages over whichever visits built it, so the colour carries extra scatter of order
  the variability amplitude.
- **A band exists only if the object alerted in it.** An object needs a g, r and i detection to appear in the g, r, i
  panels. That is why very red objects reach a brighter limit in r (see the g−i > 2 note).
- **Timing in practice.** Of the 8,196 objects in the g, r, i panels, 3,495 (43%) alerted in g, r and i within a single
  night. The median time between their first g, r and i alerts is 2.1 days, and 90% are within 31 days.
- **Template flux is not perfectly constant.** For these objects, the template flux in one band varies across alerts by
  a median of 13% (g), 8.5% (r) and 12% (i), measured as (max − min)/median. That is far above the quoted errors, so
  templates or object positions change during the survey. The median per band limits the effect, but this is an open
  calibration question.
- **Not coadd galaxy photometry.** `templateFlux` is a point-source fit at the object's position. For a galaxy it is
  the flux under the PSF there, not a total or CModel magnitude.

## What a richer packet would add

The full Rubin alert also carries the DiaObject record, which has per-band weighted mean forced fluxes on the visit
images (`<band>_scienceFluxMean`), plus forced photometry history. Our Fink Light Static deliveries carry neither, so
template fluxes are the only multi-band static photometry we have.

## Correction

Earlier notes said `templateFlux` is measured "at the DIA source position". The alert schema says "centred at the
DiaObject position". The wording is corrected; no number changes.

Sources: `lsst/alert_packet` schemas `lsst.v10_0`, `lsst.v11_0`, `lsst.v11_1` (`diaSource.avsc`); Rubin DP1
documentation, "Template coadd". Measurements: `/astro/store/shire/FINK/outputs/magnitude-derivation-check-20261013-v2/facts.json`.

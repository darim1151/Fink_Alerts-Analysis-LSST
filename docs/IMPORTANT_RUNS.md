# Important Runs

This file preserves lightweight provenance for the current Data Transfer milestone. Raw and processed artifacts remain local-only and ignored.

## Real Smoke Delivery

- Topic: `ftransfer_lsst_2026-06-24_657339`
- Survey: `lsst`
- UTC window: `2026-02-25` to `2026-02-26`
- Scope: `tag_filtered_smoke_delivery`
- Filter: `in_tns`
- Content: `Light static packet`
- Raw rows: `8731`
- Raw files: `20`
- Raw delivery path: `data/raw/data_transfer/smoke_delivery/ftransfer_lsst_2026-06-24_657339/`

## Checkpoint 6 Successful Smoke Ingestion

- Run id: `20260627T210030Z`
- Validation status: `passed_with_warnings`
- Completeness status: blocked as expected because the smoke delivery is tag-filtered and not all-alert/full-night.

Processed table shapes:

- `alerts`: `8731 x 42`
- `objects`: `81 x 15`
- `classifications`: `8731 x 25`
- `forced_photometry`: `8731 x 15`
- `lightcurve_features`: `40191 x 38`
- `nightly_summary`: `1 x 17`

Nested fields preserved/sanitized:

- `lc_features`
- `clf`
- `xm`
- `pred`
- `misc`

## Checkpoint 7 Generalized Pipeline Smoke Run

- Run id: `20260627T211648Z`
- Raw rows: `8731`
- Validation status: `passed_with_warnings`
- Completeness status: blocked as expected.

## Test Status

- Latest known full-suite status before cleanup: `84 passed`

## Policy

- Raw files under `data/raw/` are intentionally local and ignored.
- Processed tables under `data/processed/` are intentionally local and ignored.
- Generated run reports under `outputs/` are intentionally local and ignored except lightweight maintenance summaries.
- This smoke delivery proves Data Transfer access and ingestion, not full-night all-alert completeness.

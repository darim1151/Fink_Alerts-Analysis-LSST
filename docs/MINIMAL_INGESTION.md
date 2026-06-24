# Minimal Public REST Ingestion

## Purpose

Checkpoint 1 tests whether public/no-login Fink LSST REST endpoints can support tiny real sample lookup and preliminary local normalization. It is not a full-night extraction, not a population-level science dataset, and not evidence of alert-stream completeness.

## What This Checkpoint Does

- Discovers real `diaObjectId` and `diaSourceId` values from tiny public responses.
- Uses a real `diaObjectId` to retrieve tiny samples from:
  - `/api/v1/objects`
  - `/api/v1/sources`
  - `/api/v1/fp`
- Saves raw responses before normalization.
- Normalizes available responses into preliminary internal tables.
- Writes Parquet tables and CSV previews.
- Runs structured validation checks.

## ID Discovery

The script is:

```bash
python scripts/discover_real_fink_ids.py
```

It first scans existing fixtures. If no real IDs are found, it performs a bounded `/api/v1/tags` lookup using `sample_tag` and `max_sample_rows` from `configs/local_smoke_test.yaml`.

Conesearch is skipped unless `sample_conesearch_ra` and `sample_conesearch_dec` are explicitly configured. The default config intentionally leaves those unset.

Outputs:

- `outputs/id_discovery/id_candidates.json`
- `outputs/id_discovery/id_discovery_manifest.json`
- `data/fixtures/samples/fink_lsst_tags_id_lookup_sample.json` if the tiny tag lookup succeeds

## Sample Retrieval

The script is:

```bash
python scripts/fetch_minimal_fink_samples.py
```

It uses a real discovered `diaObjectId` and narrow column lists to retrieve tiny samples. Raw successful responses are saved to:

- `data/raw/minimal_samples/objects_sample_raw.json`
- `data/raw/minimal_samples/sources_sample_raw.json`
- `data/raw/minimal_samples/fp_sample_raw.json`

Provenance is saved to:

- `data/raw/minimal_samples/sample_retrieval_manifest.json`
- `outputs/minimal_ingestion/sample_endpoint_attempts.json`

## Normalized Tables

The script is:

```bash
python scripts/build_minimal_internal_tables.py
```

It creates:

- `data/processed/minimal_samples/sources.parquet`
- `data/processed/minimal_samples/objects.parquet`
- `data/processed/minimal_samples/forced_photometry.parquet`
- `data/processed/minimal_samples/statistics.parquet`
- `data/processed/minimal_samples/tags.parquet`

CSV previews are saved under:

- `outputs/minimal_ingestion/tables_preview/`

Normalization preserves original Fink field names and adds internal columns where fields can be identified, such as `internal_object_id`, `internal_source_id`, `ra`, `dec`, `time_mjd`, `band`, `flux`, `flux_err`, `class_label`, and `tag`.

## Validation

The script is:

```bash
python scripts/validate_minimal_samples.py
```

Outputs:

- `outputs/minimal_ingestion/validation_report.json`
- `outputs/minimal_ingestion/validation_summary.md`

Checks include non-empty tables, required columns, ID presence, duplicate keys, coordinate ranges, MJD sanity, flux/magnitude sanity, and normalization provenance columns.

## What Remains Unresolved

- Whether public REST can support complete full-night extraction.
- Whether a public endpoint can safely enumerate all alerts for a night without large downloads.
- Whether Data Transfer will eventually be required for full-scale historical work.

Checkpoint 2 added the repeatable small-window test in `scripts/run_bounded_public_rest_extraction.py`. It shows bounded tag/date-window extraction and capped known-ID detail retrieval are feasible, while complete full-night all-alert extraction remains unresolved.

Checkpoint 3 adds `scripts/run_full_night_feasibility.py`, which is the first phase aimed directly at the full-night question. It uses UTC-only date windows beginning no earlier than `2026-02-25` and treats any row-cap hit as a completeness blocker.

## Difference From ANTARES

This checkpoint is not locus ingestion, sky tiling, or nightly backfill. It proves a Fink-native minimal path: discover real IDs, fetch tiny object/source/forced-photometry responses, normalize them, and validate the result while preserving the broker's own alert/object vocabulary.

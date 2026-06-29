# Data Model

This is a preliminary internal model for Fink LSST/Rubin analysis. It is intentionally broker-aware but not overfit before schema/tag fixtures are captured.

After running `python scripts/discover_fink_lsst_contracts.py`, the schema notebook reads:

- endpoint-specific schema fixtures under `data/fixtures/schema/`
- `data/fixtures/fink_lsst_tags.json`
- `data/fixtures/fink_lsst_swagger.json`

The notebook then generates `outputs/schema_exploration/internal_data_model_proposal.json` from actual fixture fields.

Current Checkpoint 0.6 fixture status:

- `data/fixtures/fink_lsst_tags.json` exists and includes LSST-oriented public tags such as `extragalactic_new_candidate`, `hostless_candidate`, `in_tns`, and `most_likely_sn`.
- `data/fixtures/fink_lsst_statistics_sample.json` exists and includes nightly fields such as `f:night`, `f:alerts`, `f:objects`, band alert counts, Fink version fields, and `f:lsst_schema_version`.
- Endpoint-specific schema fixtures exist under `data/fixtures/schema/` for `sources`, `objects`, `fp`, `conesearch`, `tags`, and `statistics`.
- `data/fixtures/fink_lsst_swagger.json` and `data/fixtures/fink_lsst_api_contract_summary.json` exist and describe the public REST contract.

## Alert Or Source-Level Table

One row per alert/source measurement.

Likely fields:

- source identifier, such as `diaSourceId` or broker-specific equivalent
- object identifier, such as `diaObjectId` or broker-specific equivalent
- RA/Dec
- time, MJD, JD, or survey-specific timestamp
- band/filter
- magnitude, flux, uncertainty, signal-to-noise
- quality flags and tags
- alert packet metadata

## Object-Level Table

One row per astronomical object or broker-aggregated object.

Likely fields:

- object identifier
- current/best coordinates
- first and latest detection time
- number of detections
- available bands
- current classification summary
- crossmatch/context summary

## Forced-Photometry Or Lightcurve Table

One row per forced-photometry measurement or lightcurve point when available.

Likely fields:

- object identifier
- forced-source identifier if present
- time
- band/filter
- flux and flux uncertainty
- quality flags
- non-detection or upper-limit information

## Classification And Context Summary

One row per object/classification snapshot or one row per source/classification output, depending on schema availability.

Likely fields:

- object/source identifier
- Fink class label
- model-specific scores
- tag fields
- SIMBAD/TNS/Gaia/MPC/host context
- classification timestamp or alert time

## Nightly Summary

One row per observing night or broker processing date.

Observed fields in the current statistics fixture include:

- `f:night`
- `f:alerts`
- `f:objects`
- `f:alerts_u`, `f:alerts_g`, `f:alerts_r`, `f:alerts_i`, `f:alerts_z`, `f:alerts_y`
- `f:visits`
- `f:lsst_schema_version`
- `f:fink_broker_version`
- `f:fink_science_version`
- tag/filter counts such as `f:in_tns`, `f:is_sso`, `f:is_first`, and quality/pixel flags

## Validation Report

One row per validation check or a JSON list per artifact.

Likely fields:

- artifact path
- check name
- passed flag
- severity
- message
- details
- creation time

## Concept Mapping Strategy

The `src/fink_lsst/schema.py` helpers map raw schema fields into broad concepts:

- object IDs
- source IDs
- coordinates
- time
- filter/band
- magnitude/flux
- classification
- tags
- crossmatch/context
- forced photometry
- cutouts

This mapping should be revised after each schema snapshot rather than treated as permanent.

## Fixture-Aware Table

`notebooks/01_schema_exploration.ipynb` produces a table with:

- internal concept
- description
- candidate field count
- candidate fields

This keeps the data model anchored to real fixture fields while avoiding premature science claims.

The broad internal concept map should now be derived from endpoint-specific schema fixtures first, then checked against tiny sample rows only when a safe public example identifier exists.

## Checkpoint 1 Minimal Tables

Tiny real-ID sample retrieval created preliminary normalized tables:

- `data/processed/minimal_samples/sources.parquet`
- `data/processed/minimal_samples/objects.parquet`
- `data/processed/minimal_samples/forced_photometry.parquet`
- `data/processed/minimal_samples/statistics.parquet`
- `data/processed/minimal_samples/tags.parquet`

These tables preserve original Fink/Rubin columns and add internal convenience columns where fields are identifiable. They are suitable for testing normalization and validation code only; they are not population-level science products.

## Checkpoint 2 Bounded Extraction Tables

Bounded public REST extraction writes timestamped processed tables under:

- `data/processed/bounded_extraction/<run_id>/tag_rows.parquet`
- `data/processed/bounded_extraction/<run_id>/object_summary.parquet`
- `data/processed/bounded_extraction/<run_id>/objects.parquet`
- `data/processed/bounded_extraction/<run_id>/sources.parquet`
- `data/processed/bounded_extraction/<run_id>/forced_photometry.parquet`
- `data/processed/bounded_extraction/<run_id>/statistics.parquet`

The stable pointer is:

- `outputs/bounded_extraction/latest_run.json`

`tag_rows` is the capped tag/date-window sample. `object_summary` is derived from those tag rows and keeps one row per sampled object with row counts, source counts, first/last MJD, bands, and representative coordinates. `objects`, `sources`, and `forced_photometry` are known-ID detail tables for the capped object list. `statistics` is context only and should not be treated as a direct denominator unless matching filters are available.

The latest local run produced 50 tag rows, 15 unique objects in the tag sample, and detail retrieval for 10 capped objects. Because the tag response hit `n=50`, these tables are bounded feasibility artifacts, not complete nightly catalogs.

## Checkpoint 3 Full-Night Feasibility Tables

Partitioned full-night feasibility writes timestamped artifacts under:

- `data/raw/full_night_feasibility/<run_id>/partitions/`
- `data/processed/full_night_feasibility/<run_id>/deduplicated_rows.parquet`
- `data/processed/full_night_feasibility/<run_id>/deduplicated_rows.csv`
- `data/processed/full_night_feasibility/<run_id>/object_summary.parquet`
- `data/processed/full_night_feasibility/<run_id>/source_summary.parquet`
- `data/processed/full_night_feasibility/<run_id>/completeness_accounting.json`

These products are written only when bounded partitions return rows. A partition result status of `possibly_truncated`, `failed`, `unsupported`, `unsafe`, or `skipped` prevents a full-night completeness claim.

## Checkpoint 4-6 Data Transfer Tables

Data Transfer deliveries are expected under:

- `data/raw/data_transfer/`

The first real smoke delivery is stored under:

- `data/raw/data_transfer/smoke_delivery/ftransfer_lsst_2026-06-24_657339/`

After smoke ingestion, processed outputs are written under:

- `data/processed/data_transfer/smoke_delivery/<run_id>/alerts.parquet`
- `data/processed/data_transfer/smoke_delivery/<run_id>/objects.parquet`
- `data/processed/data_transfer/smoke_delivery/<run_id>/forced_photometry.parquet` when available
- `data/processed/data_transfer/smoke_delivery/<run_id>/classifications.parquet` when available
- `data/processed/data_transfer/smoke_delivery/<run_id>/lightcurve_features.parquet` when `lc_features` can be expanded
- `data/processed/data_transfer/smoke_delivery/<run_id>/nightly_summary.parquet`
- `data/processed/data_transfer/smoke_delivery/<run_id>/manifest.json`
- `data/processed/data_transfer/smoke_delivery/<run_id>/nested_conversion_report.json`

These tables reuse the same internal convenience columns as REST-derived products where possible, including object/source IDs, coordinates, time MJD, band, flux, classification, and provenance fields.

Real Fink LSST Data Transfer payloads can include nested Arrow fields. The current smoke schema includes nested `lc_features`, `clf`, `xm`, `pred`, and `misc` fields. Processed Parquet tables preserve those values as stable JSON strings rather than dropping them. `alerts.parquet` includes `lc_features_json`, and `lightcurve_features.parquet` expands feature maps into linked object/source/band rows when possible.

The current smoke products are tagged with:

- `scope = tag_filtered_smoke_delivery`
- `full_night_complete = false`
- reason: `in_tns filter and light static packet; not all-alert full-night production`

## Checkpoint 7 Full-Night Data Transfer Tables

Full-night all-alert deliveries use the same processed table names as smoke deliveries, but are written under:

- `data/processed/data_transfer/full_night/<run_id>/alerts.parquet`
- `data/processed/data_transfer/full_night/<run_id>/objects.parquet`
- `data/processed/data_transfer/full_night/<run_id>/forced_photometry.parquet` when available
- `data/processed/data_transfer/full_night/<run_id>/classifications.parquet` when available
- `data/processed/data_transfer/full_night/<run_id>/lightcurve_features.parquet` when available
- `data/processed/data_transfer/full_night/<run_id>/nightly_summary.parquet`
- `data/processed/data_transfer/full_night/<run_id>/manifest.json`

Full-night metadata is sourced from `configs/data_transfer_topics.yaml`. A manifest may record `full_night_complete = true` only for `scope = full_night_all_alerts`; validation still gates any scientific completeness claim through `completeness_claim_allowed`.

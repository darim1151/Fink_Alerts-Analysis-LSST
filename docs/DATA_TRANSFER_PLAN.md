# Data Transfer Plan

## Why This Exists

Checkpoint 3 showed that public REST is useful but did not prove complete full-night all-alert extraction. The REST run fell back to tag-specific partitioning, had no exposed pagination, and still hit row caps. Therefore Data Transfer is now the planned route for complete nightly alert census and historical bulk extraction.

This does not replace REST. REST remains useful for bounded candidate work, known-ID enrichment, diagnostics, dashboard drilldowns, and comparison against bulk outputs.

## Hybrid Architecture

REST mode:

- bounded/candidate extraction
- known-ID object/source/forced-photometry enrichment
- diagnostics
- dashboards

Data Transfer mode:

- complete nightly alert census
- historical bulk extraction
- population-level science after validation

Both paths map into the same local internal table model where possible.

## Default Window

The dry-run request uses UTC:

- start: `2026-02-25`
- stop: `2026-02-26`
- timezone: `UTC`
- minimum alert date: `2026-02-25`

Local timezone must not affect Data Transfer or REST date windows. Raw MJD/TAI fields are preserved.

## Safety Rules

The repository does not submit Data Transfer jobs in this checkpoint.

- `dry_run: true`
- `allow_submit: false`
- user confirmation is required before any future submission
- credentials are never requested or stored
- tokens, passwords, API keys, and `.env` files must not be committed
- cutouts, FITS, and image payloads are not requested

## Registration And Setup

Run:

```bash
python scripts/install_fink_client_helper.py
python scripts/check_fink_data_transfer_setup.py
python scripts/probe_fink_client_capabilities.py
python scripts/write_registration_checklist.py
```

These scripts detect local readiness and safe client availability. They do not authenticate, submit jobs, or record secret values.

## Dry-Run Request Workflow

Run:

```bash
python scripts/prepare_data_transfer_request.py
python scripts/prepare_data_transfer_smoke_request.py
```

Outputs:

- `outputs/data_transfer/request_drafts/data_transfer_request.json`
- `outputs/data_transfer/request_drafts/DATA_TRANSFER_REQUEST.md`
- `outputs/data_transfer/request_drafts/MANUAL_PORTAL_CHECKLIST.md`

The checklist is meant for manual review before any portal or client-based request is attempted.

For the first operational test, use the smoke request artifacts:

- `outputs/data_transfer/request_drafts/SMOKE_DATA_TRANSFER_REQUEST.json`
- `outputs/data_transfer/request_drafts/SMOKE_DATA_TRANSFER_REQUEST.md`
- `outputs/data_transfer/request_drafts/SMOKE_MANUAL_PORTAL_CHECKLIST.md`

If the portal only supports whole-night delivery, stop and ask before submitting.

## Real Smoke Delivery Status

Fink Data Transfer access is now working for a bounded smoke delivery:

- topic: `ftransfer_lsst_2026-06-24_657339`
- survey: `lsst`
- UTC window: `2026-02-25` to `2026-02-26`
- filter: `in_tns`
- content: `Light static packet`
- raw files: `20` Parquet files
- raw rows: `8731`

This is a tag-filtered smoke delivery. It is useful for validating the transfer, ingestion, schema inventory, nested-field handling, and first diagnostics. It is not a complete full-night all-alert product.

## Delivery Storage

When real files are delivered manually, place them under:

- `data/raw/data_transfer/`
- `data/raw/data_transfer/smoke_delivery/` for tiny smoke deliveries
- `data/raw/data_transfer/full_night/` for later full-night deliveries

Then inspect:

```bash
python scripts/inspect_data_transfer_delivery.py
```

The inspector supports empty directories and tiny local fixtures. It classifies Parquet, Avro, JSON/JSONL, CSV, and unknown files.

## Ingestion

After delivery files exist:

```bash
python scripts/inspect_data_transfer_delivery.py
python scripts/run_data_transfer_smoke_ingestion.py
python scripts/validate_data_transfer_delivery.py
```

The generalized Checkpoint 7 pipeline can also consume any topic recorded in `configs/data_transfer_topics.yaml`:

```bash
python scripts/run_data_transfer_pipeline.py --scope tag_filtered_smoke_delivery
python scripts/run_data_transfer_pipeline.py --scope full_night_all_alerts --topic TOPIC
```

Processed outputs are written under:

- `data/processed/data_transfer/smoke_delivery/<run_id>/alerts.parquet`
- `data/processed/data_transfer/smoke_delivery/<run_id>/objects.parquet`
- `data/processed/data_transfer/smoke_delivery/<run_id>/forced_photometry.parquet` when available
- `data/processed/data_transfer/smoke_delivery/<run_id>/classifications.parquet` when available
- `data/processed/data_transfer/smoke_delivery/<run_id>/lightcurve_features.parquet` when `lc_features` can be expanded
- `data/processed/data_transfer/smoke_delivery/<run_id>/nightly_summary.parquet`
- `data/processed/data_transfer/smoke_delivery/<run_id>/manifest.json`
- `data/processed/data_transfer/smoke_delivery/<run_id>/nested_conversion_report.json`

Raw reports and diagnostics are written under:

- `outputs/data_transfer/smoke_delivery/<run_id>/RAW_FIELD_INVENTORY.md`
- `outputs/data_transfer/smoke_delivery/<run_id>/VALIDATION_SUMMARY.md`
- `outputs/data_transfer/smoke_delivery/<run_id>/SMOKE_DIAGNOSTICS.md`
- `outputs/data_transfer/smoke_delivery/<run_id>/figures/`

The ingestion layer preserves raw files unchanged. Nested fields such as `lc_features`, `clf`, `xm`, `pred`, and `misc` are JSON-sanitized before processed Parquet writes. `lc_features` is also preserved as `lc_features_json` and expanded into `lightcurve_features.parquet` where possible.

## Validation Criteria

Run:

```bash
python scripts/validate_data_transfer_delivery.py
```

Validation refuses a complete-night claim if:

- delivery is absent
- date/window is ambiguous
- required fields are missing
- all-alert scope is not confirmed
- denominator comparison is unavailable or incompatible unless delivery metadata supplies complete counts
- duplicate/source integrity is bad

For the current smoke delivery, validation is expected to block the complete-night claim because the scope is `tag_filtered_smoke_delivery`.

For a recorded full-night delivery, validation allows a complete-night claim only when the registry and local evidence support it: all-alert/no-filter scope, nonzero raw rows, processed alerts, no hard validation failures, no recorded nonzero Kafka lag, no truncation warning, and acceptable or explicitly explained date-window behavior.

## First Full-Night Workflow

Prepare the request artifacts:

```bash
python scripts/prepare_full_night_transfer_request.py
```

After manual portal topic creation, record the topic in:

```text
configs/data_transfer_topics.yaml
```

Then print the download command and run the pipeline:

```bash
python scripts/print_data_transfer_download_command.py --topic TOPIC
python scripts/run_full_night_ingestion.py --topic TOPIC
```

See `docs/FULL_NIGHT_TRANSFER_WORKFLOW.md` for the Checkpoint 7 checklist.

## Full-Week Full-Packet Readiness

Checkpoint 8A adds pre-download controls for the manually submitted full-week full-packet topic `ftransfer_lsst_2026-06-27_38507`.

Use:

```bash
python scripts/register_data_transfer_topic.py --scope full_week_full_packet --survey lsst --topic ftransfer_lsst_2026-06-27_38507 --startdate 2026-02-25 --stopdate 2026-03-04 --content "Full packet" --all-alert
python scripts/preflight_full_packet_delivery.py --topic ftransfer_lsst_2026-06-27_38507
python scripts/print_data_transfer_download_command.py --topic ftransfer_lsst_2026-06-27_38507
python scripts/summarize_download_progress.py --raw-dir data/raw/data_transfer/full_week_full_packet/2026-02-25_to_2026-03-04/ftransfer_lsst_2026-06-27_38507 --topic ftransfer_lsst_2026-06-27_38507
python scripts/inspect_full_packet_delivery.py --topic ftransfer_lsst_2026-06-27_38507
```

Completeness remains unresolved until validation. Do not ingest heavy full-packet fields until raw schema inspection establishes a storage policy.

If the full-packet download is interrupted or uncertain, run `scripts/triage_full_packet_download.py` and `scripts/recommend_download_action.py` before ingestion. The guarded full-week pipeline refuses partial or active raw data unless `--allow-partial` is explicitly supplied.

## Difference From ANTARES And REST Chunking

ANTARES work was locus-centered and required search/tiling around broker-specific limits. Fink REST chunking remains useful for bounded workflows, but complete nightly population-level work should be built around Data Transfer once access is available and deliveries pass validation.

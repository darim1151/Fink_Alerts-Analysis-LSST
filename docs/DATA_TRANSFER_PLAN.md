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
python scripts/ingest_data_transfer_delivery.py
python scripts/run_data_transfer_smoke_ingestion.py
```

Processed outputs are written under:

- `data/processed/data_transfer/<run_id>/alerts.parquet`
- `data/processed/data_transfer/<run_id>/objects.parquet`
- `data/processed/data_transfer/<run_id>/forced_photometry.parquet` when available
- `data/processed/data_transfer/<run_id>/classification_context.parquet` when available
- `data/processed/data_transfer/<run_id>/nightly_summary.parquet`
- `data/processed/data_transfer/<run_id>/manifest.json`

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

## Difference From ANTARES And REST Chunking

ANTARES work was locus-centered and required search/tiling around broker-specific limits. Fink REST chunking remains useful for bounded workflows, but complete nightly population-level work should be built around Data Transfer once access is available and deliveries pass validation.

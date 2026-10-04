# fink_lsst_analysis

Local-first research code for exploring public Fink Broker access patterns for LSST/Rubin alert analysis.

This repository is intentionally small at this stage. It is an architecture, access reconnaissance, and smoke-testing foundation, not a production science pipeline. The code uses public/no-login Fink REST API access where possible and now includes a hardened local ingestion path for manually delivered Fink Data Transfer smoke files. It still avoids Livestream, Spark job execution, privileged secrets in-repo, paid services, cutouts/FITS/images, and unconfirmed large downloads.

## Scope

- Inspect public Fink REST API reachability and schemas.
- Save small schema and smoke-test artifacts under `data/` or `outputs/`.
- Build reusable utilities for configuration, local storage, schema summaries, validation, summaries, and lightweight plotting.
- Ingest real Data Transfer smoke deliveries locally while preserving raw files and sanitizing nested scientific fields.
- Keep room for later historical ingestion, classification analysis, science notebooks, and ANTARES comparison without copying ANTARES-specific architecture.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -e .
```

## Run Tests

```bash
python -m pytest
```

The tests do not require internet access.

## Common Workflow

```bash
cd "/Users/darim_shamsi/Documents/Fink Analysis"
source .venv/bin/activate
python -m pytest
python scripts/probe_fink_lsst_api.py
python scripts/discover_fink_lsst_contracts.py
python scripts/discover_real_fink_ids.py
python scripts/fetch_minimal_fink_samples.py
python scripts/build_minimal_internal_tables.py
python scripts/validate_minimal_samples.py
python scripts/run_bounded_public_rest_extraction.py
python scripts/run_full_night_feasibility.py
python scripts/check_fink_data_transfer_setup.py
python scripts/install_fink_client_helper.py
python scripts/prepare_data_transfer_request.py
python scripts/probe_fink_client_capabilities.py
python scripts/inspect_data_transfer_delivery.py
python scripts/run_data_transfer_smoke_ingestion.py
python scripts/validate_data_transfer_delivery.py
python scripts/write_registration_checklist.py
python scripts/prepare_data_transfer_smoke_request.py
python scripts/prepare_full_night_transfer_request.py
python scripts/report_data_transfer_next_action.py
python scripts/list_analysis_runs.py
python scripts/validate_run_manifest.py --all
jupyter lab
```

## Probe The Public LSST API

```bash
python scripts/probe_fink_lsst_api.py
python scripts/discover_fink_lsst_contracts.py
```

The probe and contract-discovery scripts try only tiny public/no-login endpoints on `https://api.lsst.fink-portal.org`:

- `GET /api/v1/schema`
- `GET /api/v1/tags`
- `POST /api/v1/statistics`
- `GET /swagger.json`
- schema metadata variants using `endpoint=/api/v1/<endpoint>`
- `GET /api/v1/blocks`

Successful responses are saved under `data/fixtures/`. Probe and contract reports are written under `outputs/smoke_test/` and `outputs/api_contracts/`.

## Use the Notebooks

```bash
jupyter lab
```

Open:

- `notebooks/00_fink_access_smoke_test.ipynb`
- `notebooks/01_schema_exploration.ipynb`
- `notebooks/02_minimal_public_rest_ingestion.ipynb`
- `notebooks/03_bounded_public_rest_extraction.ipynb`
- `notebooks/04_full_night_feasibility.ipynb`
- `notebooks/05_data_transfer_readiness.ipynb`
- `notebooks/06_data_transfer_smoke_ingestion.ipynb`

The first notebook performs the same tiny public API checks and saves fixtures. The second notebook works from saved fixtures and does not require live API access.
The third notebook summarizes minimal real-ID sample ingestion and validation; it is not a full-night science analysis.
The fourth notebook reads the latest bounded extraction artifacts and summarizes endpoint capabilities, normalized table shapes, validation warnings, and feasibility conclusions. It does not make live API calls.
The fifth notebook reads the latest full-night feasibility run and shows the UTC target window, capability diagnosis, partition statuses, truncation blocks, denominator accounting, validation, and final REST completeness decision.
The sixth notebook reads saved Data Transfer readiness artifacts and shows setup status, dry-run request draft, client probe, delivery inspection, and the hybrid REST/Data Transfer architecture.
The seventh notebook reads saved real smoke-ingestion outputs only: raw field inventory, processed shapes, nested conversion, validation, diagnostics, figures, and next-action interpretation.

## Inspect Fixtures

```bash
ls -lh data/fixtures
ls -lh data/fixtures/schema
ls -lh data/fixtures/samples
python -m json.tool outputs/smoke_test/manifest.json
python -m json.tool outputs/api_contracts/schema_probe_results.json
python -m json.tool outputs/id_discovery/id_candidates.json
python -m json.tool outputs/minimal_ingestion/validation_report.json
python -m json.tool outputs/bounded_extraction/latest_run.json
python -m json.tool outputs/full_night_feasibility/latest_run.json
python -m json.tool outputs/data_transfer/setup_status.json
python -m json.tool outputs/data_transfer/request_drafts/data_transfer_request.json
python -m json.tool outputs/data_transfer/smoke_delivery/latest_run.json
```

## Configuration

Default local settings live in `configs/local_smoke_test.yaml`.

Important defaults:

- Primary survey: `lsst`
- Primary Fink LSST REST API base URL: `https://api.lsst.fink-portal.org`
- Optional ZTF reference API base URL: `https://api.ztf.fink-portal.org`
- Small smoke-query row limit: `5`
- Data directory: `data`
- Output directory: `outputs`
- Request timeout: `60` seconds

Checkpoint 0.6 locked the schema request shape:

```text
GET /api/v1/schema?endpoint=/api/v1/sources&output-format=json
```

Bare `GET /api/v1/schema` returns documentation text, not JSON.

All paths are relative by default. Locally they resolve inside the repository. For production, set `FINK_LSST_DATA_ROOT` to an absolute directory outside the checkout and run-manifest data paths resolve under it instead; see `docs/ARNOR_PRODUCTION_FOUNDATION.md`.

## Notes

The current public Fink documentation has historically included many ZTF-era examples using field names such as `i:objectId`, `i:ra`, and `i:jd`. This repository keeps the primary target on the LSST-specific API and treats ZTF as optional reference material only.

See `docs/API_CONTRACTS.md` for the current LSST Swagger and endpoint probe results.
See `docs/MINIMAL_INGESTION.md` for the Checkpoint 1 real-ID and tiny sample workflow.
See `docs/BOUNDED_EXTRACTION.md` for the Checkpoint 2 bounded public REST feasibility workflow.
See `docs/FULL_NIGHT_FEASIBILITY.md` for the Checkpoint 3 partitioned REST full-night completeness feasibility workflow.
See `docs/DATA_TRANSFER_PLAN.md` for the Checkpoint 4 bulk/Data Transfer readiness and hybrid architecture plan.
See `docs/FINK_DATA_TRANSFER_REGISTRATION.md` and `docs/DATA_TRANSFER_DELIVERY_DROPZONE.md` for Checkpoint 5 registration and smoke-delivery workflow notes.
See `docs/REAL_DELIVERY_INGESTION.md` for Checkpoint 6 real Data Transfer smoke ingestion, nested-field handling, diagnostics, and completeness limits.
See `docs/FULL_NIGHT_TRANSFER_WORKFLOW.md` for Checkpoint 7 full-night all-alert preparation, topic registry, download command, ingestion, and validation gates.
See `docs/FULL_WEEK_FULL_PACKET_WORKFLOW.md` for Checkpoint 8A full-week full-packet preflight, command generation, progress monitoring, and raw-schema inspection.
See `docs/ADAPTIVE_RUN_MANIFESTS.md` for Checkpoint 9A manifest-driven run contracts, lifecycle states, claim states, and compatibility commands.
See `docs/MANIFEST_DRIVEN_INGESTION.md` for Checkpoint 9B guarded manifest ingestion, nightly splitting, validation, and diagnostics.
See `docs/FULL_PACKET_INGESTION_PERFORMANCE_NOTE.md` for the Checkpoint 10A full-packet performance note and quick partial stress-analysis path.
See `docs/FULL_WEEK_FULL_PACKET_WORKFLOW.md` for the Checkpoint 10B full available partial visual-analysis command, plots, notebook viewer, and completeness limits.

## Hybrid Architecture

REST mode supports bounded/candidate extraction, known-ID enrichment, diagnostics, and dashboards.

Data Transfer mode is the planned route for complete nightly alert census, historical bulk extraction, and population-level science after validation.

The first real smoke delivery has been ingested locally:

- topic: `ftransfer_lsst_2026-06-24_657339`
- raw files: `20` Parquet files
- raw rows: `8731`
- scope: `in_tns` tag-filtered light static packet

This proves smoke ingestion and validation, not full-night completeness.

## Data Transfer Smoke Workflow

```bash
cd "/Users/darim_shamsi/Documents/Fink Analysis"
source .venv/bin/activate

python -m pytest

python scripts/install_fink_client_helper.py
python scripts/check_fink_data_transfer_setup.py
python scripts/probe_fink_client_capabilities.py
python scripts/write_registration_checklist.py
python scripts/prepare_data_transfer_smoke_request.py
python scripts/report_data_transfer_next_action.py
```

After manual portal delivery:

```bash
python scripts/inspect_data_transfer_delivery.py
python scripts/run_data_transfer_smoke_ingestion.py
python scripts/validate_data_transfer_delivery.py
python scripts/report_data_transfer_next_action.py
```

## First Full-Night Data Transfer Workflow

Prepare request artifacts without submitting a job:

```bash
python scripts/prepare_full_night_transfer_request.py
```

After manual portal topic creation, add the non-secret topic metadata to `configs/data_transfer_topics.yaml`, then run:

```bash
python scripts/print_data_transfer_download_command.py --topic TOPIC
python scripts/run_full_night_ingestion.py --topic TOPIC
```

Full-night completeness is claimed only if validation allows `completeness_claim_allowed`.

## Adaptive Run Manifests

Run configs live under `configs/runs/` and are indexed by `configs/runs/index.yaml`.

```bash
python scripts/list_analysis_runs.py
python scripts/validate_run_manifest.py --all
python scripts/run_analysis.py \
  --run-config configs/runs/full_week_full_packet_2026-02-25_to_2026-03-04.yaml \
  --stage triage \
  --dry-run
```

Manifest mode is preferred for new Data Transfer analyses. Topic-based CLIs remain supported for compatibility.

## Manifest-Driven Ingestion

### Which Command Should I Run?

```bash
python scripts/list_analysis_runs.py
python scripts/validate_run_manifest.py --all
python scripts/run_analysis.py --run-config CONFIG --stage triage
python scripts/run_analysis.py --run-config CONFIG --stage inspect_raw
python scripts/run_analysis.py --run-config CONFIG --stage ingest --dry-run
python scripts/run_analysis.py --run-config CONFIG --stage all
```

Use `run_analysis.py` for new manifest-driven work. Older topic-based scripts remain compatibility helpers for existing smoke/full-night/full-week workflows.

Plan guarded ingestion without touching raw data:

```bash
python scripts/run_analysis.py \
  --run-config configs/runs/full_week_full_packet_2026-02-25_to_2026-03-04.yaml \
  --stage ingest \
  --dry-run
```

Controlled partial/debug smoke check:

```bash
python scripts/run_analysis.py \
  --run-config configs/runs/smoke_in_tns_2026-02-25.yaml \
  --stage all \
  --max-files 3 \
  --allow-partial \
  --skip-plots \
  --write-report
```

Partial/debug outputs are not science-ready and cannot support completeness claims.

## Partial Full-Packet Stress Analysis

Use this path for the partial full-week full-packet dataset. It avoids full nested JSON serialization and skips cutout/heavy fields by default.

```bash
python scripts/run_quick_full_packet_analysis.py \
  --run-config configs/runs/full_week_full_packet_2026-02-25_to_2026-03-04.yaml \
  --expected-total 1071519 \
  --max-files 5000 \
  --write-report
```

Viewer notebook:

```text
notebooks/10_full_packet_partial_stress_viewer.ipynb
```

This is partial/debug stress testing only. It does not support full-week, first-day, or all-alert completeness claims.

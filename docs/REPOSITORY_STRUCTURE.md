# Repository Structure

This repository is a local-first Fink/Rubin/LSST alert-analysis project. Source, documentation, tests, notebooks, non-secret configs, and lightweight fixtures are intended for Git. Raw broker deliveries, processed tables, generated reports, figures, logs, caches, and credentials are local-only.

## Layout

```text
src/                         core package logic
scripts/                     command-line entrypoints
configs/                     non-secret configs and topic registry
docs/                        project documentation
notebooks/                   reproducible analysis/viewer notebooks
tests/                       offline tests
data/fixtures/               lightweight tracked fixtures
data/raw/                    ignored raw broker deliveries
data/processed/              ignored generated processed tables
outputs/                     ignored generated reports/figures/run artifacts
outputs/maintenance/         lightweight tracked maintenance summaries
```

## What Should Be Committed

- Source code under `src/`.
- Command-line scripts under `scripts/`.
- Offline tests under `tests/`.
- Project documentation under `docs/`.
- Reproducible notebooks under `notebooks/`.
- Non-secret configs under `configs/`.
- Lightweight API/schema/sample fixtures under `data/fixtures/`.
- Lightweight maintenance summaries under `outputs/maintenance/`.
- Project metadata such as `README.md`, `requirements.txt`, `setup.py`, `setup.cfg`, and `pyproject.toml`.

## What Should Stay Local Only

- Raw Fink Data Transfer deliveries under `data/raw/`.
- Generated processed tables under `data/processed/`.
- Generated reports, figures, diagnostics, and run artifacts under `outputs/`, except tracked maintenance summaries.
- Local archive content under `outputs/archive/`.
- Local logs, caches, temporary files, and virtual environments.

## What Should Never Be Committed

- Credentials, passwords, tokens, key files, local auth outputs, and Kafka client secrets.
- Raw scientific delivery files or generated processed Parquet tables.
- Large generated figures/reports unless intentionally converted into small documentation.
- Full-night/full-week transfer outputs before an explicit data-management decision.

## Reproduce Smoke Ingestion

The real smoke delivery is local-only and ignored:

```text
data/raw/data_transfer/smoke_delivery/ftransfer_lsst_2026-06-24_657339/
```

Run:

```bash
python scripts/run_data_transfer_pipeline.py \
  --scope smoke_delivery \
  --startdate 2026-02-25 \
  --stopdate 2026-02-26 \
  --topic ftransfer_lsst_2026-06-24_657339 \
  --validate \
  --diagnostics \
  --plots
```

This validates the local smoke delivery only. It must not be interpreted as a full-night all-alert completeness proof.

## Prepare A Full-Night Request

Generate local request/checklist artifacts without submitting a job:

```bash
python scripts/prepare_full_night_transfer_request.py
```

After manual portal topic creation, record non-secret topic metadata in `configs/data_transfer_topics.yaml`, then print the download command:

```bash
python scripts/print_data_transfer_download_command.py --topic TOPIC
```

Do not store credentials in the repo.

## Run The Generalized Pipeline

```bash
python scripts/run_data_transfer_pipeline.py --scope tag_filtered_smoke_delivery --topic ftransfer_lsst_2026-06-24_657339
python scripts/run_full_night_ingestion.py --topic TOPIC
```

Pipeline outputs remain local under `data/processed/` and `outputs/`.

## Full-Week Full-Packet Preflight

Before downloading the full-week full-packet topic:

```bash
python scripts/register_data_transfer_topic.py --scope full_week_full_packet --survey lsst --topic ftransfer_lsst_2026-06-27_38507 --startdate 2026-02-25 --stopdate 2026-03-04 --content "Full packet" --all-alert
python scripts/preflight_full_packet_delivery.py --topic ftransfer_lsst_2026-06-27_38507
python scripts/print_data_transfer_download_command.py --topic ftransfer_lsst_2026-06-27_38507
```

Raw full-packet data remains ignored under `data/raw/data_transfer/full_week_full_packet/`.

## Run Hygiene Audit

```bash
python scripts/audit_repo_hygiene.py
```

This writes:

- `outputs/maintenance/REPO_CLEANUP_AUDIT.md`
- `outputs/maintenance/repo_cleanup_inventory.json`

The audit is read-only.

## Safely Clean Caches

Review first:

```bash
python scripts/clean_safe_caches.py --dry-run
```

Apply only after review:

```bash
python scripts/clean_safe_caches.py --apply
```

The cleaner is restricted to cache files/directories and must not remove scientific data, source code, docs, configs, notebooks, or tests.

## Adaptive Run Manifests

## Which Command Should I Run?

```bash
python scripts/list_analysis_runs.py
python scripts/validate_run_manifest.py --all
python scripts/run_analysis.py --run-config CONFIG --stage triage
python scripts/run_analysis.py --run-config CONFIG --stage inspect_raw
python scripts/run_analysis.py --run-config CONFIG --stage ingest --dry-run
python scripts/run_analysis.py --run-config CONFIG --stage all
python scripts/check_commit_readiness.py
```

Use `run_analysis.py` for new manifest-driven work. Legacy scripts are retained for compatibility.

Manifest configs live under:

```text
configs/runs/
configs/runs/templates/
```

The run index is:

```text
configs/runs/index.yaml
```

These files are lightweight provenance/configuration artifacts and are intended to be tracked. They may point to local-only raw, processed, or output paths, but they must not contain credentials or scientific payload data.

Use:

```bash
python scripts/list_analysis_runs.py
python scripts/validate_run_manifest.py --all
```

Manifest-backed commands are preferred for future nightly, weekly, and historical reruns. Existing topic-based commands remain available for compatibility.

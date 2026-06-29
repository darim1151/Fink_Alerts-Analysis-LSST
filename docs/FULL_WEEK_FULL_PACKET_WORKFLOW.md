# Full-Week Full-Packet Workflow

Checkpoint 8A prepares the repository to safely receive and inspect the first full-week full-packet LSST Data Transfer delivery. It does not download data, ingest the full packet, or make completeness claims.

## Why This Is Higher Risk

The prior real delivery was an `in_tns` light-static smoke transfer with `8731` rows. It proved credentials, Kafka consumption, nested-field preservation, and smoke diagnostics. The new full-week full-packet topic may be much larger and may include heavier nested, binary, or cutout-like fields. Treat the first download as an execution-readiness and raw-schema inspection step.

## Registered Topic

- Topic: `ftransfer_lsst_2026-06-27_38507`
- Survey: `lsst`
- Scope: `full_week_full_packet`
- UTC window: `2026-02-25` to `2026-03-04`
- Content: `Full packet`
- Filters: none recorded
- Completeness: unresolved until validation

## Register Or Update The Topic

```bash
python scripts/register_data_transfer_topic.py \
  --scope full_week_full_packet \
  --survey lsst \
  --topic ftransfer_lsst_2026-06-27_38507 \
  --startdate 2026-02-25 \
  --stopdate 2026-03-04 \
  --content "Full packet" \
  --all-alert \
  --notes "First full-week full-packet LSST Data Transfer request"
```

Use `--force` only to intentionally replace a non-secret registry entry.

## Run Preflight

```bash
python scripts/preflight_full_packet_delivery.py \
  --topic ftransfer_lsst_2026-06-27_38507
```

Preflight checks the registry, client availability, ignored raw/processed/output paths, disk space, visible credential-like files, previous smoke provenance, and raw-directory conflicts.

## Print The Download Command

```bash
python scripts/print_data_transfer_download_command.py \
  --scope full_week_full_packet \
  --startdate 2026-02-25 \
  --stopdate 2026-03-04 \
  --topic ftransfer_lsst_2026-06-27_38507
```

The script prints commands only. Run `fink_datatransfer` manually in the authenticated shell after reviewing preflight output.

## Monitor Download Progress

```bash
python scripts/summarize_download_progress.py \
  --raw-dir "data/raw/data_transfer/full_week_full_packet/2026-02-25_to_2026-03-04/ftransfer_lsst_2026-06-27_38507" \
  --topic ftransfer_lsst_2026-06-27_38507
```

Wait for Kafka lag to reach zero before treating the delivery as complete.

## Triage A Long Or Interrupted Download

If the full-packet download ran for hours, timed out, or may be partial, run:

```bash
python scripts/triage_full_packet_download.py \
  --topic ftransfer_lsst_2026-06-27_38507 \
  --expected-total 1071519 \
  --progress-from-terminal 456494 \
  --write-report

python scripts/recommend_download_action.py \
  --topic ftransfer_lsst_2026-06-27_38507 \
  --expected-total 1071519
```

Triage reads file metadata only. It reports missing, active, partial, corrupt, or raw-download-ready states. It does not claim scientific week completeness.

## Inspect Raw Full-Packet Files

```bash
python scripts/inspect_full_packet_delivery.py \
  --topic ftransfer_lsst_2026-06-27_38507
```

The inspector reports schema groups, nested fields, binary-like fields, suspected heavy/cutout-like fields, row counts, and unreadable files. It does not dump raw payload values or expand binary fields.

## Ingestion Guardrail

The guarded full-week entrypoint refuses partial raw data by default:

```bash
python scripts/run_full_week_full_packet_pipeline.py \
  --topic ftransfer_lsst_2026-06-27_38507 \
  --expected-total 1071519
```

Partial test ingestion requires `--allow-partial` and labels output as `partial_full_week_full_packet`. Do not use partial outputs for population-level science.

## Stop Conditions

Stop and review before ingestion if:

- preflight is `blocked`,
- raw path is not ignored by Git,
- credentials are visible to Git,
- disk space looks insufficient,
- download progress shows unreadable/corrupted files,
- raw inspection finds heavy fields that need a storage policy,
- Kafka lag does not reach zero.

## Next Phase

After raw download and inspection, build a full-packet ingestion policy using the observed schema. Do not process heavy binary/cutout-like fields into expanded tables until an explicit storage policy exists.

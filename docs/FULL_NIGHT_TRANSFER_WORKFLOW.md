# Full-Night Transfer Workflow

Checkpoint 7 prepares the first production-scale Fink LSST Data Transfer run. It does not submit a portal job automatically.

For full-week full-packet readiness, use `docs/FULL_WEEK_FULL_PACKET_WORKFLOW.md`; do not reuse the full-night scope label for multi-night full-packet data.

## Target Scope

- Survey: `lsst`
- UTC window: `2026-02-25` to `2026-02-26`
- Filter: none / all alerts, if the portal supports it
- Content: `Light static packet`
- Cutouts/FITS/images: none
- Local scope label: `full_night_all_alerts`

The prior smoke delivery was `in_tns` filtered. It proves access and ingestion, not full-night completeness.

## Prepare The Manual Request

```bash
python scripts/prepare_full_night_transfer_request.py
```

Review:

- `outputs/data_transfer/request_drafts/FULL_NIGHT_DATA_TRANSFER_REQUEST.md`
- `outputs/data_transfer/request_drafts/FULL_NIGHT_MANUAL_PORTAL_CHECKLIST.md`

Submit the portal request manually only after explicit confirmation. After the portal creates a topic, record it in:

```text
configs/data_transfer_topics.yaml
```

Do not store credentials, passwords, tokens, or auth logs in the registry.

## Download A Recorded Topic

After adding the topic, print the exact command:

```bash
python scripts/print_data_transfer_download_command.py --topic TOPIC
```

Expected command shape:

```bash
fink_datatransfer -survey lsst -topic TOPIC -outdir data/raw/data_transfer/full_night/2026-02-25_to_2026-02-26/TOPIC --dump_schemas --verbose
```

Run that command manually in the authenticated environment.

## Ingest And Validate

Once files exist locally:

```bash
python scripts/run_data_transfer_pipeline.py --scope full_night_all_alerts --topic TOPIC
```

or:

```bash
python scripts/run_full_night_ingestion.py --topic TOPIC
```

Outputs are written under:

- `data/processed/data_transfer/full_night/<run_id>/`
- `outputs/data_transfer/full_night/<run_id>/`

## Completeness Rule

Validation allows `completeness_claim_allowed` only when the evidence supports it:

- topic metadata says `scope: full_night_all_alerts`
- topic metadata says `all_alerts: true`
- topic metadata has no Fink filter
- raw delivery has nonzero rows
- processed alerts exist
- no hard validation failures are present
- recorded Kafka lag is not nonzero
- no truncation warning is recorded
- date-window validation passes, or the window mismatch is explicitly explained

If any of these fail, keep the full-night claim blocked or unresolved.

## Manifest Mode

Checkpoint 9A adds adaptive run manifests under `configs/runs/`. For new full-night requests, start from one of:

```text
configs/runs/templates/single_night_all_alert_light_static.yaml
configs/runs/templates/single_night_full_packet.yaml
```

Then validate the manifest before any portal or download work:

```bash
python scripts/validate_run_manifest.py --run-config configs/runs/YOUR_FULL_NIGHT_RUN.yaml
```

The manifest keeps the run window, packet type, filters, paths, lifecycle state, and claim state in one provenance object. A full-night completeness claim remains blocked or unresolved until strict validation updates it.

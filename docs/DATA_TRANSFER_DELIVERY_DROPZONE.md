# Data Transfer Delivery Drop-Zones

Use these local folders for manually delivered Data Transfer files:

- Smoke delivery: `data/raw/data_transfer/smoke_delivery/`
- Full-night delivery: `data/raw/data_transfer/full_night/`

## Allowed File Types

- `.parquet`
- `.avro`
- `.json`
- `.jsonl`
- `.csv` only if the portal provides it

Do not place cutouts, FITS files, images, credentials, tokens, `.env` files, or authentication logs in these folders.

## Git Behavior

The repository keeps `.gitkeep` files for the drop-zones but ignores delivered data files by default. This prevents accidental commits of large files or sensitive delivery artifacts.

## Naming

For smoke deliveries, keep the original portal filename when possible and optionally add a date prefix such as:

```text
2026-02-25_smoke_<portal-id>.parquet
```

For full-night deliveries, use a subfolder or filename that records the UTC window:

```text
2026-02-25_2026-02-26_full_night_<portal-id>.parquet
```

## Metadata

Save non-secret delivery manifests or README files if useful. Remove any auth headers, signed URLs, tokens, usernames, or private account identifiers before saving metadata in the repository.

## Commands

Smoke delivery:

```bash
python scripts/run_data_transfer_smoke_ingestion.py
python scripts/report_data_transfer_next_action.py
```

General delivery inspection:

```bash
python scripts/inspect_data_transfer_delivery.py
```

# Real Data Transfer Ingestion

Checkpoint 6 hardens the Data Transfer ingestion path against the first real Fink LSST smoke delivery.

## Real Smoke Delivery

- Topic: `ftransfer_lsst_2026-06-24_657339`
- Survey: `lsst`
- UTC window: `2026-02-25` to `2026-02-26`
- Filter: `in_tns`
- Content: `Light static packet`
- Downloaded alerts: `8731`
- Kafka lag after consumption: `0`
- Raw path: `data/raw/data_transfer/smoke_delivery/ftransfer_lsst_2026-06-24_657339/`
- Raw files: `20` Parquet files

This delivery proves Data Transfer access, topic consumption, and local raw-file preservation. It does not prove full-night completeness because it is tag-filtered and uses a light static packet.

## Why Ingestion Failed Before

The first ingestion attempt crashed while writing processed Parquet:

```text
pyarrow.lib.ArrowTypeError: ("Expected bytes, got a 'dict' object", "Conversion failed for column lc_features with type object")
```

The raw Fink payload contains nested Arrow fields. In pandas, columns such as `lc_features`, `clf`, `xm`, `pred`, and `misc` become object columns containing maps, structs, lists, and dictionaries. The old writer assumed scalar columns and passed those mixed objects directly to Parquet.

## Preservation Strategy

Raw files remain unchanged under `data/raw/`.

Processed tables are sanitized before Parquet writing:

- nested values are converted to stable JSON strings
- bytes are decoded safely or base64 encoded
- scalar columns are preserved where possible
- mixed object columns are serialized rather than dropped
- conversion details are recorded in `nested_conversion_report.json`

For `lc_features`, ingestion also writes:

- `alerts.parquet` with `lc_features_json`
- `lightcurve_features.parquet` with one row per object/source/band feature group when expansion is possible

## Run The Smoke Pipeline

```bash
python scripts/inspect_data_transfer_delivery.py
python scripts/run_data_transfer_smoke_ingestion.py
python scripts/validate_data_transfer_delivery.py
python scripts/report_data_transfer_next_action.py
```

The smoke ingestion script finds the latest topic directory under `data/raw/data_transfer/smoke_delivery/` unless `--delivery-dir` is supplied.

## Outputs

Run-scoped reports are written under:

```text
outputs/data_transfer/smoke_delivery/<run_id>/
```

Important files:

- `RAW_FIELD_INVENTORY.md`
- `raw_field_inventory.json`
- `SMOKE_DELIVERY_INSPECTION.md`
- `SMOKE_INGESTION_REPORT.md`
- `VALIDATION_SUMMARY.md`
- `validation_report.json`
- `SMOKE_DIAGNOSTICS.md`
- `smoke_diagnostics.json`
- `figures/`

Processed tables are written under:

```text
data/processed/data_transfer/smoke_delivery/<run_id>/
```

Expected tables:

- `alerts.parquet`
- `objects.parquet`
- `forced_photometry.parquet`
- `classifications.parquet`
- `lightcurve_features.parquet`
- `nightly_summary.parquet`
- `manifest.json`
- `nested_conversion_report.json`

## Completeness Status

The manifest records:

```json
{
  "scope": "tag_filtered_smoke_delivery",
  "full_night_complete": false,
  "reason": "in_tns filter and light static packet; not all-alert full-night production"
}
```

Validation treats the blocked completeness claim as expected. A smoke validation pass means the pipeline is ready to prepare a first full-night request after explicit user confirmation. It does not mean this smoke delivery is a full-night population dataset.

## Before Production Full-Night Transfer

Before requesting a full-night delivery:

- review `VALIDATION_SUMMARY.md`
- review `SMOKE_DIAGNOSTICS.md`
- confirm the next-action report says `ready_for_first_full_night_request`
- ask for explicit user confirmation
- keep credentials and auth logs out of the repository
- request full-night products only through a confirmed Data Transfer workflow

Checkpoint 7 adds the full-night preparation path:

```bash
python scripts/prepare_full_night_transfer_request.py
python scripts/print_data_transfer_download_command.py --topic TOPIC
python scripts/run_full_night_ingestion.py --topic TOPIC
```

The topic must first be created manually through the Fink Data Transfer portal and recorded in `configs/data_transfer_topics.yaml`.

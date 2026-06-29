# Manifest-Driven Ingestion

Checkpoint 9B extends adaptive run manifests into guarded ingestion, validation, diagnostics, and nightly splitting.

The ingestion engine uses the same manifest contract for smoke, single-night, and multi-night runs. It does not submit Fink jobs, does not run `fink_datatransfer`, and does not move or rewrite raw files.

## Guarded Ingestion

## Which Command Should I Run?

```bash
python scripts/list_analysis_runs.py
python scripts/validate_run_manifest.py --all
python scripts/run_analysis.py --run-config CONFIG --stage triage
python scripts/run_analysis.py --run-config CONFIG --stage inspect_raw
python scripts/run_analysis.py --run-config CONFIG --stage ingest --dry-run
python scripts/run_analysis.py --run-config CONFIG --stage all
```

Legacy topic-based scripts remain available for compatibility, but new ingestion work should use `run_analysis.py`.

Use `scripts/run_analysis.py` for manifest-driven ingestion:

```bash
python scripts/run_analysis.py \
  --run-config configs/runs/smoke_in_tns_2026-02-25.yaml \
  --stage all \
  --max-files 3 \
  --allow-partial \
  --skip-plots \
  --write-report
```

Before ingestion, the runner validates the manifest and runs raw triage. It refuses ingestion by default when raw state is:

- `raw_missing`
- `download_active`
- `partial_download`
- `partial_with_errors`
- `blocked_corrupt_raw`
- `blocked_disk_risk`

`--allow-partial` is only for schema/debug work. Partial outputs are written under paths containing `partial`, and completeness claims remain blocked.

## Output Layout

Canonical processed outputs:

```text
data/processed/data_transfer/runs/<run_name>/<run_id>/
  manifest_snapshot.yaml
  all/
    alerts.parquet
    objects.parquet
    classifications.parquet
    forced_photometry.parquet
    lightcurve_features.parquet
    nightly_summary.parquet
  nights/
    YYYY-MM-DD/
    out_of_window/
  partial/
```

Canonical reports:

```text
outputs/data_transfer/runs/<run_name>/<run_id>/
  EXECUTION_SUMMARY.md
  execution_manifest.json
  INGESTION_SUMMARY.md
  nested_column_report.json
  VALIDATION_SUMMARY.md
  validation_report.json
  DIAGNOSTICS.md
  diagnostics_report.json
```

For partial/debug ingestion, the run id and output directory include `partial`.

## Nightly Splitting

Night keys use UTC semantics and the manifest half-open window:

```text
startdate <= night < stopdate
```

Time-field priority:

1. trusted MJD fields such as `time_mjd`, `midpointMjdTai`, `r:midpointMjdTai`,
2. other MJD-like fields such as `firstDiaSourceMjdTai` and `brokerIngestMjd`,
3. timestamp-like fields,
4. fallback to `unsplit` with a warning.

Rows outside the manifest window are preserved under:

```text
nights/out_of_window/
```

Rows are not discarded because of time-window mismatch.

## Table Family

The shared table builder writes:

- `alerts`
- `objects`
- `classifications`
- `forced_photometry`
- `lightcurve_features`
- `nightly_summary`

Nested fields are preserved through the existing JSON-shadow/sanitization policy. Optional tables may be empty when source fields are absent; this is recorded as a warning rather than silently treated as a failure.

## Full-Week Ingestion

After raw download completes and triage reports a safe raw state, run:

```bash
python scripts/run_analysis.py \
  --run-config configs/runs/full_week_full_packet_2026-02-25_to_2026-03-04.yaml \
  --stage ingest \
  --write-report
```

For dry-run planning only:

```bash
python scripts/run_analysis.py \
  --run-config configs/runs/full_week_full_packet_2026-02-25_to_2026-03-04.yaml \
  --stage ingest \
  --dry-run
```

Do not use `--allow-partial` for science. It is a schema/debug mode.

## Partial Full-Packet Stress Testing

For partial full-packet deliveries, use the quick-analysis path instead of full manifest ingestion when the goal is schema/performance/data-quality stress testing.

The interrupted full-packet partial ingestion showed that serializing all nested fields into JSON shadow columns is too expensive for this delivery shape. The quick-analysis path:

- scans Parquet metadata without loading full payloads,
- extracts a bounded flattened sample,
- skips cutout/heavy fields by default,
- writes reports labeled `partial_debug_stress_test`,
- makes no completeness claims.

```bash
python scripts/run_quick_full_packet_analysis.py \
  --run-config configs/runs/full_week_full_packet_2026-02-25_to_2026-03-04.yaml \
  --expected-total 1071519 \
  --max-files 5000 \
  --write-report
```

View saved outputs in:

```text
notebooks/10_full_packet_partial_stress_viewer.ipynb
```

## Validation And Diagnostics

Validation distinguishes raw readiness, ingestion success, partial/debug outputs, schema warnings, night coverage, tag-filtered runs, all-alert runs, and packet-type uncertainty.

Completeness rules remain conservative:

- tag-filtered smoke runs stay blocked,
- partial/debug runs stay blocked,
- full-week all-alert runs remain unresolved unless strict evidence exists,
- validation warnings do not silently become validated science claims.

Diagnostics are data-quality summaries only: row counts, objects/sources, bands, time/MJD coverage, sky coverage, classification counts, nested coverage, missingness, and out-of-window counts. They do not make astrophysical interpretation claims.

## Notebooks

Notebooks remain viewers over manifest-backed outputs. Processing belongs in scripts and library modules so runs are reproducible and resumable.

## Do Not Commit

Do not commit raw data, processed tables, generated diagnostics, large outputs, credentials, auth logs, or local environment files. Manifest configs and lightweight docs/tests/source code are the intended committed artifacts.

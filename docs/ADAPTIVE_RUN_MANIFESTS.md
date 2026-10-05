# Adaptive Run Manifests

Checkpoint 9A introduces a manifest-driven run contract for Fink/Rubin/LSST alert analysis.

The run manifest is a scientific provenance and claim-control object, not just a config file. It records what was requested, what exists locally, whether the request was filtered or all-alert, packet type, lifecycle state, claim state, expected nights, local paths, processing options, and download evidence.

## Why This Exists

Earlier checkpoints intentionally used explicit scopes such as `smoke_delivery`, `full_night`, and `full_week_full_packet` to reduce operational risk. That helped validate the smoke pipeline and prepare a full-week request, but hardcoding every future date window or topic would create duplicate analysis code.

The adaptive manifest layer lets one engine represent:

- tag-filtered smoke deliveries,
- one all-alert night,
- multi-night or full-week windows,
- month-scale windows,
- full packet or light static packet runs,
- schema-only or partial raw inspections,
- historical reruns and future topics.

Existing smoke and full-week scripts remain available. The manifest layer is a compatibility contract in front of them, not a rewrite.

## Lifecycle State

Lifecycle state describes what is known about the local run:

- `declared`
- `topic_registered`
- `download_pending`
- `download_active`
- `raw_missing`
- `raw_partial`
- `raw_appears_complete_unverified`
- `raw_inspected`
- `ingestion_pending`
- `ingestion_partial`
- `ingested`
- `validated_with_warnings`
- `validated`
- `failed`
- `archived`

Partial or raw-missing runs cannot be treated as science-ready. `validated_with_warnings` is intentionally distinct from `validated`; warnings require explicit review.

## Claim State

Claim state controls what scientific claims are allowed:

- `blocked`
- `unresolved`
- `allowed`
- `rejected`

The manifest stores separate claim states for:

- all-alert completeness,
- night completeness,
- week completeness.

Tag-filtered smoke runs block completeness claims. Full-week all-alert runs keep week completeness `unresolved` until strict validation provides evidence. Unknown packet type also keeps claims conservative.

FINK-G3B.0 adds the generic Light Static scope `date_range` (two or more nights, unfiltered, all-alert) and a fourth claim, `range_completeness`. For `date_range` manifests the range claim is `unresolved` by default and `week_completeness` is `blocked`, because a range is not a week; every other scope keeps `range_completeness: blocked` and its historical claims. See `docs/RANGE_ACQUISITION_ORCHESTRATOR.md`.

## Expected Nights

Expected nights are derived from a half-open UTC window:

```text
startdate <= night < stopdate
```

For example, `2026-02-25` to `2026-03-04` produces seven nights:

```text
2026-02-25
2026-02-26
2026-02-27
2026-02-28
2026-03-01
2026-03-02
2026-03-03
```

Multi-night manifests keep this list explicitly so per-night status can be added without changing the run identity.

## Paths

Manifest paths must stay under:

- `data/raw/data_transfer`
- `data/processed/data_transfer`
- `outputs/data_transfer`

This prevents overlap with unrelated local files and keeps raw, processed, and generated artifacts isolated. `.gitignore` keeps scientific data and generated products local by default.

## Tag-Filtered Versus All-Alert

Filters imply `is_all_alert: false`. A manifest cannot set `is_all_alert: true` while also listing filters.

This distinction is critical:

- `in_tns` smoke delivery proves ingestion and validation mechanics.
- It does not prove all-alert completeness for a night.
- All-alert manifests can still only make completeness claims after strict validation.

## Add A New Run

1. Copy the closest template from `configs/runs/templates/`.
2. Set `run_name`, `topic`, `batch_id`, `startdate`, `stopdate`, `scope`, `packet_type`, `filters`, and `is_all_alert`.
3. Keep lifecycle conservative until raw evidence exists.
4. Derive or set paths under the approved data-transfer roots.
5. Add the config to `configs/runs/index.yaml`.
6. Validate:

```bash
python scripts/validate_run_manifest.py --run-config configs/runs/YOUR_RUN.yaml
python scripts/list_analysis_runs.py
```

## Inspect Through A Run Config

## Which Command Should I Run?

Preferred commands:

```bash
python scripts/list_analysis_runs.py
python scripts/validate_run_manifest.py --all
python scripts/run_analysis.py --run-config CONFIG --stage triage
python scripts/run_analysis.py --run-config CONFIG --stage inspect_raw
python scripts/run_analysis.py --run-config CONFIG --stage ingest --dry-run
python scripts/run_analysis.py --run-config CONFIG --stage all
```

Use manifest configs for new work. Topic-based scripts remain compatibility helpers.

Preferred manifest mode:

```bash
python scripts/triage_full_packet_download.py \
  --run-config configs/runs/full_week_full_packet_2026-02-25_to_2026-03-04.yaml
```

Legacy topic mode still works:

```bash
python scripts/triage_full_packet_download.py \
  --topic ftransfer_lsst_2026-06-27_38507
```

The unified runner supports safe inspection stages:

```bash
python scripts/run_analysis.py \
  --run-config configs/runs/full_week_full_packet_2026-02-25_to_2026-03-04.yaml \
  --stage triage \
  --dry-run
```

Checkpoint 9B adds guarded ingestion stages to the same runner:

```bash
python scripts/run_analysis.py \
  --run-config configs/runs/smoke_in_tns_2026-02-25.yaml \
  --stage all \
  --max-files 3 \
  --allow-partial \
  --skip-plots \
  --write-report
```

The runner still refuses raw-missing or partial data by default. `--allow-partial` creates explicitly labeled partial/debug outputs and keeps completeness claims blocked.

## Notebook Strategy

Notebooks should view and interpret manifest-backed outputs; they should not become processing engines.

Planned notebooks:

1. `notebooks/07_run_viewer.ipynb`: generic manifest/run viewer.
2. `notebooks/08_nightly_analysis.ipynb`: one night from either single-night or multi-night runs.
3. `notebooks/09_multi_night_analysis.ipynb`: cross-night analysis.

No new notebooks are added in Checkpoint 9A.

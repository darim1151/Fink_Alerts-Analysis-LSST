# Checkpoint 9C Architecture Audit

## Responsibility Map

- `run_state.py`: canonical lifecycle, claim, adaptive scope states, and legal transition checks.
- `run_manifest.py`: strict run manifest model, path derivation, claim defaults, validation, topic-registry adapters.
- `run_index.py`: lightweight index over known run config files.
- `run_ingestion.py`: guarded manifest-driven ingestion orchestration, raw-readiness planning, filewise processing, manifest snapshots, ingestion summaries.
- `table_builder.py`: shared processed table family interface over the existing Checkpoint 6 smoke table logic.
- `nightly_split.py`: UTC night-key derivation, out-of-window preservation, per-night table writes.
- `validation.py`: legacy table validation plus manifest-aware run validation and conservative claim updates.
- `diagnostics.py`: legacy smoke diagnostics plus manifest-aware data-quality diagnostics.
- `execution.py`: resumable execution manifest and per-file processing records.
- `scopes.py`: legacy Data Transfer scope/path policy used by topic registry and older scripts.
- `topic_registry.py`: non-secret topic registry, legacy topic metadata, download command rendering.
- `scripts/run_analysis.py`: preferred manifest-driven entrypoint for triage, inspection, guarded ingestion, validation, diagnostics, and next-action reporting.

## Preferred Scripts

- `scripts/list_analysis_runs.py`
- `scripts/validate_run_manifest.py --all`
- `scripts/run_analysis.py --run-config CONFIG --stage triage`
- `scripts/run_analysis.py --run-config CONFIG --stage inspect_raw`
- `scripts/run_analysis.py --run-config CONFIG --stage ingest --dry-run`
- `scripts/run_analysis.py --run-config CONFIG --stage all`
- `scripts/check_commit_readiness.py`

## Compatibility Wrappers

These remain valid for existing workflows and tests:

- `scripts/triage_full_packet_download.py`
- `scripts/inspect_full_packet_delivery.py`
- `scripts/preflight_full_packet_delivery.py`
- `scripts/register_data_transfer_topic.py`
- `scripts/report_data_transfer_next_action.py`
- `scripts/run_data_transfer_pipeline.py`
- `scripts/run_full_night_ingestion.py`
- `scripts/run_full_week_full_packet_pipeline.py`

Several now accept `--run-config` while preserving topic-based mode.

## Duplication And Fragmentation

- `scopes.py` and `run_manifest.py` both derive paths/scope meaning. This is intentional during compatibility, but `run_manifest.py` should become the preferred source for new analyses.
- `ingest.py` still owns the original smoke table split implementation. `table_builder.py` wraps that logic for manifest-driven runs instead of duplicating it.
- `validation.py` contains both legacy delivery checks and new manifest run validation. This is acceptable for now because the checks share table-level assumptions.
- `diagnostics.py` similarly contains smoke-era and manifest-era diagnostics. The manifest functions are thin wrappers over existing summaries.

## Risky Overlaps

- Legacy scripts can still write legacy output locations. New work should prefer `scripts/run_analysis.py` so output layout is manifest-driven and consistent.
- `run_manifest.py` and `topic_registry.py` can both represent topic metadata. Use manifests for run provenance; keep topic registry for backward-compatible topic lookup and download command rendering.
- Full-week full-packet ingestion must remain guarded until raw triage reports a safe state. The current manifest dry-run still reports `raw_missing`.

## Eventual Deprecation Candidates

Do not delete these now, but consider documenting them as compatibility-only after the manifest runner is proven:

- direct full-week pipeline entrypoints that bypass run manifests,
- direct full-night ingestion entrypoints that only accept topic registry records,
- older smoke-specific report commands whose outputs are superseded by manifest diagnostics.

## Current Recommendation

Commit the manifest architecture, guarded ingestion engine, docs, tests, and maintenance reports as one checkpoint. Keep all raw/processed/generated run artifacts local and ignored.

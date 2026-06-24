# REST Full-Night Completeness Feasibility

## Purpose

Checkpoint 3 asks the central project question:

Can public/no-login Fink REST enumerate all alerts or sources for one selected Rubin/Fink alert night without Data Transfer, Kafka, Livestream, Spark, login, admin access, cloud infrastructure, or cutout/image downloads?

This phase is intentionally stricter than bounded sampling. It does not call a result complete just because a query succeeds. Any partition that returns exactly the requested row cap is marked `possibly_truncated` and blocks a full-night completeness claim.

## Default Target Window

The default target is the first safe post-public-alert UTC calendar window:

- start: `2026-02-25`
- stop: `2026-02-26`
- timezone: `UTC`
- convention: UTC calendar dates; `stopdate` is assumed exclusive unless Fink documentation proves otherwise

The repository rejects target dates before `min_lsst_alert_date_utc: "2026-02-25"` by default. Local machine timezone, India timezone, and Seattle timezone must not affect REST payload dates.

Raw time fields such as `r:midpointMjdTai` are preserved. They are not reinterpreted as local time.

## Safety Rules

The default config under `full_night_feasibility` keeps the run bounded:

- `max_rows_per_partition: 50`
- `max_total_rows_safety: 5000`
- `allow_unfiltered_full_night_query: false`
- `stop_if_safety_cap_hit: true`
- `target_tag: null`
- `fallback_tag: "in_tns"`

If all-alert enumeration is not proven by the public REST contract, the runner performs only a clearly labeled tag-specific feasibility test using the fallback tag. It does not silently turn that into a full-night all-alert claim.

## Command

```bash
python scripts/run_full_night_feasibility.py
```

The script writes:

- `outputs/full_night_feasibility/latest_run.json`
- `outputs/full_night_feasibility/<run_id>/capability_diagnosis.json`
- `outputs/full_night_feasibility/<run_id>/capability_diagnosis.md`
- `outputs/full_night_feasibility/<run_id>/partition_plan.json`
- `outputs/full_night_feasibility/<run_id>/partition_results.json`
- `outputs/full_night_feasibility/<run_id>/completeness_accounting.json`
- `outputs/full_night_feasibility/<run_id>/validation_report.json`
- `outputs/full_night_feasibility/<run_id>/validation_summary.md`
- `outputs/full_night_feasibility/<run_id>/FULL_NIGHT_FEASIBILITY_REPORT.md`
- `outputs/full_night_feasibility/<run_id>/manifest.json`

Raw partition responses are saved under:

- `data/raw/full_night_feasibility/<run_id>/partitions/`

Processed rows, when any exist, are saved under:

- `data/processed/full_night_feasibility/<run_id>/deduplicated_rows.parquet`
- `data/processed/full_night_feasibility/<run_id>/deduplicated_rows.csv`
- `data/processed/full_night_feasibility/<run_id>/object_summary.parquet`
- `data/processed/full_night_feasibility/<run_id>/source_summary.parquet`
- `data/processed/full_night_feasibility/<run_id>/completeness_accounting.json`
- `data/processed/full_night_feasibility/<run_id>/manifest.json`

## What The Diagnosis Distinguishes

The capability diagnosis explicitly separates:

- all-alert completeness
- tag-specific completeness
- candidate-level lookup
- object enrichment
- forced-photometry enrichment

Known-ID object/source/forced-photometry retrieval is useful enrichment, but it is not an all-alert enumeration path.

## Partitioning

The default strategy builds UTC time partitions across the target window. A partition records:

- partition ID
- endpoint
- UTC start/stop payload
- requested row cap
- target tag if tag-specific
- intended completeness scope
- whether the result is exhausted, possibly truncated, failed, skipped, unsafe, or unsupported

If a partition is capped and supported refinement is available, the runner can split into smaller UTC partitions up to `time_partitions.max_count`. It stops when partitions are exhausted, max partition count is reached, or the total row safety cap is hit.

## Statistics And Denominators

The script attempts to query `/api/v1/statistics` for the selected target date and falls back to the existing fixture only as context. Statistics are used as a denominator only when they are proven to match the target UTC window and filters.

The accounting records:

- denominator availability
- denominator type
- direct comparability
- reason
- extracted count
- denominator count
- completeness fraction only when directly comparable

It never computes a misleading completeness fraction from broad yearly or non-filter-matched statistics.

## Decision Values

The final report uses one of:

- `yes`: all partitions are exhausted or explicitly complete, deduplication is clean, and a directly comparable denominator or all-pages completion supports completeness.
- `no`: public REST lacks the required filters/pagination and completeness cannot be reached safely.
- `unresolved`: public REST produced bounded evidence, but capped/failed/unsupported partitions or denominator gaps prevent proof.
- `unsafe_to_attempt`: the only apparent path would require an unbounded or very large public REST call.

## Latest Local Result

Latest run at the time this document was updated:

- run ID: `20260624T051005Z`
- target UTC window: `2026-02-25` to `2026-02-26`
- all-alert REST enumeration supported from contract: `false`
- tag-specific enumeration supported from contract: `true`
- sky-region enumeration supported from contract: `true`, but only as a regional path unless sky partitioning is enabled and exhausted
- pagination exposed: `false`
- row caps exposed: `true`
- attempted scope: `tag_specific` using fallback tag `in_tns`
- final partition count: `96`
- exhausted or empty partitions: `75`
- possibly truncated partitions: `21`
- failed/skipped/unsupported/unsafe partitions: `0`
- total raw rows: `1521`
- deduplicated rows: `1521`
- duplicate rate: `0.0`
- denominator available and directly comparable: `false`
- final decision: `rest_full_night_complete = unresolved`

The target-date `/api/v1/statistics` request succeeded but returned an empty list, so it did not provide a usable denominator for the selected UTC window. The REST result is scientifically useful as a bounded tag-specific extraction, but it does not prove complete full-night all-alert enumeration.

Checkpoint 4 keeps this REST pipeline intact and adds Data Transfer readiness as the planned full-night route.

Checkpoint 5 adds a fink-client install guide, registration/auth checklist, tiny smoke request draft, and smoke delivery ingestion path. It still does not authorize a full-night request without manual review.

## Difference From ANTARES

The ANTARES project used locus-centered search behavior and tiling around query caps. This Fink phase does not copy that architecture. Fink public REST may support bounded/tag-specific samples and known-ID enrichment, while complete all-alert historical extraction may require Data Transfer or another bulk-oriented access route.

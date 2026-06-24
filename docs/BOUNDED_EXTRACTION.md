# Bounded Public REST Extraction

## Purpose

Checkpoint 2 tests whether the public/no-login Fink LSST REST API can support a small, explicitly bounded extraction workflow beyond one-object lookup. It is a feasibility test, not a full-night alert census and not a science dataset.

This checkpoint still avoids Data Transfer, Kafka, Livestream, Spark, login-gated services, cutouts, FITS/images, large downloads, and unbounded all-alert queries.

## Bounded Query

The default local configuration is in `configs/local_smoke_test.yaml` under `bounded_extraction`:

- tag: `in_tns`
- date window: `2026-01-01` to `2026-01-02`
- maximum tag rows: `50`
- maximum detail objects: `10`
- request timeout: `60` seconds
- allowed endpoints: `tags`, `objects`, `sources`, `fp`, and `statistics`

The runner refuses unbounded tag queries when `fail_on_unbounded_query` is true.

## Command

```bash
python scripts/run_bounded_public_rest_extraction.py
```

The script writes a timestamped run and updates:

- `outputs/bounded_extraction/latest_run.json`

Use that file as the stable pointer to the latest bounded extraction artifacts.

## Outputs

For each run, raw JSON responses are saved under:

- `data/raw/bounded_extraction/<run_id>/`

Processed tables and run metadata are saved under:

- `data/processed/bounded_extraction/<run_id>/tag_rows.parquet`
- `data/processed/bounded_extraction/<run_id>/object_summary.parquet`
- `data/processed/bounded_extraction/<run_id>/objects.parquet`
- `data/processed/bounded_extraction/<run_id>/sources.parquet`
- `data/processed/bounded_extraction/<run_id>/forced_photometry.parquet`
- `data/processed/bounded_extraction/<run_id>/statistics.parquet`
- `data/processed/bounded_extraction/<run_id>/extraction_summary.json`
- `data/processed/bounded_extraction/<run_id>/manifest.json`

Reports are saved under:

- `outputs/bounded_extraction/<run_id>/endpoint_capabilities.json`
- `outputs/bounded_extraction/<run_id>/endpoint_capabilities.md`
- `outputs/bounded_extraction/<run_id>/endpoint_attempts.json`
- `outputs/bounded_extraction/<run_id>/validation_report.json`
- `outputs/bounded_extraction/<run_id>/validation_summary.md`
- `outputs/bounded_extraction/<run_id>/feasibility_report.json`
- `outputs/bounded_extraction/<run_id>/FEASIBILITY_REPORT.md`

Repository-level copies of the latest endpoint capability table are also saved at:

- `outputs/bounded_extraction/endpoint_capabilities.json`
- `outputs/bounded_extraction/endpoint_capabilities.md`

## Latest Local Result

The latest local run at the time this document was updated is `20260623T192730Z`.

Observed bounded outputs:

- tag rows: `50`
- unique objects in the tag sample: `15`
- detail objects fetched under cap: `10`
- unique source IDs in the tag sample: `50`
- object detail attempts: `10 / 10` succeeded
- source detail attempts: `10 / 10` succeeded
- forced-photometry detail attempts: `10 / 10` succeeded

Processed table shapes from that run:

| Table | Rows | Columns |
|---|---:|---:|
| `tag_rows` | 50 | 21 |
| `object_summary` | 15 | 10 |
| `objects` | 10 | 13 |
| `sources` | 4909 | 21 |
| `forced_photometry` | 5613 | 20 |
| `statistics` | 81 | 24 |

The large `sources` and `forced_photometry` row counts come from fetching complete detail responses for the capped set of 10 objects. They are still bounded by object count, not by all-night enumeration.

## Endpoint Capability Findings

The public contract analysis found:

- `/api/v1/tags` exposes row limit and date/window-style parameters and can be used for bounded tag samples.
- `/api/v1/objects`, `/api/v1/sources`, and `/api/v1/fp` are useful for known-ID detail retrieval.
- `/api/v1/statistics` can provide broad count context, but it is not directly comparable to a tag/date-window extract unless matching filters are available.
- No proven pagination or continuation strategy was exposed for all matching tag rows.
- Cutout/image endpoints remain intentionally excluded.

## Validation And Completeness

Validation checks cover non-empty tables, required normalized columns, ID presence, provenance columns, coordinate ranges, broad time sanity, duplicate keys, detail fetch coverage, and truncation/completeness metadata.

The latest run passed the core table checks but recorded a warning:

- `truncation_pagination`: the tag response hit the configured row limit, so additional matching rows may exist.

That warning is expected and important. This checkpoint proves bounded public REST sampling and known-ID detail retrieval. It does not prove complete full-night all-alert extraction.

## Feasibility Conclusion

- Public REST minimal lookup: `yes`
- Public REST bounded tag/window extraction: `yes`
- Public REST complete full-night all-alert extraction: `unresolved`

Data Transfer should be considered later if the project needs complete all-alert historical extraction or stronger completeness guarantees.

Checkpoint 3 builds on this result with partitioned REST full-night feasibility. It uses the first post-alert UTC window, `2026-02-25` to `2026-02-26`, and treats cap hits as blockers rather than warnings.

Checkpoint 4 preserves bounded REST extraction as the candidate/enrichment layer while preparing Data Transfer for complete nightly census work.

## Difference From ANTARES

ANTARES work involved broker-specific locus/search behavior and search-limit management. This Fink checkpoint follows Fink's public REST vocabulary instead: tags, object/source/forced-photometry detail endpoints, endpoint capabilities, explicit row caps, and completeness warnings. No ANTARES tiling or locus architecture is being copied into this repository.

# Versioned external-Parquet analytical foundation

`analysis_contract_v1` admits DELIVERY_VALIDATED LSST all-alert Light Static
records into a new run-scoped, immutable cohort catalog. Raw remains canonical.
No alerts are copied into DuckDB; views pin explicit Parquet file lists.

Use a clean checkout of a pushed commit and the dedicated analysis environment:

```sh
PYTHONPATH=src python scripts/build_analytical_catalog.py check \
  --acquisition-id <id> --data-root /astro/store/shire/FINK
PYTHONPATH=src python scripts/build_analytical_catalog.py build \
  --cohort configs/analysis_cohorts/month1.json \
  --data-root /astro/store/shire/FINK --run-id g4b-analytics-month1-v1 \
  --temporary-base /local/tmp
```

The read-only check reports schema/profile compatibility and explicitly marks
source integrity pending unless linked characterization establishes it. Builder
admission always rechecks exact per-acquisition and cohort source uniqueness,
nulls, positive source IDs, finite TAI times, row counts, population/object
counts, requested-window coverage and any pinned G4A daily/multiplicity evidence.
A failed run preserves the partial catalog and finalizes its manifest as FAILED;
use a justified new run ID after a correction. Existing runs are never reused.

## Contract and future admission

A new validated month joins through a checked-in cohort definition, not source
edits. Each entry supplies an acquisition ID and may pin a qualified
characterization run, summary SHA256 and code SHA. The accepted Month-1 cohort
pins G4A. Registry replay verifies receipt identity/evidence. The builder rejects
duplicate acquisition IDs/topics, overlapping half-open requested windows and
source-ID collisions even across non-overlapping deliveries. Out-of-window
source times require explicit resolution before v1 admission.

Required fields/types and semantics live in `analytics/contract.py` and are
exported into each run. Int64 IDs, float64 MJD TAI, a boolean SSO discriminator,
coordinates, band and source flux/error are required. Optional numeric fields
may widen between float32/64. Broker structs and the per-band feature map retain
evolving snapshots. Additional harmless fields get explicit inventory evidence;
required drift and newly exposed identity/history structures fail closed until a
reviewed contract extension. Optional differences use explicit projections in
separate acquisition/schema branches, never permissive union-by-name.

A one-schema receipt requires one schema-header check, retaining the accepted
full delivery audit. A multi-schema receipt requires metadata grouping to build
explicit compatible branches. Raw file counts/bytes/stat fingerprints are
checked without payload rehashing. A catalog pins its exact file list and raw
stat fingerprint; `open_catalog(path, data_root)` validates those inputs and
returns a read-only connection. A relocated archive needs a new catalog run.

## Relations and physical storage

External views:

- `sources`: one delivered source measurement, global `source_id` plus acquisition,
  topic and raw-file provenance; TAI time, coordinates, band and photometry.
- `dia_source_snapshots`: DIA detections plus their source-time broker snapshots.
- `sso_sources`: SSO sources without an invented SSO object key.
- `ambiguous_sources`: preserved discriminator disagreements/unclassified rows.
- `broker_snapshots`: `pred_json`, `clf_json`, `lc_features_json`, `xm_json`,
  `misc_json`, keyed by source and acquisition/time.
- `derived_dia_object_summary`: explicitly DERIVED delivered-row count, first/
  last TAI time, observed bands and number of contributing acquisitions.

`raw_dia_object_id` preserves the supplied ID. `dia_object_id` is meaningful only
for false SSO flags with positive IDs. True SSO flags with zero/null IDs are SSO;
other combinations remain ambiguous. Object summaries are not canonical Rubin
records; broker fields never collapse into one timeless object classification.

Physical tables are acquisition/catalog provenance, compatibility evidence,
requested UTC/TAI boundaries, source QC, daily coverage and a compact DIA
multiplicity histogram. No source, full object or enrichment table is populated.
Logical extension interfaces for historical detections, forced photometry,
versioned object context and SSO identity/orbits are part of the exported contract.
Missing enrichment relations do not prevent baseline queries.

## Time and snapshots

`observation_mjd_tai` always remains TAI. `utc_bounds` converts explicit UTC ISO
boundaries with qualified Astropy/ERFA, including leap seconds. `query_utc` applies
half-open numeric TAI filters to a supported source/snapshot relation. SQL users
can reuse the generated `requested_date_boundaries`. No UTC offset arithmetic
or per-source UTC relabeling occurs. Zero coverage retains
`zero_rows_in_validated_Fink_delivery` and makes no Rubin completeness claim.

DuckDB's `to_json` preserves nested broker fields, including its NaN/Infinity
encoding. This representation is queryable DuckDB JSON, not a promised RFC8259
export. Scientific aggregation must check `isfinite(TRY_CAST(... AS DOUBLE))`;
standard JSON exports must declare a finite/nonfinite policy. G4A nonfinite
`cats_score` counts are independently reconciled where linked.

## Reproducibility and performance

Authoritative artifacts use existing `data/processed/<run-id>`, `outputs/<run-id>`
and `manifests/<run-id>` conventions of the external data root. Artifacts are DATA
PLANE, never Git. The manifest pins cohort, contract/code/evidence lineage,
versions, input stat fingerprints, runtime and artifact SHA256s. Four DuckDB
threads and a 2GB DuckDB memory limit bound this shared-host qualification;
disposable spill data uses /local/tmp. The query benchmark is cache-influenced,
records wall time and process high-water RSS, and does not certify untested scale.

Month 2 can use the same baseline for delivered-source populations, sky/time
selection, detection sequences, DIA grouping, band/cadence summaries and broker
snapshot exploration. Full-history/forced-photometry/SSO identity science needs
separate enrichment or a separately adjudicated product. Readable/non-null
fields are not scientifically validated features.

Reference: https://duckdb.org/docs/lts/data/parquet/overview (external views and
projection/filter pushdown; consulted 2026-10-05).

## Offline qualification

G4B synthetic tests: 31 passed. Repaired committed-evidence tests: 11 passed.
Relevant G4A/handoff/data-root/date-range/schema tests: 83 passed. Combined focused
qualification: 125 passed. Full offline suite: 661 passed, zero failures. The
canonical evidence correction retains the historical R2 qualification, verifies
the production activation/approval binding and pins the accepted Month-1 receipt;
future qualified acquisitions are checked through their evidence chain without
Month-specific source edits or skipped tests.

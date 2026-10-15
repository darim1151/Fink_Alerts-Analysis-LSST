# Fink LSST final science-readiness qualification — Control packet

## A. Control ruling

**FINAL EXECUTION RESULT: PASS_WITH_LIMITATIONS.**

The exact `[2026-02-25, 2026-07-14)` cohort is scientifically admissible for analysis under `analysis_contract_v1` and the documented Fink Light Static limitations. This supports the requested meaning of `SCIENCE_READY_WITH_DOCUMENTED_LIMITATIONS`. The existing project metadata state remains `SCALE_READY_WITH_LIMITATIONS`; no new readiness enum or acquisition authority transition was introduced.

## B. Exact repository state

- Repository: `darim1151/Fink_Alerts-Analysis-LSST`.
- Branch: `codex/fink-final-science-readiness`.
- Final SHA: `the evidence commit containing this report`.
- Verified base SHA: `5f26a2026022e5ae7f708d4625aad6190ea3215f`.
- Build and full-suite candidate SHA: `7e58b13c77bd0e4bc81e41e3c2b4759db382499b`.
- Worktree: clean after the final evidence checkpoint.
- Push state: campaign checkpoints pushed; final evidence checkpoint pushed on completion.
- The final checkpoint adds qualification evidence only. Source code, scripts and tests are unchanged from the verified base.
- No PR or merge was created. The frozen Observatory adapter worktree was not modified.

## C. Five-window delivery and admission table

| Month | Acquisition ID | Requested half-open window | Topic | Delivery state | Validated rows | Parquet files | Bytes | Schema groups | Characterization run | Characterization | Compatibility | Cohort admission |
|---|---|---|---|---|---:|---:|---:|---|---|---|---|---|
| M1 | `acq_lsst_ls_v1_2026-02-25_to_2026-03-25_b1c7b482b56b` | [2026-02-25, 2026-03-25) | ftransfer_lsst_2026-10-05_555200 | DELIVERY_VALIDATED | 1,658,642 | 16,591 | 1,460,135,494 | 94e24bb1455f3bc2 | g4a-month1-20260225_20260325-v1 | PASS_WITH_LIMITATIONS | COMPATIBLE_WITH_LIMITATIONS | ADMITTED |
| M2 | `acq_lsst_ls_v1_2026-03-25_to_2026-04-25_0c852cd059fa` | [2026-03-25, 2026-04-25) | ftransfer_lsst_2026-10-06_270011 | DELIVERY_VALIDATED | 76,133 | 767 | 72,730,564 | 94e24bb1455f3bc2 | g5-month2-characterization-v1 | PASS_WITH_LIMITATIONS | COMPATIBLE_WITH_LIMITATIONS | ADMITTED |
| M3 | `acq_lsst_ls_v1_2026-04-25_to_2026-05-25_1ccf601aa29f` | [2026-04-25, 2026-05-25) | ftransfer_lsst_2026-10-06_644744 | DELIVERY_VALIDATED | 266,496 | 2,668 | 143,714,745 | 94e24bb1455f3bc2 | final-month3-characterization-20261006-v1 | PASS_WITH_LIMITATIONS | COMPATIBLE_WITH_LIMITATIONS | ADMITTED |
| M4 | `acq_lsst_ls_v1_2026-05-25_to_2026-06-25_ecc26d8ca904` | [2026-05-25, 2026-06-25) | ftransfer_lsst_2026-10-06_863953 | DELIVERY_VALIDATED | 923,667 | 9,242 | 407,951,807 | 94e24bb1455f3bc2 | final-month4-characterization-20261006-v1 | PASS_WITH_LIMITATIONS | COMPATIBLE_WITH_LIMITATIONS | ADMITTED |
| M5 | `acq_lsst_ls_v1_2026-06-25_to_2026-07-14_1ad0e9dadca7` | [2026-06-25, 2026-07-14) | ftransfer_lsst_2026-10-06_945583 | DELIVERY_VALIDATED | 4,177,009 | 41,774 | 1,813,007,795 | 94e24bb1455f3bc2 | final-month5-characterization-20261006-v1 | PASS_WITH_LIMITATIONS | COMPATIBLE_WITH_LIMITATIONS | ADMITTED |

Totals: **7,101,947 source rows**, 71,042 Parquet files and 3,897,540,405 bytes. All reconciliation checks pass; unreadable files = 0.

M1 and M2 characterization artifacts were verified and reused. M3–M5 were characterized once from `769568231e9c692802a2d6dfa79aab8efd7d59f8`. All characterization artifact hashes, raw fingerprints and delivery bindings were verified. M1–M4 inventories were reconstructed in the new audit run and matched their accepted full-inventory hashes exactly; M5’s full inventory is preserved in its topic manifest directory.

## D. Final cohort

- Name: `accepted_five_window_20260225_20260714`.
- Path: `configs/analysis_cohorts/final_20260225_20260714.json`.
- Canonical JSON SHA256 (the analytical manifest’s digest): `65117ce378064135b40bea714ac630407b0c8fef209a1089ee80f5a49237deda`.
- File-byte SHA256: `bc0ea4c3fca878e508614a1b68244b67635e4e90b6d154a947678cbd5296bd33`.
- Exact interval: `[2026-02-25, 2026-07-14)`; February 25 through July 13 inclusive.
- Acquisition count: 5; requested calendar days: 139; gaps: 0; overlaps: 0.
- Acquisition IDs and topics are unique. Profile remains `lsst_light_static_all_alerts_v1`; packet remains Light Static with no filters, blocks, catalog or SQL selection.

## E. Final analytical build

- Run ID: `final-five-window-analytics-20261006-v1`.
- Catalog: `/astro/store/shire/FINK/data/processed/final-five-window-analytics-20261006-v1/analytics.duckdb`.
- Manifest: `/astro/store/shire/FINK/manifests/final-five-window-analytics-20261006-v1/manifest.json`.
- Contract: `analysis_contract_v1`.
- Completion: `PASS_WITH_LIMITATIONS`; catalog reopened read-only and input fingerprints reverified.
- Code SHA: `7e58b13c77bd0e4bc81e41e3c2b4759db382499b`.
- Catalog SHA256: `bb308e3106d4ea3f8323e8e7032b73e84588275a546b31f3794dc23d18e5cefb`.
- Manifest SHA256: `166fc39792fbe41a68f03e2b3024f677e6ba0d76916da475938e0a66c2e52ac8`.
- Source rows: 7,101,947; DIA: 6,311,364; SSO: 790,583; ambiguous: 0.
- Distinct DIA grouping keys / derived DIA object groups: 3,487,175.
- Global `diaSourceId` uniqueness: VERIFIED. Total and distinct IDs both equal 7,101,947; duplicate excess = 0; unmapped source rows = 0. No DISTINCT, deduplication or keep-first operation was used to resolve source rows.
- Daily coverage: 139 requested days; 68 with delivered rows; 71 zero-delivery dates.
- Zero-delivery dates: 2026-03-05, 2026-03-11, 2026-03-12, 2026-03-13, 2026-03-14, 2026-03-15, 2026-03-16, 2026-03-17, 2026-03-18, 2026-03-19, 2026-03-20, 2026-03-21, 2026-03-22, 2026-03-23, 2026-03-24, 2026-03-25, 2026-03-26, 2026-03-27, 2026-03-28, 2026-03-29, 2026-03-30, 2026-03-31, 2026-04-01, 2026-04-02, 2026-04-03, 2026-04-17, 2026-04-18, 2026-04-19, 2026-04-20, 2026-04-23, 2026-04-24, 2026-04-25, 2026-04-26, 2026-04-27, 2026-04-28, 2026-04-29, 2026-05-03, 2026-05-04, 2026-05-05, 2026-05-06, 2026-05-07, 2026-05-08, 2026-05-09, 2026-05-10, 2026-05-11, 2026-05-13, 2026-05-14, 2026-05-15, 2026-05-20, 2026-05-21, 2026-05-28, 2026-06-02, 2026-06-03, 2026-06-09, 2026-06-10, 2026-06-11, 2026-06-15, 2026-06-16, 2026-06-17, 2026-06-18, 2026-06-19, 2026-06-21, 2026-06-22, 2026-06-23, 2026-06-24, 2026-06-25, 2026-07-02, 2026-07-03, 2026-07-04, 2026-07-05, 2026-07-08.
- Zero-date status is `zero_rows_in_validated_Fink_delivery`. It is not proof of zero Rubin alerts.
- Observation timestamps retain MJD TAI. All 139 UTC midnight boundary pairs were reconfirmed with qualified Astropy/ERFA conversion; no fixed-offset or per-row UTC relabeling was used.
- External views reference the explicit immutable Parquet file sets. Raw rows copied: 0. Physical tables contain only compact provenance, coverage and QC metadata.
- All build artifact hashes, contract exports, cohort hashes, characterization pins, catalog metadata and raw stat fingerprints agree. The completion marker follows successful qualification.

## F. Science-readiness verdict

**YES: `[2026-02-25, 2026-07-14)` is scientifically admissible for the documented delivered-source, DIA grouping, time/sky selection, detection sequence and evolving broker-snapshot analyses.** The qualification does not establish complete Rubin coverage, complete source histories, complete forced photometry, canonical objects/lightcurves, or validated classifier/calibration science.

## G. Active limitations

- Benchmark results are cache-influenced and do not qualify untested larger scales.
- Broker feature and prediction availability does not establish scientific validity.
- Broker pred/clf/lc_features/xm/misc values are evolving source-time snapshots, not timeless object properties.
- DIA object summaries describe only delivered detections within this cohort and are explicitly derived.
- DuckDB snapshot JSON may preserve NaN/Infinity; downstream exports and aggregations require an explicit finite-value policy.
- Feature history/reset rules, photometric/calibration validity and classifier validity are not independently qualified.
- Light Static lacks full history, forced photometry, full DIA records and SSO identity/orbits.
- Light Static packet is not Full Packet; source rows are not canonical objects or complete lightcurves.
- SSO classification is supported by pred.is_sso and DIA association accounting; independent SSO identity/orbits are absent.
- Shire storage is not backed up; raw data and analytical catalogs remain external to Git.
- Validated Fink delivery does not establish Rubin scientific completeness.
- Zero rows in a requested date do not establish zero Rubin alerts.

## H. Tests

Focused qualification: **125 passed, 1 failed**.

Complete offline suite, executed once at `7e58b13c77bd0e4bc81e41e3c2b4759db382499b`: **661 passed, 1 failed, 0 skipped**. The later final checkpoint contains evidence/documentation only.

The sole failure is `test_committed_acquisition_files_carry_no_credentials`. The lexical `9092` check matches the unchanged M3 inventory shard SHA256 `4e4b0afb07b356ed535d78a7c9e18b0f4760d9c3ad3f0523b4f49092619238cf`. The file is byte-identical to the verified base; structured credential detection passes across every committed acquisition file. Classification: **VERIFIED pre-existing, non-scientific false positive**. The assertion remains unchanged. There are no new failing repository tests. The campaign audit SQL-policy regression cases separately passed 7/7.

Test evidence: `configs/science_readiness/final_20260225_20260714_tests.json`; the complete test log and its digest are preserved in the run-scoped Arnor audit artifacts.

## I. Data integrity

| Check | Result |
|---|---|
| Raw data modified | NO |
| Authoritative data deleted | NO |
| Prior runs overwritten | NO |
| Credentials exposed | NO |
| Large science data committed | NO |

Raw per-file hashes match all five accepted inventories; names, sizes, mtimes and ctimes remain unchanged through validation, characterization, build and read-only reopening. All new outputs are run-scoped. Acquisition authority and historical accepted evidence remain intact.

## J. Canonical delta and evidence locations

- New truth: M5 delivery is canonically validated; M2 characterization is canonically pinned; M3–M5 have accepted, hash-bound characterization; the exact five-window cohort is declared and admitted; one completed external catalog and explicit readiness audit establish global source uniqueness and the documented analytical scope.
- Preserved truth: M1/M2 characterization content, M1–M4 delivery receipts, profile and contract semantics, immutable raw roots, prior successful/failed runs, historical limitations and the frozen Observatory adapter branch.
- Qualification JSON: `configs/science_readiness/final_20260225_20260714.json`.
- Data-plane audit output: `/astro/store/shire/FINK/outputs/final-science-readiness-20261006-v2/final_readiness_audit.json`.
- Data-plane final audit manifest/script: `/astro/store/shire/FINK/manifests/final-science-readiness-20261006-v2/`.
- Original inventories, test log and failed audit evidence: `/astro/store/shire/FINK/manifests/final-science-readiness-20261006-v1/`.
- Active scientific contradictions: **none**. No scientific invariant was weakened.
- The first read-only audit attempt failed because its substring check rejected `union_by_name` even when explicitly false. Stored SQL verified all five options were disabled. The failed v1 evidence and original script were preserved; the corrected policy check passed seven regression cases, and the final audit ran in a fresh v2 run without rebuilding the catalog.

Discrepancies: canonical M5 initially lagged its runtime transfer state (**VERIFIED**, reconciled by an append-only copy of the original attempt); older full inventories were absent from topic manifest directories (**VERIFIED**, reconstructed and matched to accepted hashes); the test substring failure is a baseline lexical collision (**VERIFIED**). Rubin completeness, classifier/feature validity, feature history/reset rules and independent SSO associations remain **UNKNOWN**. Population interpretation is **STRONGLY_SUPPORTED** by the conservative discriminator, not independent SSO identity. No hypothesis was promoted to project truth.

## Reproduction

Use the pushed build revision and the qualified analysis environment `/astro/store/shiren/mdarim/envs/fink-lsst-analysis-g4b-py311`. Choose a new run ID; never reuse the accepted run.

```sh
PYTHONPATH=src /astro/store/shiren/mdarim/envs/fink-lsst-analysis-g4b-py311/bin/python scripts/build_analytical_catalog.py build \
  --cohort configs/analysis_cohorts/final_20260225_20260714.json \
  --data-root /astro/store/shire/FINK --run-id NEW_UNIQUE_RUN_ID --temporary-base /local/tmp
```

Exact package versions and per-run commands are pinned in each characterization/build manifest. The campaign-specific `final_audit_v2.py` is preserved with its SHA256 in the audit manifest; it is intentionally exclusive-output and must be adapted to a fresh audit/build run for a rerun.

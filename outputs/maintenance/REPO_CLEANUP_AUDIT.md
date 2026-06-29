# Repository Cleanup Audit

- Created at UTC: `2026-06-27T21:39:24.503224+00:00`
- Branch: `main`
- HEAD: `831daad11132eb017ab34c0d5fb0357536c3bcc4`

## Directory Sizes

- `.`: `955M`
- `data`: `346M`
- `data/raw`: `35M`
- `data/processed`: `311M`
- `outputs`: `13M`

## Classification Counts

- `keep_tracked`: `159`
- `keep_local_ignored`: `545`
- `candidate_archive`: `215`
- `safe_cache_cleanup`: `19`
- `needs_user_decision`: `13`

## Large File Candidates

- `data/processed/data_transfer/smoke_delivery/20260627T205321Z/alerts.parquet`: `36.81 MB`
- `data/processed/data_transfer/smoke_delivery/20260627T210030Z/alerts.parquet`: `36.81 MB`
- `data/processed/data_transfer/smoke_delivery/20260627T211556Z/alerts.parquet`: `36.81 MB`
- `data/processed/data_transfer/smoke_delivery/20260627T211648Z/alerts.parquet`: `36.81 MB`
- `data/processed/data_transfer/smoke_delivery/20260627T213848Z/alerts.parquet`: `36.81 MB`
- `data/processed/data_transfer/smoke_delivery/20260627T205321Z/lightcurve_features.parquet`: `20.66 MB`
- `data/processed/data_transfer/smoke_delivery/20260627T210030Z/lightcurve_features.parquet`: `20.66 MB`
- `data/processed/data_transfer/smoke_delivery/20260627T211556Z/lightcurve_features.parquet`: `20.66 MB`
- `data/processed/data_transfer/smoke_delivery/20260627T211648Z/lightcurve_features.parquet`: `20.66 MB`
- `data/processed/data_transfer/smoke_delivery/20260627T213848Z/lightcurve_features.parquet`: `20.66 MB`
- `data/processed/full_night_feasibility/20260624T050717Z/partition_results.json`: `7.07 MB`
- `outputs/full_night_feasibility/20260624T050717Z/partition_results.json`: `7.07 MB`

## Suspicious Credential-Like Files

- None found by filename pattern.

## Never-Touch Paths

- `configs/data_transfer_topics.yaml`
- `data/processed/data_transfer/smoke_delivery/20260627T210030Z`
- `data/raw/data_transfer/smoke_delivery/ftransfer_lsst_2026-06-24_657339`
- `outputs/data_transfer/smoke_delivery/20260627T210030Z`
- `outputs/data_transfer/smoke_delivery/20260627T211648Z`

## Recommendations

- Keep raw and processed scientific data local and ignored.
- Commit source, docs, tests, notebooks, configs, fixtures, and maintenance summaries only.
- Review candidate_archive entries before moving anything to outputs/archive/.
- Run scripts/clean_safe_caches.py --dry-run before any cache deletion.
- Review large_file_candidates and keep them ignored unless they are deliberate lightweight fixtures.
- Resolve needs_user_decision entries manually; do not delete them automatically.

## Category Samples


### keep_tracked

- `.gitignore`
- `README.md`
- `configs/Untitled.ipynb`
- `configs/data_transfer_topics.yaml`
- `configs/local_smoke_test.yaml`
- `data/.gitkeep`
- `data/fixtures/.gitkeep`
- `data/fixtures/fink_lsst_api_contract_summary.json`
- `data/fixtures/fink_lsst_statistics_sample.json`
- `data/fixtures/fink_lsst_swagger.json`
- `data/fixtures/fink_lsst_tags.json`
- `data/fixtures/samples`
- `data/fixtures/samples/.gitkeep`
- `data/fixtures/samples/fink_lsst_blocks_sample.json`
- `data/fixtures/samples/fink_lsst_statistics_2026_sample.json`
- `data/fixtures/samples/fink_lsst_tags_id_lookup_sample.json`
- `data/fixtures/samples/fink_lsst_tags_sample.json`
- `data/fixtures/schema`
- `data/fixtures/schema/.gitkeep`
- `data/fixtures/schema/fink_lsst_schema_conesearch.json`
- `data/fixtures/schema/fink_lsst_schema_fp.json`
- `data/fixtures/schema/fink_lsst_schema_objects.json`
- `data/fixtures/schema/fink_lsst_schema_sources.json`
- `data/fixtures/schema/fink_lsst_schema_statistics.json`
- `data/fixtures/schema/fink_lsst_schema_tags.json`
- `data/processed/.gitkeep`
- `data/processed/bounded_extraction/.gitkeep`
- `data/processed/data_transfer/.gitkeep`
- `data/processed/full_night_feasibility/.gitkeep`
- `data/processed/minimal_samples/.gitkeep`
- `data/raw/.gitkeep`
- `data/raw/bounded_extraction/.gitkeep`
- `data/raw/data_transfer/.gitkeep`
- `data/raw/data_transfer/full_night/.gitkeep`
- `data/raw/data_transfer/smoke_delivery/.gitkeep`
- `data/raw/full_night_feasibility/.gitkeep`
- `data/raw/minimal_samples/.gitkeep`
- `docs/API_CONTRACTS.md`
- `docs/BOUNDED_EXTRACTION.md`
- `docs/DATA_MODEL.md`
- `docs/DATA_TRANSFER_DELIVERY_DROPZONE.md`
- `docs/DATA_TRANSFER_PLAN.md`
- `docs/FINK_ACCESS_NOTES.md`
- `docs/FINK_DATA_TRANSFER_REGISTRATION.md`
- `docs/FULL_NIGHT_FEASIBILITY.md`
- `docs/FULL_NIGHT_TRANSFER_WORKFLOW.md`
- `docs/IMPORTANT_RUNS.md`
- `docs/MINIMAL_INGESTION.md`
- `docs/REAL_DELIVERY_INGESTION.md`
- `docs/REPOSITORY_STRUCTURE.md`
- `docs/SCIENCE_PLAN.md`
- `notebooks/00_fink_access_smoke_test.ipynb`
- `notebooks/01_schema_exploration.ipynb`
- `notebooks/02_minimal_public_rest_ingestion.ipynb`
- `notebooks/03_bounded_public_rest_extraction.ipynb`
- `notebooks/04_full_night_feasibility.ipynb`
- `notebooks/05_data_transfer_readiness.ipynb`
- `notebooks/06_data_transfer_smoke_ingestion.ipynb`
- `outputs/.gitkeep`
- `outputs/api_contracts/.gitkeep`
- `outputs/bounded_extraction/.gitkeep`
- `outputs/data_transfer/.gitkeep`
- `outputs/full_night_feasibility/.gitkeep`
- `outputs/id_discovery/.gitkeep`
- `outputs/maintenance/ARCHIVE_RECOMMENDATIONS.md`
- `outputs/maintenance/IMPORTANT_RUNS.md`
- `outputs/maintenance/PRE_CLEANUP_SNAPSHOT.md`
- `outputs/maintenance/REPO_CLEANUP_AUDIT.md`
- `outputs/maintenance/pre_cleanup_diff_stat.txt`
- `outputs/maintenance/pre_cleanup_git_status.txt`
- `outputs/maintenance/repo_cleanup_inventory.json`
- `outputs/minimal_ingestion/.gitkeep`
- `outputs/minimal_ingestion/tables_preview/.gitkeep`
- `outputs/smoke_test/.gitkeep`
- `pyproject.toml`
- `requirements.txt`
- `scripts/audit_repo_hygiene.py`
- `scripts/build_minimal_internal_tables.py`
- `scripts/check_fink_data_transfer_setup.py`
- `scripts/clean_safe_caches.py`
- ... 79 more

### keep_local_ignored

- `arrow_schema_ftransfer_lsst_2026-06-24_657339.metadata`
- `avro_schema_ftransfer_lsst_2026-06-24_657339.json`
- `data/processed/bounded_extraction`
- `data/processed/bounded_extraction/20260623T192258Z`
- `data/processed/bounded_extraction/20260623T192258Z/extraction_summary.json`
- `data/processed/bounded_extraction/20260623T192258Z/forced_photometry.parquet`
- `data/processed/bounded_extraction/20260623T192258Z/manifest.json`
- `data/processed/bounded_extraction/20260623T192258Z/objects.parquet`
- `data/processed/bounded_extraction/20260623T192258Z/sources.parquet`
- `data/processed/bounded_extraction/20260623T192258Z/statistics.parquet`
- `data/processed/bounded_extraction/20260623T192258Z/tag_rows.parquet`
- `data/processed/bounded_extraction/20260623T192537Z`
- `data/processed/bounded_extraction/20260623T192537Z/extraction_summary.json`
- `data/processed/bounded_extraction/20260623T192537Z/forced_photometry.parquet`
- `data/processed/bounded_extraction/20260623T192537Z/manifest.json`
- `data/processed/bounded_extraction/20260623T192537Z/object_summary.parquet`
- `data/processed/bounded_extraction/20260623T192537Z/objects.parquet`
- `data/processed/bounded_extraction/20260623T192537Z/sources.parquet`
- `data/processed/bounded_extraction/20260623T192537Z/statistics.parquet`
- `data/processed/bounded_extraction/20260623T192537Z/tag_rows.parquet`
- `data/processed/bounded_extraction/20260623T192632Z`
- `data/processed/bounded_extraction/20260623T192632Z/extraction_summary.json`
- `data/processed/bounded_extraction/20260623T192632Z/forced_photometry.parquet`
- `data/processed/bounded_extraction/20260623T192632Z/manifest.json`
- `data/processed/bounded_extraction/20260623T192632Z/object_summary.parquet`
- `data/processed/bounded_extraction/20260623T192632Z/objects.parquet`
- `data/processed/bounded_extraction/20260623T192632Z/sources.parquet`
- `data/processed/bounded_extraction/20260623T192632Z/statistics.parquet`
- `data/processed/bounded_extraction/20260623T192632Z/tag_rows.parquet`
- `data/processed/bounded_extraction/20260623T192730Z`
- `data/processed/bounded_extraction/20260623T192730Z/extraction_summary.json`
- `data/processed/bounded_extraction/20260623T192730Z/forced_photometry.parquet`
- `data/processed/bounded_extraction/20260623T192730Z/manifest.json`
- `data/processed/bounded_extraction/20260623T192730Z/object_summary.parquet`
- `data/processed/bounded_extraction/20260623T192730Z/objects.parquet`
- `data/processed/bounded_extraction/20260623T192730Z/sources.parquet`
- `data/processed/bounded_extraction/20260623T192730Z/statistics.parquet`
- `data/processed/bounded_extraction/20260623T192730Z/tag_rows.parquet`
- `data/processed/data_transfer`
- `data/processed/data_transfer/20260624T055031Z`
- `data/processed/data_transfer/smoke_delivery`
- `data/processed/data_transfer/smoke_delivery/20260624T063058Z`
- `data/processed/data_transfer/smoke_delivery/20260624T072350Z`
- `data/processed/data_transfer/smoke_delivery/20260624T072631Z`
- `data/processed/data_transfer/smoke_delivery/20260624T072741Z`
- `data/processed/data_transfer/smoke_delivery/20260624T192541Z`
- `data/processed/data_transfer/smoke_delivery/20260627T203129Z`
- `data/processed/data_transfer/smoke_delivery/20260627T203151Z`
- `data/processed/data_transfer/smoke_delivery/20260627T205321Z`
- `data/processed/data_transfer/smoke_delivery/20260627T205321Z/alerts.parquet`
- `data/processed/data_transfer/smoke_delivery/20260627T205321Z/classifications.parquet`
- `data/processed/data_transfer/smoke_delivery/20260627T205321Z/forced_photometry.parquet`
- `data/processed/data_transfer/smoke_delivery/20260627T205321Z/lightcurve_features.parquet`
- `data/processed/data_transfer/smoke_delivery/20260627T205321Z/manifest.json`
- `data/processed/data_transfer/smoke_delivery/20260627T205321Z/nested_conversion_report.json`
- `data/processed/data_transfer/smoke_delivery/20260627T205321Z/nightly_summary.parquet`
- `data/processed/data_transfer/smoke_delivery/20260627T205321Z/objects.parquet`
- `data/processed/data_transfer/smoke_delivery/20260627T210030Z`
- `data/processed/data_transfer/smoke_delivery/20260627T210030Z/alerts.parquet`
- `data/processed/data_transfer/smoke_delivery/20260627T210030Z/classifications.parquet`
- `data/processed/data_transfer/smoke_delivery/20260627T210030Z/forced_photometry.parquet`
- `data/processed/data_transfer/smoke_delivery/20260627T210030Z/lightcurve_features.parquet`
- `data/processed/data_transfer/smoke_delivery/20260627T210030Z/manifest.json`
- `data/processed/data_transfer/smoke_delivery/20260627T210030Z/nested_conversion_report.json`
- `data/processed/data_transfer/smoke_delivery/20260627T210030Z/nightly_summary.parquet`
- `data/processed/data_transfer/smoke_delivery/20260627T210030Z/objects.parquet`
- `data/processed/data_transfer/smoke_delivery/20260627T211556Z`
- `data/processed/data_transfer/smoke_delivery/20260627T211556Z/alerts.parquet`
- `data/processed/data_transfer/smoke_delivery/20260627T211556Z/classifications.parquet`
- `data/processed/data_transfer/smoke_delivery/20260627T211556Z/forced_photometry.parquet`
- `data/processed/data_transfer/smoke_delivery/20260627T211556Z/lightcurve_features.parquet`
- `data/processed/data_transfer/smoke_delivery/20260627T211556Z/manifest.json`
- `data/processed/data_transfer/smoke_delivery/20260627T211556Z/nested_conversion_report.json`
- `data/processed/data_transfer/smoke_delivery/20260627T211556Z/nightly_summary.parquet`
- `data/processed/data_transfer/smoke_delivery/20260627T211556Z/objects.parquet`
- `data/processed/data_transfer/smoke_delivery/20260627T211648Z`
- `data/processed/data_transfer/smoke_delivery/20260627T211648Z/alerts.parquet`
- `data/processed/data_transfer/smoke_delivery/20260627T211648Z/classifications.parquet`
- `data/processed/data_transfer/smoke_delivery/20260627T211648Z/forced_photometry.parquet`
- `data/processed/data_transfer/smoke_delivery/20260627T211648Z/lightcurve_features.parquet`
- ... 465 more

### candidate_archive

- `outputs/bounded_extraction/20260623T192258Z`
- `outputs/bounded_extraction/20260623T192258Z/FEASIBILITY_REPORT.md`
- `outputs/bounded_extraction/20260623T192258Z/endpoint_attempts.json`
- `outputs/bounded_extraction/20260623T192258Z/endpoint_capabilities.json`
- `outputs/bounded_extraction/20260623T192258Z/endpoint_capabilities.md`
- `outputs/bounded_extraction/20260623T192258Z/feasibility_report.json`
- `outputs/bounded_extraction/20260623T192258Z/manifest.json`
- `outputs/bounded_extraction/20260623T192258Z/validation_report.json`
- `outputs/bounded_extraction/20260623T192258Z/validation_summary.md`
- `outputs/bounded_extraction/20260623T192537Z`
- `outputs/bounded_extraction/20260623T192537Z/FEASIBILITY_REPORT.md`
- `outputs/bounded_extraction/20260623T192537Z/endpoint_attempts.json`
- `outputs/bounded_extraction/20260623T192537Z/endpoint_capabilities.json`
- `outputs/bounded_extraction/20260623T192537Z/endpoint_capabilities.md`
- `outputs/bounded_extraction/20260623T192537Z/feasibility_report.json`
- `outputs/bounded_extraction/20260623T192537Z/manifest.json`
- `outputs/bounded_extraction/20260623T192537Z/validation_report.json`
- `outputs/bounded_extraction/20260623T192537Z/validation_summary.md`
- `outputs/bounded_extraction/20260623T192632Z`
- `outputs/bounded_extraction/20260623T192632Z/FEASIBILITY_REPORT.md`
- `outputs/bounded_extraction/20260623T192632Z/endpoint_attempts.json`
- `outputs/bounded_extraction/20260623T192632Z/endpoint_capabilities.json`
- `outputs/bounded_extraction/20260623T192632Z/endpoint_capabilities.md`
- `outputs/bounded_extraction/20260623T192632Z/feasibility_report.json`
- `outputs/bounded_extraction/20260623T192632Z/manifest.json`
- `outputs/bounded_extraction/20260623T192632Z/validation_report.json`
- `outputs/bounded_extraction/20260623T192632Z/validation_summary.md`
- `outputs/bounded_extraction/20260623T192730Z`
- `outputs/bounded_extraction/20260623T192730Z/FEASIBILITY_REPORT.md`
- `outputs/bounded_extraction/20260623T192730Z/endpoint_attempts.json`
- `outputs/bounded_extraction/20260623T192730Z/endpoint_capabilities.json`
- `outputs/bounded_extraction/20260623T192730Z/endpoint_capabilities.md`
- `outputs/bounded_extraction/20260623T192730Z/feasibility_report.json`
- `outputs/bounded_extraction/20260623T192730Z/manifest.json`
- `outputs/bounded_extraction/20260623T192730Z/validation_report.json`
- `outputs/bounded_extraction/20260623T192730Z/validation_summary.md`
- `outputs/data_transfer/20260624T055031Z`
- `outputs/data_transfer/20260624T055031Z/INGESTION_REPORT.md`
- `outputs/data_transfer/20260624T055031Z/VALIDATION_SUMMARY.md`
- `outputs/data_transfer/20260624T055031Z/validation_report.json`
- `outputs/data_transfer/request_drafts`
- `outputs/data_transfer/request_drafts/DATA_TRANSFER_REQUEST.md`
- `outputs/data_transfer/request_drafts/FULL_NIGHT_DATA_TRANSFER_REQUEST.json`
- `outputs/data_transfer/request_drafts/FULL_NIGHT_DATA_TRANSFER_REQUEST.md`
- `outputs/data_transfer/request_drafts/FULL_NIGHT_MANUAL_PORTAL_CHECKLIST.md`
- `outputs/data_transfer/request_drafts/MANUAL_PORTAL_CHECKLIST.md`
- `outputs/data_transfer/request_drafts/SMOKE_DATA_TRANSFER_REQUEST.json`
- `outputs/data_transfer/request_drafts/SMOKE_DATA_TRANSFER_REQUEST.md`
- `outputs/data_transfer/request_drafts/SMOKE_MANUAL_PORTAL_CHECKLIST.md`
- `outputs/data_transfer/request_drafts/data_transfer_request.json`
- `outputs/data_transfer/request_drafts/full_night_request_manifest.json`
- `outputs/data_transfer/request_drafts/portal_exports`
- `outputs/data_transfer/request_drafts/portal_exports/datatransfer_20260624_085934.yml`
- `outputs/data_transfer/request_drafts/request_validation.json`
- `outputs/data_transfer/request_drafts/smoke_request_manifest.json`
- `outputs/data_transfer/smoke_delivery/20260624T063058Z`
- `outputs/data_transfer/smoke_delivery/20260624T063058Z/SMOKE_DELIVERY_INSPECTION.md`
- `outputs/data_transfer/smoke_delivery/20260624T063058Z/SMOKE_INGESTION_REPORT.md`
- `outputs/data_transfer/smoke_delivery/20260624T063058Z/SMOKE_VALIDATION_SUMMARY.md`
- `outputs/data_transfer/smoke_delivery/20260624T063058Z/smoke_delivery_inspection.json`
- `outputs/data_transfer/smoke_delivery/20260624T063058Z/smoke_manifest.json`
- `outputs/data_transfer/smoke_delivery/20260624T063058Z/smoke_validation_report.json`
- `outputs/data_transfer/smoke_delivery/20260624T072350Z`
- `outputs/data_transfer/smoke_delivery/20260624T072350Z/SMOKE_DELIVERY_INSPECTION.md`
- `outputs/data_transfer/smoke_delivery/20260624T072350Z/SMOKE_INGESTION_REPORT.md`
- `outputs/data_transfer/smoke_delivery/20260624T072350Z/SMOKE_VALIDATION_SUMMARY.md`
- `outputs/data_transfer/smoke_delivery/20260624T072350Z/smoke_delivery_inspection.json`
- `outputs/data_transfer/smoke_delivery/20260624T072350Z/smoke_manifest.json`
- `outputs/data_transfer/smoke_delivery/20260624T072350Z/smoke_validation_report.json`
- `outputs/data_transfer/smoke_delivery/20260624T072631Z`
- `outputs/data_transfer/smoke_delivery/20260624T072631Z/SMOKE_DELIVERY_INSPECTION.md`
- `outputs/data_transfer/smoke_delivery/20260624T072631Z/SMOKE_INGESTION_REPORT.md`
- `outputs/data_transfer/smoke_delivery/20260624T072631Z/SMOKE_VALIDATION_SUMMARY.md`
- `outputs/data_transfer/smoke_delivery/20260624T072631Z/smoke_delivery_inspection.json`
- `outputs/data_transfer/smoke_delivery/20260624T072631Z/smoke_manifest.json`
- `outputs/data_transfer/smoke_delivery/20260624T072631Z/smoke_validation_report.json`
- `outputs/data_transfer/smoke_delivery/20260624T072741Z`
- `outputs/data_transfer/smoke_delivery/20260624T072741Z/SMOKE_DELIVERY_INSPECTION.md`
- `outputs/data_transfer/smoke_delivery/20260624T072741Z/SMOKE_INGESTION_REPORT.md`
- `outputs/data_transfer/smoke_delivery/20260624T072741Z/SMOKE_VALIDATION_SUMMARY.md`
- ... 135 more

### safe_cache_cleanup

- `.DS_Store`
- `.pytest_cache`
- `.pytest_cache/.gitignore`
- `.pytest_cache/CACHEDIR.TAG`
- `.pytest_cache/README.md`
- `.pytest_cache/v`
- `.pytest_cache/v/cache`
- `.pytest_cache/v/cache/lastfailed`
- `.pytest_cache/v/cache/nodeids`
- `configs/.ipynb_checkpoints`
- `configs/.ipynb_checkpoints/Untitled-checkpoint.ipynb`
- `notebooks/.ipynb_checkpoints`
- `notebooks/.ipynb_checkpoints/00_fink_access_smoke_test-checkpoint.ipynb`
- `notebooks/.ipynb_checkpoints/01_schema_exploration-checkpoint.ipynb`
- `notebooks/.ipynb_checkpoints/02_minimal_public_rest_ingestion-checkpoint.ipynb`
- `notebooks/.ipynb_checkpoints/03_bounded_public_rest_extraction-checkpoint.ipynb`
- `notebooks/.ipynb_checkpoints/04_full_night_feasibility-checkpoint.ipynb`
- `notebooks/.ipynb_checkpoints/05_data_transfer_readiness-checkpoint.ipynb`
- `outputs/full_night_feasibility/.DS_Store`

### needs_user_decision

- `configs`
- `data`
- `data/fixtures`
- `data/processed`
- `data/raw`
- `data/samples`
- `data/schema`
- `docs`
- `notebooks`
- `outputs`
- `scripts`
- `src`
- `tests`

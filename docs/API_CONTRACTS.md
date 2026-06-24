# API Contracts

## Base URLs

- Primary LSST API: `https://api.lsst.fink-portal.org`
- Versioned REST base: `https://api.lsst.fink-portal.org/api/v1`
- Swagger/OpenAPI document: `https://api.lsst.fink-portal.org/swagger.json`
- Optional ZTF reference API: `https://api.ztf.fink-portal.org`

The generic `https://api.fink-portal.org` host is treated as ambiguous for LSST work and is not used as the primary target.

## Swagger Discovery

Checkpoint 0.6 successfully retrieved `swagger.json` and saved it to:

- `data/fixtures/fink_lsst_swagger.json`
- `data/fixtures/fink_lsst_api_contract_summary.json`

The discovered paths are:

- `/api/v1/blocks`
- `/api/v1/conesearch`
- `/api/v1/cutouts`
- `/api/v1/fp`
- `/api/v1/objects`
- `/api/v1/resolver`
- `/api/v1/schema`
- `/api/v1/skymap`
- `/api/v1/sources`
- `/api/v1/sso`
- `/api/v1/statistics`
- `/api/v1/tags`

The full generated report is:

- `outputs/api_contracts/FINK_LSST_API_CONTRACT_REPORT.md`

## Schema Request Shape

Bare `GET /api/v1/schema` returns an HTML/text documentation response, not JSON.

The locked JSON request shape is:

```text
GET /api/v1/schema?endpoint=/api/v1/<endpoint>&output-format=json
```

or:

```json
{
  "endpoint": "/api/v1/<endpoint>",
  "output-format": "json"
}
```

Using `endpoint=sources` without `/api/v1/` returned 404. Using `endpoint=/api/v1/sources` returned JSON.

Schema fixtures saved:

- `data/fixtures/schema/fink_lsst_schema_sources.json`
- `data/fixtures/schema/fink_lsst_schema_objects.json`
- `data/fixtures/schema/fink_lsst_schema_fp.json`
- `data/fixtures/schema/fink_lsst_schema_conesearch.json`
- `data/fixtures/schema/fink_lsst_schema_tags.json`
- `data/fixtures/schema/fink_lsst_schema_statistics.json`

Schema probe provenance is saved in:

- `outputs/api_contracts/schema_probe_results.json`

## Confirmed Public JSON Endpoints

The following tiny public/no-login probes returned JSON:

- `GET /api/v1/tags`
- `POST /api/v1/tags` with a tag, small `n`, and narrow columns for ID discovery
- `POST /api/v1/statistics` with `{"date": "2026", "output-format": "json"}`
- `GET /api/v1/blocks`
- `POST /api/v1/objects` with a real `diaObjectId`
- `POST /api/v1/sources` with a real `diaObjectId`
- `POST /api/v1/fp` with a real `diaObjectId`

Sample fixtures saved:

- `data/fixtures/samples/fink_lsst_tags_sample.json`
- `data/fixtures/samples/fink_lsst_statistics_2026_sample.json`
- `data/fixtures/samples/fink_lsst_blocks_sample.json`

Endpoint probe provenance is saved in:

- `outputs/api_contracts/endpoint_probe_results.json`

## Deferred Or ID-Gated Endpoints

These require real IDs or safe inputs before general use:

- `/api/v1/sources`: tiny retrieval worked with one real `diaObjectId`; broader use remains ID-gated.
- `/api/v1/objects`: tiny retrieval worked with one real `diaObjectId`; broader use remains ID-gated.
- `/api/v1/fp`: tiny retrieval worked with one real `diaObjectId`; broader use remains ID-gated.
- `/api/v1/conesearch`: requires documented safe coordinates/radius before probing.
- `/api/v1/resolver`: requires harmless documented input before probing.
- `/api/v1/sso`: requires a valid SSO identifier.
- `/api/v1/skymap`: requires a valid skymap payload.
- `/api/v1/cutouts`: deferred because it may fetch image/cutout data.

## Bounded Extraction Capability Results

Checkpoint 2 analyzed the Swagger-derived contract for bounded public REST extraction and saved the generated capability table to:

- `outputs/bounded_extraction/endpoint_capabilities.json`
- `outputs/bounded_extraction/endpoint_capabilities.md`

Current findings:

- `/api/v1/tags` supports bounded tag/date-window sampling through parameters such as `tag`, `n`, `startdate`, and `stopdate`.
- `/api/v1/objects`, `/api/v1/sources`, and `/api/v1/fp` support known-ID detail retrieval with `diaObjectId`.
- `/api/v1/statistics` provides broad count context, but the current statistics fixture is not a matching filtered denominator for the bounded tag sample.
- No all-pages pagination, cursor, or continuation strategy has been proven for complete retrieval of all matching tag rows.
- The latest bounded tag query hit the configured row limit, so public REST completeness remains unresolved.

These results support bounded public REST samples and known-ID follow-up. They do not establish complete full-night all-alert extraction.

## Full-Night Completeness Diagnosis

Checkpoint 3 writes a stricter completeness-oriented contract diagnosis to:

- `outputs/full_night_feasibility/capability_diagnosis.json`
- `outputs/full_night_feasibility/capability_diagnosis.md`

This report distinguishes all-alert enumeration from tag-specific enumeration and known-ID enrichment. Full-night completeness requires a safe all-alert enumeration path, partition statuses that are all exhausted or explicitly complete, and a directly comparable denominator or equivalent all-pages completion evidence.

Row-cap support is necessary for safe public REST tests, but a cap hit invalidates completeness for that partition unless pagination/continuation or further subdivision exhausts it.

## Data Transfer Readiness

Checkpoint 4 does not change the REST contract. It adds a separate dry-run Data Transfer readiness path for complete nightly extraction planning. REST contract outputs remain useful for candidate lookup and post-delivery enrichment, while Data Transfer request drafts and delivery inspections are saved under `outputs/data_transfer/`.

## Deferred Services

This phase still avoids:

- Data Transfer
- Kafka
- Livestream
- Spark
- Login-gated services
- Cutout/FITS/image downloads
- Large historical ingestion

## Difference From ANTARES

The earlier ANTARES project had broker-specific search/query and tiling constraints around locus-centered access. This checkpoint is different: it locks the Fink LSST REST contract and schema request shape before any ingestion design. No sky tiling, nightly backfill, or locus-style architecture is being copied into Fink.

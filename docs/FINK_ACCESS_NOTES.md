# Fink Access Notes

## Current Public Access Strategy

This repository targets the LSST-specific public Fink REST API:

- LSST primary API: `https://api.lsst.fink-portal.org`
- ZTF reference API: `https://api.ztf.fink-portal.org`
- Generic/ambiguous API host: `https://api.fink-portal.org`

The generic host is not used as the primary LSST target. If old config points to `https://api.fink-portal.org`, the config loader warns and rewrites the LSST primary URL to `https://api.lsst.fink-portal.org`.

Relevant public documentation:

- https://fink-broker.readthedocs.io/en/latest/broker/services_summary/
- https://fink-broker.readthedocs.io/en/latest/services/search/definitions/

The public REST API is the right fit for this phase because it supports small programmatic queries without login. This repository uses only tiny endpoint probes and saved fixtures.

## Services Avoided In This Phase

This phase avoids:

- Data Transfer
- Kafka
- Livestream
- Spark
- Login-gated services
- Large downloads
- Cutout/FITS/image downloads

Fink documentation distinguishes the REST API from Livestream and Data Transfer. Livestream is for real-time alert data selected by filters and requires login. Data Transfer is for bulk alert access, complex queries, and Spark/Kafka workflows, and also requires login.

## Safe Public Endpoints For Reconnaissance

The Checkpoint 0.6 scripts try only metadata and tiny public/no-login endpoints:

- `GET https://api.lsst.fink-portal.org/swagger.json`
- `GET https://api.lsst.fink-portal.org/api/v1/schema?endpoint=/api/v1/<endpoint>&output-format=json`
- `GET https://api.lsst.fink-portal.org/api/v1/tags`
- `POST https://api.lsst.fink-portal.org/api/v1/statistics`
- `GET https://api.lsst.fink-portal.org/api/v1/blocks`

Successful responses are saved as:

- endpoint-specific schema fixtures under `data/fixtures/schema/`
- `data/fixtures/fink_lsst_tags.json`
- `data/fixtures/fink_lsst_statistics_sample.json`

The manifest is saved as `outputs/smoke_test/manifest.json`.

The client still contains optional wrappers for other small public endpoints, but the Checkpoint 0.6 scripts deliberately avoid object/source/forced-photometry data retrieval unless a valid ID is available, and they do not fetch cutouts.

## Field Vocabulary Notes

Fink field names may use prefixes:

- `i:` original alert/survey fields
- `d:` Fink science-module or database-added values
- `v:` runtime-generated values
- `b:` cutout/image payloads
- `key:` database key fields

For LSST/Rubin work, we should not assume that ZTF-era names are the final vocabulary. This repository therefore maps fields into internal concepts such as object rows, source/alert rows, forced-photometry rows, classification/tag fields, and context/crossmatch fields.

## Local Endpoint Results

Latest local contract result from `scripts/discover_fink_lsst_contracts.py` on June 23, 2026:

- `GET https://api.lsst.fink-portal.org/swagger.json`: succeeded with HTTP 200 and saved `data/fixtures/fink_lsst_swagger.json`.
- `GET https://api.lsst.fink-portal.org/api/v1/schema`: reached the server with HTTP 200, but returned `text/html` rather than JSON. Response preview: `Retrieve the data schema for a given endpoint for Fink/Rubin API`.
- `GET /api/v1/schema?endpoint=/api/v1/<endpoint>&output-format=json`: succeeded for `sources`, `objects`, `fp`, `conesearch`, `tags`, and `statistics`, saving endpoint-specific schema fixtures.
- `GET https://api.lsst.fink-portal.org/api/v1/tags`: succeeded with HTTP 200 and saved `data/fixtures/fink_lsst_tags.json`.
- `POST https://api.lsst.fink-portal.org/api/v1/tags` with `tag=in_tns`, tiny `n`, and narrow columns: succeeded and provided real `diaObjectId`/`diaSourceId` values.
- `POST https://api.lsst.fink-portal.org/api/v1/statistics` with `{"date": "2026", "output-format": "json"}`: succeeded with HTTP 200 and saved `data/fixtures/fink_lsst_statistics_sample.json`.
- `GET https://api.lsst.fink-portal.org/api/v1/blocks`: succeeded with HTTP 200 and saved `data/fixtures/samples/fink_lsst_blocks_sample.json`.
- `POST /api/v1/objects`, `POST /api/v1/sources`, and `POST /api/v1/fp` succeeded for one discovered real `diaObjectId`, saving tiny raw samples under `data/raw/minimal_samples/`.
- `POST /api/v1/tags` with `tag=in_tns`, `startdate=2026-01-01`, `stopdate=2026-01-02`, `n=50`, and narrow columns succeeded for bounded extraction.
- Known-ID detail retrieval through `POST /api/v1/objects`, `POST /api/v1/sources`, and `POST /api/v1/fp` succeeded for 10 capped objects discovered from the bounded tag sample.
- The bounded tag response hit the configured row limit, so additional matching rows may exist.
- Checkpoint 3 uses `2026-02-25` to `2026-02-26` UTC as the default first post-public-alert window and rejects earlier target dates unless explicitly overridden.

Endpoint success should not be claimed unless the manifest shows `ok: true`. Endpoint failures are recorded with URL, method, status code when available, elapsed time, and an error summary.

The initial sandboxed probe failed with DNS resolution errors. The successful tags/statistics captures required network-enabled execution.

## Known Unknowns

- Whether public REST can support complete full-night extraction, or only bounded lookup/sample workflows.
- Whether an all-pages pagination, cursor, or continuation mechanism is available for all rows matching a tag/date window.
- Whether LSST DIA object/source identifiers will be exposed directly, aliased, or translated into Fink-specific names.
- Which classification fields will be stream-native, Fink-added, or runtime-generated.
- What public/no-login constraints will apply once LSST-scale data is live.
- Whether forced photometry can be bounded by row count directly, or only indirectly through capped object-detail retrieval.
- Whether public REST exposes a safe all-alert enumeration path for a complete UTC night, or whether full-night work should move to Data Transfer.

## Hybrid Access Direction

REST mode remains active for bounded/candidate extraction, known-ID enrichment, diagnostics, and dashboards. Data Transfer mode is now the planned route for complete nightly alert census and historical bulk extraction once registration/access and delivered files are available.

No credentials, tokens, or `.env` files should be stored in this repository.

Checkpoint 5 adds a tiny smoke-delivery workflow. The first Data Transfer operation should be manually submitted through the portal using the smallest supported scope, and delivered files should be placed under `data/raw/data_transfer/smoke_delivery/`.

## Smoke-Test Failure Interpretation

Endpoint failures in `00_fink_access_smoke_test.ipynb` or `scripts/probe_fink_lsst_api.py` should be treated as reconnaissance results, not test failures. Possible causes include local DNS/connectivity, API timeout, endpoint changes, method mismatch, schema migration, rate limiting, or endpoints that are not relevant to LSST-era public access.

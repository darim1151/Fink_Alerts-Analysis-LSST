# CLAUDE.md

Durable instructions for executors working in this repository. Project history lives in docs/ and Git, not here.

## Tests

```bash
.venv/bin/python -m pytest                       # full offline suite (Python 3.9 venv)
.venv/bin/python -m pytest tests/test_acquisition_*.py tests/test_date_range_scope.py
```

Tests must stay offline. Never delete or weaken a test to make code pass.

## Architecture landmarks

- `src/fink_lsst/data_root.py`: data root and path confinement; every production path goes through `confine`/`confine_tree`.
- `src/fink_lsst/bulk_transfer/`: manifests (`run_manifest.py`), scopes, topic registry, `finkctl transfer` command generation, raw audit, ingestion, validation.
- `src/fink_lsst/acquisition/`: guarded range acquisition (profile, planner, portal compiler, state machine and registry, portal adapter, Kafka/Arnor handoff, evidence). See `docs/RANGE_ACQUISITION_ORCHESTRATOR.md`.
- `configs/runs/`, `configs/data_transfer_topics.yaml`, `configs/portal_requests/`, `configs/delivery_evidence/`, `configs/acquisitions/`: committed, non-secret provenance.
- Arnor layout and transfer conventions: `docs/ARNOR_PRODUCTION_FOUNDATION.md`.

## Scientific invariants

- Repository windows are half-open UTC dates `[start, stop)`; the Fink portal is inclusive (`stop - 1 day`).
- The production profile is `lsst_light_static_all_alerts_v1` (LSST, Light static packet, no filters/blocks/catalogue/SQL). New selections need a new profile version.
- Light static is not non-SSO.
- Kafka lag 0 is not completeness; a readable Parquet set is not science readiness. A delivery is validated only when expected topic messages = terminal committed = local readable rows, lag 0.
- Raw acquired data is immutable. Historical manifests (`full_week*`, G3A) and Full Packet artifacts are not rewritten.

## Git workflow

Work on the gate branch named by the gate; never commit to `main`; push the branch; no PR or merge unless asked.

## Actions that need explicit approval

- Submitting a Fink Data Transfer job (live submission is disabled in code until a gate enables it).
- Running `finkctl transfer`, consuming Kafka, or any write on Arnor.
- Touching ANTARES, credentials, `~/.finkclient`, or running `finkctl auth show`.
- Printing or committing Kafka server values, cookies, tokens or browser state.

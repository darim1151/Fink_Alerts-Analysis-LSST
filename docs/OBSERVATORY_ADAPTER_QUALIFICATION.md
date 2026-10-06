# V3-UI-U1.F qualification evidence packet

Frozen source: `85252bd801eb1c1fbe3aa26da93ad0224863c738`.
Branch: `codex/v3-ui-u1-fink-observatory-adapter`.
Implementation/generator commit:
`19113e1675a35b3825a483bc04f6fa82e0d61bce`
(`Add qualified Fink Observatory native domain adapter`).
The final handoff commit is reported by Git in the adjudication response rather
than embedded in its own contents. No merge or PR was created.

## Test results

| Check | Result |
|---|---|
| New Observatory adapter tests | **42 passed** |
| Relevant analytics tests | **32 passed** |
| Characterization tests | **29 passed** |
| Combined adapter/analytics/characterization | **103 passed** |
| Committed-evidence tests | **10 passed, 1 pre-existing failure** |
| Complete offline suite, final scientific code | **703 passed, 1 pre-existing failure**, 207 warnings, 33.77 seconds |
| Exact frozen-baseline reproduction of the failing test | Same failure reproduced independently |
| New-file Ruff static checks (`E4,E7,E9,F,I`, isolated config, Python 3.9) | PASS |
| Ruff formatting check on all nine new Python files | PASS |
| Python compileall on adapter, exporter and tests | PASS |
| Git diff whitespace checks | PASS |
| Two independent metadata-fixture exports from clean implementation commit | Byte-identical; payload and whole-file checksums verified |

The new tests exercise the real native characterization and analytical builders
over a fully replayable synthetic delivery chain. Only Git identity/cleanliness
is stubbed in that fixture. Acquisition evidence replay, scans, hashes, cohort
admission, read-only catalog reopening and raw-stat checks execute normally.
All authority fixtures are temporary and synthetic.

Coverage includes qualification transitions; uncharacterized cohorts rejected;
malformed cohort/registry/build evidence; incomplete catalogs even with a
matching catalog digest; changed raw/characterization/catalog evidence;
concurrent raw changes; dirty-code refusal; DIA/SSO/ambiguous preservation;
explicitly derived DIA grouping; evolving source-time broker snapshots;
unavailable/schema-only/all-null features; map-element coverage; TAI provenance;
qualified UTC leap-second boundaries; hierarchical sky cells and invalid
positions; deterministic bytes; full lineage references; exact IDs above 2^53;
finite JSON/nonfinite accounting; bounded/disabled samples; no input mutation;
new-output confinement/no reuse; and the closed shared-contract boundary.

Local environment: Python 3.9.6, DuckDB 1.4.4, PyArrow 21.0.0, Astropy 6.0.1,
NumPy 1.26.4, pyerfa 2.0.1.5 and pytest 8.4.2. DuckDB was already available in
`/private/tmp/fink-g4b-deps`; no scientific dependency change was required.
Ruff 0.16.10 was installed only in `/private/tmp/fink-u1-lint` for static checks.
Warnings concern existing LibreSSL/urllib3, inaccessible optional Astropy cache,
and Matplotlib/pyparsing deprecations. Astropy/ERFA time conversion disables
network auto-download. No live infrastructure was required.

Reproduce the scientific checks:

```sh
PYTHONPATH=src:/private/tmp/fink-g4b-deps \
PYTHONPYCACHEPREFIX=/private/tmp/fink-observatory-pycache \
.venv/bin/python -m pytest \
  tests/test_observatory_adapter.py tests/test_analytics.py \
  tests/test_characterization.py -q --disable-warnings

PYTHONPATH=src:/private/tmp/fink-g4b-deps \
PYTHONPYCACHEPREFIX=/private/tmp/fink-observatory-pycache \
.venv/bin/python -m pytest -q --disable-warnings

/private/tmp/fink-u1-lint/bin/ruff check --isolated --no-cache \
  --target-version py39 --select E4,E7,E9,F,I \
  src/fink_lsst/observatory scripts/export_observatory_bundle.py \
  tests/test_observatory_adapter.py
```

The disposable dependency paths describe this local qualification environment;
other hosts should use the repository's existing `analytics` environment/extra.

## Existing baseline failure, preserved

`tests/test_acquisition_committed_evidence.py:111`,
`test_committed_acquisition_files_carry_no_credentials`, searches whole file
text for the literal substring `9092`. It matches inside a hexadecimal inventory
SHA256 in the committed Month-3
`configs/acquisitions/acq_lsst_ls_v1_2026-04-25_to_2026-05-25_1ccf601aa29f/evidence/inventory_summary_1.json`.
The structured credential check passes first; the lexical substring check then
fails on the digest fragment `3f0523b4f49092619238cf`.

The same exact test fails on a read-only archive of frozen
`85252bd801eb1c1fbe3aa26da93ad0224863c738` in
`/private/tmp/fink-u1-baseline-85252bd`. Running that single test avoids Git
ignore tests that require a checkout rather than an archive. No assertion was
removed, skipped or weakened. No committed acquisition/evidence file was edited.
Accordingly the complete suite is reported with a known baseline failure, not
as an unconditional pass. Control may adjudicate that separate false positive.

## Metadata fixture and checksums

Artifact: `data/fixtures/observatory/fink_observatory_metadata_v1.json`.
Checksum record:
`data/fixtures/observatory/fink_observatory_metadata_v1.sha256.json`.

- Whole-file SHA256:
  `721cb826a1b807807f9c820f25c2dfa34919779019bdcfb54d4f907405011058`.
- Native payload SHA256:
  `384d0dbc1a8e006154381589d3c227c369b9b7623f7bad27e851e8ae96394081`.
- Generator code SHA:
  `19113e1675a35b3825a483bc04f6fa82e0d61bce`.
- Size: 45,442 bytes; four acquisition evidence entries; 29 capability definitions.
- Product kind: **METADATA / CONTRACT FIXTURE**.
- Authority: **DERIVED / NON-AUTHORITATIVE**.
- Shared contract binding: **UNBOUND**.

The fixture contains real committed metadata references and transport facts.
Every science capability is UNAVAILABLE. Entities, broker snapshots, permitted
laboratory dimensions, observation bins and sky cells are empty; unmeasured sky
input/exclusion counts are null. No Month-1 scientific source rows or model/
feature values are fabricated. Two separate processes exported this fixture from
the clean implementation commit to temporary files before either artifact was
added to the repository; their bytes matched exactly.

To reproduce exact fixture bytes, use the recorded generator commit in a clean
checkout. A later code SHA produces a different provenance digest by design,
even when the scientific implementation and committed inputs have not changed.
The subsequent handoff commit only adds this fixture, its checksum record and
this qualification packet.

## Files and rendezvous

New implementation files:

- `src/fink_lsst/observatory/__init__.py`
- `src/fink_lsst/observatory/model.py`
- `src/fink_lsst/observatory/qualification.py`
- `src/fink_lsst/observatory/capabilities.py`
- `src/fink_lsst/observatory/products.py`
- `src/fink_lsst/observatory/adapter.py`
- `src/fink_lsst/observatory/serialization.py`
- `scripts/export_observatory_bundle.py`
- `tests/test_observatory_adapter.py`

New handoff/documentation artifacts are this packet,
`docs/OBSERVATORY_ADAPTER.md`, and the two metadata-fixture files above.
Existing analytics, characterization, acquisition orchestration, scientific data,
validated deliveries, manifests and authority are unchanged.

The exact shared-schema mapping proposal and unresolved owner fields are in
`docs/OBSERVATORY_ADAPTER.md` and
`serialization.NATIVE_TO_COMMON_PROPOSAL`. Bundle V1 serialization remains
closed pending verified owner schema identity/version/hash. Expected alignment
is a serializer-only binding and compatibility validation; an owner requirement
for HEALPix would additionally require the isolated spatial backend.

## Independent main movement observed at handoff

After implementation and the first handoff push, the local origin reference had
advanced independently to `5f26a2026022e5ae7f708d4625aad6190ea3215f`.
The gate remains based on frozen
`85252bd801eb1c1fbe3aa26da93ad0224863c738`; it was not rebased or merged.
The fixture and test results above retain that exact scientific source baseline.

The three additional main commits are:

- `394398bc90646f19745e9d75074a3b83df3e6457`: Validate Month-4 Fink delivery.
- `e29021147c97ee581d9ba9b25ed6b4c0f3319a9f`: Prepare final Fink acquisition through July 13.
- `5f26a2026022e5ae7f708d4625aad6190ea3215f`: Record final Fink submission and producer completion.

Their complete diff contains only acquisition-state/evidence additions under
`configs/acquisitions`. Analytics, characterization, cohorts and their
scientific contract documents have no changes. A read-only archive of that
exact observed SHA in `/private/tmp/fink-u1-observed-main-5f26a20` replayed all
five acquisition records successfully through the existing registry verifier.

| Latest observed window, half-open UTC | Transport | Validated delivered rows | Characterization | Analytical admission |
|---|---|---:|---|---|
| 2026-02-25 → 2026-03-25 | DELIVERY_VALIDATED | 1,658,642 | Accepted pinned G4A | Accepted historical G4B; external catalog not reopened here |
| 2026-03-25 → 2026-04-25 | DELIVERY_VALIDATED | 76,133 | NOT_ESTABLISHED | NOT_ADMITTED |
| 2026-04-25 → 2026-05-25 | DELIVERY_VALIDATED | 266,496 | NOT_ESTABLISHED | NOT_ADMITTED |
| 2026-05-25 → 2026-06-25 | DELIVERY_VALIDATED | 923,667 | NOT_ESTABLISHED | NOT_ADMITTED |
| 2026-06-25 → 2026-07-14 | PRODUCER_COMPLETE | — | NOT_ESTABLISHED | NOT_ADMITTED |

The final acquisition identity is
`acq_lsst_ls_v1_2026-06-25_to_2026-07-14_1ad0e9dadca7`. Month 4 retains identity
`acq_lsst_ls_v1_2026-05-25_to_2026-06-25_ecc26d8ca904`; its newly validated
receipt was not imported into the frozen adapter branch. These transport counts
do not imply astrophysical evolution. A final documentation-only commit records
this observation; the implementation, metadata fixture and qualification remain
unchanged.

AWAITING_CONTROL_V3_UI_U1_FINK_ADAPTER_ADJUDICATION

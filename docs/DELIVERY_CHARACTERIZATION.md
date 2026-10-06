# Validated-delivery characterization

Run only from a clean checkout of a pushed commit, in an environment with the
`characterization` extra (Astropy plus the existing PyArrow/NumPy stack):

```sh
PYTHONPATH=src python scripts/characterize_delivery.py \
  --acquisition-id <accepted-acquisition-id> \
  --data-root /astro/store/shire/FINK \
  --run-id <new-run-id>
```

The registry is replayed and its existing receipt/evidence verification reused.
Only DELIVERY_VALIDATED acquisitions are eligible. Identity, topic and raw path
come from that record. Data-root and tree confinement use existing project code.
No acquisition transitions, Kafka operations, processed copies or databases occur.

One dataset scan projects source identifiers, TAI times, photometry, coordinates,
and scientific broker structs/maps. Coverage of each projected scalar/struct/map
is exact, with separate row/element denominators and nonfinite counts. Map
entries in `lc_features` are feature summaries rather than measurement history.
Operational timestamps/versions are inventoried but are not projected. A second,
filtered scan takes at most 12 deterministic repeated DIA IDs, spaced across
multiplicity, and retains up to first/last two snapshots per ID. No row exports
or per-object tables are written. This sample addresses structure and changing
snapshots; it does not validate feature estimators or establish population trends.

The Month-1 flattened mapping is explicit and checked against the actual schema;
critical absent paths and unexpected list/history structures fail closed rather
than substituting broker ingest times or guessing scientific semantics. New
packet schemas require reviewed mappings. Struct children propagate parent
nulls; map/list child coverage uses elements. Schema hashes reuse raw-audit
machinery on the initial file; the accepted full metadata audit is retained.
Full projected reading catches incompatible schemas without repeating the
16,591-file validation campaign. Names, size, mtime and ctime are compared before
and after; raw payloads are not rehashed. No raw write API is used.

Source observation times are converted with Astropy/ERFA from MJD TAI to UTC,
with network auto-download disabled. This replaces no existing approximation
helpers: this gate needs the exact scale treatment, including leap seconds.
Repository source-schema documentation declares the field's TAI scale.

DIA selection requires `pred.is_sso == false` and a positive `diaObjectId`.
SSO selection requires `pred.is_sso == true` and a null/zero `diaObjectId`.
Other combinations are ambiguous; negative IDs and both disagreement directions
are reported. This is product representation, not independently validated
physical classification. SSO identity/orbits are never inferred from DIA IDs.
Duplicate excess rows = non-null rows minus distinct IDs; rows in duplicate
groups and duplicate ID groups are separately recorded.

Outputs are compact DATA PLANE artifacts under `outputs/<run-id>` and
`manifests/<run-id>` of the external data root, never in Git. Both directories
must be absent before reservation. A failed invocation preserves directories and
finalizes its manifest as FAILED; it must use a new ID after a justified fix.
Only the current invocation's lifecycle manifest moves from RUNNING to its final
status. The manifest records commit, environment, command, times and artifact
SHA256s. Readability, delivered dates and available fields do not establish Rubin
completeness, complete histories, or scientific validity of broker features.

Readiness addresses the detection/source analytical layer plus evolving broker
snapshots. Full-history, upper-limit, forced-photometry and SSO-identity work
requires a separately adjudicated product/enrichment choice. Control decides
merging and subsequent acquisition. The implementation never acquires more data.

## Offline qualification

The G4A focused suite has 29 passing synthetic tests. The relevant existing
handoff, data-root, date-range and schema suites have 54 passing tests. The full
offline suite has 627 passes and three pre-existing failures in
`tests/test_acquisition_committed_evidence.py`: the assertions expect no live
submission, PORTAL_VERIFIED Month-1 state, and an earlier qualification revision.
All three reproduce on an untouched archive of canonical
`aeda9315171fc26ca5a6d58a94d342cf2439869d`, whose accepted Month-1 state is
DELIVERY_VALIDATED. Neither those tests nor the accepted acquisition evidence
was modified for G4A. These stale assertions are a qualification limitation,
not a new characterization regression.

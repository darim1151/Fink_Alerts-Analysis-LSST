# Fink Observatory scientific adapter — V3-UI-U1.F

This adapter produces a **Fink-native derived domain model**, independently of
the common Observatory wire schema. The shared Bundle V1 remains **UNBOUND**.
All artifacts identify themselves as **DERIVED / NON-AUTHORITATIVE**. This gate
adds no acquisition, portal, Kafka, frontend, ANTARES or identity-matching work.

## Frozen baseline

Repository: `darim1151/Fink_Alerts-Analysis-LSST`.

Origin was fetched before mutation. `origin/main` was exactly
`85252bd801eb1c1fbe3aa26da93ad0224863c738`, identical to Control's observation.
The initial checkout was clean on `claude/g3b0-r2-remediation` at
`8af43a2e8166b38f18d5c3d1432feaeaf82fd671`. The new branch
`codex/v3-ui-u1-fink-observatory-adapter` starts from the frozen origin SHA.
Recent main history records Month-4 producer completion, Month-4 preparation,
Month-3 validation/submission, and Month-2 validation/submission. No moving main
was followed during implementation. No merge or PR is part of this gate.

## Scientific state at the frozen source

These rows describe committed acquisition evidence and independently reviewed
archived qualification metadata. Transport counts are delivery facts, not an
astrophysical time series.

| Acquisition window, half-open UTC | Acquisition suffix | Transport | Validated delivered rows | Characterization | Analytical admission |
|---|---|---|---:|---|---|
| 2026-02-25 → 2026-03-25 | `b1c7b482b56b` | DELIVERY_VALIDATED | 1,658,642 | Accepted G4A, pinned summary | Accepted G4B Month-1 build; external catalog |
| 2026-03-25 → 2026-04-25 | `0c852cd059fa` | DELIVERY_VALIDATED | 76,133 | NOT_ESTABLISHED in committed evidence | NOT_ADMITTED in committed evidence |
| 2026-04-25 → 2026-05-25 | `1ccf601aa29f` | DELIVERY_VALIDATED | 266,496 | NOT_ESTABLISHED in committed evidence | NOT_ADMITTED in committed evidence |
| 2026-05-25 → 2026-06-25 | `ecc26d8ca904` | PRODUCER_COMPLETE | — | NOT_ESTABLISHED | NOT_ADMITTED |

Full acquisition IDs are `acq_lsst_ls_v1_<start>_to_<stop>_<suffix>`.
The registry replay verifies evidence references and sealed log entries. Months
1–3 have equal expected topic messages, terminal committed messages and locally
readable rows, with terminal lag zero. Month 4 has terminal producer markers;
no validated local-delivery receipt is committed at this baseline. Producer
completion alone does not prove acquisition or transport validation.

`configs/analysis_cohorts/month1.json` pins:

- characterization `g4a-month1-20260225_20260325-v1`;
- summary SHA256 `47a7adf35689cc409a90f6de739a05d1b510dde7f416dbffb95073bd67c9e540`;
- characterization code `d34f6c244a2cc0c6ec6840d63e274b7c79fd7424`.

The read-only archived copy in `/private/tmp/fink-g4a-evidence` matches that
summary digest and records `PASS_WITH_LIMITATIONS`. The archived G4B manifest
in `/private/tmp/fink-g4b-evidence-v2` records `g4b-analytics-month1-v2`,
`PASS_WITH_LIMITATIONS`, code `e7883af5e3de7e56d5326495f2ccbf37c005b0ba`, the
same exact cohort, and completion at `2026-10-06T05:34:54.308337+00:00`.
These archived files are supporting local evidence, not new committed science
artifacts or a replacement for reopening the qualified external catalog.

The metadata fixture deliberately reports Month 1 as `REFERENCE_PINNED` and
`COHORT_DECLARED_NOT_VERIFIED`: its generation reads committed metadata only.
This states the fixture's verification scope, not a reversal of historical G4A/
G4B qualification. Real Observatory science requires verifying the actual
characterization artifacts, completed build manifest, exact catalog and raw
stat bindings. No external catalog was reopened in this gate.

## Domain and qualification model

`src/fink_lsst/observatory/model.py` defines the basis, capability manifest,
feature definitions, temporal/spatial products, native entities, source-time
broker snapshots, and provenance records. Qualification exposes separately:

- `transport_state`, from replayed native acquisition state;
- `characterization_state`: NOT_ESTABLISHED / REFERENCE_PINNED / CHARACTERIZED;
- `analytical_admission`: NOT_ADMITTED / COHORT_DECLARED_NOT_VERIFIED / ANALYTICALLY_ADMITTED;
- `capability_state`: AVAILABLE / PARTIALLY_QUALIFIED / UNAVAILABLE;
- explanatory reason and evidence identities.

No unadmitted acquisition can have an available science capability.
The Observatory conservatively requires pinned, verified characterization for
every member of a completed analytical cohort, even though the underlying
analytics builder can independently qualify an uncharacterized delivery.
The adapter never creates or changes an analytical admission itself.

`adapter.build_from_catalog` requires clean committed code and a tracked cohort;
replays acquisition evidence; validates cohort windows and identities; verifies
all pinned build artifact hashes; reuses `analytics.load_cohort` to verify raw,
schema and characterization bindings; reopens the catalog read-only through
`analytics.open_catalog`; checks its PASS marker, exact contract, build identity
and provenance; then verifies raw stats and artifact/evidence hashes again after
derivation. Failed or partial catalogs, malformed pins, changed inputs and
unqualified cohorts fail closed before a product is returned.

## Capabilities and Feature/Population Laboratory

`capabilities.py` returns a machine-readable registry. Each entry records name,
Fink namespace, native entity level, dtype, unit where established, time meaning,
definition, provenance references, exact coverage or explicit NOT_MEASURED,
qualification, reason/evidence, and an optional laboratory role.

Permitted dimensions include delivered source identity/multiplicity, population
representation, native observation time, reported coordinates, safe band labels,
delivered PSF difference flux/error, derived DIA grouping multiplicity, observed
bands and delivery-window temporal baseline. Native PSF flux/error units are nJy
from the pinned source-schema fixture. Their scientific calibration is not
independently established; photometry capabilities remain PARTIALLY_QUALIFIED.
Coverage predicates retain finite/range checks and positive flux-error checks.

Broker fields are inventoried per acquisition/schema. Model fields can be
discovered only as measured source-time snapshot outputs, with module/field and
payload provenance, usable coverage and explicit calibration/physical-validity
limitations. Actual module payloads retain any embedded model/version metadata;
schema documentation versions are never substituted for per-row model identity.
Fields without measured usable coverage remain UNAVAILABLE. Row and map-element
coverage denominators remain distinct.

`lc_features` can be inspected as a per-band snapshot map. Its child estimators
remain UNAVAILABLE to the Laboratory: this characterization did not establish
their definitions, history windows or physical/estimator validity. SDSS-like
colors, variability rms/chi-square/skewness/amplitude, calibrated probabilities,
astrophysical truth labels, complete histories, forced photometry, canonical
DiaObjects, SSO identities/orbits and survey footprint remain unavailable.

## Temporal, spatial and entity products

Observation times remain float64 MJD TAI from `midpointMjdTai`. Native
one-day TAI bins retain acquisition and DIA/SSO/AMBIGUOUS counts. They describe
delivered observations, not observing nights. Optional UTC date bins reuse the
qualified catalog boundaries and verify each boundary with
`analytics.contract.utc_bounds` (Astropy/ERFA, network auto-download disabled),
including leap seconds. Empty validated-delivery dates retain
`zero_rows_in_validated_Fink_delivery`. Requested delivery windows, broker
transport states and admitted-cohort coverage are distinct fields.

The compact sky aggregate uses `fink_equal_area_ra_sin_dec_v1`. At order `k`,
longitude has `4*2**k` bins and sin(declination) has `2*2**k` bins. Cell IDs are
`latitude_index*longitude_bin_count + longitude_index`; parent indices divide
both indices by two. Half-open RA [0,360), declination [-90,90] and the north-pole
clamp are explicit. Orders 0–6 are supported; default 3. Equal solid-angle cells,
bounds, parent IDs, excluded-position counts, population and acquisition are
deterministic. This is explicitly **not HEALPix**. A future backend can replace
the cell scheme/order/cells implementation while preserving the sky product's
abstract structure. No heavyweight dependency was added. Reported coordinate
frame/epoch are not independently certified. Density never claims footprint.

Inspector records are bounded by population (default 12, maximum 100), ordered
by native source ID, with separate derived DIA grouping records. DIA sources,
SSO sources, ambiguous sources, broker snapshots and derived DiaObjects remain
distinct. Raw diaObjectId values, including zero/null and disagreements, remain
visible. No SSO or cross-broker object key is invented. IDs serialize as decimal
strings to preserve int64 precision in JavaScript. Sampling is deterministic;
population representativeness is NOT_ESTABLISHED.

Broker snapshot JSON retains every native projected module, including model
metadata present in those modules. Nonfinite numeric values become null with
exact field paths and original module-text SHA256s. Strict native JSON rejects
unaccounted NaN/Infinity. Source positions and photometry similarly retain an
explicit nonfinite policy. No classifier is promoted to astrophysical truth or
timeless DiaObject context.

## Provenance, determinism and output safety

Every product links to provenance records identifying acquisition, cohort and
cohort digest, contract ID/digest, characterization identity/digests/code,
input catalog SHA256 and build/code identity, adapter code SHA, receipt/log/
inventory hashes, raw stat fingerprint and schema groups where applicable.
Absent build/characterization identities in metadata fixtures remain null or
NOT_ESTABLISHED. Definitions link the source-schema fixture digest. Ordering is
canonical; artifact bytes are finite sorted-key UTF-8 JSON with one final newline.
The envelope contains the payload SHA256; an export has a separate full-file
checksum. Runtime timestamps and durations do not enter scientific products.

The scientific export CLI writes exclusively to a **new** confined
`outputs/<export-run-id>` directory. It rejects an existing run identity in
outputs, manifests or processed storage. Raw, validated receipts, catalogs,
characterization outputs and authority state have no write path. Tests use
temporary fake authority/transport fixtures only; no production credentials or
live infrastructure are accessed.

```sh
PYTHONPATH=src python scripts/export_observatory_bundle.py metadata-fixture

PYTHONPATH=src python scripts/export_observatory_bundle.py science \
  --data-root /astro/store/shire/FINK \
  --catalog /astro/store/shire/FINK/data/processed/g4b-analytics-month1-v2/analytics.duckdb \
  --cohort configs/analysis_cohorts/month1.json \
  --export-run-id observatory-month1-v1 --utc-dates
```

Use the existing `analytics` environment/extra (DuckDB 1.4.4, Astropy, PyArrow,
NumPy). The science command above is a documented future invocation; it was
not run against live external data in this gate.

## Observatory contract rendezvous

The exact boundary is `src/fink_lsst/observatory/serialization.py`.
`serialize_observatory_bundle_v1` deliberately raises `SharedContractUnbound`.
`native_artifact` emits **FINK NATIVE DOMAIN HANDOFF / PROVISIONAL**, with
`shared_contract_binding=UNBOUND`. It is not an alternate Bundle V1 schema.

The exact **mapping proposal**, also machine-readable as
`NATIVE_TO_COMMON_PROPOSAL`, assigns all native top-level structures as follows:

| Shared conceptual section | Native input paths, relative to envelope payload |
|---|---|
| manifest | `native_model_version`, `product_kind`, `authority`, `sampling` |
| basis | `basis` (cohort name/digest; acquisition qualification and delivery windows) |
| capabilities | `capabilities.entries` (definitions, coverage, states, reasons/evidence) |
| time | `temporal` (native/UTC observation bins; separate delivery/status/cohort coverage) |
| sky | `sky` (explicit cell scheme/order, cells and exclusion counts) |
| entities | `entities`, `broker_snapshots` (native identities, source-time modules, derived flags) |
| features | `features` (permitted laboratory dimensions with definitions/provenance) |
| provenance | `provenance` (all source/build/evidence lineage) |

The owner must still provide verified schema identity, version and SHA256;
precise section/field names; required/optional/null rules; capability enum
mapping; ID encodings and entity discrimination; snapshot attachment/reference
conventions; time/bin representation; permitted spatial schemes; and provenance/
checksum conventions. Binding and compatibility validation belong to this one
serializer. If the owner mandates HEALPix, only a spatial backend addition is
also necessary; scientific analytics and the native model need no redesign.

## Qualification evidence

All new scientific records in tests are explicitly synthetic. The committed
integration handoff is metadata only, with no fabricated scientific source rows,
time observations, sky cells, model outputs or laboratory feature values.

The gate qualification packet in `docs/OBSERVATORY_ADAPTER_QUALIFICATION.md`
records exact checks, known baseline failure and artifact identity. Existing
scientific fail-closed behavior and committed acquisition evidence are unchanged.

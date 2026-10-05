# Range Acquisition Orchestrator (FINK-G3B.0, hardened in G3B.0-R2)

This note explains how a scientific date window becomes a Fink LSST Data Transfer request, a Kafka topic, an Arnor delivery and validation evidence, and which steps are guarded. Code lives in `src/fink_lsst/acquisition/`.

**Status:** planning, recording, the real-portal dry run and all handoff/validation primitives are implemented. **Live submission is disabled** (`LIVE_SUBMISSION_ENABLED = False` in `cli.py`); enabling it is a reviewed change in a later gate. No Arnor transfer is run by this code.

## Quick start

```bash
# review only: prints the plan, writes nothing
python scripts/fink_lsst_cli.py acquire --start 2026-02-25 --stop 2026-03-25

# register the request: PLANNED -> PORTAL_PREPARED in configs/acquisitions/
python scripts/fink_lsst_cli.py acquire --start 2026-02-25 --stop 2026-03-25 --record

# real portal dry run (local portal environment): upload, verify, download config, stop before Submit
.venv-portal/bin/python scripts/fink_lsst_cli.py acquire --start 2026-02-25 --stop 2026-03-25 --portal-check

python scripts/fink_lsst_cli.py status [--id ACQUISITION_ID]
python scripts/fink_lsst_cli.py handoff --id ACQUISITION_ID   # prints the Arnor transfer plan; runs nothing
```

After `pip install -e .` the same commands are available as `fink-lsst acquire ...`.

## Science profile

There is exactly one production profile, `lsst_light_static_all_alerts_v1` (`profile.py`):

| Field | Value |
|---|---|
| survey | `lsst` |
| packet | `Light static packet` |
| filters / blocks | none |
| catalog_filename / extra_cond | null |

The CLI has no option that changes it. The profile is a frozen dataclass whose content digest is pinned; a modified copy or an in-place edit raises `ProfileIntegrityError`. A different scientific selection needs a new profile name and version.

Light Static is not "non-SSO": the G3A night held 144,830 rows with `pred.is_sso = True`.

## Dates

The repository uses half-open UTC dates `[start, stop)`. The portal date picker is inclusive ("Pick up start and stop dates (included)"), and Fink's transfer job expands the range with an inclusive `pandas.date_range` (public source: `astrolabsoftware/lsst.fink-portal.org`, `assets/spark_lsst_transfer.py`).

| Repository window | Portal dates | Nights | Scope |
|---|---|---|---|
| `[2026-02-25, 2026-02-26)` | 2026-02-25 to 2026-02-25 | 1 | `full_night` |
| `[2026-02-25, 2026-03-25)` | 2026-02-25 to 2026-03-24 | 28 | `date_range` |
| `[2026-03-25, 2026-04-25)` | 2026-03-25 to 2026-04-24 | 31 | `date_range` |

Dates must be written `YYYY-MM-DD`; `stop <= start`, malformed dates and windows longer than 92 nights (a typo guard) are rejected. The last requested night must have ended at least one full day earlier (UTC), so Fink's ingestion of it has settled: on 2026-10-05 the latest requestable night is 2026-10-03.

## Scopes and claims

Single nights keep the G3A `full_night` scope and path. Two or more nights use the generic Light Static scope `date_range`:

```text
data/raw/data_transfer/date_range/<start>_to_<stop>/<topic>/
```

`date_range` is valid only for unfiltered all-alert Light Static manifests. Its completeness unit is the range, recorded as `claim_state.range_completeness` (default `unresolved`, never `allowed` by default); `week_completeness` stays `blocked` because a range is not a week. `full_week`, `full_week_full_packet` and every historical manifest are unchanged.

## Layers

| Layer | Module | Responsibility |
|---|---|---|
| A science profile | `profile.py` | fixed, pinned scientific content |
| B request planner | `planner.py` | half-open window, portal dates, canonical request, fingerprint |
| C portal compiler | `portal_config.py` | compile the portal YAML; parse and compare portal output semantically |
| D registry / state machine | `states.py`, `registry.py` | transitions with evidence guards; append-only hash-chained log; evidence re-verified on replay |
| submission authority | `authority.py` | external SQLite ledger: one permanent claim per fingerprint |
| E portal adapter | `portal.py`, `portal_playwright.py` | drive the public form; form checks; one-use submit authorization; structural callback guard; producer-log semantics |
| F Kafka / Arnor handoff | `handoff.py` | watermark observation, run manifest, topic entry, transfer plan |
| G delivery validation | `receipts.py`, `evidence.py` | acquisition-bound receipts, three-way reconciliation, scalable integrity evidence |
| orchestration / CLI | `orchestrator.py`, `cli.py` | order of operations, duplicate policy, operator review |

## Canonical request and fingerprint

`request.json` separates the **scientific identity** (survey, profile name, pinned profile digest, scope, half-open window) from **operational metadata** (creation time, generator, as-of date). The fingerprint is `sha256(canonical_json(scientific_identity))` with sorted keys and no whitespace, so key order, formatting, timestamps, machine and invocation path never change it, while a different window or profile version always does. The acquisition id is derived from it, for example `acq_lsst_ls_v1_2026-02-25_to_2026-03-25_b1c7b482b56b`. Loading a stored request re-derives everything and fails on any difference.

## Portal configuration

The compiled YAML uses the portal's own download layout; the one-night request reproduces the G3A portal file byte for byte. Before anything could be submitted, the portal's own "Download configuration" output and the visible form must both match the request:

- dates, `content == ["Light static packet"]`, no filters, no blocks, no catalogue, no extra SQL;
- unknown or missing YAML keys, unreadable dates and anything not observable fail closed.

Portal behaviour observed in G3B.0 (live form and public source):

- After a configuration upload the portal downloads `extra_cond: []` instead of `null`; both mean no SQL. At submit time the uploaded form sends an empty `-extraCond=`, which the transfer job skips (`if cond == "": continue`).
- An empty packet selection means Full packet in the transfer job, so an empty `content` is a mismatch.
- The portal's Dash front end fires the "Submit job" callback once, without an `n_clicks` value, when an uploaded configuration moves the form to its final step. The server ignores it (`if n_clicks:`). During a dry run the adapter's request guard aborts it anyway.
- The alert gauge is a statistical estimate (Month 1 showed 1,658,642), never an expected count.

## State machine

```text
PLANNED -> PORTAL_PREPARED -> PORTAL_VERIFIED -> APPROVED -> SUBMITTING
  -> SUBMITTED -> TOPIC_IDENTIFIED -> PRODUCER_RUNNING -> PRODUCER_COMPLETE
  -> TOPIC_VERIFIED -> TRANSFER_RUNNING -> TRANSFER_COMPLETE -> DELIVERY_VALIDATED
```

Failure and uncertainty states: `BLOCKED`, `PORTAL_FAILURE`, `SUBMISSION_UNCERTAIN`, `PRODUCER_UNCONFIRMED`, `TOPIC_TIMEOUT`, `TRANSFER_INTERRUPTED`, `RECONCILIATION_FAILED`. Every transition must be an allowed edge and carry the evidence its target requires, for example a matching fingerprint for `APPROVED`, both terminal producer markers for `PRODUCER_COMPLETE`, `sum(high - low)` for `TOPIC_VERIFIED`, and exactly equal counts for `DELIVERY_VALIDATED`.

Registry layout (committed; lock files are ignored):

```text
configs/acquisitions/<acquisition_id>/
    request.json  portal_config.yml  state_log.jsonl  evidence/
```

The state is the replay of `state_log.jsonl` (append-only, hash-chained, fsynced, written under a file lock with a stale-writer check). It is never inferred from which files exist. Evidence files are write-once and every write is scanned for credential-looking keys and values.

## Submission authority (Git is provenance, not authority)

A `git checkout`, `reset`, branch switch or second clone can show an older registry in which a request was never submitted, so Git cannot decide whether a fingerprint may be submitted. That decision belongs to one SQLite database outside every checkout on the **single host authorized to submit**:

```text
$XDG_STATE_HOME/fink-lsst/submission_authority.sqlite3   (default ~/.local/state/fink-lsst/...)
```

- The scientific fingerprint is the primary key. The claim is taken atomically (`BEGIN IMMEDIATE`, `synchronous=FULL`) before any Submit callback can be authorized, and it is permanent: triggers refuse deletes and identity changes, batch id and topic are set once, and one topic belongs to one fingerprint.
- A crash before, during or after the click leaves the claim in place. Git rollback, another clone or a concurrent process cannot clear it; concurrent claims have exactly one winner.
- A wrong owner, a directory writable by others, a corrupt file or a foreign schema raises `SubmissionAuthorityError`, and nothing is submitted. The directory is `0700`, the file `0600`; only ids, digests, statuses and timestamps are stored.
- There is no command-line option to use another authority; tests inject temporary files through the Python API, and the test session points `XDG_STATE_HOME` at a temporary directory.
- Do not delete or copy the authority between hosts. A second submitting host would need a new design, not a second file.

## Duplicate submission and uncertainty

- A request with a claim in the authority, or a `SUBMITTING` in its registry history, is never verified or submitted again.
- A lost response, an exception after the click, a refused callback or a `SUBMITTING` left by a dead process becomes `SUBMISSION_UNCERTAIN`. There is no retry.
- Leaving `SUBMISSION_UNCERTAIN` needs explicit reconciliation with a written statement, serialized with the authority:
  - `fink-lsst reconcile --id ID --resolution job_found --batch-id N --topic T --statement "..."` attaches recovered evidence, which must agree with any batch id or topic already observed;
  - `--resolution no_job_created` is refused when a batch id or topic was observed. Otherwise the statement is recorded and the request stays `BLOCKED` for Control. It never becomes submittable again.
- `BLOCKED` resumes only into the state it came from. A request blocked before any submission can be re-verified with `fink-lsst unblock --id ID --statement "..."`.

## Dry run vs submit

| Mode | Command | Writes | Browser | Submit |
|---|---|---|---|---|
| review | `acquire` | nothing | no | no |
| record | `acquire --record` | registry | no | no |
| portal dry run | `acquire --portal-check` | registry + evidence | yes | never |
| submit | `acquire --portal-check --submit` | refused (live submission disabled) | no | no |

When enabled, a submission runs in one browser context, in this order:

1. no claim in the authority; registry record committed, clean and pushed (`--record`, commit, push first);
2. portal verification in the current **browser-context generation** (it rotates on every open, configuration upload and page reload, and is cleared on close);
3. the operator types the exact acquisition id at an interactive terminal;
4. the form is observed again; any drift (dates, packet, filters, blocks, catalogue, SQL, final review) blocks the request **without** claiming or clicking;
5. `APPROVED`, then the permanent claim, then `SUBMITTING`, and only then a one-use `SubmitAuthorization` bound to the attempt id, fingerprint, request and config digests, context and approved scientific state;
6. the adapter spends the authorization, re-reads the form, and clicks once.

The adapter context is ephemeral (no storage state) and blocks service workers, so request interception sees every request. Each request is classified structurally (`portal.classify_dash_request`: JSON parsed, escaped ids recognised, unexpected shapes fail closed). The only Submit callback ever let through is the single one sent during `submit(authorization)`, carrying `n_clicks >= 1` and form state equal to the canonical request: dates, empty filters and blocks, `["Light static packet"]`, no SQL, no catalogue. All of these are in the portal's submit callback. The allowance is revoked when it is used and in `finally`. The portal's mount-time callback (no click count) is always aborted. Fink's private backends (Livy/Spark) are never called.

## Producer status

The transfer job logs `Starting to send data to topic <t>` before writing, then `Data available at topic: <t>` and `End.` when done. Only both terminal markers, in order and for the identified topic, give `PRODUCER_COMPLETE`. A lost portal page gives `PRODUCER_UNCONFIRMED`; from there `TOPIC_VERIFIED` needs written fallback evidence (for example Fink support's confirmation, as in G3A). A log row mentioning an error or failure gives `BLOCKED`; an operator may judge it inconclusive (`mark_producer_unconfirmed`, with a statement), which still requires fallback evidence. Only canonical marker text is stored, never raw log rows.

## Local vs Arnor responsibilities

| Local / control side (Mac) | Arnor / data plane |
|---|---|
| plan, record, registry, Git provenance | `FINK_LSST_DATA_ROOT=/astro/store/shire/FINK` |
| portal automation (Playwright, `.venv-portal/`) | `finkctl transfer` in a `fink-*` tmux session |
| approval and reconciliation | raw files (immutable), full sha256 inventory, logs |

Browser tooling is never installed into the Arnor science environment.

`fink-lsst handoff --id ID` builds, from the accepted G2B/G3A primitives, the run manifest, the topic-registry entry, the confined absolute `-outdir`, the log path and `finkctl transfer -survey lsst -topic T -outdir ABS -nconsumers 4 --dump_schemas --verbose`, with the working directory `<root>/manifests/<topic>` because `--dump_schemas` writes to the working directory. It is `ready_for_transfer` only in `TOPIC_VERIFIED`. The pre-check reads partition watermarks with `watermarks_from_consumer` (metadata only, no consume or commit) and records `expected_topic_messages = sum(high - low)`, a transport count, not Rubin completeness. A fresh topic must show low watermark 0 on every partition; anything else (retention already removed messages) blocks `TOPIC_VERIFIED`.

## Delivery validation and integrity evidence

`DELIVERY_VALIDATED` requires `expected_topic_messages == terminal committed == local readable rows` with lag 0 and no unreadable files, the G3A pattern `798047 = 798047 = 798047`. There is no tolerance, and equal counts alone are not enough: each step is a typed receipt (`receipts.py`) whose identity comes from the acquisition record, not from the caller.

| Receipt | Binds |
|---|---|
| topic metadata | acquisition id, fingerprint, batch id, topic, **topic actually queried**, partition watermarks, `expected_topic_messages` |
| transfer | the same identity, transfer attempt id, this acquisition's confined raw directory, command digest, release, exit code, committed, lag, log digest |
| delivery | the same identity and attempt id, the same raw directory (audited by the builder itself), file count, bytes, readable rows, unreadable files, schema groups, inventory digests, reconciliation |

Receipts are write-once evidence files referenced as `{path, sha256, kind}`. Each registry load re-reads every referenced file (portal downloads included). A missing file, a symlink, a path outside the record, a digest mismatch, or a file differing from the logged receipt makes the load fail. A topic can be identified by one acquisition only.

For orchestrated acquisitions the full per-file inventory (`<sha256>  <relative path>`, sorted, the G3A format) stays on the data plane next to the immutable raw files. Git receives only small evidence: counts, bytes, readable rows, schema groups, the inventory's SHA-256, 256 shard digests keyed by `sha256(relative_path)[:2]` and a root digest (`evidence.py`). The committed G3A inventory is unchanged; the test suite recomputes its recorded digest.

## Local portal environment

```bash
~/.local/bin/python3.11 -m venv .venv-portal
.venv-portal/bin/python -m pip install -e ".[portal]"
```

The adapter drives the installed Google Chrome (`channel="chrome"`) in a fresh, ephemeral context: no stored profile, cookies or screenshots. `.venv-portal/` and browser artifacts are gitignored.

## Tests

```bash
python -m pytest                                  # full offline suite
python -m pytest tests/test_acquisition_*.py tests/test_date_range_scope.py
```

The acquisition tests use a deterministic fake portal; they never open a browser or reach the network.

## Month 1, eventually

The Month 1 request is registered as `acq_lsst_ls_v1_2026-02-25_to_2026-03-25_b1c7b482b56b` and was portal-verified without submission in G3B.0, and again under the hardened code in G3B.0-R2. After Control authorizes live submission in a later gate:

1. on the single authorized submission host, enable submission (`LIVE_SUBMISSION_ENABLED`) in a reviewed commit, then commit and push the registry;
2. `fink-lsst acquire --start 2026-02-25 --stop 2026-03-25 --portal-check --submit` re-verifies the portal in a fresh context, asks for the typed acquisition id, re-observes the form, claims the attempt, submits once and records the batch id and topic;
3. watch the producer to `PRODUCER_COMPLETE`, record the Kafka watermark pre-check (`TOPIC_VERIFIED`), then run the `handoff` transfer plan on Arnor and record the transfer and the three-way reconciliation. In this release those executor steps are `AcquisitionOrchestrator` methods (`observe_producer`, `record_topic_metadata`, `record_transfer_started`, `record_transfer_result`, `record_delivery_validation`); command-line wrappers belong to the transfer gate.

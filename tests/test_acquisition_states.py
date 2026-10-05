"""Acquisition state machine and durable registry tests (FINK-G3B.0; evidence binding per G3B.0-R2)."""

import json
from datetime import date

import pytest

from fink_lsst.acquisition.planner import build_acquisition_request
from fink_lsst.acquisition.portal_config import compile_portal_config, render_portal_yaml
from fink_lsst.acquisition.receipts import (
    PartitionWatermarks,
    TopicWatermarkObservation,
    build_topic_metadata_receipt,
    build_transfer_receipt,
    receipt_text,
)
from fink_lsst.acquisition.registry import (
    AcquisitionRegistry,
    ConcurrentModificationError,
    DuplicateAcquisitionError,
    RegistryError,
)
from fink_lsst.acquisition.states import (
    POST_SUBMISSION_STATES,
    AcquisitionState as S,
    TransitionError,
    blocks_automatic_submission,
)


AS_OF = date(2026, 10, 5)
TOPIC = "ftransfer_lsst_2026-10-05_123456"


class Clock:
    def __init__(self):
        self.tick = 0

    def __call__(self):
        self.tick += 1
        return f"2026-10-05T00:00:{self.tick:02d}Z"


def _registry(tmp_path):
    return AcquisitionRegistry(tmp_path / "acquisitions", clock=Clock())


def _created(tmp_path, start="2026-02-25", stop="2026-03-25"):
    registry = _registry(tmp_path)
    request = build_acquisition_request(start, stop, as_of=AS_OF, created_at_utc="2026-10-05T00:00:00Z")
    record = registry.create(request, render_portal_yaml(compile_portal_config(request)))
    return registry, record


def _write(registry, record, kind, payload, suffix="json"):
    name = f"{kind}_{registry.evidence_count(record, kind + '_') + 1}.{suffix}"
    text = payload if isinstance(payload, str) else receipt_text(payload)
    return registry.write_evidence(record, name, text, kind)


def _verification(registry, record, context="ctx-1"):
    ref = _write(registry, record, "portal_download", render_portal_yaml(compile_portal_config(record.request)), suffix="yml")
    return {
        "context_id": context,
        "semantic_match": True,
        "ui_checks_passed": True,
        "final_review_reached": True,
        "submit_clicked": False,
        "expected_config_sha256": record.portal_config_sha256,
        "downloaded_config_sha256": ref["sha256"],
        "downloaded_config_ref": ref,
    }


def _to_portal_verified(registry, record, context="ctx-1"):
    record = registry.transition(record, S.PORTAL_PREPARED, actor="orchestrator", reason="compiled", evidence={"portal_config_sha256": record.portal_config_sha256})
    return registry.transition(record, S.PORTAL_VERIFIED, actor="orchestrator", reason="portal dry run", evidence=_verification(registry, record, context))


def _approval(record, context="ctx-1"):
    return {
        "method": "interactive_tty",
        "approved_fingerprint": record.fingerprint,
        "verification_seq": record.last_entry(S.PORTAL_VERIFIED).seq,
        "context_id": context,
        "presubmit_state_digest": "a" * 64,
    }


def _to_submitting(registry, record, context="ctx-1"):
    record = _to_portal_verified(registry, record, context)
    record = registry.transition(record, S.APPROVED, actor="operator", reason="approved", evidence=_approval(record, context))
    return registry.transition(record, S.SUBMITTING, actor="orchestrator", reason="clicking submit", evidence={"context_id": context, "approval_seq": record.last_entry(S.APPROVED).seq, "attempt_id": "b" * 32})


def _identified(registry, record):
    record = _to_submitting(registry, record)
    return registry.transition(record, S.TOPIC_IDENTIFIED, actor="orchestrator", reason="portal returned batch and topic", evidence={"batch_id": "41", "topic": TOPIC})


def _topic_receipt(record, highs=(10,)):
    observation = TopicWatermarkObservation(TOPIC, "2026-10-05T01:00:00Z", tuple(PartitionWatermarks(i, 0, h) for i, h in enumerate(highs)))
    return build_topic_metadata_receipt(record, observation)


def _topic_evidence(registry, record, receipt=None, **extra):
    receipt = receipt if receipt is not None else _topic_receipt(record)
    return {"receipt": receipt, "receipt_ref": _write(registry, record, "topic_metadata", receipt), **extra}


def _running_evidence(record, attempt="c" * 32):
    return {"topic": TOPIC, "transfer_attempt_id": attempt, "raw_dir": record.request.expected_raw_dir(TOPIC), "command_sha256": "d" * 64, "release": "rev-test"}


def _transfer_evidence(registry, record, committed=10, exit_code=0, lag=0):
    receipt = build_transfer_receipt(record, exit_code=exit_code, terminal_committed=committed, terminal_lag=lag, log_sha256="e" * 64)
    return {"receipt": receipt, "receipt_ref": _write(registry, record, "transfer", receipt)}


def _delivery_evidence(registry, record, rows, expected=10, committed=10, unreadable=0, passed=None):
    transfer = record.last_entry(S.TRANSFER_COMPLETE).evidence["receipt"]
    reconciliation = {"expected_topic_messages": expected, "terminal_committed": committed, "terminal_lag": 0, "local_readable_rows": rows}
    reconciliation["passed"] = (rows == expected == committed and unreadable == 0) if passed is None else passed
    receipt = {
        "schema_version": 1, "kind": "delivery", "acquisition_id": record.acquisition_id, "fingerprint": record.fingerprint,
        "batch_id": record.batch_id, "topic": TOPIC, "transfer_attempt_id": transfer["transfer_attempt_id"], "raw_dir": transfer["raw_dir"],
        "file_count": 2, "readable_rows": rows, "unreadable_files": unreadable, "reconciliation": reconciliation,
    }
    return {"receipt": receipt, "receipt_ref": _write(registry, record, "delivery", receipt)}


def _to_transfer_complete(registry, record):
    record = _identified(registry, record)
    record = registry.transition(record, S.PRODUCER_COMPLETE, actor="orchestrator", reason="log", evidence={"data_available_marker": True, "end_marker": True, "topic": TOPIC})
    record = registry.transition(record, S.TOPIC_VERIFIED, actor="executor", reason="kafka metadata", evidence=_topic_evidence(registry, record))
    record = registry.transition(record, S.TRANSFER_RUNNING, actor="executor", reason="finkctl started", evidence=_running_evidence(record))
    return registry.transition(record, S.TRANSFER_COMPLETE, actor="executor", reason="exit 0", evidence=_transfer_evidence(registry, record))


# ---------------------------------------------------------------- state machine


def test_happy_path_through_delivery_validated_keeps_full_history(tmp_path):
    registry, record = _created(tmp_path)
    assert record.state == S.PLANNED
    record = _identified(registry, record)
    record = registry.transition(record, S.PRODUCER_RUNNING, actor="orchestrator", reason="log", evidence={"marker": "Starting to send data to topic"})
    record = registry.transition(record, S.PRODUCER_COMPLETE, actor="orchestrator", reason="log", evidence={"data_available_marker": True, "end_marker": True, "topic": TOPIC})
    record = registry.transition(record, S.TOPIC_VERIFIED, actor="executor", reason="kafka metadata", evidence=_topic_evidence(registry, record))
    record = registry.transition(record, S.TRANSFER_RUNNING, actor="executor", reason="finkctl started", evidence=_running_evidence(record))
    record = registry.transition(record, S.TRANSFER_COMPLETE, actor="executor", reason="exit 0", evidence=_transfer_evidence(registry, record))
    record = registry.transition(record, S.DELIVERY_VALIDATED, actor="executor", reason="reconciled", evidence=_delivery_evidence(registry, record, 10))
    reloaded = registry.load(record.acquisition_id)
    assert reloaded.state == S.DELIVERY_VALIDATED
    assert [entry.to_state for entry in reloaded.entries] == [
        S.PLANNED, S.PORTAL_PREPARED, S.PORTAL_VERIFIED, S.APPROVED, S.SUBMITTING, S.TOPIC_IDENTIFIED,
        S.PRODUCER_RUNNING, S.PRODUCER_COMPLETE, S.TOPIC_VERIFIED, S.TRANSFER_RUNNING, S.TRANSFER_COMPLETE, S.DELIVERY_VALIDATED,
    ]
    assert [entry.seq for entry in reloaded.entries] == list(range(1, 13))


@pytest.mark.parametrize(
    "target",
    [S.SUBMITTING, S.SUBMITTED, S.TOPIC_IDENTIFIED, S.APPROVED, S.TRANSFER_RUNNING, S.DELIVERY_VALIDATED],
)
def test_invalid_transitions_from_planned_are_rejected(tmp_path, target):
    registry, record = _created(tmp_path)
    with pytest.raises(TransitionError):
        registry.transition(record, target, actor="orchestrator", reason="skip ahead", evidence={})
    assert registry.load(record.acquisition_id).state == S.PLANNED


def test_evidence_is_required_for_guarded_transitions(tmp_path):
    registry, record = _created(tmp_path)
    with pytest.raises(TransitionError, match="portal_config_sha256"):
        registry.transition(record, S.PORTAL_PREPARED, actor="orchestrator", reason="x", evidence={"portal_config_sha256": "0" * 64})
    record = registry.transition(record, S.PORTAL_PREPARED, actor="orchestrator", reason="x", evidence={"portal_config_sha256": record.portal_config_sha256})
    bad = _verification(registry, record)
    bad["semantic_match"] = False
    with pytest.raises(TransitionError):
        registry.transition(record, S.PORTAL_VERIFIED, actor="orchestrator", reason="x", evidence=bad)
    bad = _verification(registry, record)
    bad["submit_clicked"] = True
    with pytest.raises(TransitionError):
        registry.transition(record, S.PORTAL_VERIFIED, actor="orchestrator", reason="x", evidence=bad)


def test_approval_must_match_fingerprint_and_latest_verification(tmp_path):
    registry, record = _created(tmp_path)
    record = _to_portal_verified(registry, record)
    wrong = _approval(record)
    wrong["approved_fingerprint"] = "0" * 64
    with pytest.raises(TransitionError, match="fingerprint"):
        registry.transition(record, S.APPROVED, actor="operator", reason="x", evidence=wrong)
    wrong = _approval(record)
    wrong["context_id"] = "other-context"
    with pytest.raises(TransitionError, match="context"):
        registry.transition(record, S.APPROVED, actor="operator", reason="x", evidence=wrong)
    wrong = _approval(record)
    del wrong["presubmit_state_digest"]
    with pytest.raises(TransitionError, match="pre-submit"):
        registry.transition(record, S.APPROVED, actor="operator", reason="x", evidence=wrong)
    wrong = _approval(record)
    wrong["method"] = "absence_of_dry_run_flag"
    with pytest.raises(TransitionError, match="method"):
        registry.transition(record, S.APPROVED, actor="operator", reason="x", evidence=wrong)


def test_topic_must_look_like_an_lsst_data_transfer_topic(tmp_path):
    registry, record = _created(tmp_path)
    record = _to_submitting(registry, record)
    for topic in ("ftransfer_ztf_2026-10-05_1", "../etc", "ftransfer_lsst_2026-10-05", ""):
        with pytest.raises(TransitionError):
            registry.transition(record, S.TOPIC_IDENTIFIED, actor="orchestrator", reason="x", evidence={"batch_id": "41", "topic": topic})


def test_producer_complete_requires_terminal_markers(tmp_path):
    registry, record = _created(tmp_path)
    record = _identified(registry, record)
    record = registry.transition(record, S.PRODUCER_RUNNING, actor="orchestrator", reason="x", evidence={"marker": "Starting to send data to topic"})
    with pytest.raises(TransitionError):
        registry.transition(record, S.PRODUCER_COMPLETE, actor="orchestrator", reason="browser lost", evidence={"data_available_marker": True, "end_marker": False, "topic": TOPIC})
    record = registry.transition(record, S.PRODUCER_UNCONFIRMED, actor="orchestrator", reason="portal log unavailable", evidence={"last_observed": "Starting to send data to topic"})
    with pytest.raises(TransitionError, match="fallback"):
        registry.transition(record, S.TOPIC_VERIFIED, actor="executor", reason="x", evidence=_topic_evidence(registry, record))
    record = registry.transition(
        record, S.TOPIC_VERIFIED, actor="executor", reason="fallback evidence",
        evidence=_topic_evidence(registry, record, fallback_evidence="Fink support confirmed batch 41 completed"),
    )
    assert record.state == S.TOPIC_VERIFIED


def test_delivery_validated_requires_exact_three_way_reconciliation(tmp_path):
    registry, record = _created(tmp_path)
    record = _to_transfer_complete(registry, record)
    with pytest.raises(TransitionError):
        registry.transition(record, S.DELIVERY_VALIDATED, actor="executor", reason="x", evidence=_delivery_evidence(registry, record, 9, passed=True))
    with pytest.raises(TransitionError, match="recorded pre-check, transfer"):
        registry.transition(record, S.DELIVERY_VALIDATED, actor="executor", reason="x", evidence=_delivery_evidence(registry, record, 11, committed=11, expected=11))
    with pytest.raises(TransitionError):
        registry.transition(record, S.DELIVERY_VALIDATED, actor="executor", reason="x", evidence=_delivery_evidence(registry, record, 10, unreadable=2, passed=True))
    no_ref = _delivery_evidence(registry, record, 10)
    del no_ref["receipt_ref"]
    with pytest.raises(TransitionError):
        registry.transition(record, S.DELIVERY_VALIDATED, actor="executor", reason="x", evidence=no_ref)
    record = registry.transition(record, S.RECONCILIATION_FAILED, actor="executor", reason="rows short", evidence=_delivery_evidence(registry, record, 9))
    assert record.state == S.RECONCILIATION_FAILED


def test_no_path_reaches_topic_dependent_states_without_an_identified_topic(tmp_path):
    registry, record = _created(tmp_path)
    record = _to_submitting(registry, record)
    record = registry.transition(record, S.SUBMITTED, actor="orchestrator", reason="batch only", evidence={"batch_id": "41"})
    record = registry.transition(record, S.TOPIC_TIMEOUT, actor="orchestrator", reason="no topic", evidence={})
    reconciliation = {"resolution": "late_evidence", "statement": "support says done", "fallback_evidence": "support"}
    forged = {"schema_version": 1, "kind": "topic_metadata", "acquisition_id": record.acquisition_id, "fingerprint": record.fingerprint, "batch_id": "41",
              "topic": None, "queried_topic": None, "checked_utc": "x", "partitions": [{"partition": 0, "low": 0, "high": 10}], "expected_topic_messages": 10}
    with pytest.raises(TransitionError, match="no LSST topic"):
        registry.transition(record, S.TOPIC_VERIFIED, actor="reconciliation", reason="x", evidence={**reconciliation, **_topic_evidence(registry, record, receipt=forged)})
    with pytest.raises(TransitionError, match="no LSST topic"):
        registry.transition(record, S.PRODUCER_COMPLETE, actor="reconciliation", reason="x", evidence={**reconciliation, "topic": None, "data_available_marker": True, "end_marker": True})


def test_transfer_and_watermark_evidence_types_are_strict(tmp_path):
    registry, record = _created(tmp_path)
    record = _identified(registry, record)
    record = registry.transition(record, S.PRODUCER_COMPLETE, actor="orchestrator", reason="x", evidence={"data_available_marker": True, "end_marker": True, "topic": TOPIC})
    base = _topic_receipt(record)
    for partitions in ([{"partition": 0, "low": 5, "high": 15}], [{"partition": 0, "low": 0, "high": True}], [{"partition": 0}], [{"partition": 0, "low": 0, "high": 5}, {"partition": 0, "low": 0, "high": 5}]):
        with pytest.raises(TransitionError):
            registry.transition(record, S.TOPIC_VERIFIED, actor="executor", reason="x", evidence=_topic_evidence(registry, record, receipt={**base, "partitions": partitions}))
    record = registry.transition(record, S.TOPIC_VERIFIED, actor="executor", reason="x", evidence=_topic_evidence(registry, record))
    for bad in ({"raw_dir": "data/raw/data_transfer/date_range/elsewhere/" + TOPIC}, {"transfer_attempt_id": "short"}, {"command_sha256": None}, {"release": ""}):
        with pytest.raises(TransitionError):
            registry.transition(record, S.TRANSFER_RUNNING, actor="executor", reason="x", evidence={**_running_evidence(record), **bad})
    record = registry.transition(record, S.TRANSFER_RUNNING, actor="executor", reason="x", evidence=_running_evidence(record))
    good = build_transfer_receipt(record, exit_code=0, terminal_committed=10, terminal_lag=0)
    for bad in ({"terminal_committed": True}, {"exit_code": False}, {"terminal_lag": False}, {"transfer_attempt_id": "f" * 32}, {"raw_dir": "data/raw/data_transfer/other"}):
        receipt = {**good, **bad}
        with pytest.raises(TransitionError):
            registry.transition(record, S.TRANSFER_COMPLETE, actor="executor", reason="x", evidence={"receipt": receipt, "receipt_ref": _write(registry, record, "transfer", receipt)})


def test_tuple_and_endpoint_evidence_is_refused(tmp_path):
    registry, record = _created(tmp_path)
    sha = record.portal_config_sha256
    for evidence in (
        {"portal_config_sha256": sha, "lines": ("sasl.password=hunter2",)},
        {"portal_config_sha256": sha, "note": "broker kafka.example.org:9092"},
        {"portal_config_sha256": sha, "note": "connect to 10.1.2.3"},
        {"portal_config_sha256": sha, "note": "-servers were re-registered"},
    ):
        with pytest.raises(RegistryError):
            registry.transition(record, S.PORTAL_PREPARED, actor="orchestrator", reason="x", evidence=evidence)
    assert registry.load(record.acquisition_id).state == S.PLANNED


def test_create_leaves_no_partial_record_and_ignores_staging(tmp_path):
    registry, record = _created(tmp_path)
    (registry.root / ".staging-crashed-abc").mkdir()
    assert [item.acquisition_id for item in registry.list_records()] == [record.acquisition_id]
    assert not any(path.name.startswith(".staging-") and path.name != ".staging-crashed-abc" for path in registry.root.iterdir())


def test_hash_consistent_but_invalid_history_is_rejected_on_load(tmp_path):
    """Replay re-checks every guard, so a correctly re-sealed forged line still fails."""
    from fink_lsst.acquisition.registry import _entry_line, _seal
    from fink_lsst.acquisition.states import StateLogEntry

    registry, record = _created(tmp_path)
    record = registry.transition(record, S.PORTAL_PREPARED, actor="orchestrator", reason="x", evidence={"portal_config_sha256": record.portal_config_sha256})
    forged = _verification(registry, record)
    forged["submit_clicked"] = True
    last = record.entries[-1]
    entry = _seal(StateLogEntry(seq=last.seq + 1, at_utc="2026-10-05T00:59:00Z", from_state=last.to_state, to_state=S.PORTAL_VERIFIED, actor="orchestrator", reason="forged", evidence=forged), last.entry_sha256)
    with open(record.directory / "state_log.jsonl", "a", encoding="utf-8") as handle:
        handle.write(_entry_line(entry))
    with pytest.raises(RegistryError, match="invalid transition"):
        registry.load(record.acquisition_id)


def test_blocked_can_only_resume_previous_state_or_replan_before_submission(tmp_path):
    registry, record = _created(tmp_path)
    record = registry.transition(record, S.PORTAL_PREPARED, actor="orchestrator", reason="x", evidence={"portal_config_sha256": record.portal_config_sha256})
    record = registry.transition(record, S.BLOCKED, actor="orchestrator", reason="portal mismatch", evidence={"mismatches": ["stopdate"]})
    with pytest.raises(TransitionError):
        registry.transition(record, S.APPROVED, actor="operator", reason="x", evidence={})
    record = registry.transition(record, S.PORTAL_PREPARED, actor="operator", reason="portal fixed", evidence={"portal_config_sha256": record.portal_config_sha256})
    assert record.state == S.PORTAL_PREPARED


def test_post_submission_states_never_return_to_pre_submission(tmp_path):
    registry, record = _created(tmp_path)
    record = _identified(registry, record)
    record = registry.transition(record, S.BLOCKED, actor="executor", reason="kafka unreachable", evidence={})
    for target in (S.PLANNED, S.PORTAL_PREPARED, S.PORTAL_VERIFIED, S.APPROVED, S.SUBMITTING):
        with pytest.raises(TransitionError):
            registry.transition(record, target, actor="operator", reason="retry", evidence={"portal_config_sha256": record.portal_config_sha256})
    assert blocks_automatic_submission(record.entries)
    record = registry.transition(record, S.TOPIC_IDENTIFIED, actor="operator", reason="resume", evidence={"batch_id": "41", "topic": TOPIC})
    assert record.state == S.TOPIC_IDENTIFIED


def test_post_submission_state_set_matches_policy():
    for state in (S.SUBMITTING, S.SUBMITTED, S.TOPIC_IDENTIFIED, S.PRODUCER_RUNNING, S.PRODUCER_COMPLETE, S.TOPIC_VERIFIED,
                  S.TRANSFER_RUNNING, S.TRANSFER_COMPLETE, S.DELIVERY_VALIDATED, S.SUBMISSION_UNCERTAIN, S.TOPIC_TIMEOUT,
                  S.TRANSFER_INTERRUPTED, S.RECONCILIATION_FAILED, S.PRODUCER_UNCONFIRMED):
        assert state in POST_SUBMISSION_STATES
    for state in (S.PLANNED, S.PORTAL_PREPARED, S.PORTAL_VERIFIED, S.APPROVED, S.PORTAL_FAILURE):
        assert state not in POST_SUBMISSION_STATES


def test_submission_uncertain_requires_explicit_reconciliation(tmp_path):
    registry, record = _created(tmp_path)
    record = _to_submitting(registry, record)
    record = registry.transition(record, S.SUBMISSION_UNCERTAIN, actor="orchestrator", reason="response lost", evidence={"error": "TimeoutError"})
    with pytest.raises(TransitionError, match="reconciliation"):
        registry.transition(record, S.TOPIC_IDENTIFIED, actor="orchestrator", reason="auto", evidence={"batch_id": "41", "topic": TOPIC})
    with pytest.raises(TransitionError):
        registry.transition(record, S.SUBMITTING, actor="reconciliation", reason="retry", evidence={})
    for evidence in ({"resolution": "no_job_created", "portal_config_sha256": record.portal_config_sha256, "statement": "support says no job"}, {"portal_config_sha256": record.portal_config_sha256}):
        with pytest.raises(TransitionError):  # no negative reopening, ever (R1-03)
            registry.transition(record, S.PORTAL_PREPARED, actor="reconciliation", reason="x", evidence=evidence)
    with pytest.raises(TransitionError, match="statement"):
        registry.transition(record, S.TOPIC_IDENTIFIED, actor="reconciliation", reason="x", evidence={"resolution": "job_found", "batch_id": "41", "topic": TOPIC})
    found = registry.transition(record, S.TOPIC_IDENTIFIED, actor="reconciliation", reason="job found", evidence={"resolution": "job_found", "batch_id": "41", "topic": TOPIC, "statement": "portal batch list shows 41"})
    assert found.state == S.TOPIC_IDENTIFIED


# ---------------------------------------------------------------- durable registry


def test_registry_files_are_append_only_and_hash_chained(tmp_path):
    registry, record = _created(tmp_path)
    record = registry.transition(record, S.PORTAL_PREPARED, actor="orchestrator", reason="x", evidence={"portal_config_sha256": record.portal_config_sha256})
    directory = record.directory
    assert sorted(path.name for path in directory.iterdir() if not path.name.startswith(".")) == ["portal_config.yml", "request.json", "state_log.jsonl"]
    lines = (directory / "state_log.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first, second = (json.loads(line) for line in lines)
    assert first["prev_entry_sha256"] is None
    assert second["prev_entry_sha256"] == first["entry_sha256"]

    tampered = dict(first)
    tampered["to"] = "DELIVERY_VALIDATED"
    (directory / "state_log.jsonl").write_text(json.dumps(tampered) + "\n" + lines[1] + "\n", encoding="utf-8")
    with pytest.raises(RegistryError):
        registry.load(record.acquisition_id)


def test_registry_rejects_tampered_request_or_portal_config(tmp_path):
    registry, record = _created(tmp_path)
    portal = record.directory / "portal_config.yml"
    portal.write_text(portal.read_text(encoding="utf-8").replace("2026-03-24", "2026-03-25"), encoding="utf-8")
    with pytest.raises(RegistryError):
        registry.load(record.acquisition_id)


def test_canonical_state_comes_from_the_log_not_from_file_existence(tmp_path):
    registry, record = _created(tmp_path)
    (record.directory / "batch_id.txt").write_text("41\n", encoding="utf-8")
    (record.directory / "topic.txt").write_text(TOPIC + "\n", encoding="utf-8")
    assert registry.load(record.acquisition_id).state == S.PLANNED


def test_create_is_exclusive_per_fingerprint(tmp_path):
    registry, record = _created(tmp_path)
    request = build_acquisition_request("2026-02-25", "2026-03-25", as_of=AS_OF, created_at_utc="2030-01-01T00:00:00Z")
    with pytest.raises(DuplicateAcquisitionError):
        registry.create(request, render_portal_yaml(compile_portal_config(request)))
    assert registry.find_by_fingerprint(request.fingerprint).acquisition_id == record.acquisition_id
    assert [item.acquisition_id for item in registry.list_records()] == [record.acquisition_id]


def test_stale_in_memory_record_cannot_append(tmp_path):
    registry, record = _created(tmp_path)
    registry.transition(record, S.PORTAL_PREPARED, actor="orchestrator", reason="first writer", evidence={"portal_config_sha256": record.portal_config_sha256})
    with pytest.raises(ConcurrentModificationError):
        registry.transition(record, S.BLOCKED, actor="orchestrator", reason="second writer", evidence={})


def test_credential_like_evidence_is_refused(tmp_path):
    registry, record = _created(tmp_path)
    for evidence in ({"portal_config_sha256": record.portal_config_sha256, "session_cookie": "x"}, {"portal_config_sha256": record.portal_config_sha256, "note": "password=hunter2"}):
        with pytest.raises(RegistryError, match="credential"):
            registry.transition(record, S.PORTAL_PREPARED, actor="orchestrator", reason="x", evidence=evidence)
    with pytest.raises(RegistryError, match="credential"):
        registry.write_evidence_file(record, "portal_download.yml", "auth_token: abc\n")


def test_evidence_files_are_write_once(tmp_path):
    registry, record = _created(tmp_path)
    path = registry.write_evidence_file(record, "portal_download.yml", "blocks: []\n")
    assert path.parent.name == "evidence"
    with pytest.raises(RegistryError):
        registry.write_evidence_file(record, "portal_download.yml", "blocks: [x]\n")
    for name in ("../escape.yml", "sub/dir.yml", ".hidden"):
        with pytest.raises(RegistryError):
            registry.write_evidence_file(record, name, "x\n")


def test_operator_can_judge_a_blocked_producer_inconclusive(tmp_path):
    registry, record = _created(tmp_path)
    record = _identified(registry, record)
    record = registry.transition(record, S.BLOCKED, actor="orchestrator", reason="log flagged", evidence={})
    with pytest.raises(TransitionError, match="statement"):
        registry.transition(record, S.PRODUCER_UNCONFIRMED, actor="operator", reason="x", evidence={})
    record = registry.transition(record, S.PRODUCER_UNCONFIRMED, actor="operator", reason="x", evidence={"statement": "benign warning"})
    with pytest.raises(TransitionError, match="fallback"):
        registry.transition(record, S.TOPIC_VERIFIED, actor="executor", reason="x", evidence=_topic_evidence(registry, record))

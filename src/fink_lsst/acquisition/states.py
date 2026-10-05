"""Acquisition state machine (layer D).

States describe the life of one scientific request from planning to a
validated delivery. Every transition is checked twice: the edge must be in
the table below, and the evidence attached to it must satisfy the target
state's guard. The registry refuses to persist anything else, so a buggy
caller cannot record, for example, `PRODUCER_COMPLETE` without the terminal
producer markers or `DELIVERY_VALIDATED` without an exact three-way count.

Submission safety rules encoded here:

- `SUBMITTING` is written (after the durable claim in the external
  submission authority) before the portal Submit control can be activated.
  From then on no transition ever leads back into a pre-submission state:
  there is no negative reopening. A request whose attempt is ambiguous stays
  blocked and needs Control, never an automatic second Fink job.
- Leaving `SUBMISSION_UNCERTAIN` or `TOPIC_TIMEOUT` for anything but
  `BLOCKED` requires actor `reconciliation`, a written statement and a
  positive resolution (`job_found` or `late_evidence`).
- `BLOCKED` resumes only into the state it was entered from, or (if nothing
  was ever submitted) back into planning.
- Topic metadata, transfer and delivery steps carry typed receipts bound to
  this acquisition (fingerprint, id, topic, transfer attempt, raw directory);
  the referenced evidence files are re-verified (path, regular file, sha256,
  content) every time the log is replayed.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Optional, Sequence


class AcquisitionState(str, Enum):
    PLANNED = "PLANNED"
    PORTAL_PREPARED = "PORTAL_PREPARED"
    PORTAL_VERIFIED = "PORTAL_VERIFIED"
    APPROVED = "APPROVED"
    SUBMITTING = "SUBMITTING"
    SUBMITTED = "SUBMITTED"
    TOPIC_IDENTIFIED = "TOPIC_IDENTIFIED"
    PRODUCER_RUNNING = "PRODUCER_RUNNING"
    PRODUCER_COMPLETE = "PRODUCER_COMPLETE"
    PRODUCER_UNCONFIRMED = "PRODUCER_UNCONFIRMED"
    TOPIC_VERIFIED = "TOPIC_VERIFIED"
    TRANSFER_RUNNING = "TRANSFER_RUNNING"
    TRANSFER_COMPLETE = "TRANSFER_COMPLETE"
    DELIVERY_VALIDATED = "DELIVERY_VALIDATED"
    BLOCKED = "BLOCKED"
    SUBMISSION_UNCERTAIN = "SUBMISSION_UNCERTAIN"
    PORTAL_FAILURE = "PORTAL_FAILURE"
    TOPIC_TIMEOUT = "TOPIC_TIMEOUT"
    TRANSFER_INTERRUPTED = "TRANSFER_INTERRUPTED"
    RECONCILIATION_FAILED = "RECONCILIATION_FAILED"


S = AcquisitionState

PRE_SUBMISSION_STATES = frozenset({S.PLANNED, S.PORTAL_PREPARED, S.PORTAL_VERIFIED, S.APPROVED, S.PORTAL_FAILURE})
POST_SUBMISSION_STATES = frozenset(
    {
        S.SUBMITTING, S.SUBMITTED, S.TOPIC_IDENTIFIED, S.PRODUCER_RUNNING, S.PRODUCER_COMPLETE, S.PRODUCER_UNCONFIRMED,
        S.TOPIC_VERIFIED, S.TRANSFER_RUNNING, S.TRANSFER_COMPLETE, S.DELIVERY_VALIDATED, S.SUBMISSION_UNCERTAIN,
        S.TOPIC_TIMEOUT, S.TRANSFER_INTERRUPTED, S.RECONCILIATION_FAILED,
    }
)
RECONCILIATION_REQUIRED_FROM = frozenset({S.SUBMISSION_UNCERTAIN, S.TOPIC_TIMEOUT})
APPROVAL_METHODS = frozenset({"interactive_tty"})
LSST_TOPIC = re.compile(r"ftransfer_lsst_\d{4}-\d{2}-\d{2}_\d+")
BATCH_ID = re.compile(r"\d{1,12}")
_HEX32 = re.compile(r"[0-9a-f]{32}")
_HEX64 = re.compile(r"[0-9a-f]{64}")

TRANSITIONS: Mapping[AcquisitionState, frozenset] = {
    S.PLANNED: frozenset({S.PORTAL_PREPARED, S.BLOCKED}),
    S.PORTAL_PREPARED: frozenset({S.PORTAL_VERIFIED, S.PORTAL_FAILURE, S.BLOCKED}),
    S.PORTAL_VERIFIED: frozenset({S.PORTAL_VERIFIED, S.APPROVED, S.PORTAL_PREPARED, S.PORTAL_FAILURE, S.BLOCKED}),
    S.APPROVED: frozenset({S.SUBMITTING, S.PORTAL_PREPARED, S.BLOCKED}),
    S.PORTAL_FAILURE: frozenset({S.PORTAL_PREPARED, S.BLOCKED}),
    S.SUBMITTING: frozenset({S.SUBMITTED, S.TOPIC_IDENTIFIED, S.SUBMISSION_UNCERTAIN}),
    S.SUBMITTED: frozenset({S.TOPIC_IDENTIFIED, S.TOPIC_TIMEOUT, S.BLOCKED}),
    S.SUBMISSION_UNCERTAIN: frozenset({S.SUBMITTED, S.TOPIC_IDENTIFIED, S.BLOCKED}),
    S.TOPIC_IDENTIFIED: frozenset({S.PRODUCER_RUNNING, S.PRODUCER_COMPLETE, S.PRODUCER_UNCONFIRMED, S.TOPIC_TIMEOUT, S.BLOCKED}),
    S.PRODUCER_RUNNING: frozenset({S.PRODUCER_COMPLETE, S.PRODUCER_UNCONFIRMED, S.TOPIC_TIMEOUT, S.BLOCKED}),
    S.PRODUCER_UNCONFIRMED: frozenset({S.PRODUCER_RUNNING, S.PRODUCER_COMPLETE, S.TOPIC_VERIFIED, S.TOPIC_TIMEOUT, S.BLOCKED}),
    S.PRODUCER_COMPLETE: frozenset({S.TOPIC_VERIFIED, S.BLOCKED}),
    S.TOPIC_TIMEOUT: frozenset({S.TOPIC_IDENTIFIED, S.PRODUCER_COMPLETE, S.TOPIC_VERIFIED, S.BLOCKED}),
    S.TOPIC_VERIFIED: frozenset({S.TRANSFER_RUNNING, S.BLOCKED}),
    S.TRANSFER_RUNNING: frozenset({S.TRANSFER_COMPLETE, S.TRANSFER_INTERRUPTED}),
    S.TRANSFER_INTERRUPTED: frozenset({S.TRANSFER_RUNNING, S.BLOCKED}),
    S.TRANSFER_COMPLETE: frozenset({S.DELIVERY_VALIDATED, S.RECONCILIATION_FAILED}),
    S.RECONCILIATION_FAILED: frozenset({S.TRANSFER_RUNNING, S.BLOCKED}),
    S.DELIVERY_VALIDATED: frozenset(),
    S.BLOCKED: frozenset(),  # resolved dynamically, see _blocked_exits
}


class TransitionError(ValueError):
    """Raised when a transition is not allowed or its evidence is insufficient."""


@dataclass(frozen=True)
class StateLogEntry:
    seq: int
    at_utc: str
    from_state: Optional[AcquisitionState]
    to_state: AcquisitionState
    actor: str
    reason: str
    evidence: Mapping[str, Any] = field(default_factory=dict)
    prev_entry_sha256: Optional[str] = None
    entry_sha256: str = ""


def submission_attempted(entries: Sequence[StateLogEntry]) -> bool:
    """True once `SUBMITTING` has ever been recorded."""
    return any(entry.to_state == S.SUBMITTING for entry in entries)


def blocks_automatic_submission(entries: Sequence[StateLogEntry]) -> bool:
    """True once a submission was attempted: such a request is never submitted again automatically."""
    return bool(entries) and (entries[-1].to_state in POST_SUBMISSION_STATES or submission_attempted(entries))


def check_transition(entries: Sequence[StateLogEntry], target: AcquisitionState, *, actor: str, evidence: Mapping[str, Any], context: Mapping[str, Any]) -> None:
    """Raise `TransitionError` unless `target` may follow the recorded history with this evidence.

    `context` carries record-level facts guards need (`fingerprint`,
    `portal_config_sha256`).
    """
    target = AcquisitionState(target)
    if not entries:
        if target != S.PLANNED:
            raise TransitionError(f"the first state must be PLANNED, not {target.value}")
        return
    current = entries[-1].to_state
    allowed = _blocked_exits(entries) if current == S.BLOCKED else TRANSITIONS[current]
    if target not in allowed:
        raise TransitionError(f"illegal transition {current.value} -> {target.value}")
    if target in PRE_SUBMISSION_STATES and submission_attempted(entries):
        raise TransitionError(f"{target.value} is a pre-submission state; a submission was already attempted and is never reopened")
    if current in RECONCILIATION_REQUIRED_FROM and target != S.BLOCKED:
        if actor != "reconciliation":
            raise TransitionError(f"leaving {current.value} requires explicit reconciliation, not actor {actor!r}")
        if not str(evidence.get("statement") or "").strip():
            raise TransitionError("reconciliation requires a written statement of the evidence")
        if evidence.get("resolution") not in {"job_found", "late_evidence"}:
            raise TransitionError("reconciliation resolution must be job_found or late_evidence")
    _check_evidence(entries, target, evidence, context)


PRODUCER_OBSERVATION_STATES = frozenset({S.TOPIC_IDENTIFIED, S.PRODUCER_RUNNING, S.PRODUCER_UNCONFIRMED})


def _blocked_exits(entries: Sequence[StateLogEntry]) -> Iterable[AcquisitionState]:
    previous = next((entry.to_state for entry in reversed(entries) if entry.to_state != S.BLOCKED), S.PLANNED)
    exits = {previous}
    if not submission_attempted(entries):
        exits |= {S.PLANNED, S.PORTAL_PREPARED}
    if previous in PRODUCER_OBSERVATION_STATES:
        # An operator may judge a flagged producer log inconclusive; completion
        # then still needs fallback evidence (see TOPIC_VERIFIED).
        exits.add(S.PRODUCER_UNCONFIRMED)
    return exits


def _check_evidence(entries: Sequence[StateLogEntry], target: AcquisitionState, evidence: Mapping[str, Any], context: Mapping[str, Any]) -> None:
    def require(condition: bool, message: str) -> None:
        if not condition:
            raise TransitionError(f"{target.value}: {message}")

    if target == S.PORTAL_PREPARED:
        require(evidence.get("portal_config_sha256") == context.get("portal_config_sha256"), "portal_config_sha256 must match the compiled portal configuration")
    elif target == S.PORTAL_VERIFIED:
        require(bool(_context_id(evidence)), "a browser context id is required")
        require(evidence.get("semantic_match") is True, "the portal-normalized configuration must match semantically")
        require(evidence.get("ui_checks_passed") is True, "the portal form checks must pass")
        require(evidence.get("final_review_reached") is True, "the final pre-submit review must be reached")
        require(evidence.get("submit_clicked") is False, "verification must not activate Submit")
        require(evidence.get("expected_config_sha256") == context.get("portal_config_sha256"), "verification must be of the compiled portal configuration")
        ref = evidence.get("downloaded_config_ref")
        if ref is None and evidence.get("downloaded_config_file"):  # G3B.0 records: path plus digest
            ref = {"path": evidence["downloaded_config_file"], "sha256": evidence.get("downloaded_config_sha256"), "kind": "portal_download"}
        require(ref is not None, "the portal's downloaded configuration must be referenced")
        content = _verified(context, ref, "portal_download", require)
        require(hashlib.sha256(content).hexdigest() == evidence.get("downloaded_config_sha256"), "downloaded configuration digest differs")
    elif target == S.APPROVED:
        verification = _last(entries, S.PORTAL_VERIFIED)
        require(entries[-1].to_state == S.PORTAL_VERIFIED, "approval must directly follow a portal verification")
        require(evidence.get("method") in APPROVAL_METHODS, f"approval method must be one of {sorted(APPROVAL_METHODS)}")
        require(evidence.get("approved_fingerprint") == context.get("fingerprint"), "approved fingerprint must equal the request fingerprint")
        require(verification is not None and evidence.get("verification_seq") == verification.seq, "approval must reference the latest portal verification")
        require(verification is not None and evidence.get("context_id") == _context_id(verification.evidence), "approval must be in the verified browser context")
        require(_hex(evidence.get("presubmit_state_digest"), _HEX64), "the immediate pre-submit form observation digest is required")
    elif target == S.SUBMITTING:
        approval = _last(entries, S.APPROVED)
        require(entries[-1].to_state == S.APPROVED, "SUBMITTING must directly follow APPROVED")
        require(approval is not None and evidence.get("approval_seq") == approval.seq, "SUBMITTING must reference its approval")
        require(approval is not None and evidence.get("context_id") == approval.evidence.get("context_id"), "SUBMITTING must use the approved browser context")
        require(_hex(evidence.get("attempt_id"), _HEX32), "SUBMITTING needs the attempt id claimed in the submission authority")
    elif target == S.SUBMITTED:
        require(bool(BATCH_ID.fullmatch(str(evidence.get("batch_id") or ""))), "a numeric portal batch_id is required")
        known = _known_batch(entries)
        require(known is None or known == str(evidence.get("batch_id")), f"batch id differs from the recorded batch {known}")
    elif target == S.TOPIC_IDENTIFIED:
        require(bool(LSST_TOPIC.fullmatch(str(evidence.get("topic") or ""))), "topic must look like ftransfer_lsst_YYYY-MM-DD_N")
        batch = evidence.get("batch_id")
        require(batch is None or bool(BATCH_ID.fullmatch(str(batch))), "batch_id must be numeric when given")
        known_batch = _known_batch(entries)
        require(batch is None or known_batch is None or known_batch == str(batch), f"batch id differs from the recorded batch {known_batch}")
        known = _known_topic(entries)
        require(known is None or known == evidence.get("topic"), f"topic differs from the already identified topic {known}")
    elif target == S.PRODUCER_RUNNING:
        require(bool(str(evidence.get("marker") or "").strip()), "the observed producer marker is required")
    elif target == S.PRODUCER_UNCONFIRMED:
        if entries[-1].to_state == S.BLOCKED:
            require(bool(str(evidence.get("statement") or "").strip()), "leaving BLOCKED for PRODUCER_UNCONFIRMED needs a written statement")
    elif target == S.PRODUCER_COMPLETE:
        _require_identified_topic(entries, evidence.get("topic"), require)
        require(evidence.get("data_available_marker") is True and evidence.get("end_marker") is True, "both 'Data available at topic' and 'End.' markers are required")
    elif target == S.TOPIC_VERIFIED:
        receipt = _receipt(entries, evidence, context, "topic_metadata", require)
        require(receipt.get("queried_topic") == receipt.get("topic"), "the watermarks must be for the topic that was queried")
        expected = receipt.get("expected_topic_messages")
        require(_count(expected) and expected > 0, "expected_topic_messages must be a positive integer")
        partitions = receipt.get("partitions") or []
        require(bool(partitions) and isinstance(partitions, list), "per-partition watermarks are required")
        require(all(isinstance(item, Mapping) and _count(item.get("low")) and _count(item.get("high")) and item["high"] >= item["low"] for item in partitions), "every partition needs integer watermarks with high >= low >= 0")
        require(len({item.get("partition") for item in partitions}) == len(partitions), "partitions must be distinct")
        require(all(item["low"] == 0 for item in partitions), "a fresh topic must have low watermark 0 on every partition (retention may have removed messages)")
        require(sum(item["high"] - item["low"] for item in partitions) == expected, "expected_topic_messages must equal sum(high - low)")
        if entries[-1].to_state != S.PRODUCER_COMPLETE:
            require(bool(str(evidence.get("fallback_evidence") or "").strip()), "without terminal producer markers, fallback_evidence is required")
    elif target == S.TRANSFER_RUNNING:
        topic = _require_identified_topic(entries, evidence.get("topic"), require)
        require(_last(entries, S.TOPIC_VERIFIED) is not None, "the transfer needs a recorded topic metadata pre-check")
        require(_hex(evidence.get("transfer_attempt_id"), _HEX32), "a transfer attempt id is required")
        expected_raw_dir = context.get("expected_raw_dir")
        require(callable(expected_raw_dir) and evidence.get("raw_dir") == expected_raw_dir(topic), "the transfer must write this acquisition's own raw directory")
        require(_hex(evidence.get("command_sha256"), _HEX64), "the transfer command digest is required")
        require(bool(str(evidence.get("release") or "").strip()), "the executing release is required")
    elif target in {S.TRANSFER_COMPLETE, S.TRANSFER_INTERRUPTED}:
        receipt = _receipt(entries, evidence, context, "transfer", require)
        running = entries[-1]
        for key in ("transfer_attempt_id", "raw_dir", "command_sha256", "release"):
            require(receipt.get(key) == running.evidence.get(key), f"transfer receipt {key} differs from the running transfer")
        require(type(receipt.get("exit_code")) is int and _count(receipt.get("terminal_committed")) and type(receipt.get("terminal_lag")) is int, "exit code, terminal committed and lag must be integers")
        succeeded = receipt.get("exit_code") == 0 and receipt.get("terminal_lag") == 0
        require(succeeded if target == S.TRANSFER_COMPLETE else not succeeded, "TRANSFER_COMPLETE needs exit 0 and lag 0; anything else is TRANSFER_INTERRUPTED")
    elif target in {S.DELIVERY_VALIDATED, S.RECONCILIATION_FAILED}:
        receipt = _receipt(entries, evidence, context, "delivery", require)
        verified, complete = _last(entries, S.TOPIC_VERIFIED), _last(entries, S.TRANSFER_COMPLETE)
        require(verified is not None and complete is not None, "reconciliation needs the topic pre-check and the transfer result")
        transfer = complete.evidence.get("receipt") or {}
        require(receipt.get("transfer_attempt_id") == transfer.get("transfer_attempt_id"), "delivery evidence is for another transfer attempt")
        require(receipt.get("raw_dir") == transfer.get("raw_dir") == context["expected_raw_dir"](receipt.get("topic")), "delivery evidence is for another raw directory")
        recon = receipt.get("reconciliation") or {}
        require(
            recon.get("expected_topic_messages") == (verified.evidence.get("receipt") or {}).get("expected_topic_messages")
            and recon.get("terminal_committed") == transfer.get("terminal_committed")
            and recon.get("terminal_lag") == transfer.get("terminal_lag")
            and recon.get("local_readable_rows") == receipt.get("readable_rows"),
            "reconciliation counts must be the recorded pre-check, transfer and raw-delivery values",
        )
        counts = [recon.get(key) for key in ("expected_topic_messages", "terminal_committed", "local_readable_rows")]
        exact = all(_count(value) for value in counts) and counts[0] > 0 and len(set(counts)) == 1 and recon.get("terminal_lag") == 0 and receipt.get("unreadable_files") == 0
        if target == S.DELIVERY_VALIDATED:
            require(exact and recon.get("passed") is True, "expected topic messages, terminal committed and local readable rows must be equal with lag 0 and no unreadable files")
        else:
            require(not exact and recon.get("passed") is False, "RECONCILIATION_FAILED requires a failed reconciliation")


def _receipt(entries: Sequence[StateLogEntry], evidence: Mapping[str, Any], context: Mapping[str, Any], kind: str, require) -> Mapping[str, Any]:
    """Verify a logged receipt against its evidence file and this acquisition's identity."""
    receipt = evidence.get("receipt")
    require(isinstance(receipt, Mapping), f"a {kind} receipt is required")
    content = _verified(context, evidence.get("receipt_ref"), kind, require)
    try:
        stored = json.loads(content)
    except ValueError:
        stored = None
    require(stored == json.loads(json.dumps(receipt)), f"the {kind} receipt file differs from the logged receipt")
    require(receipt.get("schema_version") == 1 and receipt.get("kind") == kind, f"unsupported {kind} receipt schema")
    require(receipt.get("acquisition_id") == context.get("acquisition_id") and receipt.get("fingerprint") == context.get("fingerprint"), f"the {kind} receipt belongs to another acquisition")
    _require_identified_topic(entries, receipt.get("topic"), require)
    known_batch = _known_batch(entries)
    require(known_batch is None or receipt.get("batch_id") == known_batch, f"the {kind} receipt names another batch")
    return receipt


def _verified(context: Mapping[str, Any], ref: Any, kind: str, require) -> bytes:
    verify = context.get("verify_ref")
    require(callable(verify), "no evidence verifier is available")
    try:
        return verify(ref, kind)
    except (OSError, ValueError) as exc:
        raise TransitionError(f"evidence check failed: {exc}") from exc


def _context_id(evidence: Mapping[str, Any]) -> Optional[str]:
    return evidence.get("context_id") or evidence.get("session_id")  # G3B.0 records call it session_id


def _hex(value: Any, pattern: "re.Pattern[str]") -> bool:
    return isinstance(value, str) and bool(pattern.fullmatch(value))


def _count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _require_identified_topic(entries: Sequence[StateLogEntry], topic: Any, require) -> str:
    known = _known_topic(entries)
    require(bool(known) and bool(LSST_TOPIC.fullmatch(str(known))), "no LSST topic has been identified for this request")
    require(topic == known, f"evidence must name the identified topic {known}")
    return known


def _last(entries: Sequence[StateLogEntry], state: AcquisitionState) -> Optional[StateLogEntry]:
    return next((entry for entry in reversed(entries) if entry.to_state == state), None)


def _known_topic(entries: Sequence[StateLogEntry]) -> Optional[str]:
    entry = _last(entries, S.TOPIC_IDENTIFIED)
    return entry.evidence.get("topic") if entry is not None else None


def _known_batch(entries: Sequence[StateLogEntry]) -> Optional[str]:
    for entry in reversed(entries):
        if entry.to_state in {S.SUBMITTED, S.TOPIC_IDENTIFIED} and entry.evidence.get("batch_id"):
            return str(entry.evidence["batch_id"])
    return None

"""Acquisition state machine (layer D).

States describe the life of one scientific request from planning to a
validated delivery. Every transition is checked twice: the edge must be in
the table below, and the evidence attached to it must satisfy the target
state's guard. The registry refuses to persist anything else, so a buggy
caller cannot record, for example, `PRODUCER_COMPLETE` without the terminal
producer markers or `DELIVERY_VALIDATED` without an exact three-way count.

Submission safety rules encoded here:

- `SUBMITTING` is written before the portal Submit control is activated.
  Every state from then on is a post-submission state, and no transition may
  lead from one back into the pre-submission states except the explicit
  `no_job_created` reconciliation of `SUBMISSION_UNCERTAIN`, which returns to
  `PORTAL_PREPARED` and therefore needs a fresh portal verification and a
  fresh approval before another submission.
- Leaving `SUBMISSION_UNCERTAIN` or `TOPIC_TIMEOUT` requires actor
  `reconciliation` and a written statement.
- `BLOCKED` resumes only into the state it was entered from, or (if nothing
  was ever submitted) back into planning.
"""

from __future__ import annotations

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

TRANSITIONS: Mapping[AcquisitionState, frozenset] = {
    S.PLANNED: frozenset({S.PORTAL_PREPARED, S.BLOCKED}),
    S.PORTAL_PREPARED: frozenset({S.PORTAL_VERIFIED, S.PORTAL_FAILURE, S.BLOCKED}),
    S.PORTAL_VERIFIED: frozenset({S.PORTAL_VERIFIED, S.APPROVED, S.PORTAL_PREPARED, S.PORTAL_FAILURE, S.BLOCKED}),
    S.APPROVED: frozenset({S.SUBMITTING, S.PORTAL_PREPARED, S.BLOCKED}),
    S.PORTAL_FAILURE: frozenset({S.PORTAL_PREPARED, S.BLOCKED}),
    S.SUBMITTING: frozenset({S.SUBMITTED, S.TOPIC_IDENTIFIED, S.SUBMISSION_UNCERTAIN}),
    S.SUBMITTED: frozenset({S.TOPIC_IDENTIFIED, S.TOPIC_TIMEOUT, S.BLOCKED}),
    S.SUBMISSION_UNCERTAIN: frozenset({S.SUBMITTED, S.TOPIC_IDENTIFIED, S.PORTAL_PREPARED, S.BLOCKED}),
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


def _last_submission_cleared(entries: Sequence[StateLogEntry]) -> bool:
    """True when the latest submission attempt was explicitly reconciled as `no_job_created`."""
    last_submitting = max((index for index, entry in enumerate(entries) if entry.to_state == S.SUBMITTING), default=None)
    if last_submitting is None:
        return True
    return any(
        entry.actor == "reconciliation" and entry.to_state == S.PORTAL_PREPARED and entry.evidence.get("resolution") == "no_job_created"
        for entry in entries[last_submitting + 1 :]
    )


def blocks_automatic_submission(entries: Sequence[StateLogEntry]) -> bool:
    """True when this request may already have produced a Fink job and must not be submitted again automatically."""
    if not entries:
        return False
    current = entries[-1].to_state
    if current in POST_SUBMISSION_STATES:
        return True
    return not _last_submission_cleared(entries)


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
    if target in PRE_SUBMISSION_STATES and not _last_submission_cleared(entries) and not _is_no_job_reconciliation(current, target, actor, evidence):
        raise TransitionError(f"{target.value} is a pre-submission state; a submission was already attempted")
    if current in RECONCILIATION_REQUIRED_FROM and target != S.BLOCKED:
        if actor != "reconciliation":
            raise TransitionError(f"leaving {current.value} requires explicit reconciliation, not actor {actor!r}")
        if not str(evidence.get("statement") or "").strip():
            raise TransitionError("reconciliation requires a written statement of the evidence")
        resolution = evidence.get("resolution")
        if target == S.PORTAL_PREPARED and resolution != "no_job_created":
            raise TransitionError("returning to PORTAL_PREPARED requires resolution no_job_created")
        if target != S.PORTAL_PREPARED and resolution not in {"job_found", "late_evidence"}:
            raise TransitionError("reconciliation resolution must be job_found or late_evidence")
    _check_evidence(entries, target, evidence, context)


def _is_no_job_reconciliation(current: AcquisitionState, target: AcquisitionState, actor: str, evidence: Mapping[str, Any]) -> bool:
    return current == S.SUBMISSION_UNCERTAIN and target == S.PORTAL_PREPARED and actor == "reconciliation" and evidence.get("resolution") == "no_job_created"


PRODUCER_OBSERVATION_STATES = frozenset({S.TOPIC_IDENTIFIED, S.PRODUCER_RUNNING, S.PRODUCER_UNCONFIRMED})


def _blocked_exits(entries: Sequence[StateLogEntry]) -> Iterable[AcquisitionState]:
    previous = next((entry.to_state for entry in reversed(entries) if entry.to_state != S.BLOCKED), S.PLANNED)
    exits = {previous}
    if _last_submission_cleared(entries):
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
        require(bool(evidence.get("session_id")), "a portal session_id is required")
        require(evidence.get("semantic_match") is True, "the portal-normalized configuration must match semantically")
        require(evidence.get("ui_checks_passed") is True, "the portal form checks must pass")
        require(evidence.get("final_review_reached") is True, "the final pre-submit review must be reached")
        require(evidence.get("submit_clicked") is False, "verification must not activate Submit")
        require(evidence.get("expected_config_sha256") == context.get("portal_config_sha256"), "verification must be of the compiled portal configuration")
    elif target == S.APPROVED:
        verification = _last(entries, S.PORTAL_VERIFIED)
        require(entries[-1].to_state == S.PORTAL_VERIFIED, "approval must directly follow a portal verification")
        require(evidence.get("method") in APPROVAL_METHODS, f"approval method must be one of {sorted(APPROVAL_METHODS)}")
        require(evidence.get("approved_fingerprint") == context.get("fingerprint"), "approved fingerprint must equal the request fingerprint")
        require(verification is not None and evidence.get("verification_seq") == verification.seq, "approval must reference the latest portal verification")
        require(verification is not None and evidence.get("session_id") == verification.evidence.get("session_id"), "approval session must be the verified portal session")
    elif target == S.SUBMITTING:
        approval = _last(entries, S.APPROVED)
        require(entries[-1].to_state == S.APPROVED, "SUBMITTING must directly follow APPROVED")
        require(approval is not None and evidence.get("approval_seq") == approval.seq, "SUBMITTING must reference its approval")
        require(approval is not None and evidence.get("session_id") == approval.evidence.get("session_id"), "SUBMITTING must use the approved portal session")
    elif target == S.SUBMITTED:
        require(bool(BATCH_ID.fullmatch(str(evidence.get("batch_id") or ""))), "a numeric portal batch_id is required")
    elif target == S.TOPIC_IDENTIFIED:
        require(bool(LSST_TOPIC.fullmatch(str(evidence.get("topic") or ""))), "topic must look like ftransfer_lsst_YYYY-MM-DD_N")
        batch = evidence.get("batch_id")
        require(batch is None or bool(BATCH_ID.fullmatch(str(batch))), "batch_id must be numeric when given")
        known = _known_topic(entries)
        require(known is None or known == evidence.get("topic"), f"topic differs from the already identified topic {known}")
    elif target == S.PRODUCER_RUNNING:
        require(bool(str(evidence.get("marker") or "").strip()), "the observed producer marker is required")
    elif target == S.PRODUCER_UNCONFIRMED:
        if entries[-1].to_state == S.BLOCKED:
            require(bool(str(evidence.get("statement") or "").strip()), "leaving BLOCKED for PRODUCER_UNCONFIRMED needs a written statement")
    elif target == S.PRODUCER_COMPLETE:
        _require_identified_topic(entries, evidence, require)
        require(evidence.get("data_available_marker") is True and evidence.get("end_marker") is True, "both 'Data available at topic' and 'End.' markers are required")
    elif target == S.TOPIC_VERIFIED:
        _require_identified_topic(entries, evidence, require)
        expected = evidence.get("expected_topic_messages")
        require(_count(expected) and expected > 0, "expected_topic_messages must be a positive integer")
        partitions = evidence.get("partitions") or []
        require(bool(partitions) and isinstance(partitions, list), "per-partition watermarks are required")
        require(all(isinstance(item, Mapping) and _count(item.get("low")) and _count(item.get("high")) and item["high"] >= item["low"] for item in partitions), "every partition needs integer watermarks with high >= low >= 0")
        require(len({item.get("partition") for item in partitions}) == len(partitions), "partitions must be distinct")
        require(all(item["low"] == 0 for item in partitions), "a fresh topic must have low watermark 0 on every partition (retention may have removed messages)")
        require(sum(item["high"] - item["low"] for item in partitions) == expected, "expected_topic_messages must equal sum(high - low)")
        if entries[-1].to_state != S.PRODUCER_COMPLETE:
            require(bool(str(evidence.get("fallback_evidence") or "").strip()), "without terminal producer markers, fallback_evidence is required")
    elif target == S.TRANSFER_RUNNING:
        _require_identified_topic(entries, evidence, require)
        require(_last(entries, S.TOPIC_VERIFIED) is not None, "the transfer needs a recorded topic metadata pre-check")
    elif target == S.TRANSFER_COMPLETE:
        require(evidence.get("exit_code") == 0 and not isinstance(evidence.get("exit_code"), bool), "transfer exit code must be 0")
        require(_count(evidence.get("terminal_committed")), "terminal committed offsets must be a non-negative integer")
        require(evidence.get("terminal_lag") == 0 and _count(evidence.get("terminal_lag")), "terminal lag must be 0")
    elif target in {S.DELIVERY_VALIDATED, S.RECONCILIATION_FAILED}:
        reconciliation = evidence.get("reconciliation") or {}
        verified = _last(entries, S.TOPIC_VERIFIED)
        complete = _last(entries, S.TRANSFER_COMPLETE)
        require(verified is not None and complete is not None, "reconciliation needs the topic pre-check and the transfer result")
        require(
            reconciliation.get("expected_topic_messages") == verified.evidence.get("expected_topic_messages")
            and reconciliation.get("terminal_committed") == complete.evidence.get("terminal_committed")
            and reconciliation.get("terminal_lag") == complete.evidence.get("terminal_lag"),
            "reconciliation counts must be the recorded pre-check and transfer values",
        )
        summary = evidence.get("delivery_summary") or {}
        summary_reconciliation = summary.get("reconciliation") or {}
        require(summary.get("topic") == _known_topic(entries), "the delivery summary must be for the identified topic")
        require(_count(summary.get("readable_rows")) and summary.get("readable_rows") == reconciliation.get("local_readable_rows"), "local readable rows must come from the recorded delivery summary")
        require(
            all(summary_reconciliation.get(key) == reconciliation.get(key) for key in ("expected_topic_messages", "terminal_committed", "terminal_lag", "local_readable_rows")),
            "the delivery summary must reconcile the same counts",
        )
        counts = [reconciliation.get(key) for key in ("expected_topic_messages", "terminal_committed", "local_readable_rows")]
        exact = (
            all(_count(value) for value in counts)
            and counts[0] > 0
            and len(set(counts)) == 1
            and reconciliation.get("terminal_lag") == 0
            and summary.get("unreadable_files") == 0
            and (summary.get("reconciliation") or {}).get("passed") is True
        )
        if target == S.DELIVERY_VALIDATED:
            require(exact and reconciliation.get("passed") is True, "expected topic messages, terminal committed and local readable rows must be equal with lag 0 and no unreadable files")
        else:
            require(not exact and reconciliation.get("passed") is False, "RECONCILIATION_FAILED requires a failed reconciliation")


def _count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _require_identified_topic(entries: Sequence[StateLogEntry], evidence: Mapping[str, Any], require) -> None:
    known = _known_topic(entries)
    require(bool(known) and bool(LSST_TOPIC.fullmatch(str(known))), "no LSST topic has been identified for this request")
    require(evidence.get("topic") == known, f"evidence must name the identified topic {known}")


def _last(entries: Sequence[StateLogEntry], state: AcquisitionState) -> Optional[StateLogEntry]:
    return next((entry for entry in reversed(entries) if entry.to_state == state), None)


def _known_topic(entries: Sequence[StateLogEntry]) -> Optional[str]:
    entry = _last(entries, S.TOPIC_IDENTIFIED)
    return entry.evidence.get("topic") if entry is not None else None

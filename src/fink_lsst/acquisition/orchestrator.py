"""Acquisition orchestrator: composes planner, compiler, registry, portal and handoff.

The orchestrator owns the order of operations and the duplicate-submission
policy; the state machine and registry enforce the rules underneath it.

Submission sequence (guarded; the CLI arms it only with FINK_LSST_LIVE_SUBMISSION=1):

1. the external submission authority must be configured and readable, and
   must hold no attempt for this fingerprint (any error fails closed);
2. take the per-acquisition lock and reload; a SUBMITTING left by a dead
   process becomes SUBMISSION_UNCERTAIN, never a retry;
3. refuse if the registry shows a submission attempt;
4. require a PORTAL_VERIFIED made in the current browser-context generation;
5. ask the approver (a human at a terminal) and check the approved fingerprint;
6. observe the form again and require it to equal the canonical request and
   the final review (a mismatch blocks without claiming anything);
7. record APPROVED, take the permanent claim in the authority, record
   SUBMITTING, and only then issue a one-use `SubmitAuthorization` bound to
   the attempt, the context and the approved scientific state;
8. the adapter admits one Submit callback whose form state equals the
   request; a batch id and topic give SUBMITTED / TOPIC_IDENTIFIED, and
   anything else gives SUBMISSION_UNCERTAIN.

A claimed attempt is never reopened: `no_job_created` is recorded as a
statement and leaves the request BLOCKED for Control; it is refused outright
when positive evidence (an observed batch id or topic) exists.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable, Optional, Protocol, Sequence

from fink_lsst.bulk_transfer.topic_registry import DEFAULT_TOPIC_REGISTRY_PATH, load_topic_registry
from fink_lsst.data_root import REPO_ROOT

from .authority import AttemptAlreadyClaimedError, SubmissionAuthority, SubmissionAuthorityError
from .handoff import HandoffError, TransferPlan, observe_topic_watermarks
from .planner import AcquisitionRequest, build_acquisition_request
from .profile import LIGHT_STATIC_PACKET
from .portal import (
    PortalAdapter,
    SubmissionDisabledError,
    check_final_review,
    check_form_observation,
    classify_producer_log,
    issue_submit_authorization,
    scientific_state_from_config,
    scientific_state_from_observation,
)
from .portal_config import PortalConfigError, compare_portal_configs, compile_portal_config, parse_portal_config, portal_config_sha256, render_portal_yaml
from .receipts import (
    EvidenceError,
    build_delivery_receipt,
    build_topic_metadata_receipt,
    build_transfer_receipt,
    receipt_text,
)
from .registry import AcquisitionRecord, AcquisitionRegistry, SubmissionLockedError
from .states import BATCH_ID, LSST_TOPIC, AcquisitionState as S, blocks_automatic_submission


MODES = {
    "dry_run": "NO-SUBMIT / DRY RUN",
    "record": "NO-SUBMIT / RECORD PLAN (no portal)",
    "portal_check": "NO-SUBMIT / PORTAL DRY RUN (stops before Submit)",
    "submit": "SUBMIT (requires typed confirmation)",
}


class OrchestrationError(RuntimeError):
    """Base class for refusals and failures in the acquisition workflow."""


class DuplicateSubmissionError(OrchestrationError):
    """The request may already have produced a Fink job; automatic submission is refused."""


class SubmissionUncertainError(OrchestrationError):
    """Submit was (or may have been) activated but the outcome was not observed."""


class PortalVerificationError(OrchestrationError):
    """The portal could not be driven, or its state did not match the request."""


class StaleVerificationError(OrchestrationError):
    """The portal verification was made in a different browser session."""


class ApprovalRefusedError(OrchestrationError):
    """No valid human approval was given."""


@dataclass(frozen=True)
class Approval:
    method: str
    approved_fingerprint: str
    statement: str


class Approver(Protocol):
    def approve(self, record: AcquisitionRecord, review_text: str) -> Optional[Approval]: ...


@dataclass(frozen=True)
class AcquisitionPlan:
    request: AcquisitionRequest
    portal_yaml: str
    existing: Optional[AcquisitionRecord]
    overlaps: Sequence[dict] = field(default_factory=tuple)

    @property
    def raw_path_concept(self) -> str:
        component = "full_night" if self.request.scope == "full_night" else "date_range"
        return f"data/raw/data_transfer/{component}/{self.request.window.component}/<topic>"


def current_code_revision(repo_root: Path = REPO_ROOT) -> str:
    """HEAD of the checkout running this code, marked `-dirty` with local changes; `unknown` outside Git."""
    try:
        head = subprocess.run(["git", "-C", str(repo_root), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(repo_root), "status", "--porcelain", "--untracked-files=no"], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return f"{head}-dirty" if dirty else head


class AcquisitionOrchestrator:
    def __init__(
        self,
        registry: AcquisitionRegistry,
        *,
        authority: Optional[SubmissionAuthority] = None,
        topic_registry_path: Path = DEFAULT_TOPIC_REGISTRY_PATH,
        clock: Optional[Callable[[], str]] = None,
        workdir: Optional[Path] = None,
        code_revision: Callable[[], str] = current_code_revision,
    ):
        self.registry = registry
        self.authority = authority
        self.topic_registry_path = Path(topic_registry_path)
        self.clock = clock
        self.workdir = Path(workdir) if workdir else None
        self.code_revision = code_revision

    # ------------------------------------------------------------ planning

    def plan(self, start, stop, *, as_of: Optional[date] = None) -> AcquisitionPlan:
        """Pure planning: builds the canonical request and review facts; writes nothing."""
        request = build_acquisition_request(start, stop, as_of=as_of, created_at_utc=self.clock() if self.clock else None)
        portal_yaml = render_portal_yaml(compile_portal_config(request))
        existing = self.registry.find_by_fingerprint(request.fingerprint)
        return AcquisitionPlan(request, portal_yaml, existing, tuple(self._overlaps(request)))

    def render_review(self, plan: AcquisitionPlan, mode: str) -> str:
        request = plan.request
        existing = f"{plan.existing.acquisition_id} ({plan.existing.state.value})" if plan.existing else "none"
        attempt = self.authority.get(request.fingerprint) if self.authority is not None else None
        authority_line = (
            "not consulted" if self.authority is None
            else f"CLAIMED attempt {attempt.attempt_id} ({attempt.status}); never submitted again automatically" if attempt
            else "no submission attempt"
        )
        profile = request.profile
        packet = "Light Static" if profile.packet == LIGHT_STATIC_PACKET else profile.packet
        lines = [
            f"Scientific window: [{request.start}, {request.stop})",
            f"Portal dates:       {request.portal_startdate} → {request.portal_stopdate} inclusive",
            f"Requested dates:    {len(request.expected_dates)}",
            f"Profile:            {request.science_profile}",
            f"Packet:             {packet}",
            f"Filters:            {', '.join(profile.filters) or 'none'}",
            f"Blocks:             {', '.join(profile.blocks) or 'none'}",
            f"Catalogue:          {profile.catalog_filename or 'none'}",
            f"Extra SQL:          {profile.extra_cond or 'none'}",
            f"Scope:              {request.scope}",
            f"Raw path concept:   {plan.raw_path_concept}",
            f"Acquisition ID:     {request.acquisition_id}",
            f"Fingerprint:        {request.fingerprint}",
            f"Existing request:   {existing}",
            f"Submission authority: {authority_line}",
            f"Mode:               {MODES[mode]}",
        ]
        if plan.overlaps:
            lines.append("Overlapping recorded deliveries (informational; they do not block a new request):")
            for item in plan.overlaps:
                lines.append(f"  - {item['topic']} [{item['startdate']}, {item['stopdate']}) {item['scope']} {item.get('state') or ''}".rstrip())
        return "\n".join(lines) + "\n"

    def record(self, plan: AcquisitionPlan) -> AcquisitionRecord:
        """Register the request (PLANNED -> PORTAL_PREPARED), or return the existing record unchanged."""
        existing = self.registry.find_by_fingerprint(plan.request.fingerprint)
        if existing is not None:
            return existing
        record = self.registry.create(plan.request, plan.portal_yaml)
        return self.registry.transition(
            record,
            S.PORTAL_PREPARED,
            actor="orchestrator",
            reason="portal configuration compiled from the canonical request",
            evidence={"portal_config_sha256": record.portal_config_sha256},
        )

    # ------------------------------------------------------------ portal verification

    def verify_portal(self, record: AcquisitionRecord, portal: PortalAdapter) -> AcquisitionRecord:
        """Upload, read back, download and compare; stop at the final review without submitting."""
        record = self.registry.load(record.acquisition_id)
        self._refuse_if_claimed(record)
        if blocks_automatic_submission(record.entries):
            raise DuplicateSubmissionError(f"{record.acquisition_id} is {record.state.value}; portal verification is only for requests that were never submitted")
        if record.state == S.PORTAL_FAILURE:
            record = self.registry.transition(record, S.PORTAL_PREPARED, actor="orchestrator", reason="retrying portal verification", evidence={"portal_config_sha256": record.portal_config_sha256})
        if record.state == S.APPROVED:
            # An approval belongs to one browser session; a new verification withdraws it.
            record = self.registry.transition(record, S.PORTAL_PREPARED, actor="orchestrator", reason="approval from an earlier session withdrawn before re-verification", evidence={"portal_config_sha256": record.portal_config_sha256})
        if record.state not in {S.PORTAL_PREPARED, S.PORTAL_VERIFIED}:
            raise OrchestrationError(f"{record.acquisition_id} is {record.state.value}; expected PORTAL_PREPARED or PORTAL_VERIFIED")
        expected = compile_portal_config(record.request)
        attempt = self.registry.evidence_count(record, "portal_verification_") + 1
        try:
            with tempfile.TemporaryDirectory(dir=self._workdir()) as scratch:
                upload = Path(scratch) / f"{record.acquisition_id}.yml"
                upload.write_text(record.portal_yaml, encoding="utf-8")
                portal.open()
                portal.upload_config(upload)
                context_id = portal.context_id
                form = portal.observe_form()
                final = portal.reach_final_review()
                downloaded_path = portal.download_config(Path(scratch))
                downloaded_text = downloaded_path.read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 - any adapter failure is a portal failure before submission
            failure = {"error_type": type(exc).__name__, "error": _short(str(exc)), "context_id": getattr(portal, "context_id", None)}
            self.registry.transition(record, S.PORTAL_FAILURE, actor="orchestrator", reason="portal could not be driven or observed", evidence=failure)
            raise PortalVerificationError(f"portal failure: {failure['error_type']}: {failure['error']}") from exc

        mismatches: list[str] = []
        try:
            observed = parse_portal_config(downloaded_text)
            mismatches += [f"downloaded config {item}" for item in compare_portal_configs(expected, observed)]
            semantic_match = not mismatches
        except PortalConfigError as exc:
            mismatches.append(f"downloaded config unreadable: {exc}")
            semantic_match = False
        ui_mismatches = check_form_observation(form, expected) + check_final_review(final)
        blocked_submits = int(getattr(portal, "blocked_submit_requests", 0) or 0)
        if blocked_submits:
            ui_mismatches.append(f"{blocked_submits} Submit request(s) were attempted and blocked during verification")
        if not context_id or portal.context_id != context_id:
            ui_mismatches.append("the browser context changed during verification (reload or navigation)")
        mismatches += ui_mismatches
        download_name = f"portal_download_{attempt}.yml"
        download_ref = None
        try:
            download_ref = self.registry.write_evidence(record, download_name, downloaded_text, "portal_download")
        except ValueError as exc:
            mismatches.append(f"downloaded config not stored as evidence: {exc}")
        verification = {
            "context_id": context_id,
            "code_revision": self.code_revision(),
            "service_workers": getattr(portal, "service_worker_policy", None),
            "semantic_match": semantic_match,
            "ui_checks_passed": not ui_mismatches,
            "final_review_reached": final.reached,
            "submit_clicked": False,
            "submit_requests_blocked": blocked_submits,
            "initial_submit_callbacks_blocked": int(getattr(portal, "blocked_initial_submit_callbacks", 0) or 0),
            "expected_config_sha256": record.portal_config_sha256,
            "downloaded_config_sha256": portal_config_sha256(downloaded_text),
            "downloaded_config_ref": download_ref,
            "form": form.to_evidence(),
            "final_review": final.to_evidence(),
            "portal_estimated_alerts_text": form.alert_estimate_text,
            "mismatches": mismatches,
        }
        self.registry.write_evidence_file(record, f"portal_verification_{attempt}.json", json.dumps(verification, indent=2, sort_keys=True) + "\n")
        if mismatches:
            self.registry.transition(record, S.BLOCKED, actor="orchestrator", reason="portal state does not match the canonical request; Control review needed", evidence=verification)
            raise PortalVerificationError("portal verification failed closed: " + "; ".join(mismatches))
        return self.registry.transition(record, S.PORTAL_VERIFIED, actor="orchestrator", reason="portal dry run matched; stopped before Submit", evidence=verification)

    # ------------------------------------------------------------ submission (guarded)

    def submit(self, record: AcquisitionRecord, portal: PortalAdapter, approver: Approver) -> AcquisitionRecord:
        if self.authority is None:
            raise OrchestrationError("no submission authority is configured; a live submission needs the authorized host's authority")
        if not getattr(portal, "live_submit_enabled", False):
            raise SubmissionDisabledError("portal adapter is not armed for live submission; nothing was recorded")
        self._refuse_if_claimed(record)
        try:
            with self.registry.submission_lock(record.acquisition_id):
                return self._submit_locked(record, portal, approver)
        except SubmissionLockedError as exc:
            raise OrchestrationError(str(exc)) from exc

    def _submit_locked(self, record: AcquisitionRecord, portal: PortalAdapter, approver: Approver) -> AcquisitionRecord:
        record = self.registry.load(record.acquisition_id)
        if record.state == S.SUBMITTING:
            record = self._mark_uncertain(record, "found SUBMITTING with no live submitter; outcome unknown")
            raise SubmissionUncertainError(f"{record.acquisition_id}: a previous submission's outcome is unknown; reconcile explicitly")
        if blocks_automatic_submission(record.entries):
            raise DuplicateSubmissionError(f"{record.acquisition_id} is {record.state.value}; this request may already have a Fink job, so it is never submitted automatically")
        if record.state != S.PORTAL_VERIFIED:
            raise OrchestrationError(f"{record.acquisition_id} is {record.state.value}; verify the portal in this browser context first")
        verification = record.last_entry(S.PORTAL_VERIFIED)
        verified_context = verification.evidence.get("context_id")
        if not portal.context_id or verified_context != portal.context_id:
            raise StaleVerificationError("the portal verification belongs to another browser context; verify again in this one")
        approval = approver.approve(record, self.render_review(AcquisitionPlan(record.request, record.portal_yaml, record), mode="submit"))
        if approval is None:
            raise ApprovalRefusedError("submission was not approved")
        if approval.approved_fingerprint != record.fingerprint:
            raise ApprovalRefusedError("approval does not match this request's fingerprint")

        # Immediate re-verification after approval, before anything is claimed.
        expected = compile_portal_config(record.request)
        try:
            form = portal.observe_form()
            final = portal.reach_final_review()
        except Exception as exc:  # noqa: BLE001 - nothing is claimed yet
            self.registry.transition(record, S.PORTAL_FAILURE, actor="orchestrator", reason="pre-submit observation failed", evidence={"error_type": type(exc).__name__, "error": _short(str(exc))})
            raise PortalVerificationError(f"pre-submit observation failed: {exc}") from exc
        observed_state = scientific_state_from_observation(form)
        canonical_state = scientific_state_from_config(expected)
        mismatches = check_form_observation(form, expected) + check_final_review(final)
        if observed_state.digest() != canonical_state.digest() and not mismatches:
            mismatches.append("observed scientific state differs from the canonical request")
        if portal.context_id != verified_context:
            mismatches.append("the browser context changed while awaiting approval")
        if int(getattr(portal, "blocked_submit_requests", 0) or 0):
            mismatches.append("a Submit request was attempted and blocked while awaiting approval")
        if mismatches:
            self.registry.transition(
                record, S.BLOCKED, actor="orchestrator", reason="form changed after verification; nothing was claimed or clicked",
                evidence={"presubmit_mismatches": mismatches, "context_id": portal.context_id, "observed_state": observed_state.to_dict()},
            )
            raise PortalVerificationError("form no longer matches the approved request: " + "; ".join(mismatches))

        record = self.registry.transition(
            record,
            S.APPROVED,
            actor="operator",
            reason="human approval for one portal submission, re-verified immediately",
            evidence={
                "method": approval.method,
                "approved_fingerprint": approval.approved_fingerprint,
                "verification_seq": verification.seq,
                "context_id": verified_context,
                "statement": approval.statement,
                "presubmit_state_digest": canonical_state.digest(),
            },
        )
        try:
            attempt = self.authority.claim(
                fingerprint=record.fingerprint,
                acquisition_id=record.acquisition_id,
                request_sha256=record.request_sha256,
                portal_config_sha256=record.portal_config_sha256,
                code_revision=self.code_revision(),
                browser_context_id=verified_context,
            )
        except AttemptAlreadyClaimedError as exc:
            raise DuplicateSubmissionError(f"{record.acquisition_id}: the submission authority already holds an attempt for this fingerprint") from exc
        record = self.registry.transition(
            record,
            S.SUBMITTING,
            actor="orchestrator",
            reason="permanent claim taken; activating Submit once",
            evidence={"context_id": verified_context, "approval_seq": record.last_entry(S.APPROVED).seq, "attempt_id": attempt.attempt_id, "claimed_at_utc": attempt.claimed_at_utc},
        )
        authorization = issue_submit_authorization(
            authority=self.authority,
            attempt_id=attempt.attempt_id,
            fingerprint=record.fingerprint,
            acquisition_id=record.acquisition_id,
            request_sha256=record.request_sha256,
            portal_config_sha256=record.portal_config_sha256,
            context_id=verified_context,
            expected_config=expected,
            approved_state_digest=canonical_state.digest(),
        )
        self.authority.record_status(record.fingerprint, attempt.attempt_id, "click_authorized", detail=f"context {verified_context}")
        try:
            observation = portal.submit(authorization)
        except Exception as exc:  # noqa: BLE001 - after the claim every failure is uncertain
            self._authority_note(record, "uncertain", detail=f"submit raised {type(exc).__name__}")
            record = self._mark_uncertain(record, f"submit raised {type(exc).__name__}: {_short(str(exc))}")
            raise SubmissionUncertainError(f"{record.acquisition_id}: submission outcome unknown; never retried automatically") from exc
        batch_id = (observation.batch_id or "").strip()
        topic = (observation.topic or "").strip()
        observed = f"observed batch_id={batch_id or None} topic={topic or None}"
        if observation.callback_admitted is False:
            self._authority_note(record, "guard_blocked" if observation.guard_reason and "no Submit callback" not in observation.guard_reason else "uncertain", detail=observation.guard_reason)
            record = self._mark_uncertain(record, f"Submit callback not admitted: {_short(observation.guard_reason)}", observed_batch_id=batch_id or None, observed_topic=topic or None)
            raise SubmissionUncertainError(f"{record.acquisition_id}: the Submit callback was not admitted ({observation.guard_reason}); the attempt stays claimed")
        if not observation.clicked or not BATCH_ID.fullmatch(batch_id):
            self._authority_note(record, "uncertain", detail="no batch id observed")
            record = self._mark_uncertain(record, "no batch id observed after Submit", observed_batch_id=batch_id or None, observed_topic=topic or None)
            raise SubmissionUncertainError(f"{record.acquisition_id}: no batch id observed ({observed}); reconcile explicitly")
        try:
            valid_topic = topic if LSST_TOPIC.fullmatch(topic) else None
            self.authority.record_status(record.fingerprint, attempt.attempt_id, "submitted", batch_id=batch_id, topic=valid_topic)
            record = self.registry.transition(record, S.SUBMITTED, actor="orchestrator", reason="portal returned a batch id", evidence={"batch_id": batch_id})
            if valid_topic:
                record = self.registry.transition(record, S.TOPIC_IDENTIFIED, actor="orchestrator", reason="portal returned the Kafka topic", evidence={"batch_id": batch_id, "topic": valid_topic})
        except Exception as exc:  # noqa: BLE001 - never lose what the portal returned
            try:
                current = self.registry.load(record.acquisition_id)
                if current.state == S.SUBMITTING:
                    self._mark_uncertain(current, f"recording the outcome failed: {_short(str(exc))}", observed_batch_id=batch_id, observed_topic=topic or None)
            except Exception:  # noqa: BLE001
                pass
            raise SubmissionUncertainError(f"{record.acquisition_id}: Submit succeeded ({observed}) but recording it failed: {exc}; reconcile with these values") from exc
        return record

    def recover_interrupted_submission(self, record: AcquisitionRecord) -> AcquisitionRecord:
        """Turn a SUBMITTING left by a dead process into SUBMISSION_UNCERTAIN."""
        record = self.registry.load(record.acquisition_id)
        if record.state != S.SUBMITTING:
            return record
        try:
            with self.registry.submission_lock(record.acquisition_id):
                record = self.registry.load(record.acquisition_id)
                if record.state == S.SUBMITTING:
                    self._authority_note(record, "uncertain", detail="process ended while SUBMITTING")
                    record = self._mark_uncertain(record, "process ended while SUBMITTING; outcome unknown")
        except SubmissionLockedError:
            pass  # a live submitter still owns it
        return record

    def reconcile_submission(self, record: AcquisitionRecord, *, resolution: str, statement: str, batch_id: Optional[str] = None, topic: Optional[str] = None) -> AcquisitionRecord:
        """Explicit, human-driven resolution of SUBMISSION_UNCERTAIN (or TOPIC_TIMEOUT).

        `job_found` attaches recovered batch/topic evidence, which must agree
        with anything already observed. `no_job_created` never reopens the
        request: it is refused when a batch id or topic was observed, and
        otherwise recorded while the request stays BLOCKED for Control.
        """
        if self.authority is None:
            raise OrchestrationError("reconciliation needs the submission authority")
        if not statement.strip():
            raise OrchestrationError("reconciliation needs a written statement of what was checked")
        try:
            with self.registry.submission_lock(record.acquisition_id):
                return self._reconcile_locked(record, resolution=resolution, statement=statement, batch_id=batch_id, topic=topic)
        except SubmissionLockedError as exc:
            raise OrchestrationError(str(exc)) from exc

    def _reconcile_locked(self, record: AcquisitionRecord, *, resolution: str, statement: str, batch_id: Optional[str], topic: Optional[str]) -> AcquisitionRecord:
        record = self.registry.load(record.acquisition_id)
        if record.state not in {S.SUBMISSION_UNCERTAIN, S.TOPIC_TIMEOUT}:
            raise OrchestrationError(f"{record.acquisition_id} is {record.state.value}; nothing to reconcile")
        attempt = self.authority.get(record.fingerprint)
        if attempt is None:
            raise OrchestrationError(f"{record.acquisition_id}: the submission authority holds no attempt for this request")
        observed_batch, observed_topic = self._observed_ids(record, attempt)
        if resolution == "no_job_created":
            if observed_batch or observed_topic:
                raise OrchestrationError(f"positive submission evidence was observed (batch {observed_batch}, topic {observed_topic}); a negative statement cannot override it")
            self.authority.record_status(record.fingerprint, attempt.attempt_id, "negative_statement_recorded", detail=statement)
            return self.registry.transition(
                record, S.BLOCKED, actor="reconciliation", reason="no-job statement recorded; the claimed attempt is never reopened",
                evidence={"resolution": "no_job_created", "statement": statement, "attempt_id": attempt.attempt_id, "reopened": False},
            )
        if resolution != "job_found":
            raise OrchestrationError("resolution must be job_found or no_job_created")
        batch_id = str(batch_id) if batch_id else None
        if not batch_id and not topic:
            raise OrchestrationError("job_found needs the recovered batch id and/or topic")
        for label, given, seen in (("batch id", batch_id, observed_batch), ("topic", topic, observed_topic)):
            if given and seen and given != seen:
                raise OrchestrationError(f"{label} {given} contradicts the observed {label} {seen}")
        batch_id, topic = batch_id or observed_batch, topic or observed_topic
        if topic and not LSST_TOPIC.fullmatch(topic):
            raise OrchestrationError(f"{topic!r} is not an LSST Data Transfer topic")
        if topic and self.registry.topic_owner(topic, excluding=record.acquisition_id):
            raise OrchestrationError(f"topic {topic} is already owned by another acquisition")
        evidence = {"resolution": "job_found", "statement": statement}
        if batch_id:
            evidence["batch_id"] = batch_id
        if topic:
            evidence["topic"] = topic
        try:
            self.authority.record_status(record.fingerprint, attempt.attempt_id, "job_found", batch_id=batch_id, topic=topic, detail=statement)
            return self.registry.transition(record, S.TOPIC_IDENTIFIED if topic else S.SUBMITTED, actor="reconciliation", reason="explicit reconciliation: job_found", evidence=evidence)
        except (SubmissionAuthorityError, ValueError) as exc:
            raise OrchestrationError(str(exc)) from exc

    def unblock_before_submission(self, record: AcquisitionRecord, *, statement: str) -> AcquisitionRecord:
        """Human decision to re-verify a request that was BLOCKED before any submission."""
        record = self.registry.load(record.acquisition_id)
        if record.state != S.BLOCKED:
            raise OrchestrationError(f"{record.acquisition_id} is {record.state.value}, not BLOCKED")
        if blocks_automatic_submission(record.entries):
            raise OrchestrationError(f"{record.acquisition_id} was blocked after a submission attempt; resume it with that state's own evidence, not by re-planning")
        if not statement.strip():
            raise OrchestrationError("unblocking needs a written statement of what was resolved")
        return self.registry.transition(
            record,
            S.PORTAL_PREPARED,
            actor="operator",
            reason="unblocked for a fresh portal verification",
            evidence={"portal_config_sha256": record.portal_config_sha256, "statement": statement},
        )

    # ------------------------------------------------------------ producer and handoff evidence

    def observe_producer(self, record: AcquisitionRecord, portal: PortalAdapter) -> AcquisitionRecord:
        record = self.registry.load(record.acquisition_id)
        if record.state not in {S.TOPIC_IDENTIFIED, S.PRODUCER_RUNNING, S.PRODUCER_UNCONFIRMED}:
            raise OrchestrationError(f"{record.acquisition_id} is {record.state.value}; producer observation needs an identified topic")
        observation = portal.read_producer_log()
        if not observation.available:
            if record.state == S.PRODUCER_UNCONFIRMED:
                return record
            return self.registry.transition(record, S.PRODUCER_UNCONFIRMED, actor="orchestrator", reason="portal job log unavailable; producer completion not observed", evidence={"last_state": record.state.value})
        status = classify_producer_log(observation.text, record.topic)
        if status.state == "complete":
            return self.registry.transition(
                record, S.PRODUCER_COMPLETE, actor="orchestrator", reason="terminal producer markers observed",
                evidence={"data_available_marker": True, "end_marker": True, "topic": record.topic, "lines": list(status.evidence_lines)},
            )
        if status.state == "failed":
            return self.registry.transition(record, S.BLOCKED, actor="orchestrator", reason="portal job log reports a failure", evidence={"lines": list(status.evidence_lines)})
        if status.state == "running" and record.state != S.PRODUCER_RUNNING:
            return self.registry.transition(record, S.PRODUCER_RUNNING, actor="orchestrator", reason="producer started", evidence={"marker": "Starting to send data to topic", "lines": list(status.evidence_lines)})
        return record

    def wait_for_producer(
        self,
        record: AcquisitionRecord,
        portal: PortalAdapter,
        *,
        timeout_seconds: float,
        poll_seconds: float,
        sleep: Callable[[float], None],
        monotonic: Callable[[], float] = time.monotonic,
    ) -> AcquisitionRecord:
        """Poll `observe_producer` in the same browser session until a terminal producer state or the deadline.

        Terminal means PRODUCER_COMPLETE (both canonical markers) or BLOCKED
        (the log reports a failure). At the deadline the record is left in
        whatever unresolved state the last observation produced
        (TOPIC_IDENTIFIED, PRODUCER_RUNNING or PRODUCER_UNCONFIRMED); nothing is
        inferred from Kafka or from the topic existing.
        """
        if poll_seconds <= 0 or timeout_seconds <= 0:
            raise OrchestrationError("producer polling needs a positive interval and timeout")
        deadline = monotonic() + timeout_seconds
        while True:
            record = self.observe_producer(record, portal)
            if record.state in {S.PRODUCER_COMPLETE, S.BLOCKED}:
                return record
            remaining = deadline - monotonic()
            if remaining <= 0:
                return record
            sleep(min(poll_seconds, remaining))

    def mark_producer_unconfirmed(self, record: AcquisitionRecord, *, statement: str) -> AcquisitionRecord:
        """Operator judgement that a flagged or missing producer log is inconclusive (BLOCKED -> PRODUCER_UNCONFIRMED)."""
        record = self.registry.load(record.acquisition_id)
        try:
            return self.registry.transition(record, S.PRODUCER_UNCONFIRMED, actor="operator", reason="producer outcome judged inconclusive; fallback evidence will be required", evidence={"statement": statement})
        except ValueError as exc:
            raise OrchestrationError(str(exc)) from exc

    def record_topic_metadata(self, record: AcquisitionRecord, consumer, *, fallback_evidence: Optional[str] = None, topic_partition_factory=None) -> AcquisitionRecord:
        """Query the watermarks of this acquisition's own topic (metadata only) and bind them to it.

        The topic is taken from the record, never from the caller, so another
        acquisition's watermarks cannot be relabelled as this one's.
        """
        record = self.registry.load(record.acquisition_id)
        if not record.topic:
            raise OrchestrationError(f"{record.acquisition_id} has no identified topic")
        try:
            observation = observe_topic_watermarks(consumer, record.topic, topic_partition_factory=topic_partition_factory)
            receipt = build_topic_metadata_receipt(record, observation)
        except (EvidenceError, HandoffError) as exc:
            raise OrchestrationError(str(exc)) from exc
        ref = self.registry.write_evidence(record, self._evidence_name(record, "topic_metadata"), receipt_text(receipt), "topic_metadata")
        evidence = {"receipt": receipt, "receipt_ref": ref}
        if fallback_evidence:
            evidence["fallback_evidence"] = fallback_evidence
        return self._transition(record, S.TOPIC_VERIFIED, actor="executor", reason="Kafka topic metadata pre-check", evidence=evidence)

    def record_transfer_started(self, record: AcquisitionRecord, plan: TransferPlan, *, release: str) -> AcquisitionRecord:
        """Start a transfer attempt from a plan built for this very acquisition."""
        record = self.registry.load(record.acquisition_id)
        if plan.acquisition_id != record.acquisition_id or plan.topic != record.topic or plan.raw_dir_relative != record.request.expected_raw_dir(record.topic or "unknown"):
            raise OrchestrationError(f"transfer plan for {plan.acquisition_id}/{plan.topic} does not belong to {record.acquisition_id}")
        if not plan.ready_for_transfer:
            raise OrchestrationError("transfer plan is not ready: " + "; ".join(plan.blocking_reasons))
        evidence = {
            "topic": record.topic,
            "transfer_attempt_id": uuid.uuid4().hex,
            "raw_dir": plan.raw_dir_relative,
            "command_sha256": plan.command_sha256,
            "release": release,
        }
        return self._transition(record, S.TRANSFER_RUNNING, actor="executor", reason="finkctl transfer started", evidence=evidence)

    def record_transfer_result(self, record: AcquisitionRecord, *, exit_code: int, terminal_committed: int, terminal_lag: int, log_sha256: Optional[str] = None) -> AcquisitionRecord:
        record = self.registry.load(record.acquisition_id)
        try:
            receipt = build_transfer_receipt(record, exit_code=exit_code, terminal_committed=terminal_committed, terminal_lag=terminal_lag, log_sha256=log_sha256)
        except EvidenceError as exc:
            raise OrchestrationError(str(exc)) from exc
        ref = self.registry.write_evidence(record, self._evidence_name(record, "transfer"), receipt_text(receipt), "transfer")
        target = S.TRANSFER_COMPLETE if exit_code == 0 and terminal_lag == 0 else S.TRANSFER_INTERRUPTED
        return self._transition(record, target, actor="executor", reason="finkctl transfer finished", evidence={"receipt": receipt, "receipt_ref": ref})

    def record_delivery_validation(self, record: AcquisitionRecord, *, data_root: Path) -> AcquisitionRecord:
        """Audit this acquisition's own confined raw directory and reconcile it three ways."""
        record = self.registry.load(record.acquisition_id)
        try:
            receipt, inventory, _lines = build_delivery_receipt(record, data_root)
        except (EvidenceError, OSError) as exc:
            raise OrchestrationError(f"raw delivery evidence could not be built: {exc}") from exc
        inventory_ref = self.registry.write_evidence(record, self._evidence_name(record, "inventory_summary"), receipt_text(inventory), "inventory_summary")
        receipt = {**receipt, "inventory_summary_ref": inventory_ref}
        ref = self.registry.write_evidence(record, self._evidence_name(record, "delivery"), receipt_text(receipt), "delivery")
        target = S.DELIVERY_VALIDATED if receipt["reconciliation"]["passed"] else S.RECONCILIATION_FAILED
        return self._transition(record, target, actor="executor", reason="three-way delivery reconciliation", evidence={"receipt": receipt, "receipt_ref": ref})

    # ------------------------------------------------------------ helpers

    def _refuse_if_claimed(self, record: AcquisitionRecord) -> None:
        if self.authority is None:
            return
        attempt = self.authority.get(record.fingerprint)  # SubmissionAuthorityError propagates: fail closed
        if attempt is not None:
            raise DuplicateSubmissionError(
                f"{record.acquisition_id}: the submission authority holds attempt {attempt.attempt_id} ({attempt.status}) for this fingerprint; it is never submitted again automatically"
            )

    def _authority_note(self, record: AcquisitionRecord, status: str, *, detail: str = "") -> None:
        attempt = self.authority.get(record.fingerprint) if self.authority is not None else None
        if attempt is not None:
            self.authority.record_status(record.fingerprint, attempt.attempt_id, status, detail=detail)

    def _observed_ids(self, record: AcquisitionRecord, attempt) -> tuple:
        batch, topic = record.batch_id or attempt.batch_id, record.topic or attempt.topic
        for entry in record.entries:
            if entry.to_state == S.SUBMISSION_UNCERTAIN:
                batch = batch or entry.evidence.get("observed_batch_id")
                topic = topic or entry.evidence.get("observed_topic")
        return batch, topic

    def _evidence_name(self, record: AcquisitionRecord, kind: str) -> str:
        return f"{kind}_{self.registry.evidence_count(record, kind + '_') + 1}.json"

    def _transition(self, record: AcquisitionRecord, target: S, *, actor: str, reason: str, evidence: dict) -> AcquisitionRecord:
        try:
            return self.registry.transition(record, target, actor=actor, reason=reason, evidence=evidence)
        except ValueError as exc:
            raise OrchestrationError(str(exc)) from exc

    def _mark_uncertain(self, record: AcquisitionRecord, reason: str, **observed) -> AcquisitionRecord:
        evidence = {"auto_retry": False}
        evidence.update({key: value for key, value in observed.items() if value})
        return self.registry.transition(record, S.SUBMISSION_UNCERTAIN, actor="orchestrator", reason=reason, evidence=evidence)

    def _workdir(self) -> Optional[str]:
        if self.workdir is None:
            return None
        self.workdir.mkdir(parents=True, exist_ok=True)
        return str(self.workdir)

    def _overlaps(self, request: AcquisitionRequest) -> list[dict]:
        overlaps = []
        try:
            topics = load_topic_registry(self.topic_registry_path).get("topics", [])
        except (OSError, ValueError):
            topics = []
        for entry in topics:
            start = str(entry.get("startdate") or entry.get("utc_start") or "")
            stop = str(entry.get("stopdate") or entry.get("utc_stop") or "")
            light_static = "light static" in str(entry.get("content") or entry.get("content_type") or "").lower()
            all_alerts = bool(entry.get("is_all_alert", entry.get("all_alerts"))) and not entry.get("filter") and not entry.get("filters")
            if light_static and all_alerts and start < request.stop and request.start < stop:
                overlaps.append({"topic": entry.get("topic"), "startdate": start, "stopdate": stop, "scope": entry.get("scope"), "state": entry.get("lifecycle_state")})
        for other in self.registry.list_records():
            if other.fingerprint != request.fingerprint and other.request.start < request.stop and request.start < other.request.stop:
                overlaps.append({"topic": other.topic or other.acquisition_id, "startdate": other.request.start, "stopdate": other.request.stop, "scope": other.request.scope, "state": other.state.value})
        return overlaps


def _short(text: str, limit: int = 300) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."

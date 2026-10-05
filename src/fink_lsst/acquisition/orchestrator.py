"""Acquisition orchestrator: composes planner, compiler, registry, portal and handoff.

The orchestrator owns the order of operations and the duplicate-submission
policy; the state machine and registry enforce the rules underneath it.

Submission sequence (guarded; not enabled for live use in G3B.0):

1. take the per-acquisition submission lock and reload the record;
2. a record found in SUBMITTING without a live submitter becomes
   SUBMISSION_UNCERTAIN, never a retry;
3. refuse if the request may already have produced a Fink job;
4. require a PORTAL_VERIFIED made by the same live browser session;
5. ask the approver (a human at a terminal) and check the approved fingerprint;
6. record APPROVED, then SUBMITTING (fsync) before the click;
7. click once; a batch id and topic give SUBMITTED / TOPIC_IDENTIFIED, and
   anything else, including an exception, gives SUBMISSION_UNCERTAIN.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable, Optional, Protocol, Sequence

from fink_lsst.bulk_transfer.topic_registry import DEFAULT_TOPIC_REGISTRY_PATH, load_topic_registry

from .handoff import PartitionWatermarks, expected_topic_messages, partitions_to_evidence
from .planner import AcquisitionRequest, build_acquisition_request
from .profile import LIGHT_STATIC_PACKET
from .portal import (
    PortalAdapter,
    SubmissionDisabledError,
    check_final_review,
    check_form_observation,
    classify_producer_log,
)
from .portal_config import PortalConfigError, compare_portal_configs, compile_portal_config, parse_portal_config, portal_config_sha256, render_portal_yaml
from .registry import AcquisitionRecord, AcquisitionRegistry, SubmissionLockedError
from .states import LSST_TOPIC, AcquisitionState as S, blocks_automatic_submission
from .evidence import reconcile_delivery


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


class AcquisitionOrchestrator:
    def __init__(
        self,
        registry: AcquisitionRegistry,
        *,
        topic_registry_path: Path = DEFAULT_TOPIC_REGISTRY_PATH,
        clock: Optional[Callable[[], str]] = None,
        workdir: Optional[Path] = None,
    ):
        self.registry = registry
        self.topic_registry_path = Path(topic_registry_path)
        self.clock = clock
        self.workdir = Path(workdir) if workdir else None

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
        if blocks_automatic_submission(record.entries):
            raise OrchestrationError(f"{record.acquisition_id} is {record.state.value}; portal verification is only for requests that were never submitted")
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
                form = portal.observe_form()
                final = portal.reach_final_review()
                downloaded_path = portal.download_config(Path(scratch))
                downloaded_text = downloaded_path.read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 - any adapter failure is a portal failure before submission
            failure = {"error_type": type(exc).__name__, "error": _short(str(exc)), "session_id": getattr(portal, "session_id", None)}
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
        mismatches += ui_mismatches
        download_name = f"portal_download_{attempt}.yml"
        download_file = None
        try:
            download_file = self.registry.write_evidence_file(record, download_name, downloaded_text)
        except ValueError as exc:
            mismatches.append(f"downloaded config not stored as evidence: {exc}")
        verification = {
            "session_id": portal.session_id,
            "semantic_match": semantic_match,
            "ui_checks_passed": not ui_mismatches,
            "final_review_reached": final.reached,
            "submit_clicked": False,
            "submit_requests_blocked": blocked_submits,
            "initial_submit_callbacks_blocked": int(getattr(portal, "blocked_initial_submit_callbacks", 0) or 0),
            "expected_config_sha256": record.portal_config_sha256,
            "downloaded_config_sha256": portal_config_sha256(downloaded_text),
            "downloaded_config_file": f"evidence/{download_name}" if download_file else None,
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
        if not getattr(portal, "live_submit_enabled", False):
            raise SubmissionDisabledError("portal adapter is not armed for live submission; nothing was recorded")
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
            raise OrchestrationError(f"{record.acquisition_id} is {record.state.value}; verify the portal in this session first")
        verification = record.last_entry(S.PORTAL_VERIFIED)
        if verification.evidence.get("session_id") != portal.session_id:
            raise StaleVerificationError("the portal verification belongs to another browser session; verify again in this session")
        approval = approver.approve(record, self.render_review(AcquisitionPlan(record.request, record.portal_yaml, record), mode="submit"))
        if approval is None:
            raise ApprovalRefusedError("submission was not approved")
        if approval.approved_fingerprint != record.fingerprint:
            raise ApprovalRefusedError("approval does not match this request's fingerprint")
        record = self.registry.transition(
            record,
            S.APPROVED,
            actor="operator",
            reason="human approval for one portal submission",
            evidence={
                "method": approval.method,
                "approved_fingerprint": approval.approved_fingerprint,
                "verification_seq": verification.seq,
                "session_id": portal.session_id,
                "statement": approval.statement,
            },
        )
        record = self.registry.transition(
            record,
            S.SUBMITTING,
            actor="orchestrator",
            reason="activating Submit once",
            evidence={"session_id": portal.session_id, "approval_seq": record.last_entry(S.APPROVED).seq},
        )
        try:
            observation = portal.submit()
        except Exception as exc:  # noqa: BLE001 - after SUBMITTING every failure is uncertain
            record = self._mark_uncertain(record, f"submit raised {type(exc).__name__}: {_short(str(exc))}")
            raise SubmissionUncertainError(f"{record.acquisition_id}: submission outcome unknown; never retried automatically") from exc
        batch_id = (observation.batch_id or "").strip()
        topic = (observation.topic or "").strip()
        observed = f"observed batch_id={batch_id or None} topic={topic or None}"
        if not observation.clicked or not batch_id.isdigit():
            record = self._mark_uncertain(record, "no batch id observed after Submit", observed_batch_id=batch_id or None, observed_topic=topic or None)
            raise SubmissionUncertainError(f"{record.acquisition_id}: no batch id observed ({observed}); reconcile explicitly")
        try:
            record = self.registry.transition(record, S.SUBMITTED, actor="orchestrator", reason="portal returned a batch id", evidence={"batch_id": batch_id})
            if LSST_TOPIC.fullmatch(topic):
                record = self.registry.transition(record, S.TOPIC_IDENTIFIED, actor="orchestrator", reason="portal returned the Kafka topic", evidence={"batch_id": batch_id, "topic": topic})
        except Exception as exc:  # noqa: BLE001 - never lose what the portal returned
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
                    record = self._mark_uncertain(record, "process ended while SUBMITTING; outcome unknown")
        except SubmissionLockedError:
            pass  # a live submitter still owns it
        return record

    def reconcile_submission(self, record: AcquisitionRecord, *, resolution: str, statement: str, batch_id: Optional[str] = None, topic: Optional[str] = None) -> AcquisitionRecord:
        """Explicit, human-driven resolution of SUBMISSION_UNCERTAIN (or TOPIC_TIMEOUT)."""
        record = self.registry.load(record.acquisition_id)
        if not statement.strip():
            raise OrchestrationError("reconciliation needs a written statement of what was checked")
        if record.state not in {S.SUBMISSION_UNCERTAIN, S.TOPIC_TIMEOUT}:
            raise OrchestrationError(f"{record.acquisition_id} is {record.state.value}; nothing to reconcile")
        evidence = {"resolution": resolution, "statement": statement}
        if resolution == "no_job_created":
            evidence["portal_config_sha256"] = record.portal_config_sha256
            target = S.PORTAL_PREPARED
        elif resolution == "job_found":
            if batch_id:
                evidence["batch_id"] = str(batch_id)
            if topic:
                evidence["topic"] = topic
                target = S.TOPIC_IDENTIFIED
            else:
                target = S.SUBMITTED
        else:
            raise OrchestrationError("resolution must be job_found or no_job_created")
        try:
            return self.registry.transition(record, target, actor="reconciliation", reason=f"explicit reconciliation: {resolution}", evidence=evidence)
        except ValueError as exc:
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

    def mark_producer_unconfirmed(self, record: AcquisitionRecord, *, statement: str) -> AcquisitionRecord:
        """Operator judgement that a flagged or missing producer log is inconclusive (BLOCKED -> PRODUCER_UNCONFIRMED)."""
        record = self.registry.load(record.acquisition_id)
        try:
            return self.registry.transition(record, S.PRODUCER_UNCONFIRMED, actor="operator", reason="producer outcome judged inconclusive; fallback evidence will be required", evidence={"statement": statement})
        except ValueError as exc:
            raise OrchestrationError(str(exc)) from exc

    def record_topic_metadata(self, record: AcquisitionRecord, partitions: Sequence[PartitionWatermarks], *, checked_utc: str, fallback_evidence: Optional[str] = None) -> AcquisitionRecord:
        record = self.registry.load(record.acquisition_id)
        evidence = {
            "topic": record.topic,
            "checked_utc": checked_utc,
            "partitions": partitions_to_evidence(partitions),
            "partition_count": len(partitions),
            "expected_topic_messages": expected_topic_messages(partitions),
            "meaning": "sum(high - low) of the fresh topic; transport expectation, not scientific completeness",
        }
        if fallback_evidence:
            evidence["fallback_evidence"] = fallback_evidence
        return self.registry.transition(record, S.TOPIC_VERIFIED, actor="executor", reason="Kafka topic metadata pre-check", evidence=evidence)

    def record_transfer_started(self, record: AcquisitionRecord, *, note: str) -> AcquisitionRecord:
        record = self.registry.load(record.acquisition_id)
        return self.registry.transition(record, S.TRANSFER_RUNNING, actor="executor", reason="finkctl transfer started", evidence={"topic": record.topic, "note": note})

    def record_transfer_result(self, record: AcquisitionRecord, *, exit_code: int, terminal_committed: int, terminal_lag: int) -> AcquisitionRecord:
        record = self.registry.load(record.acquisition_id)
        evidence = {"exit_code": exit_code, "terminal_committed": terminal_committed, "terminal_lag": terminal_lag}
        target = S.TRANSFER_COMPLETE if exit_code == 0 and terminal_lag == 0 else S.TRANSFER_INTERRUPTED
        return self.registry.transition(record, target, actor="executor", reason="finkctl transfer finished", evidence=evidence)

    def record_delivery_validation(self, record: AcquisitionRecord, *, delivery_summary: dict) -> AcquisitionRecord:
        """Reconcile from a `build_delivery_evidence` summary of the raw delivery.

        The local row count is the summary's, never a separate argument. The
        full small evidence (with shard digests) is stored as an evidence file;
        the transition carries the counts and that file's digest.
        """
        record = self.registry.load(record.acquisition_id)
        verified = record.last_entry(S.TOPIC_VERIFIED)
        complete = record.last_entry(S.TRANSFER_COMPLETE)
        if verified is None or complete is None or record.state != S.TRANSFER_COMPLETE:
            raise OrchestrationError(f"{record.acquisition_id} is {record.state.value}; delivery validation needs TRANSFER_COMPLETE")
        if delivery_summary.get("topic") != record.topic:
            raise OrchestrationError(f"delivery summary is for {delivery_summary.get('topic')!r}, not {record.topic}")
        result = reconcile_delivery(
            expected_topic_messages=verified.evidence["expected_topic_messages"],
            terminal_committed=complete.evidence["terminal_committed"],
            terminal_lag=complete.evidence["terminal_lag"],
            local_readable_rows=delivery_summary.get("readable_rows"),
        )
        if delivery_summary.get("unreadable_files") != 0:
            result["passed"] = False
        attempt = self.registry.evidence_count(record, "delivery_evidence_") + 1
        text = json.dumps(delivery_summary, indent=2, sort_keys=True) + "\n"
        self.registry.write_evidence_file(record, f"delivery_evidence_{attempt}.json", text)
        compact = {key: value for key, value in delivery_summary.items() if key not in {"inventory", "schema_groups"}}
        compact["inventory_sha256"] = (delivery_summary.get("inventory") or {}).get("inventory_sha256")
        compact["inventory_root_sha256"] = (delivery_summary.get("inventory") or {}).get("root_sha256")
        compact["evidence_file"] = f"evidence/delivery_evidence_{attempt}.json"
        compact["evidence_file_sha256"] = portal_config_sha256(text)
        evidence = {"reconciliation": result, "delivery_summary": compact}
        target = S.DELIVERY_VALIDATED if result["passed"] else S.RECONCILIATION_FAILED
        return self.registry.transition(record, target, actor="executor", reason="three-way delivery reconciliation", evidence=evidence)

    # ------------------------------------------------------------ helpers

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

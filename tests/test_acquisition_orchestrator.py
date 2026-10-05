"""Orchestration, idempotency and CLI tests with a deterministic fake portal (FINK-G3B.0; hardened in G3B.0-R2)."""

import io
import json
from datetime import date
from pathlib import Path

import pytest
import yaml

from _acquisition_fakes import FakeApprover, FakeKafkaConsumer, FakePortal, TopicPartition, write_raw_parquet
from fink_lsst.acquisition import cli
from fink_lsst.acquisition.authority import SubmissionAuthority
from fink_lsst.acquisition.handoff import build_transfer_plan
from fink_lsst.acquisition.orchestrator import (
    AcquisitionOrchestrator,
    ApprovalRefusedError,
    DuplicateSubmissionError,
    OrchestrationError,
    PortalVerificationError,
    StaleVerificationError,
    SubmissionUncertainError,
)
from fink_lsst.acquisition.portal import SubmissionDisabledError, classify_producer_log
from fink_lsst.acquisition.registry import AcquisitionRegistry
from fink_lsst.acquisition.states import AcquisitionState as S


AS_OF = date(2026, 10, 5)
TOPIC = "ftransfer_lsst_2026-10-05_123456"
TOPIC_REGISTRY = Path("configs/data_transfer_topics.yaml")


class Clock:
    def __init__(self):
        self.tick = 0

    def __call__(self):
        self.tick += 1
        return f"2026-10-05T00:{self.tick // 60:02d}:{self.tick % 60:02d}Z"


def _data_root(tmp_path):
    root = tmp_path / "FINK"
    for relative in ("data/raw/data_transfer", "data/processed/data_transfer", "outputs/data_transfer", "manifests", "logs"):
        (root / relative).mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture
def orchestrator(tmp_path):
    registry = AcquisitionRegistry(tmp_path / "acquisitions", clock=Clock())
    authority = SubmissionAuthority(tmp_path / "state" / "submission_authority.sqlite3")
    return AcquisitionOrchestrator(registry, authority=authority, topic_registry_path=TOPIC_REGISTRY, clock=Clock(), workdir=tmp_path / "work", code_revision=lambda: "rev-test")


def _plan(orchestrator, start="2026-02-25", stop="2026-03-25"):
    return orchestrator.plan(start, stop, as_of=AS_OF)


def _verified(orchestrator, portal=None):
    portal = portal or FakePortal(live_submit_enabled=True)
    record = orchestrator.record(_plan(orchestrator))
    record = orchestrator.verify_portal(record, portal)
    return record, portal


# ---------------------------------------------------------------- plan / review


def test_plan_writes_nothing_and_renders_the_operator_review(orchestrator, tmp_path):
    plan = _plan(orchestrator)
    review = orchestrator.render_review(plan, mode="dry_run")
    assert "Scientific window: [2026-02-25, 2026-03-25)" in review
    assert "Portal dates:       2026-02-25 → 2026-03-24 inclusive" in review
    assert "Requested dates:    28" in review
    assert "Profile:            lsst_light_static_all_alerts_v1" in review
    assert "Packet:             Light Static" in review
    assert "Filters:            none" in review
    assert "Existing request:   none" in review
    assert "Mode:               NO-SUBMIT / DRY RUN" in review
    assert "Scope:              date_range" in review
    assert "data/raw/data_transfer/date_range/2026-02-25_to_2026-03-25/<topic>" in review
    assert plan.request.fingerprint in review
    assert not (tmp_path / "acquisitions").exists()


def test_review_reports_overlapping_historical_deliveries(orchestrator):
    plan = _plan(orchestrator)
    topics = {item["topic"] for item in plan.overlaps}
    assert "ftransfer_lsst_2026-10-04_177446" in topics  # G3A night 2026-02-25
    assert "ftransfer_lsst_2026-07-29_101214" in topics  # historical Light Static week
    assert "ftransfer_lsst_2026-06-27_38507" not in topics  # Full Packet is a different product
    assert "ftransfer_lsst_2026-06-24_657339" not in topics  # tag-filtered smoke
    review = orchestrator.render_review(plan, mode="dry_run")
    assert "ftransfer_lsst_2026-10-04_177446" in review
    assert "informational" in review.lower()


def test_record_creates_planned_then_portal_prepared_and_is_idempotent(orchestrator):
    plan = _plan(orchestrator)
    record = orchestrator.record(plan)
    assert [entry.to_state for entry in record.entries] == [S.PLANNED, S.PORTAL_PREPARED]
    again = orchestrator.record(_plan(orchestrator))
    assert again.acquisition_id == record.acquisition_id
    assert len(again.entries) == 2
    review = orchestrator.render_review(_plan(orchestrator), mode="dry_run")
    assert f"Existing request:   {record.acquisition_id} (PORTAL_PREPARED)" in review


# ---------------------------------------------------------------- portal verification


def test_portal_dry_run_verifies_semantically_and_never_clicks_submit(orchestrator):
    portal = FakePortal(live_submit_enabled=False)
    record, _ = _verified(orchestrator, portal)
    assert record.state == S.PORTAL_VERIFIED
    assert "submit" not in portal.calls
    assert portal.submit_clicks == 0
    evidence = record.last_entry(S.PORTAL_VERIFIED).evidence
    assert evidence["semantic_match"] is True
    assert evidence["ui_checks_passed"] is True
    assert evidence["submit_clicked"] is False
    assert evidence["final_review"]["submit_visible"] is True
    assert evidence["portal_estimated_alerts_text"] == "21,000,000 alerts"
    files = sorted(path.name for path in (record.directory / "evidence").iterdir())
    assert files == ["portal_download_1.yml", "portal_verification_1.json"]
    downloaded = yaml.safe_load((record.directory / "evidence" / "portal_download_1.yml").read_text(encoding="utf-8"))
    assert downloaded["dates"] == {"startdate": "2026-02-25", "stopdate": "2026-03-24"}


@pytest.mark.parametrize(
    "hook, field",
    [
        ("normalize", lambda m: {**m, "dates": {"startdate": "2026-02-25", "stopdate": "2026-03-25"}}),
        ("normalize", lambda m: {**m, "content": ["Full packet"]}),
        ("normalize", lambda m: {**m, "filters": ["in_tns"]}),
        ("normalize", lambda m: {**m, "extra_cond": "diaSource.snr > 5;"}),
        ("normalize", lambda m: {**m, "catalog_filename": "targets.csv"}),
        ("display", lambda m: {**m, "content": []}),
        ("display", lambda m: {**m, "blocks": ["b_is_new"]}),
        ("display", lambda m: {**m, "dates": {"startdate": "2026-02-25", "stopdate": "2026-03-25"}}),
    ],
)
def test_portal_mismatches_block_before_submission(orchestrator, hook, field):
    portal = FakePortal(live_submit_enabled=True, **{hook: field})
    record = orchestrator.record(_plan(orchestrator))
    with pytest.raises(PortalVerificationError):
        orchestrator.verify_portal(record, portal)
    record = orchestrator.registry.load(record.acquisition_id)
    assert record.state == S.BLOCKED
    assert record.last_entry(S.BLOCKED).evidence["mismatches"]
    with pytest.raises(OrchestrationError):
        orchestrator.submit(record, portal, FakeApprover())
    assert portal.submit_clicks == 0


def test_portal_exception_is_portal_failure_and_retryable(orchestrator):
    class BrokenPortal(FakePortal):
        def upload_config(self, path):
            raise RuntimeError("upload widget not found")

    record = orchestrator.record(_plan(orchestrator))
    with pytest.raises(PortalVerificationError):
        orchestrator.verify_portal(record, BrokenPortal())
    record = orchestrator.registry.load(record.acquisition_id)
    assert record.state == S.PORTAL_FAILURE
    record = orchestrator.verify_portal(record, FakePortal())
    assert record.state == S.PORTAL_VERIFIED
    assert [entry.to_state for entry in record.entries][-3:] == [S.PORTAL_FAILURE, S.PORTAL_PREPARED, S.PORTAL_VERIFIED]


# ---------------------------------------------------------------- submission gating and idempotency


def test_submission_is_disabled_by_default_and_records_nothing(orchestrator):
    record, _ = _verified(orchestrator, FakePortal(live_submit_enabled=False))
    portal = FakePortal(live_submit_enabled=False)
    with pytest.raises(SubmissionDisabledError):
        orchestrator.submit(record, portal, FakeApprover())
    assert orchestrator.registry.load(record.acquisition_id).state == S.PORTAL_VERIFIED
    assert portal.submit_clicks == 0


def test_successful_guarded_submission_reaches_topic_identified(orchestrator):
    record, portal = _verified(orchestrator)
    approver = FakeApprover()
    record = orchestrator.submit(record, portal, approver)
    assert approver.calls == 1
    assert portal.submit_clicks == 1
    assert [entry.to_state for entry in record.entries][-4:] == [S.APPROVED, S.SUBMITTING, S.SUBMITTED, S.TOPIC_IDENTIFIED]
    assert record.topic == TOPIC
    assert record.batch_id == "41"


def test_approval_refusal_or_wrong_fingerprint_does_not_submit(orchestrator):
    record, portal = _verified(orchestrator)
    with pytest.raises(ApprovalRefusedError):
        orchestrator.submit(record, portal, FakeApprover(refuse=True))
    record = orchestrator.registry.load(record.acquisition_id)
    with pytest.raises(OrchestrationError):
        orchestrator.submit(record, portal, FakeApprover(approve_fingerprint="0" * 64))
    assert portal.submit_clicks == 0
    assert orchestrator.registry.load(record.acquisition_id).state == S.PORTAL_VERIFIED


def test_verification_from_another_browser_session_cannot_be_submitted(orchestrator):
    record, _ = _verified(orchestrator, FakePortal(name="dry-run"))
    later = FakePortal(name="later", live_submit_enabled=True)
    with pytest.raises(StaleVerificationError):
        orchestrator.submit(record, later, FakeApprover())
    assert later.submit_clicks == 0


def test_duplicate_scientific_request_is_detected(orchestrator):
    record, portal = _verified(orchestrator)
    orchestrator.submit(record, portal, FakeApprover())
    duplicate_plan = orchestrator.plan("2026-02-25", "2026-03-25", as_of=date(2026, 12, 1))
    assert duplicate_plan.existing is not None
    assert duplicate_plan.existing.acquisition_id == record.acquisition_id
    assert "TOPIC_IDENTIFIED" in orchestrator.render_review(duplicate_plan, mode="dry_run")
    assert orchestrator.record(duplicate_plan).state == S.TOPIC_IDENTIFIED


@pytest.mark.parametrize("final_state", [S.SUBMITTED, S.TOPIC_IDENTIFIED, S.DELIVERY_VALIDATED])
def test_submitted_or_delivered_requests_cannot_resubmit(orchestrator, final_state):
    behavior = "batch_only" if final_state == S.SUBMITTED else "topic"
    record, portal = _verified(orchestrator, FakePortal(live_submit_enabled=True, submit_behavior=behavior))
    record = orchestrator.submit(record, portal, FakeApprover())
    if final_state == S.DELIVERY_VALIDATED:
        record = _drive_to_delivery_validated(orchestrator, record)
    assert record.state == final_state
    second = FakePortal(live_submit_enabled=True)
    with pytest.raises(DuplicateSubmissionError):
        orchestrator.submit(record, second, FakeApprover())
    with pytest.raises(OrchestrationError):
        orchestrator.verify_portal(record, second)
    assert second.submit_clicks == 0
    assert portal.submit_clicks == 1


@pytest.mark.parametrize("behavior", ["lost", "raise"])
def test_response_loss_after_click_becomes_submission_uncertain_and_never_retries(orchestrator, behavior):
    record, portal = _verified(orchestrator, FakePortal(live_submit_enabled=True, submit_behavior=behavior))
    with pytest.raises(SubmissionUncertainError):
        orchestrator.submit(record, portal, FakeApprover())
    record = orchestrator.registry.load(record.acquisition_id)
    assert record.state == S.SUBMISSION_UNCERTAIN
    assert portal.submit_clicks == 1
    retry_portal = FakePortal(live_submit_enabled=True)
    with pytest.raises(DuplicateSubmissionError):
        orchestrator.submit(record, retry_portal, FakeApprover())
    with pytest.raises(OrchestrationError):
        orchestrator.verify_portal(record, retry_portal)
    assert retry_portal.submit_clicks == 0
    assert portal.submit_clicks == 1


def test_crash_while_submitting_is_recovered_as_uncertain_not_retried(orchestrator):
    class DiesDuringClick(FakePortal):
        def submit(self, authorization):
            raise SystemExit("process killed while clicking")

    portal = DiesDuringClick(live_submit_enabled=True)
    record, _ = _verified(orchestrator, portal)
    with pytest.raises(SystemExit):
        orchestrator.submit(record, portal, FakeApprover())
    registry = orchestrator.registry
    assert registry.load(record.acquisition_id).state == S.SUBMITTING
    recovered = orchestrator.recover_interrupted_submission(registry.load(record.acquisition_id))
    assert recovered.state == S.SUBMISSION_UNCERTAIN
    assert orchestrator.authority.get(record.fingerprint).status == "uncertain"
    with pytest.raises(DuplicateSubmissionError):
        orchestrator.submit(recovered, FakePortal(live_submit_enabled=True), FakeApprover())


def test_explicit_reconciliation_paths(orchestrator):
    record, portal = _verified(orchestrator, FakePortal(live_submit_enabled=True, submit_behavior="lost"))
    with pytest.raises(SubmissionUncertainError):
        orchestrator.submit(record, portal, FakeApprover())
    record = orchestrator.registry.load(record.acquisition_id)
    with pytest.raises(OrchestrationError):
        orchestrator.reconcile_submission(record, resolution="job_found", statement="", batch_id="41", topic=TOPIC)
    found = orchestrator.reconcile_submission(record, resolution="job_found", statement="portal batch list shows 41", batch_id="41", topic=TOPIC)
    assert found.state == S.TOPIC_IDENTIFIED
    assert found.last_entry(S.TOPIC_IDENTIFIED).actor == "reconciliation"


def test_no_job_created_reconciliation_never_reopens_the_request(orchestrator):
    """G3B.0 allowed `no_job_created` to reopen submission; R1-03 forbids it."""
    record, portal = _verified(orchestrator, FakePortal(live_submit_enabled=True, submit_behavior="lost"))
    with pytest.raises(SubmissionUncertainError):
        orchestrator.submit(record, portal, FakeApprover())
    record = orchestrator.reconcile_submission(orchestrator.registry.load(record.acquisition_id), resolution="no_job_created", statement="portal job list and Fink support show no batch for this request")
    assert record.state == S.BLOCKED
    fresh = FakePortal(name="fresh", live_submit_enabled=True)
    with pytest.raises(OrchestrationError):
        orchestrator.verify_portal(record, fresh)
    with pytest.raises(OrchestrationError):
        orchestrator.submit(record, fresh, FakeApprover())
    assert fresh.submit_clicks == 0
    assert sum(1 for entry in orchestrator.registry.load(record.acquisition_id).entries if entry.to_state == S.SUBMITTING) == 1


# ---------------------------------------------------------------- producer status


def test_producer_log_semantics():
    assert classify_producer_log("", TOPIC).state == "not_started"
    running = classify_producer_log(f"Batch 41 started\nStarting to send data to topic {TOPIC}\n", TOPIC)
    assert running.state == "running"
    almost = classify_producer_log(f"Starting to send data to topic {TOPIC}\nData available at topic: {TOPIC}\n", TOPIC)
    assert almost.state == "running"  # no End. yet
    other_topic = classify_producer_log("Data available at topic: ftransfer_lsst_2026-10-05_999\nEnd.\n", TOPIC)
    assert other_topic.state != "complete"
    complete = classify_producer_log(f"Starting to send data to topic {TOPIC}\nData available at topic: {TOPIC}\nEnd.\n", TOPIC)
    assert complete.state == "complete"
    assert all("9092" not in line for line in complete.evidence_lines)
    leaky = classify_producer_log(f"Connecting to broker kafka.example.org:9092\nData available at topic: {TOPIC}\nEnd.\n", TOPIC)
    assert leaky.state == "complete"
    assert all("example.org" not in line for line in leaky.evidence_lines)


def test_browser_loss_after_topic_capture_is_not_producer_complete(orchestrator):
    record, portal = _verified(orchestrator)
    record = orchestrator.submit(record, portal, FakeApprover())
    portal.producer_log = f"Starting to send data to topic {TOPIC}\n"
    record = orchestrator.observe_producer(record, portal)
    assert record.state == S.PRODUCER_RUNNING
    portal.producer_log = None  # browser/portal lost
    record = orchestrator.observe_producer(record, portal)
    assert record.state == S.PRODUCER_UNCONFIRMED
    portal.producer_log = f"Starting to send data to topic {TOPIC}\nData available at topic: {TOPIC}\nEnd.\n"
    record = orchestrator.observe_producer(record, portal)
    assert record.state == S.PRODUCER_COMPLETE


def _to_transfer_complete(orchestrator, record, highs=(6, 4), committed=10):
    if record.state == S.SUBMITTED:
        raise AssertionError("needs a topic")
    record = orchestrator.observe_producer(record, FakePortal(producer_log=f"Data available at topic: {TOPIC}\nEnd.\n"))
    record = orchestrator.record_topic_metadata(record, FakeKafkaConsumer({TOPIC: highs}), topic_partition_factory=TopicPartition)
    data_root = _data_root(orchestrator.workdir.parent)
    record = orchestrator.record_transfer_started(record, build_transfer_plan(record, data_root), release="rev-test")
    return orchestrator.record_transfer_result(record, exit_code=0, terminal_committed=committed, terminal_lag=0, log_sha256="f" * 64)


def _drive_to_delivery_validated(orchestrator, record, rows=(6, 4)):
    record = _to_transfer_complete(orchestrator, record)
    data_root = _data_root(orchestrator.workdir.parent)
    write_raw_parquet(data_root / record.request.expected_raw_dir(TOPIC), rows)
    return orchestrator.record_delivery_validation(record, data_root=data_root)


def test_integration_through_delivery_validated_with_fake_components(orchestrator):
    record, portal = _verified(orchestrator)
    record = orchestrator.submit(record, portal, FakeApprover())
    record = _drive_to_delivery_validated(orchestrator, record)
    assert record.state == S.DELIVERY_VALIDATED
    assert record.last_entry(S.TOPIC_VERIFIED).evidence["receipt"]["expected_topic_messages"] == 10
    validated = record.last_entry(S.DELIVERY_VALIDATED).evidence
    assert validated["receipt"]["readable_rows"] == 10
    assert (record.directory / validated["receipt_ref"]["path"]).is_file()
    assert (record.directory / validated["receipt"]["inventory_summary_ref"]["path"]).is_file()
    assert "shards" not in json.dumps(validated)  # shard digests live in the inventory summary, not the log


def test_unreadable_raw_files_prevent_validation(orchestrator):
    record, portal = _verified(orchestrator)
    record = orchestrator.submit(record, portal, FakeApprover())
    record = _to_transfer_complete(orchestrator, record)
    data_root = _data_root(orchestrator.workdir.parent)
    raw = write_raw_parquet(data_root / record.request.expected_raw_dir(TOPIC), [6, 4])
    (raw / "part-9.parquet").write_bytes(b"not parquet")
    record = orchestrator.record_delivery_validation(record, data_root=data_root)
    assert record.state == S.RECONCILIATION_FAILED
    assert record.last_entry(S.RECONCILIATION_FAILED).evidence["receipt"]["unreadable_files"] == 1


def test_flagged_producer_log_can_be_judged_inconclusive_but_not_complete(orchestrator):
    record, portal = _verified(orchestrator)
    record = orchestrator.submit(record, portal, FakeApprover())
    record = orchestrator.observe_producer(record, FakePortal(producer_log=f"WARN ERROR in an unrelated executor\nData available at topic: {TOPIC}\nEnd.\n"))
    assert record.state == S.BLOCKED
    record = orchestrator.mark_producer_unconfirmed(record, statement="Spark ERROR line is a known benign warning; awaiting Fink confirmation")
    assert record.state == S.PRODUCER_UNCONFIRMED


def test_reconciliation_shortfall_is_recorded_not_validated(orchestrator):
    record, portal = _verified(orchestrator)
    record = orchestrator.submit(record, portal, FakeApprover())
    record = _drive_to_delivery_validated(orchestrator, record, rows=(5, 4))
    assert record.state == S.RECONCILIATION_FAILED
    assert record.last_entry(S.RECONCILIATION_FAILED).evidence["receipt"]["reconciliation"]["local_readable_rows"] == 9


# ---------------------------------------------------------------- CLI


def _run_cli(argv, tmp_path, capsys, **kwargs):
    kwargs.setdefault("authority_path", tmp_path / "state" / "submission_authority.sqlite3")
    code = cli.main(argv, registry_root=tmp_path / "acquisitions", topic_registry_path=TOPIC_REGISTRY, as_of=AS_OF, **kwargs)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_cli_acquire_defaults_to_review_only(tmp_path, capsys):
    code, out, _err = _run_cli(["acquire", "--start", "2026-02-25", "--stop", "2026-03-25"], tmp_path, capsys)
    assert code == 0
    assert "Scientific window: [2026-02-25, 2026-03-25)" in out
    assert "Portal dates:       2026-02-25 → 2026-03-24 inclusive" in out
    assert "Mode:               NO-SUBMIT / DRY RUN" in out
    assert not (tmp_path / "acquisitions").exists()


def test_cli_record_then_status(tmp_path, capsys):
    code, out, _err = _run_cli(["acquire", "--start", "2026-02-25", "--stop", "2026-03-25", "--record"], tmp_path, capsys)
    assert code == 0
    records = list((tmp_path / "acquisitions").iterdir())
    assert len(records) == 1
    code, out, _err = _run_cli(["status"], tmp_path, capsys)
    assert code == 0
    assert records[0].name in out and "PORTAL_PREPARED" in out
    code, out, _err = _run_cli(["status", "--id", records[0].name], tmp_path, capsys)
    assert code == 0
    assert "PLANNED" in out and "PORTAL_PREPARED" in out


@pytest.mark.parametrize(
    "argv",
    [
        ["acquire", "--start", "2026-03-25", "--stop", "2026-02-25"],
        ["acquire", "--start", "2026-02-25", "--stop", "2026-02-25"],
        ["acquire", "--start", "2026-02-30", "--stop", "2026-03-25"],
        ["acquire", "--start", "20260225", "--stop", "2026-03-25"],
    ],
)
def test_cli_rejects_invalid_windows(tmp_path, capsys, argv):
    code, out, err = _run_cli(argv, tmp_path, capsys)
    assert code == 2
    assert "error" in (out + err).lower()
    assert not (tmp_path / "acquisitions").exists()


@pytest.mark.parametrize(
    "extra",
    [["--filters", "in_tns"], ["--packet", "Full packet"], ["--extra-cond", "x"], ["--catalog", "c.csv"], ["--blocks", "b_is_new"], ["--profile", "x"], ["--registry", "/tmp/x"]],
)
def test_cli_exposes_no_profile_or_registry_overrides(tmp_path, capsys, extra):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["acquire", "--start", "2026-02-25", "--stop", "2026-03-25", *extra], registry_root=tmp_path / "acquisitions", topic_registry_path=TOPIC_REGISTRY, as_of=AS_OF)
    assert excinfo.value.code == 2


def test_cli_submit_is_refused_in_this_gate_before_any_side_effect(tmp_path, capsys, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("no browser may be opened when submission is refused")

    monkeypatch.setattr(cli, "_make_playwright_portal", forbidden)
    code, out, err = _run_cli(["acquire", "--start", "2026-02-25", "--stop", "2026-03-25", "--portal-check", "--submit"], tmp_path, capsys)
    assert code == 2
    assert "live submission is not enabled" in (out + err)
    assert not (tmp_path / "acquisitions").exists()
    assert cli.LIVE_SUBMISSION_ENABLED is False


def test_cli_portal_check_uses_adapter_and_stops_before_submit(tmp_path, capsys, monkeypatch):
    portal = FakePortal(live_submit_enabled=False)
    monkeypatch.setattr(cli, "_make_playwright_portal", lambda **kwargs: portal)
    code, out, _err = _run_cli(["acquire", "--start", "2026-02-25", "--stop", "2026-03-25", "--portal-check"], tmp_path, capsys)
    assert code == 0, out
    assert "PORTAL_VERIFIED" in out
    assert "Submit was NOT activated" in out
    assert portal.submit_clicks == 0 and "submit" not in portal.calls
    assert portal.calls[-1] == "close"


def test_interactive_approver_requires_tty_and_exact_acquisition_id(orchestrator, monkeypatch):
    record, _ = _verified(orchestrator)

    class FakeStdin(io.StringIO):
        def __init__(self, text, tty):
            super().__init__(text)
            self._tty = tty

        def isatty(self):
            return self._tty

    approver = cli.InteractiveApprover(stdin=FakeStdin(record.acquisition_id + "\n", tty=False), stdout=io.StringIO())
    assert approver.approve(record, "review") is None
    approver = cli.InteractiveApprover(stdin=FakeStdin("yes\n", tty=True), stdout=io.StringIO())
    assert approver.approve(record, "review") is None
    approver = cli.InteractiveApprover(stdin=FakeStdin(record.acquisition_id + "\n", tty=True), stdout=io.StringIO())
    approval = approver.approve(record, "review")
    assert approval.approved_fingerprint == record.fingerprint
    assert approval.method == "interactive_tty"


def test_cli_handoff_prints_plan_for_identified_topic(tmp_path, capsys, monkeypatch, orchestrator):
    record, portal = _verified(orchestrator)
    record = orchestrator.submit(record, portal, FakeApprover())
    data_root = tmp_path / "FINK"
    for relative in ("data/raw/data_transfer", "manifests", "logs"):
        (data_root / relative).mkdir(parents=True)
    monkeypatch.setenv("FINK_LSST_DATA_ROOT", str(data_root))
    code, out, _err = _run_cli(["handoff", "--id", record.acquisition_id], tmp_path, capsys)
    assert code == 0, out
    payload = json.loads(out)
    assert payload["transfer_command"][:2] == ["finkctl", "transfer"]
    assert payload["working_dir"] == str(data_root / "manifests" / TOPIC)
    assert payload["executes_nothing"] is True


def test_unblock_is_only_for_requests_blocked_before_submission(orchestrator):
    portal = FakePortal(normalize=lambda m: {**m, "content": ["Full packet"]})
    record = orchestrator.record(_plan(orchestrator))
    with pytest.raises(PortalVerificationError):
        orchestrator.verify_portal(record, portal)
    blocked = orchestrator.registry.load(record.acquisition_id)
    with pytest.raises(OrchestrationError):
        orchestrator.unblock_before_submission(blocked, statement="  ")
    record = orchestrator.unblock_before_submission(blocked, statement="portal content selector fixed")
    assert record.state == S.PORTAL_PREPARED
    assert orchestrator.verify_portal(record, FakePortal()).state == S.PORTAL_VERIFIED

    submitted, portal = _verified_other_window(orchestrator)
    submitted = orchestrator.submit(submitted, portal, FakeApprover())
    submitted = orchestrator.registry.transition(submitted, S.BLOCKED, actor="executor", reason="kafka unreachable", evidence={})
    with pytest.raises(OrchestrationError, match="after a submission"):
        orchestrator.unblock_before_submission(submitted, statement="retry")


def _verified_other_window(orchestrator):
    portal = FakePortal(live_submit_enabled=True)
    record = orchestrator.record(orchestrator.plan("2026-03-25", "2026-04-25", as_of=AS_OF))
    return orchestrator.verify_portal(record, portal), portal


def test_cli_unblock(tmp_path, capsys):
    registry = AcquisitionRegistry(tmp_path / "acquisitions", clock=Clock())
    orchestrator = AcquisitionOrchestrator(registry, topic_registry_path=TOPIC_REGISTRY, workdir=tmp_path / "work")
    record = orchestrator.record(_plan(orchestrator))
    with pytest.raises(PortalVerificationError):
        orchestrator.verify_portal(record, FakePortal(display=lambda m: {**m, "filters": ["in_tns"]}))
    code, out, _err = _run_cli(["unblock", "--id", record.acquisition_id, "--statement", "fixed"], tmp_path, capsys)
    assert code == 0 and "PORTAL_PREPARED" in out


def test_an_approval_from_an_ended_session_is_withdrawn_by_reverification(orchestrator):
    record, portal = _verified(orchestrator)
    record = orchestrator.registry.transition(
        record,
        S.APPROVED,
        actor="operator",
        reason="approved, then the process ended before SUBMITTING",
        evidence={
            "method": "interactive_tty",
            "approved_fingerprint": record.fingerprint,
            "verification_seq": record.last_entry(S.PORTAL_VERIFIED).seq,
            "context_id": portal.context_id,
            "statement": "typed acquisition id",
            "presubmit_state_digest": "a" * 64,
        },
    )
    later = FakePortal(name="later", live_submit_enabled=True)
    with pytest.raises(OrchestrationError):
        orchestrator.submit(record, later, FakeApprover())
    record = orchestrator.verify_portal(record, later)
    assert [entry.to_state for entry in record.entries][-3:] == [S.APPROVED, S.PORTAL_PREPARED, S.PORTAL_VERIFIED]
    record = orchestrator.submit(record, later, FakeApprover())
    assert record.state == S.TOPIC_IDENTIFIED
    assert later.submit_clicks == 1 and portal.submit_clicks == 0


def _git(cwd, *args):
    import subprocess

    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True)


def test_registry_durability_requires_a_committed_pushed_record(tmp_path):
    from fink_lsst.acquisition.registry import registry_durability_problems

    repo = tmp_path / "repo"
    repo.mkdir()
    registry = AcquisitionRegistry(repo / "configs" / "acquisitions", clock=Clock())
    orchestrator = AcquisitionOrchestrator(registry, topic_registry_path=TOPIC_REGISTRY, workdir=tmp_path / "work")
    record = orchestrator.record(_plan(orchestrator))
    assert registry_durability_problems(record.directory) == ["the registry is not inside a Git checkout"]
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "-c", "user.email=t@example.org", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "base")
    problems = registry_durability_problems(record.directory)
    assert "the record is not committed" in problems and "the branch has no upstream; push the record first" in problems
    _git(repo, "add", "configs")
    _git(repo, "-c", "user.email=t@example.org", "-c", "user.name=t", "commit", "-q", "-m", "record")
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", str(remote))
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "-q", "-u", "origin", "main")
    assert registry_durability_problems(record.directory) == []
    orchestrator.verify_portal(registry.load(record.acquisition_id), FakePortal())
    assert registry_durability_problems(record.directory) == ["the record has uncommitted changes"]


def test_cli_submit_path_refuses_a_non_durable_record_before_opening_the_portal(tmp_path, capsys, monkeypatch):
    opened = []

    class TrackingPortal(FakePortal):
        def open(self):
            opened.append(True)

    monkeypatch.setattr(cli, "LIVE_SUBMISSION_ENABLED", True)
    monkeypatch.setattr(cli, "_make_playwright_portal", lambda **kwargs: TrackingPortal(live_submit_enabled=kwargs["live_submit_enabled"]))
    code, _out, err = _run_cli(["acquire", "--start", "2026-02-25", "--stop", "2026-03-25", "--portal-check", "--submit"], tmp_path, capsys)
    assert code == 2
    assert "not inside a Git checkout" in err
    assert opened == []

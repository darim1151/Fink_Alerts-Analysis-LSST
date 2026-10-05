"""Bounded producer-log polling in the submitting browser session (FINK-G3B.1A); fakes only."""

from datetime import date
from pathlib import Path

import pytest

from _acquisition_fakes import FakeApprover, FakePortal
from fink_lsst.acquisition.authority import SubmissionAuthority
from fink_lsst.acquisition.orchestrator import AcquisitionOrchestrator, OrchestrationError
from fink_lsst.acquisition.registry import AcquisitionRegistry
from fink_lsst.acquisition.states import AcquisitionState as S


AS_OF = date(2026, 10, 5)
TOPIC = "ftransfer_lsst_2026-10-05_123456"
TOPIC_REGISTRY = Path("configs/data_transfer_topics.yaml")
STARTED = f"Starting to send data to topic {TOPIC}\n"
COMPLETE = f"{STARTED}Data available at topic: {TOPIC}\nEnd.\n"


class Clock:
    def __init__(self):
        self.tick = 0

    def __call__(self):
        self.tick += 1
        return f"2026-10-05T00:{self.tick // 60:02d}:{self.tick % 60:02d}Z"


class ScriptedPortal(FakePortal):
    """Returns one scripted producer log per read; None means the log is unavailable."""

    def __init__(self, logs, **kwargs):
        super().__init__(live_submit_enabled=True, **kwargs)
        self._logs = list(logs)

    def read_producer_log(self):
        if self._logs:
            self.producer_log = self._logs.pop(0)
        return super().read_producer_log()


class FakeTime:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def orchestrator(tmp_path):
    registry = AcquisitionRegistry(tmp_path / "acquisitions", clock=Clock())
    authority = SubmissionAuthority(tmp_path / "state" / "submission_authority.sqlite3")
    return AcquisitionOrchestrator(registry, authority=authority, topic_registry_path=TOPIC_REGISTRY, clock=Clock(), workdir=tmp_path / "work", code_revision=lambda: "rev-test")


def _submitted(orchestrator, logs):
    portal = ScriptedPortal(logs)
    record = orchestrator.record(orchestrator.plan("2026-02-25", "2026-03-25", as_of=AS_OF))
    record = orchestrator.verify_portal(record, portal)
    record = orchestrator.submit(record, portal, FakeApprover())
    assert record.state == S.TOPIC_IDENTIFIED
    return record, portal


def _wait(orchestrator, record, portal, clock, timeout=600.0, poll=30.0):
    return orchestrator.wait_for_producer(record, portal, timeout_seconds=timeout, poll_seconds=poll, sleep=clock.sleep, monotonic=clock.monotonic)


def test_polling_reaches_producer_complete_only_with_both_canonical_markers(orchestrator):
    almost = f"{STARTED}Data available at topic: {TOPIC}\n"
    record, portal = _submitted(orchestrator, ["", STARTED, almost, COMPLETE])
    clock = FakeTime()
    record = _wait(orchestrator, record, portal, clock)
    assert record.state == S.PRODUCER_COMPLETE
    assert portal.calls.count("producer_log") == 4
    assert clock.sleeps == [30.0, 30.0, 30.0]
    assert [entry.to_state for entry in record.entries][-2:] == [S.PRODUCER_RUNNING, S.PRODUCER_COMPLETE]
    assert portal.submit_clicks == 1


def test_polling_is_bounded_and_leaves_a_running_producer_unresolved(orchestrator):
    record, portal = _submitted(orchestrator, [STARTED] * 100)
    clock = FakeTime()
    record = _wait(orchestrator, record, portal, clock, timeout=100.0, poll=30.0)
    assert record.state == S.PRODUCER_RUNNING
    assert sum(clock.sleeps) == pytest.approx(100.0)
    assert max(clock.sleeps) <= 30.0
    assert portal.calls.count("producer_log") == 5


def test_an_unavailable_log_stays_producer_unconfirmed(orchestrator):
    record, portal = _submitted(orchestrator, [None] * 100)
    record = _wait(orchestrator, record, portal, FakeTime(), timeout=90.0)
    assert record.state == S.PRODUCER_UNCONFIRMED
    assert [entry.to_state for entry in record.entries].count(S.PRODUCER_UNCONFIRMED) == 1


def test_a_marker_for_another_topic_or_without_end_is_not_completion(orchestrator):
    other = "Data available at topic: ftransfer_lsst_2026-10-05_999\nEnd.\n"
    record, portal = _submitted(orchestrator, [other, f"Data available at topic: {TOPIC}\n"] * 10)
    record = _wait(orchestrator, record, portal, FakeTime(), timeout=300.0)
    assert record.state == S.PRODUCER_RUNNING
    assert record.last_entry(S.PRODUCER_COMPLETE) is None


def test_a_failed_producer_log_stops_polling_blocked(orchestrator):
    record, portal = _submitted(orchestrator, [STARTED, f"{STARTED}Traceback (most recent call last):\n", COMPLETE])
    record = _wait(orchestrator, record, portal, FakeTime())
    assert record.state == S.BLOCKED
    assert portal.calls.count("producer_log") == 2


def test_a_browser_loss_mid_wait_can_recover_only_through_the_markers(orchestrator):
    record, portal = _submitted(orchestrator, [STARTED, None, COMPLETE])
    record = _wait(orchestrator, record, portal, FakeTime())
    assert [entry.to_state for entry in record.entries][-3:] == [S.PRODUCER_RUNNING, S.PRODUCER_UNCONFIRMED, S.PRODUCER_COMPLETE]


def test_polling_needs_a_positive_interval_and_an_identified_topic(orchestrator):
    record, portal = _submitted(orchestrator, [STARTED])
    with pytest.raises(OrchestrationError):
        _wait(orchestrator, record, portal, FakeTime(), poll=0)
    with pytest.raises(OrchestrationError):
        _wait(orchestrator, record, portal, FakeTime(), timeout=0)
    verified_only = orchestrator.record(orchestrator.plan("2026-03-25", "2026-04-25", as_of=AS_OF))
    with pytest.raises(OrchestrationError, match="identified topic"):
        _wait(orchestrator, verified_only, portal, FakeTime())


def test_producer_evidence_carries_no_endpoint_or_secret_from_the_log(orchestrator):
    leaky = f"Connecting to broker kafka.example.org:9092 sasl.password=hunter2\n{COMPLETE}"
    record, portal = _submitted(orchestrator, [leaky])
    record = _wait(orchestrator, record, portal, FakeTime())
    assert record.state == S.PRODUCER_COMPLETE
    stored = "\n".join(path.read_text(encoding="utf-8") for path in record.directory.rglob("*") if path.is_file())
    assert "kafka.example.org" not in stored and "hunter2" not in stored

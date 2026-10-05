"""FINK-G3B.1A production activation: the explicit live-submission opt-in and the CLI producer wait.

Everything here runs offline against the deterministic fake portal; no
browser is opened, no Fink job is submitted and Kafka is never contacted.
The real Playwright factory is replaced in every test that reaches it.
"""

import io
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from _acquisition_fakes import FakePortal
from fink_lsst.acquisition import cli
from fink_lsst.acquisition.authority import SubmissionAuthority
from fink_lsst.acquisition.portal_playwright import PlaywrightPortalAdapter
from fink_lsst.acquisition.registry import AcquisitionRegistry
from fink_lsst.acquisition.states import AcquisitionState as S


AS_OF = date(2026, 10, 5)
TOPIC = "ftransfer_lsst_2026-10-05_123456"
TOPIC_REGISTRY = Path("configs/data_transfer_topics.yaml")
MONTH1 = ["acquire", "--start", "2026-02-25", "--stop", "2026-03-25"]
SUBMIT = [*MONTH1, "--portal-check", "--submit"]
STARTED = f"Starting to send data to topic {TOPIC}\n"
COMPLETE = f"{STARTED}Data available at topic: {TOPIC}\nEnd.\n"
SECRETS = ("kafka.example.org", "9092", "hunter2", "sasl.password")


class Tty(io.StringIO):
    def __init__(self, text, tty=True):
        super().__init__(text)
        self._tty = tty

    def isatty(self):
        return self._tty


class FakeTime:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class ScriptedPortal(FakePortal):
    """Fake portal whose producer log advances one scripted step per read (None = unavailable)."""

    def __init__(self, logs=(), authority_path=None, **kwargs):
        super().__init__(**kwargs)
        self._logs = list(logs)
        self._authority_path = authority_path
        self.authority_status_at_click = None

    def submit(self, authorization):
        if self._authority_path is not None:
            attempt = SubmissionAuthority(self._authority_path).get(authorization.fingerprint)
            self.authority_status_at_click = attempt.status if attempt else None
        return super().submit(authorization)

    def read_producer_log(self):
        if self._logs:
            self.producer_log = self._logs.pop(0)
        return super().read_producer_log()


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A Git checkout with a pushed upstream, holding the registry, plus a private authority and fake clock."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "-c", "user.email=t@example.org", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "base")
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", str(remote))
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "-q", "-u", "origin", "main")
    clock = FakeTime()
    monkeypatch.setattr(cli, "time", clock)
    return {
        "repo": repo,
        "registry_root": repo / "configs" / "acquisitions",
        "authority_path": tmp_path / "state" / "submission_authority.sqlite3",
        "clock": clock,
    }


def _run(env, argv, capsys):
    code = cli.main(argv, registry_root=env["registry_root"], topic_registry_path=TOPIC_REGISTRY, as_of=AS_OF, authority_path=env["authority_path"])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _durable_record(env, capsys):
    code, out, _err = _run(env, [*MONTH1, "--record"], capsys)
    assert code == 0, out
    _git(env["repo"], "add", "configs")
    _git(env["repo"], "-c", "user.email=t@example.org", "-c", "user.name=t", "commit", "-q", "-m", "record")
    _git(env["repo"], "push", "-q")
    return re.search(r"Recorded:\s+(\S+)", out).group(1)


def _install_portal(monkeypatch, portal, made=None):
    def factory(**kwargs):
        if made is not None:
            made.append(kwargs)
        portal.live_submit_enabled = kwargs["live_submit_enabled"]
        return portal

    monkeypatch.setattr(cli, "_make_playwright_portal", factory)


def _arm(monkeypatch, acquisition_id, tty=True):
    monkeypatch.setenv(cli.LIVE_SUBMISSION_ENV, "1")
    monkeypatch.setattr(sys, "stdin", Tty(acquisition_id + "\n", tty=tty))


def _registry(env):
    return AcquisitionRegistry(env["registry_root"])


# ---------------------------------------------------------------- opt-in


@pytest.mark.parametrize("value", [None, "", "0", "true", "yes", "on", " 1", "1 ", "01", "TRUE"])
def test_submission_is_refused_without_the_exact_opt_in_before_any_side_effect(env, capsys, monkeypatch, value):
    if value is not None:
        monkeypatch.setenv(cli.LIVE_SUBMISSION_ENV, value)

    def forbidden(**kwargs):
        raise AssertionError("no browser may be opened when submission is refused")

    monkeypatch.setattr(cli, "_make_playwright_portal", forbidden)
    code, out, err = _run(env, SUBMIT, capsys)
    assert code == 2
    assert "live submission is not enabled" in err and cli.LIVE_SUBMISSION_ENV in err
    assert not env["registry_root"].exists()
    assert not env["authority_path"].exists()


def test_live_submission_enabled_accepts_only_exactly_one():
    assert cli.live_submission_enabled({}) is False
    assert cli.live_submission_enabled({cli.LIVE_SUBMISSION_ENV: "1"}) is True
    assert cli.live_submission_enabled() is False  # the test environment never carries the opt-in
    assert PlaywrightPortalAdapter().live_submit_enabled is False


def test_the_opt_in_never_arms_a_portal_check_without_submit(env, capsys, monkeypatch):
    monkeypatch.setenv(cli.LIVE_SUBMISSION_ENV, "1")
    made, portal = [], ScriptedPortal()
    _install_portal(monkeypatch, portal, made)
    code, out, _err = _run(env, [*MONTH1, "--portal-check"], capsys)
    assert code == 0, out
    assert made == [{"headless": True, "live_submit_enabled": False}]
    assert "Submit was NOT activated" in out
    assert portal.submit_clicks == 0 and not env["authority_path"].exists()


def test_the_opt_in_still_requires_a_durable_registry_record(env, capsys, monkeypatch):
    monkeypatch.setenv(cli.LIVE_SUBMISSION_ENV, "1")
    portal = ScriptedPortal()
    _install_portal(monkeypatch, portal)
    code, _out, err = _run(env, SUBMIT, capsys)
    assert code == 2
    assert "durable" in err
    assert "open" not in portal.calls and portal.submit_clicks == 0
    assert not env["authority_path"].exists()


@pytest.mark.parametrize("typed, tty", [("wrong-id", True), (None, False)])
def test_the_opt_in_still_requires_the_typed_acquisition_id_at_a_terminal(env, capsys, monkeypatch, typed, tty):
    acquisition_id = _durable_record(env, capsys)
    _arm(monkeypatch, typed or acquisition_id, tty=tty)
    portal = ScriptedPortal()
    _install_portal(monkeypatch, portal)
    code, out, err = _run(env, SUBMIT, capsys)
    assert code == 1
    assert "not approved" in err
    assert portal.submit_clicks == 0 and "submit" not in portal.calls
    assert not env["authority_path"].exists() or SubmissionAuthority(env["authority_path"]).get(_registry(env).load(acquisition_id).fingerprint) is None
    assert _registry(env).load(acquisition_id).state == S.PORTAL_VERIFIED
    assert portal.calls[-1] == "close"


def test_an_armed_submission_claims_the_authority_before_the_one_submit_and_prints_the_handoff(env, capsys, monkeypatch):
    acquisition_id = _durable_record(env, capsys)
    _arm(monkeypatch, acquisition_id)
    portal = ScriptedPortal(authority_path=env["authority_path"])
    made = []
    _install_portal(monkeypatch, portal, made)
    code, out, err = _run(env, SUBMIT, capsys)
    assert code == 0, err
    assert made == [{"headless": True, "live_submit_enabled": True}]
    assert portal.submit_clicks == 1
    assert portal.authority_status_at_click == "click_authorized"
    record = _registry(env).load(acquisition_id)
    assert record.state == S.TOPIC_IDENTIFIED
    states = [entry.to_state for entry in record.entries]
    assert states[-5:] == [S.PORTAL_VERIFIED, S.APPROVED, S.SUBMITTING, S.SUBMITTED, S.TOPIC_IDENTIFIED]
    events = [event["status"] for event in SubmissionAuthority(env["authority_path"]).events(record.fingerprint)]
    assert events == ["claimed", "click_authorized", "submitted"]
    assert f"acquisition ID:    {acquisition_id}" in out
    assert "batch ID:          41" in out
    assert f"Kafka topic:       {TOPIC}" in out
    assert "state:             TOPIC_IDENTIFIED" in out
    assert f"Arnor raw path:    /astro/store/shire/FINK/{record.request.expected_raw_dir(TOPIC)}" in out
    assert "transfer command:  not generated at TOPIC_IDENTIFIED" in out
    assert f"fink-lsst handoff --id {acquisition_id}" in out
    assert "producer_log" not in portal.calls  # no polling without --wait-producer


def test_a_second_armed_run_never_submits_again(env, capsys, monkeypatch):
    acquisition_id = _durable_record(env, capsys)
    _arm(monkeypatch, acquisition_id)
    _install_portal(monkeypatch, ScriptedPortal())
    assert _run(env, SUBMIT, capsys)[0] == 0
    _git(env["repo"], "add", "configs")
    _git(env["repo"], "-c", "user.email=t@example.org", "-c", "user.name=t", "commit", "-q", "-m", "submitted")
    _git(env["repo"], "push", "-q")
    _arm(monkeypatch, acquisition_id)
    again = ScriptedPortal()
    _install_portal(monkeypatch, again)
    code, _out, err = _run(env, SUBMIT, capsys)
    assert code == 1
    assert "never submitted again" in err or "already" in err
    assert again.submit_clicks == 0 and "submit" not in again.calls
    events = [event["status"] for event in SubmissionAuthority(env["authority_path"]).events(_registry(env).load(acquisition_id).fingerprint)]
    assert events.count("claimed") == 1


# ---------------------------------------------------------------- --wait-producer


def test_wait_producer_reaches_producer_complete_only_with_the_canonical_markers(env, capsys, monkeypatch):
    acquisition_id = _durable_record(env, capsys)
    _arm(monkeypatch, acquisition_id)
    almost = f"{STARTED}Data available at topic: {TOPIC}\n"
    portal = ScriptedPortal(["", STARTED, almost, COMPLETE])
    _install_portal(monkeypatch, portal)
    code, out, err = _run(env, [*SUBMIT, "--wait-producer"], capsys)
    assert code == 0, err
    record = _registry(env).load(acquisition_id)
    assert record.state == S.PRODUCER_COMPLETE
    assert portal.calls.count("producer_log") == 4
    assert env["clock"].sleeps == [cli.PRODUCER_POLL_SECONDS] * 3
    assert "Producer: PRODUCER_COMPLETE" in out
    assert "state:             PRODUCER_COMPLETE" in out
    assert portal.submit_clicks == 1
    assert portal.calls.index("submit") < portal.calls.index("producer_log") < portal.calls.index("close")
    assert portal.calls.count("open") == 1  # the same browser session throughout


def test_wait_producer_is_bounded_and_keeps_an_unavailable_log_unconfirmed(env, capsys, monkeypatch):
    acquisition_id = _durable_record(env, capsys)
    _arm(monkeypatch, acquisition_id)
    portal = ScriptedPortal([None] * 1000)
    _install_portal(monkeypatch, portal)
    code, out, err = _run(env, [*SUBMIT, "--wait-producer", "--producer-timeout-hours", "0.1"], capsys)
    assert code == 4
    assert _registry(env).load(acquisition_id).state == S.PRODUCER_UNCONFIRMED
    assert sum(env["clock"].sleeps) == pytest.approx(360.0)
    assert portal.calls.count("producer_log") == 13
    assert "completion NOT observed" in err
    assert "state:             PRODUCER_UNCONFIRMED" in out


def test_wait_producer_keeps_a_running_producer_unresolved_at_the_deadline(env, capsys, monkeypatch):
    acquisition_id = _durable_record(env, capsys)
    _arm(monkeypatch, acquisition_id)
    _install_portal(monkeypatch, ScriptedPortal([STARTED] * 1000))
    code, _out, err = _run(env, [*SUBMIT, "--wait-producer", "--producer-timeout-hours", "0.05"], capsys)
    assert code == 4
    record = _registry(env).load(acquisition_id)
    assert record.state == S.PRODUCER_RUNNING
    assert record.last_entry(S.PRODUCER_COMPLETE) is None


def test_wait_producer_stops_blocked_on_a_failed_log(env, capsys, monkeypatch):
    acquisition_id = _durable_record(env, capsys)
    _arm(monkeypatch, acquisition_id)
    portal = ScriptedPortal([STARTED, f"{STARTED}Traceback (most recent call last):\n"])
    _install_portal(monkeypatch, portal)
    code, _out, err = _run(env, [*SUBMIT, "--wait-producer"], capsys)
    assert code == 1
    assert _registry(env).load(acquisition_id).state == S.BLOCKED
    assert "BLOCKED" in err


def test_wait_producer_without_a_topic_leaves_the_submission_as_recorded(env, capsys, monkeypatch):
    acquisition_id = _durable_record(env, capsys)
    _arm(monkeypatch, acquisition_id)
    portal = ScriptedPortal(submit_behavior="batch_only")
    _install_portal(monkeypatch, portal)
    code, out, err = _run(env, [*SUBMIT, "--wait-producer"], capsys)
    assert code == 4
    assert _registry(env).load(acquisition_id).state == S.SUBMITTED
    assert "producer_log" not in portal.calls
    assert "unknown until a topic is identified" in out


@pytest.mark.parametrize("argv", [[*MONTH1, "--portal-check", "--wait-producer"], [*SUBMIT, "--wait-producer", "--producer-timeout-hours", "0"], [*SUBMIT, "--wait-producer", "--producer-timeout-hours", "49"]])
def test_wait_producer_arguments_are_checked_before_anything_happens(env, capsys, monkeypatch, argv):
    monkeypatch.setenv(cli.LIVE_SUBMISSION_ENV, "1")
    monkeypatch.setattr(cli, "_make_playwright_portal", lambda **kwargs: (_ for _ in ()).throw(AssertionError("no browser")))
    code, _out, _err = _run(env, argv, capsys)
    assert code == 2
    assert not env["registry_root"].exists()


def test_no_endpoint_or_secret_reaches_output_or_evidence(env, capsys, monkeypatch):
    acquisition_id = _durable_record(env, capsys)
    _arm(monkeypatch, acquisition_id)
    leaky = f"Connecting to broker kafka.example.org:9092 sasl.password=hunter2\n{COMPLETE}"
    _install_portal(monkeypatch, ScriptedPortal([leaky]))
    code, out, err = _run(env, [*SUBMIT, "--wait-producer"], capsys)
    assert code == 0, err
    record = _registry(env).load(acquisition_id)
    stored = "\n".join(path.read_text(encoding="utf-8") for path in record.directory.rglob("*") if path.is_file())
    for secret in SECRETS:
        assert secret not in out and secret not in err and secret not in stored
    events = SubmissionAuthority(env["authority_path"]).events(record.fingerprint)
    assert all(secret not in str(events) for secret in SECRETS)


def test_the_arnor_root_matches_the_production_preflight():
    script = Path("scripts/arnor_production_preflight.py").read_text(encoding="utf-8")
    assert f'PRODUCTION_DATA_ROOT = "{cli.ARNOR_DATA_ROOT}"' in script

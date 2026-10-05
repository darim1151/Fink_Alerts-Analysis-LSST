"""External submission authority (FINK-G3B.0-R2, R1-01)."""

import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import threading
from datetime import date
from pathlib import Path

import pytest

from _acquisition_fakes import FakeApprover, FakePortal
from fink_lsst.acquisition.authority import (
    AttemptAlreadyClaimedError,
    AuthorityConflictError,
    SubmissionAuthority,
    SubmissionAuthorityError,
    default_authority_path,
)
from fink_lsst.acquisition.orchestrator import AcquisitionOrchestrator, DuplicateSubmissionError, OrchestrationError
from fink_lsst.acquisition.registry import AcquisitionRegistry
from fink_lsst.acquisition.states import AcquisitionState as S


AS_OF = date(2026, 10, 5)
FP = "b" * 64
TOPIC = "ftransfer_lsst_2026-10-05_123456"


def _claim(authority, fingerprint=FP, acquisition_id="acq_test", context_id="ctx-1"):
    return authority.claim(
        fingerprint=fingerprint,
        acquisition_id=acquisition_id,
        request_sha256="1" * 64,
        portal_config_sha256="2" * 64,
        code_revision="abc123",
        browser_context_id=context_id,
    )


# ---------------------------------------------------------------- location and permissions


def test_default_location_follows_xdg_state_home(tmp_path):
    assert default_authority_path({"XDG_STATE_HOME": str(tmp_path)}) == tmp_path / "fink-lsst" / "submission_authority.sqlite3"
    assert default_authority_path({}, home=tmp_path) == tmp_path / ".local" / "state" / "fink-lsst" / "submission_authority.sqlite3"
    assert default_authority_path({"XDG_STATE_HOME": "relative/dir"}, home=tmp_path) == tmp_path / ".local" / "state" / "fink-lsst" / "submission_authority.sqlite3"


def test_tests_never_see_the_real_state_directory():
    assert str(Path.home() / ".local" / "state") not in str(default_authority_path())


def test_authority_files_are_private(tmp_path):
    authority = SubmissionAuthority(tmp_path / "state" / "fink-lsst" / "submission_authority.sqlite3")
    _claim(authority)
    assert stat.S_IMODE(os.stat(tmp_path / "state" / "fink-lsst").st_mode) == 0o700
    assert stat.S_IMODE(os.stat(authority.path).st_mode) == 0o600


def test_authority_records_the_binding_and_never_deletes(tmp_path):
    authority = SubmissionAuthority(tmp_path / "a" / "authority.sqlite3")
    attempt = _claim(authority)
    assert attempt.fingerprint == FP and attempt.status == "claimed"
    assert len(attempt.attempt_id) == 32 and attempt.code_revision == "abc123" and attempt.browser_context_id == "ctx-1"
    with sqlite3.connect(authority.path) as connection:
        with pytest.raises(sqlite3.DatabaseError, match="permanent"):
            connection.execute("DELETE FROM submission_attempts")
        with pytest.raises(sqlite3.DatabaseError, match="immutable"):
            connection.execute("UPDATE submission_attempts SET fingerprint = 'x'")
    assert authority.get(FP).attempt_id == attempt.attempt_id


# ---------------------------------------------------------------- exclusivity


def test_same_checkout_cannot_claim_twice(tmp_path):
    authority = SubmissionAuthority(tmp_path / "authority.sqlite3")
    _claim(authority)
    with pytest.raises(AttemptAlreadyClaimedError):
        _claim(authority, context_id="ctx-2")


def test_two_handles_on_one_authority_cannot_both_claim(tmp_path):
    path = tmp_path / "authority.sqlite3"
    _claim(SubmissionAuthority(path), acquisition_id="checkout_one")
    with pytest.raises(AttemptAlreadyClaimedError):
        _claim(SubmissionAuthority(path), acquisition_id="checkout_two")


def test_concurrent_claims_have_exactly_one_winner(tmp_path):
    path = tmp_path / "authority.sqlite3"
    SubmissionAuthority(path).get(FP)  # create nothing; first claim initializes
    barrier = threading.Barrier(8)
    results = []

    def worker(index):
        authority = SubmissionAuthority(path)
        barrier.wait()
        try:
            _claim(authority, context_id=f"ctx-{index}")
            results.append("won")
        except AttemptAlreadyClaimedError:
            results.append("lost")

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results) == ["lost"] * 7 + ["won"]


def test_a_claim_made_by_another_process_is_seen(tmp_path):
    path = tmp_path / "authority.sqlite3"
    code = (
        "import sys; sys.path.insert(0, 'src');"
        "from pathlib import Path;"
        "from fink_lsst.acquisition.authority import SubmissionAuthority;"
        f"SubmissionAuthority(Path({str(path)!r})).claim(fingerprint={FP!r}, acquisition_id='other_process', request_sha256='1'*64,"
        " portal_config_sha256='2'*64, code_revision='x', browser_context_id='ctx-child')"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
    with pytest.raises(AttemptAlreadyClaimedError):
        _claim(SubmissionAuthority(path))
    assert SubmissionAuthority(path).get(FP).acquisition_id == "other_process"


def test_batch_and_topic_are_set_once_and_topics_have_one_owner(tmp_path):
    authority = SubmissionAuthority(tmp_path / "authority.sqlite3")
    attempt = _claim(authority)
    authority.record_status(FP, attempt.attempt_id, "submitted", batch_id="41", topic=TOPIC)
    with pytest.raises(AuthorityConflictError):
        authority.record_status(FP, attempt.attempt_id, "job_found", batch_id="42", topic=TOPIC)
    with pytest.raises(AuthorityConflictError):
        authority.record_status(FP, attempt.attempt_id, "job_found", batch_id="41", topic="ftransfer_lsst_2026-10-05_9")
    other = _claim(authority, fingerprint="c" * 64, acquisition_id="acq_other")
    with pytest.raises(AuthorityConflictError):
        authority.record_status("c" * 64, other.attempt_id, "job_found", batch_id="77", topic=TOPIC)
    with pytest.raises(AuthorityConflictError):
        authority.record_status(FP, "0" * 32, "uncertain")
    assert authority.owner_of_topic(TOPIC) == FP


# ---------------------------------------------------------------- fail closed


def test_corrupt_or_unavailable_authority_fails_closed(tmp_path):
    corrupt = tmp_path / "corrupt.sqlite3"
    corrupt.write_bytes(b"this is not a database" * 100)
    os.chmod(corrupt, 0o600)
    for operation in (lambda a: a.get(FP), lambda a: _claim(a)):
        with pytest.raises(SubmissionAuthorityError):
            operation(SubmissionAuthority(corrupt))
    directory_in_the_way = tmp_path / "dir.sqlite3"
    directory_in_the_way.mkdir()
    with pytest.raises(SubmissionAuthorityError):
        _claim(SubmissionAuthority(directory_in_the_way))
    locked_parent = tmp_path / "locked"
    locked_parent.mkdir()
    os.chmod(locked_parent, 0o500)
    try:
        with pytest.raises(SubmissionAuthorityError):
            _claim(SubmissionAuthority(locked_parent / "fink-lsst" / "authority.sqlite3"))
    finally:
        os.chmod(locked_parent, 0o700)


def test_foreign_schema_is_rejected(tmp_path):
    path = tmp_path / "authority.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE something_else (x)")
    os.chmod(path, 0o600)
    with pytest.raises(SubmissionAuthorityError):
        _claim(SubmissionAuthority(path))


def test_world_writable_state_directory_is_refused(tmp_path):
    parent = tmp_path / "shared"
    parent.mkdir()
    os.chmod(parent, 0o777)
    target = parent / "authority.sqlite3"
    try:
        with pytest.raises(SubmissionAuthorityError, match="writable by others"):
            _claim(SubmissionAuthority(target))
    finally:
        os.chmod(parent, 0o700)


# ---------------------------------------------------------------- orchestrator integration (R1-01)


class Clock:
    def __init__(self):
        self.tick = 0

    def __call__(self):
        self.tick += 1
        return f"2026-10-05T02:{self.tick // 60:02d}:{self.tick % 60:02d}Z"


def _orchestrator(root, authority):
    return AcquisitionOrchestrator(AcquisitionRegistry(root, clock=Clock()), authority=authority, clock=Clock(), workdir=root.parent / "work", code_revision=lambda: "rev-test")


def _verified(orchestrator, portal):
    record = orchestrator.record(orchestrator.plan("2026-02-25", "2026-03-25", as_of=AS_OF))
    return orchestrator.verify_portal(record, portal)


def test_restoring_an_older_git_registry_cannot_reopen_submission(tmp_path):
    authority = SubmissionAuthority(tmp_path / "state" / "authority.sqlite3")
    root = tmp_path / "checkout" / "acquisitions"
    orchestrator = _orchestrator(root, authority)
    portal = FakePortal(live_submit_enabled=True)
    record = _verified(orchestrator, portal)
    snapshot = tmp_path / "snapshot"
    shutil.copytree(root, snapshot)  # what `git checkout <older commit>` would bring back
    orchestrator.submit(record, portal, FakeApprover())
    shutil.rmtree(root)
    shutil.copytree(snapshot, root)
    restored = orchestrator.registry.load(record.acquisition_id)
    assert restored.state == S.PORTAL_VERIFIED  # Git says "never submitted"
    again = FakePortal(live_submit_enabled=True)
    with pytest.raises(DuplicateSubmissionError, match="submission authority"):
        orchestrator.submit(orchestrator.verify_portal(restored, again), again, FakeApprover())
    assert again.submit_clicks == 0


def test_a_second_checkout_sharing_the_authority_cannot_submit(tmp_path):
    authority_path = tmp_path / "state" / "authority.sqlite3"
    first = _orchestrator(tmp_path / "one" / "acquisitions", SubmissionAuthority(authority_path))
    second = _orchestrator(tmp_path / "two" / "acquisitions", SubmissionAuthority(authority_path))
    portal_one, portal_two = FakePortal(live_submit_enabled=True), FakePortal(live_submit_enabled=True)
    record_one, record_two = _verified(first, portal_one), _verified(second, portal_two)
    assert record_one.fingerprint == record_two.fingerprint
    first.submit(record_one, portal_one, FakeApprover())
    with pytest.raises(DuplicateSubmissionError):
        second.submit(record_two, portal_two, FakeApprover())
    with pytest.raises(OrchestrationError):
        second.verify_portal(record_two, FakePortal())
    assert portal_two.submit_clicks == 0


def test_a_claim_without_a_click_still_blocks_forever(tmp_path):
    authority = SubmissionAuthority(tmp_path / "state" / "authority.sqlite3")
    orchestrator = _orchestrator(tmp_path / "acquisitions", authority)

    class CrashBeforeClick(FakePortal):
        def submit(self, authorization):
            raise KeyboardInterrupt("operator pressed Ctrl-C before the click")

    portal = CrashBeforeClick(live_submit_enabled=True)
    record = _verified(orchestrator, portal)
    with pytest.raises(KeyboardInterrupt):
        orchestrator.submit(record, portal, FakeApprover())
    assert authority.get(record.fingerprint) is not None
    record = orchestrator.recover_interrupted_submission(orchestrator.registry.load(record.acquisition_id))
    assert record.state == S.SUBMISSION_UNCERTAIN
    later = FakePortal(live_submit_enabled=True)
    with pytest.raises(DuplicateSubmissionError):
        orchestrator.submit(record, later, FakeApprover())
    assert later.submit_clicks == 0 and portal.submit_clicks == 0


def test_submission_without_an_authority_is_refused(tmp_path):
    orchestrator = _orchestrator(tmp_path / "acquisitions", None)
    portal = FakePortal(live_submit_enabled=True)
    record = _verified(orchestrator, portal)
    with pytest.raises(OrchestrationError, match="authority"):
        orchestrator.submit(record, portal, FakeApprover())
    assert portal.submit_clicks == 0


def test_corrupt_authority_blocks_submission_before_any_click(tmp_path):
    path = tmp_path / "state" / "authority.sqlite3"
    orchestrator = _orchestrator(tmp_path / "acquisitions", SubmissionAuthority(path))
    portal = FakePortal(live_submit_enabled=True)
    record = _verified(orchestrator, portal)  # no authority file yet: no claims
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    path.write_bytes(b"\x00garbage" * 64)
    os.chmod(path, 0o600)
    with pytest.raises(SubmissionAuthorityError):
        orchestrator.submit(record, portal, FakeApprover())
    with pytest.raises(SubmissionAuthorityError):
        orchestrator.verify_portal(record, FakePortal())
    assert portal.submit_clicks == 0
    assert orchestrator.registry.load(record.acquisition_id).state == S.PORTAL_VERIFIED

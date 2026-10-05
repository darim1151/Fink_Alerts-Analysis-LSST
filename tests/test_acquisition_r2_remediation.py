"""Regression tests for the independent G3B.0 review findings R1-02 to R1-07 (FINK-G3B.0-R2)."""

import json
import os
from datetime import date
from pathlib import Path

import pytest

from _acquisition_fakes import FakeApprover, FakeKafkaConsumer, FakePortal, TopicPartition, write_raw_parquet
from fink_lsst.acquisition.authority import SubmissionAuthority
from fink_lsst.acquisition.handoff import build_transfer_plan
from fink_lsst.acquisition.orchestrator import (
    AcquisitionOrchestrator,
    OrchestrationError,
    PortalVerificationError,
    StaleVerificationError,
    SubmissionUncertainError,
)
from fink_lsst.acquisition.portal import (
    PortalError,
    SubmissionDisabledError,
    SubmitAuthorization,
    classify_dash_request,
)
from fink_lsst.acquisition.portal_config import compile_portal_config
from fink_lsst.acquisition.portal_playwright import PlaywrightPortalAdapter
from fink_lsst.acquisition.registry import AcquisitionRegistry, RegistryError
from fink_lsst.acquisition.states import AcquisitionState as S


AS_OF = date(2026, 10, 5)
TOPIC_A = "ftransfer_lsst_2026-10-05_111111"
TOPIC_B = "ftransfer_lsst_2026-10-05_222222"
CALLBACK_URL = "https://lsst.fink-portal.org/_dash-update-component"


class Clock:
    def __init__(self):
        self.tick = 0

    def __call__(self):
        self.tick += 1
        return f"2026-10-05T03:{self.tick // 60:02d}:{self.tick % 60:02d}Z"


@pytest.fixture
def env(tmp_path):
    authority = SubmissionAuthority(tmp_path / "state" / "authority.sqlite3")
    registry = AcquisitionRegistry(tmp_path / "acquisitions", clock=Clock())
    orchestrator = AcquisitionOrchestrator(registry, authority=authority, clock=Clock(), workdir=tmp_path / "work", code_revision=lambda: "rev-test")
    data_root = tmp_path / "FINK"
    for relative in ("data/raw/data_transfer", "data/processed/data_transfer", "outputs/data_transfer", "manifests", "logs"):
        (data_root / relative).mkdir(parents=True)
    return orchestrator, authority, data_root


def _verified(orchestrator, portal, start="2026-02-25", stop="2026-03-25"):
    record = orchestrator.record(orchestrator.plan(start, stop, as_of=AS_OF))
    return orchestrator.verify_portal(record, portal)


def _submitted(orchestrator, topic, start="2026-02-25", stop="2026-03-25", batch="41"):
    portal = FakePortal(live_submit_enabled=True, topic=topic, batch_id=batch)
    record = _verified(orchestrator, portal, start, stop)
    return orchestrator.submit(record, portal, FakeApprover())


def _producer_complete(orchestrator, record):
    return orchestrator.observe_producer(record, FakePortal(producer_log=f"Data available at topic: {record.topic}\nEnd.\n"))


def _record_metadata(orchestrator, record, known_topics=None, highs=(6, 4)):
    consumer = FakeKafkaConsumer(known_topics if known_topics is not None else {record.topic: highs})
    return orchestrator.record_topic_metadata(record, consumer, topic_partition_factory=TopicPartition)


def _to_transfer_complete(orchestrator, record, data_root, committed=10):
    record = _producer_complete(orchestrator, record)
    record = _record_metadata(orchestrator, record)
    record = orchestrator.record_transfer_started(record, build_transfer_plan(record, data_root), release="rev-test")
    return orchestrator.record_transfer_result(record, exit_code=0, terminal_committed=committed, terminal_lag=0, log_sha256="f" * 64)


def _raw_dir(data_root, record):
    return data_root / record.request.expected_raw_dir(record.topic)


# ---------------------------------------------------------------- R1-02: bound browser state


def test_r1_02_form_changed_while_awaiting_approval_cannot_submit(env):
    orchestrator, authority, _root = env
    portal = FakePortal(live_submit_enabled=True)
    record = _verified(orchestrator, portal)

    class ChangesFormDuringApproval(FakeApprover):
        def approve(self, record, review_text):
            portal.display = lambda mapping: {**mapping, "filters": ["in_tns"]}  # someone edits the form
            return super().approve(record, review_text)

    with pytest.raises(PortalVerificationError, match="filters"):
        orchestrator.submit(record, portal, ChangesFormDuringApproval())
    assert portal.submit_clicks == 0
    assert authority.get(record.fingerprint) is None  # nothing claimed: the mismatch was caught before the claim
    assert orchestrator.registry.load(record.acquisition_id).state == S.BLOCKED


def test_r1_02_dates_changed_while_awaiting_approval_cannot_submit(env):
    orchestrator, authority, _root = env
    portal = FakePortal(live_submit_enabled=True)
    record = _verified(orchestrator, portal)

    class ChangesDates(FakeApprover):
        def approve(self, record, review_text):
            portal.display = lambda mapping: {**mapping, "dates": {"startdate": "2026-02-25", "stopdate": "2026-03-25"}}
            return super().approve(record, review_text)

    with pytest.raises(PortalVerificationError):
        orchestrator.submit(record, portal, ChangesDates())
    assert portal.submit_clicks == 0 and authority.get(record.fingerprint) is None


def test_r1_02_close_and_reopen_invalidates_the_verification(env):
    orchestrator, _authority, _root = env
    portal = FakePortal(live_submit_enabled=True)
    record = _verified(orchestrator, portal)
    verified_context = portal.context_id
    portal.close()
    assert portal.context_id is None
    portal.open()
    assert portal.context_id not in (None, verified_context)
    with pytest.raises(StaleVerificationError):
        orchestrator.submit(record, portal, FakeApprover())
    assert portal.submit_clicks == 0


def test_r1_02_adapter_cannot_submit_without_an_orchestrator_authorization():
    adapter = PlaywrightPortalAdapter(live_submit_enabled=True)
    with pytest.raises(TypeError):
        adapter.submit()  # no authorization at all
    forged = SubmitAuthorization(
        attempt_id="0" * 32, fingerprint="f" * 64, acquisition_id="acq_x", request_sha256="1" * 64,
        portal_config_sha256="2" * 64, context_id=None, expected_config=None, approved_state_digest="3" * 64, nonce="forged",
    )
    with pytest.raises(SubmissionDisabledError, match="not issued"):
        adapter.submit(forged)
    fake = FakePortal(live_submit_enabled=True)
    with pytest.raises(SubmissionDisabledError):
        fake.submit(forged)
    assert fake.submit_clicks == 0


def test_r1_02_authorization_is_one_use_and_context_bound(env):
    orchestrator, authority, _root = env
    portal = FakePortal(live_submit_enabled=True)
    record = orchestrator.submit(_verified(orchestrator, portal), portal, FakeApprover())
    assert record.state == S.TOPIC_IDENTIFIED
    used = portal.last_authorization
    assert used.fingerprint == record.fingerprint and used.context_id == portal.context_id
    assert used.attempt_id == authority.get(record.fingerprint).attempt_id
    with pytest.raises(SubmissionDisabledError, match="not issued|already used"):
        portal.submit(used)
    assert portal.submit_clicks == 1


# ---------------------------------------------------------------- R1-03: no negative reopening


def _uncertain_with_observed_topic(orchestrator):
    portal = FakePortal(live_submit_enabled=True, submit_behavior="topic_without_batch", topic=TOPIC_A)
    record = _verified(orchestrator, portal)
    with pytest.raises(SubmissionUncertainError):
        orchestrator.submit(record, portal, FakeApprover())
    return orchestrator.registry.load(record.acquisition_id)


def test_r1_03_observed_topic_plus_no_job_created_cannot_reopen(env):
    orchestrator, authority, _root = env
    record = _uncertain_with_observed_topic(orchestrator)
    assert record.state == S.SUBMISSION_UNCERTAIN
    assert record.last_entry(S.SUBMISSION_UNCERTAIN).evidence["observed_topic"] == TOPIC_A
    with pytest.raises(OrchestrationError, match="observed"):
        orchestrator.reconcile_submission(record, resolution="no_job_created", statement="portal list looked empty")
    assert orchestrator.registry.load(record.acquisition_id).state == S.SUBMISSION_UNCERTAIN


def test_r1_03_negative_statement_never_makes_a_request_submittable(env):
    orchestrator, authority, _root = env
    portal = FakePortal(live_submit_enabled=True, submit_behavior="lost")
    record = _verified(orchestrator, portal)
    with pytest.raises(SubmissionUncertainError):
        orchestrator.submit(record, portal, FakeApprover())
    record = orchestrator.reconcile_submission(orchestrator.registry.load(record.acquisition_id), resolution="no_job_created", statement="Fink support reports no batch")
    assert record.state == S.BLOCKED
    assert authority.get(record.fingerprint).status == "negative_statement_recorded"
    fresh = FakePortal(live_submit_enabled=True)
    with pytest.raises(OrchestrationError):
        orchestrator.verify_portal(record, fresh)
    with pytest.raises(OrchestrationError):
        orchestrator.submit(record, fresh, FakeApprover())
    with pytest.raises(OrchestrationError):
        orchestrator.unblock_before_submission(record, statement="please retry")
    assert fresh.submit_clicks == 0


def test_r1_03_contradictory_job_found_evidence_is_rejected(env):
    orchestrator, authority, _root = env
    record = _uncertain_with_observed_topic(orchestrator)
    with pytest.raises(OrchestrationError, match="observed"):
        orchestrator.reconcile_submission(record, resolution="job_found", statement="support", batch_id="99", topic=TOPIC_B)
    record = orchestrator.reconcile_submission(record, resolution="job_found", statement="portal batch list shows 41", batch_id="41", topic=TOPIC_A)
    assert record.state == S.TOPIC_IDENTIFIED
    assert authority.get(record.fingerprint).topic == TOPIC_A and authority.get(record.fingerprint).batch_id == "41"


# ---------------------------------------------------------------- R1-04: acquisition-bound evidence


def test_r1_04_watermarks_of_another_topic_cannot_verify_this_acquisition(env):
    orchestrator, _authority, _root = env
    record_a = _producer_complete(orchestrator, _submitted(orchestrator, TOPIC_A))
    consumer = FakeKafkaConsumer({TOPIC_B: (6, 4)})  # only B's watermarks are available
    with pytest.raises(OrchestrationError, match="not available"):
        orchestrator.record_topic_metadata(record_a, consumer, topic_partition_factory=TopicPartition)
    assert consumer.queried == [TOPIC_A]  # the query used A's own topic, never a caller label
    assert orchestrator.registry.load(record_a.acquisition_id).state == S.PRODUCER_COMPLETE


def test_r1_04_relabelled_observations_cannot_reach_the_receipt_builder(env):
    from fink_lsst.acquisition.receipts import EvidenceError, PartitionWatermarks, TopicWatermarkObservation, build_topic_metadata_receipt

    orchestrator, _authority, _root = env
    record_a = _producer_complete(orchestrator, _submitted(orchestrator, TOPIC_A))
    with pytest.raises(EvidenceError, match="queried topic"):
        build_topic_metadata_receipt(record_a, TopicWatermarkObservation(TOPIC_B, "t", (PartitionWatermarks(0, 0, 10),)))


def test_r1_04_topic_ownership_conflict_is_rejected(env):
    orchestrator, _authority, _root = env
    _submitted(orchestrator, TOPIC_A, "2026-02-25", "2026-03-25", batch="41")
    with pytest.raises((OrchestrationError, RegistryError), match="already (owned|recorded)|owner"):
        _submitted(orchestrator, TOPIC_A, "2026-03-25", "2026-04-25", batch="42")


def test_r1_04_transfer_plan_for_another_acquisition_is_rejected(env):
    orchestrator, _authority, data_root = env
    record_a = _submitted(orchestrator, TOPIC_A, "2026-02-25", "2026-03-25", batch="41")
    record_b = _submitted(orchestrator, TOPIC_B, "2026-03-25", "2026-04-25", batch="42")
    record_a = _record_metadata(orchestrator, _producer_complete(orchestrator, record_a))
    record_b = _record_metadata(orchestrator, _producer_complete(orchestrator, record_b))
    with pytest.raises(OrchestrationError, match="plan"):
        orchestrator.record_transfer_started(record_a, build_transfer_plan(record_b, data_root), release="rev-test")


def test_r1_04_raw_directory_of_another_acquisition_cannot_validate_this_one(env):
    orchestrator, _authority, data_root = env
    record_a = _to_transfer_complete(orchestrator, _submitted(orchestrator, TOPIC_A, "2026-02-25", "2026-03-25", batch="41"), data_root)
    record_b = _to_transfer_complete(orchestrator, _submitted(orchestrator, TOPIC_B, "2026-03-25", "2026-04-25", batch="42"), data_root)
    write_raw_parquet(_raw_dir(data_root, record_b), [6, 4])  # B delivered exactly 10 rows; A delivered nothing
    with pytest.raises(OrchestrationError, match="raw delivery"):
        orchestrator.record_delivery_validation(record_a, data_root=data_root)
    assert orchestrator.registry.load(record_a.acquisition_id).state == S.TRANSFER_COMPLETE
    record_b = orchestrator.record_delivery_validation(record_b, data_root=data_root)
    assert record_b.state == S.DELIVERY_VALIDATED
    receipt = record_b.last_entry(S.DELIVERY_VALIDATED).evidence["receipt"]
    assert receipt["acquisition_id"] == record_b.acquisition_id and receipt["topic"] == TOPIC_B
    assert receipt["raw_dir"] == record_b.request.expected_raw_dir(TOPIC_B)
    assert receipt["transfer_attempt_id"] == record_b.last_entry(S.TRANSFER_COMPLETE).evidence["receipt"]["transfer_attempt_id"]


def test_r1_04_relabelled_receipts_are_rejected_by_the_state_guards(env):
    orchestrator, _authority, data_root = env
    record_a = _submitted(orchestrator, TOPIC_A, "2026-02-25", "2026-03-25", batch="41")
    record_b = _submitted(orchestrator, TOPIC_B, "2026-03-25", "2026-04-25", batch="42")
    record_a = _producer_complete(orchestrator, record_a)
    record_b = _record_metadata(orchestrator, _producer_complete(orchestrator, record_b))
    stolen = dict(record_b.last_entry(S.TOPIC_VERIFIED).evidence)
    registry = orchestrator.registry
    with pytest.raises(ValueError):
        registry.transition(record_a, S.TOPIC_VERIFIED, actor="executor", reason="relabel", evidence=stolen)
    relabelled = json.loads(json.dumps(stolen))
    relabelled["receipt"]["topic"] = relabelled["receipt"]["queried_topic"] = TOPIC_A
    with pytest.raises(ValueError):
        registry.transition(record_a, S.TOPIC_VERIFIED, actor="executor", reason="relabel", evidence=relabelled)


# ---------------------------------------------------------------- R1-05: structural callback detection


def _callback(n_clicks="missing", component="submit_datatransfer", states=None):
    item = {"id": component, "property": "n_clicks"}
    if n_clicks != "missing":
        item["value"] = n_clicks
    return {"output": "..submit_datatransfer.disabled..", "outputs": [], "inputs": [item], "changedPropIds": [f"{component}.n_clicks"], "state": states or []}


def test_r1_05_unicode_escaped_component_ids_are_detected():
    body = json.dumps(_callback(1)).replace("submit_datatransfer", "submit_\\u0064atatransfer")
    assert "submit_datatransfer" not in body
    decision = classify_dash_request(CALLBACK_URL, "POST", body)
    assert decision.kind == "submit_click" and decision.clicks == 1
    mount = json.dumps(_callback()).replace("submit_datatransfer", "\\u0073ubmit_datatransfer")
    assert classify_dash_request(CALLBACK_URL, "POST", mount).kind == "submit_mount"


@pytest.mark.parametrize(
    "body",
    ["{not json", "[]", json.dumps({"inputs": "submit_datatransfer"}), json.dumps(_callback("1")), json.dumps(_callback(True)), json.dumps({"inputs": [], "state": [{"id": "submit_datatransfer", "property": "n_clicks", "value": 1}]})],
)
def test_r1_05_malformed_or_unexpected_callbacks_fail_closed(body):
    assert classify_dash_request(CALLBACK_URL, "POST", body).kind == "malformed"


def test_r1_05_unrelated_requests_are_not_submit_callbacks():
    assert classify_dash_request(CALLBACK_URL, "POST", json.dumps({"inputs": [{"id": "submit_yaml_file", "property": "n_clicks", "value": 1}], "state": []})).kind == "unrelated"
    assert classify_dash_request("https://lsst.fink-portal.org/_dash-layout", "GET", "").kind == "unrelated"
    assert classify_dash_request("https://lsst.fink-portal.org/assets/x.js", "POST", "binary\x00").kind == "unrelated"


def test_r1_05_guard_blocks_escaped_submit_even_when_unarmed():
    adapter = PlaywrightPortalAdapter()
    route = _Route()
    adapter._guard_submit_requests(route, _Request("POST", json.dumps(_callback(1)).replace("submit_datatransfer", "submit_\\u0064atatransfer")))
    assert route.action == "abort" and adapter.blocked_submit_requests == 1


# ---------------------------------------------------------------- R1-06: evidence replay verification


def _validated(orchestrator, data_root):
    record = _to_transfer_complete(orchestrator, _submitted(orchestrator, TOPIC_A), data_root)
    write_raw_parquet(_raw_dir(data_root, record), [6, 4])
    return orchestrator.record_delivery_validation(record, data_root=data_root)


def _ref_path(record, state):
    return record.directory / record.last_entry(state).evidence["receipt_ref"]["path"]


def test_r1_06_deleted_evidence_fails_replay(env):
    orchestrator, _authority, data_root = env
    record = _validated(orchestrator, data_root)
    assert record.state == S.DELIVERY_VALIDATED
    _ref_path(record, S.DELIVERY_VALIDATED).unlink()
    with pytest.raises(RegistryError, match="evidence"):
        orchestrator.registry.load(record.acquisition_id)


@pytest.mark.parametrize("state", [S.TOPIC_VERIFIED, S.TRANSFER_COMPLETE, S.DELIVERY_VALIDATED])
def test_r1_06_altered_evidence_fails_replay(env, state):
    orchestrator, _authority, data_root = env
    record = _validated(orchestrator, data_root)
    path = _ref_path(record, state)
    path.write_text(path.read_text(encoding="utf-8").replace('"schema_version": 1', '"schema_version": 1 '), encoding="utf-8")
    with pytest.raises(RegistryError, match="sha256|digest"):
        orchestrator.registry.load(record.acquisition_id)


def test_r1_06_symlinked_or_escaping_evidence_fails_replay(env, tmp_path):
    orchestrator, _authority, data_root = env
    record = _validated(orchestrator, data_root)
    path = _ref_path(record, S.DELIVERY_VALIDATED)
    outside = tmp_path / "outside.json"
    outside.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(outside)
    with pytest.raises(RegistryError, match="evidence"):
        orchestrator.registry.load(record.acquisition_id)


def test_r1_06_portal_download_evidence_is_verified_on_replay(env):
    orchestrator, _authority, _root = env
    record = _verified(orchestrator, FakePortal())
    ref = record.last_entry(S.PORTAL_VERIFIED).evidence["downloaded_config_ref"]
    (record.directory / ref["path"]).write_text("blocks: [b_is_new]\n", encoding="utf-8")
    with pytest.raises(RegistryError):
        orchestrator.registry.load(record.acquisition_id)


# ---------------------------------------------------------------- R1-07: callback allowance lifetime


class _Route:
    def __init__(self):
        self.action = None

    def abort(self):
        self.action = "abort"

    def continue_(self):
        self.action = "continue"


class _Request:
    def __init__(self, method, body, url=CALLBACK_URL):
        self.method, self.post_data, self.url = method, body, url


EXPECTED = compile_portal_config(__import__("fink_lsst.acquisition.planner", fromlist=["x"]).build_acquisition_request("2026-02-25", "2026-03-25", as_of=AS_OF))
GOOD_STATES = [
    {"id": "date-range-picker", "property": "value", "value": ["2026-02-25", "2026-03-24"]},
    {"id": "tag_select", "property": "data", "value": []},
    {"id": "blocks_select", "property": "data", "value": []},
    {"id": "field_select", "property": "value", "value": ["Light static packet"]},
    {"id": "extra_cond", "property": "value", "value": ""},
    {"id": "object-catalog", "property": "data", "value": None},
    {"id": "upload-data", "property": "filename", "value": None},
    {"id": "ra-column", "property": "value", "value": None},
    {"id": "dec-column", "property": "value", "value": None},
    {"id": "radius_xmatch", "property": "value", "value": None},
    {"id": "id-column", "property": "value", "value": None},
]


class _ScriptedAdapter(PlaywrightPortalAdapter):
    """Real `submit()` control flow and real guard; the browser is replaced by a script."""

    def __init__(self, click_effect, authority_dir=None):
        super().__init__(live_submit_enabled=True, timeout_ms=200)
        self.context_id = "ctx-scripted"
        self.authority_dir = authority_dir
        self.click_effect = click_effect
        self.routes = []

    def _require_page(self):
        return self

    def _go_to_step(self, target):
        return None

    def _read_scientific_state(self):
        return self.expected_state_for_test

    def _submit_control_available(self):
        return True

    def _click_submit(self):
        self.click_effect(self)

    def _read_submission_ids(self):
        return ("41", TOPIC_A) if any(route.action == "continue" for route in self.routes) else (None, None)

    def wait_for_timeout(self, ms):
        return None

    def send_callback(self, n_clicks=1, states=None):
        route = _Route()
        self._guard_submit_requests(route, _Request("POST", json.dumps(_callback(n_clicks, states=GOOD_STATES if states is None else states))))
        self.routes.append(route)
        return route


def _authorize(adapter, tmp_path_factory=None):
    import tempfile

    from fink_lsst.acquisition.portal import issue_submit_authorization, scientific_state_from_config

    authority = SubmissionAuthority(Path(tempfile.mkdtemp()) / "authority.sqlite3")
    attempt = authority.claim(fingerprint="f" * 64, acquisition_id="acq_x", request_sha256="1" * 64, portal_config_sha256="2" * 64, code_revision="rev", browser_context_id=adapter.context_id)
    adapter.expected_state_for_test = scientific_state_from_config(EXPECTED)
    return issue_submit_authorization(
        authority=authority, attempt_id=attempt.attempt_id, fingerprint="f" * 64, acquisition_id="acq_x", request_sha256="1" * 64, portal_config_sha256="2" * 64,
        context_id=adapter.context_id, expected_config=EXPECTED, approved_state_digest=adapter.expected_state_for_test.digest(),
    )


def test_authorizations_require_a_matching_unused_authority_claim(tmp_path):
    from fink_lsst.acquisition.portal import issue_submit_authorization, scientific_state_from_config

    authority = SubmissionAuthority(tmp_path / "authority.sqlite3")
    digest = scientific_state_from_config(EXPECTED).digest()
    common = dict(fingerprint="f" * 64, acquisition_id="acq_x", request_sha256="1" * 64, portal_config_sha256="2" * 64, context_id="ctx", expected_config=EXPECTED, approved_state_digest=digest)
    with pytest.raises(SubmissionDisabledError, match="claim"):
        issue_submit_authorization(authority=authority, attempt_id="a" * 32, **common)  # nothing claimed
    with pytest.raises(SubmissionDisabledError, match="claim"):
        issue_submit_authorization(authority=None, attempt_id="a" * 32, **common)
    attempt = authority.claim(fingerprint="f" * 64, acquisition_id="acq_x", request_sha256="1" * 64, portal_config_sha256="2" * 64, code_revision="rev", browser_context_id="ctx")
    with pytest.raises(SubmissionDisabledError, match="claim"):
        issue_submit_authorization(authority=authority, attempt_id=attempt.attempt_id, **{**common, "context_id": "other"})
    with pytest.raises(SubmissionDisabledError):
        issue_submit_authorization(authority=authority, attempt_id=attempt.attempt_id, **{**common, "approved_state_digest": "0" * 64})
    assert issue_submit_authorization(authority=authority, attempt_id=attempt.attempt_id, **common).attempt_id == attempt.attempt_id
    authority.record_status("f" * 64, attempt.attempt_id, "click_authorized")
    with pytest.raises(SubmissionDisabledError, match="claim"):
        issue_submit_authorization(authority=authority, attempt_id=attempt.attempt_id, **common)  # already used for a click


def test_r1_07_normal_submit_admits_one_callback_and_revokes_on_return():
    adapter = _ScriptedAdapter(lambda a: (a.send_callback(), a.send_callback()))
    observation = adapter.submit(_authorize(adapter))
    assert [route.action for route in adapter.routes] == ["continue", "abort"]  # a second callback is blocked
    assert observation.callback_admitted is True and observation.batch_id == "41"
    assert adapter.send_callback().action == "abort"  # later callback after return
    assert adapter._armed_authorization is None


def test_r1_07_click_raising_before_any_callback_leaves_no_allowance():
    def explode(adapter):
        raise RuntimeError("click failed")

    adapter = _ScriptedAdapter(explode)
    with pytest.raises(RuntimeError):
        adapter.submit(_authorize(adapter))
    assert adapter._armed_authorization is None
    assert adapter.send_callback().action == "abort"


def test_r1_07_click_raising_after_the_callback_leaves_no_allowance():
    def callback_then_explode(adapter):
        adapter.send_callback()
        raise RuntimeError("page crashed")

    adapter = _ScriptedAdapter(callback_then_explode)
    with pytest.raises(RuntimeError):
        adapter.submit(_authorize(adapter))
    assert adapter.send_callback().action == "abort"


def test_r1_07_missing_callback_is_reported_and_allowance_revoked():
    adapter = _ScriptedAdapter(lambda a: None)
    observation = adapter.submit(_authorize(adapter))
    assert observation.callback_admitted is False and observation.batch_id is None
    assert adapter.send_callback().action == "abort"


def test_r1_07_callback_with_wrong_scientific_state_is_blocked_even_when_authorized():
    bad = [dict(item) for item in GOOD_STATES]
    bad[3] = {"id": "field_select", "property": "value", "value": ["Full packet"]}
    adapter = _ScriptedAdapter(lambda a: a.send_callback(states=bad))
    observation = adapter.submit(_authorize(adapter))
    assert [route.action for route in adapter.routes] == ["abort"]
    assert observation.callback_admitted is False and "field_select" in observation.guard_reason


def test_r1_07_callback_missing_scientific_state_is_blocked():
    adapter = _ScriptedAdapter(lambda a: a.send_callback(states=GOOD_STATES[:3]))
    observation = adapter.submit(_authorize(adapter))
    assert [route.action for route in adapter.routes] == ["abort"] and observation.callback_admitted is False


def test_r1_07_mount_callback_during_submit_is_not_mistaken_for_the_click():
    adapter = _ScriptedAdapter(lambda a: (a.send_callback(n_clicks=None), a.send_callback()))
    observation = adapter.submit(_authorize(adapter))
    assert [route.action for route in adapter.routes] == ["abort", "continue"]
    assert observation.callback_admitted is True
    assert adapter.blocked_initial_submit_callbacks == 1


def test_r1_07_authorization_for_another_context_is_refused():
    adapter = _ScriptedAdapter(lambda a: a.send_callback())
    authorization = _authorize(adapter)
    adapter.context_id = "ctx-reopened"
    with pytest.raises(SubmissionDisabledError, match="context"):
        adapter.submit(authorization)
    assert adapter.routes == []


def test_r1_07_presubmit_form_drift_stops_before_the_click():
    from fink_lsst.acquisition.portal import PortalScientificState

    adapter = _ScriptedAdapter(lambda a: a.send_callback())
    authorization = _authorize(adapter)
    state = adapter.expected_state_for_test
    adapter.expected_state_for_test = PortalScientificState(**{**state.__dict__, "filters": ("in_tns",)})
    with pytest.raises(PortalError, match="changed"):
        adapter.submit(authorization)
    assert adapter.routes == [] and adapter._armed_authorization is None


# ---------------------------------------------------------------- browser context policy


def test_contexts_block_service_workers_and_rotate_identity(monkeypatch):
    seen = {}

    class FakeContext:
        def __init__(self):
            self.handlers = {}

        def set_default_timeout(self, ms):
            pass

        def route(self, pattern, handler):
            seen["route"] = pattern

        def new_page(self):
            return FakePage()

        def close(self):
            seen["closed"] = True

    class FakePage:
        def on(self, event, handler):
            seen.setdefault("events", []).append(event)

        def goto(self, url, wait_until):
            seen["url"] = url

        def get_by_text(self, text, exact):
            return self

        def locator(self, selector):
            return self

        @property
        def first(self):
            return self

        def wait_for(self, **kwargs):
            pass

    class FakeBrowser:
        def new_context(self, **kwargs):
            seen["context_kwargs"] = kwargs
            return FakeContext()

        def close(self):
            pass

    class FakeChromium:
        def launch(self, **kwargs):
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()

        def stop(self):
            pass

    class Starter:
        def start(self):
            return FakePlaywright()

    import types

    monkeypatch.setitem(__import__("sys").modules, "playwright.sync_api", types.SimpleNamespace(sync_playwright=lambda: Starter()))
    adapter = PlaywrightPortalAdapter()
    adapter.open()
    first = adapter.context_id
    assert seen["context_kwargs"]["service_workers"] == "block"
    assert "storage_state" not in seen["context_kwargs"]
    assert "load" in seen["events"]
    adapter.close()
    assert adapter.context_id is None
    adapter.open()
    assert adapter.context_id not in (None, first)

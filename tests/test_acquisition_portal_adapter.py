"""Portal adapter safety guard and form-observation checks (FINK-G3B.0); no browser needed."""

import json
from datetime import date

import pytest

from _acquisition_fakes import FakePortal
from fink_lsst.acquisition.orchestrator import AcquisitionOrchestrator, PortalVerificationError
from fink_lsst.acquisition.planner import build_acquisition_request
from fink_lsst.acquisition.portal import PortalFormObservation, check_form_observation, observed_dates
from fink_lsst.acquisition.portal_config import compile_portal_config
from fink_lsst.acquisition.portal_playwright import PlaywrightPortalAdapter, _is_initial_submit_callback
from fink_lsst.acquisition.portal import PortalError, SubmissionDisabledError
from fink_lsst.acquisition.registry import AcquisitionRegistry
from fink_lsst.acquisition.states import AcquisitionState as S


EXPECTED = compile_portal_config(build_acquisition_request("2026-02-25", "2026-03-25", as_of=date(2026, 10, 5)))


def _form(**changes):
    values = dict(
        date_value_text="2026-02-25 – 2026-03-24",
        date_display_text="February 25, 2026 – March 24, 2026",
        content_values=("Light static packet",),
        selected_filter_buttons=(),
        selected_block_buttons=(),
        filter_buttons_seen=11,
        block_buttons_seen=16,
        extra_cond_text="",
        catalog_label="No catalog",
        alert_estimate_text="1,658,642 alerts",
    )
    values.update(changes)
    return PortalFormObservation(**values)


def test_live_form_observation_from_g3b0_development_passes():
    assert check_form_observation(_form(), EXPECTED) == []


def test_display_dates_are_used_when_the_form_value_is_unavailable():
    assert observed_dates(_form(date_value_text="")) == ("2026-02-25", "2026-03-24")
    assert check_form_observation(_form(date_value_text=""), EXPECTED) == []
    assert check_form_observation(_form(date_value_text="", date_display_text="Feb 25, 2026 – Mar 24, 2026"), EXPECTED) == []


@pytest.mark.parametrize(
    "changes",
    [
        {"date_value_text": "2026-02-25 – 2026-03-25"},
        {"date_value_text": "", "date_display_text": "soon"},
        {"content_values": ()},
        {"content_values": ("Light SSO packet",)},
        {"selected_filter_buttons": ("in_tns",)},
        {"selected_block_buttons": ("b_is_new",)},
        {"filter_buttons_seen": 0},
        {"extra_cond_text": "diaSource.snr > 5;"},
        {"catalog_label": "120 sources"},
    ],
)
def test_form_mismatches_or_unreadable_state_fail_closed(changes):
    assert check_form_observation(_form(**changes), EXPECTED)


MISSING = object()


def _dash_body(n_clicks, changed):
    submit_input = {"id": "submit_datatransfer", "property": "n_clicks"}
    if n_clicks is not MISSING:
        submit_input["value"] = n_clicks
    return json.dumps(
        {
            "output": "..submit_datatransfer.disabled...batch_id.children...topic_name.children..",
            "inputs": [submit_input],
            "changedPropIds": changed,
            "state": [],
        }
    )


def test_initial_submit_callback_is_distinguished_from_a_click():
    # Shape observed on the live portal right after a configuration upload (component mount).
    assert _is_initial_submit_callback(_dash_body(MISSING, ["submit_datatransfer.n_clicks"])) is True
    assert _is_initial_submit_callback(_dash_body(None, [])) is True
    assert _is_initial_submit_callback(_dash_body(None, ["submit_datatransfer.n_clicks"])) is True
    assert _is_initial_submit_callback(_dash_body(1, ["submit_datatransfer.n_clicks"])) is False
    assert _is_initial_submit_callback(_dash_body(2, [])) is False
    assert _is_initial_submit_callback("submit_datatransfer=1") is False
    assert _is_initial_submit_callback(json.dumps({"inputs": [], "changedPropIds": ["submit_datatransfer.n_clicks"]})) is False


class _Route:
    def __init__(self):
        self.action = None

    def abort(self):
        self.action = "abort"

    def continue_(self):
        self.action = "continue"


class _Request:
    def __init__(self, method, body):
        self.method, self.post_data = method, body


def test_unarmed_adapter_aborts_every_submit_callback_and_counts_clicks():
    adapter = PlaywrightPortalAdapter()
    cases = [
        (_Request("GET", None), "continue"),
        (_Request("POST", json.dumps({"inputs": [{"id": "submit_yaml_file", "property": "n_clicks", "value": 1}]})), "continue"),
        (_Request("POST", _dash_body(None, [])), "abort"),
        (_Request("POST", _dash_body(1, ["submit_datatransfer.n_clicks"])), "abort"),
        (_Request("POST", "garbled submit_datatransfer payload"), "abort"),
    ]
    for request, expected in cases:
        route = _Route()
        adapter._guard_submit_requests(route, request)
        assert route.action == expected
    assert adapter.blocked_initial_submit_callbacks == 1
    assert adapter.blocked_submit_requests == 2


def test_unarmed_adapter_refuses_submit_and_only_targets_the_public_portal():
    adapter = PlaywrightPortalAdapter()
    with pytest.raises(SubmissionDisabledError):
        adapter.submit()
    with pytest.raises(PortalError):
        PlaywrightPortalAdapter(url="https://example.org/download")


def test_a_blocked_submit_click_during_verification_blocks_the_request(tmp_path):
    registry = AcquisitionRegistry(tmp_path / "acquisitions")
    orchestrator = AcquisitionOrchestrator(registry, workdir=tmp_path / "work")
    record = orchestrator.record(orchestrator.plan("2026-02-25", "2026-03-25", as_of=date(2026, 10, 5)))
    portal = FakePortal()
    portal.blocked_submit_requests = 1
    with pytest.raises(PortalVerificationError, match="Submit request"):
        orchestrator.verify_portal(record, portal)
    assert registry.load(record.acquisition_id).state == S.BLOCKED


TOPIC = "ftransfer_lsst_2026-10-05_123456"


def test_producer_markers_with_log_prefixes_follow_the_fink_job_wording():
    from fink_lsst.acquisition.portal import classify_producer_log

    prefixed = "\n".join(
        [
            "Batch ID: 41",
            "Starting...",
            f"26/10/05 01:00:00 INFO -Livy- spark_lsst_transfer: Starting to send data to topic {TOPIC}",
            f"26/10/05 01:04:00 INFO -Livy- spark_lsst_transfer: Data available at topic: {TOPIC}",
            "26/10/05 01:04:00 INFO -Livy- spark_lsst_transfer: End.",
        ]
    )
    status = classify_producer_log(prefixed, TOPIC)
    assert status.state == "complete"
    assert len(status.evidence_lines) == 3
    out_of_order = classify_producer_log(f"INFO End.\nINFO Data available at topic: {TOPIC}\n", TOPIC)
    assert out_of_order.state == "running"
    portal_failure = "Batch ID: 41\nFailed. Please, contact contact@fink-broker.org with your batch ID and the message below.\nTraceback (most recent call last):\n"
    assert classify_producer_log(portal_failure, TOPIC).state == "failed"
    assert classify_producer_log(f"Starting to send data to topic {TOPIC}_schema\n", TOPIC).state == "not_started"


def test_armed_adapter_lets_exactly_one_click_through_only_inside_submit():
    adapter = PlaywrightPortalAdapter(live_submit_enabled=True)
    route = _Route()
    adapter._guard_submit_requests(route, _Request("POST", _dash_body(1, ["submit_datatransfer.n_clicks"])))
    assert route.action == "abort" and adapter.blocked_submit_requests == 1  # click outside submit() is blocked
    adapter._submit_request_allowance = 1  # what submit() sets just before its single click
    for expected in ("continue", "abort"):
        route = _Route()
        adapter._guard_submit_requests(route, _Request("POST", _dash_body(1, ["submit_datatransfer.n_clicks"])))
        assert route.action == expected
    route = _Route()
    adapter._guard_submit_requests(route, _Request("POST", _dash_body(MISSING, ["submit_datatransfer.n_clicks"])))
    assert route.action == "abort"
    assert adapter.blocked_initial_submit_callbacks == 1 and adapter.blocked_submit_requests == 2

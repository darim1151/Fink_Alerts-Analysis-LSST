"""Science profile, half-open window, and canonical request tests (FINK-G3B.0)."""

import dataclasses
from datetime import date

import pytest
import yaml

from fink_lsst.acquisition.planner import (
    MAX_WINDOW_NIGHTS,
    AcquisitionWindow,
    PlanningError,
    build_acquisition_request,
    canonical_json,
    request_from_dict,
    request_from_mapping,
)
from fink_lsst.acquisition.profile import (
    LIGHT_STATIC_PACKET,
    LSST_LIGHT_STATIC_ALL_ALERTS_V1,
    PRODUCTION_PROFILE_NAME,
    SCIENCE_PROFILES,
    ProfileIntegrityError,
    get_science_profile,
    resolve_profile,
)
from fink_lsst.data_root import validate_path_component


AS_OF = date(2026, 10, 5)


def _request(start="2026-02-25", stop="2026-03-25", **kwargs):
    kwargs.setdefault("as_of", AS_OF)
    return build_acquisition_request(start, stop, **kwargs)


# ---------------------------------------------------------------- date semantics


def test_one_night_half_open_maps_to_same_day_inclusive_portal_window():
    window = AcquisitionWindow.from_values("2026-02-25", "2026-02-26")
    assert window.nights == ("2026-02-25",)
    assert window.portal_startdate == "2026-02-25"
    assert window.portal_stopdate == "2026-02-25"
    assert window.scope == "full_night"
    assert window.component == "2026-02-25_to_2026-02-26"


def test_month_one_is_28_nights_and_portal_stops_on_march_24():
    window = AcquisitionWindow.from_values("2026-02-25", "2026-03-25")
    assert len(window.nights) == 28
    assert window.nights[0] == "2026-02-25"
    assert window.nights[3] == "2026-02-28"
    assert window.nights[4] == "2026-03-01"
    assert window.nights[-1] == "2026-03-24"
    assert "2026-03-25" not in window.nights
    assert window.portal_startdate == "2026-02-25"
    assert window.portal_stopdate == "2026-03-24"
    assert window.scope == "date_range"


@pytest.mark.parametrize(
    "start, stop, nights, portal_stop",
    [
        ("2024-02-28", "2024-03-01", ["2024-02-28", "2024-02-29"], "2024-02-29"),  # leap day included
        ("2026-02-28", "2026-03-01", ["2026-02-28"], "2026-02-28"),  # non-leap month end
        ("2026-03-31", "2026-04-02", ["2026-03-31", "2026-04-01"], "2026-04-01"),
        ("2025-12-31", "2026-01-02", ["2025-12-31", "2026-01-01"], "2026-01-01"),  # year boundary
    ],
)
def test_leap_and_month_boundaries(start, stop, nights, portal_stop):
    window = AcquisitionWindow.from_values(start, stop)
    assert list(window.nights) == nights
    assert window.portal_startdate == start
    assert window.portal_stopdate == portal_stop


def test_other_generic_ranges_are_date_range_scope():
    for start, stop, count in (("2026-03-25", "2026-04-25", 31), ("2026-04-10", "2026-04-23", 13)):
        window = AcquisitionWindow.from_values(start, stop)
        assert len(window.nights) == count
        assert window.scope == "date_range"


@pytest.mark.parametrize(
    "start, stop",
    [
        ("2026-03-25", "2026-02-25"),  # reversed
        ("2026-02-25", "2026-02-25"),  # empty half-open window
    ],
)
def test_reversed_or_empty_ranges_are_rejected(start, stop):
    with pytest.raises(PlanningError, match="stop must be after start"):
        AcquisitionWindow.from_values(start, stop)


@pytest.mark.parametrize(
    "value",
    ["2026-2-25", "20260225", "2026-02-25T00:00:00", "2026-02-30", "2026-W09-3", " 2026-02-25", "", None, 20260225, "2026/02/25"],
)
def test_malformed_dates_are_rejected(value):
    with pytest.raises(PlanningError):
        AcquisitionWindow.from_values(value, "2026-03-25")


def test_datetime_objects_are_rejected_but_dates_accepted():
    from datetime import datetime

    with pytest.raises(PlanningError):
        AcquisitionWindow.from_values(datetime(2026, 2, 25), "2026-03-25")
    window = AcquisitionWindow.from_values(date(2026, 2, 25), date(2026, 3, 25))
    assert window.portal_stopdate == "2026-03-24"


def test_window_size_guard():
    AcquisitionWindow.from_values("2026-01-01", date.fromordinal(date(2026, 1, 1).toordinal() + MAX_WINDOW_NIGHTS))
    with pytest.raises(PlanningError, match="at most"):
        AcquisitionWindow.from_values("2026-01-01", date.fromordinal(date(2026, 1, 1).toordinal() + MAX_WINDOW_NIGHTS + 1))


def test_nights_that_have_not_finished_and_settled_are_rejected():
    with pytest.raises(PlanningError, match="not finished"):
        build_acquisition_request("2026-10-01", "2026-10-06", as_of=AS_OF)
    with pytest.raises(PlanningError, match="not finished"):
        build_acquisition_request("2026-10-04", "2026-10-06", as_of=AS_OF)
    with pytest.raises(PlanningError, match="last requestable night is 2026-10-03"):
        build_acquisition_request("2026-10-03", "2026-10-05", as_of=AS_OF)  # yesterday (UTC) has not settled
    assert build_acquisition_request("2026-10-02", "2026-10-04", as_of=AS_OF).portal_stopdate == "2026-10-03"


# ---------------------------------------------------------------- science profile


def test_production_profile_is_fixed_light_static_all_alerts():
    profile = get_science_profile()
    assert profile is LSST_LIGHT_STATIC_ALL_ALERTS_V1
    assert profile.name == PRODUCTION_PROFILE_NAME == "lsst_light_static_all_alerts_v1"
    assert profile.survey == "lsst"
    assert profile.packet == LIGHT_STATIC_PACKET == "Light static packet"
    assert profile.filters == ()
    assert profile.blocks == ()
    assert profile.catalog_filename is None
    assert profile.extra_cond is None
    assert list(SCIENCE_PROFILES) == [PRODUCTION_PROFILE_NAME]


def test_profile_is_immutable():
    with pytest.raises(dataclasses.FrozenInstanceError):
        LSST_LIGHT_STATIC_ALL_ALERTS_V1.filters = ("in_tns",)
    with pytest.raises(TypeError):
        SCIENCE_PROFILES["other"] = LSST_LIGHT_STATIC_ALL_ALERTS_V1


@pytest.mark.parametrize(
    "changes",
    [
        {"filters": ("in_tns",)},
        {"blocks": ("b_is_new",)},
        {"catalog_filename": "targets.csv"},
        {"extra_cond": "diaSource.snr > 5;"},
        {"packet": "Full packet"},
        {"packet": "Light SSO packet"},
        {"survey": "ztf"},
    ],
)
def test_profile_mutation_without_new_version_fails_closed(changes):
    mutated = dataclasses.replace(LSST_LIGHT_STATIC_ALL_ALERTS_V1, **changes)
    with pytest.raises(ProfileIntegrityError):
        resolve_profile(mutated)
    with pytest.raises(ProfileIntegrityError):
        build_acquisition_request("2026-02-25", "2026-03-25", profile=mutated, as_of=AS_OF)


def test_in_place_tampering_of_registered_profile_is_detected():
    profile = LSST_LIGHT_STATIC_ALL_ALERTS_V1
    original = profile.filters
    object.__setattr__(profile, "filters", ("in_tns",))
    try:
        with pytest.raises(ProfileIntegrityError):
            get_science_profile()
        with pytest.raises(ProfileIntegrityError):
            _request()
    finally:
        object.__setattr__(profile, "filters", original)
    assert get_science_profile() is profile


def test_unknown_profile_names_are_rejected():
    with pytest.raises(ProfileIntegrityError):
        get_science_profile("lsst_full_packet_v1")


# ---------------------------------------------------------------- canonical request


def test_request_separates_scientific_identity_from_operational_metadata():
    request = _request(created_at_utc="2026-10-05T01:02:03Z")
    identity = request.scientific_identity()
    assert identity["science_profile"] == PRODUCTION_PROFILE_NAME
    assert identity["window"] == {"start": "2026-02-25", "stop": "2026-03-25", "semantics": "half_open_utc_dates"}
    assert identity["scope"] == "date_range"
    assert "created_at_utc" not in canonical_json(identity)
    payload = request.to_dict()
    assert payload["schema_version"] == 1
    assert payload["portal_dates_inclusive"] == {"startdate": "2026-02-25", "stopdate": "2026-03-24"}
    assert len(payload["expected_dates"]) == 28
    assert payload["operational"]["created_at_utc"] == "2026-10-05T01:02:03Z"
    assert set(payload["scientific_identity"]) == set(identity)
    assert len(request.fingerprint) == 64
    validate_path_component(request.acquisition_id, "acquisition_id")
    assert request.acquisition_id.startswith("acq_lsst_ls_v1_2026-02-25_to_2026-03-25_")
    assert request.fingerprint[:12] in request.acquisition_id


def test_operational_metadata_does_not_affect_fingerprint():
    first = _request(created_at_utc="2026-10-05T00:00:00Z", generator="laptop-a")
    second = _request(created_at_utc="2027-01-01T12:00:00Z", generator="arnor", as_of=date(2026, 12, 1))
    assert first.fingerprint == second.fingerprint
    assert first.acquisition_id == second.acquisition_id
    assert first.to_dict()["operational"] != second.to_dict()["operational"]


def test_equivalent_requests_from_differently_formatted_yaml_hash_identically():
    text_a = "start: '2026-02-25'\nstop: '2026-03-25'\nscience_profile: lsst_light_static_all_alerts_v1\n"
    text_b = (
        "# operator notes\n"
        "science_profile:   lsst_light_static_all_alerts_v1\n"
        "created_at_utc: '2031-01-01T00:00:00Z'\n"
        "stop: 2026-03-25\n"
        "start: 2026-02-25\n"
    )
    text_c = '{"stop": "2026-03-25", "start": "2026-02-25"}'
    fingerprints = {request_from_mapping(yaml.safe_load(text)).fingerprint for text in (text_a, text_b, text_c)}
    assert fingerprints == {_request().fingerprint}


def test_different_windows_hash_differently():
    fingerprints = {
        _request("2026-02-25", "2026-03-25").fingerprint,
        _request("2026-02-25", "2026-03-24").fingerprint,
        _request("2026-02-26", "2026-03-25").fingerprint,
        _request("2026-03-25", "2026-04-25").fingerprint,
        _request("2026-02-25", "2026-02-26").fingerprint,
    }
    assert len(fingerprints) == 5


def test_profile_version_participates_in_fingerprint(monkeypatch):
    from fink_lsst.acquisition import planner

    baseline = planner.fingerprint_identity(_request().scientific_identity())
    identity = dict(_request().scientific_identity())
    identity["profile_content_sha256"] = "0" * 64
    assert planner.fingerprint_identity(identity) != baseline
    identity = dict(_request().scientific_identity())
    identity["science_profile"] = "lsst_light_static_all_alerts_v2"
    assert planner.fingerprint_identity(identity) != baseline


def test_canonical_json_is_order_and_whitespace_independent():
    assert canonical_json({"b": 1, "a": [1, {"d": 2, "c": 3}]}) == canonical_json({"a": [1, {"c": 3, "d": 2}], "b": 1})
    assert canonical_json({"a": 1}) == '{"a":1}'


def test_request_round_trips_and_detects_tampering():
    request = _request()
    payload = request.to_dict()
    assert request_from_dict(payload) == request
    for path, value in (
        (("portal_dates_inclusive", "stopdate"), "2026-03-25"),
        (("fingerprint",), "f" * 64),
        (("acquisition_id",), "acq_other"),
        (("scientific_identity", "window", "stop"), "2026-03-26"),
        (("expected_dates",), ["2026-02-25"]),
    ):
        tampered = yaml.safe_load(yaml.safe_dump(payload))
        target = tampered
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        with pytest.raises(PlanningError):
            request_from_dict(tampered)


def test_request_mapping_rejects_profile_content_overrides():
    for key, value in (
        ("filters", ["in_tns"]), ("packet", "Full packet"), ("extra_cond", "x > 1;"), ("catalog_filename", "c.csv"), ("blocks", ["b_is_new"]),
        ("catalogue", "c.csv"), ("tags", ["in_tns"]), ("fink_filter", "in_tns"), ("extraCond", "x;"), ("packet_type", "full"),
    ):
        with pytest.raises(PlanningError, match="science profile"):
            request_from_mapping({"start": "2026-02-25", "stop": "2026-03-25", key: value})

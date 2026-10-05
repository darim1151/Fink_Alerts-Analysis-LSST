"""Portal config compiler and semantic round-trip tests (FINK-G3B.0)."""

import hashlib
from datetime import date
from pathlib import Path

import pytest
import yaml

from fink_lsst.acquisition.planner import build_acquisition_request
from fink_lsst.acquisition.portal_config import (
    PortalConfigError,
    PortalConfigMismatch,
    assert_portal_config_matches,
    compare_portal_configs,
    compile_portal_config,
    parse_portal_config,
    render_portal_yaml,
)


AS_OF = date(2026, 10, 5)
G3A_PORTAL_CONFIG = Path("configs/portal_requests/datatransfer_20261004_113217.yml")


def _month_one():
    return build_acquisition_request("2026-02-25", "2026-03-25", as_of=AS_OF)


def _expected():
    return compile_portal_config(_month_one())


def _mutated_yaml(**changes):
    mapping = yaml.safe_load(render_portal_yaml(_expected()))
    for key, value in changes.items():
        if key in {"startdate", "stopdate"}:
            mapping["dates"][key] = value
        else:
            mapping[key] = value
    return yaml.safe_dump(mapping, sort_keys=True)


def test_month_one_portal_config_is_exact():
    mapping = yaml.safe_load(render_portal_yaml(_expected()))
    assert mapping == {
        "blocks": [],
        "catalog_filename": None,
        "content": ["Light static packet"],
        "dates": {"startdate": "2026-02-25", "stopdate": "2026-03-24"},
        "extra_cond": None,
        "filters": [],
    }


def test_compiled_yaml_is_byte_identical_to_the_real_g3a_portal_download():
    """The portal-generated G3A file is the reference for format; compiling the same request must reproduce it."""
    g3a = build_acquisition_request("2026-02-25", "2026-02-26", as_of=AS_OF)
    text = render_portal_yaml(compile_portal_config(g3a))
    reference = G3A_PORTAL_CONFIG.read_bytes()
    assert text.encode("utf-8") == reference
    assert hashlib.sha256(reference).hexdigest() == "9e18799ce44a413cbf14ae294dfc61766e973d96ab1b71461cce6dea39f09249"


def test_semantic_round_trip_matches_regardless_of_formatting():
    expected = _expected()
    reformatted = (
        "filters: []\n"
        "extra_cond:\n"
        "dates: {stopdate: 2026-03-24, startdate: 2026-02-25}\n"
        "content: [Light static packet]\n"
        "catalog_filename: ~\n"
        "blocks: []\n"
    )
    observed = parse_portal_config(reformatted)
    assert compare_portal_configs(expected, observed) == []
    assert_portal_config_matches(expected, observed)
    assert_portal_config_matches(expected, parse_portal_config(render_portal_yaml(expected)))


@pytest.mark.parametrize(
    "changes, needle",
    [
        ({"stopdate": "2026-03-25"}, "stopdate"),  # half-open stop leaked into inclusive portal
        ({"startdate": "2026-02-24"}, "startdate"),
        ({"content": ["Full packet"]}, "content"),
        ({"content": ["Light SSO packet"]}, "content"),
        ({"content": ["Light static packet", "diaSource.ra"]}, "content"),
        ({"content": []}, "content"),  # empty means "all fields" in the portal
        ({"filters": ["in_tns"]}, "filters"),
        ({"filters": ["~in_tns"]}, "filters"),
        ({"blocks": ["b_is_new"]}, "blocks"),
        ({"catalog_filename": "targets.csv"}, "catalog_filename"),
        ({"extra_cond": "diaSource.snr > 5;"}, "extra_cond"),
    ],
)
def test_semantic_mismatches_fail_closed(changes, needle):
    observed = parse_portal_config(_mutated_yaml(**changes))
    mismatches = compare_portal_configs(_expected(), observed)
    assert mismatches and any(needle in item for item in mismatches)
    with pytest.raises(PortalConfigMismatch):
        assert_portal_config_matches(_expected(), observed)


@pytest.mark.parametrize(
    "text",
    [
        "dates: {startdate: '2026-02-25', stopdate: '2026-03-24'}\ncontent: [Light static packet]\nfilters: []\nblocks: []\ncatalog_filename: null\n",  # missing extra_cond
        _mutated_yaml(survey="ztf"),  # unknown key
        "- just\n- a list\n",
        "dates: {startdate: 'Feb 25', stopdate: '2026-03-24'}\ncontent: [Light static packet]\nfilters: []\nblocks: []\ncatalog_filename: null\nextra_cond: null\n",
        "dates: '2026-02-25'\ncontent: [Light static packet]\nfilters: []\nblocks: []\ncatalog_filename: null\nextra_cond: null\n",
        "dates: {startdate: '2026-02-25', stopdate: '2026-03-24', timezone: UTC}\ncontent: [Light static packet]\nfilters: []\nblocks: []\ncatalog_filename: null\nextra_cond: null\n",
        "{{not yaml",
    ],
)
def test_unparseable_or_unexpected_portal_configs_are_rejected(text):
    with pytest.raises(PortalConfigError):
        parse_portal_config(text)


def test_empty_sql_text_is_semantically_no_condition_but_real_sql_is_not():
    assert compare_portal_configs(_expected(), parse_portal_config(_mutated_yaml(extra_cond="  \n"))) == []
    assert compare_portal_configs(_expected(), parse_portal_config(_mutated_yaml(extra_cond=";"))) != []


def test_compiler_only_accepts_canonical_requests():
    with pytest.raises(TypeError):
        compile_portal_config({"start": "2026-02-25", "stop": "2026-03-25", "filters": []})


# What the live portal returned from "Download configuration" after uploading the
# compiled Month-1 YAML during G3B.0 development (2026-10-05): extra_cond comes back
# as an empty list of lines rather than null.
LIVE_PORTAL_DOWNLOAD_AFTER_UPLOAD = (
    "blocks: []\n"
    "catalog_filename: null\n"
    "content:\n"
    "- Light static packet\n"
    "dates:\n"
    "  startdate: '2026-02-25'\n"
    "  stopdate: '2026-03-24'\n"
    "extra_cond: []\n"
    "filters: []\n"
)


def test_live_portal_download_with_empty_condition_list_matches():
    observed = parse_portal_config(LIVE_PORTAL_DOWNLOAD_AFTER_UPLOAD)
    assert observed.extra_cond is None
    assert compare_portal_configs(_expected(), observed) == []


def test_condition_lists_with_sql_still_fail_closed():
    for value in (["diaSource.snr > 5;"], ["", "pred.is_sso = false;"]):
        observed = parse_portal_config(_mutated_yaml(extra_cond=value))
        assert any("extra_cond" in item for item in compare_portal_configs(_expected(), observed))
    assert compare_portal_configs(_expected(), parse_portal_config(_mutated_yaml(extra_cond=["", "  "]))) == []
    with pytest.raises(PortalConfigError):
        parse_portal_config(_mutated_yaml(extra_cond=[1, 2]))

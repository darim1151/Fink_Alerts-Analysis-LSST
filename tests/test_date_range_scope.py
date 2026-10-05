"""Generic Light Static date_range scope semantics (FINK-G3B.0)."""

from pathlib import Path

import pytest
import yaml

from fink_lsst.bulk_transfer.run_index import summarize_runs
from fink_lsst.bulk_transfer.run_manifest import (
    RunManifest,
    derive_claim_state,
    derive_default_paths,
    derive_expected_nights,
    load_run_manifest,
    manifest_to_dict,
    validate_run_manifest,
)
from fink_lsst.bulk_transfer.run_state import normalize_data_scope
from fink_lsst.bulk_transfer.scopes import claim_policy_for_scope, get_scope_policy, normalize_scope, scope_paths
from fink_lsst.bulk_transfer.topic_registry import build_completeness_scope, build_topic_entry
from fink_lsst.bulk_transfer.validation import derive_claim_updates


TOPIC = "ftransfer_lsst_2026-10-05_123456"


def _manifest(**overrides):
    start, stop = overrides.pop("startdate", "2026-02-25"), overrides.pop("stopdate", "2026-03-25")
    values = dict(
        schema_version=1,
        run_name="acq_test",
        run_id=TOPIC,
        survey="lsst",
        broker="fink",
        topic=TOPIC,
        batch_id="41",
        startdate=start,
        stopdate=stop,
        date_mode="utc_window",
        scope="date_range",
        packet_type="light_static",
        content="Light static packet",
        filters=[],
        is_all_alert=True,
        expected_nights=derive_expected_nights(start, stop),
        lifecycle_state="topic_registered",
    )
    values.update(overrides)
    manifest = RunManifest(**values)
    manifest.paths = derive_default_paths(manifest)
    manifest.claim_state = derive_claim_state(manifest)
    return manifest


def test_date_range_is_a_light_static_multi_night_scope():
    assert normalize_scope("date_range") == "date_range"
    policy = get_scope_policy("date_range")
    assert policy.path_component == "date_range"
    assert policy.is_multi_night and not policy.is_single_night
    assert policy.is_all_alert and not policy.is_tag_filtered
    assert policy.packet_type == "light_static" and policy.content_type == "Light static packet"
    assert normalize_data_scope("date_range") == "date_range"


def test_date_range_paths():
    paths = scope_paths("date_range", "2026-02-25", "2026-03-25", TOPIC)
    assert paths["raw_delivery_dir"] == f"data/raw/data_transfer/date_range/2026-02-25_to_2026-03-25/{TOPIC}"
    manifest = _manifest()
    assert manifest.paths.raw_dir == f"data/raw/data_transfer/date_range/2026-02-25_to_2026-03-25/{TOPIC}"
    assert manifest.paths.processed_dir.startswith("data/processed/data_transfer/date_range/2026-02-25_to_2026-03-25/")
    assert manifest.paths.outputs_dir.startswith("outputs/data_transfer/date_range/2026-02-25_to_2026-03-25/")


def test_range_claims_are_not_week_claims():
    assert claim_policy_for_scope("date_range") == {"range_complete_default": "unresolved", "allow_completeness_only_after_validation": True}
    assert claim_policy_for_scope("full_week") == {"week_complete_default": "unresolved", "allow_completeness_only_after_validation": True}
    assert claim_policy_for_scope("full_night") == {"night_complete_default": "unresolved", "allow_completeness_only_after_validation": True}

    registered = _manifest()
    assert manifest_to_dict(registered.claim_state) == {
        "all_alert_completeness": "blocked",
        "night_completeness": "blocked",
        "week_completeness": "blocked",
        "range_completeness": "unresolved",
    }
    inspected = _manifest(lifecycle_state="raw_inspected")
    assert manifest_to_dict(inspected.claim_state) == {
        "all_alert_completeness": "unresolved",
        "night_completeness": "unresolved",
        "week_completeness": "blocked",
        "range_completeness": "unresolved",
    }
    assert validate_run_manifest(inspected)[0] == []


def test_range_completeness_cannot_be_allowed_by_default():
    manifest = _manifest(lifecycle_state="validated")
    manifest.claim_state.range_completeness = "allowed"
    errors, _warnings = validate_run_manifest(manifest)
    assert any("range_completeness cannot be allowed" in error for error in errors)
    updates = derive_claim_updates(manifest, {"validation_status": "passed", "execution_manifest": {}})
    assert updates["range_completeness"] != "allowed"
    assert updates["week_completeness"] != "allowed"


def test_date_range_rejects_full_packet_and_filters():
    errors, _warnings = validate_run_manifest(_manifest(packet_type="full", content="Full packet"))
    assert any("date_range" in error and "light_static" in error for error in errors)
    errors, _warnings = validate_run_manifest(_manifest(filters=["in_tns"], is_all_alert=False))
    assert any("date_range" in error for error in errors)


def test_date_range_topic_entry_and_completeness_scope():
    entry = build_topic_entry(
        scope="date_range",
        survey="lsst",
        topic=TOPIC,
        startdate="2026-02-25",
        stopdate="2026-03-25",
        content="Light static packet",
        all_alert=True,
    )
    assert entry["packet_type"] == "light_static"
    assert entry["claim_policy"]["range_complete_default"] == "unresolved"
    completeness = build_completeness_scope({"scope": "date_range", "all_alerts": True, "filter": None})
    assert completeness["scope"] == "date_range"
    assert completeness["range_complete"] is False
    assert "week" not in completeness["reason"]


def test_historical_manifests_keep_their_scopes_paths_and_claims():
    index = yaml.safe_load(Path("configs/runs/index.yaml").read_text(encoding="utf-8"))
    for name, item in index["runs"].items():
        raw = yaml.safe_load(Path(item["config"]).read_text(encoding="utf-8"))
        manifest = load_run_manifest(item["config"])
        assert manifest.paths.raw_dir == raw["paths"]["raw_dir"], name
        for key in ("all_alert_completeness", "night_completeness", "week_completeness"):
            assert getattr(manifest.claim_state, key) == raw["claim_state"][key], (name, key)
        assert manifest.claim_state.range_completeness == "blocked", name
        assert normalize_data_scope(raw["scope"]) == manifest.scope
    week = next(item for item in summarize_runs("configs/runs/index.yaml") if item["name"] == "full_week_light_static_2026-02-25_to_2026-03-04")
    assert week["claim_state"]["week_completeness"] == "unresolved"


@pytest.mark.parametrize("scope", ["full_week", "full_week_full_packet", "full_night", "smoke_delivery"])
def test_existing_scope_policies_are_unchanged(scope):
    policy = get_scope_policy(scope)
    assert policy.path_component == scope

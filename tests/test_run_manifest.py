import subprocess
import sys
from pathlib import Path

import pytest

from fink_lsst.bulk_transfer.run_index import list_runs, load_run_index, summarize_runs
from fink_lsst.bulk_transfer.run_manifest import (
    ClaimStateSet,
    RunManifest,
    RunPaths,
    derive_claim_state,
    derive_default_paths,
    derive_expected_nights,
    load_run_manifest,
    manifest_from_dict,
    manifest_to_dict,
    normalize_packet_type,
    validate_run_manifest,
    write_run_manifest,
)
from fink_lsst.bulk_transfer.run_state import check_lifecycle_transition


def test_expected_nights_single_and_multi_night():
    assert derive_expected_nights("2026-02-25", "2026-02-26") == ["2026-02-25"]
    assert derive_expected_nights("2026-02-25", "2026-03-04") == [
        "2026-02-25",
        "2026-02-26",
        "2026-02-27",
        "2026-02-28",
        "2026-03-01",
        "2026-03-02",
        "2026-03-03",
    ]


def test_invalid_date_window_rejected():
    with pytest.raises(ValueError):
        derive_expected_nights("2026-02-26", "2026-02-26")
    manifest = _manifest(startdate="2026-02-26", stopdate="2026-02-25", expected_nights=["2026-02-26"])
    errors, _warnings = validate_run_manifest(manifest)
    assert any("startdate must be before stopdate" in error for error in errors)


def test_filters_and_all_alert_contradiction_rejected():
    manifest = _manifest(filters=["in_tns"], is_all_alert=True)
    errors, _warnings = validate_run_manifest(manifest)
    assert any("filters imply" in error for error in errors)
    assert any("all-alert manifests" in error for error in errors)


def test_default_paths_are_under_data_transfer_roots():
    manifest = _manifest(scope="full_week", packet_type="full", filters=[], is_all_alert=True)
    paths = derive_default_paths(manifest)
    assert paths.raw_dir.startswith("data/raw/data_transfer/full_week_full_packet/")
    assert paths.processed_dir.startswith("data/processed/data_transfer/full_week_full_packet/")
    assert paths.outputs_dir.startswith("outputs/data_transfer/full_week_full_packet/")


def test_packet_type_normalization():
    assert normalize_packet_type("Full packet") == "full"
    assert normalize_packet_type("Light static packet") == "light_static"
    assert normalize_packet_type("Medium packet") == "medium"
    assert normalize_packet_type("schema only") == "unknown"


def test_claim_state_for_smoke_tag_filtered_blocks_completeness():
    manifest = _manifest(scope="bounded_tag", filters=["in_tns"], is_all_alert=False, lifecycle_state="validated")
    claims = derive_claim_state(manifest)
    assert claims.all_alert_completeness == "blocked"
    assert claims.night_completeness == "blocked"
    assert claims.week_completeness == "blocked"


def test_claim_state_for_full_week_full_packet_stays_unresolved_not_allowed():
    manifest = _manifest(scope="full_week", packet_type="full", filters=[], is_all_alert=True, lifecycle_state="raw_missing")
    claims = derive_claim_state(manifest)
    assert claims.week_completeness == "unresolved"
    assert claims.all_alert_completeness == "blocked"


def test_manifest_load_write_roundtrip(tmp_path):
    manifest = _manifest()
    path = tmp_path / "run.yaml"
    write_run_manifest(manifest, path)
    loaded = load_run_manifest(path)
    assert manifest_to_dict(loaded) == manifest_to_dict(manifest)


def test_manifest_rejects_credential_like_fields_and_values():
    payload = manifest_to_dict(_manifest())
    payload["token"] = "abc"
    manifest = manifest_from_dict(payload)
    errors, _warnings = validate_run_manifest(manifest)
    assert any("credential-looking" in error for error in errors)


def test_manifest_rejects_paths_outside_allowed_roots():
    manifest = _manifest(paths=RunPaths(raw_dir="tmp/raw", processed_dir="data/processed/data_transfer/run", outputs_dir="outputs/data_transfer/run"))
    errors, _warnings = validate_run_manifest(manifest)
    assert any("paths.raw_dir" in error for error in errors)


def test_lifecycle_transitions_block_partial_to_validated():
    assert check_lifecycle_transition("raw_partial", "validated").allowed is False
    warning_transition = check_lifecycle_transition("validated_with_warnings", "validated")
    assert warning_transition.allowed is True
    assert "explicitly resolved" in warning_transition.reason


def test_run_index_loading_and_summary():
    index = load_run_index("configs/runs/index.yaml")
    runs = list_runs(index)
    assert {run["name"] for run in runs} == {
        "smoke_in_tns_2026-02-25",
        "full_week_full_packet_2026-02-25_to_2026-03-04",
        "full_week_light_static_2026-02-25_to_2026-03-04",
    }
    summaries = summarize_runs("configs/runs/index.yaml")
    assert any(item["packet_type"] == "full" for item in summaries)
    assert any(
        item["name"] == "full_week_light_static_2026-02-25_to_2026-03-04"
        and item["packet_type"] == "light_static"
        and item["claim_state"]["week_completeness"] == "unresolved"
        for item in summaries
    )


def test_manifest_validation_cli_success_and_failure(tmp_path):
    success = subprocess.run(
        [sys.executable, "scripts/validate_run_manifest.py", "--all"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert success.returncode == 0
    assert "full_week_full_packet_2026-02-25_to_2026-03-04" in success.stdout
    assert "full_week_light_static_2026-02-25_to_2026-03-04" in success.stdout

    bad = manifest_to_dict(_manifest(startdate="2026-02-26", stopdate="2026-02-25", expected_nights=["2026-02-26"]))
    bad_path = tmp_path / "bad.yaml"
    bad_path.write_text("schema_version: 1\nrun_name: bad\nstartdate: '2026-02-26'\nstopdate: '2026-02-25'\n", encoding="utf-8")
    failure = subprocess.run(
        [sys.executable, "scripts/validate_run_manifest.py", "--run-config", str(bad_path)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert failure.returncode != 0
    assert "error:" in failure.stdout or "ValueError" in failure.stderr
    assert bad


def test_existing_topic_based_cli_still_parses(tmp_path):
    registry = tmp_path / "registry.yaml"
    registry.write_text(
        """
version: 1
topics:
  - topic: ftransfer_lsst_test_missing
    scope: full_week_full_packet
    survey: lsst
    utc_start: '2026-02-25'
    utc_stop: '2026-02-26'
    startdate: '2026-02-25'
    stopdate: '2026-02-26'
    content: Full packet
    packet_type: full
    filters: []
    is_all_alert: true
    raw_delivery_dir: data/raw/data_transfer/test_missing/topic_mode
""",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/triage_full_packet_download.py",
            "--registry",
            str(registry),
            "--topic",
            "ftransfer_lsst_test_missing",
            "--json",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0
    assert "ftransfer_lsst_test_missing" in completed.stdout


def test_unified_runner_safe_stages_dry_run(tmp_path):
    config = _missing_run_config(tmp_path, "run_manifest_missing")
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_analysis.py",
            "--run-config",
            str(config),
            "--stage",
            "triage",
            "--dry-run",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0
    assert '"dry_run": true' in completed.stdout
    assert '"stage": "triage"' in completed.stdout

    rejected = subprocess.run(
        [
            sys.executable,
            "scripts/run_analysis.py",
            "--run-config",
            str(config),
            "--stage",
            "ingest",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert rejected.returncode == 2
    assert "raw_missing" in rejected.stdout
    assert "refused" in rejected.stdout


def _manifest(**overrides):
    values = {
        "schema_version": 1,
        "run_name": "test_run",
        "run_id": "test_run",
        "survey": "lsst",
        "broker": "fink",
        "topic": "ftransfer_lsst_test",
        "batch_id": None,
        "startdate": "2026-02-25",
        "stopdate": "2026-02-26",
        "date_mode": "utc_window",
        "scope": "bounded_tag",
        "packet_type": "light_static",
        "content": "Light static packet",
        "filters": ["in_tns"],
        "is_all_alert": False,
        "expected_nights": ["2026-02-25"],
        "lifecycle_state": "validated",
        "claim_state": ClaimStateSet(),
        "paths": RunPaths(
            raw_dir="data/raw/data_transfer/smoke_delivery/test",
            processed_dir="data/processed/data_transfer/smoke_delivery/test",
            outputs_dir="outputs/data_transfer/smoke_delivery/test",
        ),
        "notes": "",
    }
    values.update(overrides)
    if "expected_nights" not in overrides and values["startdate"] < values["stopdate"]:
        values["expected_nights"] = derive_expected_nights(values["startdate"], values["stopdate"])
    return RunManifest(**values)


def _missing_run_config(tmp_path, name):
    path = tmp_path / f"{name}.yaml"
    path.write_text(
        f"""
schema_version: 1
run_name: {name}
run_id: {name}
survey: lsst
broker: fink
topic: ftransfer_lsst_{name}
batch_id: null
startdate: '2026-02-25'
stopdate: '2026-02-26'
date_mode: utc_window
scope: full_week
packet_type: full
content: Full packet
filters: []
is_all_alert: true
expected_nights:
- '2026-02-25'
lifecycle_state: raw_missing
claim_state:
  all_alert_completeness: blocked
  night_completeness: blocked
  week_completeness: unresolved
paths:
  raw_dir: data/raw/data_transfer/test_missing/{name}
  processed_dir: data/processed/data_transfer/test_missing/{name}
  outputs_dir: outputs/data_transfer/test_missing/{name}
processing:
  split_by_night: true
  allow_partial: false
  max_files: null
  diagnostics: true
  validation: true
  plots: true
download_evidence:
  kafka_lag_zero: null
  expected_total_messages: 10
  terminal_progress_messages: null
  local_readable_rows: 0
  raw_file_count: 0
  raw_size_bytes: 0
notes: synthetic missing raw test config
""",
        encoding="utf-8",
    )
    return path

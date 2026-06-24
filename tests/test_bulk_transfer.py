from pathlib import Path

import pandas as pd
import pytest

from fink_lsst.bulk_transfer.client_probe import run_safe_client_probes
from fink_lsst.bulk_transfer.config import validate_data_transfer_config
from fink_lsst.bulk_transfer.install_helper import build_install_status, render_install_guide
from fink_lsst.bulk_transfer.ingest import normalize_bulk_alerts, split_bulk_tables
from fink_lsst.bulk_transfer.output_inspector import summarize_delivery, summarize_dropzones
from fink_lsst.bulk_transfer.readiness_decision import decide_data_transfer_readiness
from fink_lsst.bulk_transfer.registration import render_registration_checklist
from fink_lsst.bulk_transfer.reporting import render_setup_report
from fink_lsst.bulk_transfer.request_builder import (
    build_data_transfer_request,
    build_profiled_data_transfer_request,
    build_manual_portal_checklist,
    build_smoke_manual_portal_checklist,
    render_request_as_markdown,
    validate_data_transfer_request,
)
from fink_lsst.bulk_transfer.setup_check import build_setup_status_report
from fink_lsst.bulk_transfer.validation import validate_delivery_tables


def test_data_transfer_config_validation_rejects_pre_alert_dates():
    config = _config()
    config["target_startdate"] = "2026-01-01"
    with pytest.raises(ValueError):
        validate_data_transfer_config(config)


def test_data_transfer_config_requires_dry_run_and_submission_disabled():
    config = _config()
    config["dry_run"] = False
    with pytest.raises(ValueError):
        validate_data_transfer_config(config)
    config = _config()
    config["safety"]["allow_submit"] = True
    with pytest.raises(ValueError):
        validate_data_transfer_config(config)


def test_setup_check_works_without_fink_client_requirement():
    report = build_setup_status_report(_config())
    names = {check["name"] for check in report["checks"]}
    assert "fink_client_installed" in names
    assert report["job_submission_attempted"] is False
    assert report["credentials_recorded"] is False


def test_request_builder_outputs_safety_flags_and_no_submit():
    request = build_data_transfer_request(_config())
    checks = validate_data_transfer_request(request)
    assert request["confirmation"]["dry_run"] is True
    assert request["confirmation"]["allow_submit"] is False
    assert request["safety_flags"]["job_submission_attempted"] is False
    assert all(check["passed"] for check in checks)


def test_reports_do_not_include_secret_values():
    report = build_setup_status_report(_config())
    text = render_setup_report(report)
    assert "secret-value" not in text
    assert "Credentials recorded: `False`" in text


def test_manual_checklist_is_human_readable():
    request = build_data_transfer_request(_config())
    md = build_manual_portal_checklist(request)
    assert "Do not paste or save credentials" in md
    assert "2026-02-25" in md


def test_output_inspector_empty_directory(tmp_path):
    report = summarize_delivery(tmp_path)
    assert report["empty"] is True
    assert report["file_count"] == 0


def test_output_inspector_json_and_parquet(tmp_path):
    json_path = tmp_path / "sample.json"
    json_path.write_text('[{"diaObjectId": 1, "diaSourceId": 2}]', encoding="utf-8")
    parquet_path = tmp_path / "sample.parquet"
    pd.DataFrame([{"diaObjectId": 3, "diaSourceId": 4}]).to_parquet(parquet_path, index=False)
    report = summarize_delivery(tmp_path)
    assert report["file_count"] == 2
    assert {item["file_type"] for item in report["files"]} == {"json", "parquet"}


def test_ingestion_splitter_with_synthetic_bulk_rows():
    frame = pd.DataFrame(
        [
            {
                "diaObjectId": 1,
                "diaSourceId": 10,
                "ra": 12.0,
                "dec": -1.0,
                "midpointMjdTai": 61096.0,
                "band": "g",
                "psfFlux": 1.2,
                "psfFluxErr": 0.1,
                "scienceFlux": 1.3,
                "fink_class": "candidate",
            }
        ]
    )
    normalized = normalize_bulk_alerts(frame)
    tables = split_bulk_tables(frame)
    assert "internal_object_id" in normalized.columns
    assert len(tables["alerts"]) == 1
    assert len(tables["objects"]) == 1
    assert len(tables["nightly_summary"]) == 1


def test_validation_refuses_completeness_without_delivery():
    checks = validate_delivery_tables({}, _config())
    blocked = [check for check in checks if check["check"] == "completeness_claim_allowed"]
    assert blocked and blocked[0]["passed"] is False


def test_validation_refuses_ambiguous_timezone():
    config = _config()
    config["timezone"] = "Asia/Kolkata"
    checks = validate_delivery_tables({"alerts": pd.DataFrame([{"diaObjectId": 1}])}, config)
    failed = [check for check in checks if check["check"] == "timezone_utc" and not check["passed"]]
    assert failed


def test_client_probe_never_submits_or_records_credentials():
    report = run_safe_client_probes()
    assert report["job_submission_attempted"] is False
    assert report["credentials_recorded"] is False


def test_request_markdown_renders():
    request = build_data_transfer_request(_config())
    md = render_request_as_markdown(request)
    assert "Data Transfer Request Draft" in md
    assert "Submission allowed: `False`" in md


def test_install_helper_report_generation():
    status = build_install_status()
    guide = render_install_guide(status)
    assert "python -m pip install fink-client" in guide
    assert "Fink Client Install Guide" in guide
    assert "recommended_install_command" in status


def test_smoke_request_profile_generation():
    request = build_profiled_data_transfer_request(_config(), profile="smoke_delivery")
    assert request["profile"] == "smoke_delivery"
    assert request["scope"]["all_alerts"] is False
    assert request["scope"]["completeness_claim_allowed"] is False
    assert request["confirmation"]["allow_submit"] is False
    assert request["risk"]["level"] == "low"


def test_smoke_request_checklist_generation():
    request = build_profiled_data_transfer_request(_config(), profile="smoke_delivery")
    checklist = build_smoke_manual_portal_checklist(request)
    assert "tiny non-complete smoke delivery" in checklist
    assert "data/raw/data_transfer/smoke_delivery/" in checklist


def test_registration_checklist_generation():
    checklist = render_registration_checklist()
    assert "Do Not" in checklist
    assert "tokens" in checklist
    assert "REST remains useful" in checklist


def test_dropzone_detection(tmp_path):
    (tmp_path / "smoke_delivery").mkdir()
    (tmp_path / "full_night").mkdir()
    (tmp_path / "smoke_delivery" / "sample.json").write_text('[{"diaObjectId": 1}]', encoding="utf-8")
    summary = summarize_dropzones(tmp_path)
    assert summary["zones"]["smoke_delivery"]["file_count"] == 1
    assert summary["zones"]["full_night"]["empty"] is True


def test_readiness_decision_states():
    setup = {"status": "ready_for_dry_run"}
    probe = {"package_installed": False, "cli_available": False}
    request = build_profiled_data_transfer_request(_config(), profile="smoke_delivery")
    delivery = {"empty": True}
    decision = decide_data_transfer_readiness(setup, probe, request, delivery)
    assert decision["decision"] == "ready_for_registration"
    assert decision["full_night_request_allowed"] is False
    probe = {"package_installed": True, "cli_available": True}
    decision = decide_data_transfer_readiness(setup, probe, request, delivery)
    assert decision["decision"] == "ready_for_manual_smoke_request"
    decision = decide_data_transfer_readiness(setup, probe, request, {"empty": False, "file_count": 1})
    assert decision["decision"] == "ready_for_smoke_ingestion"


def test_no_secret_values_in_checkpoint5_reports():
    status = build_install_status()
    text = render_install_guide(status) + render_registration_checklist()
    assert "secret-value" not in text
    assert ".env" in text


def test_gitignore_covers_data_transfer_delivery_files():
    text = Path(".gitignore").read_text()
    assert "data/raw/data_transfer/**/*.parquet" in text
    assert "data/raw/data_transfer/**/*.json" in text
    assert "!data/raw/data_transfer/**/.gitkeep" in text


def _config():
    return {
        "enabled": False,
        "dry_run": True,
        "survey": "LSST",
        "target_startdate": "2026-02-25",
        "target_stopdate": "2026-02-26",
        "timezone": "UTC",
        "min_lsst_alert_date_utc": "2026-02-25",
        "reject_pre_alert_dates": True,
        "preferred_output_format": "parquet",
        "fallback_output_formats": ["avro", "json"],
        "request_scope": {
            "all_alerts": True,
            "selected_blocks": [],
            "selected_tags": [],
            "selected_fields": ["diaObjectId", "diaSourceId", "ra", "dec", "midpointMjdTai"],
        },
        "safety": {
            "require_user_confirmation_to_submit": True,
            "max_local_file_size_gb_warning": 5,
            "never_store_credentials": True,
            "allow_submit": False,
        },
        "local_paths": {
            "raw_delivery_dir": "data/raw/data_transfer",
            "processed_dir": "data/processed/data_transfer",
            "reports_dir": "outputs/data_transfer",
        },
    }

import pandas as pd

from fink_lsst.bulk_transfer.diagnostics import build_smoke_science_readiness_report
from fink_lsst.bulk_transfer.ingest import (
    COMPLETENESS_SCOPE,
    load_data_transfer_files,
    split_bulk_tables,
    table_shapes,
    write_bulk_processed_tables,
)
from fink_lsst.bulk_transfer.nested import (
    json_dumps_stable,
    sanitize_dataframe_for_parquet,
    summarize_nested_columns,
    to_json_safe,
)
from fink_lsst.bulk_transfer.output_inspector import summarize_delivery
from fink_lsst.bulk_transfer.readiness_decision import decide_data_transfer_readiness
from fink_lsst.bulk_transfer.topic_registry import (
    build_download_command,
    metadata_from_topic_entry,
    new_full_night_topic_template,
)
from fink_lsst.bulk_transfer.validation import summarize_validation_status, validate_delivery_tables


def test_nested_mixed_columns_are_json_sanitized_and_parquet_safe(tmp_path):
    frame = pd.DataFrame(
        {
            "id": [1, 2, 3],
            "mixed": [{"a": 1}, "plain", None],
            "bytes_value": [b"abc", None, b"\xff"],
        }
    )
    summary = summarize_nested_columns(frame)
    assert {item["column"] for item in summary["nested_columns"]} == {"mixed", "bytes_value"}
    assert to_json_safe({"b": [2, 1]}) == {"b": [2, 1]}
    assert json_dumps_stable({"b": 2, "a": 1}) == '{"a":1,"b":2}'
    sanitized, report = sanitize_dataframe_for_parquet(frame)
    assert set(report["converted_columns"]) == {"mixed", "bytes_value"}
    path = tmp_path / "sanitized.parquet"
    sanitized.to_parquet(path, index=False)
    reread = pd.read_parquet(path)
    assert reread.shape == frame.shape


def test_lc_features_are_preserved_and_expanded(tmp_path):
    raw = _synthetic_raw_frame(
        [
            {
                "diaObjectId": 1,
                "diaSourceId": 10,
                "lc_features": [("g", {"amplitude": 1.2, "n_points": 5})],
                "clf": {"top": "SN"},
            }
        ]
    )
    tables = split_bulk_tables(raw, inspection={"file_count": 1, "total_rows": 1, "schema_group_count": 1})
    assert "lc_features_json" in tables["alerts"].columns
    assert tables["lightcurve_features"].iloc[0]["feature_amplitude"] == 1.2
    processed_dir = tmp_path / "data/processed/data_transfer/smoke_delivery/run"
    artifacts, report = write_bulk_processed_tables(tables, processed_dir, project_root=tmp_path, return_report=True)
    assert "alerts" in artifacts
    assert "lc_features" in report["converted_columns"]["alerts"]
    assert (processed_dir / "lightcurve_features.parquet").exists()


def test_output_inspector_detects_synthetic_nested_parquet(tmp_path):
    path = tmp_path / "nested.parquet"
    pd.DataFrame({"id": [1, 2], "nested": [[1, 2], [3, 4]]}).to_parquet(path, index=False)
    report = summarize_delivery(tmp_path)
    assert report["file_count"] == 1
    assert report["total_rows"] == 2
    assert "nested" in report["nested_columns"]


def test_synthetic_multi_file_delivery_ingests(tmp_path):
    delivery = tmp_path / "delivery"
    delivery.mkdir()
    _synthetic_raw_frame([{"diaObjectId": 1, "diaSourceId": 10}]).drop(columns=["lc_features"]).to_parquet(delivery / "part-1.parquet", index=False)
    _synthetic_raw_frame([{"diaObjectId": 2, "diaSourceId": 20}]).drop(columns=["lc_features"]).to_parquet(delivery / "part-2.parquet", index=False)
    inspection = summarize_delivery(delivery)
    raw = load_data_transfer_files(delivery)
    tables = split_bulk_tables(raw, inspection=inspection)
    artifacts, _report = write_bulk_processed_tables(
        tables,
        tmp_path / "data/processed/data_transfer/smoke_delivery/run",
        project_root=tmp_path,
        return_report=True,
    )
    assert raw.shape[0] == 2
    assert table_shapes(tables)["alerts"]["rows"] == 2
    assert {"alerts", "objects", "nightly_summary"}.issubset(artifacts)


def test_validation_distinguishes_raw_present_processed_missing(tmp_path):
    delivery = tmp_path / "delivery"
    delivery.mkdir()
    _synthetic_raw_frame([{"diaObjectId": 1, "diaSourceId": 10}]).drop(columns=["lc_features"]).to_parquet(delivery / "part.parquet", index=False)
    raw = summarize_delivery(delivery)
    checks = validate_delivery_tables({}, _config(), metadata={"raw_delivery": raw, "completeness_scope": COMPLETENESS_SCOPE})
    assert _check(checks, "raw_delivery_present")["passed"] is True
    assert _check(checks, "processed_alerts_present")["passed"] is False
    assert "processed alerts are missing" in _check(checks, "processed_alerts_present")["message"]


def test_validation_blocks_full_night_completeness_for_tag_filtered_smoke():
    tables = split_bulk_tables(_synthetic_raw_frame([{"diaObjectId": 1, "diaSourceId": 10}]))
    raw = {"empty": False, "file_count": 1, "total_rows": 1, "nested_columns": []}
    checks = validate_delivery_tables(
        tables,
        _config(),
        metadata={"raw_delivery": raw, "nested_report": {"tables": {}}, "completeness_scope": COMPLETENESS_SCOPE},
    )
    assert _check(checks, "completeness_claim_allowed")["passed"] is False
    assert _check(checks, "smoke_completeness_blocked")["passed"] is True
    assert summarize_validation_status(checks) in {"passed", "passed_with_warnings"}


def test_validation_allows_full_night_completeness_only_with_explicit_evidence():
    tables = split_bulk_tables(_synthetic_raw_frame([{"diaObjectId": 1, "diaSourceId": 10}]))
    raw = {"empty": False, "file_count": 1, "total_rows": 1, "nested_columns": []}
    metadata = metadata_from_topic_entry(
        {
            "topic": "full-night-topic",
            "scope": "full_night_all_alerts",
            "survey": "lsst",
            "utc_start": "2026-02-25",
            "utc_stop": "2026-02-26",
            "all_alerts": True,
            "filter": None,
            "kafka_lag_zero": True,
            "truncation_warning": False,
        }
    )
    checks = validate_delivery_tables(
        tables,
        _config(),
        metadata={"raw_delivery": raw, "nested_report": {"tables": {}}, **metadata},
    )
    assert _check(checks, "full_night_completeness_evidence")["passed"] is True
    assert _check(checks, "completeness_claim_allowed")["passed"] is True
    assert _check(checks, "smoke_completeness_blocked")["passed"] is True


def test_validation_blocks_full_night_when_lag_is_not_zero():
    tables = split_bulk_tables(_synthetic_raw_frame([{"diaObjectId": 1, "diaSourceId": 10}]))
    raw = {"empty": False, "file_count": 1, "total_rows": 1, "nested_columns": []}
    metadata = metadata_from_topic_entry(
        {
            "topic": "full-night-topic",
            "scope": "full_night_all_alerts",
            "survey": "lsst",
            "utc_start": "2026-02-25",
            "utc_stop": "2026-02-26",
            "all_alerts": True,
            "filter": None,
            "kafka_lag_zero": False,
            "truncation_warning": False,
        }
    )
    checks = validate_delivery_tables(
        tables,
        _config(),
        metadata={"raw_delivery": raw, "nested_report": {"tables": {}}, **metadata},
    )
    assert _check(checks, "full_night_completeness_evidence")["passed"] is False
    assert _check(checks, "completeness_claim_allowed")["passed"] is False


def test_topic_registry_builds_non_secret_download_command():
    entry = new_full_night_topic_template(_config())
    entry["topic"] = "ftransfer_lsst_test"
    command = build_download_command(entry)
    assert command[:5] == ["fink_datatransfer", "-survey", "lsst", "-topic", "ftransfer_lsst_test"]
    assert "--dump_schemas" in command
    assert "--verbose" in command
    assert not any("darim" in part for part in command)


def test_diagnostics_on_synthetic_alert_table():
    tables = split_bulk_tables(_synthetic_raw_frame([{"diaObjectId": 1, "diaSourceId": 10}]))
    report = build_smoke_science_readiness_report(
        tables,
        inspection={"file_count": 1, "total_rows": 1, "nested_columns": ["lc_features"], "schema_group_count": 1},
        validation_status="passed",
        nested_report={"converted_columns": {"alerts": ["lc_features"]}},
    )
    assert report["alerts"]["rows"] == 1
    assert report["objects"]["unique_objects"] == 1
    assert "scienceFlux" in report["promising_first_notebook_fields"]
    assert "not a complete full-night" in report["markdown"]


def test_next_action_after_successful_smoke_validation():
    decision = decide_data_transfer_readiness(
        {"status": "ready_for_dry_run"},
        {"package_installed": False, "cli_available": True},
        {"profile": "smoke_delivery", "confirmation": {"dry_run": True, "allow_submit": False}},
        {"empty": False, "file_count": 20, "total_rows": 8731},
        {
            "validation_status": "passed",
            "checks": [
                {"check": "completeness_claim_allowed", "passed": False, "severity": "warning"},
                {"check": "smoke_completeness_blocked", "passed": True, "severity": "info"},
            ],
        },
        {"artifacts": {"alerts": "alerts.parquet"}, "validation_status": "passed"},
    )
    assert decision["decision"] == "ready_for_first_full_night_request"
    assert decision["user_confirmation_required"] is True


def _synthetic_raw_frame(overrides):
    rows = []
    for override in overrides:
        row = {
            "diaObjectId": 1,
            "diaSourceId": 10,
            "ra": 12.0,
            "dec": -1.0,
            "midpointMjdTai": 61096.1,
            "band": "g",
            "scienceFlux": 1.2,
            "scienceFluxErr": 0.1,
            "tns_type_recomputed": "SN Ia",
            "lc_features": [("g", {"amplitude": 1.0, "n_points": 3})],
        }
        row.update(override)
        rows.append(row)
    return pd.DataFrame(rows)


def _check(checks, name):
    return next(check for check in checks if check["check"] == name)


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
        "request_scope": {"all_alerts": True},
        "safety": {
            "require_user_confirmation_to_submit": True,
            "max_local_file_size_gb_warning": 5,
            "never_store_credentials": True,
            "allow_submit": False,
        },
    }

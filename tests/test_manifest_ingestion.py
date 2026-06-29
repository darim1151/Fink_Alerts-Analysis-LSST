import subprocess
import sys
import time
from pathlib import Path

import pandas as pd

from fink_lsst.bulk_transfer.diagnostics import build_run_diagnostics
from fink_lsst.bulk_transfer.nightly_split import split_table_by_night
from fink_lsst.bulk_transfer.raw_audit import build_raw_audit
from fink_lsst.bulk_transfer.run_ingestion import IngestionOptions, ingest_run, plan_ingestion
from fink_lsst.bulk_transfer.run_manifest import ClaimStateSet, DownloadEvidence, ProcessingOptions, RunManifest, RunPaths
from fink_lsst.bulk_transfer.table_builder import build_tables_for_run
from fink_lsst.bulk_transfer.validation import derive_claim_updates, validate_run_outputs
from scripts.check_commit_readiness import classify_visible_paths


def test_run_analysis_refuses_raw_missing_ingestion(tmp_path):
    config = _missing_run_config(tmp_path, "missing_ingest")
    completed = subprocess.run(
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
    assert completed.returncode == 2
    assert "raw_missing" in completed.stdout


def test_run_analysis_ingest_dry_run_includes_blocked_reason(tmp_path):
    config = _missing_run_config(tmp_path, "missing_dry_run")
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_analysis.py",
            "--run-config",
            str(config),
            "--stage",
            "ingest",
            "--dry-run",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0
    assert '"stage_allowed": false' in completed.stdout
    assert "raw_missing" in completed.stdout
    assert "blocked_reason" in completed.stdout


def test_smoke_validate_manifest_dry_run_succeeds():
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_analysis.py",
            "--run-config",
            "configs/runs/smoke_in_tns_2026-02-25.yaml",
            "--stage",
            "validate_manifest",
            "--dry-run",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0
    assert "smoke_in_tns_2026-02-25" in completed.stdout
    assert "claim_state" in completed.stdout


def test_plan_refuses_partial_ingestion_by_default(tmp_path):
    manifest = _manifest(tmp_path, expected_total=10)
    _write_raw(tmp_path, rows=2)
    audit = build_raw_audit(tmp_path / manifest.paths.raw_dir)
    plan = plan_ingestion(manifest, audit, IngestionOptions(), project_root=tmp_path)
    assert plan.allowed is False
    assert plan.raw_state == "partial_download"


def test_allow_partial_labels_outputs_partial(tmp_path):
    manifest = _manifest(tmp_path, expected_total=10)
    _write_raw(tmp_path, rows=2)
    result = ingest_run(manifest, tmp_path, IngestionOptions(allow_partial=True, max_files=1, force=True))
    assert result["status"] == "ingested_partial"
    assert "partial" in result["summary"]["processed_dir"]
    assert "partial/debug" in result["summary"]["claim_note"]


def test_max_files_limits_file_processing(tmp_path):
    manifest = _manifest(tmp_path, expected_total=None)
    _write_raw(tmp_path, rows=1, name="part-1.parquet")
    _write_raw(tmp_path, rows=1, name="part-2.parquet")
    result = ingest_run(manifest, tmp_path, IngestionOptions(max_files=1, force=True))
    assert result["summary"]["file_results"]
    assert len(result["summary"]["file_results"]) == 1


def test_manifest_driven_smoke_ingestion_on_synthetic_files(tmp_path):
    manifest = _manifest(tmp_path, expected_total=None, filters=["in_tns"], is_all_alert=False)
    _write_raw(tmp_path, rows=3)
    result = ingest_run(manifest, tmp_path, IngestionOptions(max_files=1, force=True))
    assert result["status"] == "ingested"
    artifacts = result["summary"]["processed_artifacts"]
    assert "alerts" in artifacts
    assert Path(artifacts["alerts"]).exists()
    assert Path(result["summary"]["processed_dir"], "manifest_snapshot.yaml").exists()


def test_table_builder_handles_missing_optional_fields():
    tables, warnings = build_tables_for_run(pd.DataFrame({"diaObjectId": [1], "diaSourceId": [2], "midpointMjdTai": [61096.1]}))
    assert not tables["alerts"].empty
    assert "forced_photometry table is empty; optional/source fields may be absent" in warnings


def test_nested_field_preservation_through_table_builder():
    tables, _warnings = build_tables_for_run(pd.DataFrame({"diaObjectId": [1], "diaSourceId": [2], "lc_features": [[("g", {"amp": 1.2})]]}))
    assert "lc_features_json" in tables["alerts"].columns
    assert not tables["lightcurve_features"].empty


def test_nightly_split_single_and_multi_night():
    df = pd.DataFrame({"midpointMjdTai": [61096.1, 61097.1], "diaSourceId": [1, 2]})
    split, warnings = split_table_by_night(df, "2026-02-25", "2026-02-27")
    assert warnings == []
    assert sorted(split) == ["2026-02-25", "2026-02-26"]


def test_out_of_window_rows_preserved():
    df = pd.DataFrame({"midpointMjdTai": [61096.1, 61099.1], "diaSourceId": [1, 2]})
    split, _warnings = split_table_by_night(df, "2026-02-25", "2026-02-26")
    assert "2026-02-25" in split
    assert "out_of_window" in split


def test_validation_blocks_partial_and_tag_filtered_claims(tmp_path):
    manifest = _manifest(tmp_path, filters=["in_tns"], is_all_alert=False)
    report = {"validation_status": "passed", "execution_manifest": {"run_context": {"processed_dir": "x/partial"}}}
    claims = derive_claim_updates(manifest, report)
    assert claims["all_alert_completeness"] == "blocked"
    assert claims["week_completeness"] == "blocked"


def test_validation_leaves_full_week_unresolved_without_lag_zero(tmp_path):
    manifest = _manifest(tmp_path, filters=[], is_all_alert=True, scope="full_week", packet_type="full", expected_nights=["2026-02-25", "2026-02-26"])
    claims = derive_claim_updates(manifest, {"validation_status": "passed", "execution_manifest": {}})
    assert claims["week_completeness"] == "unresolved"


def test_validate_run_outputs_blocks_tag_filtered_completeness(tmp_path):
    manifest = _manifest(tmp_path, filters=["in_tns"], is_all_alert=False)
    _write_raw(tmp_path, rows=1)
    result = ingest_run(manifest, tmp_path, IngestionOptions(max_files=1, force=True))
    report = validate_run_outputs(manifest, result["summary"]["processed_artifacts"], build_raw_audit(tmp_path / manifest.paths.raw_dir), result["execution_manifest"])
    assert report["claim_updates"]["all_alert_completeness"] == "blocked"


def test_diagnostics_handle_missing_optional_fields(tmp_path):
    manifest = _manifest(tmp_path)
    tables, _warnings = build_tables_for_run(pd.DataFrame({"diaObjectId": [1], "diaSourceId": [2]}), manifest=manifest)
    report = build_run_diagnostics(manifest, tables)
    assert report["run_name"] == manifest.run_name
    assert report["bands"]["counts"] == {}


def test_commit_readiness_detects_visible_raw_payloads():
    classified = classify_visible_paths(["data/raw/data_transfer/run/part.parquet", "src/fink_lsst/example.py"])
    assert "data/raw/data_transfer/run/part.parquet" in classified["must_not_commit_files"]
    assert "src/fink_lsst/example.py" in classified["safe_to_commit_candidates"]


def test_commit_readiness_accepts_ignored_raw_paths_by_absence():
    classified = classify_visible_paths(["src/fink_lsst/example.py", "outputs/maintenance/COMMIT_READINESS.md"])
    assert classified["must_not_commit_files"] == []
    assert "src/fink_lsst/example.py" in classified["safe_to_commit_candidates"]


def _manifest(tmp_path, expected_total=None, filters=None, is_all_alert=False, scope="bounded_tag", packet_type="light_static", expected_nights=None):
    filters = ["in_tns"] if filters is None else filters
    return RunManifest(
        schema_version=1,
        run_name="synthetic_run",
        run_id="synthetic",
        survey="lsst",
        broker="fink",
        topic="ftransfer_lsst_synthetic",
        batch_id=None,
        startdate="2026-02-25",
        stopdate="2026-02-27" if expected_nights and len(expected_nights) > 1 else "2026-02-26",
        date_mode="utc_window",
        scope=scope,
        packet_type=packet_type,
        content="Full packet" if packet_type == "full" else "Light static packet",
        filters=filters,
        is_all_alert=is_all_alert,
        expected_nights=expected_nights or ["2026-02-25"],
        lifecycle_state="raw_appears_complete_unverified",
        claim_state=ClaimStateSet(),
        paths=RunPaths(
            raw_dir="data/raw/data_transfer/synthetic_run/ftransfer_lsst_synthetic",
            processed_dir="data/processed/data_transfer/runs/synthetic_run/synthetic",
            outputs_dir="outputs/data_transfer/runs/synthetic_run/synthetic",
        ),
        processing=ProcessingOptions(),
        download_evidence=DownloadEvidence(expected_total_messages=expected_total),
        notes="synthetic test manifest",
    )


def _write_raw(tmp_path, rows=2, name="part.parquet"):
    raw = tmp_path / "data/raw/data_transfer/synthetic_run/ftransfer_lsst_synthetic"
    raw.mkdir(parents=True, exist_ok=True)
    path = raw / name
    pd.DataFrame(
        {
            "diaObjectId": list(range(rows)),
            "diaSourceId": list(range(100, 100 + rows)),
            "midpointMjdTai": [61096.1] * rows,
            "band": ["g"] * rows,
            "scienceFlux": [1.0] * rows,
        }
    ).to_parquet(path, index=False)
    old = time.time() - 7200
    path.touch()
    import os

    os.utime(path, (old, old))


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

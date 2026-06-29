import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

from fink_lsst.bulk_transfer.raw_audit import build_raw_audit
from fink_lsst.bulk_transfer.raw_readiness import classify_raw_readiness
from scripts.recommend_download_action import recommend_action


def test_raw_audit_missing_directory(tmp_path):
    audit = build_raw_audit(tmp_path / "missing")
    readiness = classify_raw_readiness(audit, expected_total=10)
    assert audit["exists"] is False
    assert readiness["state"] == "raw_missing"


def test_raw_audit_synthetic_parquet_files(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    pd.DataFrame({"diaObjectId": [1, 2], "nested": [[1], [2]]}).to_parquet(raw / "part-1.parquet", index=False)
    pd.DataFrame({"diaObjectId": [3]}).to_parquet(raw / "part-2.parquet", index=False)
    audit = build_raw_audit(raw)
    assert audit["file_count"] == 2
    assert audit["parquet"]["readable_parquet_count"] == 2
    assert audit["parquet"]["total_readable_rows"] == 3
    assert audit["schemas"]["schema_group_count"] >= 1


def test_unreadable_fake_parquet_is_reported(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "bad.parquet").write_text("not parquet", encoding="utf-8")
    audit = build_raw_audit(raw)
    readiness = classify_raw_readiness(audit)
    assert audit["parquet"]["unreadable_parquet_count"] == 1
    assert readiness["state"] == "blocked_corrupt_raw"


def test_tiny_suspicious_file_detection(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "tiny.tmp").write_text("", encoding="utf-8")
    audit = build_raw_audit(raw)
    assert audit["partial_signals"]["tiny_suspicious_files"]


def test_readiness_expected_total_percentage(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    pd.DataFrame({"x": range(5)}).to_parquet(raw / "part.parquet", index=False)
    audit = build_raw_audit(raw)
    readiness = classify_raw_readiness(audit, expected_total=10, progress_from_terminal=7)
    assert readiness["state"] in {"partial_download", "download_active"}
    assert readiness["apparent_percent_complete"] == 50.0
    assert readiness["remaining_rows"] == 5
    assert readiness["terminal_progress_gap"] == 2


def test_recommendation_for_missing_raw():
    triage = {"topic": "topic", "raw_dir": "missing", "readiness": {"state": "raw_missing"}}
    assert recommend_action(triage)["action"] == "resume_same_topic_same_directory"


def test_guarded_pipeline_refuses_missing_partial(tmp_path):
    registry = tmp_path / "registry.yml"
    registry.write_text(
        """
version: 1
topics:
  - topic: ftransfer_lsst_test
    scope: full_week_full_packet
    survey: lsst
    utc_start: '2026-02-25'
    utc_stop: '2026-03-04'
    raw_delivery_dir: missing/raw
""",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [sys.executable, "scripts/run_full_week_full_packet_pipeline.py", "--registry", str(registry), "--topic", "ftransfer_lsst_test"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 2
    assert "Refusing ingestion" in completed.stdout


def test_guarded_pipeline_allows_partial_with_flag(tmp_path):
    registry = tmp_path / "registry.yml"
    registry.write_text(
        """
version: 1
topics:
  - topic: ftransfer_lsst_test
    scope: full_week_full_packet
    survey: lsst
    utc_start: '2026-02-25'
    utc_stop: '2026-03-04'
    raw_delivery_dir: missing/raw
""",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_full_week_full_packet_pipeline.py",
            "--registry",
            str(registry),
            "--topic",
            "ftransfer_lsst_test",
            "--allow-partial",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0
    assert "partial_full_week_full_packet" in completed.stdout

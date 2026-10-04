import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from fink_lsst.bulk_transfer.execution import (
    FileProcessingResult,
    ProcessingStage,
    build_run_context,
    load_or_create_execution_manifest,
    mark_stage_completed,
    mark_stage_started,
    record_file_result,
    should_skip_completed_file,
)
from fink_lsst.bulk_transfer.scopes import VALID_SCOPES, derive_expected_nights, scope_paths
from fink_lsst.bulk_transfer.topic_registry import build_download_command, build_topic_entry, register_topic_entry


def test_valid_scopes_include_full_week_full_packet():
    assert "full_week_full_packet" in VALID_SCOPES


def test_topic_registration_entry_paths_and_nights():
    entry = _entry()
    assert entry["raw_delivery_dir"] == "data/raw/data_transfer/full_week_full_packet/2026-02-25_to_2026-03-04/ftransfer_lsst_2026-06-27_38507"
    assert entry["processed_dir_template"] == "data/processed/data_transfer/full_week_full_packet/2026-02-25_to_2026-03-04/<run_id>"
    assert entry["output_dir_template"] == "outputs/data_transfer/full_week_full_packet/2026-02-25_to_2026-03-04/<run_id>"
    assert entry["expected_nights"] == [
        "2026-02-25",
        "2026-02-26",
        "2026-02-27",
        "2026-02-28",
        "2026-03-01",
        "2026-03-02",
        "2026-03-03",
    ]
    assert entry["is_all_alert"] is True
    assert entry["claim_policy"]["week_complete_default"] == "unresolved"


def test_topic_registration_refuses_overwrite_without_force():
    registry = {"version": 1, "topics": [_entry()]}
    with pytest.raises(ValueError):
        register_topic_entry(registry, _entry(), force=False)
    updated = register_topic_entry(registry, {**_entry(), "notes": "replace"}, force=True)
    assert updated["topics"][0]["notes"] == "replace"


def test_expected_nights_date_order():
    assert len(derive_expected_nights("2026-02-25", "2026-03-04")) == 7
    with pytest.raises(ValueError):
        derive_expected_nights("2026-03-04", "2026-02-25")


def test_download_command_generation(tmp_path):
    command = build_download_command(_entry(), tmp_path, 4)
    assert command == [
        "finkctl",
        "transfer",
        "-survey",
        "lsst",
        "-topic",
        "ftransfer_lsst_2026-06-27_38507",
        "-outdir",
        str(tmp_path.resolve() / "data/raw/data_transfer/full_week_full_packet/2026-02-25_to_2026-03-04/ftransfer_lsst_2026-06-27_38507"),
        "-nconsumers",
        "4",
        "--dump_schemas",
        "--verbose",
    ]


def test_download_progress_missing_raw_dir(tmp_path):
    script = Path("scripts/summarize_download_progress.py")
    missing = tmp_path / "data/raw/data_transfer/missing"
    completed = subprocess.run([sys.executable, str(script), "--raw-dir", str(missing)], text=True, capture_output=True, check=False, env=_root_env(tmp_path))
    assert completed.returncode == 0
    assert "raw_delivery_missing" in completed.stdout


def test_download_progress_synthetic_parquet(tmp_path):
    raw = tmp_path / "data/raw/data_transfer/raw"
    raw.mkdir(parents=True)
    pd.DataFrame({"diaObjectId": [1, 2], "payload": [b"a", b"b"]}).to_parquet(raw / "part.parquet", index=False)
    completed = subprocess.run([sys.executable, "scripts/summarize_download_progress.py", "--raw-dir", str(raw)], text=True, capture_output=True, check=False, env=_root_env(tmp_path))
    assert completed.returncode == 0
    assert "total_rows_if_feasible" in completed.stdout
    assert "`2`" in completed.stdout


def test_execution_manifest_creation_and_transitions(tmp_path, monkeypatch):
    entry = _entry()
    context = build_run_context(entry, run_id="RUN")
    context.output_dir = str(tmp_path / "outputs")
    manifest = load_or_create_execution_manifest(context)
    manifest = mark_stage_started(manifest, ProcessingStage.RAW_INSPECTION)
    assert manifest.stages["raw_inspection"]["status"] == "started"
    manifest = mark_stage_completed(manifest, ProcessingStage.RAW_INSPECTION)
    assert manifest.stages["raw_inspection"]["status"] == "completed"
    result = FileProcessingResult(path="part.parquet", stage="raw_inspection", status="completed", rows=2)
    manifest = record_file_result(manifest, result)
    assert should_skip_completed_file(manifest, "part.parquet", ProcessingStage.RAW_INSPECTION) is True


def test_full_packet_inspection_missing_raw_behavior(tmp_path):
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/inspect_full_packet_delivery.py",
            "--raw-dir",
            str(tmp_path / "missing"),
            "--output-dir",
            str(tmp_path / "inspection"),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0
    assert "raw_delivery_missing" in completed.stdout
    assert (tmp_path / "inspection/raw_full_packet_field_inventory.json").exists()


def test_preflight_decision_with_mocked_good_state(tmp_path, monkeypatch):
    module = _load_script("preflight_full_packet_delivery")
    entry = _entry()
    monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(module, "module_importable", lambda name: True)
    monkeypatch.setattr(module.shutil, "which", lambda name: "/bin/fink_datatransfer")
    monkeypatch.setattr(module, "git_ignored", lambda path: True)
    monkeypatch.setattr(module, "visible_credential_like_files", lambda: [])
    (tmp_path / "configs").mkdir()
    registry_path = tmp_path / "configs/data_transfer_topics.yaml"
    registry_path.write_text("version: 1\ntopics: []\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/IMPORTANT_RUNS.md").write_text("smoke", encoding="utf-8")
    report = module.build_preflight_report(entry, allow_existing_raw=False, registry_path=registry_path)
    assert report["decision"] == "ready_to_download"


def test_preflight_blocks_if_raw_path_not_ignored(tmp_path, monkeypatch):
    module = _load_script("preflight_full_packet_delivery")
    entry = _entry()
    monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(module, "module_importable", lambda name: True)
    monkeypatch.setattr(module.shutil, "which", lambda name: "/bin/fink_datatransfer")
    monkeypatch.setattr(module, "git_ignored", lambda path: False)
    monkeypatch.setattr(module, "visible_credential_like_files", lambda: [])
    registry_path = tmp_path / "registry.yml"
    registry_path.write_text("version: 1\ntopics: []\n", encoding="utf-8")
    report = module.build_preflight_report(entry, allow_existing_raw=False, registry_path=registry_path)
    assert report["decision"] == "blocked"
    assert any(check["name"] == "raw_path_ignored" and not check["passed"] for check in report["checks"])


def test_preflight_blocks_if_credential_like_file_is_git_visible(tmp_path, monkeypatch):
    module = _load_script("preflight_full_packet_delivery")
    entry = _entry()
    monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(module, "module_importable", lambda name: True)
    monkeypatch.setattr(module.shutil, "which", lambda name: "/bin/fink_datatransfer")
    monkeypatch.setattr(module, "git_ignored", lambda path: True)
    monkeypatch.setattr(module, "visible_credential_like_files", lambda: ["secret.env"])
    registry_path = tmp_path / "registry.yml"
    registry_path.write_text("version: 1\ntopics: []\n", encoding="utf-8")
    report = module.build_preflight_report(entry, allow_existing_raw=False, registry_path=registry_path)
    assert report["decision"] == "blocked"
    assert any(check["name"] == "credentials_not_git_visible" and not check["passed"] for check in report["checks"])


def test_scope_paths_are_not_full_night_for_full_week():
    paths = scope_paths("full_week_full_packet", "2026-02-25", "2026-03-04", "topic")
    assert "/full_week_full_packet/" in paths["raw_delivery_dir"]
    assert "/full_night/" not in paths["raw_delivery_dir"]


def test_next_action_transition_to_full_week_download(tmp_path):
    registry = tmp_path / "registry.yml"
    registry.write_text("version: 1\ntopics: []\n", encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/register_data_transfer_topic.py",
            "--registry",
            str(registry),
            "--scope",
            "full_week_full_packet",
            "--survey",
            "lsst",
            "--topic",
            "ftransfer_lsst_2026-06-27_38507",
            "--startdate",
            "2026-02-25",
            "--stopdate",
            "2026-03-04",
            "--content",
            "Full packet",
            "--all-alert",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0
    assert "full_week_full_packet" in registry.read_text(encoding="utf-8")


def _entry():
    return build_topic_entry(
        scope="full_week_full_packet",
        survey="lsst",
        topic="ftransfer_lsst_2026-06-27_38507",
        startdate="2026-02-25",
        stopdate="2026-03-04",
        content="Full packet",
        all_alert=True,
        notes="First full-week full-packet LSST Data Transfer request",
    )


def _root_env(data_root):
    env = dict(os.environ)
    env["FINK_LSST_DATA_ROOT"] = str(data_root)
    env["PYTHONPATH"] = str(Path("src").resolve())
    return env


def _load_script(name):
    path = Path("scripts") / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module

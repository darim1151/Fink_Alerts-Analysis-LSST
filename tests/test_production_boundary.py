"""Regression tests for the FINK-G2B-R1 production storage boundary (review findings F1-F5)."""

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd
import pytest

from fink_lsst.bulk_transfer.raw_audit import build_raw_audit
from fink_lsst.bulk_transfer.run_ingestion import IngestionOptions, derive_run_dirs, ingest_run, plan_ingestion
from fink_lsst.bulk_transfer.run_manifest import load_run_manifest, resolve_run_paths, validate_run_manifest
from fink_lsst.bulk_transfer.topic_registry import build_download_command, render_download_instructions, transfer_log_path
from fink_lsst.data_root import (
    DATA_ROOT_ENV,
    REPO_ROOT,
    DataRootError,
    PathConfinementError,
    confine,
    resolve_data_root,
    storage_base,
    validate_path_component,
)
from fink_lsst.storage import write_json


RUN = "boundary_run"
TOPIC = "ftransfer_lsst_2026-07-29_101214"
RAW_REL = f"data/raw/data_transfer/full_week/2026-02-25_to_2026-03-04/{TOPIC}"
OUT_REL = f"outputs/data_transfer/full_week/2026-02-25_to_2026-03-04/{TOPIC}"
PROC_REL = f"data/processed/data_transfer/full_week/2026-02-25_to_2026-03-04/{TOPIC}"


# --- F1: redirected anchors and symlinked targets -------------------------------------------


def test_redirected_data_anchor_is_rejected(tmp_path):
    root, elsewhere = _root(tmp_path), _dir(tmp_path / "elsewhere")
    (elsewhere / "raw/data_transfer").mkdir(parents=True)
    (root / "data").symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(PathConfinementError):
        resolve_run_paths(_manifest(tmp_path), root)
    with pytest.raises(ValueError, match="Artifact path"):
        write_json({}, "data/processed/x.json", project_root=root)
    assert not (elsewhere / "processed").exists()


def test_redirected_outputs_anchor_is_rejected(tmp_path):
    root, elsewhere = _root(tmp_path), _dir(tmp_path / "elsewhere")
    (root / "outputs").symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(PathConfinementError):
        resolve_run_paths(_manifest(tmp_path), root)
    with pytest.raises(PathConfinementError):
        derive_run_dirs(RUN, RUN, root)
    with pytest.raises(ValueError, match="Artifact path"):
        write_json({}, "outputs/report.json", project_root=root)
    assert list(elsewhere.iterdir()) == []


def test_raw_symlink_escaping_raw_directory_is_rejected(tmp_path):
    root = _root(tmp_path)
    outside = tmp_path / "outside.parquet"
    _parquet(outside)
    raw = root / RAW_REL
    raw.mkdir(parents=True)
    (raw / "part.parquet").symlink_to(outside)
    with pytest.raises(PathConfinementError):
        resolve_run_paths(_manifest(tmp_path), root)
    with pytest.raises(PathConfinementError):
        ingest_run(_manifest(tmp_path), root, IngestionOptions(force=True))


def test_existing_output_symlink_escaping_hierarchy_is_rejected(tmp_path):
    root = _root(tmp_path)
    victim = tmp_path / "victim.json"
    victim.write_text("original", encoding="utf-8")
    report_dir = root / OUT_REL
    report_dir.mkdir(parents=True)
    (report_dir / "download_progress.json").symlink_to(victim)
    with pytest.raises(PathConfinementError):
        resolve_run_paths(_manifest(tmp_path), root)
    with pytest.raises(ValueError, match="Artifact path"):
        write_json({}, report_dir / "download_progress.json", project_root=root)
    run_dir = root / "outputs/data_transfer/runs" / RUN / RUN
    run_dir.mkdir(parents=True)
    (run_dir / "ingestion_summary.json").symlink_to(victim)
    with pytest.raises(PathConfinementError):
        derive_run_dirs(RUN, RUN, root)
    assert victim.read_text(encoding="utf-8") == "original"


def test_legitimate_nested_paths_still_work(tmp_path):
    root = _root(tmp_path)
    raw = root / RAW_REL / "nested"
    raw.mkdir(parents=True)
    _parquet(raw / "part.parquet")
    (raw / "alias.parquet").symlink_to(raw / "part.parquet")
    paths = resolve_run_paths(_manifest(tmp_path), root)
    assert paths["raw_dir"] == (root / RAW_REL).resolve()
    assert paths["outputs_dir"] == (root / OUT_REL).resolve()
    written = write_json({"ok": True}, f"{OUT_REL}/progress.json", project_root=root)
    assert written.is_relative_to(root.resolve() / "outputs")


def test_ingestion_writes_stay_under_external_root(tmp_path):
    root = _root(tmp_path)
    _parquet(root / RAW_REL / "part.parquet")
    result = ingest_run(_manifest(tmp_path), root, IngestionOptions(force=True))
    assert result["status"] == "ingested"
    for path in [result["summary"]["processed_dir"], result["summary"]["output_dir"], *result["summary"]["processed_artifacts"].values()]:
        assert Path(path).resolve().is_relative_to(root.resolve())
        assert not Path(path).resolve().is_relative_to(REPO_ROOT)


# --- F2: run identifiers ----------------------------------------------------------------------


@pytest.mark.parametrize("value", ["", ".", "..", "/abs/run", "../escape", "a/b", "a\\b", "-flag", " run", "run\n", None, 5])
def test_unsafe_identifiers_are_rejected(value):
    with pytest.raises(PathConfinementError):
        validate_path_component(value, "run_id")


@pytest.mark.parametrize(
    "value",
    [
        "full_week_light_static_2026-02-25_to_2026-03-04",
        "ftransfer_lsst_2026-07-29_101214",
        "smoke_in_tns_2026-02-25",
        "ftransfer_lsst_2026-07-29_101214_partial_20261004T060000Z",
    ],
)
def test_existing_project_identifiers_are_valid(value):
    assert validate_path_component(value) == value


@pytest.mark.parametrize("field, value", [("run_id", "/tmp/escape"), ("run_id", "../../escape"), ("run_name", "a/b"), ("run_id", "x/../../y")])
def test_unsafe_manifest_identifiers_fail_before_any_write(tmp_path, field, value):
    root = _root(tmp_path)
    _parquet(root / RAW_REL / "part.parquet")
    manifest = _manifest(tmp_path)
    setattr(manifest, field, value)
    errors, _warnings = validate_run_manifest(manifest, project_root=root)
    assert any(field in error for error in errors)
    before = sorted(str(p) for p in tmp_path.rglob("*"))
    with pytest.raises(PathConfinementError):
        ingest_run(manifest, root, IngestionOptions(force=True))
    assert sorted(str(p) for p in tmp_path.rglob("*")) == before


def test_derived_run_destinations_are_confined_and_not_raw(tmp_path):
    root = _root(tmp_path)
    processed, outputs = derive_run_dirs(RUN, "ftransfer_lsst_2026-07-29_101214", root)
    assert processed.is_relative_to(root.resolve() / "data/processed/data_transfer")
    assert outputs.is_relative_to(root.resolve() / "outputs/data_transfer")
    assert not processed.is_relative_to(root.resolve() / "data/raw")
    plan = plan_ingestion(_manifest(tmp_path), build_raw_audit(root / RAW_REL), IngestionOptions(), project_root=root)
    assert Path(plan.processed_dir).is_relative_to(root.resolve() / "data/processed/data_transfer/runs")


# --- F5: explicit data root safety ------------------------------------------------------------


def test_filesystem_root_is_rejected():
    with pytest.raises(DataRootError, match="filesystem root"):
        resolve_data_root(environ={DATA_ROOT_ENV: "/"})


def test_home_directory_is_rejected(tmp_path, monkeypatch):
    home = _dir(tmp_path / "home")
    monkeypatch.setenv("HOME", str(home))
    with pytest.raises(DataRootError, match="home directory"):
        resolve_data_root(environ={DATA_ROOT_ENV: str(home)}, repo_root=_dir(tmp_path / "repo"))
    link = tmp_path / "home_link"
    link.symlink_to(home, target_is_directory=True)
    with pytest.raises(DataRootError, match="home directory"):
        resolve_data_root(environ={DATA_ROOT_ENV: str(link)}, repo_root=tmp_path / "repo")


def test_checkout_overlaps_are_rejected_including_symlinks(tmp_path):
    parent = _dir(tmp_path / "parent")
    repo = _dir(parent / "repo")
    _dir(repo / "data")
    link = tmp_path / "repo_link"
    link.symlink_to(repo, target_is_directory=True)
    parent_link = tmp_path / "parent_link"
    parent_link.symlink_to(parent, target_is_directory=True)
    cases = {
        str(repo): "inside the Git checkout",
        str(repo / "data"): "inside the Git checkout",
        str(parent): "contain the Git checkout",
        str(link): "inside the Git checkout",
        str(link / "data"): "inside the Git checkout",
        str(parent_link): "contain the Git checkout",
    }
    for value, message in cases.items():
        with pytest.raises(DataRootError, match=message):
            resolve_data_root(environ={DATA_ROOT_ENV: value}, repo_root=repo)


def test_dedicated_sibling_root_is_accepted_and_unset_falls_back(tmp_path):
    repo = _dir(tmp_path / "repo")
    sibling = _dir(tmp_path / "FINK")
    assert resolve_data_root(environ={DATA_ROOT_ENV: str(sibling)}, repo_root=repo) == sibling.resolve()
    assert resolve_data_root(environ={}, repo_root=repo) == repo.resolve()


# --- F3: transfer command ---------------------------------------------------------------------


def test_light_static_transfer_command_is_finkctl_absolute_and_explicit(tmp_path):
    root = _root(tmp_path)
    command = build_download_command(_entry(), root, 4)
    assert command[:2] == ["finkctl", "transfer"]
    assert "fink_datatransfer" not in command
    assert command[command.index("-survey") + 1] == "lsst"
    assert command[command.index("-topic") + 1] == TOPIC
    outdir = Path(command[command.index("-outdir") + 1])
    assert outdir.is_absolute() and outdir == root.resolve() / RAW_REL
    assert command[command.index("-nconsumers") + 1] == "4"
    assert "--dump_schemas" in command and "--verbose" in command
    instructions = render_download_instructions(_entry(), root, 4)
    assert "finkctl transfer" in instructions and "fink_datatransfer" not in instructions
    assert str(root.resolve() / "logs") in instructions
    assert "Full-packet" not in instructions


@pytest.mark.parametrize("value", [0, -1, 33, 4.0, "4", None, True])
def test_invalid_consumer_counts_fail(tmp_path, value):
    with pytest.raises(ValueError, match="nconsumers"):
        build_download_command(_entry(), _root(tmp_path), value)


def test_unsafe_destination_overrides_fail(tmp_path):
    root = _root(tmp_path)
    for outdir in [tmp_path / "elsewhere", "data/raw/data_transfer/../../processed/x", "data/processed/data_transfer/x", "outputs/x"]:
        with pytest.raises(PathConfinementError):
            build_download_command(_entry(), root, 4, outdir=outdir)
    elsewhere = _dir(tmp_path / "elsewhere_raw")
    (root / "data").mkdir()
    (root / "data/raw").symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(PathConfinementError):
        build_download_command(_entry(), root, 4)
    with pytest.raises(PathConfinementError):
        build_download_command(dict(_entry(), topic="../x"), _root(tmp_path / "other"), 4)


def test_transfer_log_symlink_into_raw_is_rejected(tmp_path):
    root = _root(tmp_path)
    raw_file = root / RAW_REL / "part.parquet"
    _parquet(raw_file)
    (root / "logs").mkdir()
    (root / f"logs/{TOPIC}.transfer.log").symlink_to(raw_file)
    with pytest.raises(PathConfinementError):
        transfer_log_path(root, TOPIC)
    with pytest.raises(PathConfinementError):
        render_download_instructions(_entry(), root, 4)


def test_transfer_log_symlink_outside_root_is_rejected(tmp_path):
    root = _root(tmp_path)
    (root / "logs").mkdir()
    (root / f"logs/{TOPIC}.transfer.log").symlink_to(tmp_path / "outside.log")
    with pytest.raises(PathConfinementError):
        transfer_log_path(root, TOPIC)
    with pytest.raises(PathConfinementError):
        render_download_instructions(_entry(), root, 4)
    elsewhere = _dir(tmp_path / "elsewhere_logs")
    other = _root(tmp_path / "other")
    (other / "logs").symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(PathConfinementError):
        transfer_log_path(other, TOPIC)


def test_valid_transfer_log_targets_are_accepted(tmp_path):
    root = _root(tmp_path)
    expected = root.resolve() / f"logs/{TOPIC}.transfer.log"
    assert transfer_log_path(root, TOPIC) == expected
    (root / "logs").mkdir()
    expected.write_text("previous attempt\n", encoding="utf-8")
    assert transfer_log_path(root, TOPIC) == expected
    assert f'LOG_FILE="{expected}"' in render_download_instructions(_entry(), root, 4)
    with pytest.raises(PathConfinementError):
        transfer_log_path(root, "../escape")


def test_print_command_cli_uses_external_root(tmp_path):
    root = _root(tmp_path)
    completed = _script("print_data_transfer_download_command.py", ["--topic", TOPIC, "--nconsumers", "3"], root)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout.startswith("finkctl transfer")
    assert f"-outdir {root.resolve() / RAW_REL}" in completed.stdout
    assert "-nconsumers 3" in completed.stdout
    rejected = _script("print_data_transfer_download_command.py", ["--topic", TOPIC, "--outdir", str(tmp_path)], root)
    assert rejected.returncode != 0
    assert "outside" in rejected.stdout + rejected.stderr


# --- F4: monitoring ---------------------------------------------------------------------------


def test_progress_monitor_uses_external_raw_and_report_dirs(tmp_path):
    root = _root(tmp_path)
    _parquet(root / RAW_REL / "part.parquet")
    completed = _script("summarize_download_progress.py", ["--run-config", "configs/runs/full_week_light_static_2026-02-25_to_2026-03-04.yaml"], root)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    report_dir = root.resolve() / OUT_REL
    assert f"report_dir: {report_dir}" in completed.stdout
    summary = json.loads((report_dir / "download_progress.json").read_text(encoding="utf-8"))
    assert summary["raw_dir"] == str(root.resolve() / RAW_REL)
    assert summary["packet_type"] == "light_static"
    text = completed.stdout + (report_dir / "DOWNLOAD_PROGRESS.md").read_text(encoding="utf-8")
    for forbidden in ("fink_datatransfer", "full_packet", "inspect_full_packet_delivery"):
        assert forbidden not in text
    assert not (REPO_ROOT / OUT_REL / "download_progress.json").exists()


def test_progress_monitor_registry_topic_and_missing_raw(tmp_path):
    root = _root(tmp_path)
    completed = _script("summarize_download_progress.py", ["--topic", TOPIC], root)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "raw_delivery_missing" in completed.stdout
    assert "finkctl transfer" in completed.stdout
    assert "fink_datatransfer" not in completed.stdout
    assert (root / OUT_REL / "download_progress.json").exists()


def test_progress_monitor_rejects_escaping_raw_dir(tmp_path):
    root = _root(tmp_path)
    completed = _script("summarize_download_progress.py", ["--raw-dir", str(tmp_path)], root)
    assert completed.returncode == 2
    assert "outside" in completed.stdout


# --- production preflight (deployment policy) -------------------------------------------------


def test_production_preflight_requires_expected_root(tmp_path, monkeypatch, capsys):
    root = _preflight_root(tmp_path, monkeypatch)
    assert _preflight(monkeypatch, capsys, root, root) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["resolved"]["raw_delivery"] == str(root.resolve() / RAW_REL)
    assert report["resolved"]["transfer_log"] == str(root.resolve() / f"logs/{TOPIC}.transfer.log")
    assert report["transfer_command"][:2] == ["finkctl", "transfer"]
    other = _dir(tmp_path / "other")
    assert _preflight(monkeypatch, capsys, other, root) == 1
    assert not json.loads(capsys.readouterr().out)["ok"]


def test_production_preflight_cli_cannot_override_root(tmp_path):
    root = _root(tmp_path)
    completed = _script("arnor_production_preflight.py", ["--expected-root", str(root)], root)
    assert completed.returncode == 2
    assert "unrecognized arguments: --expected-root" in completed.stderr
    module = _load_preflight()
    assert module.PRODUCTION_DATA_ROOT == "/astro/store/shire/FINK"
    default_report = _script("arnor_production_preflight.py", [], root)
    assert json.loads(default_report.stdout)["expected_root"] == "/astro/store/shire/FINK"
    assert default_report.returncode == 1


@pytest.mark.parametrize("target", ["raw", "outside", "elsewhere_in_root"])
def test_preflight_rejects_redirected_transfer_log(tmp_path, monkeypatch, capsys, target):
    root = _preflight_root(tmp_path, monkeypatch)
    destinations = {
        "raw": root / RAW_REL / "part.parquet",
        "outside": tmp_path / "outside.log",
        "elsewhere_in_root": root / "outputs/hijacked.log",
    }
    destination = destinations[target]
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("untouched", encoding="utf-8")
    (root / f"logs/{TOPIC}.transfer.log").symlink_to(destination)
    assert _preflight(monkeypatch, capsys, root, root) == 1
    report = json.loads(capsys.readouterr().out)
    failed = {check["name"]: check["detail"] for check in report["checks"] if not check["ok"]}
    assert "paths_confined" in failed and "outside" in failed["paths_confined"]
    assert "transfer_log" not in report.get("resolved", {})
    assert destination.read_text(encoding="utf-8") == "untouched"


# --- helpers -----------------------------------------------------------------------------------


def _dir(path):
    path.mkdir(parents=True, exist_ok=True)
    return path


def _root(tmp_path):
    return _dir(tmp_path / "FINK")


def _manifest(tmp_path):
    path = tmp_path / "run.yaml"
    path.write_text(
        f"""
schema_version: 1
run_name: {RUN}
run_id: {RUN}
survey: lsst
broker: fink
topic: {TOPIC}
startdate: '2026-02-25'
stopdate: '2026-02-26'
date_mode: utc_window
scope: bounded_tag
packet_type: light_static
content: Light static packet
filters: [in_tns]
is_all_alert: false
lifecycle_state: raw_appears_complete_unverified
paths:
  raw_dir: {RAW_REL}
  processed_dir: {PROC_REL}
  outputs_dir: {OUT_REL}
processing:
  plots: false
""",
        encoding="utf-8",
    )
    return load_run_manifest(path)


def _entry():
    return {"topic": TOPIC, "survey": "lsst", "scope": "full_week", "raw_delivery_dir": RAW_REL}


def _parquet(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"diaObjectId": [1, 2], "diaSourceId": [11, 12], "midpointMjdTai": [61096.1, 61096.2], "band": ["g", "r"], "scienceFlux": [1.0, 2.0]}).to_parquet(path, index=False)
    old = time.time() - 7200
    os.utime(path, (old, old))


def _load_preflight():
    spec = importlib.util.spec_from_file_location("arnor_production_preflight", REPO_ROOT / "scripts/arnor_production_preflight.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _preflight_root(tmp_path, monkeypatch):
    root = _root(tmp_path)
    for relative in ("data/raw", "data/processed", "outputs", "manifests", "logs"):
        (root / relative).mkdir(parents=True, exist_ok=True)
    bin_dir = _dir(tmp_path / "bin")
    fake = bin_dir / "finkctl"
    fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    return root


def _preflight(monkeypatch, capsys, data_root, expected_root):
    capsys.readouterr()
    monkeypatch.setenv(DATA_ROOT_ENV, str(data_root))
    args = ["--run-config", "configs/runs/full_week_light_static_2026-02-25_to_2026-03-04.yaml"]
    return _load_preflight().main(args, expected_root=str(expected_root))


def _script(name, args, data_root, env_extra=None):
    env = {key: value for key, value in os.environ.items() if key != DATA_ROOT_ENV}
    env[DATA_ROOT_ENV] = str(data_root)
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    env.update(env_extra or {})
    return subprocess.run([sys.executable, f"scripts/{name}", *args], cwd=REPO_ROOT, env=env, text=True, capture_output=True, check=False)

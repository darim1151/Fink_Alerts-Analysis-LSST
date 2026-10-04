import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd
import pytest

from fink_lsst.bulk_transfer.run_index import validate_run_index
from fink_lsst.bulk_transfer.run_ingestion import IngestionOptions, plan_ingestion
from fink_lsst.bulk_transfer.raw_audit import build_raw_audit
from fink_lsst.bulk_transfer.run_manifest import RunPaths, load_run_manifest, validate_run_manifest
from fink_lsst.data_root import DATA_ROOT_ENV, REPO_ROOT, DataRootError, is_external_data_root, resolve_data_root


RUN_NAME = "data_root_synthetic"
RAW_REL = f"data/raw/data_transfer/{RUN_NAME}/ftransfer_lsst_{RUN_NAME}"
LIGHT_STATIC_BASELINE = "configs/runs/full_week_light_static_2026-02-25_to_2026-03-04.yaml"


def test_unset_env_defaults_to_repository_checkout():
    assert resolve_data_root(environ={}) == REPO_ROOT
    assert not is_external_data_root(REPO_ROOT)


def test_external_absolute_root_resolves(tmp_path):
    root = resolve_data_root(environ={DATA_ROOT_ENV: str(tmp_path)})
    assert root == tmp_path.resolve()
    assert is_external_data_root(root)


@pytest.mark.parametrize(
    "value, message",
    [
        ("", "empty"),
        ("   ", "empty"),
        ("relative/root", "absolute"),
        ("/nonexistent/fink-data-root-for-tests", "does not exist"),
    ],
)
def test_malformed_root_fails(value, message):
    with pytest.raises(DataRootError, match=message):
        resolve_data_root(environ={DATA_ROOT_ENV: value})


def test_root_with_parent_traversal_fails(tmp_path):
    (tmp_path / "a").mkdir()
    with pytest.raises(DataRootError, match=r"\.\."):
        resolve_data_root(environ={DATA_ROOT_ENV: f"{tmp_path}/a/../a"})


def test_root_that_is_a_file_fails(tmp_path):
    target = tmp_path / "file"
    target.write_text("x", encoding="utf-8")
    with pytest.raises(DataRootError, match="not a directory"):
        resolve_data_root(environ={DATA_ROOT_ENV: str(target)})


def test_root_inside_checkout_fails(tmp_path):
    repo = tmp_path / "repo"
    (repo / "data").mkdir(parents=True)
    with pytest.raises(DataRootError, match="inside the Git checkout"):
        resolve_data_root(environ={DATA_ROOT_ENV: str(repo / "data")}, repo_root=repo)


def test_ingestion_plan_lands_under_external_root(tmp_path):
    manifest = load_run_manifest(_write_run_config(tmp_path / "run.yaml"))
    external = tmp_path / "external"
    external.mkdir()
    _write_raw(external)
    plan = plan_ingestion(manifest, build_raw_audit(external / RAW_REL), IngestionOptions(), project_root=external)
    assert plan.files and all(Path(path).is_relative_to(external / "data/raw") for path in plan.files)
    assert Path(plan.processed_dir).is_relative_to(external / "data/processed")
    assert Path(plan.output_dir).is_relative_to(external / "outputs")
    assert not Path(plan.processed_dir).is_relative_to(REPO_ROOT)


def test_manifest_absolute_paths_must_sit_under_the_data_root(tmp_path):
    manifest = load_run_manifest(_write_run_config(tmp_path / "run.yaml"))
    manifest.paths = RunPaths(
        raw_dir=str(tmp_path / RAW_REL),
        processed_dir=f"data/processed/data_transfer/runs/{RUN_NAME}/x",
        outputs_dir=f"outputs/data_transfer/runs/{RUN_NAME}/x",
    )
    assert validate_run_manifest(manifest, project_root=tmp_path)[0] == []
    other = tmp_path / "other"
    other.mkdir()
    errors, _warnings = validate_run_manifest(manifest, project_root=other)
    assert any("paths.raw_dir" in error for error in errors)


def test_existing_run_index_validates_against_default_and_external_roots(tmp_path):
    assert validate_run_index("configs/runs/index.yaml", project_root=REPO_ROOT)[0] == []
    assert validate_run_index("configs/runs/index.yaml", project_root=REPO_ROOT, data_root=tmp_path)[0] == []
    baseline = load_run_manifest(REPO_ROOT / LIGHT_STATIC_BASELINE)
    assert baseline.paths.raw_dir.startswith("data/raw/data_transfer/full_week/")
    assert baseline.lifecycle_state == "raw_partial"


def test_runner_ingests_into_external_root_without_touching_checkout(tmp_path):
    external = tmp_path / "external"
    external.mkdir()
    _write_raw(external)
    config = _write_run_config(tmp_path / "run.yaml")
    completed = _run_analysis(["--run-config", str(config), "--stage", "ingest", "--force"], external)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert f"data_root: {external.resolve()}" in completed.stderr
    result = json.loads(completed.stdout)
    assert result["status"] == "ingested"
    processed = external / f"data/processed/data_transfer/runs/{RUN_NAME}/{RUN_NAME}"
    assert (processed / "manifest_snapshot.yaml").exists()
    assert (external / f"outputs/data_transfer/runs/{RUN_NAME}/{RUN_NAME}/ingestion_summary.json").exists()
    assert not (REPO_ROOT / f"data/processed/data_transfer/runs/{RUN_NAME}").exists()
    assert not (REPO_ROOT / f"outputs/data_transfer/runs/{RUN_NAME}").exists()


def test_runner_dry_run_reports_data_root_and_baseline_stays_readable(tmp_path):
    completed = _run_analysis(["--run-config", LIGHT_STATIC_BASELINE, "--stage", "validate_manifest"], tmp_path)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    plan = json.loads(completed.stdout)
    assert plan["data_root"] == str(tmp_path.resolve())
    assert plan["packet_type"] == "light_static"
    assert plan["raw_state"] == "raw_missing"
    assert plan["writes_outputs"] is False


def test_runner_rejects_invalid_root_and_repo_rooted_stages(tmp_path):
    invalid = _run_analysis(["--run-config", LIGHT_STATIC_BASELINE, "--stage", "validate_manifest"], "relative/root")
    assert invalid.returncode == 2
    assert "must be an absolute path" in invalid.stdout
    legacy = _run_analysis(["--run-config", LIGHT_STATIC_BASELINE, "--stage", "triage", "--dry-run"], tmp_path)
    assert legacy.returncode == 2
    assert "repository checkout" in legacy.stdout


def _run_analysis(args, data_root):
    env = {key: value for key, value in os.environ.items() if key != DATA_ROOT_ENV}
    env[DATA_ROOT_ENV] = str(data_root)
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    return subprocess.run(
        [sys.executable, "scripts/run_analysis.py", *args],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _write_run_config(path):
    path.write_text(
        f"""
schema_version: 1
run_name: {RUN_NAME}
run_id: {RUN_NAME}
survey: lsst
broker: fink
topic: ftransfer_lsst_{RUN_NAME}
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
  processed_dir: data/processed/data_transfer/runs/{RUN_NAME}/{RUN_NAME}
  outputs_dir: outputs/data_transfer/runs/{RUN_NAME}/{RUN_NAME}
processing:
  plots: false
""",
        encoding="utf-8",
    )
    return path


def _write_raw(root):
    raw = root / RAW_REL
    raw.mkdir(parents=True, exist_ok=True)
    path = raw / "part.parquet"
    pd.DataFrame(
        {
            "diaObjectId": [1, 2],
            "diaSourceId": [101, 102],
            "midpointMjdTai": [61096.1, 61096.2],
            "band": ["g", "r"],
            "scienceFlux": [1.0, 2.0],
        }
    ).to_parquet(path, index=False)
    old = time.time() - 7200
    os.utime(path, (old, old))

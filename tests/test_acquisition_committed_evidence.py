"""The committed acquisition registry must stay valid, truthful and secret-free (FINK-G3B.0)."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from fink_lsst.acquisition.portal_config import compare_portal_configs, compile_portal_config, parse_portal_config
from fink_lsst.acquisition.profile import LSST_LIGHT_STATIC_ALL_ALERTS_V1
from fink_lsst.acquisition.registry import AcquisitionRegistry
from fink_lsst.acquisition.states import AcquisitionState as S, submission_attempted
from fink_lsst.bulk_transfer.run_manifest import find_credential_like_entries


REGISTRY = Path("configs/acquisitions")
MONTH_ONE = "acq_lsst_ls_v1_2026-02-25_to_2026-03-25_b1c7b482b56b"


def test_production_profile_digest_is_pinned():
    assert LSST_LIGHT_STATIC_ALL_ALERTS_V1.content_sha256() == "62d809b0b24c6d88fa0d9259e59255e28794012958e8deebaf2e7a5c2723d173"


def test_every_committed_acquisition_replays_cleanly():
    records = AcquisitionRegistry(REGISTRY).list_records()
    assert records, "the G3B.0 Month-1 dry run should be committed"
    for record in records:
        assert record.directory.name == record.acquisition_id
        assert not submission_attempted(record.entries), "no live submission is authorized before a later gate"


def test_month_one_dry_run_evidence():
    record = AcquisitionRegistry(REGISTRY).load(MONTH_ONE)
    assert record.state == S.PORTAL_VERIFIED
    assert record.request.portal_startdate == "2026-02-25"
    assert record.request.portal_stopdate == "2026-03-24"
    assert len(record.request.expected_dates) == 28
    verification = record.last_entry(S.PORTAL_VERIFIED).evidence
    assert verification["semantic_match"] is True and verification["ui_checks_passed"] is True
    assert verification["submit_clicked"] is False
    assert verification["submit_requests_blocked"] == 0
    assert verification["final_review"]["submit_visible"] is True
    downloaded = (record.directory / verification["downloaded_config_file"]).read_text(encoding="utf-8")
    assert compare_portal_configs(compile_portal_config(record.request), parse_portal_config(downloaded)) == []


def test_committed_acquisition_files_carry_no_credentials():
    for path in REGISTRY.rglob("*"):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        payloads = [json.loads(line) for line in text.splitlines()] if path.suffix == ".jsonl" else [json.loads(text)] if path.suffix == ".json" else [yaml.safe_load(text)]
        for payload in payloads:
            assert find_credential_like_entries(payload) == [], path
        lowered = text.lower()
        for marker in ("cookie", "storage_state", "bootstrap", "9092", "sasl"):
            assert marker not in lowered, (path, marker)


@pytest.mark.parametrize("path", [".venv-portal/bin/python", "configs/acquisitions/x/.submission.lock", "playwright-profile-a/Default", "trace.har", "auth_storage_state.json"])
def test_browser_and_lock_artifacts_are_ignored(path):
    assert subprocess.run(["git", "check-ignore", "-q", path], check=False).returncode == 0


def test_cli_wrapper_runs_from_a_checkout():
    completed = subprocess.run(
        [sys.executable, "scripts/fink_lsst_cli.py", "acquire", "--help"],
        text=True,
        capture_output=True,
        check=False,
        env={"PYTHONPATH": "src", "PATH": "/usr/bin:/bin"},
    )
    assert completed.returncode == 0, completed.stderr
    assert "--portal-check" in completed.stdout and "--filters" not in completed.stdout

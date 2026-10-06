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
    assert records, "the accepted Month-1 production evidence must be committed"
    for record in records:
        assert record.directory.name == record.acquisition_id
        if record.acquisition_id == MONTH_ONE:
            assert record.state == S.DELIVERY_VALIDATED
            assert submission_attempted(record.entries)
            assert record.entries[-1].entry_sha256 == "bb1d4080a8681931d46a6eac348717cd5b7cdd9bbe24b5b6f595f7f2df39a962"
        if submission_attempted(record.entries):
            approval = record.last_entry(S.APPROVED)
            submitting = record.last_entry(S.SUBMITTING)
            assert approval is not None and submitting is not None
            assert approval.evidence["approved_fingerprint"] == record.fingerprint
            assert submitting.evidence["approval_seq"] == approval.seq
            assert submitting.evidence["context_id"] == approval.evidence["context_id"]
        if record.state == S.DELIVERY_VALIDATED:
            receipt = record.last_entry(S.DELIVERY_VALIDATED).evidence["receipt"]
            rec = receipt["reconciliation"]
            assert rec["passed"] is True and rec["terminal_lag"] == 0
            assert rec["expected_topic_messages"] == rec["terminal_committed"] == rec["local_readable_rows"] == receipt["readable_rows"]


def test_month_one_production_evidence_preserves_dry_run_qualification():
    record = AcquisitionRegistry(REGISTRY).load(MONTH_ONE)
    assert record.state == S.DELIVERY_VALIDATED
    assert record.request.portal_startdate == "2026-02-25"
    assert record.request.portal_stopdate == "2026-03-24"
    assert len(record.request.expected_dates) == 28
    verifications = [entry.evidence for entry in record.entries if entry.to_state == S.PORTAL_VERIFIED]
    assert len(verifications) == 3  # initial dry run, R2 remediation, then production activation
    for verification in verifications:
        assert verification["semantic_match"] is True and verification["ui_checks_passed"] is True
        assert verification["submit_clicked"] is False
        assert verification["submit_requests_blocked"] == 0
        assert verification["final_review"]["submit_visible"] is True
        path = verification["downloaded_config_ref"]["path"] if "downloaded_config_ref" in verification else verification["downloaded_config_file"]
        downloaded = (record.directory / path).read_text(encoding="utf-8")
        assert compare_portal_configs(compile_portal_config(record.request), parse_portal_config(downloaded)) == []
    assert record.topic == "ftransfer_lsst_2026-10-05_555200" and record.batch_id == "42"
    delivery = record.last_entry(S.DELIVERY_VALIDATED).evidence["receipt"]
    assert delivery["readable_rows"] == 1658642
    assert delivery["parquet_files"] == delivery["readable_parquet_files"] == 16591
    assert delivery["unreadable_files"] == 0 and delivery["total_bytes"] == 1460135494
    assert delivery["schema_groups"] == {"94e24bb1455f3bc2": {"files": 16591, "rows": 1658642}}
    reconciliation = delivery["reconciliation"]
    assert reconciliation["passed"] is True
    assert reconciliation["expected_topic_messages"] == reconciliation["terminal_committed"] == reconciliation["local_readable_rows"] == 1658642
    assert reconciliation["terminal_lag"] == 0
    assert record.last_entry(S.TRANSFER_COMPLETE).evidence["receipt"]["terminal_committed"] == 1658642


def test_month_one_r2_qualification_is_bound_to_the_remediation_code():
    record = AcquisitionRegistry(REGISTRY).load(MONTH_ONE)
    qualified = [entry for entry in record.entries if entry.to_state == S.PORTAL_VERIFIED and entry.evidence.get("code_revision")]
    assert [entry.evidence["code_revision"] for entry in qualified] == [
        "a9c46bc09826a8a87ac3f516558f5f6f7a9e1759", "13a6ef36509aa6dcb7d5371e2819dc60be865c81"]
    qualification = qualified[0].evidence
    assert qualification["code_revision"] == "a9c46bc09826a8a87ac3f516558f5f6f7a9e1759"
    assert qualification["service_workers"] == "block"
    assert qualification["initial_submit_callbacks_blocked"] == 1 and qualification["submit_requests_blocked"] == 0
    assert qualification["context_id"].startswith("playwright-")
    assert qualification["downloaded_config_ref"]["kind"] == "portal_download"
    activation = qualified[-1]
    assert activation.evidence["service_workers"] == "block"
    assert activation.evidence["semantic_match"] is True and activation.evidence["submit_clicked"] is False
    approval = record.last_entry(S.APPROVED)
    assert approval.evidence["verification_seq"] == activation.seq
    assert approval.evidence["context_id"] == activation.evidence["context_id"]
    assert approval.evidence["approved_fingerprint"] == record.fingerprint
    submitting = record.last_entry(S.SUBMITTING)
    assert submitting.evidence["approval_seq"] == approval.seq
    assert submitting.evidence["context_id"] == approval.evidence["context_id"]


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

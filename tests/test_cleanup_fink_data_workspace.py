import importlib.util
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "cleanup_fink_data_workspace.py"


def load_cleanup_module():
    spec = importlib.util.spec_from_file_location("cleanup_fink_data_workspace", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def run_cleanup(tmp_path, *args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--project-root", str(tmp_path), *args],
        text=True,
        capture_output=True,
        check=False,
    )


def make_fake_project(tmp_path):
    for rel in (
        "data/raw/data_transfer",
        "data/processed/data_transfer",
        "outputs/data_transfer",
        "outputs/maintenance",
        "src",
        "scripts",
        "notebooks",
        "docs",
        "configs",
        "tests",
    ):
        (tmp_path / rel).mkdir(parents=True, exist_ok=True)
    (tmp_path / "data/raw/data_transfer/raw.parquet").write_text("raw", encoding="utf-8")
    (tmp_path / "data/processed/data_transfer/table.parquet").write_text("processed", encoding="utf-8")
    (tmp_path / "outputs/data_transfer/report.json").write_text("{}", encoding="utf-8")
    (tmp_path / "data/raw/data_transfer/.gitkeep").touch()
    (tmp_path / "data/raw/data_transfer/full_night").mkdir()
    (tmp_path / "data/raw/data_transfer/full_night/.gitkeep").touch()
    (tmp_path / "data/raw/data_transfer/smoke_delivery").mkdir()
    (tmp_path / "data/raw/data_transfer/smoke_delivery/.gitkeep").touch()
    (tmp_path / "data/processed/data_transfer/.gitkeep").touch()
    (tmp_path / "data/raw/other.txt").write_text("preserve", encoding="utf-8")
    (tmp_path / "src/app.py").write_text("print('keep')\n", encoding="utf-8")


def test_dry_run_does_not_move_or_delete_anything(tmp_path):
    make_fake_project(tmp_path)
    completed = run_cleanup(tmp_path, "--dry-run")
    assert completed.returncode == 0, completed.stderr
    assert (tmp_path / "data/raw/data_transfer/raw.parquet").exists()
    assert (tmp_path / "data/processed/data_transfer/table.parquet").exists()
    assert (tmp_path / "outputs/data_transfer/report.json").exists()
    assert not list((tmp_path / "outputs/maintenance").glob("fink_data_quarantine_*"))


def test_quarantine_moves_only_allowed_generated_data_directories(tmp_path):
    make_fake_project(tmp_path)
    completed = run_cleanup(tmp_path, "--quarantine")
    assert completed.returncode == 0, completed.stderr

    quarantine_dirs = list((tmp_path / "outputs/maintenance").glob("fink_data_quarantine_*"))
    assert len(quarantine_dirs) == 1
    quarantine = quarantine_dirs[0]
    assert (quarantine / "data/raw/data_transfer/raw.parquet").exists()
    assert (quarantine / "data/processed/data_transfer/table.parquet").exists()
    assert (quarantine / "outputs/data_transfer/report.json").exists()
    assert (tmp_path / "data/raw/other.txt").exists()
    assert (tmp_path / "src/app.py").exists()


def test_protected_source_code_directories_are_refused(tmp_path):
    make_fake_project(tmp_path)
    cleanup = load_cleanup_module()
    reason = cleanup.validate_operation_path(tmp_path, tmp_path / "src")
    assert reason is not None
    assert "protected project path" in reason


def test_symlink_to_outside_project_is_moved_as_link_not_followed(tmp_path):
    make_fake_project(tmp_path)
    outside = tmp_path.parent / f"{tmp_path.name}_outside.txt"
    outside.write_text("outside", encoding="utf-8")
    link = tmp_path / "data/raw/data_transfer/outside_link"
    link.symlink_to(outside)

    completed = run_cleanup(tmp_path, "--quarantine")
    assert completed.returncode == 0, completed.stderr

    quarantine = next((tmp_path / "outputs/maintenance").glob("fink_data_quarantine_*"))
    moved_link = quarantine / "data/raw/data_transfer/outside_link"
    assert moved_link.is_symlink()
    assert os.readlink(moved_link) == str(outside)
    assert outside.exists()
    assert outside.read_text(encoding="utf-8") == "outside"


def test_empty_data_directories_are_recreated_after_quarantine(tmp_path):
    make_fake_project(tmp_path)
    completed = run_cleanup(tmp_path, "--quarantine")
    assert completed.returncode == 0, completed.stderr
    assert (tmp_path / "data/raw/data_transfer").is_dir()
    assert (tmp_path / "data/processed/data_transfer").is_dir()
    assert (tmp_path / "outputs/data_transfer").is_dir()
    assert (tmp_path / "data/raw/data_transfer/.gitkeep").is_file()
    assert (tmp_path / "data/raw/data_transfer/full_night/.gitkeep").is_file()
    assert (tmp_path / "data/raw/data_transfer/smoke_delivery/.gitkeep").is_file()
    assert (tmp_path / "data/processed/data_transfer/.gitkeep").is_file()
    assert (tmp_path / "data/raw/data_transfer/.gitkeep").read_text(encoding="utf-8") == "\n"
    assert sorted(path.name for path in (tmp_path / "outputs/data_transfer").iterdir()) == []


def test_audit_json_and_markdown_files_are_written(tmp_path):
    make_fake_project(tmp_path)
    completed = run_cleanup(tmp_path, "--quarantine")
    assert completed.returncode == 0, completed.stderr
    audit_dirs = list((tmp_path / "outputs/maintenance").glob("fink_data_cleanup_*"))
    assert len(audit_dirs) == 1
    audit = audit_dirs[0]
    assert (audit / "FINK_DATA_CLEANUP_PLAN.md").exists()
    assert (audit / "fink_data_cleanup_plan.json").exists()
    assert (audit / "FINK_DATA_CLEANUP_RESULT.md").exists()
    assert (audit / "fink_data_cleanup_result.json").exists()


def test_delete_quarantine_refuses_paths_that_do_not_look_like_quarantine(tmp_path):
    make_fake_project(tmp_path)
    bad = tmp_path / "outputs/maintenance/not_a_cleanup_quarantine"
    bad.mkdir()
    completed = run_cleanup(tmp_path, "--delete-quarantine", "--quarantine-dir", str(bad))
    assert completed.returncode == 2
    assert bad.exists()
    assert "does not match" in completed.stdout

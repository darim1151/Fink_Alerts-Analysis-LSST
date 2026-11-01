import csv
import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "compare_fink_statistics.py"


def load_module():
    spec = importlib.util.spec_from_file_location("compare_fink_statistics", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def rec(night, alerts, version="4.1"):
    return {"f:night": night, "f:alerts": str(alerts), "f:fink_broker_version": version, "f:fink_science_version": "8.40.0"}


def test_alerts_moved_to_a_later_night_are_consistent(tmp_path):
    m = load_module()
    delivered = {"2026-04-04": 834, "2026-04-05": 3123, "2026-04-06": 5954, "2026-04-07": 0}
    stats = [rec("20260403", 99), rec("20260405", 3957), rec("20260406", 5954), rec("20260408", 7)]
    rows, s = m.compare(delivered, stats, "2026-04-04", "2026-04-08")
    assert s["totals_equal"] and s["running_total_never_behind"] and s["consistent"]
    assert s["delivered_total"] == s["fink_total_in_window"] == 9911 and s["fink_nights_in_window"] == 2
    assert s["dates_different"] == ["2026-04-04", "2026-04-05"] and s["largest_running_difference"] == 834
    assert [r["fink_statistics"] for r in rows] == ["", 3957, 5954, ""]


def test_inconsistent_patterns_are_flagged():
    m = load_module()
    _, early = m.compare({"2026-04-04": 0, "2026-04-05": 10}, [rec("20260404", 10)], "2026-04-04", "2026-04-06")
    assert early["totals_equal"] and not early["running_total_never_behind"] and not early["consistent"]
    _, short = m.compare({"2026-04-04": 10}, [rec("20260404", 12)], "2026-04-04", "2026-04-05")
    assert not short["totals_equal"] and not short["consistent"]
    with pytest.raises(ValueError, match="twice"):
        m.compare({}, [rec("20260404", 1), rec("20260404", 2)], "2026-04-04", "2026-04-05")
    with pytest.raises(ValueError, match="outside the window"):
        m.compare({"2026-05-01": 3}, [], "2026-04-04", "2026-04-05")


def test_main_reads_bundle_or_csv_and_writes_outputs(tmp_path):
    m = load_module()
    stats = tmp_path / "stats.json"
    stats.write_text(json.dumps([rec("20260405", 3957), rec("20260406", 5954, "5.0rc0")]))
    bundle = tmp_path / "time.json"
    bundle.write_text(json.dumps({"counts": {"values": {"2026-04-04": 834, "2026-04-05": 3123, "2026-04-06": 5954}}}))
    s = m.main(["--stats", str(stats), "--delivered", str(bundle), "--start", "2026-04-04", "--stop", "2026-04-07",
                "--out", str(tmp_path / "a"), "--code-sha", "test"])
    assert s["consistent"] and s["fink_versions_in_window"] == ["4.1 / 8.40.0", "5.0rc0 / 8.40.0"]
    assert (tmp_path / "a" / "fink_statistics.json").read_bytes() == stats.read_bytes()
    table = list(csv.DictReader(open(tmp_path / "a" / "per_date.csv")))
    assert [r["cumulative_difference"] for r in table] == ["834", "0", "0"]
    flat = tmp_path / "delivered.csv"
    flat.write_text("date,alerts\n2026-04-04,834\n2026-04-05,3123\n2026-04-06,5954\n")
    s2 = m.main(["--stats", str(stats), "--delivered", str(flat), "--start", "2026-04-04", "--stop", "2026-04-07",
                 "--out", str(tmp_path / "b"), "--code-sha", "test"])
    assert s2["consistent"] and s2["delivered_total"] == s["delivered_total"]
    with pytest.raises(FileExistsError):
        m.main(["--stats", str(stats), "--delivered", str(flat), "--start", "2026-04-04", "--stop", "2026-04-07",
                "--out", str(tmp_path / "b"), "--code-sha", "test"])

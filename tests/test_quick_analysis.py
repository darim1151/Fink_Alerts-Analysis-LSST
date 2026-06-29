import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from fink_lsst.bulk_transfer.quick_analysis import (
    build_quick_partial_report,
    extract_flat_sample,
    summarize_raw_metadata,
    write_quick_analysis_outputs,
)


def test_metadata_summary_on_synthetic_parquet_files(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    _write_packet(raw / "part-1.parquet", rows=2)
    _write_packet(raw / "part-2.parquet", rows=3)
    summary = summarize_raw_metadata(raw, expected_total=10, progress_every=0)
    assert summary["file_count"] == 2
    assert summary["readable_file_count"] == 2
    assert summary["readable_rows"] == 5
    assert summary["scientific_completeness"] is False
    assert round(summary["apparent_percent_complete"], 1) == 50.0


def test_unreadable_file_handling(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    _write_packet(raw / "part.parquet", rows=1)
    (raw / "bad.parquet").write_text("not parquet", encoding="utf-8")
    summary = summarize_raw_metadata(raw, progress_every=0)
    assert summary["file_count"] == 2
    assert summary["failed_file_count"] == 1
    assert summary["readable_rows"] == 1


def test_sample_flattening_of_diasource_and_clf(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    _write_packet(raw / "part.parquet", rows=2)
    sample, summary = extract_flat_sample(raw, max_files=1, progress_every=0)
    assert summary["rows"] == 2
    assert "diaSource.diaSourceId" in sample.columns
    assert "diaSource.ra" in sample.columns
    assert "clf.snnSnVsOthers_score" in sample.columns
    assert "clf.cats_class" in sample.columns


def test_cutout_heavy_fields_skipped_by_default(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    _write_packet(raw / "part.parquet", rows=1, include_cutout=True)
    sample, summary = extract_flat_sample(raw, max_files=1, progress_every=0)
    assert "cutoutScience" not in sample.columns
    assert "cutoutScience" in summary["skipped_nested_or_heavy_fields"]


def test_report_generation(tmp_path):
    raw = tmp_path / "raw"
    out = tmp_path / "out"
    raw.mkdir()
    _write_packet(raw / "part.parquet", rows=2)
    metadata = summarize_raw_metadata(raw, expected_total=10, progress_every=0)
    sample, _summary = extract_flat_sample(raw, max_files=1, progress_every=0)
    report = build_quick_partial_report(sample, metadata, out)
    assert report["analysis_type"] == "partial_debug_stress_test"
    assert (out / "QUICK_PARTIAL_ANALYSIS.md").exists()
    assert (out / "sample_summary.json").exists()


def test_write_quick_analysis_outputs_sample_mode(tmp_path):
    raw = tmp_path / "raw"
    out = tmp_path / "out"
    raw.mkdir()
    _write_packet(raw / "part.parquet", rows=2)
    result = write_quick_analysis_outputs(raw, out_dir=out, expected_total=10, max_files=1, progress_every=0)
    assert Path(result["file_rows_path"]).exists()
    assert Path(result["sample_path"]).exists()
    assert Path(result["quick_report_path"]).exists()


def test_cli_metadata_only_mode(tmp_path):
    raw = tmp_path / "raw"
    out = tmp_path / "out"
    raw.mkdir()
    _write_packet(raw / "part.parquet", rows=1)
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_quick_full_packet_analysis.py",
            "--raw-dir",
            str(raw),
            "--out-dir",
            str(out),
            "--metadata-only",
            "--write-report",
            "--progress-every",
            "0",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0
    assert "partial debug/stress-test only" in completed.stdout
    assert (out / "raw_metadata_summary.json").exists()
    assert not (out / "sample_5000_files_flattened.parquet").exists()


def test_cli_sample_mode(tmp_path):
    raw = tmp_path / "raw"
    out = tmp_path / "out"
    raw.mkdir()
    _write_packet(raw / "part.parquet", rows=1)
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_quick_full_packet_analysis.py",
            "--raw-dir",
            str(raw),
            "--out-dir",
            str(out),
            "--max-files",
            "1",
            "--write-report",
            "--progress-every",
            "0",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0
    assert (out / "sample_1_files_flattened.parquet").exists()
    assert (out / "sample_1_summary.json").exists()


def test_notebook_file_exists_and_is_valid_json():
    path = Path("notebooks/10_full_packet_partial_stress_viewer.ipynb")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["nbformat"] == 4
    assert payload["cells"]
    assert any("partial" in "".join(cell.get("source", [])).lower() for cell in payload["cells"])


def _write_packet(path, rows=2, include_cutout=False):
    dia_source_type = pa.struct(
        [
            pa.field("diaSourceId", pa.int64()),
            pa.field("diaObjectId", pa.int64()),
            pa.field("midPointTai", pa.float64()),
            pa.field("ra", pa.float64()),
            pa.field("decl", pa.float64()),
            pa.field("psFlux", pa.float64()),
            pa.field("psFluxErr", pa.float64()),
            pa.field("band", pa.string()),
        ]
    )
    clf_type = pa.struct(
        [
            pa.field("snnSnVsOthers_score", pa.float64()),
            pa.field("cats_class", pa.string()),
            pa.field("cats_score", pa.float64()),
        ]
    )
    arrays = [
        pa.array(list(range(100, 100 + rows)), type=pa.int64()),
        pa.array(["science"] * rows, type=pa.string()),
        pa.array(["target"] * rows, type=pa.string()),
        pa.array([61096.1] * rows, type=pa.float64()),
        pa.array(
            [
                {
                    "diaSourceId": 100 + index,
                    "diaObjectId": 200 + index,
                    "midPointTai": 61096.1,
                    "ra": 12.0 + index,
                    "decl": -2.0,
                    "psFlux": 1.5,
                    "psFluxErr": 0.1,
                    "band": "g" if index % 2 == 0 else "r",
                }
                for index in range(rows)
            ],
            type=dia_source_type,
        ),
        pa.array(
            [
                {"snnSnVsOthers_score": 0.9, "cats_class": "SN", "cats_score": 0.8}
                for _index in range(rows)
            ],
            type=clf_type,
        ),
    ]
    names = ["diaSourceId", "observation_reason", "target_name", "brokerIngestMjd", "diaSource", "clf"]
    if include_cutout:
        arrays.append(pa.array([b"abc"] * rows, type=pa.binary()))
        names.append("cutoutScience")
    table = pa.Table.from_arrays(arrays, names=names)
    pq.write_table(table, path)

"""Fast partial full-packet stress analysis without heavy nested serialization."""

from __future__ import annotations

import json
import random
import warnings
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from fink_lsst.storage import write_json

from .raw_audit import discover_raw_files, read_parquet_metadata_safe


DEFAULT_OUT_DIR = Path("outputs/data_transfer/quick_analysis/full_week_full_packet_partial")
HEAVY_MARKERS = ("cutout", "stamp", "fits", "image", "bytes", "binary")
DIASOURCE_FIELDS = (
    "diaSourceId",
    "diaObjectId",
    "midPointTai",
    "midpointMjdTai",
    "ra",
    "decl",
    "dec",
    "psFlux",
    "psFluxErr",
    "snr",
    "band",
)
OBJECT_FIELD_MARKERS = ("ra", "decl", "dec", "flux", "mag", "count", "nobs", "latest", "mean")
PREDICTOR_STRUCTS = ("diaObject", "pred", "xm", "misc")
CLF_FIELDS = (
    "snnSnVsOthers_score",
    "cats_class",
    "cats_score",
    "earlySNIa_score",
    "elephant_kstest_science",
    "elephant_kstest_template",
)
TOP_LEVEL_KEEP = (
    "diaSourceId",
    "observation_reason",
    "target_name",
    "brokerIngestMjd",
    "lsst_schema_version",
    "brokerStartProcessTimestamp",
    "fink_broker_version",
    "fink_science_version",
    "publisher",
    "brokerEndProcessTimestamp",
    "timestamp",
    "year",
    "month",
    "day",
    "tns_type_recomputed",
)


def summarize_raw_metadata(raw_dir: str | Path, expected_total: int | None = None, progress_every: int = 10000) -> dict[str, Any]:
    """Scan Parquet metadata without loading full payloads."""
    root = Path(raw_dir)
    files = [path for path in discover_raw_files(root) if path.suffix.lower() == ".parquet"]
    metadata = []
    schema_groups: dict[str, dict[str, Any]] = {}
    failures = []
    total_rows = 0
    for index, path in enumerate(files, start=1):
        item = read_parquet_metadata_safe(path)
        metadata.append(item)
        if not item.get("readable"):
            failures.append(item)
            continue
        total_rows += int(item.get("rows", 0))
        schema_hash = item.get("schema_hash", "unknown")
        group = schema_groups.setdefault(
            schema_hash,
            {
                "schema_hash": schema_hash,
                "file_count": 0,
                "rows": 0,
                "columns": item.get("columns", []),
                "fields": item.get("fields", []),
            },
        )
        group["file_count"] += 1
        group["rows"] += int(item.get("rows", 0))
        if progress_every and index % progress_every == 0:
            print(f"metadata progress: {index}/{len(files)} files")
    apparent_percent = float(total_rows / expected_total * 100) if expected_total else None
    file_rows = pd.DataFrame(
        [
            {
                "path": item.get("path"),
                "rows": int(item.get("rows", 0) or 0),
                "readable": bool(item.get("readable")),
                "size_bytes": item.get("size_bytes"),
                "schema_hash": item.get("schema_hash"),
                "error_type": item.get("error_type"),
            }
            for item in metadata
        ]
    )
    return {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "analysis_type": "partial_debug_stress_test",
        "raw_dir": str(root),
        "file_count": len(files),
        "readable_file_count": sum(1 for item in metadata if item.get("readable")),
        "failed_file_count": len(failures),
        "readable_rows": int(total_rows),
        "expected_total": expected_total,
        "apparent_percent_complete": apparent_percent,
        "scientific_completeness": False,
        "schema_group_count": len(schema_groups),
        "schema_groups": list(schema_groups.values()),
        "columns": sorted({column for group in schema_groups.values() for column in group.get("columns", [])}),
        "failed_files": failures[:100],
        "file_rows": file_rows,
    }


def extract_flat_sample(
    raw_dir: str | Path,
    max_files: int | None = 5000,
    include_cutout_columns: bool = False,
    progress_every: int = 1000,
    sample_strategy: str = "first",
    random_seed: int = 42,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Extract a flattened scalar sample from up to max_files raw Parquet files."""
    root = Path(raw_dir)
    files = select_raw_files(
        [path for path in discover_raw_files(root) if path.suffix.lower() == ".parquet"],
        max_files=max_files,
        sample_strategy=sample_strategy,
        random_seed=random_seed,
    )
    frames = []
    skipped_nested = Counter()
    failed_files = []
    for index, path in enumerate(files, start=1):
        try:
            columns, skipped_columns = _safe_read_columns(path, include_cutout_columns=include_cutout_columns)
            for column in skipped_columns:
                skipped_nested[column] += 1
            frame = pd.read_parquet(path, columns=columns)
            flat = _flatten_frame(frame, path, skipped_nested, include_cutout_columns=include_cutout_columns)
            frames.append(flat)
        except Exception as exc:  # noqa: BLE001
            failed_files.append({"path": str(path), "error_type": type(exc).__name__, "error": str(exc)})
        if progress_every and index % progress_every == 0:
            print(f"sample progress: {index}/{len(files)} files")
    sample = _concat_frames(frames)
    summary = {
        "analysis_type": "partial_debug_stress_test",
        "max_files": int(max_files) if max_files is not None else None,
        "sample_strategy": sample_strategy,
        "files_attempted": len(files),
        "files_succeeded": len(frames),
        "files_failed": len(failed_files),
        "rows": int(len(sample)),
        "columns": list(sample.columns),
        "skipped_nested_or_heavy_fields": dict(skipped_nested),
        "failed_files": failed_files[:100],
        "include_cutout_columns": bool(include_cutout_columns),
    }
    return sample, summary


def extract_flat_batches(
    raw_dir: str | Path,
    out_dir: str | Path,
    max_files: int | None = None,
    batch_size: int = 5000,
    include_cutout_columns: bool = False,
    progress_every: int = 1000,
    sample_strategy: str = "first",
    random_seed: int = 42,
    resume: bool = False,
    force: bool = False,
) -> tuple[list[str], dict[str, Any]]:
    """Extract flattened rows in Parquet batches without holding all rows in memory."""
    root = Path(raw_dir)
    output = Path(out_dir)
    batches_dir = output / "batches"
    batches_dir.mkdir(parents=True, exist_ok=True)
    all_files = [path for path in discover_raw_files(root) if path.suffix.lower() == ".parquet"]
    files = select_raw_files(all_files, max_files=max_files, sample_strategy=sample_strategy, random_seed=random_seed)
    batch_paths: list[str] = []
    skipped_nested = Counter()
    failed_files = []
    batch_summaries = []
    total_rows = 0
    for batch_index, start in enumerate(range(0, len(files), batch_size), start=1):
        batch_files = files[start : start + batch_size]
        batch_path = batches_dir / f"flattened_batch_{batch_index:05d}.parquet"
        summary_path = batches_dir / f"flattened_batch_{batch_index:05d}.json"
        if resume and batch_path.exists() and summary_path.exists() and not force:
            batch_paths.append(str(batch_path))
            existing = json.loads(summary_path.read_text(encoding="utf-8"))
            batch_summaries.append(existing)
            total_rows += int(existing.get("rows", 0))
            continue
        frames = []
        batch_failed = []
        for offset, path in enumerate(batch_files, start=1):
            global_index = start + offset
            try:
                columns, skipped_columns = _safe_read_columns(path, include_cutout_columns=include_cutout_columns)
                for column in skipped_columns:
                    skipped_nested[column] += 1
                frame = pd.read_parquet(path, columns=columns)
                frames.append(_flatten_frame(frame, path, skipped_nested, include_cutout_columns=include_cutout_columns))
            except Exception as exc:  # noqa: BLE001
                failure = {"path": str(path), "error_type": type(exc).__name__, "error": str(exc)}
                batch_failed.append(failure)
                failed_files.append(failure)
            if progress_every and global_index % progress_every == 0:
                print(f"sample progress: {global_index}/{len(files)} files")
        batch_df = _concat_frames([frame for frame in frames if not frame.empty])
        _write_parquet(batch_df, batch_path)
        batch_summary = {
            "batch_index": batch_index,
            "path": str(batch_path),
            "files_attempted": len(batch_files),
            "files_failed": len(batch_failed),
            "rows": int(len(batch_df)),
            "columns": list(batch_df.columns),
            "failed_files": batch_failed[:100],
        }
        write_json(batch_summary, summary_path, enforce_allowed_roots=False)
        batch_paths.append(str(batch_path))
        batch_summaries.append(batch_summary)
        total_rows += int(len(batch_df))
    summary = {
        "analysis_type": "partial_debug_stress_test",
        "max_files": int(max_files) if max_files is not None else None,
        "sample_strategy": sample_strategy,
        "random_seed": int(random_seed),
        "batch_size": int(batch_size),
        "source_file_count": len(all_files),
        "files_attempted": len(files),
        "batch_count": len(batch_paths),
        "rows": int(total_rows),
        "batch_paths": batch_paths,
        "batch_summaries": batch_summaries,
        "skipped_nested_or_heavy_fields": dict(skipped_nested),
        "failed_files": failed_files[:100],
        "include_cutout_columns": bool(include_cutout_columns),
    }
    write_json(summary, output / "batch_extraction_summary.json", enforce_allowed_roots=False)
    return batch_paths, summary


def select_raw_files(
    files: list[Path],
    max_files: int | None,
    sample_strategy: str = "first",
    random_seed: int = 42,
) -> list[Path]:
    """Select raw files with a deterministic sampling strategy."""
    files = sorted(files)
    if max_files is None or max_files >= len(files):
        return files
    if sample_strategy == "first":
        return files[:max_files]
    if sample_strategy == "evenly_spaced":
        if max_files <= 1:
            return files[:max_files]
        step = (len(files) - 1) / (max_files - 1)
        return [files[round(index * step)] for index in range(max_files)]
    if sample_strategy == "random":
        rng = random.Random(random_seed)
        return sorted(rng.sample(files, max_files))
    raise ValueError(f"Unsupported sample strategy: {sample_strategy}")


def build_quick_partial_report(df: pd.DataFrame, metadata_summary: dict[str, Any], out_dir: str | Path) -> dict[str, Any]:
    """Build and write quick partial-analysis report payloads."""
    output = Path(out_dir)
    band_column = _first_present(df, ("diaSource.band", "band", "r:band"))
    object_column = _first_present(df, ("diaSource.diaObjectId", "diaObjectId", "r:diaObjectId"))
    source_column = _first_present(df, ("diaSource.diaSourceId", "diaSourceId", "r:diaSourceId"))
    ra_column = _first_present(df, ("diaSource.ra", "ra", "r:ra"))
    dec_column = _first_present(df, ("diaSource.decl", "diaSource.dec", "dec", "r:dec"))
    time_column = _first_present(df, ("diaSource.midPointTai", "diaSource.midpointMjdTai", "brokerIngestMjd", "timestamp"))
    clf_columns = [column for column in df.columns if column.startswith("clf.")]
    summary = {
        "analysis_type": "partial_debug_stress_test",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "metadata": _json_metadata(metadata_summary),
        "sample_rows": int(len(df)),
        "sample_columns": list(df.columns),
        "band_counts": _value_counts(df[band_column]) if band_column else {},
        "object_column": object_column,
        "source_column": source_column,
        "unique_objects": int(df[object_column].nunique()) if object_column else None,
        "unique_sources": int(df[source_column].nunique()) if source_column else None,
        "classifier_columns": clf_columns,
        "time_coverage": _numeric_range(df, time_column) if time_column else {},
        "sky_coverage": {"ra": _numeric_range(df, ra_column), "dec": _numeric_range(df, dec_column)} if ra_column or dec_column else {},
        "missingness": {column: int(df[column].isna().sum()) for column in df.columns[:200]},
        "sesar_style_summary": build_sesar_style_summary(df),
        "limitations": [
            "partial debug/stress-test only",
            "not a complete week",
            "not a complete first day",
            "not a validated all-alert census",
            "cutout/heavy nested payloads are skipped by default",
        ],
    }
    output.mkdir(parents=True, exist_ok=True)
    write_json(summary, output / "sample_summary.json", enforce_allowed_roots=False)
    (output / "QUICK_PARTIAL_ANALYSIS.md").write_text(render_quick_partial_report(summary), encoding="utf-8")
    return summary


def build_full_partial_visual_report(
    summary: dict[str, Any],
    metadata_summary: dict[str, Any],
    figure_paths: list[str],
    out_dir: str | Path,
) -> Path:
    """Write the full partial visual-analysis Markdown report."""
    output = Path(out_dir)
    metadata = _json_metadata(metadata_summary)
    sample = summary or {}
    lines = [
        "# Full Available Partial Full-Packet Visual Analysis",
        "",
        "> Partial debug/stress-test only. This is not a complete week, not a complete first day, and not a validated all-alert census.",
        "",
        "## Dataset Scale",
        "",
        f"- Raw files: `{metadata.get('file_count')}`",
        f"- Readable files: `{metadata.get('readable_file_count')}`",
        f"- Readable rows: `{metadata.get('readable_rows')}`",
        f"- Expected total: `{metadata.get('expected_total')}`",
        f"- Apparent completion: `{metadata.get('apparent_percent_complete')}`",
        f"- Remaining rows: `{_remaining_rows(metadata)}`",
        f"- Schema groups: `{metadata.get('schema_group_count')}`",
        f"- Scientific completeness: `{metadata.get('scientific_completeness')}`",
        "",
        "## Field Availability",
        "",
        f"- Extracted columns: `{len(sample.get('sample_columns', []))}`",
        f"- Classifier columns: `{', '.join(sample.get('classifier_columns', [])) or 'none'}`",
        f"- Heavy fields skipped: `{', '.join(sorted((sample.get('sample_extraction') or {}).get('skipped_nested_or_heavy_fields', {}).keys())) or 'none recorded'}`",
        "",
        "## Band Distribution",
        "",
    ]
    for band, count in (sample.get("band_counts") or {}).items():
        lines.append(f"- `{band}`: `{count}`")
    if not sample.get("band_counts"):
        lines.append("- No band column available.")
    lines.extend(
        [
            "",
            "## Object And Source Behavior",
            "",
            f"- Unique objects: `{sample.get('unique_objects')}`",
            f"- Unique sources: `{sample.get('unique_sources')}`",
            "- `diaObjectId = 0` is treated as a quality warning when present and should not be interpreted as a real repeated object population.",
            "",
            "## Classifier Summary",
            "",
        ]
    )
    for column in sample.get("classifier_columns", []):
        lines.append(f"- `{column}`")
    if not sample.get("classifier_columns"):
        lines.append("- No classifier columns extracted.")
    lines.extend(
        [
            "",
            "## Sesar-Style Variability Hooks",
            "",
        ]
    )
    sesar = sample.get("sesar_style_summary") or {}
    for key, value in sesar.items():
        lines.append(f"- `{key}`: `{value}`")
    if not sesar:
        lines.append("- Flux/error/time fields were insufficient for variability hooks.")
    lines.extend(
        [
            "",
            "## Data-Quality Observations",
            "",
            "- Row/file distribution should be interpreted as delivery partition behavior as well as alert density.",
            "- The sample/full flattened table skips cutout/heavy payloads by default.",
            "- Missingness and classifier availability are useful for pipeline design, not completeness claims.",
            "",
            "## Scientific Interpretation",
            "",
            "- This partial dataset supports schema stress testing, plotting ergonomics, field availability checks, classifier sanity checks, and quick exploratory data-quality analysis.",
            "- It cannot support full-week completeness, first-day completeness, all-alert census completeness, or variable-source census completeness.",
            "- A complete one-night or full-week run would add complete denominator evidence, validated night coverage, and stronger population-level quality checks.",
            "",
            "## Figure Index",
            "",
        ]
    )
    for path in figure_paths:
        lines.append(f"- `{path}`")
    if not figure_paths:
        lines.append("- No figures generated.")
    report_path = output / "FULL_PARTIAL_VISUAL_ANALYSIS.md"
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path


def write_quick_analysis_outputs(
    raw_dir: str | Path,
    out_dir: str | Path = DEFAULT_OUT_DIR,
    expected_total: int | None = None,
    max_files: int | None = 5000,
    metadata_only: bool = False,
    include_cutout_columns: bool = False,
    progress_every: int = 1000,
    batch_size: int = 5000,
    sample_strategy: str = "first",
    random_seed: int = 42,
    no_combine: bool = False,
    resume: bool = False,
    force: bool = False,
    write_plots: bool = False,
) -> dict[str, Any]:
    """Run quick partial analysis and write all requested outputs."""
    output = Path(out_dir)
    output.mkdir(parents=True, exist_ok=True)
    metadata = summarize_raw_metadata(raw_dir, expected_total=expected_total, progress_every=progress_every)
    file_rows = metadata.pop("file_rows")
    write_json(_json_metadata(metadata), output / "raw_metadata_summary.json", enforce_allowed_roots=False)
    _write_parquet(file_rows, output / "file_rows.parquet")
    (output / "RAW_METADATA_SUMMARY.md").write_text(render_raw_metadata_summary(metadata), encoding="utf-8")
    result = {
        "metadata_summary_path": str(output / "raw_metadata_summary.json"),
        "metadata_markdown_path": str(output / "RAW_METADATA_SUMMARY.md"),
        "file_rows_path": str(output / "file_rows.parquet"),
        "metadata": metadata,
        "sample_summary": None,
    }
    if metadata_only:
        return result
    batch_paths, sample_extraction_summary = extract_flat_batches(
        raw_dir,
        out_dir=output,
        max_files=max_files,
        batch_size=batch_size,
        include_cutout_columns=include_cutout_columns,
        progress_every=progress_every,
        sample_strategy=sample_strategy,
        random_seed=random_seed,
        resume=resume,
        force=force,
    )
    sample = combine_batch_outputs(batch_paths) if not no_combine else pd.DataFrame()
    file_label = "all" if max_files is None else str(max_files)
    sample_path = output / f"sample_{file_label}_files_flattened.parquet"
    if not no_combine:
        _write_parquet(sample, sample_path)
    report = build_quick_partial_report(sample, metadata, output) if not sample.empty else build_empty_quick_report(metadata, sample_extraction_summary, output)
    sample_summary_path = output / f"sample_{file_label}_summary.json"
    write_json({**report, "sample_extraction": sample_extraction_summary}, sample_summary_path, enforce_allowed_roots=False)
    figure_paths: list[str] = []
    if write_plots:
        from .quick_plots import generate_quick_analysis_plots

        figure_paths = generate_quick_analysis_plots(
            sample,
            file_rows,
            output / "figures",
            metadata_summary=metadata,
            report_summary={**report, "sample_extraction": sample_extraction_summary},
        )
    full_report_path = build_full_partial_visual_report(
        {**report, "sample_extraction": sample_extraction_summary},
        metadata,
        figure_paths,
        output,
    )
    result.update(
        {
            "sample_path": str(sample_path) if not no_combine else None,
            "batch_paths": batch_paths,
            "batch_summary_path": str(output / "batch_extraction_summary.json"),
            "sample_summary_path": str(sample_summary_path),
            "quick_report_path": str(output / "QUICK_PARTIAL_ANALYSIS.md"),
            "full_visual_report_path": str(full_report_path),
            "figure_paths": figure_paths,
            "sample_summary": {**report, "sample_extraction": sample_extraction_summary},
        }
    )
    return result


def combine_batch_outputs(batch_paths: list[str]) -> pd.DataFrame:
    """Read flattened batch files and concatenate them into one DataFrame."""
    frames = [pd.read_parquet(path) for path in batch_paths if Path(path).exists()]
    return _concat_frames(frames)


def _concat_frames(frames: list[pd.DataFrame]) -> pd.DataFrame:
    """Concatenate sparse flattened frames while preserving current pandas behavior."""
    if not frames:
        return pd.DataFrame()
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="The behavior of DataFrame concatenation with empty or all-NA entries is deprecated.*",
            category=FutureWarning,
        )
        return pd.concat(frames, ignore_index=True, sort=False)


def build_empty_quick_report(metadata: dict[str, Any], extraction_summary: dict[str, Any], out_dir: str | Path) -> dict[str, Any]:
    """Build a report payload when no combined flattened table is written."""
    output = Path(out_dir)
    summary = {
        "analysis_type": "partial_debug_stress_test",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "metadata": _json_metadata(metadata),
        "sample_rows": int(extraction_summary.get("rows", 0)),
        "sample_columns": [],
        "band_counts": {},
        "object_column": None,
        "source_column": None,
        "unique_objects": None,
        "unique_sources": None,
        "classifier_columns": [],
        "time_coverage": {},
        "sky_coverage": {},
        "missingness": {},
        "sesar_style_summary": {},
        "limitations": [
            "partial debug/stress-test only",
            "combined flattened table was not written",
            "not a complete week",
            "not a complete first day",
            "not a validated all-alert census",
        ],
    }
    output.mkdir(parents=True, exist_ok=True)
    write_json(summary, output / "sample_summary.json", enforce_allowed_roots=False)
    (output / "QUICK_PARTIAL_ANALYSIS.md").write_text(render_quick_partial_report(summary), encoding="utf-8")
    return summary


def render_raw_metadata_summary(metadata: dict[str, Any]) -> str:
    """Render metadata summary Markdown."""
    lines = [
        "# Raw Metadata Summary",
        "",
        "- Analysis type: `partial_debug_stress_test`",
        f"- Raw dir: `{metadata.get('raw_dir')}`",
        f"- Files: `{metadata.get('file_count')}`",
        f"- Readable files: `{metadata.get('readable_file_count')}`",
        f"- Failed files: `{metadata.get('failed_file_count')}`",
        f"- Readable rows: `{metadata.get('readable_rows')}`",
        f"- Expected total: `{metadata.get('expected_total')}`",
        f"- Apparent percent complete: `{metadata.get('apparent_percent_complete')}`",
        f"- Scientific completeness: `{metadata.get('scientific_completeness')}`",
        f"- Schema groups: `{metadata.get('schema_group_count')}`",
        "",
        "No completeness claim is made.",
        "",
    ]
    return "\n".join(lines)


def render_quick_partial_report(summary: dict[str, Any]) -> str:
    """Render quick partial-analysis Markdown."""
    metadata = summary.get("metadata", {})
    lines = [
        "# Quick Partial Full-Packet Stress Analysis",
        "",
        "- Analysis type: `partial_debug_stress_test`",
        "- Completeness claim: `blocked`",
        f"- Raw files: `{metadata.get('file_count')}`",
        f"- Readable rows: `{metadata.get('readable_rows')}`",
        f"- Expected total: `{metadata.get('expected_total')}`",
        f"- Apparent percent complete: `{metadata.get('apparent_percent_complete')}`",
        f"- Sample rows: `{summary.get('sample_rows')}`",
        f"- Unique objects: `{summary.get('unique_objects')}`",
        f"- Unique sources: `{summary.get('unique_sources')}`",
        "",
        "## Band Counts",
        "",
    ]
    for key, value in summary.get("band_counts", {}).items():
        lines.append(f"- `{key}`: `{value}`")
    if not summary.get("band_counts"):
        lines.append("- No band column found.")
    lines.extend(["", "## Classifier Columns", ""])
    for column in summary.get("classifier_columns", []):
        lines.append(f"- `{column}`")
    if not summary.get("classifier_columns"):
        lines.append("- None found.")
    lines.extend(["", "## Limitations", ""])
    for item in summary.get("limitations", []):
        lines.append(f"- {item}")
    return "\n".join(lines) + "\n"


def _safe_read_columns(path: Path, include_cutout_columns: bool) -> tuple[list[str] | None, list[str]]:
    metadata = read_parquet_metadata_safe(path)
    columns = metadata.get("columns") or []
    if not columns:
        return None, []
    selected = []
    skipped = []
    for column in columns:
        lowered = column.lower()
        if not include_cutout_columns and any(marker in lowered for marker in HEAVY_MARKERS):
            skipped.append(column)
            continue
        if column in TOP_LEVEL_KEEP or column in {"diaSource", "clf", *PREDICTOR_STRUCTS} or "." in column:
            selected.append(column)
    return selected or None, skipped


def _flatten_frame(df: pd.DataFrame, path: Path, skipped_nested: Counter, include_cutout_columns: bool) -> pd.DataFrame:
    rows = []
    for _, row in df.iterrows():
        flat: dict[str, Any] = {"_raw_file": str(path)}
        for column, value in row.items():
            lowered = column.lower()
            if not include_cutout_columns and any(marker in lowered for marker in HEAVY_MARKERS):
                skipped_nested[column] += 1
                continue
            if column == "diaSource":
                source = _mapping(value)
                for field in DIASOURCE_FIELDS:
                    if field in source:
                        flat[f"diaSource.{field}"] = source.get(field)
                continue
            if column == "diaObject":
                obj = _mapping(value)
                for key, item in obj.items():
                    if any(marker in key.lower() for marker in OBJECT_FIELD_MARKERS) and _is_scalar(item):
                        flat[f"diaObject.{key}"] = item
                    elif not _is_scalar(item):
                        skipped_nested[f"diaObject.{key}"] += 1
                continue
            if column == "clf":
                clf = _mapping(value)
                for key, item in clf.items():
                    if key in CLF_FIELDS and _is_scalar(item):
                        flat[f"clf.{key}"] = item
                    else:
                        skipped_nested[f"clf.{key}"] += 1
                continue
            if column in {"pred", "xm", "misc"}:
                nested = _mapping(value)
                for key, item in nested.items():
                    if _is_scalar(item):
                        flat[f"{column}.{key}"] = item
                    else:
                        skipped_nested[f"{column}.{key}"] += 1
                continue
            if "." in column and _is_scalar(value):
                flat[column] = value
                continue
            if _is_scalar(value):
                flat[column] = value
            else:
                skipped_nested[column] += 1
        rows.append(flat)
    return pd.DataFrame(rows)


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "as_py"):
        item = value.as_py()
        return item if isinstance(item, dict) else {}
    return {}


def _is_scalar(value: Any) -> bool:
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except Exception:  # noqa: BLE001
        pass
    return isinstance(value, (str, int, float, bool))


def _first_present(df: pd.DataFrame, candidates: tuple[str, ...]) -> str | None:
    return next((column for column in candidates if column in df.columns), None)


def _value_counts(series: pd.Series, limit: int = 20) -> dict[str, int]:
    return {str(key): int(value) for key, value in series.dropna().astype(str).value_counts().head(limit).items()}


def _numeric_range(df: pd.DataFrame, column: str | None) -> dict[str, Any]:
    if not column or column not in df.columns:
        return {}
    values = pd.to_numeric(df[column], errors="coerce").dropna()
    return {
        "column": column,
        "non_null": int(df[column].notna().sum()),
        "numeric_count": int(len(values)),
        "min": float(values.min()) if not values.empty else None,
        "max": float(values.max()) if not values.empty else None,
    }


def _json_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in metadata.items() if key != "file_rows"}


def build_sesar_style_summary(df: pd.DataFrame) -> dict[str, Any]:
    """Build conservative variability-analysis hooks without making variable-source claims."""
    object_col = _first_present(df, ("diaSource.diaObjectId", "diaObjectId"))
    band_col = _first_present(df, ("diaSource.band", "band"))
    flux_col = _first_present(df, ("diaSource.psFlux", "psFlux"))
    flux_err_col = _first_present(df, ("diaSource.psFluxErr", "psFluxErr"))
    if df.empty or not object_col:
        return {"status": "insufficient_fields", "reason": "object identifier is unavailable"}
    object_counts = df[object_col].dropna().value_counts()
    summary: dict[str, Any] = {
        "status": "available_hooks",
        "object_column": object_col,
        "objects_with_at_least_2_alerts": int((object_counts >= 2).sum()),
        "max_alerts_per_object": int(object_counts.max()) if not object_counts.empty else 0,
    }
    if band_col:
        summary["per_band_observation_count"] = _value_counts(df[band_col])
    if flux_col:
        grouped = df[[object_col, flux_col]].copy()
        grouped[flux_col] = pd.to_numeric(grouped[flux_col], errors="coerce")
        amplitudes = grouped.groupby(object_col)[flux_col].agg(lambda values: values.max() - values.min()).dropna()
        summary["flux_amplitude_nonzero_objects"] = int((amplitudes > 0).sum())
        summary["flux_amplitude_max"] = float(amplitudes.max()) if not amplitudes.empty else None
    if flux_col and flux_err_col:
        tmp = df[[object_col, flux_col, flux_err_col]].copy()
        tmp[flux_col] = pd.to_numeric(tmp[flux_col], errors="coerce")
        tmp[flux_err_col] = pd.to_numeric(tmp[flux_err_col], errors="coerce")
        tmp = tmp[(tmp[flux_err_col] > 0) & tmp[flux_col].notna()]
        if not tmp.empty:
            chi_like = {}
            for obj, group in tmp.groupby(object_col):
                if len(group) < 2:
                    continue
                mean = group[flux_col].mean()
                chi_like[str(obj)] = float((((group[flux_col] - mean) / group[flux_err_col]) ** 2).sum() / max(len(group) - 1, 1))
            summary["chi_square_like_objects"] = len(chi_like)
            summary["chi_square_like_max"] = max(chi_like.values()) if chi_like else None
    return summary


def _remaining_rows(metadata: dict[str, Any]) -> int | None:
    expected = metadata.get("expected_total")
    readable = metadata.get("readable_rows")
    if expected is None or readable is None:
        return None
    return max(int(expected) - int(readable), 0)


def _write_parquet(df: pd.DataFrame, path: str | Path) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(output, index=False)
    return output

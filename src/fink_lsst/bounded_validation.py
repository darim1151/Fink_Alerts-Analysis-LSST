"""Validation helpers for bounded public REST extraction runs."""

from __future__ import annotations

from typing import Any

import pandas as pd

from .validation import result, validate_normalized_table


def validate_bounded_extraction(
    tables: dict[str, pd.DataFrame],
    extraction_summary: dict[str, Any],
    attempts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Validate bounded extraction tables and coverage metrics."""
    checks: list[dict[str, Any]] = []
    for table_name, frame in tables.items():
        table_type = {"forced_photometry": "forced_photometry", "object_summary": "objects"}.get(table_name, table_name)
        for check in validate_normalized_table(frame, table_type):
            checks.append({"table": table_name, **check})

    tag_rows = int(extraction_summary.get("tag_rows", 0))
    unique_objects = int(extraction_summary.get("unique_object_ids", 0))
    detail_attempts = extraction_summary.get("detail_attempts", {})
    checks.append(
        result(
            "detail_fetch_coverage",
            unique_objects >= 0,
            "Detail fetch coverage recorded",
            severity="info",
            tag_rows=tag_rows,
            unique_object_ids=unique_objects,
            detail_attempts=detail_attempts,
        )
    )

    tag_attempt = next((attempt for attempt in attempts if attempt.get("name") == "bounded_tag_sample"), {})
    truncation = tag_attempt.get("truncation", {})
    checks.append(
        result(
            "truncation_pagination",
            not bool(truncation.get("hit_requested_limit")),
            truncation.get("warning", "No truncation metadata available"),
            severity="warning" if truncation.get("hit_requested_limit") else "info",
            truncation=truncation,
        )
    )
    return checks


def render_validation_summary(checks: list[dict[str, Any]]) -> str:
    """Render bounded validation checks as Markdown."""
    lines = ["# Bounded Extraction Validation Summary", ""]
    for check in checks:
        table = f" `{check['table']}`" if "table" in check else ""
        lines.append(
            f"- `{check['severity']}`{table} `{check['check']}`: "
            f"{'passed' if check['passed'] else 'failed'} - {check['message']}"
        )
    return "\n".join(lines) + "\n"

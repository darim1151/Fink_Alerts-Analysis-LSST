"""Markdown report renderers for Data Transfer readiness."""

from __future__ import annotations

from typing import Any


def render_setup_report(report: dict[str, Any]) -> str:
    lines = ["# Data Transfer Setup Status", "", f"- Status: `{report.get('status')}`", f"- Job submission attempted: `{report.get('job_submission_attempted')}`", f"- Credentials recorded: `{report.get('credentials_recorded')}`", "", "## Checks", ""]
    for check in report.get("checks", []):
        lines.append(f"- `{check.get('name')}`: `{check.get('ok')}`")
    return "\n".join(lines) + "\n"


def render_delivery_inspection_report(report: dict[str, Any]) -> str:
    lines = [
        "# Data Transfer Delivery Inspection",
        "",
        f"- Delivery directory: `{report.get('delivery_dir')}`",
        f"- File count: `{report.get('file_count')}`",
        f"- Total rows: `{report.get('total_rows')}`",
        f"- Total size bytes: `{report.get('total_size_bytes')}`",
        f"- Schema groups: `{report.get('schema_group_count')}`",
        f"- Nested columns: `{', '.join(report.get('nested_columns') or []) or 'none'}`",
        f"- Empty: `{report.get('empty')}`",
        "",
        "## Files",
        "",
    ]
    for item in report.get("files", []):
        schema = item.get("schema", {})
        lines.append(
            f"- `{item.get('file_type')}` `{item.get('path')}` "
            f"({item.get('size_bytes')} bytes, rows=`{schema.get('rows')}`)"
        )
    lines.extend(["", "## Schema Groups", ""])
    for group in report.get("schema_groups", []):
        lines.append(
            f"- `{group.get('schema_hash')}`: files=`{group.get('file_count')}`, "
            f"rows=`{group.get('rows')}`, nested=`{', '.join(group.get('nested_columns') or []) or 'none'}`"
        )
    if not report.get("schema_groups"):
        lines.append("- None.")
    return "\n".join(lines) + "\n"


def render_client_probe_report(report: dict[str, Any]) -> str:
    lines = [
        "# Fink Client Probe",
        "",
        f"- Package installed: `{report.get('package_installed')}`",
        f"- CLI available: `{report.get('cli_available')}`",
        f"- PATH CLI available: `{report.get('path_cli_available')}`",
        f"- Entry-point CLI available: `{report.get('entry_point_cli_available')}`",
        f"- Version known: `{report.get('version_known')}`",
        f"- Authentication known: `{report.get('authentication_known')}`",
        f"- Job submission attempted: `{report.get('job_submission_attempted')}`",
        f"- Credentials recorded: `{report.get('credentials_recorded')}`",
        "",
        "## Probes",
        "",
    ]
    for probe in report.get("probes", []):
        lines.append(f"- `{probe.get('name')}`: `{probe.get('ok')}` - {probe.get('message', probe.get('error', ''))}")
    return "\n".join(lines) + "\n"


def render_ingestion_report(run_id: str, delivery_report: dict[str, Any], artifacts: dict[str, str]) -> str:
    lines = [
        "# Data Transfer Ingestion Report",
        "",
        f"- Run ID: `{run_id}`",
        f"- Delivery files: `{delivery_report.get('file_count')}`",
        f"- Delivery rows: `{delivery_report.get('total_rows')}`",
        f"- Nested raw columns: `{', '.join(delivery_report.get('nested_columns') or []) or 'none'}`",
        f"- Processed artifacts: `{len(artifacts)}`",
        "",
        "## Artifacts",
        "",
    ]
    for name, path in artifacts.items():
        lines.append(f"- `{name}`: `{path}`")
    if not artifacts:
        lines.append("- No delivered files were available to ingest.")
    return "\n".join(lines) + "\n"


def render_validation_summary(checks: list[dict[str, Any]]) -> str:
    lines = ["# Data Transfer Validation Summary", ""]
    for check in checks:
        table = f" `{check['table']}`" if "table" in check else ""
        lines.append(f"- `{check['severity']}`{table} `{check['check']}`: {'passed' if check['passed'] else 'failed'} - {check['message']}")
    return "\n".join(lines) + "\n"


def render_raw_field_inventory(report: dict[str, Any]) -> str:
    """Render a raw field inventory from delivery inspection metadata."""
    lines = [
        "# Raw Field Inventory",
        "",
        f"- Delivery directory: `{report.get('delivery_dir')}`",
        f"- File count: `{report.get('file_count')}`",
        f"- Total rows: `{report.get('total_rows')}`",
        f"- Schema groups: `{report.get('schema_group_count')}`",
        f"- Nested columns: `{', '.join(report.get('nested_columns') or []) or 'none'}`",
        "",
        "## Columns",
        "",
    ]
    for column in report.get("columns", []):
        lines.append(f"- `{column}`")
    lines.extend(["", "## File Schemas", ""])
    for item in report.get("files", []):
        schema = item.get("schema", {})
        lines.append(f"### {item.get('path')}")
        lines.append("")
        lines.append(f"- Rows: `{schema.get('rows')}`")
        lines.append(f"- Schema hash: `{schema.get('schema_hash')}`")
        lines.append(f"- Nested columns: `{', '.join(schema.get('nested_columns') or []) or 'none'}`")
        lines.append("")
    return "\n".join(lines) + "\n"


def render_architecture_decision_report() -> str:
    return """# Hybrid Full-Night Architecture

REST mode:
- bounded/candidate extraction
- known-ID enrichment
- diagnostics
- dashboards

Data Transfer mode:
- complete nightly alert census
- historical bulk extraction
- population-level science after validation

Decision:
Public REST remains part of the system, but Data Transfer is the planned route for complete full-night products once access and delivery are available.
"""

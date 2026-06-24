"""Markdown report renderers for Data Transfer readiness."""

from __future__ import annotations

from typing import Any


def render_setup_report(report: dict[str, Any]) -> str:
    lines = ["# Data Transfer Setup Status", "", f"- Status: `{report.get('status')}`", f"- Job submission attempted: `{report.get('job_submission_attempted')}`", f"- Credentials recorded: `{report.get('credentials_recorded')}`", "", "## Checks", ""]
    for check in report.get("checks", []):
        lines.append(f"- `{check.get('name')}`: `{check.get('ok')}`")
    return "\n".join(lines) + "\n"


def render_delivery_inspection_report(report: dict[str, Any]) -> str:
    lines = ["# Data Transfer Delivery Inspection", "", f"- Delivery directory: `{report.get('delivery_dir')}`", f"- File count: `{report.get('file_count')}`", f"- Empty: `{report.get('empty')}`", ""]
    for item in report.get("files", []):
        lines.append(f"- `{item.get('file_type')}` `{item.get('path')}` ({item.get('size_bytes')} bytes)")
    return "\n".join(lines) + "\n"


def render_client_probe_report(report: dict[str, Any]) -> str:
    lines = [
        "# Fink Client Probe",
        "",
        f"- Package installed: `{report.get('package_installed')}`",
        f"- CLI available: `{report.get('cli_available')}`",
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
    lines = ["# Data Transfer Ingestion Report", "", f"- Run ID: `{run_id}`", f"- Delivery files: `{delivery_report.get('file_count')}`", f"- Processed artifacts: `{len(artifacts)}`", "", "## Artifacts", ""]
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

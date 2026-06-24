"""Local setup diagnostics for Fink Data Transfer readiness."""

from __future__ import annotations

import importlib.metadata
import os
import sys
from pathlib import Path
from typing import Any


SECRET_NAME_MARKERS = ("TOKEN", "SECRET", "PASSWORD", "PASS", "KEY", "CREDENTIAL")
ALLOWLIST_MARKERS = ("PYTHON", "PATH", "KEYCHAIN")


def check_python_version() -> dict[str, Any]:
    """Return Python version diagnostics."""
    version = sys.version_info
    return {"name": "python_version", "ok": version >= (3, 9), "version": sys.version.split()[0]}


def check_fink_client_installed() -> dict[str, Any]:
    """Detect whether fink-client is installed without requiring it."""
    try:
        importlib.metadata.version("fink-client")
        return {"name": "fink_client_installed", "ok": True}
    except importlib.metadata.PackageNotFoundError:
        return {"name": "fink_client_installed", "ok": False, "message": "fink-client is not installed"}


def check_fink_client_version() -> dict[str, Any]:
    """Return fink-client version if installed."""
    try:
        version = importlib.metadata.version("fink-client")
        return {"name": "fink_client_version", "ok": True, "version": version}
    except importlib.metadata.PackageNotFoundError:
        return {"name": "fink_client_version", "ok": False, "version": None}


def check_required_optional_dependencies() -> dict[str, Any]:
    """Check optional file-format dependencies used by delivery inspection."""
    packages = {}
    for package in ("pyarrow", "fastavro", "avro"):
        try:
            packages[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            packages[package] = None
    return {"name": "optional_dependencies", "ok": packages.get("pyarrow") is not None, "packages": packages}


def check_environment_for_secrets_leakage() -> dict[str, Any]:
    """Report likely secret-bearing environment variable names without values."""
    suspicious = []
    for name in os.environ:
        upper = name.upper()
        if any(marker in upper for marker in SECRET_NAME_MARKERS) and not any(marker in upper for marker in ALLOWLIST_MARKERS):
            suspicious.append(name)
    return {
        "name": "environment_secret_names",
        "ok": True,
        "suspicious_variable_names": sorted(suspicious),
        "note": "Values are never recorded.",
    }


def check_local_output_dirs(config: dict[str, Any]) -> dict[str, Any]:
    """Check configured local output directories."""
    local_paths = config.get("local_paths", {}) or {}
    rows = []
    for key, default in (
        ("raw_delivery_dir", "data/raw/data_transfer"),
        ("processed_dir", "data/processed/data_transfer"),
        ("reports_dir", "outputs/data_transfer"),
    ):
        path = Path(local_paths.get(key, default))
        rows.append({"key": key, "path": str(path), "exists": path.exists()})
    return {"name": "local_output_dirs", "ok": True, "directories": rows}


def build_setup_status_report(config: dict[str, Any]) -> dict[str, Any]:
    """Build all setup diagnostics."""
    checks = [
        check_python_version(),
        check_fink_client_installed(),
        check_fink_client_version(),
        check_required_optional_dependencies(),
        check_environment_for_secrets_leakage(),
        check_local_output_dirs(config),
    ]
    return {
        "status": "ready_for_dry_run" if all(check.get("ok") or check["name"].startswith("fink_client") for check in checks) else "needs_attention",
        "checks": checks,
        "job_submission_attempted": False,
        "credentials_recorded": False,
    }

"""Safe fink-client probing without job submission."""

from __future__ import annotations

import importlib
import importlib.metadata
import shutil
import subprocess
from typing import Any


LIKELY_MODULES = ("fink_client", "fink-client", "finkclient")
LIKELY_COMMANDS = ("finkctl", "fink-client", "fink_client", "finkclient", "fink_datatransfer", "fink_client_register")
LIKELY_ENTRY_POINTS = ("finkctl", "fink_datatransfer", "fink_client_register", "fink_consumer")


def probe_fink_client_import(module_names: tuple[str, ...] = LIKELY_MODULES) -> dict[str, Any]:
    """Probe whether fink_client can be imported."""
    attempts = []
    for module_name in module_names:
        try:
            module = importlib.import_module(module_name.replace("-", "_"))
            return {
                "name": "import_fink_client",
                "ok": True,
                "module": getattr(module, "__name__", module_name),
                "attempts": attempts,
            }
        except Exception as exc:  # noqa: BLE001
            attempts.append({"module": module_name, "ok": False, "error": str(exc)})
    return {"name": "import_fink_client", "ok": False, "attempts": attempts}


def probe_fink_client_package_version() -> dict[str, Any]:
    """Probe installed package versions by likely distribution names."""
    attempts = []
    for package_name in ("fink-client", "fink_client", "finkclient"):
        try:
            version = importlib.metadata.version(package_name)
            return {"name": "fink_client_package_version", "ok": True, "package": package_name, "version": version}
        except importlib.metadata.PackageNotFoundError:
            attempts.append({"package": package_name, "ok": False})
    return {"name": "fink_client_package_version", "ok": False, "version": None, "attempts": attempts}


def probe_available_fink_client_commands(command_names: tuple[str, ...] = LIKELY_COMMANDS) -> dict[str, Any]:
    """Probe basic CLI help for likely fink-client command names."""
    command_results = []
    for command in command_names:
        executable = shutil.which(command)
        if not executable:
            command_results.append({"command": command, "available": False})
            continue
        command_results.append(_probe_help(executable))
    available = [item for item in command_results if item.get("available")]
    return {
        "name": "fink_client_commands",
        "ok": bool(available),
        "commands": command_results,
        "safe_commands_discovered": [item["executable"] for item in available],
    }


def probe_fink_client_entry_points(entry_point_names: tuple[str, ...] = LIKELY_ENTRY_POINTS) -> dict[str, Any]:
    """Probe installed console scripts even when they are not exposed on PATH."""
    try:
        entry_points = importlib.metadata.entry_points()
        if hasattr(entry_points, "select"):
            console_scripts = entry_points.select(group="console_scripts")
        else:
            console_scripts = entry_points.get("console_scripts", [])
    except Exception as exc:  # noqa: BLE001
        return {"name": "fink_client_entry_points", "ok": False, "error": str(exc), "entry_points": []}
    discovered = []
    wanted = set(entry_point_names)
    for entry_point in console_scripts:
        if entry_point.name in wanted:
            discovered.append({"name": entry_point.name, "value": entry_point.value})
    return {
        "name": "fink_client_entry_points",
        "ok": bool(discovered),
        "entry_points": discovered,
    }


def _probe_help(executable: str) -> dict[str, Any]:
    try:
        completed = subprocess.run([executable, "--help"], check=False, capture_output=True, text=True, timeout=10)
        return {
            "command": executable,
            "available": True,
            "executable": executable,
            "returncode": completed.returncode,
            "help_preview": (completed.stdout or completed.stderr)[:1000],
        }
    except Exception as exc:  # noqa: BLE001
        return {"command": executable, "available": True, "executable": executable, "error": str(exc)}


def probe_authentication_status_safe() -> dict[str, Any]:
    """Record that auth cannot be safely inferred unless client exposes a safe command."""
    return {
        "name": "authentication_status",
        "ok": None,
        "authenticated": None,
        "message": "Authentication was not probed because no safe no-secret introspection command is assumed.",
        "credentials_recorded": False,
    }


def probe_data_transfer_capabilities_safe() -> dict[str, Any]:
    """Return safe capability probe diagnostics without submission."""
    return {
        "name": "data_transfer_capabilities",
        "ok": None,
        "capabilities": [],
        "message": "No job submission or authenticated capability call was attempted.",
        "job_submission_attempted": False,
    }


def run_safe_client_probes() -> dict[str, Any]:
    """Run all safe fink-client probes."""
    probes = [
        probe_fink_client_import(),
        probe_fink_client_package_version(),
        probe_available_fink_client_commands(),
        probe_fink_client_entry_points(),
        probe_authentication_status_safe(),
        probe_data_transfer_capabilities_safe(),
    ]
    command_available = bool(next((probe.get("ok") for probe in probes if probe["name"] == "fink_client_commands"), False))
    entry_point_available = bool(next((probe.get("ok") for probe in probes if probe["name"] == "fink_client_entry_points"), False))
    return {
        "package_installed": bool(next((probe.get("ok") for probe in probes if probe["name"] == "fink_client_package_version"), False)),
        "cli_available": command_available or entry_point_available,
        "path_cli_available": command_available,
        "entry_point_cli_available": entry_point_available,
        "version_known": bool(next((probe.get("version") for probe in probes if probe["name"] == "fink_client_package_version"), None)),
        "authentication_known": False,
        "probes": probes,
        "job_submission_attempted": False,
        "credentials_recorded": False,
    }

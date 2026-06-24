"""Fink client installation helper reports."""

from __future__ import annotations

import sys
from typing import Any

from .client_probe import probe_fink_client_package_version


def build_install_status() -> dict[str, Any]:
    """Build local fink-client install status without installing anything."""
    version_probe = probe_fink_client_package_version()
    return {
        "python_executable": sys.executable,
        "python_version": sys.version.split()[0],
        "fink_client_installed": bool(version_probe.get("ok")),
        "fink_client_version": version_probe.get("version"),
        "recommended_install_command": f"{sys.executable} -m pip install fink-client",
        "minimum_version": None,
        "note": "No package version is pinned here because no minimum version has been verified in this repository.",
    }


def render_install_guide(status: dict[str, Any]) -> str:
    """Render fink-client install guidance."""
    return f"""# Fink Client Install Guide

- Python executable: `{status['python_executable']}`
- Python version: `{status['python_version']}`
- fink-client installed: `{status['fink_client_installed']}`
- fink-client version: `{status['fink_client_version']}`

Recommended command:

```bash
python -m pip install fink-client
```

Repository-local equivalent using the detected interpreter:

```bash
{status['recommended_install_command']}
```

Notes:
- This helper does not install anything unless you run the command yourself.
- Do not paste credentials, tokens, passwords, or auth outputs into this repository.
- After installation, run `python scripts/probe_fink_client_capabilities.py`.
"""

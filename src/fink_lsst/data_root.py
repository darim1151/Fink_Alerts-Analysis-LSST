"""Resolve the root directory that holds Fink scientific data.

Run manifests store data paths relative to a data root (`data/raw/...`,
`data/processed/...`, `outputs/...`). The data root is chosen by exactly one
rule:

1. If the environment variable `FINK_LSST_DATA_ROOT` is set, it is the data
   root. It must be a non-empty absolute path to an existing directory, must
   not contain `..`, and must not be a subdirectory of the Git checkout.
2. Otherwise the data root is the repository checkout itself, which preserves
   the original local-first layout.

The data root is never created implicitly; a missing directory is an error.
Configs, run manifests, and source code always stay in the Git checkout.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping


DATA_ROOT_ENV = "FINK_LSST_DATA_ROOT"
REPO_ROOT = Path(__file__).resolve().parents[2]


class DataRootError(ValueError):
    """Raised when the configured data root is invalid or ambiguous."""


def resolve_data_root(
    environ: Mapping[str, str] | None = None,
    repo_root: str | Path = REPO_ROOT,
) -> Path:
    """Return the absolute data root according to the documented precedence."""
    env = os.environ if environ is None else environ
    repo = Path(repo_root).resolve()
    if DATA_ROOT_ENV not in env:
        return repo

    value = env[DATA_ROOT_ENV]
    if not value.strip():
        raise DataRootError(f"{DATA_ROOT_ENV} is set but empty; unset it to use the repository checkout")
    candidate = Path(value)
    if not candidate.is_absolute():
        raise DataRootError(f"{DATA_ROOT_ENV} must be an absolute path, got {value!r}")
    if ".." in candidate.parts:
        raise DataRootError(f"{DATA_ROOT_ENV} must not contain '..', got {value!r}")
    if not candidate.is_dir():
        raise DataRootError(f"{DATA_ROOT_ENV} does not exist or is not a directory: {value}")
    resolved = candidate.resolve()
    if repo in resolved.parents:
        raise DataRootError(
            f"{DATA_ROOT_ENV} must not point inside the Git checkout ({repo}); "
            "unset it to use the checkout's own data/ and outputs/"
        )
    return resolved


def is_external_data_root(data_root: str | Path, repo_root: str | Path = REPO_ROOT) -> bool:
    """Return true when data lives outside the Git checkout."""
    return Path(data_root).resolve() != Path(repo_root).resolve()

"""Resolve the root directory that holds Fink scientific data and keep paths inside it.

Run manifests store data paths relative to a data root (`data/raw/...`,
`data/processed/...`, `outputs/...`). The data root is chosen by exactly one
rule:

1. If the environment variable `FINK_LSST_DATA_ROOT` is set, it is the data
   root. It must be a non-empty absolute path to an existing directory, must
   not contain `..`, and must not be the filesystem root, the user's home
   directory, the Git checkout, inside the checkout, or an ancestor of the
   checkout (compared after resolving symlinks).
2. Otherwise the data root is the repository checkout itself, which preserves
   the original local-first layout.

The data root is never created implicitly; a missing directory is an error.
Configs, run manifests, and source code always stay in the Git checkout.

Confinement: storage bases are always built lexically from the *resolved* data
root plus fixed relative parts (for example `<root>/data/raw/data_transfer`).
They are never resolved themselves, so a symlinked or redirected `data/` or
`outputs/` directory makes every path beneath it fail `confine` instead of
silently moving the trusted area. This guards against configuration mistakes,
not against concurrent hostile filesystem changes.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Mapping


DATA_ROOT_ENV = "FINK_LSST_DATA_ROOT"
REPO_ROOT = Path(__file__).resolve().parents[2]
_PATH_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,254}")


class DataRootError(ValueError):
    """Raised when the configured data root is invalid or ambiguous."""


class PathConfinementError(ValueError):
    """Raised when a data path would leave its permitted storage area."""


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
    if resolved == Path(resolved.anchor):
        raise DataRootError(f"{DATA_ROOT_ENV} must not be the filesystem root")
    if resolved == Path.home().resolve():
        raise DataRootError(f"{DATA_ROOT_ENV} must not be the home directory; use a dedicated data directory")
    if resolved == repo or repo in resolved.parents:
        raise DataRootError(
            f"{DATA_ROOT_ENV} must not be or point inside the Git checkout ({repo}); "
            "unset it to use the checkout's own data/ and outputs/"
        )
    if resolved in repo.parents:
        raise DataRootError(f"{DATA_ROOT_ENV} must not contain the Git checkout ({repo})")
    return resolved


def is_external_data_root(data_root: str | Path, repo_root: str | Path = REPO_ROOT) -> bool:
    """Return true when data lives outside the Git checkout."""
    return Path(data_root).resolve() != Path(repo_root).resolve()


def storage_base(data_root: str | Path, relative: str | Path) -> Path:
    """Return the canonical, unresolved storage base `<resolved root>/<relative>`."""
    rel = Path(relative)
    if rel.is_absolute() or ".." in rel.parts:
        raise PathConfinementError(f"storage base must be a relative path without '..': {relative}")
    return Path(data_root).resolve() / rel


def confine(path: str | Path, base: str | Path) -> Path:
    """Resolve `path` (following symlinks; missing leaves allowed) and require it inside `base`.

    `base` must be canonical (see `storage_base`); it is compared as given.
    """
    resolved = Path(path).resolve()
    base = Path(base)
    if resolved != base and base not in resolved.parents:
        raise PathConfinementError(f"{path} resolves to {resolved}, outside {base}")
    return resolved


def confine_tree(directory: str | Path, base: str | Path) -> Path:
    """Confine `directory` to `base`, then require every existing entry beneath it to stay inside it.

    Catches symlinked raw inputs and symlinked existing output targets before
    anything is read from or written into the directory.
    """
    resolved = confine(directory, base)
    if resolved.is_dir():
        for current, dirnames, filenames in os.walk(resolved):
            for name in dirnames + filenames:
                confine(Path(current) / name, resolved)
    return resolved


def validate_path_component(value: object, label: str = "identifier") -> str:
    """Return `value` if it is one ordinary path component usable as a directory name."""
    if not isinstance(value, str) or not _PATH_COMPONENT.fullmatch(value):
        raise PathConfinementError(
            f"{label} must be a single path component of letters, digits, '.', '_' or '-' "
            f"starting with a letter or digit, got {value!r}"
        )
    return value

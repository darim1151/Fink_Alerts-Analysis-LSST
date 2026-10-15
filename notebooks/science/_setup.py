"""Shared configuration for the science notebooks in this folder.

Every notebook starts with ``from _setup import *``. Edit the CONFIG block below (or set the environment
variables named there) before a new run; nothing else needs changing when new alerts arrive.

Safety: notebooks only ever write inside OUTPUT_ROOT / RUN_ID, each step into its own new folder, and refuse to
write anywhere under the protected data, catalog or manifest trees.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for _p in (REPO / "tools" / "colour_plot_kit", REPO / "scripts", REPO / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

# ============================================================================= CONFIG
# Data host layout (Arnor). Change CATALOG when a new analytical catalog is built for a larger cohort.
DATA_ROOT = Path(os.environ.get("FINK_DATA_ROOT", "/astro/store/shire/FINK"))
CATALOG = Path(os.environ.get("FINK_CATALOG",
                              DATA_ROOT / "data/processed/final-five-window-analytics-20261006-v1/analytics.duckdb"))
# Rubin COSMOS stellar locus (median colours vs g-i); used by notebook 04 and the locus check in 05.
LOCUS_FILE = Path(os.environ.get("FINK_STELLAR_LOCUS",
                                 DATA_ROOT / "manifests/stellar-locus-figures-20261013-v1/inputs/RubinStellarLocus_COSMOS.txt"))
# Name of this run. Use a new name for every full run (01 -> 05); existing run folders are never overwritten.
RUN_ID = os.environ.get("FINK_NOTEBOOK_RUN_ID", "notebooks-final-five-window-v1")
OUTPUT_ROOT = Path(os.environ.get("FINK_OUTPUT_ROOT", DATA_ROOT / "outputs"))
TEMP_DIR = os.environ.get("FINK_TEMP_DIR", "/local/tmp")  # DuckDB spill space
# ============================================================================= end of CONFIG

RUN_ROOT = OUTPUT_ROOT / RUN_ID
PROTECTED = (DATA_ROOT / "data", DATA_ROOT / "manifests")


def _inside(path, parent):
    try:
        Path(path).resolve().relative_to(Path(parent).resolve())
        return True
    except ValueError:
        return False


def step_dir(name):
    """Create RUN_ROOT/name with figures/ and tables/ subfolders. Fails if it already exists (never overwrites)."""
    for p in PROTECTED:
        if _inside(RUN_ROOT, p):
            raise RuntimeError(f"refusing to write under protected path {p}")
    out = RUN_ROOT / name
    if out.exists():
        raise FileExistsError(f"{out} already exists: set a new RUN_ID in _setup.py (or FINK_NOTEBOOK_RUN_ID) for a fresh run")
    (out / "figures").mkdir(parents=True)
    (out / "tables").mkdir()
    return out


def require(path, made_by):
    """Stop with a clear message if an input from an earlier notebook is missing."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"{path} not found: run {made_by} first with the same RUN_ID ({RUN_ID})")
    return path


def code_version():
    """Git commit of this code (or the CODE_SHA file written when the code was copied to the data host)."""
    try:
        return subprocess.check_output(["git", "-C", str(REPO), "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        f = REPO / "CODE_SHA"
        return f.read_text().strip() if f.exists() else "unknown"


def show(paths):
    """Display saved figures inline (in VS Code / Jupyter); prints the paths when run outside a notebook."""
    paths = [paths] if isinstance(paths, (str, Path)) else list(paths)
    try:
        from IPython.display import Image, display
        for p in paths:
            display(Image(filename=str(p)))
    except Exception:
        for p in paths:
            print("figure:", p)


try:  # display() exists inside Jupyter; this fallback lets the notebooks also run as plain Python (run_notebook.py)
    from IPython.display import display
except ImportError:
    def display(*objs):
        for o in objs:
            print(o)


def save_json(obj, path):
    Path(path).write_text(json.dumps(obj, indent=2, default=lambda o: o.item() if hasattr(o, "item") else str(o)) + "\n")
    return path


def describe_config():
    print(f"code version : {code_version()}")
    print(f"catalog      : {CATALOG}")
    print(f"locus file   : {LOCUS_FILE}")
    print(f"run folder   : {RUN_ROOT}")


T0 = time.time()

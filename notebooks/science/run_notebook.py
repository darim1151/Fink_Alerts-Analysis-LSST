#!/usr/bin/env python
"""Run science notebooks top to bottom without Jupyter (e.g. in a terminal on the data host).

    python run_notebook.py 01_template_colours.ipynb 02_science_suite.ipynb ...
    python run_notebook.py --all          # 01 -> 06 in order

Each notebook's code cells run in order in a fresh namespace, from this folder, exactly as in VS Code. Figures and
tables are written to the run folder set in _setup.py; nothing is written back into the notebooks.
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ORDER = ["01_template_colours.ipynb", "02_science_suite.ipynb", "03_red_objects_and_red_bands.ipynb",
         "04_stellar_locus.ipynb", "05_checks_and_audits.ipynb", "06_paper_additions.ipynb"]


def run(path):
    cells = [c for c in json.loads(Path(path).read_text())["cells"] if c["cell_type"] == "code"]
    ns = {"__name__": "__main__"}
    t0 = time.time()
    print(f"=== {Path(path).name}: {len(cells)} code cells", flush=True)
    for i, c in enumerate(cells, 1):
        src = "".join(c["source"])
        try:
            exec(compile(src, f"{Path(path).name}[cell {i}]", "exec"), ns)
        except Exception:
            traceback.print_exc()
            print(f"FAILED in {Path(path).name}, code cell {i}", flush=True)
            return False
        print(f"    cell {i}/{len(cells)} done ({time.time() - t0:.0f}s)", flush=True)
    print(f"=== {Path(path).name} finished in {time.time() - t0:.0f}s", flush=True)
    return True


def main(argv):
    names = ORDER if argv == ["--all"] else argv
    if not names:
        print(__doc__)
        return 2
    os.chdir(HERE)
    sys.path.insert(0, str(HERE))
    import matplotlib
    matplotlib.use("Agg")
    for n in names:
        if not run(HERE / n):
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

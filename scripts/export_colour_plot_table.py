#!/usr/bin/env python
"""Export the per-object template-photometry table used by tools/colour_plot_kit (runs on the data host).

One row per DIA object with usable template magnitudes in at least two bands, taken from the audited tables of
runs alert-colour-diagrams-20261011-v3 (photometry, Fink labels, SIMBAD group, programme) and
alert-science-suite-20261008-v1 (Gaia parallax, Legacy DR8 pstar). No alert-level data is exported.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

BANDS = ("u", "g", "r", "i", "z", "y")
CATS_SHORT = {11: "SN-like", 12: "Fast", 13: "Long", 21: "Periodic", 22: "non-Periodic (AGN)", -1: "first alert, not classified"}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--template-photometry", required=True)
    p.add_argument("--science-table", required=True)
    p.add_argument("--out", required=True, help="new run-scoped output directory (must not exist)")
    p.add_argument("--code-sha", required=True)
    a = p.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=False)

    v = pd.read_parquet(a.template_photometry)
    sci = pd.read_parquet(a.science_table, columns=["dia_object_id", "pstar", "plx", "eplx"])
    assert v.dia_object_id.is_unique and sci.dia_object_id.is_unique and len(v) == len(sci)
    v = v.merge(sci, on="dia_object_id", how="left", validate="one_to_one")
    n_ok = v[[f"ok_{b}" for b in BANDS]].sum(axis=1)
    v = v[n_ok >= 2].copy()

    t = pd.DataFrame({"dia_object_id": v.dia_object_id, "ra_deg": v.ra_deg, "dec_deg": v.dec_deg, "field": v.field})
    for b in BANDS:
        t[f"n_alerts_{b}"] = v[f"n_src_{b}"].fillna(0).astype(int)
    for b in BANDS:
        # Template PSF magnitude (AB); NaN where the band is not usable (no alert, flux <= 0 or median S/N < 5).
        t[f"mag_{b}"] = np.where(v[f"ok_{b}"], v[f"mag_{b}"], np.nan)
        t[f"magerr_{b}"] = np.where(v[f"ok_{b}"], v[f"magerr_{b}"], np.nan)
    t["fink_cats_code"] = v.cats_class_latest.astype("Int64")
    t["fink_cats_label"] = v.cats_class_latest.map(CATS_SHORT)
    t["simbad_otype"] = v.simbad_otype_valid
    t["simbad_group"] = v.simbad_group
    t["legacy_pstar"] = v.pstar
    t["gaia_parallax_mas"] = v.plx
    t["gaia_parallax_err_mas"] = v.eplx
    assert t.dia_object_id.is_unique and t.fink_cats_label.notna().all() and t.field.notna().all()

    path = out / "fink_dia_template_colours.csv.gz"
    t.to_csv(path, index=False, float_format="%.6f")
    back = pd.read_csv(path)
    for b in BANDS:
        d = np.nanmax(np.abs(back[f"mag_{b}"].to_numpy() - t[f"mag_{b}"].to_numpy()))
        assert d < 1e-5, (b, d)
    summary = {"run": out.name, "code_sha": a.code_sha, "script_sha256": sha256(Path(__file__)),
               "template_photometry_sha256": sha256(a.template_photometry), "science_table_sha256": sha256(a.science_table),
               "rows": int(len(t)), "file": path.name, "file_sha256": sha256(path),
               "usable_band_counts": {b: int(t[f"mag_{b}"].notna().sum()) for b in BANDS},
               "panel_counts": {"ugr": int(t[["mag_u", "mag_g", "mag_r"]].notna().all(axis=1).sum()),
                                "gri": int(t[["mag_g", "mag_r", "mag_i"]].notna().all(axis=1).sum()),
                                "riz": int(t[["mag_r", "mag_i", "mag_z"]].notna().all(axis=1).sum()),
                                "izy": int(t[["mag_i", "mag_z", "mag_y"]].notna().all(axis=1).sum())}}
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()

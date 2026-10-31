#!/usr/bin/env python
"""Pick a small, reproducible set of DIA objects for the paper's image figures (alert stamps and gri template mosaics).

Input is the per-object table written by scripts/export_colour_plot_table.py (fink_dia_template_colours.csv[.gz]).
Each object goes to the first of six groups it qualifies for; the groups follow the paper's colour figures:

  1  Gaia DR3 parallax S/N > 5 and (g-i)_tmpl <= 2          4  SIMBAD galaxy (Fink crossmatch within 1")
  2  (g-i)_tmpl > 2                                          5  (u-g)_tmpl < 0.6 without a Gaia parallax S/N > 5
  3  SIMBAD QSO / AGN (Fink crossmatch within 1")            6  CATS SN-like, no SIMBAD object within 1", Legacy pstar < 0.5

Groups 1-4 and 6 need usable g, r and i templates; group 5 needs u, g and r (all u-band alerts are in COSMOS). Every
group keeps (r)_tmpl < --max-r and leaves out the Galactic bulge and plane fields. Within a group, objects are ranked by
sha256("<seed>:<diaObjectId>") and the lowest --per-group are taken, so the choice does not depend on library versions.
Colours are template PSF colours in the native LSST system (no extinction correction, no transformation to SDSS).
Outputs go to a new run directory that must not exist.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

DUSTY_FIELDS = ("Galactic bulge", "Galactic plane")
NO_SIMBAD = "No SIMBAD object within 1″"
SN_LIKE = 11
OUTPUT_COLUMNS = ["name", "group", "dia_object_id", "ra_deg", "dec_deg", "field", "r_tmpl", "g_r_tmpl", "r_i_tmpl",
                  "g_i_tmpl", "u_g_tmpl", "fink_cats_label", "simbad_otype", "simbad_group", "gaia_parallax_snr",
                  "legacy_pstar"]


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def add_colours(t):
    """Template colours and Gaia parallax S/N; NaN where a band is not usable."""
    t = t.copy()
    t["r_tmpl"] = t.mag_r
    t["g_r_tmpl"] = t.mag_g - t.mag_r
    t["r_i_tmpl"] = t.mag_r - t.mag_i
    t["g_i_tmpl"] = t.mag_g - t.mag_i
    t["u_g_tmpl"] = t.mag_u - t.mag_g
    t["gaia_parallax_snr"] = t.gaia_parallax_mas / t.gaia_parallax_err_mas
    return t


def group_masks(t, max_r):
    """Boolean selection per group, in output order (before removing objects already taken by an earlier group)."""
    gri = t[["mag_g", "mag_r", "mag_i"]].notna().all(axis=1)
    ugr = t[["mag_u", "mag_g", "mag_r"]].notna().all(axis=1)
    star = t.gaia_parallax_snr > 5
    base = ~t.field.isin(DUSTY_FIELDS) & (t.mag_r < max_r)
    return {
        "Gaia parallax star, g - i <= 2": base & gri & star & (t.g_i_tmpl <= 2),
        "Red, g - i > 2": base & gri & (t.g_i_tmpl > 2),
        "SIMBAD QSO / AGN": base & gri & (t.simbad_group == "QSO / AGN"),
        "SIMBAD galaxy": base & gri & (t.simbad_group == "Galaxy"),
        "Blue u - g < 0.6, no Gaia parallax": base & ugr & ~star & (t.u_g_tmpl < 0.6),
        "SN-like, no SIMBAD match, Legacy pstar < 0.5": base & gri & (t.simbad_group == NO_SIMBAD)
                                                        & (t.fink_cats_code == SN_LIKE) & (t.legacy_pstar < 0.5),
    }


def rank_key(seed, dia_object_id):
    return hashlib.sha256(f"{seed}:{dia_object_id}".encode()).hexdigest()


def select(table, per_group=5, seed=20261030, max_r=21.0):
    """Return (selected rows with OUTPUT_COLUMNS, pool size per group)."""
    t = add_colours(table)
    t["rank_key"] = [rank_key(seed, i) for i in t.dia_object_id]
    taken = pd.Series(False, index=t.index)
    picks, pools = [], {}
    for k, (name, mask) in enumerate(group_masks(t, max_r).items(), start=1):
        pool = t[mask & ~taken]
        pools[name] = int(len(pool))
        pick = pool.sort_values("rank_key").head(per_group).copy()
        taken.loc[pick.index] = True
        pick["name"] = [f"G{k}-{j:02d}" for j in range(1, len(pick) + 1)]
        pick["group"] = name
        picks.append(pick)
    sel = pd.concat(picks, ignore_index=True) if picks else pd.DataFrame(columns=OUTPUT_COLUMNS)
    return sel[OUTPUT_COLUMNS], pools


def write_outputs(sel, out):
    """image_targets.csv (all columns) and image_targets_radec.csv (name, ra_deg, dec_deg only)."""
    rounded = sel.copy()
    rounded["ra_deg"] = rounded.ra_deg.round(6)
    rounded["dec_deg"] = rounded.dec_deg.round(6)
    for c in ("r_tmpl", "g_r_tmpl", "r_i_tmpl", "g_i_tmpl", "u_g_tmpl", "gaia_parallax_snr", "legacy_pstar"):
        rounded[c] = rounded[c].round(3)
    rounded.to_csv(out / "image_targets.csv", index=False)
    rounded[["name", "ra_deg", "dec_deg"]].to_csv(out / "image_targets_radec.csv", index=False)
    return out / "image_targets.csv", out / "image_targets_radec.csv"


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--data", required=True, help="fink_dia_template_colours.csv[.gz]")
    p.add_argument("--out", required=True, help="new run-scoped output directory (must not exist)")
    p.add_argument("--code-sha", required=True)
    p.add_argument("--per-group", type=int, default=5)
    p.add_argument("--seed", type=int, default=20261030)
    p.add_argument("--max-r", type=float, default=21.0, help="keep objects with template r brighter than this")
    a = p.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=False)

    table = pd.read_csv(a.data, dtype={"dia_object_id": str})
    assert table.dia_object_id.is_unique and table.dia_object_id.str.fullmatch(r"\d+").all()
    sel, pools = select(table, a.per_group, a.seed, a.max_r)
    full, radec = write_outputs(sel, out)
    summary = {"run": out.name, "code_sha": a.code_sha, "script_sha256": sha256(Path(__file__)),
               "input": Path(a.data).name, "input_sha256": sha256(a.data), "input_rows": int(len(table)),
               "seed": a.seed, "per_group": a.per_group, "max_r_tmpl": a.max_r, "pool_sizes": pools,
               "selected": int(len(sel)), "files": {full.name: sha256(full), radec.name: sha256(radec)}}
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()

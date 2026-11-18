"""Driver for the full EDP2 crossmatch on the RSP (lsst kernel, Large container). Mirrors the run of 2026-11-16.

    python edp2_crossmatch_rsp_run.py        # from ~/edp2_xmatch/scripts on the RSP

Real positions and 30-arcsec-offset control positions go through ONE crossmatch (control ids are the negated
dia_object_id), so the EDP2 partitions are read once. Outputs go to ~/edp2_xmatch/out with a summary JSON.
"""
import hashlib
import json
import os
import time

import numpy as np
import pandas as pd

import edp2_crossmatch_rsp as ex


def main():
    import dask
    import lsdb
    from dask.distributed import Client
    dask.config.set({"logging.distributed": "critical"})
    client = Client(n_workers=4, threads_per_worker=1, memory_limit="auto",
                    local_directory=f"/deleted-sundays/{os.environ.get('USER', 'x')}")
    out = os.path.join(ex.HOME, "out")
    os.makedirs(out, exist_ok=False)
    t0 = time.time()
    full = ex.load_input()
    ra_o, dec_o = ex.offset_positions(full.ra.to_numpy(), full.dec.to_numpy(), ex.OFFSET_ARCSEC, ex.SEED)
    if np.max(np.abs(ex.angsep_arcsec(full.ra.to_numpy(), full.dec.to_numpy(), ra_o, dec_o) - ex.OFFSET_ARCSEC)) > 1e-6:
        raise ValueError("offset positions are not exactly OFFSET_ARCSEC away")
    ctrl = pd.DataFrame({"dia_object_id": -full.dia_object_id.to_numpy(), "ra": ra_o, "dec": dec_o,
                         "field": full.field.to_numpy()})
    both = pd.concat([full, ctrl], ignore_index=True)
    if not both.dia_object_id.is_unique or len(both) != 2 * ex.N_EXPECTED:
        raise ValueError("real + control ids must be unique")
    # Gather partition by partition: a single concatenated result (one worker holding ~7 million rows) ran the
    # Large container out of memory on the first attempt. Control rows keep only the columns the chance rate needs.
    keep_ctrl = ["dia_object_id", "ra", "dec", "objectId", "coord_ra", "coord_dec", "_dist_arcsec"]

    @dask.delayed
    def split_part(df):
        df = pd.DataFrame(df).reset_index(drop=True)
        return df[df.dia_object_id > 0], df.loc[df.dia_object_id < 0, keep_ctrl]

    parts = ex.lazy_crossmatch(both, "real_and_control").to_dask_dataframe().to_delayed()
    futs = client.compute([split_part(p) for p in parts])
    reals, ctls = [], []
    for i in range(0, len(futs), 250):
        for r_, c_ in client.gather(futs[i:i + 250]):
            reals.append(r_)
            ctls.append(c_)
        for f in futs[i:i + 250]:
            f.release()
    t_match = time.time() - t0
    real = ex.finish_crossmatch(full, pd.concat(reals, ignore_index=True))
    ctl = pd.concat(ctls, ignore_index=True).assign(dia_object_id=lambda d: -d.dia_object_id)
    ctl = ex.finish_crossmatch(ctrl.assign(dia_object_id=-ctrl.dia_object_id), ctl)
    ctrl_pos = ctrl.assign(dia_object_id=-ctrl.dia_object_id)
    one = ex.summarise(full, real)
    ctrl_one = ex.summarise(ctrl_pos, ctl)
    ins, n_patch = ex.footprint(full)
    one["inside_edp2"] = pd.Series(ins, index=full.dia_object_id).reindex(one.dia_object_id).to_numpy()
    ins_c, _ = ex.footprint(ctrl_pos)
    ctrl_one["inside_edp2"] = pd.Series(ins_c, index=ctrl_pos.dia_object_id).reindex(ctrl_one.dia_object_id).to_numpy()
    rates = ex.rates(one, ctrl_one)
    n_spot, bad = ex.spot_check(one, 20)
    if bad:
        raise ValueError(f"TAP spot check disagrees: {bad}")
    files = {"edp2_match_one_per_object.parquet": one,
             "edp2_neighbours_within_2arcsec.parquet": real,
             "edp2_offset_control_one_per_object.parquet":
                 ctrl_one[["dia_object_id", "field", "ra", "dec", "n_within_0p5", "n_within_1p0", "n_within_2p0",
                           "sep_arcsec", "matched_1arcsec", "inside_edp2"]]}
    shas = {}
    for name, df in files.items():
        p = os.path.join(out, name)
        df.to_parquet(p, index=False)
        shas[name] = hashlib.sha256(open(p, "rb").read()).hexdigest()
    cls = one.r_extendedness.astype(float).where(one.matched_1arcsec & ~one.r_extendedness_flag.astype("boolean").fillna(True).astype(bool))
    summary = {"input_sha256": ex.EXPECTED_SHA256, "n_objects": int(len(one)), "radius_arcsec": ex.R_MAIN,
               "search_radius_arcsec": ex.R_MAX, "offset_control_arcsec": ex.OFFSET_ARCSEC, "seed": ex.SEED,
               "edp2_catalog": ex.EDP2_PATH, "lsdb_version": lsdb.__version__, "n_coadd_patches": n_patch,
               "match_seconds": round(t_match), "inside_edp2": int(one.inside_edp2.sum()),
               "matched": {str(r): int((one[f"n_within_{str(r).replace('.', 'p')}"] > 0).sum()) for r in ex.RADII},
               "chance": {str(r): float((ctrl_one[f"n_within_{str(r).replace('.', 'p')}"] > 0).mean()) for r in ex.RADII},
               "r_extendedness_1arcsec": {"unresolved": int((cls == 0).sum()), "resolved": int((cls == 1).sum()),
                                          "matched_unusable": int((one.matched_1arcsec & cls.isna()).sum())},
               "spot_check": {"n": n_spot, "disagreements": bad}, "per_field": rates.to_dict(orient="records"),
               "outputs_sha256": shas}
    with open(os.path.join(out, "edp2_match_summary.json"), "w") as f:
        json.dump(summary, f, indent=2, default=str)
    print(json.dumps(summary, indent=2, default=str))
    client.close()


if __name__ == "__main__":
    main()

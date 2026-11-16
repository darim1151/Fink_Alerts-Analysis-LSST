"""Positional crossmatch of the Fink DIAObjects with the Rubin EDP2 Object catalogue.

Runs INSIDE the Rubin Science Platform notebook aspect (lsst kernel), where the EDP2 HATS catalogue lives at
/rubin/lsdb_data/object_collection. Nothing here writes outside ~/edp2_xmatch on the RSP.

Steps (each followed by checks that raise on failure):
  1. load_input        the frozen input (dia_object_id, ra, dec, field), verified by sha256 and row counts
  2. crossmatch        LSDB KD-tree crossmatch within R_MAX arcsec, up to K_NEIGH neighbours, how='left'
  3. footprint         each DIAObject inside / outside the EDP2 coadd patches (dp2.CoaddPatches polygons)
  4. offset control    the same crossmatch after moving every position OFFSET_ARCSEC in a random direction
  5. summarise         one row per DIAObject (nearest within 0.5/1/2 arcsec), per-field counts, chance-match rates
  6. spot_check        random matches re-queried with independent TAP cone searches

Geometry helpers (offset_positions, inside_patches) are pure numpy and have offline tests.
"""
from __future__ import annotations

import hashlib
import json
import os
import time

import numpy as np
import pandas as pd

HOME = os.path.expanduser("~/edp2_xmatch")
INPUT = os.path.join(HOME, "fink_dia_objects_edp2_input.parquet")
EXPECTED_SHA256 = "967553525ff92885fb7488cd921204fc948beceacd3539fdf77ebfd2cb0753ed"
N_EXPECTED = 3_487_175
FIELD_COUNTS = {"Low-dust WFD": 1_792_680, "Galactic bulge": 980_861, "Galactic plane": 498_224,
                "Deep drilling fields": 166_862, "M49 field": 30_068, "North ecliptic spur": 10_613,
                "Engineering": 5_487, "Target of opportunity": 2_380}
EDP2_PATH = "/rubin/lsdb_data/object_collection"
R_MAX = 2.0           # arcsec: search radius (the right-hand margin of the EDP2 catalogue is 5 arcsec)
RADII = (0.5, 1.0, 2.0)
R_MAIN = 1.0          # arcsec: the match used in the paper
K_NEIGH = 5
OFFSET_ARCSEC = 30.0
SEED = 20261116

EDP2_COLUMNS = (["objectId", "coord_ra", "coord_dec", "tract", "patch", "refBand", "refExtendedness",
                 "refSizeExtendedness", "griz_model_extendedness", "ebv", "detect_isIsolated", "detect_fromBlend",
                 "deblend_blendNChild", "parentObjectId", "shape_flag", "footprintArea",
                 "r_cModelFlux", "r_cModelFluxErr", "r_cModel_flag", "r_sizeExtendedness", "r_sizeExtendedness_flag",
                 "r_blendedness", "r_pixelFlags_saturatedCenter", "r_pixelFlags_edge", "r_inputCount"]
                + [f"{b}_{c}" for b in "ugriz" for c in ("psfFlux", "psfFluxErr", "psfFlux_flag", "extendedness",
                                                           "extendedness_flag")])


def sha256(path, chunk=1 << 22):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


# ----------------------------------------------------------------------------- geometry (pure numpy, tested offline)
def _unit(ra_deg, dec_deg):
    ra, dec = np.radians(ra_deg), np.radians(dec_deg)
    return np.stack([np.cos(dec) * np.cos(ra), np.cos(dec) * np.sin(ra), np.sin(dec)], axis=-1)


def angsep_arcsec(ra1, dec1, ra2, dec2):
    """Great-circle separation (Vincenty form, stable at small angles)."""
    ra1, dec1, ra2, dec2 = map(np.radians, (ra1, dec1, ra2, dec2))
    dra = ra2 - ra1
    num = np.hypot(np.cos(dec2) * np.sin(dra), np.cos(dec1) * np.sin(dec2) - np.sin(dec1) * np.cos(dec2) * np.cos(dra))
    den = np.sin(dec1) * np.sin(dec2) + np.cos(dec1) * np.cos(dec2) * np.cos(dra)
    return np.degrees(np.arctan2(num, den)) * 3600.0


def offset_positions(ra_deg, dec_deg, arcsec, seed):
    """Move every position by exactly `arcsec` along a great circle, in a random position angle (fixed seed)."""
    rng = np.random.default_rng(seed)
    pa = rng.uniform(0.0, 2.0 * np.pi, len(ra_deg))
    d = np.radians(arcsec / 3600.0)
    ra1, dec1 = np.radians(ra_deg), np.radians(dec_deg)
    dec2 = np.arcsin(np.sin(dec1) * np.cos(d) + np.cos(dec1) * np.sin(d) * np.cos(pa))
    ra2 = ra1 + np.arctan2(np.sin(pa) * np.sin(d) * np.cos(dec1), np.cos(d) - np.sin(dec1) * np.sin(dec2))
    return np.degrees(ra2) % 360.0, np.degrees(dec2)


def parse_polygons(s_region):
    """'POLYGON ICRS ra1 dec1 ra2 dec2 ...' -> array (n, k, 2) in degrees; all polygons must have the same k."""
    rows = [np.asarray(s.split()[2:], dtype=float).reshape(-1, 2) for s in s_region]
    k = {len(r) for r in rows}
    if len(k) != 1:
        raise ValueError(f"polygons with different vertex counts: {sorted(k)}")
    return np.stack(rows)


def _gnomonic(p, c):
    """Gnomonic projection of unit vectors p (..., 3) about unit centre c (..., 3); great circles -> straight lines."""
    z = np.array([0.0, 0.0, 1.0])
    east = np.cross(z, c)
    east /= np.linalg.norm(east, axis=-1, keepdims=True)
    north = np.cross(c, east)
    w = np.sum(p * c, axis=-1)
    return np.sum(p * east, axis=-1) / w, np.sum(p * north, axis=-1) / w, w


def inside_patches(ra_deg, dec_deg, poly_deg, centre_ra, centre_dec, k_nearest=6):
    """True where a point lies inside at least one convex sky polygon (edges = great circles).

    Candidate polygons are the k nearest by centre (KD-tree on unit vectors); each test is exact in the gnomonic
    projection about that polygon's centre, where the polygon's edges are straight lines."""
    from scipy.spatial import cKDTree
    pts = _unit(ra_deg, dec_deg)
    cen = _unit(centre_ra, centre_dec)
    verts = _unit(poly_deg[..., 0], poly_deg[..., 1])                      # (m, k, 3)
    k = min(k_nearest, len(cen))
    _, idx = cKDTree(cen).query(pts, k=k)
    idx = idx.reshape(len(pts), k)
    inside = np.zeros(len(pts), dtype=bool)
    for j in range(k):
        c = cen[idx[:, j]]                                                  # (n, 3)
        v = verts[idx[:, j]]                                                # (n, kv, 3)
        px, py, pw = _gnomonic(pts, c)
        vx, vy, vw = _gnomonic(v, c[:, None, :])
        ex, ey = np.roll(vx, -1, axis=1) - vx, np.roll(vy, -1, axis=1) - vy
        cross = ex * (py[:, None] - vy) - ey * (px[:, None] - vx)
        same_side = np.all(cross >= 0, axis=1) | np.all(cross <= 0, axis=1)
        inside |= same_side & (pw > 0) & np.all(vw > 0, axis=1)
    return inside


# ----------------------------------------------------------------------------- RSP steps
def load_input(sample=None):
    got = sha256(INPUT)
    if got != EXPECTED_SHA256:
        raise ValueError(f"input sha256 {got} != expected {EXPECTED_SHA256}")
    df = pd.read_parquet(INPUT)
    if len(df) != N_EXPECTED or not df.dia_object_id.is_unique:
        raise ValueError("input row count or id uniqueness check failed")
    if df.field.value_counts().to_dict() != FIELD_COUNTS:
        raise ValueError("input per-field counts differ from the paper's Table 2")
    if not (df.ra.between(0, 360, inclusive="left").all() and df.dec.between(-90, 90).all()):
        raise ValueError("coordinates out of range")
    if sample:
        df = df.sample(sample, random_state=SEED).reset_index(drop=True)
    return df


def lazy_crossmatch(df, label):
    """LSDB crossmatch, how='left': every input row is kept; unmatched rows carry NaN separations (not computed)."""
    import lsdb
    left = lsdb.from_dataframe(df[["dia_object_id", "ra", "dec"]].copy(), ra_column="ra", dec_column="dec",
                               catalog_name=f"fink_{label}", margin_threshold=None)
    right = lsdb.open_catalog(EDP2_PATH, columns=EDP2_COLUMNS)
    return left.crossmatch(right, n_neighbors=K_NEIGH, radius_arcsec=R_MAX, how="left", require_right_margin=True,
                           suffixes=("", "_edp2"), suffix_method="overlapping_columns")


def crossmatch(df, label):
    t0 = time.time()
    out = finish_crossmatch(df, lazy_crossmatch(df, label).compute())
    return out, time.time() - t0


def finish_crossmatch(df, out):
    """Checks on a computed crossmatch: one distance column, separations recomputed, every input id present."""
    out = pd.DataFrame(out).reset_index(drop=True)
    sep_col = [c for c in out.columns if c.endswith("dist_arcsec")]
    if len(sep_col) != 1:
        raise ValueError(f"expected one distance column, got {sep_col}")
    out = out.rename(columns={sep_col[0]: "sep_arcsec"})
    # independent separation from the returned coordinates must agree with LSDB's
    m = out.objectId.notna()
    mine = angsep_arcsec(out.ra[m].to_numpy(), out.dec[m].to_numpy(), out.coord_ra[m].to_numpy(), out.coord_dec[m].to_numpy())
    if m.any() and np.max(np.abs(mine - out.sep_arcsec[m].to_numpy())) > 1e-3:
        raise ValueError("LSDB separations disagree with recomputed ones by > 1 mas")
    if (out.sep_arcsec[m] > R_MAX + 1e-6).any():
        raise ValueError("a separation exceeds the search radius")
    if set(out.dia_object_id) != set(df.dia_object_id):
        raise ValueError("crossmatch output does not cover every input id exactly")
    return out


def summarise(df, xm):
    """One row per DIAObject: nearest EDP2 object within each radius and how many lie within it."""
    xm = xm.sort_values(["dia_object_id", "sep_arcsec"], na_position="last")
    has = xm.objectId.notna()
    counts = {r: xm[has & (xm.sep_arcsec <= r)].groupby("dia_object_id").size() for r in RADII}
    nearest = xm[has].drop_duplicates("dia_object_id", keep="first").set_index("dia_object_id")
    one = df.set_index("dia_object_id")[["ra", "dec", "field"]].copy()
    for r in RADII:
        one[f"n_within_{str(r).replace('.', 'p')}"] = counts[r].reindex(one.index).fillna(0).astype(int)
    one = one.join(nearest.drop(columns=["ra", "dec"]), how="left")
    # plain bool: with pyarrow-backed floats a NaN separation would otherwise give <NA>, which groupby means skip
    one["matched_1arcsec"] = (one.sep_arcsec.astype(float) <= R_MAIN).to_numpy(dtype=bool)
    if len(one) != len(df) or not one.index.is_unique:
        raise ValueError("summary is not one row per input id")
    return one.reset_index()


def footprint(df):
    from lsst.rsp import get_tap_service
    tap = get_tap_service("tap")
    pt = tap.search("SELECT lsst_tract, lsst_patch, s_ra, s_dec, s_region FROM dp2.CoaddPatches").to_table().to_pandas()
    poly = parse_polygons(pt.s_region.astype(str))
    ins = inside_patches(df.ra.to_numpy(), df.dec.to_numpy(), poly, pt.s_ra.to_numpy(), pt.s_dec.to_numpy())
    # sanity: every patch centre lies inside the union of patches
    if not inside_patches(pt.s_ra.to_numpy()[:2000], pt.s_dec.to_numpy()[:2000], poly, pt.s_ra.to_numpy(), pt.s_dec.to_numpy()).all():
        raise ValueError("patch centres not inside their own polygons")
    return ins, len(pt)


def rates(one, ctrl_one):
    rows = []
    for f, g in one.groupby("field"):
        c = ctrl_one[ctrl_one.field == f]
        row = {"field": f, "n": len(g), "inside_edp2": int(g.inside_edp2.sum())}
        for r in RADII:
            k = f"n_within_{str(r).replace('.', 'p')}"
            row[f"matched_{r}"] = int((g[k] > 0).sum())
            row[f"chance_{r}"] = float((c[k] > 0).mean())
        rows.append(row)
    return pd.DataFrame(rows)


def spot_check(one, n=20):
    from lsst.rsp import get_tap_service
    tap = get_tap_service("tap")
    s = one[one.matched_1arcsec].sample(n, random_state=SEED)
    bad = []
    for r in s.itertuples():
        q = (f"SELECT objectId, DISTANCE(POINT('ICRS', coord_ra, coord_dec), POINT('ICRS', {r.ra!r}, {r.dec!r})) * 3600 AS sep "
             f"FROM dp2.Object WHERE CONTAINS(POINT('ICRS', coord_ra, coord_dec), CIRCLE('ICRS', {r.ra!r}, {r.dec!r}, {R_MAIN / 3600!r})) = 1")
        t = tap.search(q).to_table().to_pandas().sort_values("sep")
        if t.empty or int(t.objectId.iloc[0]) != int(r.objectId) or abs(float(t.sep.iloc[0]) - r.sep_arcsec) > 1e-3 \
                or len(t) != r.n_within_1p0:
            bad.append({"dia_object_id": int(r.dia_object_id), "lsdb": int(r.objectId), "tap": t.objectId.tolist()})
    return len(s), bad

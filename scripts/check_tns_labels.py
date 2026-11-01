#!/usr/bin/env python
"""Check the TNS supernova labels of the per-object science table against Fink's public API.

For every DIA object whose tns_type (science table) starts with "SN" or "SLSN", the object's alerts are fetched from
POST https://api.lsst.fink-portal.org/api/v1/sources (columns diaSourceId, midpointMjdTai, f:xm_tns_fullname,
f:xm_tns_type, f:fink_broker_version) and the label counts as confirmed if at least one alert inside the cohort window
carries a TNS name. Objects are also grouped: two labelled objects with the same TNS type within --group-arcmin belong
to one group (friends of friends), since one real transient should normally give one DIA object. Optionally the Gaia
category from scripts/analyse_gaia_counterparts.py (counterparts.parquet) is added.

Writes tns_labels.csv and summary.json to a new directory. Read-only; network only to Fink's public API.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

SOURCES_URL = "https://api.lsst.fink-portal.org/api/v1/sources"
COLUMNS = "r:diaSourceId,r:midpointMjdTai,f:xm_tns_fullname,f:xm_tns_type,f:fink_broker_version"
WINDOW_MJD_TAI = (61096.0, 61235.0)      # catalog's UTC-midnight boundaries of [2026-02-25, 2026-07-14) in TAI MJD


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fetch_sources(oid, tries=5, timeout=120):
    body = json.dumps({"diaObjectId": str(oid), "columns": COLUMNS, "output-format": "json"}).encode()
    for attempt in range(1, tries + 1):
        try:
            req = urllib.request.Request(SOURCES_URL, data=body, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except Exception:
            if attempt == tries:
                raise
            time.sleep(3 * attempt)


def tns_names(records, window=WINDOW_MJD_TAI):
    """Distinct TNS names on alerts inside the window (empty strings, None and 'nan' are not names)."""
    names = {str(r.get("f:xm_tns_fullname")) for r in records if window[0] <= float(r["r:midpointMjdTai"]) < window[1]}
    return sorted(n for n in names if n not in ("None", "nan", ""))


def groups(ra, dec, labels, radius_arcmin):
    """Friends-of-friends groups of objects with the same label within radius_arcmin."""
    ra, dec = np.radians(np.asarray(ra, float)), np.radians(np.asarray(dec, float))
    cosd = np.sin(dec[:, None]) * np.sin(dec[None, :]) + np.cos(dec[:, None]) * np.cos(dec[None, :]) * np.cos(
        ra[:, None] - ra[None, :])
    sep = np.degrees(np.arccos(np.clip(cosd, -1, 1))) * 60.0
    labels = np.asarray(labels)
    near = (sep <= radius_arcmin) & (labels[:, None] == labels[None, :]) & ~np.eye(len(ra), dtype=bool)
    gid = -np.ones(len(ra), dtype=int)
    for i in range(len(ra)):
        if gid[i] >= 0:
            continue
        stack, gid[i] = [i], i
        while stack:
            j = stack.pop()
            for k in np.flatnonzero(near[j] & (gid < 0)):
                gid[k] = i
                stack.append(k)
    size = pd.Series(gid).map(pd.Series(gid).value_counts()).to_numpy()
    span = np.array([sep[np.ix_(gid == g, gid == g)].max() for g in gid])
    return gid, size, span


def run(science, objects, out, group_arcmin=10.0, counterparts=None, fetch=fetch_sources, pause=0.2):
    s = pd.read_parquet(science, columns=["dia_object_id", "tns_type", "tns_z", "field"])
    o = pd.read_parquet(objects, columns=["dia_object_id", "ra_deg", "dec_deg"])
    x = s[s.tns_type.fillna("").str.startswith(("SN", "SLSN"))].merge(o, on="dia_object_id").reset_index(drop=True)
    gid, size, span = groups(x.ra_deg, x.dec_deg, x.tns_type, group_arcmin)
    x["group"], x["group_size"], x["group_span_arcmin"] = gid, size, np.round(span, 2)
    rows = []
    for oid in x.dia_object_id:
        rec = fetch(int(oid))
        names = tns_names(rec)
        in_win = [r for r in rec if WINDOW_MJD_TAI[0] <= float(r["r:midpointMjdTai"]) < WINDOW_MJD_TAI[1]]
        rows.append({"dia_object_id": int(oid), "api_alerts": len(rec), "api_alerts_in_window": len(in_win),
                     "api_tns_names": ";".join(names), "confirmed_by_api": bool(names),
                     "api_broker_versions": ";".join(sorted({str(r.get("f:fink_broker_version")) for r in in_win}))})
        time.sleep(pause)
    x = x.merge(pd.DataFrame(rows), on="dia_object_id")
    if counterparts is not None:
        cp = pd.read_parquet(counterparts, columns=["dia_object_id", "category_r_star", "nearest_sep_arcsec"])
        x = x.merge(cp.rename(columns={"category_r_star": "gaia_category"}), on="dia_object_id", how="left")
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    x.to_csv(out / "tns_labels.csv", index=False)
    multi = x.group_size > 1
    summary = {"run": out.name, "n_labelled": int(len(x)), "n_confirmed": int(x.confirmed_by_api.sum()),
               "n_in_groups": int(multi.sum()), "n_groups": int(x[multi].group.nunique()),
               "n_confirmed_in_groups": int((multi & x.confirmed_by_api).sum()),
               "n_confirmed_single": int((~multi & x.confirmed_by_api).sum()), "n_single": int((~multi).sum()),
               "group_arcmin": group_arcmin, "window_mjd_tai": list(WINDOW_MJD_TAI), "api": SOURCES_URL,
               "api_columns": COLUMNS, "science_sha256": sha256_file(science), "objects_sha256": sha256_file(objects),
               "retrieved_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    if counterparts is not None:
        summary["counterparts_sha256"] = sha256_file(counterparts)
        summary["gaia_category_by_confirmation"] = {
            str(k): v for k, v in x.groupby("confirmed_by_api").gaia_category.value_counts().unstack(fill_value=0)
            .to_dict("index").items()}
    (out / "summary.json").write_text(json.dumps(summary, indent=1, default=int) + "\n")
    return summary


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--science", required=True)
    p.add_argument("--objects", required=True, help="dia_object_template_photometry.parquet (positions)")
    p.add_argument("--counterparts", help="counterparts.parquet from analyse_gaia_counterparts.py")
    p.add_argument("--group-arcmin", type=float, default=10.0)
    p.add_argument("--out", required=True, help="new output directory (must not exist)")
    p.add_argument("--code-sha", required=True)
    a = p.parse_args(argv)
    s = run(a.science, a.objects, a.out, a.group_arcmin, a.counterparts)
    s["code_sha"] = a.code_sha
    (Path(a.out) / "summary.json").write_text(json.dumps(s, indent=1, default=int) + "\n")
    print(json.dumps(s, indent=1, default=int))


if __name__ == "__main__":
    main()

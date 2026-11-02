#!/usr/bin/env python
"""Trace where the TNS supernova labels of the per-object science table come from in the delivered alert packets.

The science suite (scripts/alert_science_suite.py) gives each alert a TNS type, tns_type_recomputed with "(TNS) "
removed (when not null and not "Unknown"), otherwise xm.tns_type, and then keeps each object's last non-null value
in (time, diaSourceId) order. scripts/check_tns_labels.py found 64 of 231 SN/SLSN labels without a TNS name on any
alert served by Fink's API. This script answers which packet field each label comes from and whether the delivered
packets carry a TNS name.

  extract    Run on the data host. Opens the analytical catalog with read_only=True, finds the raw Parquet files
             holding the given DIA objects, reads only the needed columns of only those files, and prints JSON
             (meta + one row per delivered alert) to stdout. Writes nothing.
  summarise  Run anywhere. Reads that JSON and tns_labels.csv (from check_tns_labels.py) and writes per_object.csv
             and summary.json to a new directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
import time
from pathlib import Path

CATALOG = "/astro/store/shire/FINK/data/processed/final-five-window-analytics-20261006-v1/analytics.duckdb"
WANT = ("diaObjectId", "diaSourceId", "midpointMjdTai", "band", "fink_broker_version", "tns_type_recomputed", "xm", "pred")
XM_FIELDS = ("tns_fullname", "tns_type", "tns_redshift")


# ----------------------------------------------------------------------------- extract (data host)
def _struct_field(arr, name, n):
    """Values of field `name` of a struct array (null where the struct itself is null), or None when the struct or
    the field is absent."""
    if arr is None:
        return None
    for i in range(arr.type.num_fields):
        if arr.type.field(i).name == name:
            return arr.flatten()[i].to_pylist()
    return None


def read_file(path, ids):
    """Rows of one raw Parquet file for the given diaObjectIds, plus the packet fields this file lacks."""
    import pyarrow.parquet as pq
    names = pq.ParquetFile(path).schema_arrow.names
    cols = [c for c in WANT if c in names]
    t = pq.read_table(path, columns=cols, filters=[("diaObjectId", "in", list(ids))])
    n = t.num_rows

    def col(c):
        return t.column(c).to_pylist() if c in t.column_names else [None] * n

    xm = t.column("xm").combine_chunks() if "xm" in t.column_names else None
    pred = t.column("pred").combine_chunks() if "pred" in t.column_names else None
    xmv = {f: _struct_field(xm, f, n) for f in XM_FIELDS}
    missing = [c for c in ("tns_type_recomputed", "fink_broker_version") if c not in names]
    missing += [f"xm.{f}" for f, v in xmv.items() if v is None and xm is not None] + (["xm"] if xm is None else [])
    is_sso = _struct_field(pred, "is_sso", n) or [None] * n
    rows = []
    base = Path(path).name
    for i, (oid, sid, mjd, band, ver, rec) in enumerate(zip(col("diaObjectId"), col("diaSourceId"), col("midpointMjdTai"),
                                                            col("band"), col("fink_broker_version"),
                                                            col("tns_type_recomputed"))):
        rows.append({"oid": oid, "sid": sid, "mjd": mjd, "band": band, "ver": ver, "rec": rec,
                     "name": (xmv["tns_fullname"] or [None] * n)[i], "xm_type": (xmv["tns_type"] or [None] * n)[i],
                     "xm_z": (xmv["tns_redshift"] or [None] * n)[i], "is_sso": is_sso[i], "file": base})
    return rows, missing


def extract(catalog, ids):
    import duckdb
    t0 = time.time()
    ids = sorted({int(i) for i in ids})
    idl = ",".join(str(i) for i in ids)
    con = duckdb.connect(str(catalog), read_only=True)
    try:
        files = [r[0] for r in con.execute(
            f"SELECT DISTINCT raw_file FROM sources WHERE dia_object_id IN ({idl}) ORDER BY 1").fetchall()]
        n_catalog = con.execute(f"SELECT count(*) FROM sources WHERE dia_object_id IN ({idl})").fetchone()[0]
        per_object = dict(con.execute(
            f"SELECT dia_object_id, count(*) FROM sources WHERE dia_object_id IN ({idl}) GROUP BY 1").fetchall())
    finally:
        con.close()
    rows, gaps = [], {}
    for f in files:
        r, missing = read_file(f, ids)
        rows += r
        for m in missing:
            gaps[m] = gaps.get(m, 0) + 1
    dia = [r for r in rows if r["is_sso"] is False and (r["oid"] or 0) > 0]
    meta = {"catalog": str(catalog), "n_ids": len(ids), "n_files": len(files), "n_catalog_sources": int(n_catalog),
            "n_rows_read": len(rows), "n_dia_rows_read": len(dia),
            "dia_rows_equal_catalog": len(dia) == n_catalog,
            "sids_unique": len({r["sid"] for r in rows}) == len(rows),
            "ids_without_sources": [i for i in ids if i not in per_object],
            "files_missing_field": gaps, "python": platform.python_version(),
            "duckdb": duckdb.__version__, "pyarrow": __import__("pyarrow").__version__,
            "runtime_s": round(time.time() - t0, 1), "finished_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    return {"meta": meta, "rows": rows}


# ----------------------------------------------------------------------------- summarise (anywhere)
def alert_label(rec, xm_type):
    """The science suite's per-alert TNS type and the field it came from."""
    if isinstance(rec, str) and rec != "Unknown":
        return rec.replace("(TNS) ", ""), "tns_type_recomputed"
    if isinstance(xm_type, str):
        return xm_type, "xm.tns_type"
    return None, None


def _name(v):
    return isinstance(v, str) and v not in ("", "None", "nan")


def summarise_objects(rows, labels):
    """One record per labelled object. `labels` maps dia_object_id -> (notebook tns_type, confirmed_by_api)."""
    by = {}
    for r in rows:
        if r["is_sso"] is False and (r["oid"] or 0) > 0:
            by.setdefault(int(r["oid"]), []).append(r)
    out = []
    for oid, (nb_type, confirmed) in sorted(labels.items()):
        al = sorted(by.get(oid, []), key=lambda r: (r["mjd"], r["sid"]))
        lab = [alert_label(r["rec"], r["xm_type"]) for r in al]
        last = next(((t, src) for t, src in reversed(lab) if t is not None), (None, None))
        rec_ok = [r for r in al if isinstance(r["rec"], str) and r["rec"] != "Unknown"]
        named = [r for r in al if _name(r["name"])]
        out.append({
            "dia_object_id": oid, "confirmed_by_api": bool(confirmed), "notebook_tns_type": nb_type,
            "n_alerts": len(al), "versions": ";".join(sorted({str(r["ver"]) for r in al})),
            "n_with_tns_name": len(named), "tns_names": ";".join(sorted({r["name"] for r in named})),
            "n_with_xm_tns_type": sum(isinstance(r["xm_type"], str) for r in al),
            "xm_tns_types": ";".join(sorted({r["xm_type"] for r in al if isinstance(r["xm_type"], str)})),
            "n_with_recomputed": len(rec_ok), "recomputed_values": ";".join(sorted({r["rec"] for r in rec_ok})),
            "n_recomputed_unknown": sum(r["rec"] == "Unknown" for r in al),
            "derived_tns_type": last[0], "derived_from": last[1],
            "derived_equals_notebook": last[0] == nb_type,
            "first_mjd_recomputed": min((r["mjd"] for r in rec_ok), default=None),
            "first_mjd_tns_name": min((r["mjd"] for r in named), default=None),
            "versions_with_recomputed": ";".join(sorted({str(r["ver"]) for r in rec_ok})),
            "versions_with_tns_name": ";".join(sorted({str(r["ver"]) for r in named})),
        })
    return out


def summarise(extract_json, labels_csv, out):
    import pandas as pd
    data = json.loads(Path(extract_json).read_text())
    lab = pd.read_csv(labels_csv)
    labels = {int(r.dia_object_id): (r.tns_type, bool(r.confirmed_by_api)) for r in lab.itertuples()}
    per = pd.DataFrame(summarise_objects(data["rows"], labels))
    per = per.merge(lab[["dia_object_id", "api_alerts_in_window", "group_size", "gaia_category"]], on="dia_object_id")
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    per.to_csv(out / "per_object.csv", index=False)
    s = {"run": out.name, "extract_meta": data["meta"],
         "extract_sha256": hashlib.sha256(Path(extract_json).read_bytes()).hexdigest(),
         "labels_sha256": hashlib.sha256(Path(labels_csv).read_bytes()).hexdigest(),
         "n_objects": int(len(per)), "derived_equals_notebook": int(per.derived_equals_notebook.sum())}
    for key, g in per.groupby("confirmed_by_api"):
        k = "confirmed" if key else "unconfirmed"
        s[k] = {"n": int(len(g)),
                "label_from": {str(a): int(b) for a, b in g.derived_from.value_counts(dropna=False).items()},
                "with_tns_name_in_delivery": int((g.n_with_tns_name > 0).sum()),
                "with_xm_tns_type_in_delivery": int((g.n_with_xm_tns_type > 0).sum()),
                "with_recomputed_in_delivery": int((g.n_with_recomputed > 0).sum()),
                "delivered_alerts_equal_api_in_window": int((g.n_alerts == g.api_alerts_in_window).sum())}
    (out / "summary.json").write_text(json.dumps(s, indent=1, default=str) + "\n")
    return s


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("extract")
    e.add_argument("--catalog", default=CATALOG)
    e.add_argument("--ids", required=True, help="comma-separated diaObjectIds")
    m = sub.add_parser("summarise")
    m.add_argument("--extract", required=True)
    m.add_argument("--labels", required=True, help="tns_labels.csv from check_tns_labels.py")
    m.add_argument("--out", required=True, help="new output directory (must not exist)")
    m.add_argument("--code-sha", required=True)
    a = p.parse_args(argv)
    if a.cmd == "extract":
        res = extract(a.catalog, a.ids.split(","))
        json.dump(res, sys.stdout, default=str)
        sys.stdout.write("\n")
        print(json.dumps(res["meta"]), file=sys.stderr)
        return res
    s = summarise(a.extract, a.labels, a.out)
    s["code_sha"] = a.code_sha
    (Path(a.out) / "summary.json").write_text(json.dumps(s, indent=1, default=str) + "\n")
    print(json.dumps({k: v for k, v in s.items() if k != "extract_meta"}, indent=1, default=str))
    return s


if __name__ == "__main__":
    main()

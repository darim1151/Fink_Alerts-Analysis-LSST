#!/usr/bin/env python
"""Find Gaia DR3 sources around every DIA object position, through IRSA's TAP service (gaia_dr3_source).

Input: the per-object table dia_object_template_photometry.parquet (dia_object_id, ra_deg, dec_deg). Positions are
uploaded in chunks (TAP_UPLOAD) and every Gaia DR3 source within --radius-arcsec of the J2016.0 Gaia position is
returned, so the separations can be recomputed afterwards with proper motion. The default radius (5 arcsec) is wider
than any true match needs: it keeps stars moving up to ~480 mas/yr over the ~10.3 yr from J2016.0 to the 2026 alerts,
and leaves room to see the flat background of chance matches. With --shift-arcsec the positions are
first moved north by that amount (a false-match control); with --sample-fraction only objects whose
sha256("<seed>:<diaObjectId>") falls below that fraction are used, so the sample does not depend on library versions.

Chunks are written one by one under <out>/chunks/ (parquet plus a JSON record of the query, counts and time) and an
existing valid chunk is not fetched again, so an interrupted run can be resumed with the same arguments. When all
chunks are present, matches.parquet, uploaded.parquet and summary.json are written. A chunk whose file no longer matches
its recorded SHA-256 is fetched again. Read-only on the input; network
only to IRSA.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

TAP_URL = "https://irsa.ipac.caltech.edu/TAP"
GAIA_TABLE = "gaia_dr3_source"
GAIA_COLUMNS = ("source_id", "ra", "dec", "parallax", "parallax_error", "pmra", "pmra_error", "pmdec", "pmdec_error",
                "phot_g_mean_mag", "ruwe", "astrometric_params_solved", "classprob_dsc_combmod_star",
                "classprob_dsc_combmod_quasar", "classprob_dsc_combmod_galaxy", "in_qso_candidates",
                "in_galaxy_candidates")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def query_text(radius_arcsec):
    cols = ", ".join(f"g.{c}" for c in GAIA_COLUMNS)
    return (f"SELECT u.oid, {cols} FROM TAP_UPLOAD.u AS u, {GAIA_TABLE} AS g "
            f"WHERE CONTAINS(POINT('ICRS', g.ra, g.dec), CIRCLE('ICRS', u.ra, u.dec, {radius_arcsec / 3600.0:.9f}))=1")


def hash_fraction(seed, ids):
    """Uniform number in [0, 1) per id from sha256("<seed>:<id>"): reproducible sampling."""
    return np.array([int(hashlib.sha256(f"{seed}:{i}".encode()).hexdigest()[:15], 16) / 16 ** 15 for i in ids])


def prepare_positions(table, shift_arcsec=0.0, sample_fraction=1.0, seed=20261101):
    """(oid, ra, dec) to upload, sorted into compact sky order; dec moved north by shift_arcsec."""
    t = table[["dia_object_id", "ra_deg", "dec_deg"]].copy()
    if not t.dia_object_id.is_unique or t[["ra_deg", "dec_deg"]].isna().any().any():
        raise ValueError("object ids must be unique and positions finite")
    if sample_fraction < 1.0:
        t = t[hash_fraction(seed, t.dia_object_id) < sample_fraction]
    dec = t.dec_deg.to_numpy() + shift_arcsec / 3600.0
    if (np.abs(dec) > 90).any():
        raise ValueError("shifted position beyond a pole")
    pos = pd.DataFrame({"oid": t.dia_object_id.to_numpy(np.int64), "ra": t.ra_deg.to_numpy(), "dec": dec})
    pos["_band"] = np.floor(pos.dec).astype(int)
    return pos.sort_values(["_band", "ra", "oid"]).drop(columns="_band").reset_index(drop=True)


class TruncatedResult(RuntimeError):
    """The service returned fewer rows than matched (overflow); never retried silently."""


def result_status(result):
    status = getattr(result, "status", "OK")
    if isinstance(status, (tuple, list)):
        status = status[0] if status else "OK"
    return str(status).upper()


def run_query(chunk, radius_arcsec, tries=8, service_factory=None, maxrec=10_000_000):
    """Matches for one chunk as a DataFrame, plus the number of attempts. Raises if the result was truncated."""
    from astropy.table import Table

    if service_factory is None:
        import pyvo

        def service_factory():
            return pyvo.dal.TAPService(TAP_URL)
    upload = Table({"oid": chunk.oid.to_numpy(np.int64), "ra": chunk.ra.to_numpy(), "dec": chunk.dec.to_numpy()})
    last = None
    for attempt in range(1, tries + 1):
        try:
            result = service_factory().run_sync(query_text(radius_arcsec), uploads={"u": upload}, maxrec=maxrec)
            df = result.to_table().to_pandas()
            if result_status(result) == "OVERFLOW" or len(df) >= maxrec:
                raise TruncatedResult("TAP result truncated (OVERFLOW)")
            return df, attempt
        except TruncatedResult:
            raise
        except Exception as exc:  # network and service errors: retry with backoff
            last = exc
            time.sleep(min(120, 5 * 2 ** (attempt - 1)))
    raise RuntimeError(f"chunk failed after {tries} attempts: {last}")


def check_chunk(df, chunk):
    expected = ["oid", *GAIA_COLUMNS]
    if list(df.columns) != expected:
        raise ValueError(f"unexpected columns {list(df.columns)}")
    if not set(df.oid.astype(np.int64)).issubset(set(chunk.oid)):
        raise ValueError("result contains an object that was not uploaded")
    if df.duplicated(["oid", "source_id"]).any():
        raise ValueError("duplicate (object, Gaia source) pairs")


def fetch_chunk(i, chunk, out, radius_arcsec, service_factory=None):
    path, meta = out / "chunks" / f"chunk_{i:05d}.parquet", out / "chunks" / f"chunk_{i:05d}.json"
    if path.exists() and meta.exists():
        m = json.loads(meta.read_text())
        if (m["n_uploaded"] == len(chunk) and m["first_oid"] == int(chunk.oid.iloc[0])
                and m["last_oid"] == int(chunk.oid.iloc[-1]) and m["parquet_sha256"] == sha256_file(path)):
            return m
    started = time.time()
    df, attempts = run_query(chunk, radius_arcsec, service_factory=service_factory)
    df = df[["oid", *GAIA_COLUMNS]] if set(["oid", *GAIA_COLUMNS]).issubset(df.columns) else df
    check_chunk(df, chunk)
    tmp = path.with_suffix(".tmp")
    df.to_parquet(tmp, index=False)
    tmp.rename(path)
    m = {"chunk": i, "n_uploaded": int(len(chunk)), "first_oid": int(chunk.oid.iloc[0]), "last_oid": int(chunk.oid.iloc[-1]),
         "n_rows": int(len(df)), "n_objects_matched": int(df.oid.nunique()), "attempts": attempts,
         "seconds": round(time.time() - started, 1), "finished_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
         "parquet_sha256": sha256_file(path)}
    meta.write_text(json.dumps(m, indent=1) + "\n")
    return m


def run(objects, out, radius_arcsec=5.0, chunk_size=20000, workers=4, shift_arcsec=0.0, sample_fraction=1.0,
        seed=20261101, code_sha="", service_factory=None, log=print):
    out = Path(out)
    (out / "chunks").mkdir(parents=True, exist_ok=True)
    table = pd.read_parquet(objects, columns=["dia_object_id", "ra_deg", "dec_deg"])
    pos = prepare_positions(table, shift_arcsec, sample_fraction, seed)
    chunks = [pos.iloc[a:a + chunk_size] for a in range(0, len(pos), chunk_size)]
    settings = {"objects": Path(objects).name, "objects_sha256": sha256_file(objects), "radius_arcsec": radius_arcsec,
                "chunk_size": chunk_size, "shift_arcsec_north": shift_arcsec, "sample_fraction": sample_fraction,
                "seed": seed, "n_uploaded": int(len(pos)), "n_chunks": len(chunks)}
    settings_path = out / "settings.json"
    if settings_path.exists() and json.loads(settings_path.read_text()) != settings:
        raise ValueError(f"{out} holds a run with different settings")
    settings_path.write_text(json.dumps(settings, indent=1) + "\n")
    metas = []
    pool = cf.ThreadPoolExecutor(max_workers=workers)
    try:
        futures = {pool.submit(fetch_chunk, i, c, out, radius_arcsec, service_factory): i for i, c in enumerate(chunks)}
        for n, fut in enumerate(cf.as_completed(futures), start=1):
            m = fut.result()
            metas.append(m)
            log(f"chunk {m['chunk']:5d} done ({n}/{len(chunks)}): {m['n_uploaded']} up, {m['n_rows']} rows, "
                f"{m['seconds']} s, {m['attempts']} attempt(s)")
    except BaseException:
        pool.shutdown(wait=True, cancel_futures=True)   # stop queued chunks; finished ones stay on disk for a resume
        raise
    pool.shutdown(wait=True)
    metas.sort(key=lambda m: m["chunk"])
    matches = pd.concat([pd.read_parquet(out / "chunks" / f"chunk_{m['chunk']:05d}.parquet") for m in metas],
                        ignore_index=True)
    matches["oid"] = matches.oid.astype(np.int64)
    if matches.duplicated(["oid", "source_id"]).any():
        raise ValueError("duplicate (object, Gaia source) pairs across chunks")
    matches.to_parquet(out / "matches.parquet", index=False)
    pos.to_parquet(out / "uploaded.parquet", index=False)
    summary = {"run": out.name, "code_sha": code_sha, "script_sha256": sha256_file(Path(__file__)),
               "service": TAP_URL, "table": GAIA_TABLE, "query": query_text(radius_arcsec), **settings,
               "n_rows": int(len(matches)), "n_objects_matched": int(matches.oid.nunique()),
               "chunk_seconds_total": round(sum(m["seconds"] for m in metas), 1),
               "finished_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "files": {f: sha256_file(out / f) for f in ("matches.parquet", "uploaded.parquet")}}
    (out / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    return summary


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--objects", required=True, help="dia_object_template_photometry.parquet")
    p.add_argument("--out", required=True, help="run directory (created; resumed if it holds the same settings)")
    p.add_argument("--radius-arcsec", type=float, default=5.0)
    p.add_argument("--chunk-size", type=int, default=20000)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--shift-arcsec", type=float, default=0.0, help="move positions north by this much (control run)")
    p.add_argument("--sample-fraction", type=float, default=1.0)
    p.add_argument("--seed", type=int, default=20261101)
    p.add_argument("--code-sha", required=True)
    a = p.parse_args(argv)
    s = run(a.objects, a.out, a.radius_arcsec, a.chunk_size, a.workers, a.shift_arcsec, a.sample_fraction, a.seed,
            a.code_sha, log=lambda msg: print(msg, flush=True))
    print(json.dumps({k: v for k, v in s.items() if k not in ("files", "query")}, indent=1))


if __name__ == "__main__":
    main()

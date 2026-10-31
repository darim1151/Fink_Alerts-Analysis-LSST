#!/usr/bin/env python3
"""Fetch Rubin alert stamps (science, template, difference) from the public Fink LSST API for the paper image targets.

For each target in image_targets.csv (written by scripts/select_image_targets.py) the object's alerts are read from
POST /api/v1/sources and one alert is chosen: the alert inside the cohort window [--start, --stop) (UTC dates, compared
with midpointMjdTai) with the highest |psfFlux| / psfFluxErr; ties go to the lowest diaSourceId. Its three stamps are
fetched from POST /api/v1/cutouts as FITS, one request per kind (the API returns all three at once only as arrays).

With --colour-table (fink_dia_template_colours.csv[.gz]) the API's in-window alert count per band is compared with the
count in our delivered data; a difference is reported, not fixed.

Outputs go to a new directory that must not exist: sources/ (raw /sources responses), stamps/ (FITS), stamps.csv and
summary.json. Standard library only, so it runs with any Python 3.8+. Public API: no login and no credentials.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import hashlib
import http.client
import json
import math
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

API_BASE = "https://api.lsst.fink-portal.org"
USER_AGENT = "fink-lsst-alert-stamps/1.0"
KINDS = ("Science", "Template", "Difference")
BANDS = ("u", "g", "r", "i", "z", "y")
SOURCE_COLUMNS = ("r:diaObjectId", "r:diaSourceId", "r:midpointMjdTai", "r:band", "r:psfFlux", "r:psfFluxErr",
                  "r:ra", "r:dec")
RETRY_HTTP = (429, 500, 502, 503, 504)
MJD_ZERO = dt.date(1858, 11, 17)
FITS_BLOCK = 2880
STAMP_COLUMNS = ["name", "group", "dia_object_id", "dia_source_id", "band", "midpoint_mjd_tai", "psf_flux_njy",
                 "psf_flux_err_njy", "snr", "ra_deg", "dec_deg", "n_api_alerts", "n_api_in_window", "count_check",
                 "science_file", "science_sha256", "template_file", "template_sha256", "difference_file",
                 "difference_sha256", "status"]


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def mjd(date_text):
    """MJD at 00:00 of a UTC date given as YYYY-MM-DD."""
    return float((dt.date.fromisoformat(date_text) - MJD_ZERO).days)


class Client:
    """JSON POST client: waits --delay seconds between requests, retries network errors, HTTP 429 and 5xx."""

    def __init__(self, base=API_BASE, timeout=120.0, delay=1.0, tries=3):
        self.base, self.timeout, self.delay, self.tries = base.rstrip("/"), timeout, delay, tries
        self.n_requests = 0

    def post(self, path, payload):
        data = json.dumps(payload).encode()
        for attempt in range(1, self.tries + 1):
            if self.n_requests:
                time.sleep(self.delay)
            self.n_requests += 1
            request = urllib.request.Request(self.base + path, data=data, method="POST",
                                             headers={"Content-Type": "application/json", "User-Agent": USER_AGENT})
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return response.read()
            except urllib.error.HTTPError as exc:
                if exc.code not in RETRY_HTTP or attempt == self.tries:
                    raise RuntimeError(f"HTTP {exc.code} from {path}: {exc.read()[:300]!r}") from exc
            except (OSError, http.client.HTTPException) as exc:
                if attempt == self.tries:
                    raise RuntimeError(f"network error from {path}: {exc}") from exc
            time.sleep(2 ** attempt)
        raise AssertionError("unreachable")


def field(row, name):
    """Value of a Rubin field, whether the API names it 'r:<name>' or '<name>'."""
    for key in (f"r:{name}", name):
        if key in row:
            return row[key]
    raise KeyError(name)


def as_id(value):
    """Rubin identifiers are 64-bit integers; refuse anything that may have passed through a float."""
    if isinstance(value, bool):
        raise ValueError(f"identifier is not an integer: {value!r}")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str) and value.isdigit():
        return value
    raise ValueError(f"identifier is not an integer: {value!r}")


def as_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return math.nan


def parse_sources(body, dia_object_id):
    """Alerts of one object from a /sources JSON response, with checked identifiers."""
    rows = json.loads(body)
    if not isinstance(rows, list):
        raise ValueError(f"/sources did not return a list: {str(rows)[:300]}")
    alerts = []
    for row in rows:
        oid = as_id(field(row, "diaObjectId"))
        if oid != dia_object_id:
            raise ValueError(f"/sources returned object {oid} for {dia_object_id}")
        alerts.append({"dia_source_id": as_id(field(row, "diaSourceId")), "mjd": as_float(field(row, "midpointMjdTai")),
                       "band": str(field(row, "band")), "flux": as_float(field(row, "psfFlux")),
                       "flux_err": as_float(field(row, "psfFluxErr")), "ra": as_float(field(row, "ra")),
                       "dec": as_float(field(row, "dec"))})
    ids = [a["dia_source_id"] for a in alerts]
    if len(ids) != len(set(ids)):
        raise ValueError(f"/sources returned duplicate diaSourceIds for {dia_object_id}")
    return alerts


def in_window(alerts, start_mjd, stop_mjd):
    return [a for a in alerts if start_mjd <= a["mjd"] < stop_mjd]


def choose_alert(alerts):
    """Highest |psfFlux| / psfFluxErr among alerts with finite flux and positive error; ties: lowest diaSourceId."""
    usable = [a for a in alerts if math.isfinite(a["flux"]) and math.isfinite(a["flux_err"]) and a["flux_err"] > 0]
    if not usable:
        return None
    return min(usable, key=lambda a: (-abs(a["flux"]) / a["flux_err"], int(a["dia_source_id"])))


def check_fits(body):
    if not (body[:9] == b"SIMPLE  =" and len(body) % FITS_BLOCK == 0 and len(body) > 0):
        raise ValueError(f"not a FITS file ({len(body)} bytes, starts {body[:60]!r})")


def band_counts(alerts):
    counts = {b: 0 for b in BANDS}
    for a in alerts:
        counts[a["band"]] = counts.get(a["band"], 0) + 1
    return counts


def count_check(api, table):
    """'match', or the bands where the API's in-window count differs from our data, as 'band api/ours'."""
    if table is None:
        return "not checked"
    bands = list(BANDS) + sorted((set(api) | set(table)) - set(BANDS))
    diffs = [f"{b} {api.get(b, 0)}/{table.get(b, 0)}" for b in bands if api.get(b, 0) != table.get(b, 0)]
    return "match" if not diffs else "differs (api/ours): " + ", ".join(diffs)


def read_targets(path):
    with open(path, newline="") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        r["dia_object_id"] = as_id(r["dia_object_id"])
    if len({r["dia_object_id"] for r in rows}) != len(rows) or len({r["name"] for r in rows}) != len(rows):
        raise ValueError("targets must have unique names and diaObjectIds")
    return rows


def read_table_counts(path, ids):
    """Per-band alert counts of the given objects from the colour table (n_alerts_<band>)."""
    opener = gzip.open if str(path).endswith(".gz") else open
    out = {}
    with opener(path, "rt", newline="") as fh:
        for row in csv.DictReader(fh):
            if row["dia_object_id"] in ids:
                out[row["dia_object_id"]] = {b: int(float(row[f"n_alerts_{b}"])) for b in BANDS}
    return out


def fetch_one(client, target, start_mjd, stop_mjd, table, out):
    """Fetch one target's sources and stamps; return its stamps.csv row (status 'ok' or the error)."""
    name, oid = target["name"], target["dia_object_id"]
    row = {c: "" for c in STAMP_COLUMNS}
    row.update(name=name, group=target.get("group", ""), dia_object_id=oid)
    try:
        body = client.post("/api/v1/sources", {"diaObjectId": oid, "columns": ",".join(SOURCE_COLUMNS),
                                               "output-format": "json"})
        (out / "sources" / f"{name}_{oid}.json").write_bytes(body)
        alerts = parse_sources(body, oid)
        window = in_window(alerts, start_mjd, stop_mjd)
        row.update(n_api_alerts=len(alerts), n_api_in_window=len(window),
                   count_check=count_check(band_counts(window), table.get(oid) if table is not None else None))
        best = choose_alert(window)
        if best is None:
            raise ValueError("no in-window alert with finite flux and positive error")
        row.update(dia_source_id=best["dia_source_id"], band=best["band"], midpoint_mjd_tai=f"{best['mjd']:.6f}",
                   psf_flux_njy=f"{best['flux']:.3f}", psf_flux_err_njy=f"{best['flux_err']:.3f}",
                   snr=f"{best['flux'] / best['flux_err']:.2f}", ra_deg=f"{best['ra']:.7f}", dec_deg=f"{best['dec']:.7f}")
        for kind in KINDS:
            stamp = client.post("/api/v1/cutouts", {"diaSourceId": best["dia_source_id"], "kind": kind,
                                                    "output-format": "FITS"})
            check_fits(stamp)
            path = out / "stamps" / f"{name}_{best['dia_source_id']}_{kind.lower()}.fits"
            path.write_bytes(stamp)
            row[f"{kind.lower()}_file"] = f"stamps/{path.name}"
            row[f"{kind.lower()}_sha256"] = sha256_bytes(stamp)
        row["status"] = "ok"
    except Exception as exc:  # one failed object must not stop the others; the error is recorded per object
        row["status"] = f"failed: {exc}"
    return row


def run(targets_path, out, client, start, stop, colour_table=None, code_sha=""):
    out = Path(out)
    targets = read_targets(targets_path)
    table = None
    if colour_table is not None:
        table = read_table_counts(colour_table, {t["dia_object_id"] for t in targets})
        missing = [t["name"] for t in targets if t["dia_object_id"] not in table]
        if missing:
            raise ValueError(f"targets not in the colour table: {missing}")
    out.mkdir(parents=True, exist_ok=False)
    (out / "sources").mkdir()
    (out / "stamps").mkdir()
    started = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    rows = [fetch_one(client, t, mjd(start), mjd(stop), table, out) for t in targets]
    with open(out / "stamps.csv", "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=STAMP_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    files = {str(p.relative_to(out)): sha256_file(p) for p in sorted(out.rglob("*")) if p.is_file()}
    summary = {
        "run": out.name, "code_sha": code_sha, "script_sha256": sha256_file(Path(__file__)),
        "targets": Path(targets_path).name, "targets_sha256": sha256_file(targets_path),
        "colour_table": Path(colour_table).name if colour_table else None,
        "colour_table_sha256": sha256_file(colour_table) if colour_table else None,
        "api_base": client.base, "window_utc": [start, stop], "window_mjd": [mjd(start), mjd(stop)],
        "selection": "in-window alert with the highest |psfFlux|/psfFluxErr; ties: lowest diaSourceId",
        "kinds": list(KINDS), "output_format": "FITS",
        "started_utc": started, "finished_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "n_requests": client.n_requests, "n_targets": len(rows), "n_ok": sum(r["status"] == "ok" for r in rows),
        "count_mismatches": {r["name"]: r["count_check"] for r in rows if r["count_check"].startswith("differs")},
        "failures": {r["name"]: r["status"] for r in rows if r["status"] != "ok"}, "files": files,
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--targets", required=True, help="image_targets.csv from scripts/select_image_targets.py")
    p.add_argument("--out", required=True, help="new output directory (must not exist)")
    p.add_argument("--colour-table", help="fink_dia_template_colours.csv[.gz], to compare alert counts per band")
    p.add_argument("--start", default="2026-02-25", help="first UTC date of the cohort (inclusive)")
    p.add_argument("--stop", default="2026-07-14", help="UTC date after the cohort's last night (exclusive)")
    p.add_argument("--api-base", default=API_BASE)
    p.add_argument("--delay", type=float, default=1.0, help="seconds between requests")
    p.add_argument("--code-sha", default="")
    a = p.parse_args(argv)
    summary = run(a.targets, a.out, Client(a.api_base, delay=a.delay), a.start, a.stop, a.colour_table, a.code_sha)
    print(json.dumps({k: summary[k] for k in ("run", "n_targets", "n_ok", "n_requests", "count_mismatches",
                                              "failures")}, indent=1))
    return 0 if summary["n_ok"] == summary["n_targets"] else 1


if __name__ == "__main__":
    sys.exit(main())

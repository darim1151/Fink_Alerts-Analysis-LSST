#!/usr/bin/env python
"""Compare delivered alert rows per UTC date with Fink's own nightly statistics (api/v1/statistics).

Fink files each alert under a "night" that can be later than the UTC date of the observation (it is not the UTC date:
an exposure at 23:59 UTC on 4 April 2026 is counted in Fink's night 2026-04-05). Nightly counts therefore differ by
alerts moved to a later night, while running totals agree. The script checks exactly that: over the cohort window the
totals must be equal, and at every date our running total must be at least Fink's (no alert counted by Fink before we
have it). Any other pattern is reported.

Inputs: Fink's statistics as returned by POST https://api.lsst.fink-portal.org/api/v1/statistics with
{"date": "<YYYY>", "output-format": "json"} (fetched and saved with --fetch-year, or read from --stats), and the
delivered rows per UTC date (the observatory bundle's time.json, whose counts.values maps date -> rows, or a CSV with
columns date,alerts). Writes per_date.csv, the raw statistics file and summary.json to a new directory.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import time
import urllib.request
from datetime import date, timedelta
from pathlib import Path

STATS_URL = "https://api.lsst.fink-portal.org/api/v1/statistics"


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def fetch_statistics(year, url=STATS_URL, timeout=120):
    body = json.dumps({"date": str(year), "output-format": "json"}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def read_delivered(path):
    path = Path(path)
    if path.suffix == ".json":
        values = json.loads(path.read_text())["counts"]["values"]
        return {d: int(v) for d, v in values.items()}
    with open(path, newline="") as fh:
        return {r["date"]: int(r["alerts"]) for r in csv.DictReader(fh)}


def compare(delivered, stats, start, stop):
    """Rows per date in [start, stop) and a summary. stats: list of Fink statistics records."""
    fink = {}
    for rec in stats:
        n = rec["f:night"]
        if n in fink:
            raise ValueError(f"night {n} appears twice in Fink's statistics")
        fink[n] = int(rec["f:alerts"])
    d0, d1 = date.fromisoformat(start), date.fromisoformat(stop)
    dates = [(d0 + timedelta(days=i)).isoformat() for i in range((d1 - d0).days)]
    outside = sorted(d for d in delivered if d not in set(dates) and delivered[d])
    if outside:
        raise ValueError(f"delivered rows outside the window: {outside[:5]}")
    rows, cum_ours, cum_fink = [], 0, 0
    for d in dates:
        ours, theirs = delivered.get(d, 0), fink.get(d.replace("-", ""))
        cum_ours += ours
        cum_fink += theirs or 0
        rows.append({"date": d, "delivered": ours, "fink_statistics": "" if theirs is None else theirs,
                     "difference": ours - (theirs or 0), "cumulative_difference": cum_ours - cum_fink})
    in_window = [n for n in fink if start.replace("-", "") <= n < stop.replace("-", "")]
    summary = {"window_utc": [start, stop], "delivered_total": cum_ours, "fink_total_in_window": cum_fink,
               "totals_equal": cum_ours == cum_fink,
               "running_total_never_behind": all(r["cumulative_difference"] >= 0 for r in rows),
               "dates_with_delivered_rows": sum(1 for r in rows if r["delivered"]),
               "fink_nights_in_window": len(in_window),
               "dates_equal": sum(1 for r in rows if r["difference"] == 0 and (r["delivered"] or r["fink_statistics"])),
               "dates_different": [r["date"] for r in rows if r["difference"] != 0],
               "largest_running_difference": max(r["cumulative_difference"] for r in rows) if rows else 0,
               "fink_versions_in_window": sorted({f"{rec['f:fink_broker_version']} / {rec['f:fink_science_version']}"
                                                  for rec in stats if rec["f:night"] in in_window})}
    summary["consistent"] = summary["totals_equal"] and summary["running_total_never_behind"]
    return rows, summary


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--stats", help="saved Fink statistics JSON")
    src.add_argument("--fetch-year", help="fetch Fink statistics for this year now")
    p.add_argument("--delivered", required=True, help="observatory time.json or CSV with date,alerts")
    p.add_argument("--start", default="2026-02-25")
    p.add_argument("--stop", default="2026-07-14", help="exclusive")
    p.add_argument("--out", required=True, help="new output directory (must not exist)")
    p.add_argument("--code-sha", required=True)
    a = p.parse_args(argv)
    out = Path(a.out)
    if a.stats:
        raw, source = Path(a.stats).read_bytes(), {"file": Path(a.stats).name}
    else:
        raw = fetch_statistics(a.fetch_year)
        source = {"url": STATS_URL, "payload": {"date": a.fetch_year, "output-format": "json"},
                  "retrieved_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    rows, summary = compare(read_delivered(a.delivered), json.loads(raw), a.start, a.stop)
    out.mkdir(parents=True, exist_ok=False)
    (out / "fink_statistics.json").write_bytes(raw)
    with open(out / "per_date.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    summary = {"run": out.name, "code_sha": a.code_sha, "statistics_source": source,
               "statistics_sha256": sha256_bytes(raw), "delivered": Path(a.delivered).name,
               "delivered_sha256": sha256_bytes(Path(a.delivered).read_bytes()), **summary}
    summary["files"] = {f.name: sha256_bytes(f.read_bytes()) for f in sorted(out.iterdir()) if f.is_file()}
    (out / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    print(json.dumps({k: v for k, v in summary.items() if k != "files"}, indent=1))
    return summary


if __name__ == "__main__":
    main()

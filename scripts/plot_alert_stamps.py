#!/usr/bin/env python
"""Plot the alert stamps fetched by scripts/fetch_alert_stamps.py: one science / template / difference triplet per target.

Reads a stamp run folder (stamps.csv, stamps/*.fits), checks every FITS file against the SHA-256 recorded in stamps.csv,
and draws one row per target group with five triplets each. Stamps are shown in pixel orientation; the template panel
gets north (N) and east (E) arrows from the stamp's WCS. Fink's FITS stamps carry CRVAL/CRPIX/PC keywords but no CTYPE;
the WCS is then read as RA---TAN/DEC--TAN (axis 1 = RA, axis 2 = Dec) and accepted only if the position angle of the
pixel +Y axis it implies agrees with the header's ROTPA within --max-dpa degrees. Science and template stamps
are each scaled separately (asinh between their 1st and 99.5th percentiles); the difference stamp uses a symmetric
linear scale (+/- its 99.5th percentile of |pixel|), so positive and negative residuals keep their sign.
Writes alert_stamps.png, alert_stamps.pdf, stamp_properties.csv and summary.json to a new directory.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

import numpy as np

KINDS = ("science", "template", "difference")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


WCS_KEYS = ("CRVAL1", "CRVAL2", "CRPIX1", "CRPIX2")
SCALE_KEYS = ("PC1_1", "CD1_1", "CDELT1")


def stamp_wcs(header):
    """(celestial WCS or None, source): 'header' if CTYPE is given, 'TAN assumed' if only CRVAL/CRPIX and PC, CD or
    CDELT are."""
    import warnings

    from astropy.wcs import WCS, FITSFixedWarning

    h = header.copy()
    if "CTYPE1" in h and "CTYPE2" in h:
        source = "header"
    elif all(k in h for k in WCS_KEYS) and any(k in h for k in SCALE_KEYS):
        h["CTYPE1"], h["CTYPE2"], source = "RA---TAN", "DEC--TAN", "TAN assumed"
    else:
        return None, "none"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FITSFixedWarning)
        w = WCS(h)
    return (w.celestial, source) if w.has_celestial else (None, "none")


def load_stamp(path):
    """(data as float array, primary header, celestial WCS or None, WCS source) from the first 2-D image HDU."""
    from astropy.io import fits

    with fits.open(path) as hdul:
        for hdu in hdul:
            if hdu.data is not None and np.ndim(hdu.data) == 2:
                data, header = np.asarray(hdu.data, dtype=float), hdu.header.copy()
                break
        else:
            raise ValueError(f"{path}: no 2-D image")
    wcs, source = stamp_wcs(header)
    return data, header, wcs, source


def pa_of_y_axis(wcs, shape):
    """Position angle (deg, north through east) of the pixel +Y direction at the stamp centre."""
    y0, x0 = (shape[0] - 1) / 2.0, (shape[1] - 1) / 2.0
    a, b = wcs.pixel_to_world(x0, y0), wcs.pixel_to_world(x0, y0 + 1.0)
    return float(a.position_angle(b).deg) % 360.0


def dpa_from_rotpa(header, wcs, shape):
    """WCS position angle of +Y minus the header's ROTPA, wrapped to [-180, 180); None without ROTPA or WCS."""
    if wcs is None or "ROTPA" not in header:
        return None
    return (pa_of_y_axis(wcs, shape) - float(header["ROTPA"]) + 180.0) % 360.0 - 180.0


def orientation_ok(header, wcs, shape, max_dpa):
    if wcs is None:
        return False
    dpa = dpa_from_rotpa(header, wcs, shape)
    return dpa is None or abs(dpa) <= max_dpa


def compass(wcs, shape):
    """Unit pixel vectors (dx, dy) pointing north and east at the stamp centre, from the WCS."""
    from astropy import units as u

    y0, x0 = (shape[0] - 1) / 2.0, (shape[1] - 1) / 2.0
    centre = wcs.pixel_to_world(x0, y0)
    out = {}
    for label, pa in (("N", 0.0), ("E", 90.0)):
        p = centre.directional_offset_by(pa * u.deg, 1.0 * u.arcsec)
        x, y = wcs.world_to_pixel(p)
        v = np.array([float(x) - x0, float(y) - y0])
        out[label] = v / np.hypot(*v)
    return out


def pixel_scale_arcsec(wcs):
    from astropy.wcs.utils import proj_plane_pixel_scales

    return float(np.mean(proj_plane_pixel_scales(wcs)) * 3600.0)


def read_run(run):
    """Rows of stamps.csv with status ok, after checking each FITS file against its recorded SHA-256."""
    run = Path(run)
    with open(run / "stamps.csv", newline="") as fh:
        rows = [r for r in csv.DictReader(fh) if r["status"] == "ok"]
    for r in rows:
        for kind in KINDS:
            path = run / r[f"{kind}_file"]
            if sha256(path) != r[f"{kind}_sha256"]:
                raise ValueError(f"checksum mismatch: {path}")
    return rows


def stamp_properties(run, rows, max_dpa):
    props = []
    for r in rows:
        for kind in KINDS:
            data, header, wcs, source = load_stamp(Path(run) / r[f"{kind}_file"])
            dpa = dpa_from_rotpa(header, wcs, data.shape)
            props.append({"name": r["name"], "kind": kind, "dia_source_id": r["dia_source_id"],
                          "ny": data.shape[0], "nx": data.shape[1], "n_nonfinite": int((~np.isfinite(data)).sum()),
                          "bunit": str(header.get("BUNIT", "")), "wcs_source": source,
                          "pixel_scale_arcsec": round(pixel_scale_arcsec(wcs), 4) if wcs is not None else "",
                          "pa_y_deg": round(pa_of_y_axis(wcs, data.shape), 3) if wcs is not None else "",
                          "rotpa_deg": round(float(header["ROTPA"]), 3) if "ROTPA" in header else "",
                          "dpa_deg": round(dpa, 3) if dpa is not None else "",
                          "compass_drawn": orientation_ok(header, wcs, data.shape, max_dpa)})
    return props


def norms(data, kind):
    from astropy.visualization import AsinhStretch, ImageNormalize, LinearStretch

    finite = data[np.isfinite(data)]
    if finite.size == 0:
        return ImageNormalize(vmin=0, vmax=1, stretch=LinearStretch())
    if kind == "difference":
        v = float(np.percentile(np.abs(finite), 99.5)) or 1.0
        return ImageNormalize(vmin=-v, vmax=v, stretch=LinearStretch())
    lo, hi = (float(x) for x in np.percentile(finite, [1.0, 99.5]))
    if hi <= lo:
        hi = lo + 1.0
    return ImageNormalize(vmin=lo, vmax=hi, stretch=AsinhStretch(a=0.1))


def plot(run, rows, out_png, out_pdf, max_dpa, per_row=5):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    groups = list(dict.fromkeys(r["group"] for r in rows))
    ncol = 3 * per_row
    fig, axes = plt.subplots(len(groups), ncol, figsize=(1.05 * ncol + 1.6, 1.32 * len(groups) + 0.6), squeeze=False)
    cmap = plt.get_cmap("gray").copy()
    cmap.set_bad("#6f8fb3")
    for gi, group in enumerate(groups):
        members = [r for r in rows if r["group"] == group][:per_row]
        for j in range(per_row):
            for k, kind in enumerate(KINDS):
                ax = axes[gi][3 * j + k]
                ax.set_xticks([])
                ax.set_yticks([])
                if j >= len(members):
                    ax.set_axis_off()
                    continue
                r = members[j]
                data, header, wcs, _ = load_stamp(Path(run) / r[f"{kind}_file"])
                ax.imshow(np.ma.masked_invalid(data), origin="lower", cmap=cmap, norm=norms(data, kind),
                          interpolation="nearest")
                if gi == len(groups) - 1:
                    ax.set_xlabel({"science": "science", "template": "template", "difference": "difference"}[kind],
                                  fontsize=6, labelpad=2)
                if kind == "template":
                    ax.set_title(f"{r['name']}  {r['band']}  S/N {float(r['snr']):+.0f}", fontsize=6.5, pad=2)
                    if orientation_ok(header, wcs, data.shape, max_dpa):
                        ny, nx = data.shape
                        base = np.array([0.27 * nx, 0.27 * ny])
                        for label, v in compass(wcs, data.shape).items():
                            tip = base + 0.15 * min(nx, ny) * v
                            ax.annotate("", xy=tip, xytext=base, arrowprops=dict(arrowstyle="-|>", color="#ffcc00", lw=0.6,
                                                                                 mutation_scale=4))
                            ax.text(*(base + 0.23 * min(nx, ny) * v), label, color="#ffcc00", fontsize=5,
                                    ha="center", va="center")
        axes[gi][0].set_ylabel(group.replace(", ", ",\n"), fontsize=6.5, rotation=0, ha="right", va="center", labelpad=4)
    fig.subplots_adjust(left=0.115, right=0.995, top=0.97, bottom=0.04, wspace=0.04, hspace=0.28)
    fig.savefig(out_png, dpi=220)
    fig.savefig(out_pdf)
    plt.close(fig)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--stamps", required=True, help="stamp run folder from scripts/fetch_alert_stamps.py")
    p.add_argument("--out", required=True, help="new output directory (must not exist)")
    p.add_argument("--code-sha", required=True)
    p.add_argument("--max-dpa", type=float, default=3.0,
                   help="largest accepted difference (deg) between the WCS position angle of +Y and ROTPA")
    a = p.parse_args(argv)
    run, out = Path(a.stamps), Path(a.out)
    rows = read_run(run)
    props = stamp_properties(run, rows, a.max_dpa)
    out.mkdir(parents=True, exist_ok=False)
    plot(run, rows, out / "alert_stamps.png", out / "alert_stamps.pdf", a.max_dpa)
    with open(out / "stamp_properties.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(props[0]))
        w.writeheader()
        w.writerows(props)
    summary = {"run": out.name, "code_sha": a.code_sha, "script_sha256": sha256(Path(__file__)),
               "stamp_run": run.name, "stamps_csv_sha256": sha256(run / "stamps.csv"),
               "stamp_run_summary_sha256": sha256(run / "summary.json") if (run / "summary.json").exists() else None,
               "n_targets": len(rows), "n_stamps_checked": 3 * len(rows),
               "max_dpa_deg": a.max_dpa, "wcs_sources": {k: sum(p["wcs_source"] == k for p in props)
                                                         for k in sorted({p["wcs_source"] for p in props})},
               "compass_drawn": sum(p["compass_drawn"] for p in props if p["kind"] == "template"),
               "dpa_range_deg": [min(p["dpa_deg"] for p in props if p["dpa_deg"] != ""),
                                 max(p["dpa_deg"] for p in props if p["dpa_deg"] != "")]
               if any(p["dpa_deg"] != "" for p in props) else None,
               "stamp_shapes": sorted({f"{p['ny']}x{p['nx']}" for p in props}),
               "files": {f.name: sha256(f) for f in sorted(out.iterdir()) if f.is_file()}}
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({k: v for k, v in summary.items() if k != "files"}, indent=1))


if __name__ == "__main__":
    main()
